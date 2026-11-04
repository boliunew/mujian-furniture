"""Assemble the click-to-try preview: the real pages with api.js swapped for mock-api.js.

    python tools/preview/build_preview.py OUT_DIR

Output: OUT_DIR/home.html (launcher) plus shop.html, admin.html, success.html, print.html, schedule.html and assets.
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUB = ROOT / "public"
out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "preview-build")
if out.exists():
    shutil.rmtree(out)
out.mkdir(parents=True)

PAGES = {"index.html": "shop.html", "admin.html": "admin.html", "success.html": "success.html", "print.html": "print.html", "schedule.html": "schedule.html"}
for src, dst in PAGES.items():
    html = (PUB / src).read_text(encoding="utf-8")
    html = html.replace('href="index.html"', 'href="shop.html"')
    (out / dst).write_text(html, encoding="utf-8")
for f in ["style.css", "admin.css", "art.js", "spin.js", "admin-freight.js"]:
    shutil.copy(PUB / f, out / f)
shutil.copy(Path(__file__).with_name("mock-api.js"), out / "api.js")
shutil.copytree(PUB / "demo-spin", out / "demo-spin")
shutil.copy(Path(__file__).with_name("home.html"), out / "home.html")
print("preview written to", out)
