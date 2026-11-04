// Freight delivery in the admin: order panel section, delivery calendar, settings.
// Loaded after admin.html's main script; uses its helpers ($, esc, toast, guard, goPrint, openOrder…).

let SETTINGS = null, calFrom = null;
const FS = [
  ["unscheduled", "待预约", "📅"], ["scheduled", "已预约", "🗓️"], ["picked_up", "已装车/已提货", "📦"],
  ["in_transit", "运输中", "🛣️"], ["out_for_delivery", "派送中", "🚚"], ["delivered", "已签收", "✅"],
];
const FS_LABEL = Object.fromEntries(FS.map(([k, l, i]) => [k, `${i} ${l}`]));
FS_LABEL.exception = "⚠️ 异常";
const LTL_CARRIERS = ["XPO", "Estes", "R+L Carriers", "Old Dominion", "ABF", "Saia", "TForce Freight", "Southeastern Freight"];
const WEEK_ZH = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const HOME_ZH = { house: "🏡 独立屋", apartment: "🏢 公寓", business: "🏬 商业地址" };

async function getSettings(force) {
  if (!SETTINGS || force) SETTINGS = await api.settings();
  return SETTINGS;
}
const localISO = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const dayLabel = ds => { const d = new Date(ds + "T12:00:00"); return `${WEEK_ZH[d.getDay() || 7]} ${d.getMonth() + 1}/${d.getDate()}`; };
function fullUrl(path) { return new URL(path, location.href).href; }
async function copyText(text, okMsg) {
  try { await navigator.clipboard.writeText(text); toast(okMsg); }
  catch (e) { toast("复制失败，请手动复制：" + text.slice(0, 80)); }
}

// ---------------------------------------------------------------- list helpers
function freightTag(o) {
  if (o.fulfillment !== "freight") return "";
  const type = o.delivery_type === "local" ? "🚚 自送" : "🚛 LTL";
  const appt = o.appt_date ? ` · ${dayLabel(o.appt_date)} ${esc((o.appt_window_label || "").replace(/\s/g, ""))}` : "";
  return ` <span class="tag ${o.freight_status === "unscheduled" ? "tag-warn" : ""}">${type}${appt}${o.freight_status === "unscheduled" ? " · 待预约" : ""}</span>`;
}

