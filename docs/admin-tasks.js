// Order notes, to-dos with reminders, the 🔔 bell, and the email page.
// Uses helpers from admin.html ($, esc, t, tr, toast, guard, openPanel, openOrder, fmtTime…).

let todoFilter = "open", bellSeen = new Set(), bellTimer = null, mailFilter = "";
const localInput = iso => { if (!iso) return ""; const d = new Date(iso); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };
const fromLocalInput = v => v ? new Date(v).toISOString() : "";
const dueText = iso => iso ? new Date(iso).toLocaleString(LOCALE, { month: "numeric", day: "numeric", weekday: "short", hour: "numeric", minute: "2-digit" }) : t("没有到期时间");

// ---------------------------------------------------------------- order panel: notes + linked to-dos
function notesSection(o) {
  const notes = o.notes || [], todos = (o.todos || []);
  return `<div class="sec"><h3>${t("📝 备注和待办")}</h3>
    ${notes.length ? `<ul class="notes">${notes.map(n => `<li>
        <div class="ntext">${esc(n.text)}</div>
        <div class="nmeta"><span>${fmtTime(n.created_at)}</span>
          ${n.remind_at ? `<span class="tag">⏰ ${esc(dueText(n.remind_at))}</span>` : ""}
          <button type="button" class="chip ${n.print ? "on" : ""}" data-nprint="${n.id}" aria-pressed="${n.print}" title="${t("是否印在标签、装箱单和司机单上")}">${n.print ? t("🏷️ 印在标签上") : t("🏷️ 不打印")}</button>
          <button type="button" class="link danger" data-ndel="${n.id}" aria-label="${t("删除备注")}">🗑️</button></div></li>`).join("")}</ul>`
      : `<div class="sub">${t("还没有备注。")}</div>`}
    <div class="box nform">
      <textarea class="in" id="n-text" rows="2" placeholder="${t("写一条备注，例如：玻璃台面易碎，两人搬运")}"></textarea>
      <div class="nrow">
        <label class="check"><input type="checkbox" id="n-print" checked> ${t("印在标签上")}</label>
        <label class="f">${t("⏰ 提醒时间（选填）")}<input class="in" type="datetime-local" id="n-remind"></label>
        <label class="check"><input type="checkbox" id="n-email"> ${t("到时发邮件提醒")}</label>
      </div>
      <div class="actions"><button class="btn primary" type="button" data-nadd>${t("➕ 加备注")}</button></div>
    </div>
    ${todos.length ? `<h3 style="margin-top:6px">${t("✅ 这个订单的待办")}</h3><ul class="todos">${todos.map(todoRow).join("")}</ul>` : ""}
    <div class="actions"><button class="btn" type="button" data-tnew="${o.id}" data-tno="${esc(o.order_no)}">${t("➕ 给这个订单加待办")}</button></div>
  </div>`;
}

function todoRow(x) {
  return `<li class="todo ${x.status} ${x.overdue ? "overdue" : ""}">
    <button type="button" class="tick" data-tdone="${x.id}" data-to="${x.status === "open" ? "done" : "open"}" aria-label="${x.status === "open" ? t("标记完成") : t("重新打开")}">${x.status === "done" ? "☑" : "☐"}</button>
    <div class="tbody"><div class="ttitle">${esc(tr(x.title))}</div>
      <div class="tmeta">${x.due_at ? `<span class="${x.overdue ? "late" : ""}">⏰ ${esc(dueText(x.due_at))}${x.overdue ? " · " + t("已过期") : ""}</span>` : ""}
        ${x.order_no ? `<button type="button" class="link" data-topen="${x.order_id}">📦 ${esc(x.order_no)}${x.ship_name ? " · " + esc(x.ship_name) : ""}</button>` : ""}
        ${x.email ? `<span>📧</span>` : ""}${x.print ? `<span title="${t("印在标签上")}">🏷️</span>` : ""}${x.source === "auto" ? `<span class="tag">🤖 ${t("自动")}</span>` : ""}</div>
      ${x.note ? `<div class="sub">${esc(x.note)}</div>` : ""}</div>
    <button type="button" class="link" data-tedit="${x.id}" aria-label="${t("编辑")}">✏️</button>
  </li>`;
}

