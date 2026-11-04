import json, sys
import os
S = os.path.dirname(os.path.abspath(__file__)) + "/"
ns = {}
exec(open(S + "admin_tr.py", encoding="utf-8").read(), ns); A = ns["T"]
ns = {}
exec(open(S + "store_tr.py", encoding="utf-8").read(), ns); B, EXTRA, RULES_EN = ns["T"], ns["EXTRA"], ns["RULES_EN"]
ns = {}
exec(open(S + "server_tr.py", encoding="utf-8").read(), ns); SX, SR = ns["EXACT"], ns["RULES"]
akeys = json.load(open(S + "keys2.json", encoding="utf-8"))
ekeys = json.load(open(S + "enkeys.json", encoding="utf-8"))
assert len(akeys) == len(A) and len(ekeys) == len(B), (len(akeys), len(A), len(ekeys), len(B))

en, es, zh = {}, {}, {}
for k, (e, s) in zip(akeys, A):
    en[k] = e; es[k] = s
for k, (e, s) in SX.items():
    en.setdefault(k, e); es.setdefault(k, s)
for k, (z, s) in zip(ekeys, B):
    zh[k] = z; es.setdefault(k, s)
for k, (z, s) in EXTRA.items():
    zh[k] = z; es[k] = s
# punctuation used between translated pieces
for k, e in {"、": ", ", "」": "”", "）": ")", "：": ": "}.items():
    en[k] = e; es[k] = {"」": "»"}.get(k, e)
