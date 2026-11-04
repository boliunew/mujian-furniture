// Inventory in the admin: overview per warehouse, counts with scanning, receiving,
// transfers, stock ledger and warehouses. Uses helpers from admin.html ($, esc, toast, guard, openPanel…).

let INV = null, invSub = "overview", curCount = null, countReasons = [], camera = null, countDirty = false, countTimer = null;
const COUNT_ST = { open: ["盘点中", "paid"], review: ["待审核", "shipped"], posted: ["已过账", "delivered"], canceled: ["已作废", "canceled"] };
const ADJ_REASONS = ["盘点差异", "损坏", "丢失", "录错", "样品/展示", "退货回库", "其他"];

async function loadInventory(sub) {
  if (sub) invSub = sub;
  stopCamera();
  const view = $("inventoryView");
  const subs = [["overview", "📊 总览"], ["counts", "📋 盘点"], ["receive", "📥 入库"], ["transfer", "🔁 调拨"], ["ledger", "📒 流水"], ["warehouses", "🏬 仓库"]];
  view.innerHTML = `<div class="toolbar"><h1>库存</h1><div class="grow"></div></div>
    <nav class="subnav">${subs.map(([k, l]) => `<button class="sub-tab" data-inv="${k}" aria-selected="${k === invSub}">${l}</button>`).join("")}</nav>
    <div id="invBody"><div class="empty">⏳ 加载中…</div></div>`;
  INV = await guard(() => api.inventory());
  if (!INV) return;
  ({ overview: invOverview, counts: invCounts, receive: () => invMoveForm("receive"), transfer: () => invMoveForm("transfer"),
     ledger: invLedger, warehouses: invWarehouses })[invSub]();
}
const whName = id => (INV.warehouses.find(w => w.id === id) || {}).name || id || "—";
const prodBySku = sku => INV.products.find(p => (p.sku || "").toUpperCase() === sku.trim().toUpperCase());
const thumb = p => `<span class="ithumb">${p.photo ? `<img src="${esc(p.photo)}" alt="">` : (ART[p.art] || "")}</span>`;

// ---------------------------------------------------------------- overview
function invOverview() {
  const q = (invOverview.q || "").toLowerCase();
  const rows = INV.products.filter(p => !q || p.name.toLowerCase().includes(q) || (p.sku || "").toLowerCase().includes(q));
  $("invBody").innerHTML = `
    <div class="toolbar">
      <input class="in search" id="invSearch" placeholder="🔎 搜索商品名或 SKU，也可以直接扫码" value="${esc(invOverview.q || "")}" autocomplete="off">
      <div class="grow"></div>
      <button class="btn" id="printSku" ${invOverview.sel?.size ? "" : "disabled"}>🏷️ 打印条码${invOverview.sel?.size ? ` (${invOverview.sel.size})` : ""}</button>
    </div>
    <div class="tip">点任意一格可以改数量或库位。「在库」是货架上的实物数；「占用」是已付款还没发货的订单；可卖 = 在库 − 占用。只有勾了「给顾客发货」的仓库，库存才算进商店的可卖数。</div>
    <div class="tablewrap"><table class="itable">
      <thead><tr><th></th><th>商品</th>${INV.warehouses.map(w => `<th>${esc(w.name)}${w.ships ? "" : ' <span class="tag">不发货</span>'}</th>`).join("")}<th>合计在库</th><th>商店可卖</th></tr></thead>
      <tbody>${rows.map(p => `<tr>
        <td><input type="checkbox" data-skusel="${p.id}" ${invOverview.sel?.has(p.id) ? "checked" : ""} aria-label="选择 ${esc(p.name)}"></td>
        <td><div class="iname">${thumb(p)}<div><div>${esc(p.name)}${p.active ? "" : ' <span class="tag">已下架</span>'}</div><div class="mono sub">${esc(p.sku || "")}</div></div></div></td>
        ${INV.warehouses.map(w => { const c = p.cells[w.id]; return `<td><button class="icell ${c.qty < 0 ? "neg" : ""}" data-cell="${p.id}|${w.id}">
          <b class="mono">${c.qty}</b>${c.reserved ? `<span class="sub">占用 ${c.reserved} · 可卖 ${c.available}</span>` : ""}${c.bin ? `<span class="bin">📍 ${esc(c.bin)}</span>` : ""}</button></td>`; }).join("")}
        <td class="mono">${p.total}</td>
        <td><span class="mono ${p.sellable <= 0 ? "stock-out" : p.sellable <= 2 ? "stock-low" : ""}">${p.sellable}</span></td></tr>`).join("")}</tbody>
    </table></div>`;
  invOverview.sel = invOverview.sel || new Set();
  const s = $("invSearch");
  s.oninput = () => { invOverview.q = s.value; const pos = s.selectionStart; invOverview(); const n = $("invSearch"); n.focus(); n.setSelectionRange(pos, pos); };
  s.onkeydown = e => { if (e.key === "Enter") { const p = prodBySku(s.value); if (p) { invOverview.q = ""; adjustPanel(p.id, INV.warehouses[0].id); } } };
  $("printSku").onclick = () => skuPrint([...invOverview.sel]);
}

