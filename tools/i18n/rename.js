// Rename every *local* binding called `t` to `tb`, leaving calls to the global t() alone.
const fs = require("fs");
const parser = require(__dirname + "/node_modules/@babel/parser");
const traverse = require(__dirname + "/node_modules/@babel/traverse").default;
function fix(code) {
  const ast = parser.parse(code, { sourceType: "script", errorRecovery: false, allowReturnOutsideFunction: true, allowAwaitOutsideFunction: true });
  const pos = new Set();
  traverse(ast, {
    Scope(path) {
      const b = path.scope.getOwnBinding("t");
      if (!b || b.__done) return; b.__done = true;
      pos.add(b.identifier.start);
      b.referencePaths.forEach(r => pos.add(r.node.start));
      b.constantViolations.forEach(v => { const id = v.node.left || v.node.id; if (id && id.type === "Identifier") pos.add(id.start); });
    },
  });
  let out = code;
  [...pos].sort((a, b) => b - a).forEach(p => { if (out.slice(p, p + 1) === "t") out = out.slice(0, p) + "tb" + out.slice(p + 1); });
  return [out, pos.size];
}
for (const f of process.argv.slice(2)) {
  const src = fs.readFileSync(f, "utf8");
  let n = 0, out;
  if (f.endsWith(".html")) out = src.replace(/<script>([\s\S]*?)<\/script>/g, (m, c) => { const [o, k] = fix(c); n += k; return "<script>" + o + "</script>"; });
  else { const [o, k] = fix(src); out = o; n = k; }
  fs.writeFileSync(f, out); console.log(f, n);
}
