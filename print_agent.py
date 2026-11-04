"""
Mujian print agent — prints pallet/box labels for new orders automatically, no clicks.

Runs on the warehouse PC next to a Zebra (or other ZPL) label printer. Every few seconds
it asks the store for paid orders whose labels haven't been printed, prints one 4x6
label per pallet or box, and marks them printed (the order timeline shows it).

    python print_agent.py --url https://your-store.com --password YOUR_ADMIN_PASSWORD --printer 192.168.1.50
    python print_agent.py --printer "windows:ZDesigner GK420d"      # USB printer installed in Windows
    python print_agent.py --printer dir:labels --once                 # save .zpl files instead of printing
    python print_agent.py --test --printer 192.168.1.50               # print one sample label
    python print_agent.py --test --printer dir:labels --preview       # also render a PNG via labelary.com

Printer must speak ZPL (Zebra ZD/GK/GX series, most industrial printers). Rollo and DYMO
printers don't; for those use the admin's "自动打印" switch with Chrome --kiosk-printing.
No extra packages needed; "windows:" printing needs `pip install pywin32`.
"""
import argparse
import http.cookiejar
import json
import os
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime

DPI_DOTS = {203: 8, 300: 12}  # dots per mm
SVC = {"curbside": "CURBSIDE", "threshold": "INSIDE DELIVERY", "white_glove": "WHITE GLOVE + ASSEMBLY"}


# ------------------------------------------------------------------ ZPL
def zt(s):
    """ZPL-safe ASCII text: fold accents, drop ^ and ~ which are ZPL control characters."""
    s = str(s or "").replace("×", "x").replace("Ø", "D").replace("–", "-").replace("—", "-").replace("·", "-")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.replace("^", " ").replace("~", " ")


def route(o):
    if o.get("fulfillment") != "freight":
        return f"{o.get('carrier') or ''} {o.get('tracking') or ''}".strip() or "PARCEL"
    day = "APPT TBD"
    if o.get("appt_date"):
        day = date.fromisoformat(o["appt_date"]).strftime("%a %m/%d").upper()
    if o.get("delivery_type") == "local":
        return f"{(o.get('truck') or 'TRUCK ?').upper()}{' - STOP ' + str(o['stop_no']) if o.get('stop_no') else ''} - {day}"
    return f"{(o.get('freight_carrier') or 'LTL').upper()}{' - PRO ' + o['pro'] if o.get('pro') else ''} - {day}"