function adjustPanel(pid, wid) {
  const p = INV.products.find(x => x.id === pid), c = p.cells[wid];
  openPanel(`<div class="ph"><h2>✏️ 调整库存</h2><button class="x" data-close aria-label="关闭">✕</button></div>
    <form class="pb" id="adjForm">
      <div class="iname">${thumb(p)}<div><b>${esc(p.name)}</b><div class="mono sub">${esc(p.sku || "")}</div></div></div>
      <label class="f">仓库<select class="in" id="adj-wh">${INV.warehouses.map(w => `<option value="${w.id}" ${w.id === wid ? "selected" : ""}>${esc(w.name)}</option>`).join("")}</select></label>
      <div class="box"><div class="kv"><span>现在在库</span><span id="adj-now">${c.qty}</span></div><div class="kv"><span>待发货占用</span><span id="adj-res">${c.reserved}</span></div></div>
      <div class="two">
        <label class="f">改成多少件<input class="in" id="adj-qty" type="number" min="0" step="1" placeholder="${c.qty}"></label>
        <label class="f">库位<input class="in" id="adj-bin" value="${esc(c.bin || "")}" placeholder="例如 A-03"></label>
      </div>
      <label class="f">原因（改数量时必填）<select class="in" id="adj-reason"><option value="">—</option>${ADJ_REASONS.map(r => `<option>${r}</option>`).join("")}</select></label>
      <label class="f">备注<input class="in" id="adj-note" placeholder="可选"></label>
      <div class="tip">💡 大量核对数量用「📋 盘点」更好，会留下完整的盘点记录。这里适合单件的临时修正。</div>
      <div class="actions"><button class="btn primary" type="submit">💾 保存</button><button class="btn" type="button" data-skuprint>🏷️ 打印这件的条码</button></div>
      <div class="err" id="adjErr"></div>
    </form>`);
  const refresh = () => { const cc = p.cells[$("adj-wh").value]; $("adj-now").textContent = cc.qty; $("adj-res").textContent = cc.reserved; $("adj-bin").value = cc.bin || ""; $("adj-qty").placeholder = cc.qty; };
  $("adj-wh").onchange = refresh;
  $("adjForm").onclick = e => { if (e.target.closest("[data-skuprint]")) skuPrint([pid]); };
  $("adjForm").onsubmit = async e => {
    e.preventDefault();
    const body = { product_id: pid, warehouse_id: $("adj-wh").value, bin: $("adj-bin").value, qty: $("adj-qty").value, reason: $("adj-reason").value, note: $("adj-note").value };
    if (body.qty !== "" && !body.reason) { $("adjErr").textContent = "⚠️ 改数量要选一个原因"; return; }
    const r = await guard(() => api.adjustStock(body), $("adjErr"));
    if (r) { INV = r; toast("💾 库存已更新"); closePanel(); invOverview(); }
  };
}

function skuPrint(ids) { if (ids.length) goPrint("sku", ids, "&copies=1"); }

