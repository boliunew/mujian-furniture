// 4×6 in handling-unit label (one per pallet or box). Used by print.html and the
// pallet preview in the admin, so what you preview is exactly what prints.
(function () {
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SVC = { curbside: "CURBSIDE", threshold: "INSIDE DELIVERY", white_glove: "WHITE GLOVE · ASSEMBLY" };

  function addr(o) {
    return [o.ship_name, o.ship_line1, o.ship_line2, `${o.ship_city || ""}, ${o.ship_state || ""} ${o.ship_zip || ""}`.trim()]
      .filter(x => x && x !== ",");
  }

  function routing(o) {
    if (o.fulfillment !== "freight") return o.tracking ? `${esc(o.carrier || "")} ${esc(o.tracking)}` : "PARCEL";
    const day = o.appt_date ? new Date(o.appt_date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "numeric", day: "numeric" }).toUpperCase() : "APPT TBD";
    if (o.delivery_type === "local") return `${esc((o.truck || "TRUCK ?").toUpperCase())}${o.stop_no ? " · STOP " + o.stop_no : ""} · ${day}`;
    return `${esc((o.freight_carrier || "LTL").toUpperCase())}${o.pro ? " · PRO " + esc(o.pro) : ""} · ${day}`;
  }

  window.unitLabelHTML = function (o, u, store) {
    const flags = [];
    if (o.fulfillment === "freight") {
      if (SVC[o.service]) flags.push(SVC[o.service]);
      if (o.liftgate) flags.push("LIFTGATE");
      if (o.home_type === "apartment" && o.floor > 1) flags.push(`FLOOR ${o.floor}${o.elevator ? " · ELEV" : " · STAIRS"}`);
    }
    const notes = [o.access_notes, ...(o.print_notes || [])].filter(Boolean);
    const maxItems = notes.length ? 3 : 4;
    const shown = u.items.slice(0, maxItems), more = u.items.length - shown.length;
    return `<section class="page ulabel">
      <div class="u-top"><div class="u-from"><b>${esc(store.name)}</b><br>${store.address.map(esc).join("<br>")}</div>
        <div class="u-kind"><span>${u.kind === "pallet" ? "PALLET" : "BOX"}</span><b>${u.n}<small>/${u.of}</small></b></div></div>
      <div class="u-toh">SHIP TO${o.phone ? `<span>☎ ${esc(o.phone)}</span>` : ""}</div>
      <div class="u-to">${addr(o).map(esc).join("<br>")}</div>
      ${flags.length ? `<div class="u-flags">${flags.map(f => `<span>${esc(f)}</span>`).join("")}</div>` : ""}
      <div class="u-route">${routing(o)}</div>
      <div class="u-bc"><svg class="barcode" data-code="${esc(u.id)}" data-h="54"></svg></div>
      ${notes.length ? `<div class="u-notes"><b>NOTES</b>${notes.slice(0, 4).map(n => `<div>• ${esc(n)}</div>`).join("")}${notes.length > 4 ? `<div>+ ${notes.length - 4} more</div>` : ""}</div>` : ""}
      <div class="u-items">${shown.map(i => `<div class="u-item"><div><b>${i.qty} ×</b> ${esc(i.name)}</div>
          ${i.sku ? `<svg class="barcode" data-code="${esc(i.sku)}" data-h="20"></svg>` : ""}</div>`).join("")}
        ${more > 0 ? `<div class="u-more">+ ${more} more item${more > 1 ? "s" : ""} — see packing slip</div>` : ""}</div>
      <div class="u-foot"><span>${esc(o.order_no)}</span><span>${u.weight_lb ? u.weight_lb + " LB" : ""}</span>
        <span>${new Date(o.paid_at || o.created_at || Date.now()).toLocaleDateString("en-US")}</span></div>
    </section>`;
  };

  window.drawBarcodes = function (root) {
    (root || document).querySelectorAll("svg.barcode").forEach(svg => {
      if (svg.dataset.done) return;
      svg.dataset.done = "1";
      const h = +svg.dataset.h || 60, small = h < 30;
      if (window.JsBarcode) {
        try {
          JsBarcode(svg, svg.dataset.code, { format: "CODE128", height: h, width: small ? 1.1 : h < 50 ? 1.4 : 2,
            fontSize: small ? 9 : h < 50 ? 11 : 14, margin: 0, displayValue: true, textMargin: 1 });
          return;
        } catch (e) {}
      }
      svg.outerHTML = `<div class="bc-fallback" style="font:700 ${small ? 9 : h < 50 ? 12 : 18}pt 'JetBrains Mono',monospace;letter-spacing:.06em">${esc(svg.dataset.code)}</div>`;
    });
  };

  window.UNIT_LABEL_CSS = `
.ulabel{width:4in;height:6in;padding:.14in;display:flex;flex-direction:column;gap:.06in;font:9pt "Work Sans",system-ui,sans-serif;color:#111;background:#fff;overflow:hidden}
.ulabel .u-top{display:flex;justify-content:space-between;gap:8px;border-bottom:2px solid #000;padding-bottom:.05in}
.ulabel .u-from{font-size:7.5pt;line-height:1.25}
.ulabel .u-kind{display:grid;text-align:center;border:3px solid #000;padding:2px 8px;line-height:1}
.ulabel .u-kind span{font-size:7pt;font-weight:700;letter-spacing:.1em}
.ulabel .u-kind b{font:700 26pt "JetBrains Mono",monospace}
.ulabel .u-kind small{font-size:13pt}
.ulabel .u-toh{font-size:7.5pt;font-weight:700;letter-spacing:.12em;display:flex;justify-content:space-between}
.ulabel .u-toh span{letter-spacing:0;font-weight:500}
.ulabel .u-to{font-size:14pt;line-height:1.2;font-weight:700;text-transform:uppercase}
.ulabel .u-flags{display:flex;flex-wrap:wrap;gap:4px}
.ulabel .u-flags span{background:#000;color:#fff;font-weight:700;font-size:8pt;padding:2px 6px;letter-spacing:.04em}
.ulabel .u-route{border:2px solid #000;text-align:center;font:700 11pt "JetBrains Mono",monospace;padding:3px}
.ulabel .u-bc{text-align:center}
.ulabel .u-bc svg{width:100%;height:auto;max-height:.9in}
.ulabel .u-items{display:grid;gap:3px;align-content:start;border-top:1px solid #000;padding-top:4px;flex:1;min-height:0;overflow:hidden}
.ulabel .u-item{display:flex;justify-content:space-between;align-items:center;gap:6px;font-size:8.5pt}
.ulabel .u-item div{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ulabel .u-item svg{height:.32in;width:auto;flex:0 0 auto}
.ulabel .u-more{font-size:8pt;font-style:italic}
.ulabel .u-notes{border:2px dashed #000;padding:3px 6px;font-size:8.5pt;line-height:1.25;display:grid;gap:1px;max-height:1.05in;overflow:hidden}
.ulabel .u-notes b{font-size:7pt;letter-spacing:.12em}
.ulabel .u-notes div{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ulabel .u-foot{display:flex;justify-content:space-between;font:8pt "JetBrains Mono",monospace;border-top:2px solid #000;padding-top:3px}`;
})();