async function notesClick(btn, o, err) {
  if (!btn) return false;
  if ("nadd" in btn.dataset) {
    const text = $("n-text").value.trim();
    if (!text) { err.textContent = "⚠️ " + t("备注不能是空的"); return true; }
    btn.disabled = true;
    const r = await guard(() => api.addNote(o.id, { text, print: $("n-print").checked, remind_at: fromLocalInput($("n-remind").value), email: $("n-email").checked }), err);
    if (r) { toast(t("📝 备注已保存")); openOrder(o.id); refreshBell(); } else btn.disabled = false;
    return true;
  }
  if (btn.dataset.nprint) { const n = o.notes.find(x => String(x.id) === btn.dataset.nprint); if (await guard(() => api.updateNote(o.id, n.id, { print: !n.print }), err)) openOrder(o.id); return true; }
  if (btn.dataset.ndel) {
    if (btn.dataset.sure !== "1") { btn.dataset.sure = "1"; btn.textContent = t("🗑️ 再点一次确认"); return true; }
    if (await guard(() => api.deleteNote(o.id, btn.dataset.ndel), err)) { toast(t("🗑️ 已删除")); openOrder(o.id); }
    return true;
  }
  if (btn.dataset.tdone) { if (await guard(() => api.saveTodo({ ...findTodo(o.todos, btn.dataset.tdone), status: btn.dataset.to }), err)) { openOrder(o.id); refreshBell(); } return true; }
  if (btn.dataset.tedit) { todoPanel(findTodo(o.todos, btn.dataset.tedit), o.id); return true; }
  if (btn.dataset.tnew) { todoPanel({ order_id: btn.dataset.tnew, order_no: btn.dataset.tno, print: true }, o.id); return true; }
  return false;
}
const findTodo = (list, id) => (list || []).find(x => x.id === id);

// ---------------------------------------------------------------- to-do tab
async function loadTodos() {
  const d = await guard(() => api.todos(todoFilter)); if (!d) return;
  const s = d.summary, list = d.todos;
  const groups = todoFilter === "done" ? [[t("已完成"), list]] : [
    [t("⚠️ 已过期"), list.filter(x => x.overdue)],
    [t("📅 24 小时内"), list.filter(x => !x.overdue && x.due_at && new Date(x.due_at) - Date.now() < 864e5)],
    [t("🗓️ 以后"), list.filter(x => !x.overdue && x.due_at && new Date(x.due_at) - Date.now() >= 864e5)],
    [t("📌 没有到期时间"), list.filter(x => !x.due_at)],
  ];
  $("todosView").innerHTML = `
    <div class="toolbar"><h1>${t("待办")}</h1><div class="grow"></div>
      <button class="btn ${Notif.state() === "granted" ? "" : "brass"}" id="notifBtn" ${Notif.state() === "unsupported" ? "hidden" : ""}>${Notif.state() === "granted" ? t("🔔 浏览器提醒已开") : t("🔔 打开浏览器提醒")}</button>
      <button class="btn primary" id="todoNew">${t("➕ 新待办")}</button></div>
    <div class="filters three-col">
      <button class="filter ${s.overdue ? "attn" : ""}" data-tf="open" aria-pressed="${todoFilter === "open"}"><b>${s.open}</b><span>${t("📋 未完成")}</span></button>
      <button class="filter ${s.overdue ? "attn" : ""}" data-tf="open" aria-pressed="false"><b>${s.overdue}</b><span>${t("⚠️ 已过期")}</span></button>
      <button class="filter" data-tf="done" aria-pressed="${todoFilter === "done"}"><b>✓</b><span>${t("☑ 已完成")}</span></button>
    </div>
    ${groups.filter(g => g[1].length).map(([h, xs]) => `<div class="sec"><h3>${h} (${xs.length})</h3><ul class="todos card">${xs.map(todoRow).join("")}</ul></div>`).join("")
      || `<div class="empty">${todoFilter === "done" ? t("还没有完成的待办") : t("🎉 没有待办。系统也会自动提醒：两天没约送货、两小时没打印标签、LTL 快到日子还没有 PRO 单号。")}</div>`}
    <div class="tip">${t("💡 到期的待办会让右上角 🔔 变红；勾了「发邮件」的会发到「📧 邮件」里设置的员工邮箱。带 🏷️ 的待办会印在这个订单的标签和装箱单上，完成后就不再打印。")}</div>`;
  const v = $("todosView");
  v.onclick = async e => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.id === "todoNew") return todoPanel({});
    if (b.id === "notifBtn") { await Notif.ask(); return loadTodos(); }
    if (b.dataset.tf) { todoFilter = b.dataset.tf; return loadTodos(); }
    if (b.dataset.topen) return openOrder(b.dataset.topen);
    if (b.dataset.tedit) return todoPanel(findTodo(list, b.dataset.tedit));
    if (b.dataset.tdone) { if (await guard(() => api.saveTodo({ ...findTodo(list, b.dataset.tdone), status: b.dataset.to }))) { toast(b.dataset.to === "done" ? t("☑ 完成了") : t("↩️ 已重新打开")); loadTodos(); refreshBell(); } }
  };
}