// ---------------------------------------------------------------- counts
async function invCounts() {
  const d = await guard(() => api.counts()); if (!d) return;
  countReasons = d.reasons;
  $("invBody").innerHTML = `
    <div class="toolbar"><div class="grow"></div><button class="btn primary" id="newCount">➕ 新建盘点</button></div>
    ${d.counts.length ? `<div class="list">${d.counts.map(c => `<button class="crow" data-count="${c.id}">
        <span class="mono">${esc(c.no)}</span><span>${esc(c.warehouse_name)} · ${scopeLabel(c.scope)}${c.blind ? ' <span class="tag">盲盘</span>' : ""}</span>
        <span class="sub">${c.done}/${c.total} 已盘${c.variances ? ` · <b class="stock-low">${c.variances} 项差异</b>` : ""}</span>
        <span class="pill st-${COUNT_ST[c.status][1]}">${COUNT_ST[c.status][0]}</span></button>`).join("")}</div>`
      : `<div class="empty">📋 还没有盘点单。点「➕ 新建盘点」开始，可以盘整个仓库、一个分类，或者挑几件商品抽盘。</div>`}`;
  $("newCount").onclick = newCountPanel;
  $("invBody").querySelectorAll("[data-count]").forEach(b => b.onclick = () => openCount(b.dataset.count));
}
function scopeLabel(s) { return s === "all" ? "全部商品" : s.startsWith("cat:") ? "分类 " + (CAT_ZH[s.slice(4)] || s.slice(4)) : "指定商品"; }

function newCountPanel() {
  openPanel(`<div class="ph"><h2>➕ 新建盘点</h2><button class="x" data-close aria-label="关闭">✕</button></div>
    <form class="pb" id="ncForm">
      <label class="f">盘哪个仓库<select class="in" id="nc-wh">${INV.warehouses.map(w => `<option value="${w.id}">${esc(w.name)}</option>`).join("")}</select></label>
      <label class="f">范围<select class="in" id="nc-scope"><option value="all">全部商品</option>${CATS.map(c => `<option value="cat:${c}">分类：${CAT_ZH[c]}</option>`).join("")}<option value="ids">挑选商品（抽盘）</option></select></label>
      <div id="nc-pick" hidden class="picklist">${INV.products.map(p => `<label class="check"><input type="checkbox" value="${p.id}"> ${esc(p.name)} <span class="mono sub">${esc(p.sku || "")}</span></label>`).join("")}</div>
      <label class="check"><input type="checkbox" id="nc-blind" checked> 盲盘：盘点时不显示账面数量，避免照着数填</label>
      <label class="f">备注<input class="in" id="nc-note" placeholder="例如 月底全盘 / B 区抽盘"></label>
      <div class="tip">开单时会记下每件商品的账面数量。盘点过程中如果有订单发货，过账时只按「实盘 − 开单时账面」调整，发货扣掉的不会被覆盖。最好开单后尽快开始数。</div>
      <div class="actions"><button class="btn primary" type="submit">📋 开始盘点</button></div>
      <div class="err" id="ncErr"></div>
    </form>`);
  $("nc-scope").onchange = () => $("nc-pick").hidden = $("nc-scope").value !== "ids";
  $("ncForm").onsubmit = async e => {
    e.preventDefault();
    const body = { warehouse_id: $("nc-wh").value, scope: $("nc-scope").value, blind: $("nc-blind").checked, note: $("nc-note").value,
      product_ids: [...document.querySelectorAll("#nc-pick input:checked")].map(x => x.value) };
    const r = await guard(() => api.newCount(body), $("ncErr"));
    if (r) { closePanel(); toast(`📋 ${r.no} 已开单，共 ${r.total} 件`); openCount(r.id, r); }
  };
}

async function openCount(id, data) {
  curCount = data || await guard(() => api.getCount(id));
  if (!curCount) return;
  countDirty = false;
  renderCount();
}