def unit_zpl(o, u, store, dpi=203, font=None):
    k = DPI_DOTS.get(dpi, 8) / 8  # layout is drawn for 203 dpi, scaled for 300
    d = lambda v: int(round(v * k))
    addr = [o.get("ship_name"), o.get("ship_line1"), o.get("ship_line2"),
            f"{o.get('ship_city') or ''}, {o.get('ship_state') or ''} {o.get('ship_zip') or ''}".strip()]
    addr = [zt(a).upper() for a in addr if a and a.strip(" ,")]
    flags = []
    if o.get("fulfillment") == "freight":
        if SVC.get(o.get("service")):
            flags.append(SVC[o["service"]])
        if o.get("liftgate"):
            flags.append("LIFTGATE")
        if o.get("home_type") == "apartment" and (o.get("floor") or 1) > 1:
            flags.append(f"FLOOR {o['floor']} {'ELEV' if o.get('elevator') else 'STAIRS'}")
    kind = "PALLET" if u["kind"] == "pallet" else "BOX"
    z = ["^XA", "^CI28", f"^PW{d(812)}", f"^LL{d(1218)}", "^LH0,0"]
    from_txt = "\\&".join(zt(x) for x in [store.get("name")] + list(store.get("address") or []))
    z += [f"^FO{d(30)},{d(28)}^A0N,{d(24)},{d(24)}^FB{d(560)},4,2,L^FD{from_txt}^FS",
          f"^FO{d(620)},{d(24)}^GB{d(170)},{d(122)},{d(5)}^FS",
          f"^FO{d(620)},{d(36)}^A0N,{d(24)},{d(24)}^FB{d(170)},1,0,C^FD{kind}^FS",
          f"^FO{d(620)},{d(66)}^A0N,{d(72)},{d(66)}^FB{d(170)},1,0,C^FD{u['n']}/{u['of']}^FS",
          f"^FO{d(30)},{d(158)}^GB{d(752)},{d(4)},{d(4)}^FS",
          f"^FO{d(30)},{d(174)}^A0N,{d(24)},{d(24)}^FDSHIP TO^FS"]
    if o.get("phone"):
        z.append(f"^FO{d(400)},{d(174)}^A0N,{d(24)},{d(24)}^FB{d(382)},1,0,R^FDTEL {zt(o['phone'])}^FS")
    addr_txt = "\\&".join(addr)
    z.append(f"^FO{d(30)},{d(208)}^A0N,{d(46)},{d(42)}^FB{d(752)},5,6,L^FD{addr_txt}^FS")
    y = 480
    if flags:
        z += [f"^FO{d(30)},{d(y)}^GB{d(752)},{d(44)},{d(44)}^FS",
              f"^FO{d(30)},{d(y + 8)}^A0N,{d(30)},{d(28)}^FR^FB{d(752)},1,0,C^FD{zt(' - '.join(flags))}^FS"]
    y = 540
    z += [f"^FO{d(30)},{d(y)}^GB{d(752)},{d(58)},{d(3)}^FS",
          f"^FO{d(30)},{d(y + 13)}^A0N,{d(34)},{d(30)}^FB{d(752)},1,0,C^FD{zt(route(o))}^FS",
          f"^FO{d(70)},{d(622)}^BY{d(3)},3,{d(110)}^BCN,{d(110)},Y,N,N^FD{zt(u['id'])}^FS"]
    notes = [n for n in [o.get("access_notes")] + list(o.get("print_notes") or []) if n]
    y = 790
    if notes:
        # Chinese/accented notes need a Unicode font stored on the printer (--font E:SIMSUN.TTF);
        # without one they are folded to plain ASCII.
        fcmd = (lambda h, w: f"^A@N,{d(h)},{d(w)},{font}") if font else (lambda h, w: f"^A0N,{d(h)},{d(w)}")
        txt = (lambda x: str(x).replace("^", " ").replace("~", " ")) if font else zt
        lines = ["NOTES"] + [f"* {txt(n)[:60]}" for n in notes[:3]]
        z.append(f"^FO{d(30)},{d(y)}^GB{d(752)},{d(26 * len(lines) + 14)},{d(2)}^FS")
        for i, ln in enumerate(lines):
            z.append(f"^FO{d(42)},{d(y + 8 + i * 26)}{fcmd(22, 20)}^FB{d(728)},1,0,L^FD{ln}^FS")
        y += 26 * len(lines) + 26
    items = u["items"][:4 if not notes else 3]
    for it in items:
        z.append(f"^FO{d(30)},{d(y)}^A0N,{d(28)},{d(26)}^FB{d(470)},2,2,L^FD{it['qty']} x {zt(it['name'])}^FS")
        if it.get("sku"):
            modules = (len(zt(it["sku"])) + 2) * 11 + 13           # Code 128 width in modules
            by = 2 if modules * 2 <= 270 else 1                    # keep it inside the right edge
            z.append(f"^FO{d(520)},{d(y)}^BY{d(by)},2,{d(44)}^BCN,{d(44)},Y,N,N^FD{zt(it['sku'])}^FS")
        y += 82
    if len(u["items"]) > 4:
        z.append(f"^FO{d(30)},{d(y)}^A0N,{d(24)},{d(24)}^FD+ {len(u['items']) - 4} more items - see packing slip^FS")
    paid = (o.get("paid_at") or o.get("created_at") or datetime.now().isoformat())[:10]
    z += [f"^FO{d(30)},{d(1140)}^GB{d(752)},{d(3)},{d(3)}^FS",
          f"^FO{d(30)},{d(1158)}^A0N,{d(26)},{d(24)}^FD{zt(o['order_no'])}^FS",
          f"^FO{d(330)},{d(1158)}^A0N,{d(26)},{d(24)}^FB{d(160)},1,0,C^FD{str(u.get('weight_lb') or '') + ' LB' if u.get('weight_lb') else ''}^FS",
          f"^FO{d(560)},{d(1158)}^A0N,{d(26)},{d(24)}^FB{d(222)},1,0,R^FD{paid}^FS",
          "^PQ1", "^XZ"]
    return "\n".join(z) + "\n"


# ------------------------------------------------------------------ printers
def send(printer, zpl, name="label"):
    if printer.startswith("dir:"):
        folder = printer[4:] or "labels"
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{name}.zpl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(zpl)
        return path
    if printer.startswith("windows:"):
        try:
            import win32print  # pip install pywin32
        except ImportError:
            sys.exit("windows: printing needs pywin32 →  pip install pywin32")
        h = win32print.OpenPrinter(printer[8:])
        try:
            win32print.StartDocPrinter(h, 1, (name, None, "RAW"))
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, zpl.encode("utf-8"))
            win32print.EndPagePrinter(h)
            win32print.EndDocPrinter(h)
        finally:
            win32print.ClosePrinter(h)
        return printer
    host, _, port = printer.partition(":")
    with socket.create_connection((host, int(port or 9100)), timeout=10) as s:
        s.sendall(zpl.encode("utf-8"))
    return f"{host}:{port or 9100}"