# demo launcher (docs/index.html) — Chinese source
HOME = {
 "Mujian Store Preview": ["Mujian Store Preview", "Vista previa de Mujian"],
 "🪵 木间家具 · 商城 + 后台 + 卡派预约": ["🪵 Mujian Furniture · store + admin + truck delivery", "🪵 Mujian Furniture · tienda + panel + entregas en camión"],
 "商店和后台都能直接点着用。数据只存在你这个浏览器里，不连 Stripe，也不会扣款。": ["Both the store and the admin work right here. Data stays in this browser; nothing connects to Stripe and no card is charged.", "La tienda y el panel funcionan aquí mismo. Los datos se quedan en este navegador; no se conecta a Stripe ni se cobra nada."],
 "🛍️ 进入商店": ["🛍️ Open the store", "🛍️ Abrir la tienda"],
 "顾客看到的页面：选家具、看 360°、下单、预约送货": ["What customers see: browse, 360° views, checkout, delivery booking", "Lo que ve el cliente: catálogo, vistas 360°, pago y reserva de entrega"],
 "🧭 进入后台": ["🧭 Open the admin", "🧭 Abrir el panel"],
 "订单、卡派、托盘标签、库存盘点和商品。密码是 demo": ["Orders, freight, pallet labels, stock counts and products. Password: demo", "Pedidos, carga, etiquetas de tarima, conteos y productos. Contraseña: demo"],
 "建议这样体验": ["Try it like this", "Pruébalo así"],
 "🛍️ 进商店，点电视柜的图片，拖动看 360° 旋转。右上角 🌐 可以切换中文 / English / Español。": ["🛍️ Open the store, tap the media console photo and drag it to spin 360°. Switch 中文 / English / Español with 🌐 at the top right.", "🛍️ Abre la tienda, toca la foto del mueble de TV y arrástrala para girarla 360°. Cambia entre 中文 / English / Español con 🌐 arriba a la derecha."],
 "🛒 把电视柜或沙发加进购物车（大件走卡派），点结账模拟付款。": ["🛒 Add the media console or the sofa to the cart (big pieces go by truck) and check out with a simulated payment.", "🛒 Añade el mueble de TV o el sofá al carrito (las piezas grandes van en camión) y paga con un pago simulado."],
 "📅 确认页点「预约送货」，以顾客身份选送货日期和时段，填楼层、电梯、尾板。": ["📅 On the confirmation page tap “Book your delivery” and, as the customer, pick a day and window and fill in floor, elevator and liftgate.", "📅 En la confirmación toca «Reservar la entrega» y, como cliente, elige día y franja e indica piso, ascensor y plataforma."],
 "🧭 进后台，密码是 demo。打开刚才的订单，能看到每个托盘贴好标签的样子，可以打印托盘标签、装箱单、司机单和 BOL 提单。": ["🧭 Open the admin (password: demo). Open that order to see each pallet with its label, and print pallet labels, packing slips, driver manifests and bills of lading.", "🧭 Abre el panel (contraseña: demo). Abre ese pedido para ver cada tarima con su etiqueta e imprime etiquetas, albaranes, hojas de ruta y conocimientos de embarque."],
 "🗓️「送货」里看每天的预约和剩余名额；在订单里分配车辆和站序，或填 LTL 承运商和 PRO 单号，一步步推进到已签收。": ["🗓️ In “Deliveries” see each day's bookings and open spots; on the order assign a truck and stop, or an LTL carrier and PRO number, and move it step by step to signed for.", "🗓️ En «Entregas» ve las citas de cada día y los lugares libres; en el pedido asigna camión y parada, o transportista LTL y número PRO, y avánzalo paso a paso hasta recibido."],
 "🏬「库存」里看每个仓库的在库和占用，做盘点（可以扫码）、入库、调拨，所有变动都记在流水里。": ["🏬 In “Inventory” see on-hand and reserved stock per warehouse, run counts (with scanning), receive and transfer; every change goes into the ledger.", "🏬 En «Inventario» ve existencias y reservas por almacén, haz conteos (con escáner), recepciones y traspasos; todo queda en los movimientos."],
 "⚙️「设置」改送货时段、名额、本地 ZIP 和车辆；🪑「商品」可以填中文和西班牙语名称、包装重量和货物等级。": ["⚙️ “Settings” changes delivery windows, capacity, local ZIPs and trucks; in 🪑 “Products” you can add Chinese and Spanish names, packed weight and freight class.", "⚙️ En «Ajustes» cambia franjas, cupos, ZIP locales y camiones; en 🪑 «Productos» puedes añadir nombres en chino y español, peso embalado y clase de flete."],
 "🖨️ 预览里浏览器不允许弹出打印窗口，标签和装箱单会显示出来但打不出去。部署后的正式网站点「打印」会直接打开打印对话框。📧 发货邮件同理，预览版只记录不发送。": ["🖨️ The preview can't open the print dialog, so labels and slips show on screen but don't print. On the deployed site “Print” opens the dialog directly. 📧 Emails are logged here, not sent.", "🖨️ La vista previa no puede abrir la impresión: etiquetas y albaranes se ven en pantalla pero no se imprimen. En el sitio publicado «Imprimir» abre el diálogo directamente. 📧 Los correos se registran aquí, no se envían."],
 "🌐 其他语言（选填，不填就显示英文）": ["🌐 Other languages (optional; English shows if blank)", "🌐 Otros idiomas (opcional; si está vacío se muestra en inglés)"],
 "中文名称": ["Chinese name", "Nombre en chino"], "中文材质": ["Chinese material", "Material en chino"],
 "西班牙语名称": ["Spanish name", "Nombre en español"], "西班牙语材质": ["Spanish material", "Material en español"],
}
for k, (e, s) in HOME.items():
    en[k] = e; es[k] = s

def js(o): return json.dumps(o, ensure_ascii=False, separators=(",", ":"))
rules_zh = [{"re": r, "en": e, "es": s} for r, e, s in SR]
rules_en = [{"re": r, "zh": z, "es": s} for r, z, s in RULES_EN]