function renderCount() {
  const c = curCount, editable = c.status === "open" || c.status === "review";
  const showExpected = !c.blind || c.status !== "open";
  const diffOf = l => l.counted == null ? null : l.counted - l.expected;
  $("invBody").innerHTML = `
    <div class="toolbar">
      <button class="btn" data-cback>← 盘点列表</button>
      <h2 class="mono" style="margin:0">${esc(c.no)}</h2><span class="pill st-${COUNT_ST[c.status][1]}">${COUNT_ST[c.status][0]}</span>
      <span class="sub">${esc(c.warehouse_name)} · ${scopeLabel(c.scope)}${c.blind ? " · 盲盘" : ""}${c.note ? " · " + esc(c.note) : ""}</span>
      <div class="grow"></div>
      <button class="btn" data-cprint>🖨️ 打印盘点表</button>
    </div>
    <div class="cstats"><span>已盘 <b class="mono">${c.lines.filter(l => l.counted != null).length}/${c.total}</b></span>
      ${showExpected ? `<span>差异 <b class="mono">${c.lines.filter(l => diffOf(l)).length}</b> 项</span><span>净差 <b class="mono">${fmtDiff(c.lines.reduce((n, l) => n + (diffOf(l) || 0), 0))}</b> 件</span>` : ""}
      <span class="sub" id="saveState">${countDirty ? "✏️ 有改动未保存" : "✅ 已保存"}</span></div>
    ${editable ? `<div class="scanbar">
      <input class="in" id="scanIn" placeholder="🔫 扫码枪扫一下 / 输入 SKU 回车，数量 +1" autocomplete="off" autocapitalize="characters">
      <button class="btn" data-cam>📷 摄像头扫码</button>
    </div>
    <div id="camBox" hidden><div id="camView"></div><div class="actions"><button class="btn" data-camstop>⏹️ 关闭摄像头</button><span class="sub">对准条码，扫到会自动 +1</span></div></div>` : ""}
    <div class="tablewrap"><table class="itable ctable">
      <thead><tr><th>库位</th><th>商品</th>${showExpected ? "<th>账面</th>" : ""}<th>实盘</th>${showExpected ? "<th>差异</th><th>原因</th>" : ""}</tr></thead>
      <tbody>${c.lines.map(l => { const d = diffOf(l); return `<tr id="cl-${l.product_id}" class="${l.counted == null ? "todo" : ""}">
        <td>${l.bin ? `<span class="bin">📍 ${esc(l.bin)}</span>` : "—"}</td>
        <td><div>${esc(l.name)}</div><div class="mono sub">${esc(l.sku || "")}</div></td>
        ${showExpected ? `<td class="mono">${l.expected}</td>` : ""}
        <td>${editable ? `<div class="qtyedit"><button type="button" data-dec="${l.product_id}" aria-label="减一">−</button>
            <input class="in mono" inputmode="numeric" data-cnt="${l.product_id}" value="${l.counted ?? ""}" placeholder="?">
            <button type="button" data-inc="${l.product_id}" aria-label="加一">+</button></div>` : `<span class="mono">${l.counted ?? "—"}</span>`}</td>
        ${showExpected ? `<td class="mono ${d > 0 ? "plus" : d < 0 ? "minus" : ""}">${d == null ? "" : fmtDiff(d)}</td>
        <td>${d ? (editable ? `<select class="in" data-reason="${l.product_id}"><option value="">—</option>${countReasons.map(r => `<option ${r === l.reason ? "selected" : ""}>${r}</option>`).join("")}</select>` : esc(l.reason || "")) : ""}</td>` : ""}
      </tr>`; }).join("")}</tbody>
    </table></div>
    <div class="actions" style="margin-top:12px">
      ${editable ? `<button class="btn" data-cact="save">💾 保存进度</button>` : ""}
      ${c.status === "open" ? `<button class="btn primary" data-cact="review">🔎 盘完了，提交审核</button>` : ""}
      ${c.status === "review" ? `<button class="btn" data-cact="reopen">↩️ 退回继续盘</button><button class="btn primary" data-cact="post">✅ 确认差异，调整库存</button>` : ""}
      ${editable ? `<button class="btn danger" data-cact="cancel">⛔ 作废这张盘点单</button>` : ""}
    </div>
    <div class="err" id="cErr"></div>
    ${c.status === "review" ? `<div class="tip">确认后，每件有差异的商品会按「实盘 − 账面」调整库存，并在库存流水里记一笔「盘点调整」。没盘的项目要先填（没有就填 0）。</div>` : ""}`;
  wireCount();
  const s = $("scanIn"); if (s && !camera) s.focus({ preventScroll: true });
}
const fmtDiff = d => d > 0 ? "+" + d : String(d);

