// Wrap Chinese UI strings in JS (or inline <script> of HTML) with t("...").
// usage: node wrap.js [--dry] file...   → prints JSON of keys found; rewrites files unless --dry
const fs = require("fs");
const acorn = require(__dirname + "/node_modules/acorn");
const CJK0 = /[㐀-鿿（），：；、。！？「」]/;
const EN = process.argv.includes("--en");
const HAN = EN ? /[A-Za-z]{2,}/ : /[㐀-鿿]/;
const CJK = process.argv.includes("--en") ? /[A-Za-z]{2,}/ : CJK0;
const dry = process.argv.includes("--dry");
const files = process.argv.slice(2).filter(a => !a.startsWith("--"));
const keys = new Map();
const add = (k, f) => { if (!keys.has(k)) keys.set(k, f); };

function walk(node, parent, fn) {
  if (!node || typeof node.type !== "string") return;
  fn(node, parent);
  for (const k in node) {
    if (k === "loc" || k === "start" || k === "end") continue;
    const v = node[k];
    if (Array.isArray(v)) v.forEach(c => c && typeof c.type === "string" && walk(c, node, fn));
    else if (v && typeof v.type === "string") walk(v, node, fn);
  }
}

function cook(raw) {
  try { return eval("`" + raw.replace(/`/g, "\\`") + "`"); } catch (e) { return raw; }
}

// Turn the raw text of one template quasi into raw text with ${t("...")} around Chinese runs.
// state carries "inside a tag / inside a quoted attribute" across quasis.
function transformQuasi(raw, state, file) {
  let out = "", run = "", i = 0;
  const flushRun = () => {
    if (!run) return;
    if (HAN.test(run)) {
      const m = run.match(/^(\s*)([\s\S]*?)(\s*)$/);
      const core = cook(m[2]);
      add(core, file);
      out += m[1] + "${t(" + JSON.stringify(core) + ")}" + m[3];
    } else out += run;
    run = "";
  };
  let attr = "";
  const flushAttr = () => {
    if (!attr) return;
    if (state.uiAttr && HAN.test(attr)) { const core = cook(attr); add(core, file); out += "${t(" + JSON.stringify(core) + ")}"; }
    else out += attr;
    attr = "";
  };
  while (i < raw.length) {
    const ch = raw[i];
    if (!state.inTag) {
      if (ch === "<" && /[a-zA-Z\/!]/.test(raw[i + 1] || "")) { flushRun(); state.inTag = true; out += ch; i++; continue; }
      run += ch; i++; continue;
    }
    if (state.quote) {
      if (ch === state.quote) { flushAttr(); state.quote = null; out += ch; i++; continue; }
      attr += ch; i++; continue;
    }
    if (ch === '"' || ch === "'") {
      state.quote = ch; out += ch; i++;
      const before = out.slice(-40);
      state.uiAttr = !EN || /(placeholder|aria-label|title|alt)=["']$/.test(before);
      continue;
    }
    if (ch === ">") { state.inTag = false; out += ch; i++; continue; }
    out += ch; i++;
  }
  flushRun(); flushAttr();
  return out;
}

function rewriteJS(src, file) {
  const ast = acorn.parse(src, { ecmaVersion: "latest", sourceType: "script", allowReturnOutsideFunction: true, allowAwaitOutsideFunction: true });
  const edits = [];
  walk(ast, null, (n, parent) => {
    if (n.type === "CallExpression" && n.callee.type === "Identifier" && /^(t|tr|noT)$/.test(n.callee.name)) { n.__skip = true; }
    if (n.type === "Literal" && typeof n.value === "string" && HAN.test(n.value) &&
        (!EN || (/[ \u2600-\u27bf\ud83c-\ud83e]/.test(n.value) && /^[^<{(#.\/]/.test(n.value) && !/^(en-US|USD|POST|GET|Content-Type|application\/json)/.test(n.value) && !/[=;{}]/.test(n.value)))) {
      if (parent && parent.type === "Property" && parent.key === n) return;
      if (parent && parent.__skip) return;
      add(n.value, file);
      edits.push([n.start, n.end, "t(" + JSON.stringify(n.value) + ")"]);
    }
    if (n.type === "TemplateLiteral" && n.quasis.some(q => CJK.test(q.value.raw))) {
      if (parent && parent.__skip) return;
      const state = { inTag: false, quote: null };
      n.quasis.forEach(q => {
        const nr = transformQuasi(q.value.raw, state, file);
        if (nr !== q.value.raw) edits.push([q.start, q.end, nr]);
      });
    }
  });
  // drop edits nested in other edits (keep outermost)
  edits.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const kept = [];
  for (const e of edits) if (!kept.length || e[0] >= kept[kept.length - 1][1]) kept.push(e);
  let out = src;
  for (const [s, e, r] of kept.reverse()) out = out.slice(0, s) + r + out.slice(e);
  return out;
}

for (const f of files) {
  let src = fs.readFileSync(f, "utf8"), out;
  if (f.endsWith(".html")) {
    out = src.replace(/<script>([\s\S]*?)<\/script>/g, (m, code) => "<script>" + rewriteJS(code, f) + "</script>");
    // static HTML text and attributes: collect keys only (translated at runtime)
    const stat = src.replace(/<script[\s\S]*?<\/script>/g, "").replace(/<style[\s\S]*?<\/style>/g, "");
    for (const m of stat.matchAll(/>([^<>]+)</g)) { const s = m[1].trim(); if (HAN.test(s)) add(s, f + "#static"); }
    for (const m of stat.matchAll(/(?:placeholder|title|aria-label)="([^"]+)"/g)) if (HAN.test(m[1])) add(m[1], f + "#static");
  } else out = rewriteJS(src, f);
  if (!dry && out !== src) fs.writeFileSync(f, out);
}
console.log(JSON.stringify([...keys.keys()], null, 0));
