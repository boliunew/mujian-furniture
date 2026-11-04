// Browser-only stand-in for public/api.js, used by the click-to-try preview.
// Same methods, same shapes as server.py. Data lives in this browser (localStorage + IndexedDB).
(function () {
  const KEY = "mujian-preview-v2";
  const STORE = { name: "Mujian Furniture", address: ["1200 Industrial Way", "Oakland, CA 94607"], email: "orders@example.com",
    phone: "(510) 555-0100", demo_checkout: true };
  const FEES = { standard: 4900, curbside: 7900, threshold: 9900, white_glove: 14900 }, FREE = 50000;
  const SERVICES = {
    standard: ["Standard parcel delivery", "快递"], curbside: ["Freight · curbside (liftgate)", "卡派 · 门口（尾板卸货）"],
    threshold: ["Freight · inside first door", "卡派 · 送进门"], white_glove: ["White-glove · room of choice + assembly", "白手套 · 进屋安装"],
  };
  const FS = ["unscheduled", "scheduled", "picked_up", "in_transit", "out_for_delivery", "delivered", "exception"];
  const FS_ZH = { unscheduled: "待预约", scheduled: "已预约", picked_up: "已装车/已提货", in_transit: "运输中", out_for_delivery: "派送中", delivered: "已签收", exception: "异常" };
  const FS_EN = { unscheduled: "Awaiting your booking", scheduled: "Delivery booked", picked_up: "Picked up", in_transit: "In transit",
    out_for_delivery: "Out for delivery", delivered: "Delivered", exception: "Delayed — we'll contact you" };
  const CLASSES = ["50", "55", "60", "65", "70", "77.5", "85", "92.5", "100", "110", "125", "150", "175", "200", "250", "300", "400", "500"];
  const DEFAULT_SETTINGS = {
    windows: [{ id: "am", label: "8 AM – 12 PM" }, { id: "pm", label: "12 PM – 5 PM" }], capacity: 4, weekdays: [1, 2, 3, 4, 5, 6],
    lead_days: 3, horizon_days: 30, blackout: [], cutoff_hour: 17, local_zips: "940,941,943,944,945,946,947,948,949,950,951",
    trucks: ["Truck 1", "Truck 2"], ltl_note: "The carrier will call 24–48 hours before delivery to confirm a 4-hour window.",
  };
  const CARRIERS = { USPS: "https://tools.usps.com/go/TrackConfirmAction?tLabels={}", UPS: "https://www.ups.com/track?tracknum={}",
    FedEx: "https://www.fedex.com/fedextrack/?trknbr={}", Freight: "" };
  const SEED = [
    ["s1", "Haven 3-Seat Sofa", "Living", "sofa", 189900, '83" W × 36" D × 31" H', "White oak frame · linen blend", "2–3 weeks", 6, "freight", 165, [86, 39, 34], "250"],
    ["c1", "Sit Walnut Dining Chair", "Dining", "chair", 34900, '18" W × 20" D × 31" H', "Black walnut · vegetable-tanned leather", "1 week", 24, "parcel", 21, [24, 22, 34], ""],
    ["t1", "Gather 6-Seat Dining Table", "Dining", "table", 149900, '71" W × 35" D × 30" H', "Solid white oak · hardwax oil", "3–4 weeks", 4, "freight", 140, [76, 40, 12], "150"],
    ["b1", "Rest Low Platform Bed, Queen", "Bedroom", "bed", 179900, '64" W × 85" D × 35" H', "Cherry · mortise-and-tenon joints", "4 weeks", 3, "freight", 190, [88, 20, 14], "150"],
    ["sh1", "Keep 3-Tier Open Bookshelf", "Study", "shelf", 64900, '35" W × 13" D × 59" H', "White oak · adjustable shelves", "2 weeks", 10, "freight", 78, [62, 38, 8], "125"],
    ["l1", "Glow Brass Floor Lamp", "Lighting", "lamp", 39900, '16" W × 16" D × 62" H', "Brass shade · E26 socket", "1 week", 15, "parcel", 18, [20, 20, 46], ""],
    ["tv1", "Still 2-Door Media Console", "Living", "cabinet", 99900, '71" W × 17" D × 22" H', "Black walnut · push-to-open doors", "3 weeks", 5, "freight", 120, [76, 22, 28], "175"],
    ["t2", "Pause Round Side Table", "Living", "table", 22900, '20" Ø × 22" H', "White oak · solid legs", "1 week", 2, "parcel", 16, [24, 24, 8], ""],
  ];
  const CUSTOMERS = [
    ["Emily Carter", "emily.carter@example.com", "(415) 555-0142", "742 Valencia St", "Apt 3", "San Francisco", "CA", "94110"],
    ["Maya Patel", "maya.patel@example.com", "(510) 555-0187", "2420 Telegraph Ave", "", "Berkeley", "CA", "94704"],
    ["James Nguyen", "james.nguyen@example.com", "(206) 555-0199", "1520 E Pike St", "", "Seattle", "WA", "98122"],
    ["Sofia Ramirez", "sofia.r@example.com", "(512) 555-0173", "908 W 34th St", "Unit B", "Austin", "TX", "78705"],
    ["Daniel Kim", "dkim@example.com", "(718) 555-0128", "215 Bedford Ave", "Fl 2", "Brooklyn", "NY", "11211"],
    ["Grace Liu", "grace.liu@example.com", "(626) 555-0116", "88 E Colorado Blvd", "", "Pasadena", "CA", "91105"],
  ];
  const now = () => new Date().toISOString();
  const err = (msg, status = 400) => Object.assign(new Error(msg), { status });
  const delay = (v, ms = 100) => new Promise(r => setTimeout(() => r(v), ms));
  const clone = v => JSON.parse(JSON.stringify(v));
  const rid = p => p + Math.random().toString(16).slice(2, 10);
  const pick = a => a[Math.floor(Math.random() * a.length)];
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const addDays = (ds, n) => { const d = new Date(ds + "T12:00:00"); d.setDate(d.getDate() + n); return iso(d); };
  const isoWeekday = ds => new Date(ds + "T12:00:00").getDay() || 7;
  const today = () => iso(new Date());
  const isDate = s => /^\d{4}-\d{2}-\d{2}$/.test(s || "");

  // ---------- state
  let mem = null;
  function seed() {
    return {
      products: SEED.map((p, i) => ({ id: p[0], name: p[1], cat: p[2], art: p[3], price: p[4], dim: p[5], mat: p[6], lead: p[7], stock: p[8],
        ship_mode: p[9], weight_lb: p[10], box_l: p[11][0], box_w: p[11][1], box_h: p[11][2], freight_class: p[12],
        active: true, photo: null, sort: i, spin: p[0] === "tv1" ? { kind: "file", count: 24 } : null, updated_at: now() })),
      orders: [], events: [], admin: false, settings: clone(DEFAULT_SETTINGS),
    };
  }
  function load() {
    if (mem) return mem;
    try { mem = JSON.parse(localStorage.getItem(KEY)); } catch (e) {}
    if (!mem || !mem.products) mem = seed();
    return mem;
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(mem)); } catch (e) {} }
  const S = () => load().settings;

  // ---------- images in IndexedDB (falls back to memory)
  const memBlobs = new Map();
  const idb = new Promise(res => {
    try { const r = indexedDB.open(KEY, 1); r.onupgradeneeded = () => r.result.createObjectStore("blobs"); r.onsuccess = () => res(r.result); r.onerror = () => res(null); }
    catch (e) { res(null); }
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

  // ---------- freight helpers (mirror freight.py)
  const winLabel = id => (S().windows.find(w => w.id === id) || {}).label || id || "";
  const isLocal = zip => S().local_zips.split(",").filter(Boolean).some(p => (zip || "").startsWith(p));
  function bookable() {
    const s = S(), out = [], first = addDays(today(), s.lead_days);
    for (let i = 0; i <= s.horizon_days; i++) {
      const d = addDays(first, i);
      if (s.weekdays.includes(isoWeekday(d)) && !s.blackout.includes(d)) out.push(d);
    }
    return out;
  }
  function slotsFor(o) {
    const s = S(), local = o.delivery_type === "local";
    return bookable().map(d => ({
      date: d, weekday: new Date(d + "T12:00:00").toLocaleDateString("en-US", { weekday: "short" }),
      windows: s.windows.map(w => {
        const n = local ? load().orders.filter(x => x.id !== o.id && x.delivery_type === "local" && x.status !== "canceled" && x.appt_date === d && x.appt_window === w.id).length : 0;
        return { id: w.id, label: w.label, left: local ? s.capacity - n : null, full: local && n >= s.capacity };
      }),
    }));
  }
  function canChange(o) {
    if (["canceled", "delivered"].includes(o.status) || ["out_for_delivery", "delivered", "in_transit", "picked_up"].includes(o.freight_status)) return false;
    if (!o.appt_date) return true;
    const cutoff = new Date(addDays(o.appt_date, -1) + "T00:00:00"); cutoff.setHours(S().cutoff_hour);
    return new Date() < cutoff;
  }
  function specs(items) {
    let pallets = 0, weight = 0, cls = 0;
    items.forEach(it => {
      const p = load().products.find(x => x.id === it.id);
      if (!p || p.ship_mode !== "freight") return;
      pallets += it.qty; weight += (p.weight_lb || 0) * it.qty; cls = Math.max(cls, parseFloat(p.freight_class) || 0);
    });
    return pallets ? [pallets, weight + 40 * pallets, cls ? String(cls) : "125"] : [0, 0, ""];
  }
  const mainFor = (fs, cur) => cur === "canceled" ? cur : ["picked_up", "in_transit", "out_for_delivery"].includes(fs) ? "shipped"
    : fs === "delivered" ? "delivered" : ["unscheduled", "scheduled"].includes(fs) ? "paid" : cur;

  // ---------- orders
  function event(oid, kind, text) { load().events.push({ order_id: oid, at: now(), kind, text }); }
  function orderNo() {
    const d = new Date(), day = `${String(d.getFullYear()).slice(2)}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
    return `MJ${day}-${String(load().orders.filter(o => o.order_no.startsWith("MJ" + day)).length + 1).padStart(3, "0")}`;
  }
  function validate(items) {
    const s = load(), out = []; let sub = 0;
    for (const it of items || []) {
      const p = s.products.find(x => x.id === it.id && x.active);
      if (!p || !(it.qty >= 1 && it.qty <= 10)) throw err("Invalid item in cart: " + it.id);
      if (p.stock < it.qty) throw err(`Only ${p.stock} left of ${p.name}.`);
      out.push({ id: p.id, name: p.name, price: p.price, qty: it.qty, ship_mode: p.ship_mode }); sub += p.price * it.qty;
    }
    if (!out.length) throw err("Your cart is empty.");
    return [out, sub];
  }
  function createPaid(items, sub, test, service) {
    const s = load(), c = pick(CUSTOMERS);
    const fulfillment = items.some(i => i.ship_mode === "freight") ? "freight" : "parcel";
    const choices = fulfillment === "parcel" ? ["standard"] : ["curbside", "threshold", "white_glove"];
    const svc = choices.includes(service) ? service : pick(choices);
    const fee = (svc === "standard" || svc === "curbside") && sub >= FREE ? 0 : FEES[svc];
    const o = { id: rid(test ? "test_" : "demo_"), order_no: orderNo(), status: "paid", items, subtotal: sub, shipping: fee, total: sub + fee,
      email: c[1], phone: c[2], ship_name: c[0], ship_line1: c[3], ship_line2: c[4], ship_city: c[5], ship_state: c[6], ship_zip: c[7],
      service: svc, ship_method: SERVICES[svc][0], fulfillment, carrier: "", tracking: "", note: "", test: true,
      created_at: now(), paid_at: now(), shipped_at: null, delivered_at: null,
      delivery_type: null, freight_status: null, appt_date: null, appt_window: null, sched_token: null, home_type: null, floor: null,
      elevator: null, liftgate: null, access_notes: null, truck: null, stop_no: null, pro: null, freight_carrier: null, pickup_date: null,
      pallets: null, weight_lb: null, freight_class: null, reminded_for: null };
    if (fulfillment === "freight") {
      const [pl, w, cl] = specs(items);
      Object.assign(o, { delivery_type: isLocal(o.ship_zip) ? "local" : "ltl", freight_status: "unscheduled",
        sched_token: Math.random().toString(36).slice(2, 12), pallets: pl, weight_lb: w, freight_class: cl, liftgate: true });
    }
    s.orders.unshift(o);
    event(o.id, "created", test ? "测试订单已创建" : "顾客发起结账（预览版模拟付款）");
    items.forEach(i => { const p = s.products.find(x => x.id === i.id); if (p) p.stock = Math.max(0, p.stock - i.qty); });
    event(o.id, "paid", `已付款 ${usd(o.total)}`);
    if (fulfillment === "freight") {
      event(o.id, "freight", `🚛 卡派订单：${o.delivery_type === "local" ? "本地车队" : "LTL 承运商"}，${o.pallets} 托 / ${o.weight_lb} lb / Class ${o.freight_class}`);
      event(o.id, "email", `📧 预约送货邮件：预览版不发送，正式版会发到 ${o.email}`);
    }
    save(); return o;
  }
  function out(o) {
    const t = CARRIERS[o.carrier];
    return { ...clone(o), tracking_url: t && o.tracking ? t.replace("{}", o.tracking) : "",
      schedule_url: o.sched_token ? `schedule.html?o=${o.id}&t=${o.sched_token}` : "",
      service_label: (SERVICES[o.service] || [])[1] || "", appt_window_label: winLabel(o.appt_window) };
  }
  function findOrder(id) { const o = load().orders.find(x => x.id === id); if (!o) throw err("找不到这个订单", 404); return o; }
  const needAdmin = () => { if (!load().admin) throw err("请先登录后台", 401); };
  function byToken(id, t) { const o = load().orders.find(x => x.id === id); if (!o || !o.sched_token || o.sched_token !== t) throw err("This booking link isn't valid. Please use the link from your email.", 404); return o; }
  function schedPayload(o) {
    const x = out(o);
    return { order_no: x.order_no, name: x.ship_name, address: [x.ship_line1, x.ship_line2, `${x.ship_city}, ${x.ship_state} ${x.ship_zip}`].filter(Boolean),
      items: x.items.map(i => ({ name: i.name, qty: i.qty, freight: i.ship_mode === "freight" })), service: SERVICES[x.service][0],
      delivery_type: x.delivery_type, ltl_note: S().ltl_note, appt_date: x.appt_date, appt_window: x.appt_window,
      appt_window_label: x.appt_window_label, home_type: x.home_type, floor: x.floor, elevator: x.elevator, liftgate: x.liftgate,
      access_notes: x.access_notes, can_change: canChange(o), status: FS_EN[x.freight_status] || "", cutoff_hour: S().cutoff_hour,
      store_phone: STORE.phone, slots: slotsFor(o) };
  }

  function freightPatch(o, b) {
    const notes = [], prevAppt = o.appt_date;
    if (b.delivery_type && b.delivery_type !== o.delivery_type && ["local", "ltl"].includes(b.delivery_type)) { o.delivery_type = b.delivery_type; notes.push("改为" + (b.delivery_type === "local" ? "本地车队配送" : "LTL 承运商")); }
    if ("appt_date" in b || "appt_window" in b) {
      const d = b.appt_date || null, w = b.appt_window || null;
      if (d && !isDate(d)) throw err("日期格式不对");
      if (w && !S().windows.some(x => x.id === w)) throw err("没有这个时段");
      if (d !== o.appt_date || w !== o.appt_window) {
        o.appt_date = d; o.appt_window = w;
        if (d && o.freight_status === "unscheduled") o.freight_status = "scheduled";
        notes.push(`后台${d ? "安排" : "清除"}送货时间${d ? "：" + d + " " + winLabel(w) : ""}`);
      }
    }
    const L = { truck: "车辆", pro: "PRO 单号", freight_carrier: "承运商", pickup_date: "提货日期", freight_class: "货物等级", access_notes: "送货备注" };
    for (const k in L) if (k in b && (b[k] || "") !== (o[k] || "")) { o[k] = String(b[k] || "").trim() || null; notes.push(`${L[k]}：${o[k] || "—"}`); }
    const N = { stop_no: "站序", pallets: "托盘数", weight_lb: "重量 lb", floor: "楼层" };
    for (const k in N) if (k in b && b[k] !== "" && b[k] != null && String(b[k]) !== String(o[k] ?? "")) {
      const n = parseInt(b[k], 10); if (isNaN(n)) throw err(`${N[k]}要填数字`); o[k] = Math.max(0, n); notes.push(`${N[k]}：${o[k]}`);
    }
    for (const [k, l] of [["liftgate", "尾板"], ["elevator", "电梯"]]) if (k in b && !!b[k] !== !!o[k]) { o[k] = !!b[k]; notes.push(`${l}：${o[k] ? "要" : "不要"}`); }
    const fs = b.freight_status;
    if (fs && fs !== o.freight_status) {
      if (!FS.includes(fs)) throw err("没有这个卡派状态");
      if (!["unscheduled", "exception"].includes(fs) && !o.appt_date) throw err("还没有送货时间，先帮顾客预约或在上面安排日期");
      o.freight_status = fs; notes.push(`🚛 卡派状态：${FS_ZH[fs]}`);
      const m = mainFor(fs, o.status);
      if (m !== o.status) {
        if (m === "shipped" && !o.shipped_at) { o.shipped_at = now(); }
        if (m === "delivered") o.delivered_at = now();
        const wasShipped = o.status === "shipped"; o.status = m;
        if (m === "shipped" && !wasShipped) notes.push("__ship");
      }
    }
    const ship = notes.includes("__ship");
    const clean = notes.filter(n => n !== "__ship");
    if (clean.length) event(o.id, "freight", clean.join("；"));
    if (o.appt_date && o.appt_date !== prevAppt) event(o.id, "email", `📧 预约通知邮件：预览版不发送，正式版会发到 ${o.email}`);
    if (ship) event(o.id, "email", `📧 发货通知邮件：预览版不发送，正式版会发到 ${o.email}`);
  }

  window.api = {
    mode: "preview",
    store: () => delay(clone(STORE), 0),
    async products() { return Promise.all(load().products.filter(p => p.active).sort((a, b) => a.sort - b.sort).map(productOut)); },
    async checkout(items) { const [o2, sub] = validate(items); const o = createPaid(o2, sub, false); return delay({ url: "success.html?session_id=" + o.id }, 450); },
    async order(id) { const o = out(findOrder(id)); return { ...o, freight_status: FS_EN[o.freight_status] || "" }; },
    async schedule(id, t) { return delay(schedPayload(byToken(id, t))); },
    async bookDelivery(id, b) {
      const o = byToken(id, b.t);
      if (!canChange(o)) throw err(`This delivery can't be changed online anymore. Please call us at ${STORE.phone}.`);
      const day = slotsFor(o).find(s => s.date === b.date), w = day && day.windows.find(x => x.id === b.window);
      if (!w) throw err("Please pick an available day and time window.");
      if (w.full) throw err("That window just filled up. Please pick another one.");
      const changed = !!o.appt_date;
      Object.assign(o, { appt_date: b.date, appt_window: b.window, home_type: ["house", "apartment", "business"].includes(b.home_type) ? b.home_type : "house",
        floor: Math.max(1, Math.min(60, parseInt(b.floor, 10) || 1)), elevator: !!b.elevator, liftgate: b.liftgate !== false,
        access_notes: String(b.access_notes || "").slice(0, 500) });
      if (o.freight_status === "unscheduled") o.freight_status = "scheduled";
      event(o.id, "schedule", `📅 顾客${changed ? "改约" : "预约"}：${o.appt_date} ${winLabel(o.appt_window)}`);
      event(o.id, "email", `📧 预约确认邮件：预览版不发送，正式版会发到 ${o.email}`);
      save(); return delay(schedPayload(o), 300);
    },

    me: () => delay({ admin: load().admin, configured: true, demo_checkout: true }, 0),
    async login(pw) { if (pw !== "demo") throw err("密码不对（预览版密码是 demo）", 401); load().admin = true; save(); return { ok: true }; },
    async logout() { load().admin = false; save(); return { ok: true }; },
    async orders(status) {
      needAdmin(); const s = load(), counts = {};
      s.orders.forEach(o => counts[o.status] = (counts[o.status] || 0) + 1);
      counts.unscheduled = s.orders.filter(o => o.status === "paid" && o.freight_status === "unscheduled").length;
      const list = s.orders.filter(o => !status || (status === "unscheduled" ? o.status === "paid" && o.freight_status === "unscheduled" : o.status === status));
      return delay({ orders: list.map(out), counts });
    },
    async getOrder(id) {
      needAdmin();
      return delay({ ...out(findOrder(id)), events: load().events.filter(e => e.order_id === id).map(({ at, kind, text }) => ({ at, kind, text })) }, 50);
    },
    async updateOrder(id, b) {
      needAdmin(); const s = load(), o = findOrder(id);
      if ("note" in b) { o.note = String(b.note).slice(0, 2000); event(id, "note", "更新了备注"); }
      if ("carrier" in b || "tracking" in b) {
        o.carrier = b.carrier ?? o.carrier; o.tracking = String(b.tracking ?? o.tracking).replace(/\s+/g, "");
        if (o.tracking) event(id, "tracking", `快递单号：${o.carrier} ${o.tracking}`);
      }
      if (o.fulfillment === "freight") freightPatch(o, b);
      if (b.status && b.status !== o.status) {
        if (o.fulfillment === "freight" && b.status !== "canceled") throw err("卡派订单请用下面的卡派状态按钮来更新");
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
    async resendBooking(id) { needAdmin(); const o = findOrder(id); event(id, "email", `📧 重发预约链接：预览版不发送，正式版会发到 ${o.email}`); save(); return this.getOrder(id); },
    async testOrder() {
      needAdmin();
      const avail = load().products.filter(p => p.active && p.stock > 0);
      if (!avail.length) throw err("所有商品都没有库存了，先去商品管理补货");
      const k = Math.min(avail.length, pick([1, 1, 2, 3]));
      const picks = avail.slice().sort(() => Math.random() - 0.5).slice(0, k);
      const [items, sub] = validate(picks.map(p => ({ id: p.id, qty: 1 })));
      return this.getOrder(createPaid(items, sub, true).id);
    },
    async deliveries(from, days = 14) {
      needAdmin(); const s = S(); from = from || today();
      const end = addDays(from, days - 1), list = load().orders.filter(o => o.fulfillment === "freight" && o.status !== "canceled" && o.appt_date && o.appt_date >= from && o.appt_date <= end);
      const rank = Object.fromEntries(s.windows.map((w, i) => [w.id, i]));
      list.sort((a, b) => a.appt_date.localeCompare(b.appt_date) || (rank[a.appt_window] ?? 9) - (rank[b.appt_window] ?? 9) || (a.stop_no || 99) - (b.stop_no || 99));
      const out_days = Array.from({ length: days }, (_, i) => {
        const d = addDays(from, i), orders = list.filter(o => o.appt_date === d).map(out);
        return { date: d, weekday: isoWeekday(d), open: s.weekdays.includes(isoWeekday(d)) && !s.blackout.includes(d),
          windows: s.windows.map(w => ({ id: w.id, label: w.label, local: orders.filter(o => o.appt_window === w.id && o.delivery_type === "local").length })), orders };
      });
      const waiting = load().orders.filter(o => o.fulfillment === "freight" && o.status === "paid" && (!o.appt_date || o.freight_status === "unscheduled")).map(out);
      return delay({ days: out_days, waiting, settings: clone(s) });
    },
    async settings() { needAdmin(); return clone(S()); },
    async saveSettings(b) {
      needAdmin();
      const w = (b.windows || []).filter(x => String(x.id || "").trim() && String(x.label || "").trim());
      if (!w.length) throw err("至少要有一个送货时段");
      const n = (v, lo, hi, d) => Math.max(lo, Math.min(hi, parseInt(v, 10) || d));
      const s = { windows: w.slice(0, 6).map(x => ({ id: String(x.id).slice(0, 12), label: String(x.label).slice(0, 40) })),
        capacity: n(b.capacity, 1, 50, 4), lead_days: n(b.lead_days, 0, 30, 3), horizon_days: n(b.horizon_days, 7, 120, 30), cutoff_hour: n(b.cutoff_hour, 0, 23, 17),
        weekdays: [...new Set((b.weekdays || []).map(Number).filter(d => d >= 1 && d <= 7))].sort(), blackout: [...new Set((b.blackout || []).filter(isDate))].sort(),
        local_zips: String(b.local_zips || "").replace(/\s/g, ",").split(",").filter(z => /^\d+$/.test(z)).join(","),
        trucks: (b.trucks || []).map(t => String(t).trim().slice(0, 30)).filter(Boolean).slice(0, 12), ltl_note: String(b.ltl_note || "").slice(0, 300) };
      if (!s.weekdays.length) s.weekdays = [1, 2, 3, 4, 5];
      if (!s.trucks.length) s.trucks = ["Truck 1"];
      load().settings = s; save(); return clone(s);
    },
    async sendReminders() {
      needAdmin(); const tom = addDays(today(), 1), sent = [];
      load().orders.filter(o => o.fulfillment === "freight" && o.appt_date === tom && o.status !== "canceled" && o.reminded_for !== tom).forEach(o => {
        o.reminded_for = tom; event(o.id, "email", `📧 送货前一天提醒：预览版不发送，正式版会发到 ${o.email}`); sent.push(o.order_no);
      });
      save(); return { sent };
    },

    async adminProducts() { needAdmin(); return Promise.all(load().products.slice().sort((a, b) => a.sort - b.sort).map(productOut)); },
    async saveProduct(b) {
      needAdmin(); const s = load();
      if (!b.name || !(b.price > 0) || !(b.stock >= 0)) throw err("请填写商品名称、大于 0 的价格和不小于 0 的库存");
      if (b.freight_class && !CLASSES.includes(String(b.freight_class))) throw err("货物等级不在 NMFC 标准等级里");
      let p = b.id && s.products.find(x => x.id === b.id);
      if (!p) { p = { id: (b.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 30) || "item") + "-" + Math.random().toString(16).slice(2, 6),
        photo: null, spin: null, sort: s.products.length }; s.products.push(p); }
      Object.assign(p, { name: b.name, cat: b.cat, art: b.art, price: b.price, stock: b.stock, dim: b.dim, mat: b.mat, lead: b.lead, active: !!b.active,
        ship_mode: b.ship_mode === "parcel" ? "parcel" : "freight", weight_lb: parseInt(b.weight_lb, 10) || 0,
        box_l: parseFloat(b.box_l) || null, box_w: parseFloat(b.box_w) || null, box_h: parseFloat(b.box_h) || null,
        freight_class: String(b.freight_class || ""), updated_at: now() });
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