function setCounted(pid, v, flash) {
  const l = curCount.lines.find(x => x.product_id === pid); if (!l) return;
  l.counted = v; countDirty = true;
  clearTimeout(countTimer); countTimer = setTimeout(() => saveCount("save", true), 1500);
  renderCount();
  if (flash) { const row = $("cl-" + pid); if (row) { row.classList.add("hit"); row.scrollIntoView({ block: "center", behavior: "smooth" }); setTimeout(() => row.classList.remove("hit"), 900); } }
}

function scanCode(code) {
  code = (code || "").trim(); if (!code) return;
  const l = curCount.lines.find(x => (x.sku || "").toUpperCase() === code.toUpperCase());
  if (!l) { toast(`⚠️ ${code} 不在这张盘点单里`); beep(220); return; }
  setCounted(l.product_id, (l.counted || 0) + 1, true);
  beep(880); toast(`✅ ${l.name}：${l.counted}`);
}
function beep(f) {
  try { const a = beep.ctx || (beep.ctx = new (window.AudioContext || window.webkitAudioContext)()); const o = a.createOscillator(), g = a.createGain();
    o.frequency.value = f; g.gain.value = 0.08; o.connect(g); g.connect(a.destination); o.start(); o.stop(a.currentTime + 0.09); } catch (e) {}
  try { navigator.vibrate && navigator.vibrate(40); } catch (e) {}
}

function wireCount() {
  const body = $("invBody");
  body.onclick = async e => {
    const t = e.target.closest("button"); if (!t) return;
    if ("cback" in t.dataset) { if (countDirty) await saveCount("save", true); stopCamera(); curCount = null; return invCounts(); }
    if ("cprint" in t.dataset) { if (countDirty) await saveCount("save", true); return goPrint("count", [curCount.id]); }
    if (t.dataset.inc) { const l = curCount.lines.find(x => x.product_id === t.dataset.inc); return setCounted(l.product_id, (l.counted || 0) + 1); }
    if (t.dataset.dec) { const l = curCount.lines.find(x => x.product_id === t.dataset.dec); return setCounted(l.product_id, Math.max(0, (l.counted || 0) - 1)); }
    if ("cam" in t.dataset) return startCamera();
    if ("camstop" in t.dataset) return stopCamera();
    if (t.dataset.cact) {
      const a = t.dataset.cact;
      if ((a === "cancel" || a === "post") && t.dataset.sure !== "1") { t.dataset.sure = "1"; t.textContent = a === "cancel" ? "⛔ 再点一次确认作废" : "✅ 再点一次确认过账"; return; }
      await saveCount(a);
    }
  };
  body.oninput = e => {
    const i = e.target.closest("[data-cnt]");
    if (i) { const v = i.value.trim(); const l = curCount.lines.find(x => x.product_id === i.dataset.cnt);
      l.counted = v === "" ? null : Math.max(0, parseInt(v, 10) || 0); countDirty = true; $("saveState").textContent = "✏️ 有改动未保存";
      clearTimeout(countTimer); countTimer = setTimeout(() => saveCount("save", true), 1500); }
  };
  body.onchange = e => {
    const r = e.target.closest("[data-reason]");
    if (r) { curCount.lines.find(x => x.product_id === r.dataset.reason).reason = r.value || null; countDirty = true; saveCount("save", true); }
    if (e.target.closest("[data-cnt]")) renderCount();
  };
  const s = $("scanIn");
  if (s) s.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); scanCode(s.value); s.value = ""; } };
}

