// 👥 Customer address book: everyone who ordered, what they're like (from their orders and notes),
// tags and a "remember this person" memo, and a card on top of each order.
// Uses helpers from admin.html ($, esc, t, tr, toast, guard, fmtTime, usd, pill, openPanel, closePanel, openOrder, pname).

let CUSTS = [], CUST_TAGS = [], custQ = "", custTag = "", custSort = "recent";
const TAG_PRESETS = ["⭐ VIP", "🔁 回头客", "🎨 设计师", "🏢 批发/公司", "🤝 朋友介绍", "😤 需要耐心", "📞 喜欢电话联系", "💬 喜欢短信"];

// Words in notes that tell you something about the person. [key, emoji, label, pattern]
const NOTE_KW = [
  ["dog", "🐕", "有狗", /\b(dogs?|pupp(y|ies))\b|狗|perro/i],
  ["cat", "🐈", "有猫", /\b(cats?|kittens?)\b|猫|gato/i],
  ["gate", "🔑", "门禁码", /\b(gate|code|buzz(er)?|call ?box|lock ?box)\b|门禁|密码|código/i],
  ["call", "📞", "到之前先联系", /\b(call|text|phone)\b.{0,12}\b(before|ahead|first|when)\b|打电话|发短信|先联系|llam/i],
  ["stairs", "🪜", "有楼梯", /\bstairs?|staircase|楼梯|escaler/i],
  ["narrow", "📏", "门 / 走廊窄", /\bnarrow|tight\s+(door|hall|turn|corner)|门窄|门框|走廊窄|estrech/i],
  ["parking", "🅿️", "停车要注意", /\bpark(ing)?\b|停车|estacion/i],
  ["kids", "👶", "家里有小孩", /\b(baby|kids?|child(ren)?|toddlers?)\b|孩子|小孩|宝宝|niñ/i],
  ["elder", "🧓", "家里有老人", /\b(elderly|senior|grandma|grandpa|wheelchair)\b|老人|轮椅|abuel/i],
  ["gift", "🎁", "买来送人", /\bgift|surprise|礼物|惊喜|regalo/i],
  ["assembly", "🔧", "要组装 / 安装", /\bassembl|install|组装|安装|armar/i],
  ["fragile", "⚠️", "要小心轻放", /\bfragile|careful|scratch|易碎|小心|轻放|cuidado/i],
  ["morning", "🌅", "喜欢上午", /\bmornings?\b|上午|早上|mañana/i],
  ["evening", "🌆", "下班后才在家", /\bevenings?\b|after\s+(5|6|work)|晚上|下班|noche/i],
  ["weekend", "📅", "周末方便", /\bweekends?|saturday|sunday|周末|周六|周日|sábado|domingo/i],
  ["spanish", "🗣️", "说西班牙语", /\bspanish\b|español|西班牙语/i],
  ["patience", "😤", "需要多点耐心", /\bcomplain|upset|unhappy|angry|picky|demanding|投诉|不满|挑剔|难缠|生气|queja/i],
  ["designer", "🎨", "设计师 / 同行", /\b(interior\s+)?designer|stager|architect|设计师|装修|diseñador/i],
  ["measure", "📐", "很在意尺寸", /\bmeasure|dimensions?|\bfits?\b|尺寸|量过|放得下|medid/i],
  ["color", "🎨", "在意颜色 / 木色", /\bcolou?rs?\b|\bstain|\bfinish\b|\bshade|颜色|色差|木色/i],
  ["price", "💲", "在意价格", /\bdiscount|coupon|price\s*match|cheaper|折扣|优惠|便宜|descuento/i],
  ["rush", "⚡", "着急要货", /\basap\b|urgent|\brush\b|hurry|着急|尽快|urgente/i],
];
const MATS = [["walnut", "胡桃木"], ["oak", "橡木"], ["cherry", "樱桃木"], ["maple", "枫木"], ["ash", "白蜡木"], ["teak", "柚木"],
  ["pine", "松木"], ["brass", "黄铜"], ["leather", "真皮"], ["linen", "亚麻"], ["marble", "大理石"], ["rattan", "藤编"]];