// ---------------------------------------------------------------- order panel section
function freightSection(o) {
  const s = SETTINGS || { windows: [], trucks: [] };
  const local = o.delivery_type === "local";
  const home = [o.home_type && HOME_ZH[o.home_type], o.floor && `${o.floor} 楼`, o.elevator == null ? "" : o.elevator ? "有电梯" : "无电梯",
    o.liftgate ? "要尾板" : o.liftgate === false ? "不用尾板" : ""].filter(Boolean).join(" · ");
  const idx = FS.findIndex(x => x[0] === o.freight_status);
  const next = idx >= 0 && idx < FS.length - 1 ? FS[idx + 1] : null;
  const prev = idx > 1 ? FS[idx - 1] : null;
  const steps = FS.map(([k, l, i], n) => `<span class="step ${n < idx ? "done" : n === idx ? "now" : ""}">${i} ${l}</span>`).join("");
  return `
    <div class="sec"><h3>🚛 卡派</h3>
      <div class="box fbox">
        <div class="frow"><span class="pill st-${o.freight_status === "delivered" ? "delivered" : o.freight_status === "unscheduled" ? "paid" : "shipped"}">${FS_LABEL[o.freight_status] || ""}</span>
          <span class="tag">${local ? "🚚 本地车队" : "🚛 LTL 承运商"}</span><span class="tag">🧤 ${esc(o.service_label)}</span></div>
        <div class="steps">${steps}</div>
        <div><b>${o.appt_date ? `📅 ${dayLabel(o.appt_date)} · ${esc(o.appt_window_label)}` : "📅 顾客还没预约送货时间"}</b></div>
        ${home ? `<div class="sub">${home}</div>` : ""}
        ${o.access_notes ? `<div class="sub">📝 ${esc(o.access_notes)}</div>` : ""}
        <div class="actions">
          <button class="btn" type="button" data-fx="copylink">📋 复制预约链接</button>
          <a class="btn" href="${esc(o.schedule_url)}" ${api.mode === "live" ? 'target="_blank" rel="noopener"' : ""}>🔗 打开预约页</a>
          <button class="btn" type="button" data-fx="resend">📧 重发预约邮件</button>
        </div>
      </div>
      <div class="fgrid">
        <label class="f">配送方式<select class="in" id="fx-type"><option value="local" ${local ? "selected" : ""}>🚚 本地车队</option><option value="ltl" ${!local ? "selected" : ""}>🚛 LTL 承运商</option></select></label>
        <label class="f">送货日期<input class="in" type="date" id="fx-date" value="${esc(o.appt_date || "")}"></label>
        <label class="f">时段<select class="in" id="fx-win"><option value="">—</option>${s.windows.map(w => `<option value="${esc(w.id)}" ${w.id === o.appt_window ? "selected" : ""}>${esc(w.label)}</option>`).join("")}</select></label>
        ${local ? `
        <label class="f">车辆<select class="in" id="fx-truck"><option value="">未分配</option>${s.trucks.map(t => `<option ${t === o.truck ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
        <label class="f">站序<input class="in" type="number" min="1" id="fx-stop" value="${o.stop_no || ""}" placeholder="1"></label>` : `
        <label class="f">承运商<input class="in" id="fx-carrier" list="ltl-carriers" value="${esc(o.freight_carrier || "")}" placeholder="Estes"></label>
        <label class="f">PRO 单号<input class="in" id="fx-pro" value="${esc(o.pro || "")}" autocomplete="off"></label>
        <label class="f">提货日期<input class="in" type="date" id="fx-pickup" value="${esc(o.pickup_date || "")}"></label>`}
        <label class="f">托盘数<input class="in" type="number" min="0" id="fx-pallets" value="${o.pallets ?? ""}"></label>
        <label class="f">总重 lb<input class="in" type="number" min="0" id="fx-weight" value="${o.weight_lb ?? ""}"></label>
        <label class="f">Class<input class="in" id="fx-class" value="${esc(o.freight_class || "")}" placeholder="125"></label>
      </div>
      <datalist id="ltl-carriers">${LTL_CARRIERS.map(c => `<option value="${c}">`).join("")}</datalist>
      <label class="check"><input type="checkbox" id="fx-liftgate" ${o.liftgate ? "checked" : ""}> 需要尾板（liftgate）</label>
      <div class="actions"><button class="btn" type="button" data-fx="save">💾 保存卡派信息</button></div>
      <div class="actions">
        ${prev && o.status !== "canceled" ? `<button class="btn" type="button" data-fs="${prev[0]}">↩️ 退回：${prev[1]}</button>` : ""}
        ${next && o.status !== "canceled" ? `<button class="btn primary" type="button" data-fs="${next[0]}">${next[2]} 下一步：${next[1]}</button>` : ""}
        ${o.freight_status !== "exception" && o.status !== "canceled" && idx > 0 ? `<button class="btn danger" type="button" data-fs="exception">⚠️ 标记异常</button>` : ""}
        ${o.freight_status === "exception" ? `<button class="btn" type="button" data-fs="scheduled">↩️ 恢复为已预约</button>` : ""}
        ${o.status === "paid" ? `<button class="btn danger" type="button" data-act="canceled">⛔ 取消订单</button>` : ""}
      </div>
      <div class="actions">
        <button class="btn" type="button" data-print="slip">🧾 装箱单</button>
        ${local ? `<button class="btn" type="button" data-print="manifest">🗒️ 司机单</button>` : `<button class="btn" type="button" data-print="bol">📄 BOL 提单</button>`}
        <button class="btn" type="button" data-print="label">🏷️ 地址标签</button>
      </div>
      <div class="err" id="actErr"></div>
    </div>`;
}

function freightPatch() {
  const v = id => { const el = $(id); return el ? el.value.trim() : undefined; };
  const p = {
    delivery_type: v("fx-type"), appt_date: v("fx-date"), appt_window: v("fx-win"),
    pallets: v("fx-pallets"), weight_lb: v("fx-weight"), freight_class: v("fx-class"), liftgate: $("fx-liftgate").checked,
  };
  if ($("fx-truck")) Object.assign(p, { truck: v("fx-truck"), stop_no: v("fx-stop") });
  if ($("fx-pro")) Object.assign(p, { freight_carrier: v("fx-carrier"), pro: v("fx-pro"), pickup_date: v("fx-pickup") });
  if (!!p.appt_date !== !!p.appt_window) throw new Error("日期和时段要一起填");
  return p;
}

// Returns true when the click was a freight action.
async function freightClick(t, o, err) {
  if (t.dataset.fx === "copylink") { await copyText(fullUrl(o.schedule_url), "📋 预约链接已复制，可以发短信给顾客"); return true; }
  if (t.dataset.fx === "resend") { if (await guard(() => api.resendBooking(o.id), err)) { toast("📧 已重发"); openOrder(o.id); } return true; }
  if (t.dataset.fx === "save") {
    let patch; try { patch = freightPatch(); } catch (x) { err.textContent = "⚠️ " + x.message; return true; }
    t.disabled = true;
    if (await guard(() => api.updateOrder(o.id, patch), err)) { toast("💾 卡派信息已保存"); await loadOrders(); openOrder(o.id); }
    else t.disabled = false;
    return true;
  }
  if (t.dataset.fs) {
    let patch = {}; try { patch = freightPatch(); } catch (x) { err.textContent = "⚠️ " + x.message; return true; }
    patch.freight_status = t.dataset.fs;
    t.disabled = true;
    const r = await guard(() => api.updateOrder(o.id, patch), err);
    if (r) { toast(`🚛 ${FS_LABEL[r.freight_status]}`); await loadOrders(); openOrder(o.id); if (!$("deliveriesView").hidden) loadDeliveries(); }
    else t.disabled = false;
    return true;
  }
  if (t.dataset.print === "manifest") { goPrint("manifest", [o.id], o.appt_date ? `&date=${o.appt_date}&truck=${encodeURIComponent(o.truck || "")}` : ""); return true; }
  return false;
}

// ---------------------------------------------------------------- deliveries calendar
async function loadDeliveries() {
  const s = await guard(() => getSettings(true)); if (!s) return;
  if (!calFrom) calFrom = localISO(new Date());
  const d = await guard(() => api.deliveries(calFrom, 14)); if (!d) return;
  const view = $("deliveriesView");
  const winCap = s.capacity;
  view.innerHTML = `
    <div class="toolbar">
      <h1>送货</h1><div class="grow"></div>
      <button class="btn" data-cal="-7">◀ 上一周</button><button class="btn" data-cal="0">今天</button><button class="btn" data-cal="7">下一周 ▶</button>
      <button class="btn brass" data-cal="remind">📧 发送明天的提醒</button>
    </div>
    ${d.waiting.length ? `<div class="sec waitbox"><h3>📅 等顾客预约（${d.waiting.length}）</h3><div class="list">${d.waiting.map(o => `
      <div class="wrow"><span class="no">${o.order_no}</span><span>${esc(o.ship_name)} · ${esc(o.ship_city)}, ${esc(o.ship_state)}</span>
      <span class="tag">${o.delivery_type === "local" ? "🚚 自送" : "🚛 LTL"}</span><span class="sub">付款 ${fmtTime(o.paid_at)}</span>
      <span class="grow"></span><button class="btn" data-copy-link="${esc(o.schedule_url)}">📋 预约链接</button><button class="btn" data-open-order="${o.id}">打开</button></div>`).join("")}</div></div>` : ""}
    <div class="days">${d.days.map(day => {
      const byWin = s.windows.map(w => ({ w, list: day.orders.filter(o => o.appt_window === w.id) }));
      const trucks = [...new Set(day.orders.filter(o => o.delivery_type === "local").map(o => o.truck || ""))];
      const ltl = day.orders.filter(o => o.delivery_type === "ltl");
      return `<section class="dcard ${day.open ? "" : "closed"}">
        <header><b>${dayLabel(day.date)}</b>${day.date === localISO(new Date()) ? '<span class="tag">今天</span>' : ""}
          ${day.open ? day.windows.map(w => `<span class="cap ${w.local >= winCap ? "full" : ""}">${esc(w.label)} · 自送 ${w.local}/${winCap}</span>`).join("") : '<span class="sub">不送货</span>'}
          <span class="grow"></span>
          ${trucks.map(t => `<button class="btn sm" data-manifest="${day.date}" data-truck="${esc(t)}">🗒️ ${t ? esc(t) : "未分配车辆"} 司机单</button>`).join("")}
          ${ltl.length ? `<button class="btn sm" data-bols="${ltl.map(o => o.id).join(",")}">📄 BOL ×${ltl.length}</button>` : ""}
        </header>
        ${day.orders.length ? byWin.filter(x => x.list.length).map(({ w, list }) => `
          <div class="wlabel">${esc(w.label)}</div>
          ${list.map(o => `<button class="drow" data-open-order="${o.id}">
            <span class="no">${o.order_no}</span>
            <span class="who">${esc(o.ship_name)}<span class="sub"> · ${esc(o.ship_city)}</span></span>
            <span class="tag">${o.delivery_type === "local" ? `🚚 ${esc(o.truck || "未分配")}${o.stop_no ? " #" + o.stop_no : ""}` : `🚛 ${esc(o.freight_carrier || "LTL")}${o.pro ? " · " + esc(o.pro) : ""}`}</span>
            <span class="tag">🧤 ${esc(o.service_label)}</span>
            <span>${FS_LABEL[o.freight_status] || ""}</span></button>`).join("")}`).join("")
          : day.open ? `<div class="sub" style="padding:6px 2px">没有预约</div>` : ""}
      </section>`;
    }).join("")}</div>`;
}

document.addEventListener("click", async e => {
  const t = e.target.closest("button"); if (!t || !t.closest("#deliveriesView")) return;
  if (t.dataset.cal === "remind") {
    const r = await guard(() => api.sendReminders());
    if (r) { toast(r.sent.length ? `📧 已给 ${r.sent.length} 位顾客发提醒` : "明天没有需要提醒的送货"); loadDeliveries(); }
    return;
  }
  if (t.dataset.cal != null) {
    const n = +t.dataset.cal;
    const d = n === 0 ? new Date() : new Date(calFrom + "T12:00:00"); d.setDate(d.getDate() + n);
    calFrom = localISO(d); return loadDeliveries();
  }
  if (t.dataset.openOrder) return openOrder(t.dataset.openOrder);
  if (t.dataset.copyLink) return copyText(fullUrl(t.dataset.copyLink), "📋 预约链接已复制");
  if (t.dataset.manifest) {
    const d = await guard(() => api.deliveries(t.dataset.manifest, 1)); if (!d) return;
    const ids = d.days[0].orders.filter(o => o.delivery_type === "local" && (o.truck || "") === t.dataset.truck).map(o => o.id);
    return goPrint("manifest", ids, `&date=${t.dataset.manifest}&truck=${encodeURIComponent(t.dataset.truck)}`);
  }
  if (t.dataset.bols) return goPrint("bol", t.dataset.bols.split(","));
});

// ---------------------------------------------------------------- settings
async function loadSettings() {
  const s = await guard(() => getSettings(true)); if (!s) return;
  const view = $("settingsView");
  view.innerHTML = `
    <div class="toolbar"><h1>送货设置</h1></div>
    <form class="settings" id="sform">
      <section class="sbox"><h3>🕘 送货时段</h3>
        <div id="wins">${s.windows.map(w => winRow(w)).join("")}</div>
        <button class="btn" type="button" id="addWin">➕ 加一个时段</button>
        <label class="f">每个时段最多几单（只限本地自送）<input class="in" type="number" min="1" id="s-cap" value="${s.capacity}"></label>
      </section>
      <section class="sbox"><h3>📆 哪些天送货</h3>
        <div class="wkdays">${[1, 2, 3, 4, 5, 6, 7].map(d => `<label class="check"><input type="checkbox" name="wd" value="${d}" ${s.weekdays.includes(d) ? "checked" : ""}> ${WEEK_ZH[d]}</label>`).join("")}</div>
        <div class="two">
          <label class="f">最早几天后可约<input class="in" type="number" min="0" id="s-lead" value="${s.lead_days}"></label>
          <label class="f">最多可约到几天后<input class="in" type="number" min="7" id="s-horizon" value="${s.horizon_days}"></label>
        </div>
        <label class="f">顾客改约截止：送货前一天几点（0–23）<input class="in" type="number" min="0" max="23" id="s-cutoff" value="${s.cutoff_hour}"></label>
        <label class="f">不送货的日期（节假日），每行一个 YYYY-MM-DD<textarea class="in" id="s-blackout" rows="3">${s.blackout.join("\n")}</textarea></label>
      </section>
      <section class="sbox"><h3>🚚 本地车队</h3>
        <label class="f">本地配送的 ZIP 开头（逗号分隔，例如 940,941,945）<textarea class="in" id="s-zips" rows="2">${esc(s.local_zips)}</textarea></label>
        <label class="f">车辆（逗号分隔）<input class="in" id="s-trucks" value="${esc(s.trucks.join(", "))}"></label>
        <div class="tip">💡 收货地址的 ZIP 开头在列表里，就自动分给本地车队；其他的走 LTL 承运商。每个订单也可以在订单里手动改。</div>
      </section>
      <section class="sbox"><h3>🚛 LTL 承运商</h3>
        <label class="f">给顾客看的说明<textarea class="in" id="s-ltl" rows="2">${esc(s.ltl_note)}</textarea></label>
      </section>
      <div class="actions"><button class="btn primary" type="submit">💾 保存设置</button></div>
      <div class="err" id="sErr"></div>
    </form>`;
  $("addWin").onclick = () => $("wins").insertAdjacentHTML("beforeend", winRow({ id: "w" + Date.now().toString(36).slice(-4), label: "" }));
  $("wins").onclick = e => { const b = e.target.closest("[data-delwin]"); if (b) b.closest(".winrow").remove(); };
  $("sform").onsubmit = async e => {
    e.preventDefault(); $("sErr").textContent = "";
    const body = {
      windows: [...document.querySelectorAll(".winrow")].map(r => ({ id: r.dataset.id, label: r.querySelector("input").value })),
      capacity: +$("s-cap").value, weekdays: [...document.querySelectorAll("[name=wd]:checked")].map(x => +x.value),
      lead_days: +$("s-lead").value, horizon_days: +$("s-horizon").value, cutoff_hour: +$("s-cutoff").value,
      blackout: $("s-blackout").value.split(/[\s,]+/).filter(Boolean), local_zips: $("s-zips").value,
      trucks: $("s-trucks").value.split(",").map(x => x.trim()).filter(Boolean), ltl_note: $("s-ltl").value,
    };
    const r = await guard(() => api.saveSettings(body), $("sErr"));
    if (r) { SETTINGS = r; toast("💾 设置已保存"); loadSettings(); }
  };
}
function winRow(w) {
  return `<div class="winrow" data-id="${esc(w.id)}"><input class="in" value="${esc(w.label)}" placeholder="例如 8 AM – 12 PM"><button class="btn danger" type="button" data-delwin>🗑️</button></div>`;
}