async function saveCount(action, quiet) {
  clearTimeout(countTimer);
  if (!curCount) return;
  const id = curCount.id;
  const body = { action, lines: curCount.lines.map(l => ({ product_id: l.product_id, counted: l.counted, reason: l.reason })) };
  const r = await guard(() => api.updateCount(id, body), quiet ? null : $("cErr"));
  if (!r) return;
  if (!curCount || curCount.id !== id) return;
  countDirty = false;
  if (quiet) { const st = $("saveState"); if (st) st.textContent = "✅ 已保存"; curCount.status = r.status; return; }
  curCount = r;
  const msg = { save: "💾 已保存", review: "🔎 已提交审核，检查差异后过账", reopen: "↩️ 已退回，可以继续盘", post: "✅ 已过账，库存已按实盘调整", cancel: "⛔ 盘点单已作废" }[action];
  toast(msg);
  if (action === "post" || action === "cancel") { stopCamera(); INV = await api.inventory(); }
  renderCount();
}

async function startCamera() {
  if (!window.isSecureContext) return toast("📷 摄像头只能在 https 网址上用，可以先用扫码枪或手动输入");
  $("camBox").hidden = false;
  try {
    if (!window.Html5Qrcode) await new Promise((ok, bad) => { const s = document.createElement("script"); s.src = "vendor/html5-qrcode.min.js"; s.onload = ok; s.onerror = () => bad(new Error("扫码组件加载失败")); document.head.appendChild(s); });
    camera = new Html5Qrcode("camView");
    let last = "", lastAt = 0;
    await camera.start({ facingMode: "environment" }, { fps: 10, qrbox: { width: 260, height: 120 } }, text => {
      const t = Date.now(); if (text === last && t - lastAt < 1500) return; last = text; lastAt = t; scanCode(text);
    }, () => {});
  } catch (e) {
    $("camBox").hidden = true; camera = null;
    toast("📷 打不开摄像头：" + (e && e.message ? e.message : "请允许浏览器使用摄像头") + "。也可以用蓝牙扫码枪或手动输入。");
  }
}
function stopCamera() {
  if (camera) { const c = camera; camera = null; c.stop().catch(() => {}).finally(() => { try { c.clear(); } catch (e) {} }); }
  const b = $("camBox"); if (b) b.hidden = true;
}