def preview_png(zpl, path, dpi=203):
    """Render ZPL to PNG with the free labelary.com service, to check a layout without a printer."""
    url = f"http://api.labelary.com/v1/printers/{DPI_DOTS.get(dpi, 8)}dpmm/labels/4x6/0/"
    req = urllib.request.Request(url, data=zpl.encode("utf-8"), headers={"Accept": "image/png"})
    with urllib.request.urlopen(req, timeout=20) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


# ------------------------------------------------------------------ store API
class Store:
    def __init__(self, url, password):
        self.url = url.rstrip("/")
        self.password = password
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path, body=None, retry=True):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"} if data else {})
        try:
            with self.opener.open(req, timeout=20) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry:
                self.login()
                return self.call(path, body, retry=False)
            raise RuntimeError(f"{path}: HTTP {e.code} {e.read()[:200]!r}")

    def login(self):
        self.call("/api/admin/login", {"password": self.password}, retry=False)


SAMPLE = {
    "order_no": "MJ261104-001", "fulfillment": "freight", "service": "white_glove", "liftgate": True, "home_type": "apartment",
    "floor": 4, "elevator": True, "delivery_type": "local", "truck": "Truck 2", "stop_no": 3, "appt_date": date.today().isoformat(),
    "ship_name": "Maya Patel", "ship_line1": "2420 Telegraph Ave", "ship_line2": "Apt 4B", "ship_city": "Berkeley",
    "ship_state": "CA", "ship_zip": "94704", "phone": "(510) 555-0187",
}
SAMPLE_UNIT = {"id": "MJ261104-001-01", "n": 1, "of": 2, "kind": "pallet", "weight_lb": 205,
               "items": [{"name": "Haven 3-Seat Sofa", "sku": "MJ-S1", "qty": 1}]}


def main():
    ap = argparse.ArgumentParser(description="Print pallet/box labels for new Mujian orders on a ZPL printer.")
    ap.add_argument("--url", default=os.getenv("STORE_URL", "http://localhost:4242"))
    ap.add_argument("--password", default=os.getenv("ADMIN_PASSWORD", ""))
    ap.add_argument("--printer", default=os.getenv("LABEL_PRINTER", "dir:labels"),
                    help='IP[:port] of a network printer, "windows:Printer Name", or dir:FOLDER to save .zpl files')
    ap.add_argument("--dpi", type=int, default=int(os.getenv("LABEL_DPI", 203)), choices=[203, 300])
    ap.add_argument("--interval", type=int, default=10, help="seconds between checks")
    ap.add_argument("--once", action="store_true", help="print what's waiting, then exit")
    ap.add_argument("--test", action="store_true", help="print one sample label and exit")
    ap.add_argument("--preview", action="store_true", help="also save a PNG of each label (uses labelary.com)")
    ap.add_argument("--font", default=os.getenv("LABEL_FONT", ""),
                    help="Unicode font on the printer for notes, e.g. E:SIMSUN.TTF (so Chinese/Spanish notes print as written)")
    a = ap.parse_args()

    if a.test:
        z = unit_zpl({**SAMPLE, "print_notes": ["Glass top - two people to carry", "Call before arriving"]}, SAMPLE_UNIT,
                     {"name": "Mujian Furniture", "address": ["1200 Industrial Way", "Oakland, CA 94607"]}, a.dpi, a.font or None)
        print("🖨️ ", send(a.printer, z, "sample"))
        if a.preview:
            print("🖼️ ", preview_png(z, "sample.png", a.dpi))
        return
    if not a.password:
        sys.exit("Set --password (or ADMIN_PASSWORD) to the store's admin password.")
    store = Store(a.url, a.password)
    store.login()
    print(f"🖨️  Watching {a.url} for new orders → {a.printer}  (Ctrl+C to stop)")
    while True:
        try:
            q = store.call("/api/admin/print-queue")
            for o in q.get("orders", []):
                for u in o.get("units") or []:
                    z = unit_zpl(o, u, q["store"], a.dpi, a.font or None)
                    where = send(a.printer, z, u["id"])
                    if a.preview:
                        preview_png(z, os.path.join(os.path.dirname(where) if a.printer.startswith("dir:") else ".", u["id"] + ".png"), a.dpi)
                store.call("/api/admin/print-queue/ack", {"ids": [o["id"]], "by": f"打印程序 {socket.gethostname()}"})
                print(f"{datetime.now():%H:%M:%S}  ✅ {o['order_no']}: {len(o.get('units') or [])} label(s)")
        except Exception as e:  # keep running through network blips and printer hiccups
            print(f"{datetime.now():%H:%M:%S}  ⚠️  {e}")
        if a.once:
            break
        time.sleep(max(3, a.interval))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