function todoPanel(x, backToOrder) {
  const isNew = !x.id;
  openPanel(`<div class="ph"><h2>${isNew ? t("➕ 新待办") : t("✏️ 编辑待办")}</h2><button class="x" data-close aria-label="${t("关闭")}">✕</button></div>
    <form class="pb" id="tdForm">
      <label class="f">${t("要做什么")}<input class="in" id="td-title" value="${esc(x.title ? tr(x.title) : "")}" placeholder="${t("例如：给顾客打电话确认门禁码")}" required></label>
      <label class="f">${t("⏰ 到期时间（选填）")}<input class="in" type="datetime-local" id="td-due" value="${esc(localInput(x.due_at))}"></label>
      <label class="f">${t("关联订单号（选填）")}<input class="in mono" id="td-order" value="${esc(x.order_no || "")}" placeholder="MJ261104-001" autocapitalize="characters"></label>
      <label class="f">${t("说明（选填）")}<textarea class="in" id="td-note" rows="2">${esc(x.note || "")}</textarea></label>
      <label class="check"><input type="checkbox" id="td-email" ${x.email ? "checked" : ""}> ${t("到期时发邮件给员工")}</label>
      <label class="check"><input type="checkbox" id="td-print" ${x.print ? "checked" : ""}> ${t("没完成前印在订单的标签和装箱单上")}</label>
      <div class="actions"><button class="btn primary" type="submit">${t("💾 保存")}</button>
        ${isNew ? "" : `<button class="btn" type="button" data-tdtoggle>${x.status === "done" ? t("↩️ 重新打开") : t("☑ 标记完成")}</button><button class="btn danger" type="button" data-tddel>${t("🗑️ 删除")}</button>`}</div>
      <div class="err" id="tdErr"></div>
    </form>`);
  const done = () => { closePanel(); refreshBell(); if (backToOrder) openOrder(backToOrder); else if (!$("todosView").hidden) loadTodos(); };
  $("tdForm").onsubmit = async e => {
    e.preventDefault();
    const body = { id: x.id, title: $("td-title").value, due_at: fromLocalInput($("td-due").value), order_no: $("td-order").value.trim(),
      order_id: $("td-order").value.trim() ? undefined : null, note: $("td-note").value, email: $("td-email").checked, print: $("td-print").checked };
    if (x.order_id && $("td-order").value.trim() === (x.order_no || "")) { body.order_id = x.order_id; delete body.order_no; }
    if (await guard(() => api.saveTodo(body), $("tdErr"))) { toast(t("💾 待办已保存")); done(); }
  };
  $("tdForm").onclick = async e => {
    const b = e.target.closest("button"); if (!b) return;
    if ("tdtoggle" in b.dataset) { if (await guard(() => api.saveTodo({ ...x, status: x.status === "done" ? "open" : "done" }), $("tdErr"))) done(); }
    if ("tddel" in b.dataset) {
      if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = t("🗑️ 再点一次确认删除"); return; }
      if (await guard(() => api.deleteTodo(x.id), $("tdErr"))) { toast(t("🗑️ 已删除")); done(); }
    }
  };
}

// ---------------------------------------------------------------- bell
const Notif = {
  state: () => !("Notification" in window) ? "unsupported" : Notification.permission,
  async ask() { try { await Notification.requestPermission(); } catch (e) { toast(t("这个浏览器不能显示提醒")); } },
  show(title, body) { try { if (this.state() === "granted") new Notification(title, { body }); } catch (e) {} },
};
async function refreshBell() {
  const s = await api.todoSummary().catch(() => null); if (!s) return;
  const b = $("bell"); if (!b) return;
  b.innerHTML = `🔔${s.overdue ? `<b>${s.overdue}</b>` : ""}`;
  b.classList.toggle("hot", s.overdue > 0);
  b.title = t("{n} 个待办已到期", { n: s.overdue });
  for (const it of s.due_items || []) {
    if (bellSeen.has(it.id)) continue;
    bellSeen.add(it.id);
    if (refreshBell.primed) { toast("⏰ " + tr(it.title)); Notif.show(t("⏰ 待办到期"), tr(it.title)); }
  }
  refreshBell.primed = true;
}
function startBell() { clearInterval(bellTimer); refreshBell(); bellTimer = setInterval(refreshBell, 60000); }