// ---------------------------------------------------------------- receive & transfer
function invMoveForm(kind) {
  const recv = kind === "receive";
  invMoveForm.lines = invMoveForm.kind === kind ? invMoveForm.lines : [];
  invMoveForm.kind = kind;
  const lines = invMoveForm.lines;
  const opts = INV.products.map(p => `<option value="${p.id}">${esc(p.name)} · ${esc(p.sku || "")}</option>`).join("");
  $("invBody").innerHTML = `
    <form class="settings" id="mvForm">
      <section class="sbox"><h3>${recv ? "📥 到货入库" : "🔁 仓库之间调拨"}</h3>
        ${recv ? `<label class="f">入哪个仓库<select class="in" id="mv-to">${INV.warehouses.map(w => `<option value="${w.id}">${esc(w.name)}</option>`).join("")}</select></label>
          <label class="f">单号 / 供应商<input class="in" id="mv-ref" placeholder="例如 PO-1024 / 工厂发货单号"></label>`
        : `<div class="two"><label class="f">从<select class="in" id="mv-from">${INV.warehouses.map(w => `<option value="${w.id}">${esc(w.name)}</option>`).join("")}</select></label>
          <label class="f">到<select class="in" id="mv-to">${INV.warehouses.map((w, i) => `<option value="${w.id}" ${i === 1 ? "selected" : ""}>${esc(w.name)}</option>`).join("")}</select></label></div>
          <label class="f">调拨单号（可空，自动生成）<input class="in" id="mv-ref"></label>`}
        <div class="scanbar"><input class="in" id="mvScan" placeholder="🔫 扫码 / 输入 SKU 回车，加一件" autocomplete="off"></div>
        <div id="mvLines">${lines.map((l, i) => `<div class="mvline">
          <select class="in" data-mvp="${i}">${opts.replace(`value="${l.product_id}"`, `value="${l.product_id}" selected`)}</select>
          <input class="in mono" type="number" min="1" data-mvq="${i}" value="${l.qty}">
          <button class="btn danger" type="button" data-mvdel="${i}" aria-label="删除这行">🗑️</button></div>`).join("")}</div>
        <button class="btn" type="button" id="mvAdd">➕ 加一行</button>
        <label class="f">备注<input class="in" id="mv-note" placeholder="可选"></label>
        <div class="actions"><button class="btn primary" type="submit">${recv ? "📥 确认入库" : "🔁 确认调拨"}</button></div>
        <div class="err" id="mvErr"></div>
      </section>
    </form>`;
  const addLine = (pid, q = 1) => { const ex = lines.find(l => l.product_id === pid); ex ? ex.qty += q : lines.push({ product_id: pid, qty: q }); invMoveForm(kind); };
  $("mvAdd").onclick = () => addLine(INV.products[0].id);
  $("mvScan").onkeydown = e => { if (e.key !== "Enter") return; e.preventDefault(); const p = prodBySku(e.target.value); if (p) { addLine(p.id); beep(880); $("mvScan").focus(); } else { toast(`⚠️ 没找到 SKU ${e.target.value}`); beep(220); } e.target.value = ""; };
  $("mvLines").oninput = e => {
    const p = e.target.dataset.mvp, q = e.target.dataset.mvq;
    if (p != null) lines[p].product_id = e.target.value;
    if (q != null) lines[q].qty = parseInt(e.target.value, 10) || 0;
  };
  $("mvLines").onclick = e => { const d = e.target.closest("[data-mvdel]"); if (d) { lines.splice(+d.dataset.mvdel, 1); invMoveForm(kind); } };
  $("mvForm").onsubmit = async e => {
    e.preventDefault();
    const body = { ref: $("mv-ref").value, note: $("mv-note").value, lines: lines.filter(l => l.qty > 0) };
    let r;
    if (recv) r = await guard(() => api.receive({ ...body, warehouse_id: $("mv-to").value }), $("mvErr"));
    else r = await guard(() => api.transfer({ ...body, from: $("mv-from").value, to: $("mv-to").value }), $("mvErr"));
    if (r) { INV = r; invMoveForm.lines = []; toast(recv ? "📥 已入库" : "🔁 已调拨"); invSub = "ledger"; loadInventory(); }
  };
}