const CUST_CAT = { Living: "客厅", Dining: "餐厅", Bedroom: "卧室", Study: "书房", Lighting: "灯具" };
const SERVICE_ZH = { standard: "快递", curbside: "门口卸货", threshold: "送进门", white_glove: "白手套进屋安装" };
const HOME = { house: ["🏡", "独立屋"], apartment: ["🏢", "公寓"], business: ["🏬", "商业地址"] };
const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function hue(s) { let h = 0; for (const ch of String(s)) h = (h * 31 + ch.codePointAt(0)) % 360; return h; }
function initials(p) {
  const n = (p.name || p.email || p.phone || "?").trim();
  const parts = n.split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : n.slice(0, 2)).toUpperCase();
}
function avatar(p, big) { return `<span class="cav${big ? " big" : ""}" style="--h:${hue(p.key)}">${esc(initials(p))}</span>`; }
const mode = arr => { const n = {}; let best = null; arr.filter(Boolean).forEach(x => { n[x] = (n[x] || 0) + 1; if (!best || n[x] > n[best]) best = x; }); return best ? [best, n[best]] : [null, 0]; };
const shortDate = s => { if (!s) return ""; const d = new Date(s); return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`; };

function noteHits(text) { return NOTE_KW.filter(k => k[3].test(text || "")); }
function highlight(text) {
  let h = esc(text);
  NOTE_KW.forEach(([k, , , re]) => {
    const g = new RegExp(re.source, re.flags.includes("g") ? re.flags : re.flags + "g");
    h = h.replace(g, m => `<mark class="kw-${k}">${m}</mark>`);
  });
  return h;
}

// Everything the orders say about this person, grouped. Each trait: [group, emoji, text, why]
function traitsOf(p) {
  const os = p.orders_list || [], live = os.filter(o => ["paid", "shipped", "delivered"].includes(o.status)), out = [];
  if (live.length >= 2) out.push(["who", "🔁", t("回头客 · {n} 单", { n: live.length }), t("在这里买过 {n} 次", { n: live.length })]);
  else if (live.length === 1) out.push(["who", "🆕", t("新顾客"), t("第一次在这里买")]);
  if (p.spent >= 250000) out.push(["who", "💎", t("大客户 · 累计 {amt}", { amt: usd(p.spent) }), ""]);
  if (p.canceled) out.push(["who", "⛔", t("取消过 {n} 单", { n: p.canceled }), ""]);
  if (os.some(o => o.refunded)) out.push(["who", "💸", t("退过款"), ""]);
  const lg = (os.find(o => o.lang) || {}).lang;
  if (lg === "es") out.push(["who", "🇲🇽", t("用西班牙语下单"), t("发给他的邮件和短信用西班牙语")]);
  if (lg === "zh") out.push(["who", "🇨🇳", t("用中文下单"), t("发给他的邮件和短信用中文")]);

  const h = os.find(o => o.home_type);
  if (h) {
    const [e, name] = HOME[h.home_type] || ["🏠", h.home_type];
    const bits = [t(name)];
    if (h.floor) bits.push(t("{n} 楼", { n: h.floor }));
    if (h.elevator === 1 || h.elevator === true) bits.push(t("有电梯")); else if (h.elevator === 0 || h.elevator === false) bits.push(t("没电梯"));
    out.push(["home", e, bits.join(" · "), h.floor >= 3 && !h.elevator ? t("⚠️ 高楼层没电梯，大件要多派人") : ""]);
  }
  if (os.some(o => o.liftgate)) out.push(["home", "🚛", t("卸货要尾板"), ""]);
  if (p.addresses && p.addresses.length > 1) out.push(["home", "📍", t("{n} 个收货地址", { n: p.addresses.length }), p.addresses.join(" / ")]);

  const [svc, sn] = mode(os.map(o => o.service).filter(s => s && s !== "standard"));
  if (svc) out.push(["ship", svc === "white_glove" ? "🧤" : "🚚", t("常选：{s}", { s: t(SERVICE_ZH[svc] || svc) }), sn > 1 ? t("{n} 次", { n: sn }) : ""]);
  const wins = os.map(o => o.appt_window).filter(Boolean);
  const [win, wn] = mode(wins);
  if (win) {
    const lab = ((typeof SETTINGS !== "undefined" && SETTINGS && SETTINGS.windows) || []).find(w => w.id === win);
    out.push(["ship", "🕘", t("约的时段：{w}", { w: lab ? lab.label : win }), wn > 1 ? t("{n} 次都选这个", { n: wn }) : ""]);
  }
  const [wd, wdn] = mode(os.filter(o => o.appt_date).map(o => WEEK[new Date(o.appt_date + "T12:00:00").getDay()]));
  if (wd && wdn >= 2) out.push(["ship", "📅", t("常约{d}", { d: t(wd) }), ""]);

  const items = os.flatMap(o => o.items || []);
  const [cat, cn] = mode(items.map(i => i.cat));
  if (cat && items.length > 1) out.push(["taste", "🛋️", t("常买{c}家具", { c: t(CUST_CAT[cat] || cat) }), t("{n} 件", { n: cn })]);
  const matCount = MATS.map(([en, zh]) => [zh, items.filter(i => (i.mat || "").toLowerCase().includes(en)).length]).filter(x => x[1]).sort((a, b) => b[1] - a[1]);
  if (matCount.length) out.push(["taste", "🪵", t("喜欢{m}", { m: t(matCount[0][0]) }), matCount.length > 1 ? t("也买过：{list}", { list: matCount.slice(1).map(x => t(x[0])).join(t("、")) }) : ""]);
  const top = live.map(o => o.total || 0).sort((a, b) => b - a)[0];
  if (top) out.push(["taste", "💰", t("最大一单 {amt}", { amt: usd(top) }), live.length > 1 ? t("平均每单 {amt}", { amt: usd(Math.round(p.spent / live.length)) }) : ""]);

  const texts = (p.notes || []).map(n => n.text).concat(os.map(o => o.access_notes).filter(Boolean));
  NOTE_KW.forEach(([k, e, label, re]) => {
    const n = texts.filter(x => re.test(x)).length;
    if (n) out.push(["notes", e, t(label), n > 1 ? t("备注里提到 {n} 次", { n }) : t("备注里提到过"), k]);
  });
  return out;
}
const GROUPS = [["notes", "📝 备注里提到"], ["home", "🏠 家里"], ["ship", "🚚 送货习惯"], ["taste", "🛋️ 喜好"], ["who", "👤 这个人"]];
function chip(tr_) { return `<span class="tchip g-${tr_[0]}" ${tr_[3] ? `title="${esc(tr_[3])}"` : ""} ${tr_[4] ? `data-kw="${tr_[4]}"` : ""}>${tr_[1]} ${esc(tr_[2])}</span>`; }

// ---------------------------------------------------------------- address book
async function loadCustomers() {
  const d = await guard(() => api.customers()); if (!d) return;
  CUSTS = d.customers; CUST_TAGS = d.tags;
  renderCustomers();
}
function custMatch(p) {
  if (custTag && !p.tags.includes(custTag)) return false;
  if (!custQ) return true;
  const q = custQ.toLowerCase(), digitsQ = custQ.replace(/\D/g, "");
  return [p.name, p.email, p.city, p.state, p.memo, p.address, ...p.tags].join(" ").toLowerCase().includes(q) ||
    (digitsQ.length >= 3 && (p.phone || "").replace(/\D/g, "").includes(digitsQ));
}
function renderCustomers() {
  const sorters = { recent: (a, b) => (b.last_at || "").localeCompare(a.last_at || ""), spent: (a, b) => b.spent - a.spent,
    orders: (a, b) => b.orders - a.orders, name: (a, b) => (a.name || a.email).localeCompare(b.name || b.email) };
  const list = CUSTS.filter(custMatch).sort(sorters[custSort]);
  const real = CUSTS.some(p => !p.test) ? CUSTS.filter(p => !p.test) : CUSTS, repeat = real.filter(p => p.orders >= 2).length;
  $("customersView").innerHTML = `
    <div class="toolbar"><h1>${t("顾客")}</h1>
      <input class="in search" id="custSearch" type="search" placeholder="${t("🔎 搜名字、电话、邮箱、城市、标签、备注")}" value="${esc(custQ)}" autocomplete="off">
      <select class="in" id="custSort" style="width:auto">${[["recent", "最近下单"], ["spent", "累计消费"], ["orders", "订单最多"], ["name", "名字"]].map(([v, l]) => `<option value="${v}" ${v === custSort ? "selected" : ""}>${t("排序：")}${t(l)}</option>`).join("")}</select>
      <div class="grow"></div>
      <a class="btn" href="${esc(api.customersVcfUrl())}" download>${t("📇 导出到手机通讯录")}</a>
    </div>
    <div class="cstats"><span>👥 ${t("{n} 位顾客", { n: real.length })}</span><span>🔁 ${t("回头客 {n} 位", { n: repeat })}</span>
      <span>💰 ${t("累计 {amt}", { amt: usd(real.reduce((s, p) => s + p.spent, 0)) })}</span></div>
    ${CUST_TAGS.length ? `<div class="filters">${[["", t("全部")]].concat(CUST_TAGS.map(x => [x, x])).map(([v, l]) => `<button class="chip ${custTag === v ? "on" : ""}" data-ctag="${esc(v)}">${esc(l)}</button>`).join("")}</div>` : ""}
    <div class="list clist">${list.length ? list.map(p => `
      <button class="crow" data-cust="${esc(p.key)}">
        ${avatar(p)}
        <span class="cmain"><b>${esc(p.name || p.email || p.phone)}${p.test ? ` <span class="tag">${t("测试")}</span>` : ""}</b>
          <span class="sub">${esc([p.city && `${p.city}${p.state ? ", " + p.state : ""}`, p.phone, p.email].filter(Boolean).join(" · "))}</span>
          ${p.tags.length || p.memo ? `<span class="ctags">${p.tags.map(x => `<span class="tchip g-tag">${esc(x)}</span>`).join("")}${p.memo ? `<span class="cmemo">📌 ${esc(p.memo.split("\n")[0].slice(0, 60))}</span>` : ""}</span>` : ""}</span>
        <span class="cnum"><b>${usd(p.spent)}</b><span class="sub">🛒 ${p.orders}${p.orders >= 2 ? " 🔁" : ""} · ${shortDate(p.last_at)}</span></span>
      </button>`).join("") : `<div class="empty">${custQ || custTag ? t("没有找到") : t("还没有顾客，有人下单后会自动出现在这里")}</div>`}</div>`;
  $("custSearch").oninput = e => { custQ = e.target.value.trim(); const pos = e.target.selectionStart; renderCustomers(); const i = $("custSearch"); i.focus(); try { i.setSelectionRange(pos, pos); } catch (x) {} };
  $("custSort").onchange = e => { custSort = e.target.value; renderCustomers(); };
  $("customersView").onclick = e => {
    const tg = e.target.closest("[data-ctag]"); if (tg) { custTag = tg.dataset.ctag; renderCustomers(); return; }
    const r = e.target.closest("[data-cust]"); if (r) openCustomer(r.dataset.cust);
  };
}

// ---------------------------------------------------------------- one customer
async function openCustomer(key) {
  if (typeof getSettings === "function") await getSettings().catch(() => null);
  const p = await guard(() => api.customer(key)); if (!p) return;
  const tr_ = traitsOf(p);
  const notes = (p.notes || []).map(n => ({ text: n.text, at: n.created_at, order_no: n.order_no, order_id: n.order_id }))
    .concat((p.orders_list || []).filter(o => o.access_notes).map(o => ({ text: o.access_notes, at: o.paid_at || o.created_at, order_no: o.order_no, order_id: o.id, fromCustomer: true })))
    .sort((a, b) => (b.at || "").localeCompare(a.at || ""));
  const tel = (p.phone || "").replace(/[^\d+]/g, "");
  openPanel(`
    <div class="ph"><h2>${t("👤 顾客档案")}</h2><button class="x" data-close aria-label="${t("关闭")}">✕</button></div>
    <div class="pb">
      <div class="chead">${avatar(p, true)}<div><h3>${esc(p.name || p.email || p.phone)}</h3>
        <div class="sub">${esc(p.email || "")}${p.email && p.phone ? " · " : ""}${esc(p.phone || "")}</div>
        <div class="actions">${tel ? `<a class="btn sm" href="tel:${esc(tel)}">📞 ${t("打电话")}</a><a class="btn sm" href="sms:${esc(tel)}">💬 ${t("发短信")}</a>` : ""}
          ${p.email ? `<a class="btn sm" href="mailto:${esc(p.email)}">📧 ${t("发邮件")}</a>` : ""}
          <button class="btn sm" type="button" data-copyc>📋 ${t("复制联系方式")}</button></div></div></div>
      <div class="cstats"><span>🛒 ${t("{n} 单", { n: p.orders })}</span><span>💰 ${usd(p.spent)}</span>
        <span>📅 ${t("第一次：")}${shortDate(p.first_at)}</span><span>🕒 ${t("最近：")}${shortDate(p.last_at)}</span></div>

      <div class="sec"><h3>${t("📌 记住这个人")}</h3>
        <textarea class="in" id="c-memo" rows="3" placeholder="${t("例如：说话直接，喜欢先看照片再确认；周六上午最好；儿子帮忙收货")}">${esc(p.memo || "")}</textarea>
        <div class="ctagedit" id="c-tags">${p.tags.map(x => `<span class="tchip g-tag">${esc(x)}<button type="button" data-untag="${esc(x)}" aria-label="${t("删除")}">✕</button></span>`).join("")}
          <input class="in" id="c-newtag" list="c-taglist" placeholder="${t("＋ 加标签")}" autocomplete="off"></div>
        <datalist id="c-taglist">${[...new Set(TAG_PRESETS.map(x => t(x)).concat(CUST_TAGS))].map(x => `<option value="${esc(x)}">`).join("")}</datalist>
        <div class="actions"><button class="btn primary" type="button" data-savec>💾 ${t("保存")}</button></div><div class="err" id="cErr"></div>
      </div>

      <div class="sec"><h3>${t("🧠 这位顾客")}</h3>
        ${tr_.length ? GROUPS.map(([g, title]) => { const xs = tr_.filter(x => x[0] === g); return xs.length ? `<div class="tgroup"><div class="tg-t">${t(title)}</div><div class="tg-c">${xs.map(chip).join("")}</div></div>` : ""; }).join("")
          : `<div class="sub">${t("还看不出来，多几单、多写点备注就会出现")}</div>`}
        ${tr_.some(x => x[3] && x[3].startsWith("⚠️")) ? `<div class="tip">${tr_.filter(x => x[3] && x[3].startsWith("⚠️")).map(x => esc(x[3])).join("<br>")}</div>` : ""}
      </div>

      <div class="sec"><h3>${t("🗒️ 便签墙")} <span class="sub">${t("所有订单的备注，点关键词只看相关的")}</span></h3>
        ${notes.length ? `<div class="wall" id="wall">${notes.map(n => { const hit = noteHits(n.text); return `
          <div class="sticky ${hit.length ? "k-" + hit[0][0] : ""}" data-kws="${hit.map(h => h[0]).join(" ")}">
            <div class="stext">${highlight(n.text)}</div>
            <div class="sfoot">${n.fromCustomer ? t("🙋 顾客预约时写的") : "📝"} · <a href="#" data-order="${esc(n.order_id)}">${esc(n.order_no)}</a> · ${shortDate(n.at)}</div>
          </div>`; }).join("")}</div>` : `<div class="sub">${t("还没有备注。在订单里写的备注会汇总到这里。")}</div>`}
      </div>

      <div class="sec"><h3>${t("📦 买过的")}</h3><div class="ctimeline">
        ${(p.orders_list || []).map(o => `<button class="corder" data-order="${esc(o.id)}"><span class="sub">${shortDate(o.paid_at || o.created_at)}</span>
          <span><b>${esc(o.order_no)}</b> ${pill(o.status)}${o.test ? ` <span class="tag">${t("测试")}</span>` : ""}<br><span class="sub">${o.items.map(i => `${esc(pname(i))} ×${i.qty}`).join(t("、"))}</span></span>
          <span class="cnum">${usd((o.total || 0) - (o.refunded || 0))}</span></button>`).join("")}
      </div></div>

      ${p.addresses && p.addresses.length ? `<div class="sec"><h3>${t("📍 收货地址")}</h3>${p.addresses.map(a => `<div class="box addr">${esc(a)}</div>`).join("")}</div>` : ""}
    </div>`);
  const panel = $("panel");
  panel.onclick = async e => {
    const b = e.target.closest("button, a"); if (!b) return;
    if ("close" in b.dataset) return closePanel();
    if (b.dataset.order) { e.preventDefault(); return openOrder(b.dataset.order); }
    if ("copyc" in b.dataset) { const txt = [p.name, p.phone, p.email, p.address].filter(Boolean).join("\n"); try { await navigator.clipboard.writeText(txt); toast(t("📋 已复制")); } catch (x) { prompt("", txt); } return; }
    if (b.dataset.untag) { b.closest(".tchip").remove(); return; }
    if ("savec" in b.dataset) return saveCustomer(p);
  };
  $("c-newtag").onkeydown = e => {
    if (e.key !== "Enter") return; e.preventDefault();
    const v = e.target.value.trim(); if (!v) return;
    e.target.insertAdjacentHTML("beforebegin", `<span class="tchip g-tag">${esc(v)}<button type="button" data-untag="${esc(v)}">✕</button></span>`); e.target.value = "";
  };
  $("c-newtag").onchange = e => { if (TAG_PRESETS.map(x => t(x)).concat(CUST_TAGS).includes(e.target.value)) $("c-newtag").onkeydown({ key: "Enter", preventDefault() {}, target: e.target }); };
  const wall = $("wall");
  panel.querySelectorAll("[data-kw]").forEach(c => c.onclick = () => {
    if (!wall) return;
    const on = c.classList.toggle("on");
    panel.querySelectorAll("[data-kw]").forEach(x => { if (x !== c) x.classList.remove("on"); });
    wall.querySelectorAll(".sticky").forEach(s => { s.hidden = on && !s.dataset.kws.split(" ").includes(c.dataset.kw); });
    if (on) wall.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
}
async function saveCustomer(p) {
  const tags = [...document.querySelectorAll("#c-tags .tchip")].map(x => x.firstChild.textContent.trim());
  const pending = $("c-newtag").value.trim(); if (pending) tags.push(pending);
  const r = await guard(() => api.saveCustomer(p.key, { tags, memo: $("c-memo").value }), $("cErr"));
  if (r) { toast(t("💾 已保存")); const i = CUSTS.findIndex(x => x.key === p.key); if (i >= 0) Object.assign(CUSTS[i], { tags: r.tags, memo: r.memo }); CUST_TAGS = [...new Set(CUST_TAGS.concat(r.tags))]; openCustomer(p.key); }
}

// ---------------------------------------------------------------- the card on top of an order
async function fillCustomerCard(o) {
  const box = $("custCard"); if (!box || !o.customer_key) return;
  if (typeof getSettings === "function") await getSettings().catch(() => null);
  const p = await api.customer(o.customer_key).catch(() => null);
  if (!p || !$("custCard")) return;
  const tr_ = traitsOf(p);
  const order = ["notes", "home", "who", "ship", "taste"];
  const pickd = tr_.slice().sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0])).slice(0, 6);
  box.innerHTML = `<div class="ccard">${avatar(p)}<div class="cc-main">
      <div><b>${esc(p.name || p.email)}</b> <span class="sub">${p.orders >= 2 ? t("买过 {n} 次 · 累计 {amt}", { n: p.orders, amt: usd(p.spent) }) : t("第一次在这里买")}</span></div>
      ${p.tags.length ? `<div class="ctags">${p.tags.map(x => `<span class="tchip g-tag">${esc(x)}</span>`).join("")}</div>` : ""}
      ${p.memo ? `<div class="cmemo">📌 ${esc(p.memo)}</div>` : ""}
      ${pickd.length ? `<div class="ctags">${pickd.map(chip).join("")}</div>` : ""}
    </div><button class="btn sm" type="button" data-cust="${esc(p.key)}">👤 ${t("档案")}</button></div>`;
  box.onclick = e => { const b = e.target.closest("[data-cust]"); if (b) { e.stopPropagation(); openCustomer(b.dataset.cust); } };
}