out = """// Mujian — three languages (English, 中文, Español).
// t(key, vars) translates UI text written in the page's source language (English on
// the store, Chinese in the admin); tr(text) also understands messages from the server.
// Generated dictionaries below; the code at the bottom is hand-written.
(function () {
  const D = { en: %s, es: %s, zh: %s };
  const RULES_ZH = %s;
  const RULES_EN = %s;
""" % (js(en), js(es), js(zh), js(rules_zh), js(rules_en))
out += r"""
  const SUP = ["en", "zh", "es"], LABEL = { en: "EN", zh: "中文", es: "ES" }, LOC = { en: "en-US", zh: "zh-CN", es: "es-US" };
  function detect() {
    try { const s = localStorage.getItem("mj-lang"); if (SUP.includes(s)) return s; } catch (e) {}
    for (const l of navigator.languages || [navigator.language || ""]) {
      const p = String(l).slice(0, 2).toLowerCase();
      if (SUP.includes(p)) return p;
    }
    return document.documentElement.dataset.langDefault || "en";
  }
  const LANG = detect();
  document.documentElement.lang = { en: "en", zh: "zh-CN", es: "es" }[LANG];

  function fill(s, v) { return v ? String(s).replace(/\{(\w+)\}/g, (m, n) => (v[n] !== undefined ? v[n] : m)) : s; }
  function t(k, v) {
    let s = D[LANG] && D[LANG][k];
    if (s === undefined) s = k;
    return fill(s, v);
  }
  function apply(rule, m, lang) {
    return rule[lang].replace(/\{t:\$(\d)\}/g, (x, n) => trSeg(m[+n] || "")).replace(/\$(\d)/g, (x, n) => m[+n] || "");
  }
  function trSeg(s) {
    if (!s) return s;
    const d = D[LANG] && D[LANG][s];
    if (d !== undefined) return d;
    if (LANG !== "zh") {
      for (const r of RULES_ZH) { const m = s.match(new RegExp(r.re)); if (m) return apply(r, m, LANG); }
      const kv = s.match(/^([^：]{1,30})：([\s\S]*)$/);
      if (kv && /[\u3400-\u9fff]/.test(kv[1])) return trSeg(kv[1]) + ": " + trSeg(kv[2]);
    }
    if (LANG !== "en") for (const r of RULES_EN) { const m = s.match(new RegExp(r.re)); if (m) return apply(r, m, LANG); }
    return s;
  }
  // Server messages: exact, patterns, "label：value" pieces, "；"-joined lists.
  function tr(text) {
    if (text == null || LANG === "zh" && !/[A-Za-z]{3}/.test(text)) return text;
    const s = String(text);
    if (LANG !== "zh" && s.includes("；")) return s.split("；").map(trSeg).join("; ");
    return trSeg(s);
  }
  function loc(p, f) { return (p && p.i18n && p.i18n[LANG] && p.i18n[LANG][f]) || (p ? p[f] : ""); }
  function setLang(l) {
    if (!SUP.includes(l) || l === LANG) return;
    try { localStorage.setItem("mj-lang", l); } catch (e) {}
    location.reload();
  }
  function switcher() {
    return `<span class="langsw" role="group" aria-label="Language">🌐${SUP.map(l =>
      `<button type="button" data-setlang="${l}" aria-pressed="${l === LANG}">${LABEL[l]}</button>`).join("")}</span>`;
  }
  // Static text in the HTML: translate exact matches of text nodes and a few attributes.
  function translateDom(root) {
    const dict = D[LANG] || {};
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: n => /^(SCRIPT|STYLE|TEXTAREA)$/.test(n.parentNode.nodeName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
    const nodes = [];
    while (w.nextNode()) nodes.push(w.currentNode);
    for (const n of nodes) {
      const m = n.nodeValue.match(/^(\s*)([\s\S]*?)(\s*)$/);
      if (m[2] && dict[m[2]] !== undefined) n.nodeValue = m[1] + dict[m[2]] + m[3];
    }
    root.querySelectorAll("[placeholder],[title],[aria-label],[alt]").forEach(el => {
      for (const a of ["placeholder", "title", "aria-label", "alt"]) {
        const v = el.getAttribute(a);
        if (v && dict[v] !== undefined) el.setAttribute(a, dict[v]);
      }
    });
  }
  const css = `.langsw{display:inline-flex;align-items:center;gap:2px;font-size:13px;white-space:nowrap}
.langsw button{border:1px solid transparent;background:transparent;color:inherit;font:inherit;font-size:12px;padding:3px 7px;border-radius:999px;cursor:pointer;opacity:.75}
.langsw button[aria-pressed="true"]{border-color:currentColor;opacity:1;font-weight:600}
.langsw button:focus-visible{outline:2px solid currentColor;outline-offset:1px}`;
  document.addEventListener("DOMContentLoaded", () => {
    const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
    if (D[LANG] && D[LANG][document.title] !== undefined) document.title = D[LANG][document.title];
    translateDom(document.body);
    document.querySelectorAll("[data-lang-switch]").forEach(el => { el.innerHTML = switcher(); });
  });
  document.addEventListener("click", e => { const b = e.target.closest("[data-setlang]"); if (b) setLang(b.dataset.setlang); });
  Object.assign(window, { LANG, LOCALE: LOC[LANG], t, tr, loc, setLang, langSwitcher: switcher, translateDom });
})();
"""
open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(S, "..", "..", "public", "i18n.js"), "w", encoding="utf-8").write(out)
print("keys en", len(en), "es", len(es), "zh", len(zh))