// ---------------------------------------------------------------- ledger
async function invLedger() {
  const f = invLedger.f || (invLedger.f = {});
  const rows = await guard(() => api.movements({ ...f, limit: 300 })); if (!rows) return;
  const kinds = { receive: "入库", ship: "发货出库", ship_reverse: "撤销出库", adjust: "手动调整", count: "盘点调整", transfer_out: "调拨出", transfer_in: "调拨入", init: "期初" };
  $("invBody").innerHTML = `
    <div class="toolbar filters2">
      <select class="in" data-lf="product"><option value="">全部商品</option>${INV.products.map(p => `<option value="${p.id}" ${f.product === p.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
      <select class="in" data-lf="warehouse"><option value="">全部仓库</option>${INV.warehouses.map(w => `<option value="${w.id}" ${f.warehouse === w.id ? "selected" : ""}>${esc(w.name)}</option>`).join("")}</select>
      <select class="in" data-lf="kind"><option value="">全部类型</option>${Object.entries(kinds).map(([k, l]) => `<option value="${k}" ${f.kind === k ? "selected" : ""}>${l}</option>`).join("")}</select>
    </div>
    ${rows.length ? `<div class="tablewrap"><table class="itable">
      <thead><tr><th>时间</th><th>商品</th><th>仓库</th><th>变动</th><th>变动后</th><th>类型</th><th>单号</th><th>备注</th></tr></thead>
      <tbody>${rows.map(m => `<tr><td class="mono sub">${fmtTime(m.at)}</td><td>${esc(m.name || m.product_id)}</td><td>${esc(m.warehouse_name || "")}</td>
        <td class="mono ${m.delta > 0 ? "plus" : "minus"}">${fmtDiff(m.delta)}</td><td class="mono">${m.qty_after}</td>
        <td><span class="tag">${esc(m.kind_label)}</span></td><td class="mono">${esc(m.ref || "")}</td><td class="sub">${esc(m.note || "")}</td></tr>`).join("")}</tbody>
    </table></div>` : `<div class="empty">没有符合条件的记录</div>`}`;
  $("invBody").querySelectorAll("[data-lf]").forEach(s => s.onchange = () => { f[s.dataset.lf] = s.value; invLedger(); });
}

// ---------------------------------------------------------------- warehouses
function invWarehouses() {
  $("invBody").innerHTML = `
    <div class="settings">
      ${INV.warehouses.map(w => whForm(w)).join("")}
      ${whForm({ id: "", name: "", address: "", ships: true })}
      <div class="tip">💡 勾了「给顾客发货」的仓库，库存才算进商店的可卖数量，顾客下单时系统按顺序从第一个能发齐的仓库发货（订单里也可以手动换仓库）。展厅这类不发货的地方，取消勾选就行。</div>
    </div>`;
  $("invBody").querySelectorAll("form").forEach(fm => fm.onsubmit = async e => {
    e.preventDefault();
    const g = n => fm.querySelector(`[name=${n}]`);
    const r = await guard(() => api.saveWarehouse({ id: g("id").value || undefined, name: g("name").value, address: g("address").value, ships: g("ships").checked, sort: g("sort").value }), fm.querySelector(".err"));
    if (r) { INV = r; toast("💾 仓库已保存"); invWarehouses(); }
  });
  $("invBody").querySelectorAll("[data-delwh]").forEach(b => b.onclick = async () => {
    if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = "🗑️ 再点一次确认"; return; }
    const r = await guard(() => api.deleteWarehouse(b.dataset.delwh), b.closest("form").querySelector(".err"));
    if (r) { INV = r; toast("🗑️ 已删除"); invWarehouses(); }
  });
}
function whForm(w) {
  const isNew = !w.id;
  return `<form class="sbox">
    <h3>${isNew ? "➕ 新仓库" : "🏬 " + esc(w.name)}</h3>
    <input type="hidden" name="id" value="${esc(w.id)}">
    <div class="two"><label class="f">名称<input class="in" name="name" value="${esc(w.name)}" required></label>
      <label class="f">排序（小的优先发货）<input class="in" name="sort" type="number" value="${w.sort ?? ""}"></label></div>
    <label class="f">地址<input class="in" name="address" value="${esc(w.address || "")}"></label>
    <label class="check"><input type="checkbox" name="ships" ${w.ships ? "checked" : ""}> 给顾客发货（库存算进商店可卖数）</label>
    <div class="actions"><button class="btn ${isNew ? "primary" : ""}" type="submit">💾 ${isNew ? "添加仓库" : "保存"}</button>${isNew ? "" : `<button class="btn danger" type="button" data-delwh="${esc(w.id)}">🗑️ 删除</button>`}</div>
    <div class="err"></div>
  </form>`;
}

document.addEventListener("click", e => {
  const t = e.target.closest("button"); if (!t || !t.closest("#inventoryView")) return;
  if (t.dataset.inv) { if (countDirty) saveCount("save", true); curCount = null; return loadInventory(t.dataset.inv); }
  if (t.dataset.cell) { const [p, w] = t.dataset.cell.split("|"); return adjustPanel(p, w); }
});
document.addEventListener("change", e => {
  const c = e.target.closest("[data-skusel]"); if (!c) return;
  c.checked ? invOverview.sel.add(c.dataset.skusel) : invOverview.sel.delete(c.dataset.skusel);
  const b = $("printSku"); b.disabled = !invOverview.sel.size; b.textContent = `🏷️ 打印条码${invOverview.sel.size ? ` (${invOverview.sel.size})` : ""}`;
});
window.addEventListener("beforeunload", () => { if (countDirty) saveCount("save", true); stopCamera(); });