// ---------------------------------------------------------------- email page
const MAIL_ST = { queued: ["⏳", "等待发送"], sending: ["📤", "发送中"], sent: ["✅", "已发送"], failed: ["⚠️", "发送失败"], not_configured: ["💤", "未配置，没发出"], held: ["🧪", "测试订单，没发出"] };
const KIND = { customer: "👤 顾客", staff: "👥 员工", test: "🧪 测试" };
async function loadMail() {
  const [m, d] = await Promise.all([guard(() => api.mailSettings()), guard(() => api.emails(mailFilter))]);
  if (!m || !d) return;
  const presets = [["Gmail", "smtp.gmail.com", 587, "starttls"], ["Outlook / Microsoft 365", "smtp.office365.com", 587, "starttls"],
                   ["Yahoo", "smtp.mail.yahoo.com", 465, "ssl"], ["SendGrid", "smtp.sendgrid.net", 587, "starttls"], ["Mailgun", "smtp.mailgun.org", 587, "starttls"]];
  $("mailView").innerHTML = `
    <div class="toolbar"><h1>${t("邮件")}</h1><div class="grow"></div>
      <span class="pill ${m.configured ? "st-delivered" : "st-paid"}">${m.configured ? t("✅ 已接好") : t("🟠 还没接邮件服务")}</span></div>
    <div class="mailgrid">
    <form class="sbox" id="mailForm">
      <h3>${t("📮 发信设置（SMTP）")}</h3>
      <div class="presets">${t("常用：")}${presets.map(p => `<button type="button" class="chip" data-preset="${p[1]}|${p[2]}|${p[3]}">${p[0]}</button>`).join("")}</div>
      <div class="two"><label class="f">${t("发信服务器")}<input class="in" id="m-host" value="${esc(m.host)}" placeholder="smtp.gmail.com"></label>
        <label class="f">${t("端口")}<input class="in" id="m-port" type="number" value="${m.port}"></label></div>
      <label class="f">${t("加密方式")}<select class="in" id="m-sec">${[["starttls", "STARTTLS (587)"], ["ssl", "SSL/TLS (465)"], ["none", t("不加密（不推荐）")]].map(([v, l]) => `<option value="${v}" ${m.security === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <div class="two"><label class="f">${t("登录账号")}<input class="in" id="m-user" value="${esc(m.user)}" autocomplete="off"></label>
        <label class="f">${t("密码 / 应用专用密码")}<input class="in" id="m-pass" type="password" autocomplete="new-password" placeholder="${m.has_password ? t("已保存，留空就不改") : ""}"></label></div>
      <div class="two"><label class="f">${t("发件地址")}<input class="in" id="m-from" value="${esc(m.from_addr)}" placeholder="orders@yourstore.com"></label>
        <label class="f">${t("发件人名称")}<input class="in" id="m-fname" value="${esc(m.from_name)}" placeholder="Mujian Furniture"></label></div>
      <div class="tip">${t("💡 Gmail 要先开两步验证，再在 Google 账号里生成「应用专用密码」填在这里，不能用平时的登录密码。")}</div>
      <h3>${t("👥 员工通知")}</h3>
      <label class="f">${t("员工邮箱（逗号分隔）")}<input class="in" id="m-staff" value="${esc((m.staff || []).join(", "))}" placeholder="ops@yourstore.com, you@gmail.com"></label>
      <label class="f">${t("员工邮件的语言")}<select class="in" id="m-slang">${[["zh", "中文"], ["en", "English"], ["es", "Español"]].map(([v, l]) => `<option value="${v}" ${m.staff_lang === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="check"><input type="checkbox" id="m-neworder" ${m.notify_new_order ? "checked" : ""}> ${t("有新订单时通知员工")}</label>
      <label class="check"><input type="checkbox" id="m-todos" ${m.notify_todos ? "checked" : ""}> ${t("待办到期时通知员工（勾了「发邮件」的待办）")}</label>
      <label class="check"><input type="checkbox" id="m-cust" ${m.customer_emails ? "checked" : ""}> ${t("给顾客发邮件（预约链接、预约确认、送货提醒、发货通知）")}</label>
      <div class="actions"><button class="btn primary" type="submit">${t("💾 保存")}</button></div>
      <div class="err" id="mErr"></div>
      <h3>${t("🧪 发一封测试邮件")}</h3>
      <div class="whpick"><input class="in" id="m-testto" type="email" placeholder="you@example.com"><button type="button" class="btn" id="m-test">${t("📨 发送测试")}</button></div>
      <div class="sub" id="m-testres"></div>
    </form>
    <section class="sbox outbox">
      <h3>${t("📤 发件记录")}</h3>
      <div class="filters2 toolbar">${[["", t("全部")], ["sent", t("已发送")], ["queued", t("等待发送")], ["failed", t("发送失败")], ["not_configured", t("未配置，没发出")], ["held", t("测试订单，没发出")]].map(([k, l]) =>
        `<button type="button" class="chip ${mailFilter === k ? "on" : ""}" data-mf="${k}">${l}${k && d.counts[k] ? ` ${d.counts[k]}` : ""}</button>`).join("")}</div>
      ${d.emails.length ? `<ul class="mails">${d.emails.map(x => `<li>
        <button type="button" class="mrow" data-mview="${x.id}">
          <span class="ms">${MAIL_ST[x.status][0]}</span>
          <span class="mmain"><b>${esc(x.subject)}</b><span class="sub">${esc(x.to_addr)} · ${t(KIND[x.kind] || "")}${x.order_no ? " · " + esc(x.order_no) : ""}</span>
            ${x.error ? `<span class="err">${esc(tr(x.error))}</span>` : ""}</span>
          <span class="sub mono">${fmtTime(x.sent_at || x.created_at)}</span></button></li>`).join("")}</ul>`
        : `<div class="empty">${t("还没有邮件记录")}</div>`}
    </section></div>`;
  const v = $("mailView");
  v.onclick = async e => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.preset) { const [h, p, s] = b.dataset.preset.split("|"); $("m-host").value = h; $("m-port").value = p; $("m-sec").value = s; return; }
    if (b.dataset.mf != null) { mailFilter = b.dataset.mf; return loadMail(); }
    if (b.dataset.mview) return mailPanel(d.emails.find(x => String(x.id) === b.dataset.mview));
    if (b.id === "m-test") {
      $("m-testres").textContent = t("⏳ 正在发送…"); b.disabled = true;
      const r = await guard(() => api.testMail($("m-testto").value.trim()), $("m-testres"));
      b.disabled = false;
      if (r) { $("m-testres").textContent = t("✅ 已发出，去收件箱看看（也看看垃圾邮件）"); loadMail(); }
    }
  };
  $("mailForm").onsubmit = async e => {
    e.preventDefault();
    const body = { host: $("m-host").value, port: $("m-port").value, security: $("m-sec").value, user: $("m-user").value, password: $("m-pass").value,
      from_addr: $("m-from").value, from_name: $("m-fname").value, staff: $("m-staff").value, staff_lang: $("m-slang").value,
      notify_new_order: $("m-neworder").checked, notify_todos: $("m-todos").checked, customer_emails: $("m-cust").checked };
    if (await guard(() => api.saveMail(body), $("mErr"))) { toast(t("💾 邮件设置已保存")); loadMail(); }
  };
}
function mailPanel(x) {
  openPanel(`<div class="ph"><h2>📧 ${esc(x.subject)}</h2><button class="x" data-close aria-label="${t("关闭")}">✕</button></div>
    <div class="pb">
      <div class="box"><div class="kv"><span>${t("收件人")}</span><span>${esc(x.to_addr)}</span></div>
        <div class="kv"><span>${t("状态")}</span><span>${MAIL_ST[x.status][0]} ${t(MAIL_ST[x.status][1])}</span></div>
        <div class="kv"><span>${t("时间")}</span><span>${fmtTime(x.sent_at || x.created_at)}</span></div>
        ${x.error ? `<div class="err">${esc(tr(x.error))}</div>` : ""}</div>
      <pre class="mailbody">${esc(x.body)}</pre>
      <div class="actions"><button class="btn primary" type="button" id="mResend">${t("🔁 重新发送")}</button>
        ${x.order_id ? `<button class="btn" type="button" id="mOrder">📦 ${esc(x.order_no || "")}</button>` : ""}</div>
      <div class="err" id="mrErr"></div>
    </div>`);
  $("mResend").onclick = async () => { const r = await guard(() => api.resendEmail(x.id), $("mrErr")); if (r) { toast("🔁 " + tr(r.status)); closePanel(); loadMail(); } };
  if ($("mOrder")) $("mOrder").onclick = () => { closePanel(); openOrder(x.order_id); };
}
