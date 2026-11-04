// Browser-only stand-in for public/api.js, used by the click-to-try preview.
// Same methods, same shapes. Data lives in this browser (localStorage + IndexedDB).
(function () {
  const KEY = "mujian-preview-v1";
  const STORE = { name: "Mujian Furniture", address: ["1200 Industrial Way", "Oakland, CA 94607"], email: "orders@example.com", demo_checkout: true };
  const FREE = 50000, STD = 4900, WHITE = 14900;
  const CARRIERS = {
    USPS: "https://tools.usps.com/go/TrackConfirmAction?tLabels={}", UPS: "https://www.ups.com/track?tracknum={}",
    FedEx: "https://www.fedex.com/fedextrack/?trknbr={}", Freight: "",
  };
  const SEED = [
    ["s1", "Haven 3-Seat Sofa", "Living", "sofa", 189900, '83" W × 36" D × 31" H', "White oak frame · linen blend", "2–3 weeks", 6],
    ["c1", "Sit Walnut Dining Chair", "Dining", "chair", 34900, '18" W × 20" D × 31" H', "Black walnut · vegetable-tanned leather", "1 week", 24],
    ["t1", "Gather 6-Seat Dining Table", "Dining", "table", 149900, '71" W × 35" D × 30" H', "Solid white oak · hardwax oil", "3–4 weeks", 4],
    ["b1", "Rest Low Platform Bed, Queen", "Bedroom", "bed", 179900, '64" W × 85" D × 35" H', "Cherry · mortise-and-tenon joints", "4 weeks", 3],
    ["sh1", "Keep 3-Tier Open Bookshelf", "Study", "shelf", 64900, '35" W × 13" D × 59" H', "White oak · adjustable shelves", "2 weeks", 10],
    ["l1", "Glow Brass Floor Lamp", "Lighting", "lamp", 39900, '16" W × 16" D × 62" H', "Brass shade · E26 socket", "1 week", 15],
    ["tv1", "Still 2-Door Media Console", "Living", "cabinet", 99900, '71" W × 17" D × 22" H', "Black walnut · push-to-open doors", "3 weeks", 5],
    ["t2", "Pause Round Side Table", "Living", "table", 22900, '20" Ø × 22" H', "White oak · solid legs", "1 week", 2],
  ];
  const CUSTOMERS = [
    ["Emily Carter", "emily.carter@example.com", "(415) 555-0142", "742 Valencia St", "Apt 3", "San Francisco", "CA", "94110"],
    ["James Nguyen", "james.nguyen@example.com", "(206) 555-0199", "1520 E Pike St", "", "Seattle", "WA", "98122"],
    ["Sofia Ramirez", "sofia.r@example.com", "(512) 555-0173", "908 W 34th St", "Unit B", "Austin", "TX", "78705"],
    ["Daniel Kim", "dkim@example.com", "(718) 555-0128", "215 Bedford Ave", "Fl 2", "Brooklyn", "NY", "11211"],
    ["Grace Liu", "grace.liu@example.com", "(626) 555-0116", "88 E Colorado Blvd", "", "Pasadena", "CA", "91105"],
  ];
  const now = () => new Date().toISOString();
  const err = (msg, status = 400) => Object.assign(new Error(msg), { status });
  const delay = (v, ms = 120) => new Promise(r => setTimeout(() => r(v), ms));
  const clone = v => JSON.parse(JSON.stringify(v));
  const rid = p => p + Math.random().toString(16).slice(2, 10);

  // ---------- state
  let mem = null;
  function seed() {
    return {
      products: SEED.map((p, i) => ({ id: p[0], name: p[1], cat: p[2], art: p[3], price: p[4], dim: p[5], mat: p[6], lead: p[7], stock: p[8],
        active: true, photo: null, sort: i, spin: p[0] === "tv1" ? { kind: "file", count: 24 } : null, updated_at: now() })),
      orders: [], events: [], admin: false,
    };
  }
  function load() {
    if (mem) return mem;
    try { mem = JSON.parse(localStorage.getItem(KEY)); } catch (e) {}
    if (!mem || !mem.products) mem = seed();
    return mem;
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(mem)); } catch (e) {} }

  // ---------- images in IndexedDB (falls back to memory)
  const memBlobs = new Map();
  const idb = new Promise(res => {
    try {
      const r = indexedDB.open(KEY, 1);
      r.onupgradeneeded = () => r.result.createObjectStore("blobs");
      r.onsuccess = () => res(r.result); r.onerror = () => res(null);
    } catch (e) { res(null); }
  });
  async function putBlob(k, b) {
    memBlobs.set(k, b);
    const d = await idb; if (!d) return;
    await new Promise(r => { try { const t = d.transaction("blobs", "readwrite"); t.objectStore("blobs").put(b, k); t.oncomplete = t.onerror = r; } catch (e) { r(); } });
  }
  async function getBlob(k) {
    if (memBlobs.has(k)) return memBlobs.get(k);
    const d = await idb; if (!d) return null;
    return new Promise(r => { try { const q = d.transaction("blobs").objectStore("blobs").get(k); q.onsuccess = () => { if (q.result) memBlobs.set(k, q.result); r(q.result || null); }; q.onerror = () => r(null); } catch (e) { r(null); } });
  }
  const urlCache = new Map();
  async function blobUrl(k) {
    const b = await getBlob(k); if (!b) return null;
    const hit = urlCache.get(k); if (hit && hit.b === b) return hit.u;
    const u = URL.createObjectURL(b); urlCache.set(k, { b, u }); return u;
  }

  async function productOut(p) {
    let frames = [];
    if (p.spin && p.spin.kind === "file") frames = Array.from({ length: p.spin.count }, (_, i) => `demo-spin/tv1/${String(i).padStart(2, "0")}.jpg`);
    else if (p.spin) frames = (await Promise.all(Array.from({ length: p.spin.count }, (_, i) => blobUrl(`${p.id}/spin/${p.spin.v}/${i}`)))).filter(Boolean);
    const photo = p.photo ? await blobUrl(`${p.id}/photo/${p.photo}`) : null;
    const { spin, ...rest } = p;
    return { ...clone(rest), photo, spin_frames: frames };
  }

  // ---------- orders
  function event(oid, kind, text) { load().events.push({ order_id: oid, at: now(), kind, text }); }
  function orderNo() {
    const d = new Date(), day = `${String(d.getFullYear()).slice(2)}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
    return `MJ${day}-${String(load().orders.filter(o => o.order_no.startsWith("MJ" + day)).length + 1).padStart(3, "0")}`;
  }
  function method(sub, ship) { return ship === WHITE ? "White-glove delivery & assembly" : ship === 0 ? "Free standard delivery" : "Standard delivery"; }
  function validate(items) {
    const s = load(), out = []; let sub = 0;
    for (const it of items || []) {
      const p = s.products.find(x => x.id === it.id && x.active);
      if (!p || !(it.qty >= 1 && it.qty <= 10)) throw err("Invalid item in cart: " + it.id);
      if (p.stock < it.qty) throw err(`Only ${p.stock} left of ${p.name}.`);
      out.push({ id: p.id, name: p.name, price: p.price, qty: it.qty }); sub += p.price * it.qty;
    }
    if (!out.length) throw err("Your cart is empty.");
    return [out, sub];
  }
  function createPaid(items, sub, white, test) {
    const s = load(), c = CUSTOMERS[Math.floor(Math.random() * CUSTOMERS.length)];
    const ship = white ? WHITE : sub >= FREE ? 0 : STD;
    const o = { id: rid(test ? "test_" : "demo_"), order_no: orderNo(), status: "paid", items, subtotal: sub, shipping: ship, total: sub + ship,
      email: c[1], phone: c[2], ship_name: c[0], ship_line1: c[3], ship_line2: c[4], ship_city: c[5], ship_state: c[6], ship_zip: c[7],
      ship_method: method(sub, ship), carrier: "", tracking: "", note: "", test: true, created_at: now(), paid_at: now(), shipped_at: null, delivered_at: null };
    s.orders.unshift(o);
    event(o.id, "created", test ? "测试订单已创建" : "顾客发起结账（预览版模拟付款）");
    items.forEach(i => { const p = s.products.find(x => x.id === i.id); if (p) p.stock = Math.max(0, p.stock - i.qty); });
    event(o.id, "paid", `已付款 ${usd(o.total)}`);
    save(); return o;
  }
  function withTracking(o) { const t = CARRIERS[o.carrier]; return { ...clone(o), tracking_url: t && o.tracking ? t.replace("{}", o.tracking) : "" }; }
  function findOrder(id) { const o = load().orders.find(x => x.id === id); if (!o) throw err("找不到这个订单", 404); return o; }
  const needAdmin = () => { if (!load().admin) throw err("请先登录后台", 401); };

  window.api = {
    mode: "preview",
    store: () => delay(clone(STORE), 0),
    async products() { return Promise.all(load().products.filter(p => p.active).sort((a, b) => a.sort - b.sort).map(productOut)); },
    async checkout(items) {
      const [out, sub] = validate(items);
      const o = createPaid(out, sub, false, false);
      return delay({ url: "success.html?session_id=" + o.id }, 500);
    },
    async order(id) { return withTracking(findOrder(id)); },

    me: () => delay({ admin: load().admin, configured: true, demo_checkout: true }, 0),
    async login(pw) { if (pw !== "demo") throw err("密码不对（预览版密码是 demo）", 401); load().admin = true; save(); return { ok: true }; },
    async logout() { load().admin = false; save(); return { ok: true }; },
    async orders(status) {
      needAdmin(); const s = load(), counts = {};
      s.orders.forEach(o => counts[o.status] = (counts[o.status] || 0) + 1);
      return delay({ orders: s.orders.filter(o => !status || o.status === status).map(withTracking), counts });
    },
    async getOrder(id) {
      needAdmin();
      return delay({ ...withTracking(findOrder(id)), events: load().events.filter(e => e.order_id === id).map(({ at, kind, text }) => ({ at, kind, text })) }, 60);
    },
    async updateOrder(id, b) {
      needAdmin(); const s = load(), o = findOrder(id);
      if ("note" in b) { o.note = String(b.note).slice(0, 2000); event(id, "note", "更新了备注"); }
      if ("carrier" in b || "tracking" in b) {
        o.carrier = b.carrier ?? o.carrier; o.tracking = String(b.tracking ?? o.tracking).replace(/\s+/g, "");
        if (o.tracking) event(id, "tracking", `快递单号：${o.carrier} ${o.tracking}`);
      }
      if (b.status && b.status !== o.status) {
        const allowed = { paid: ["shipped", "canceled"], shipped: ["delivered", "paid"], delivered: ["shipped"], pending: ["canceled"], canceled: [] };
        if (!(allowed[o.status] || []).includes(b.status)) throw err(`不能从「${o.status}」改成「${b.status}」`);
        if (b.status === "canceled" && o.status === "paid") o.items.forEach(i => { const p = s.products.find(x => x.id === i.id); if (p) p.stock += i.qty; });
        o.status = b.status;
        if (b.status === "shipped") o.shipped_at = now();
        if (b.status === "delivered") o.delivered_at = now();
        event(id, b.status, { shipped: "标记为已发货", delivered: "标记为已送达", canceled: "订单已取消（库存已退回）", paid: "退回到待发货" }[b.status]);
        if (b.status === "shipped") event(id, "email", `📧 发货通知邮件：预览版不发送，正式版会发到 ${o.email}`);
      }
      save(); return this.getOrder(id);
    },
    async testOrder() {
      needAdmin();
      const avail = load().products.filter(p => p.active && p.stock > 0);
      if (!avail.length) throw err("所有商品都没有库存了，先去商品管理补货");
      const k = Math.min(avail.length, [1, 1, 2, 3][Math.floor(Math.random() * 4)]);
      const picks = avail.sort(() => Math.random() - 0.5).slice(0, k);
      const [out, sub] = validate(picks.map(p => ({ id: p.id, qty: 1 })));
      const o = createPaid(out, sub, Math.random() < 0.3, true);
      return this.getOrder(o.id);
    },

    async adminProducts() { needAdmin(); return Promise.all(load().products.slice().sort((a, b) => a.sort - b.sort).map(productOut)); },
    async saveProduct(b) {
      needAdmin(); const s = load();
      if (!b.name || !(b.price > 0) || !(b.stock >= 0)) throw err("请填写商品名称、大于 0 的价格和不小于 0 的库存");
      let p = b.id && s.products.find(x => x.id === b.id);
      if (!p) { p = { id: (b.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 30) || "item") + "-" + Math.random().toString(16).slice(2, 6),
        photo: null, spin: null, sort: s.products.length }; s.products.push(p); }
      Object.assign(p, { name: b.name, cat: b.cat, art: b.art, price: b.price, stock: b.stock, dim: b.dim, mat: b.mat, lead: b.lead, active: !!b.active, updated_at: now() });
      save(); return productOut(p);
    },
    async deleteProduct(id) { needAdmin(); const s = load(); s.products = s.products.filter(p => p.id !== id); save(); return { ok: true }; },
    async uploadPhoto(id, blob) {
      needAdmin(); const p = load().products.find(x => x.id === id); if (!p) throw err("找不到商品", 404);
      const v = Date.now(); await putBlob(`${id}/photo/${v}`, blob); p.photo = v; save(); return productOut(p);
    },
    async uploadSpin(id, blobs) {
      needAdmin(); const p = load().products.find(x => x.id === id); if (!p) throw err("找不到商品", 404);
      if (blobs.length < 8 || blobs.length > 72) throw err("360 图需要 8 到 72 张照片");
      const v = Date.now();
      for (let i = 0; i < blobs.length; i++) await putBlob(`${id}/spin/${v}/${i}`, blobs[i]);
      p.spin = { kind: "blob", v, count: blobs.length }; save(); return productOut(p);
    },
    async clearSpin(id) { needAdmin(); const p = load().products.find(x => x.id === id); p.spin = null; save(); return productOut(p); },
  };
})();
