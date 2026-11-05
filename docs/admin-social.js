// 📣 Social media in the admin: profile links, catalog feed for shops, posting to Facebook / Instagram / Telegram.
// Uses helpers from admin.html ($, esc, t, toast, guard, fmtTime, products).

const SOC_LINKS = [["instagram", "📸 Instagram"], ["facebook", "👍 Facebook"], ["tiktok", "🎵 TikTok"], ["pinterest", "📌 Pinterest"],
  ["youtube", "▶️ YouTube"], ["x", "𝕏 X"], ["whatsapp", "💬 WhatsApp"]];
const SOC_TARGETS = [["facebook", "👍 Facebook"], ["instagram", "📸 Instagram"], ["telegram", "✈️ Telegram"]];
const POST_ST = { queued: ["⏳", "排队中"], sending: ["📤", "发送中"], done: ["✅", "已发出"], partial: ["⚠️", "部分发出"], failed: ["❌", "没发出去"] };

async function socialCopy(text, msg) {
  try { await navigator.clipboard.writeText(text); toast(msg || t("📋 已复制")); }
  catch (e) { prompt(t("复制下面的内容"), text); }
}

async function loadSocial() {
  const [s, posts] = await Promise.all([guard(() => api.socialSettings()), guard(() => api.socialPosts())]);
  if (!s || !posts) return;
  const pl = (typeof products !== "undefined" && products.length ? products : await api.adminProducts().catch(() => [])).filter(p => p.active);
  const ready = s.ready || {};
  const feedEs = s.feed + "?lang=es", checkoutUrl = s.feed.replace("/feeds/products.xml", "/checkout");
  $("socialView").innerHTML = `
    <div class="toolbar"><h1>${t("社交平台")}</h1><div class="grow"></div>
      ${SOC_TARGETS.map(([k, l]) => `<span class="pill ${ready[k] ? "st-delivered" : ""}">${l} ${ready[k] ? "✅" : "—"}</span>`).join("")}</div>
    ${s.local ? `<div class="tip warnbox">${t("⚠️ 网站现在跑在本机（localhost）。分享卡片、商品目录和发帖都要网站上线、有正式网址以后才能用；下面可以先把账号填好。")}</div>` : ""}
    <div class="socgrid">
    <form class="sbox" id="socForm">
      <h3>${t("🔗 社交账号（显示在商店底部「关注我们」）")}</h3>
      ${SOC_LINKS.map(([k, l]) => `<label class="f">${l}<input class="in" data-link="${k}" value="${esc(s.links[k] || "")}"
        placeholder="${k === "whatsapp" ? t("电话号码，例如 (510) 555-0100") : t("账号名（@mujian）或完整网址")}" autocomplete="off"></label>`).join("")}
      <h3>${t("📣 自动发帖的账号")}</h3>
      <div class="two"><label class="f">${t("Facebook 主页 ID")}<input class="in" id="s-fbpage" value="${esc(s.fb_page_id)}" autocomplete="off"></label>
        <label class="f">${t("主页访问令牌（Page token）")}<input class="in" id="s-fbtoken" type="password" autocomplete="new-password" placeholder="${s.has_fb_token ? t("已保存，留空就不改") : "EAA…"}"></label></div>
      <label class="f">${t("Instagram 账号 ID（可以留空，点「检查连接」自动找到）")}<input class="in" id="s-ig" value="${esc(s.ig_user_id)}" autocomplete="off"></label>
      <div class="two"><label class="f">${t("Telegram 机器人令牌")}<input class="in" id="s-tgtoken" type="password" autocomplete="new-password" placeholder="${s.has_tg_token ? t("已保存，留空就不改") : "123456:ABC…"}"></label>
        <label class="f">${t("Telegram 频道")}<input class="in" id="s-tgchat" value="${esc(s.tg_chat)}" placeholder="@mujianshop" autocomplete="off"></label></div>
      <div class="two"><label class="f">${t("帖子用什么语言")}<select class="in" id="s-lang">${[["en", "English"], ["es", "Español"], ["both", "English + Español"]].map(([v, l]) => `<option value="${v}" ${s.lang === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="f">${t("话题标签")}<input class="in" id="s-tags" value="${esc(s.hashtags)}"></label></div>
      <div class="f">${t("默认发到")}<div class="actions">${SOC_TARGETS.map(([k, l]) => `<label class="check"><input type="checkbox" name="s-tg" value="${k}" ${s.targets.includes(k) ? "checked" : ""}> ${l}</label>`).join("")}</div></div>
      <label class="check"><input type="checkbox" id="s-auto" ${s.auto_new ? "checked" : ""}> ${t("上新商品自动发帖（保存后 15 分钟发出，留时间传照片）")}</label>
      <div class="actions"><button class="btn primary" type="submit">${t("💾 保存")}</button><button class="btn" type="button" id="socCheck">${t("🔌 检查连接")}</button></div>
      <div class="err" id="socErr"></div><div class="sub" id="socCheckRes"></div>
      <details class="howto"><summary>${t("❓ Facebook / Instagram 的 ID 和令牌怎么拿")}</summary><ol>
        <li>${t("Instagram 先在 App 里切换成「商业账号」或「创作者账号」，并在设置里关联你的 Facebook 主页。")}</li>
        <li>${t("打开 developers.facebook.com，创建一个应用（类型选「商家 Business」）。")}</li>
        <li>${t("打开 Graph API Explorer，选这个应用，勾选权限：pages_show_list、pages_read_engagement、pages_manage_posts、instagram_basic、instagram_content_publish，点「Generate Access Token」。")}</li>
        <li>${t("到 Access Token Debugger 把这个令牌「延长」成长期令牌，再回 Explorer 用它请求 me/accounts：返回里有主页的 id（填「主页 ID」）和 access_token（填「主页访问令牌」，长期有效）。")}</li>
        <li>${t("保存后点「🔌 检查连接」，会自动找到 Instagram 账号。")}</li></ol></details>
      <details class="howto"><summary>${t("❓ Telegram 频道怎么接")}</summary><ol>
        <li>${t("在 Telegram 里找 @BotFather，发 /newbot 建一个机器人，把它给的令牌填进来。")}</li>
        <li>${t("建一个公开频道，把这个机器人加成频道管理员（要有发消息权限）。")}</li>
        <li>${t("「Telegram 频道」填 @频道名。")}</li></ol></details>
    </form>

    <div>
      <section class="sbox">
        <h3>${t("✍️ 发一条")}</h3>
        <label class="f">${t("商品（选了会带上照片、价格和购买链接）")}<select class="in" id="pp-prod"><option value="">${t("— 不选商品，只发文字 —")}</option>
          ${pl.map(p => `<option value="${esc(p.id)}">${esc(typeof pname === "function" ? pname(p) : p.name)} · ${usd(p.price)}</option>`).join("")}</select></label>
        <label class="f">${t("内容（选了商品可以留空，自动生成文案）")}<textarea class="in" id="pp-text" rows="5" placeholder="${t("例如：周末全场实木家具 9 折 🎉")}"></textarea></label>
        <div class="actions">${SOC_TARGETS.map(([k, l]) => `<label class="check"><input type="checkbox" name="pp-tg" value="${k}" ${ready[k] && s.targets.includes(k) ? "checked" : ""} ${ready[k] ? "" : "disabled"}> ${l}${ready[k] ? "" : t("（未接）")}</label>`).join("")}</div>
        <div class="actions"><button class="btn primary" type="button" id="ppGo">${t("📣 发出去")}</button>
          <button class="btn" type="button" id="ppCap">${t("📝 生成文案（TikTok / X 手动发）")}</button></div>
        <div class="err" id="ppErr"></div>
        <div id="ppCapOut"></div>
      </section>
      <section class="sbox">
        <h3>${t("📜 发帖记录")}</h3>
        <div class="postlist">${posts.posts.length ? posts.posts.map(postRow).join("") : `<div class="sub">${t("还没有发过")}</div>`}</div>
      </section>
    </div>

    <section class="sbox">
      <h3>${t("📤 分享按钮")}</h3>
      <div class="sub">${t("每个商品详情里都有分享按钮（Facebook、Pinterest、WhatsApp、X、复制链接，手机上还有系统分享）。分享出去的链接会显示商品照片、名字和价格的预览卡片，点开直接到这个商品。")}</div>
      <div class="sub">${t("商品分享链接：")}<code>${esc(s.share_base)}${t("商品编号")}</code></div>
    </section>

    <section class="sbox">
      <h3>${t("🛍️ 商品目录（同步到各平台卖）")}</h3>
      <div class="sub">${t("把下面的网址填到各平台，它们每天自动来拿最新的商品、价格和库存。顾客在平台上点商品，会回到你的网站用 Stripe 付款。")}</div>
      <label class="f">${t("商品目录网址（英文）")}<div class="whpick"><input class="in" readonly value="${esc(s.feed)}"><button type="button" class="btn" data-copy="${esc(s.feed)}">📋</button></div></label>
      <label class="f">${t("西班牙语目录（可选）")}<div class="whpick"><input class="in" readonly value="${esc(feedEs)}"><button type="button" class="btn" data-copy="${esc(feedEs)}">📋</button></div></label>
      <label class="f">${t("结账网址（Facebook / Instagram 商店要填）")}<div class="whpick"><input class="in" readonly value="${esc(checkoutUrl)}"><button type="button" class="btn" data-copy="${esc(checkoutUrl)}">📋</button></div></label>
      <div class="sub">${t("现在在售 {n} 个商品。", { n: pl.length })}${s.no_photo.length ? " " + t("⚠️ 这些还没有照片，平台会用自动生成的文字图，最好补上实拍：{list}", { list: s.no_photo.join("、") }) : ""}</div>
      <details class="howto"><summary>👍📸 ${t("Facebook / Instagram 商店")}</summary><ol>
        <li>${t("打开 Meta Commerce Manager（business.facebook.com/commerce），创建商品目录。")}</li>
        <li>${t("「数据源」→「添加商品」→「数据馈送」→「定时馈送」，粘贴商品目录网址，频率选每天。")}</li>
        <li>${t("建商店时，结账方式选「在网站上结账」，填上面的结账网址，用平台的检查工具测一下。")}</li>
        <li>${t("Instagram：设置里开启「购物」，关联这个目录，发帖时就能标记商品。")}</li></ol></details>
      <details class="howto"><summary>🔎 ${t("Google 购物（免费展示）")}</summary><ol>
        <li>${t("打开 Google Merchant Center（merchants.google.com），验证并认领你的网站。")}</li>
        <li>${t("「商品」→「添加商品」→「从文件添加」，选「输入文件链接」，粘贴商品目录网址，设每天抓取。")}</li>
        <li>${t("运费和退货政策在 Merchant Center 的设置里填一次。")}</li></ol></details>
      <details class="howto"><summary>📌 ${t("Pinterest")}</summary><ol>
        <li>${t("换成 Pinterest 商业账号，认领你的网站。")}</li>
        <li>${t("「广告」→「目录」→「创建数据源」，粘贴商品目录网址，每天更新。商品会变成可购买的 Pin。")}</li></ol></details>
      <details class="howto"><summary>🎵 ${t("TikTok")}</summary><ol>
        <li>${t("TikTok Shop 要求顾客在 TikTok 里付款、走 TikTok 的发货流程，不能跳回你的网站，所以不适合直接接。")}</li>
        <li>${t("可以在 TikTok 广告后台建「商品目录」，同样填这个网址，用来投商品广告，点了回你的网站买。")}</li>
        <li>${t("平时发视频：用上面的「📝 生成文案」复制文字，360° 图可以在商品里「🎬 导出成视频」。")}</li></ol></details>
    </section>
    </div>`;

  const collect = () => ({
    links: Object.fromEntries([...document.querySelectorAll("[data-link]")].map(i => [i.dataset.link, i.value.trim()])),
    fb_page_id: $("s-fbpage").value.trim(), fb_token: $("s-fbtoken").value.trim(), ig_user_id: $("s-ig").value.trim(),
    tg_token: $("s-tgtoken").value.trim(), tg_chat: $("s-tgchat").value.trim(), lang: $("s-lang").value, hashtags: $("s-tags").value,
    targets: [...document.querySelectorAll("[name=s-tg]:checked")].map(x => x.value), auto_new: $("s-auto").checked,
  });
  $("socForm").onsubmit = async e => {
    e.preventDefault(); $("socErr").textContent = "";
    if (await guard(() => api.saveSocial(collect()), $("socErr"))) { toast(t("💾 已保存")); loadSocial(); }
  };
  $("socCheck").onclick = async () => {
    $("socErr").textContent = ""; $("socCheckRes").textContent = t("⏳ 检查中…");
    if (!await guard(() => api.saveSocial(collect()), $("socErr"))) { $("socCheckRes").textContent = ""; return; }
    const r = await guard(() => api.checkSocial(), $("socErr"));
    $("socCheckRes").innerHTML = r ? Object.values(r.results).map(x => esc(tr(x.text))).join("<br>") : "";
    if (r) setTimeout(() => loadSocial().then(() => { if ($("socCheckRes")) $("socCheckRes").innerHTML = Object.values(r.results).map(x => esc(tr(x.text))).join("<br>"); }), 300);
  };
  $("ppGo").onclick = async () => {
    $("ppErr").textContent = "";
    const body = { product_id: $("pp-prod").value, text: $("pp-text").value.trim(), targets: [...document.querySelectorAll("[name=pp-tg]:checked")].map(x => x.value) };
    $("ppGo").disabled = true;
    const r = await guard(() => api.socialPost(body), $("ppErr"));
    $("ppGo").disabled = false;
    if (r) { toast(t("📣 已排队，几秒钟内发出")); $("pp-text").value = ""; setTimeout(loadSocial, 2500); }
  };
  $("ppCap").onclick = async () => {
    $("ppErr").textContent = "";
    const pid = $("pp-prod").value;
    if (!pid) { $("ppErr").textContent = t("⚠️ 先选一个商品"); return; }
    const r = await guard(() => api.socialCaption(pid), $("ppErr")); if (!r) return;
    $("ppCapOut").innerHTML = `<textarea class="in" rows="7" readonly>${esc(r.text)}</textarea>
      <div class="actions"><button type="button" class="btn" data-copy="${esc(r.text)}">${t("📋 复制文案")}</button>
      ${r.image ? `<a class="btn" href="${esc(r.image)}" target="_blank" rel="noopener" download>${t("🖼️ 打开图片")}</a>` : ""}
      <button type="button" class="btn" data-copy="${esc(r.link)}">${t("🔗 复制商品链接")}</button></div>`;
  };
  $("socialView").onclick = e => { const b = e.target.closest("[data-copy]"); if (b) socialCopy(b.dataset.copy); };
}

function postRow(p) {
  const st = POST_ST[p.status] || ["", p.status];
  const what = p.product_name ? `🪑 ${esc(p.product_name)}` : esc((p.text || "").slice(0, 60));
  const res = p.targets.map(k => {
    const r = p.results[k], lab = (SOC_TARGETS.find(x => x[0] === k) || [k, k])[1];
    if (!r) return `<span class="tag">${lab} ⏳</span>`;
    return r.ok ? (r.url ? `<a class="tag ok" href="${esc(r.url)}" target="_blank" rel="noopener">${lab} ✅</a>` : `<span class="tag ok">${lab} ✅</span>`)
      : `<span class="tag tag-warn" title="${esc(tr(r.error || ""))}">${lab} ❌ ${esc(tr(r.error || "").slice(0, 60))}</span>`;
  }).join(" ");
  return `<div class="prow"><div><b>${st[0]} ${t(st[1])}</b> <span class="sub">${fmtTime(p.created_at)}${p.kind === "auto" ? t(" · 上新自动") : ""}</span></div>
    <div>${what}</div><div class="actions">${res}</div></div>`;
}
