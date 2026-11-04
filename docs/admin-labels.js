// Pallet / box labels in the admin: a preview of each label stuck on its pallet,
// plus automatic printing of new orders' labels. Uses helpers from admin.html.

let STORE_INFO = null, autoPrintTimer = null;
const opened = new Set();
const AP_KEY = "mj-autoprint";
const lsGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };

async function storeInfo() {
  if (!STORE_INFO) STORE_INFO = await api.store().catch(() => ({ name: "Mujian Furniture", address: [""], email: "" }));
  return STORE_INFO;
}

// ---------------------------------------------------------------- preview on pallets
function unitsSection(o) {
  const units = o.units || [];
  if (!units.length || o.status === "pending" || o.status === "canceled") return "";
  const printed = o.labels_printed_at;
  const pallets = units.filter(u => u.kind === "pallet").length, boxes = units.length - pallets;
  return `<div class="sec"><h3>🏷️ 托盘 / 箱子标签</h3>
    <div class="frow"><span class="pill ${printed ? "st-delivered" : "st-paid"}">${printed ? "✅ 已打印 " + fmtTime(printed) : "🟠 还没打印"}</span>
      <span class="sub">${[pallets && `🪵 ${pallets} 个托盘`, boxes && `📦 ${boxes} 个箱子`].filter(Boolean).join(" · ")}，每个一张 4×6 标签</span></div>
    <div class="scenes">${units.map(u => `<figure class="scene ${u.kind}">
      <div class="load">${u.kind === "pallet" ? `<div class="wrap-film"></div>` : ""}
        <div class="stick"><div class="mini" data-unit="${esc(u.id)}"></div></div></div>
      ${u.kind === "pallet" ? `<div class="pallet"><i></i><i></i><i></i></div>` : `<div class="floor"></div>`}
      <figcaption><b>${u.kind === "pallet" ? "🪵 托盘" : "📦 箱子"} ${u.n}/${u.of}</b> · ${u.items.map(i => `${esc(i.name)} ×${i.qty}`).join("、")}${u.weight_lb ? ` · ${u.weight_lb} lb` : ""}</figcaption>
    </figure>`).join("")}</div>
    <div class="actions"><button class="btn primary" type="button" data-print="label">🖨️ 打印这单全部 ${units.length} 张标签</button></div>
    <div class="tip">标签贴在托盘缠绕膜外面、朝外的那一侧（叉车进叉方向的侧面）；两侧各贴一张更好找。箱子贴在最大的一面。</div>
  </div>`;
}

async function fillUnitPreviews(o) {
  const store = await storeInfo();
  document.querySelectorAll("#panel .mini[data-unit]").forEach(el => {
    const u = (o.units || []).find(x => x.id === el.dataset.unit); if (!u) return;
    el.innerHTML = unitLabelHTML(o, u, store);
    drawBarcodes(el);
  });
}

// ---------------------------------------------------------------- auto print
function autoPrintOn() { return lsGet(AP_KEY) === "1"; }
function renderPrintTools() {
  const box = $("printTools"); if (!box) return;
  const n = (counts && counts.to_print) || 0;
  box.innerHTML = `<button class="btn ${autoPrintOn() ? "primary" : ""}" id="apToggle" title="后台开着时，新付款的订单自动打印标签">🖨️ 自动打印：${autoPrintOn() ? "开" : "关"}</button>
    <button class="btn ${n ? "brass" : ""}" id="apQueue" ${n ? "" : "disabled"}>🏷️ 待打印标签 ${n}</button>`;
  $("apToggle").onclick = () => {
    if (api.mode !== "live" && !autoPrintOn()) toast("👀 预览版不能自动弹出打印，部署后的正式后台才会自动打印");
    lsSet(AP_KEY, autoPrintOn() ? "0" : "1"); renderPrintTools(); scheduleAutoPrint();
    if (autoPrintOn()) toast("🖨️ 自动打印已开：新订单付款后会自动打开打印。用 Chrome 的 --kiosk-printing 启动可以连对话框都不弹。");
  };
  $("apQueue").onclick = async () => {
    const q = await guard(() => api.printQueue()); if (!q || !q.orders.length) return toast("没有待打印的标签");
    goPrint("label", q.orders.map(o => o.id));
  };
}
async function pollPrintQueue() {
  if (!autoPrintOn() || api.mode !== "live" || $("appView").hidden) return;
  const q = await api.printQueue().catch(() => null); if (!q) return;
  const fresh = q.orders.filter(o => !opened.has(o.id));
  if (!fresh.length) return;
  fresh.forEach(o => opened.add(o.id));
  const w = window.open(printUrl("label", fresh.map(o => o.id)) + "&ack=1&close=1&by=" + encodeURIComponent("自动打印"), "_blank");
  if (!w) toast("⚠️ 浏览器拦截了打印窗口，请允许这个网站弹出窗口");
  toast(`🖨️ 自动打印 ${fresh.length} 个新订单的标签`);
  if (typeof loadOrders === "function" && !$("ordersView").hidden) setTimeout(loadOrders, 3000);
}
function scheduleAutoPrint() {
  clearInterval(autoPrintTimer);
  if (autoPrintOn() && api.mode === "live") { pollPrintQueue(); autoPrintTimer = setInterval(pollPrintQueue, 15000); }
}
