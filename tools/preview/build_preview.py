"""Assemble the click-to-try preview: the real pages with api.js swapped for mock-api.js.

    python tools/preview/build_preview.py OUT_DIR

Output: OUT_DIR/home.html and index.html (launcher) plus shop.html, admin.html, success.html, print.html, schedule.html and assets.
"""
import re
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
for f in ["style.css", "admin.css", "art.js", "spin.js", "admin-freight.js", "admin-inventory.js"]:
    shutil.copy(PUB / f, out / f)
shutil.copy(Path(__file__).with_name("mock-api.js"), out / "api.js")
shutil.copytree(PUB / "demo-spin", out / "demo-spin")
shutil.copytree(PUB / "vendor", out / "vendor")
home = Path(__file__).with_name("home.html").read_text(encoding="utf-8")
(out / "home.html").write_text(home, encoding="utf-8")  # for the claude.ai preview (wrapped by the host)
# Standalone index.html for GitHub Pages: python tools/preview/build_preview.py docs
title = re.search(r"<title>.*?</title>", home).group(0)
body = home.replace(title, "").replace('<meta name="claude:viewport-fit-sides" content="cover">', "").strip()
(out / "index.html").write_text(
    '<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    f"{title}\n<style>html,body{{margin:0}}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n", encoding="utf-8")
(out / ".nojekyll").touch()
print("preview written to", out)
