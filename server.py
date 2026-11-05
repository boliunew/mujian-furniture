"""
Mujian Furniture — storefront + admin backend (Flask, SQLite, Stripe Checkout).

Run:
    pip install -r requirements.txt
    cp .env.example .env        # set ADMIN_PASSWORD (Stripe TEST keys optional)
    python server.py            # store: http://localhost:4242   admin: http://localhost:4242/admin.html
    python server.py reminders  # run reminders + backup now (the site also does this by itself every minute)

Without Stripe keys the store runs in demo checkout mode: "Checkout" creates a
paid test order immediately, so you can try the whole admin flow locally.
"""
import hmac
import json
import os
import random
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import stripe
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request, send_from_directory, session
from PIL import Image, ImageDraw, ImageFont, ImageOps
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()
import freight as fr  # noqa: E402  (reads STORE_TZ from .env)
import emails as em  # noqa: E402
import mailer  # noqa: E402
import backup  # noqa: E402
import tasks  # noqa: E402
import inventory as inv  # noqa: E402
import social  # noqa: E402

BASE = Path(__file__).resolve().parent

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
DOMAIN = os.getenv("DOMAIN", "http://localhost:4242").rstrip("/")
DB_PATH = os.getenv("DB_PATH", str(BASE / "shop.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE / "uploads")))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
LOCAL = DOMAIN.startswith(("http://localhost", "http://127.0.0.1"))
# Demo checkout (no card, instant "paid" test order) only when asked for, or when running
# on your own computer without Stripe keys. A live site without keys refuses checkout
# instead of giving furniture away.
DEMO_CHECKOUT = not stripe.api_key and (os.getenv("DEMO_MODE") == "1" or LOCAL)
PAYMENTS_READY = bool(stripe.api_key) or DEMO_CHECKOUT
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", 10))  # store-local hour to email tomorrow's deliveries
BACKUP_DIR = os.getenv("BACKUP_DIR") or str(Path(DB_PATH).resolve().parent / "backups")
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", 14))
WEAK_PASSWORDS = {"change-me", "password", "admin", "demo", "12345678", "123456789", "qwerty123", "mujian"}

STORE = {
    "name": os.getenv("STORE_NAME", "Mujian Furniture"),
    "address": os.getenv("STORE_ADDRESS", "1200 Industrial Way|Oakland, CA 94607").split("|"),
    "email": os.getenv("STORE_EMAIL", "orders@example.com"),
    "phone": os.getenv("STORE_PHONE", "(510) 555-0100"),
}

MAX_QTY_PER_ITEM = 10
STATUSES = ["pending", "paid", "shipped", "delivered", "canceled"]
CARRIERS = {
    "USPS": "https://tools.usps.com/go/TrackConfirmAction?tLabels={}",
    "UPS": "https://www.ups.com/track?tracknum={}",
    "FedEx": "https://www.fedex.com/fedextrack/?trknbr={}",
    "Freight": "",
}

# id, name, cat, art, price, dim, material, lead, stock, ship_mode, weight lb, box L×W×H in, freight class
SEED_PRODUCTS = [
    ("s1", "Haven 3-Seat Sofa", "Living", "sofa", 189900, '83" W × 36" D × 31" H', "White oak frame · linen blend", "2–3 weeks", 6, "freight", 165, (86, 39, 34), "250"),
    ("c1", "Sit Walnut Dining Chair", "Dining", "chair", 34900, '18" W × 20" D × 31" H', "Black walnut · vegetable-tanned leather", "1 week", 24, "parcel", 21, (24, 22, 34), ""),
    ("t1", "Gather 6-Seat Dining Table", "Dining", "table", 149900, '71" W × 35" D × 30" H', "Solid white oak · hardwax oil", "3–4 weeks", 4, "freight", 140, (76, 40, 12), "150"),
    ("b1", "Rest Low Platform Bed, Queen", "Bedroom", "bed", 179900, '64" W × 85" D × 35" H', "Cherry · mortise-and-tenon joints", "4 weeks", 3, "freight", 190, (88, 20, 14), "150"),
    ("sh1", "Keep 3-Tier Open Bookshelf", "Study", "shelf", 64900, '35" W × 13" D × 59" H', "White oak · adjustable shelves", "2 weeks", 10, "freight", 78, (62, 38, 8), "125"),
    ("l1", "Glow Brass Floor Lamp", "Lighting", "lamp", 39900, '16" W × 16" D × 62" H', "Brass shade · E26 socket", "1 week", 15, "parcel", 18, (20, 20, 46), ""),
    ("tv1", "Still 2-Door Media Console", "Living", "cabinet", 99900, '71" W × 17" D × 22" H', "Black walnut · push-to-open doors", "3 weeks", 5, "freight", 120, (76, 22, 28), "175"),
    ("t2", "Pause Round Side Table", "Living", "table", 22900, '20" Ø × 22" H', "White oak · solid legs", "1 week", 2, "parcel", 16, (24, 24, 8), ""),
]
DEMO_SPIN = {"tv1": ("/demo-spin/tv1", 24)}
SEED_I18N = {
    "s1": {"zh": {"name": "栖 · 三人布艺沙发", "mat": "白橡木框架 · 亚麻混纺"}, "es": {"name": "Sofá de 3 plazas Haven", "mat": "Estructura de roble blanco · mezcla de lino"}},
    "c1": {"zh": {"name": "坐 · 胡桃木餐椅", "mat": "北美黑胡桃 · 植鞣皮"}, "es": {"name": "Silla de comedor Sit de nogal", "mat": "Nogal negro · cuero curtido vegetal"}},
    "t1": {"zh": {"name": "聚 · 六人实木餐桌", "mat": "白橡木实木 · 木蜡油"}, "es": {"name": "Mesa de comedor Gather para 6", "mat": "Roble blanco macizo · aceite de cera dura"}},
    "b1": {"zh": {"name": "眠 · 低矮平台床（Queen）", "mat": "樱桃木 · 榫卯结构"}, "es": {"name": "Cama de plataforma baja Rest, Queen", "mat": "Cerezo · ensambles de caja y espiga"}},
    "sh1": {"zh": {"name": "藏 · 三层开放书架", "mat": "白橡木 · 可调层板"}, "es": {"name": "Librero abierto Keep de 3 niveles", "mat": "Roble blanco · estantes ajustables"}},
    "l1": {"zh": {"name": "明 · 黄铜落地灯", "mat": "黄铜灯罩 · E26 灯头"}, "es": {"name": "Lámpara de pie Glow de latón", "mat": "Pantalla de latón · casquillo E26"}},
    "tv1": {"zh": {"name": "静 · 双门电视柜", "mat": "黑胡桃 · 按压开门"}, "es": {"name": "Mueble de TV Still de 2 puertas", "mat": "Nogal negro · puertas push"}},
    "t2": {"zh": {"name": "憩 · 圆边几", "mat": "白橡木 · 实木腿"}, "es": {"name": "Mesa auxiliar redonda Pause", "mat": "Roble blanco · patas macizas"}},
}
LANGS = ("en", "zh", "es")
SHIP_LABELS = {  # what the customer sees on Stripe's page, per language
    "en": {"standard": "Standard delivery", "free_standard": "Free standard delivery", "curbside": "Freight · curbside (liftgate)",
           "threshold": "Freight · inside first door", "white_glove": "White-glove · room of choice + assembly", "free": " · free"},
    "zh": {"standard": "标准配送", "free_standard": "标准配送免运费", "curbside": "卡派 · 门口卸货（尾板）",
           "threshold": "卡派 · 送进门", "white_glove": "白手套 · 送到指定房间并安装", "free": " · 免运费"},
    "es": {"standard": "Envío estándar", "free_standard": "Envío estándar gratis", "curbside": "Carga · en la acera (plataforma)",
           "threshold": "Carga · dentro de la primera puerta", "white_glove": "Guante blanco · a la habitación + montaje", "free": " · gratis"},
}

SAMPLE_CUSTOMERS = [
    ("Emily Carter", "emily.carter@example.com", "(415) 555-0142", "742 Valencia St", "Apt 3", "San Francisco", "CA", "94110"),
    ("Maya Patel", "maya.patel@example.com", "(510) 555-0187", "2420 Telegraph Ave", "", "Berkeley", "CA", "94704"),
    ("James Nguyen", "james.nguyen@example.com", "(206) 555-0199", "1520 E Pike St", "", "Seattle", "WA", "98122"),
    ("Sofia Ramirez", "sofia.r@example.com", "(512) 555-0173", "908 W 34th St", "Unit B", "Austin", "TX", "78705"),
    ("Daniel Kim", "dkim@example.com", "(718) 555-0128", "215 Bedford Ave", "Fl 2", "Brooklyn", "NY", "11211"),
    ("Grace Liu", "grace.liu@example.com", "(626) 555-0116", "88 E Colorado Blvd", "", "Pasadena", "CA", "91105"),
]

PRODUCT_COLS = {"ship_mode": "TEXT DEFAULT 'freight'", "weight_lb": "INTEGER DEFAULT 0", "box_l": "REAL", "box_w": "REAL",
                "box_h": "REAL", "freight_class": "TEXT", "sku": "TEXT", "i18n": "TEXT"}
ORDER_COLS = {"fulfillment": "TEXT DEFAULT 'parcel'", "service": "TEXT", "delivery_type": "TEXT", "freight_status": "TEXT",
              "appt_date": "TEXT", "appt_window": "TEXT", "sched_token": "TEXT", "scheduled_at": "TEXT",
              "home_type": "TEXT", "floor": "INTEGER", "elevator": "INTEGER", "liftgate": "INTEGER", "access_notes": "TEXT",
              "truck": "TEXT", "stop_no": "INTEGER", "pro": "TEXT", "freight_carrier": "TEXT", "pickup_date": "TEXT",
              "pallets": "INTEGER", "weight_lb": "INTEGER", "freight_class": "TEXT", "reminded_for": "TEXT",
              "warehouse_id": "TEXT", "stock_out": "INTEGER DEFAULT 0", "labels_printed_at": "TEXT", "lang": "TEXT",
              "payment_intent": "TEXT", "refunded": "INTEGER DEFAULT 0"}


# ------------------------------------------------------------------ helpers
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def add_columns(c, table, cols):
    have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    for name, decl in cols.items():
        if name not in have:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db():
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")  # readers don't wait for writers; safe with several workers
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, cat TEXT, art TEXT, price INTEGER NOT NULL,
                dim TEXT, mat TEXT, lead TEXT, stock INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
                photo TEXT, spin_base TEXT, spin_count INTEGER DEFAULT 0, sort INTEGER DEFAULT 0, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, order_no TEXT UNIQUE, status TEXT NOT NULL, items_json TEXT NOT NULL,
                subtotal INTEGER, shipping INTEGER, total INTEGER, email TEXT, phone TEXT,
                ship_name TEXT, ship_line1 TEXT, ship_line2 TEXT, ship_city TEXT, ship_state TEXT, ship_zip TEXT,
                ship_method TEXT, carrier TEXT, tracking TEXT, note TEXT, test INTEGER DEFAULT 0,
                created_at TEXT, paid_at TEXT, shipped_at TEXT, delivered_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, at TEXT, kind TEXT, text TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        add_columns(c, "products", PRODUCT_COLS)
        add_columns(c, "orders", ORDER_COLS)
        inv.init_tables(c)
        mailer.init_tables(c)
        tasks.init_tables(c)
        social.init_tables(c)
        fresh = c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
        if fresh:
            for i, p in enumerate(SEED_PRODUCTS):
                base, count = DEMO_SPIN.get(p[0], (None, 0))
                c.execute(
                    """INSERT INTO products (id,name,cat,art,price,dim,mat,lead,stock,ship_mode,weight_lb,box_l,box_w,box_h,
                       freight_class,sort,spin_base,spin_count,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (*p[:11], *p[11], p[12], i, base, count, now()))
        else:  # older database: fill freight specs for the seed products
            for p in SEED_PRODUCTS:
                c.execute("""UPDATE products SET ship_mode=?, weight_lb=?, box_l=?, box_w=?, box_h=?, freight_class=?
                             WHERE id=? AND box_l IS NULL""", (p[9], p[10], *p[11], p[12], p[0]))
        c.execute("UPDATE products SET sku='MJ-'||UPPER(REPLACE(id,'-','')) WHERE sku IS NULL OR sku=''")
        for pid, tr_ in SEED_I18N.items():
            c.execute("UPDATE products SET i18n=? WHERE id=? AND (i18n IS NULL OR i18n='')", (json.dumps(tr_, ensure_ascii=False), pid))
        if not c.execute("SELECT COUNT(*) FROM stock").fetchone()[0]:
            inv.migrate_from_product_stock(c)
            if fresh:  # display pieces in the showroom, and shelf locations in the warehouse
                for pid, q in (("s1", 1), ("tv1", 1), ("l1", 2), ("c1", 4)):
                    inv.move(c, pid, "showroom", q, "init", "", "展厅样品")
                for pid, b in (("s1", "A-01"), ("t1", "A-02"), ("b1", "A-03"), ("tv1", "B-01"), ("sh1", "B-02"),
                               ("c1", "C-01"), ("t2", "C-02"), ("l1", "C-03")):
                    inv.set_bin(c, pid, "main", b)
                inv.sync_all(c)


def product_json(r):
    frames = [f"{r['spin_base']}/{i:02d}.jpg" for i in range(r["spin_count"] or 0)] if r["spin_base"] else []
    keys = ["id", "name", "cat", "art", "price", "dim", "mat", "lead", "stock", "photo", "sort", "updated_at", "sku",
            "ship_mode", "weight_lb", "box_l", "box_w", "box_h", "freight_class"]
    return {**{k: r[k] for k in keys}, "active": bool(r["active"]), "spin_frames": frames, "i18n": json.loads(r["i18n"] or "{}")}


def schedule_path(o):
    return f"schedule.html?o={o['id']}&t={o['sched_token']}" if o.get("sched_token") else ""


def order_json(r, settings=None, with_events=False):
    o = dict(r)
    o["items"] = json.loads(o.pop("items_json"))
    o["test"] = bool(o["test"])
    for k in ("elevator", "liftgate"):
        o[k] = None if o[k] is None else bool(o[k])
    tpl = CARRIERS.get(o.get("carrier") or "")
    o["tracking_url"] = tpl.format(o["tracking"]) if tpl and o.get("tracking") else ""
    o["schedule_url"] = schedule_path(o)
    o["service_label"] = fr.SERVICES.get(o.get("service") or "", {}).get("zh", "")
    if settings:
        o["appt_window_label"] = fr.window_label(settings, o.get("appt_window"))
    if with_events:
        with db() as c:
            o["events"] = [dict(e) for e in c.execute(
                "SELECT at, kind, text FROM events WHERE order_id=? ORDER BY id", (o["id"],))]
    return o


def log_event(c, order_id, kind, text):
    c.execute("INSERT INTO events (order_id, at, kind, text) VALUES (?,?,?,?)", (order_id, now(), kind, text))


def next_order_no(c):
    day = datetime.now(fr.TZ).strftime("%y%m%d")
    last = c.execute("SELECT MAX(CAST(substr(order_no, 10) AS INTEGER)) FROM orders WHERE order_no LIKE ?",
                     (f"MJ{day}-%",)).fetchone()[0]
    return f"MJ{day}-{(last or 0) + 1:03d}"


def store_hour():
    return datetime.now(fr.TZ).hour


def cents(n):
    return f"${n / 100:,.2f}"


def send_email(c, to, subject, body, kind="customer", order_id=None):
    """Queue an email (sent by the background worker). Returns a short status for the order history."""
    settings = mailer.load(c, STORE["email"])
    if kind == "customer" and not settings.get("customer_emails", True):
        return "顾客邮件已关闭，未发送"
    hold = kind == "customer" and order_id and bool(
        (c.execute("SELECT test FROM orders WHERE id=?", (order_id,)).fetchone() or [0])[0])
    return mailer.queue(c, settings, to, subject, body, kind, order_id, hold=hold)


def notify_staff(c, subject_key, lines, order_id=None, **kw):
    """Email the staff list (Settings → Email) in the staff language."""
    settings = mailer.load(c, STORE["email"])
    staff = settings.get("staff") or []
    if not staff:
        return
    L = mailer.STAFF.get(settings.get("staff_lang", "zh"), mailer.STAFF["zh"])
    subject = L[subject_key].format(**kw)
    body = "\n".join([subject, ""] + lines + ["", STORE["name"]])
    for to in staff:
        mailer.queue(c, settings, to, subject, body, "staff", order_id)


def localized_items(o):
    """(name, qty) in the customer's language, from the product's translations when it has them."""
    lang = em.lang_of(o)
    out = []
    with db() as c:
        for i in o["items"]:
            r = c.execute("SELECT i18n FROM products WHERE id=?", (i.get("id"),)).fetchone()
            tr_ = json.loads(r["i18n"] or "{}").get(lang, {}) if r else {}
            out.append((tr_.get("name") or i["name"], i["qty"]))
    return out


def signed(subject, lines):
    return subject, "\n".join(lines + ["", STORE["name"]])


def shipped_email(o):
    return signed(*em.shipped(o, localized_items(o)))


def booking_email(o):
    return signed(*em.booking(o, f"{DOMAIN}/{schedule_path(o)}"))


def appt_email(o, settings, kind="booked"):
    if em.lang_of(o) == "en":
        em.T["en"]["ltl_note"] = settings["ltl_note"]
    return signed(*em.appointment(o, fr.window_label(settings, o["appt_window"]), f"{DOMAIN}/{schedule_path(o)}", kind))


def products_by_id(c):
    return {r["id"]: r for r in c.execute("SELECT * FROM products")}


def mark_paid(c, order_id, info):
    """Shared by the Stripe webhook, demo checkout and test orders. Decrements stock once."""
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row or row["status"] != "pending":
        return
    items = json.loads(row["items_json"])
    service = fr.pick_service(row["fulfillment"], info["shipping"])
    settings = fr.load_settings(c)
    extra = {}
    if row["fulfillment"] == "freight":
        pallets, weight, cls = fr.freight_specs(items, products_by_id(c))
        extra = dict(delivery_type="local" if fr.is_local(settings, info.get("zip")) else "ltl",
                     freight_status="unscheduled", sched_token=secrets.token_urlsafe(12),
                     pallets=pallets, weight_lb=weight, freight_class=cls, liftgate=1)
    fields = dict(status="paid", paid_at=now(), total=info["total"], shipping=info["shipping"], email=info.get("email"),
                  phone=info.get("phone"), ship_name=info.get("name"), ship_line1=info.get("line1"),
                  ship_line2=info.get("line2"), ship_city=info.get("city"), ship_state=info.get("state"),
                  ship_zip=info.get("zip"), service=service, ship_method=fr.SERVICES[service]["en"],
                  payment_intent=info.get("payment_intent"), **extra)
    claimed = c.execute(f"UPDATE orders SET {', '.join(k + '=?' for k in fields)} WHERE id=? AND status='pending'",
                        (*fields.values(), order_id)).rowcount
    if not claimed:  # Stripe sent the same event twice, or another worker got here first
        return
    # Stock may have sold out while this customer was on Stripe's page. They've paid, so keep
    # the order, but flag it loudly: history entry, an open to-do and a staff email.
    short = []
    for it in items:
        p = c.execute("SELECT name, stock FROM products WHERE id=?", (it["id"],)).fetchone()
        if p and p["stock"] < it["qty"]:
            short.append(f"{p['name']} ×{it['qty'] - max(p['stock'], 0)}")
    wh = inv.allocate(c, items)
    c.execute("UPDATE orders SET warehouse_id=? WHERE id=?", (wh, order_id))
    if short:
        msg = "⚠️ 库存不足：" + "、".join(short)
        log_event(c, order_id, "stock", msg)
        tasks.save_todo(c, {"title": msg + "（联系顾客：等补货、换货或退款）", "due_at": now(), "order_id": order_id,
                            "email": True}, source="auto")
    for it in items:
        inv.sync_product(c, it["id"])
    log_event(c, order_id, "paid", f"已付款 {cents(info['total'])}")
    if mailer.load(c, STORE["email"]).get("notify_new_order"):
        L = mailer.STAFF.get(mailer.load(c, STORE["email"]).get("staff_lang", "zh"), mailer.STAFF["zh"])
        addr = ", ".join(x for x in [info.get("name"), info.get("line1"), info.get("city"), info.get("state"), info.get("zip")] if x)
        notify_staff(c, "new_order", [L["items"]] + [f"  • {i['name']} × {i['qty']}" for i in items] +
                     ["", f"{L['ship_to']} {addr}", "", L["open"].format(url=f"{DOMAIN}/admin.html")],
                     order_id=order_id, no=row["order_no"], total=cents(info["total"]))
    if extra:
        o = order_json(c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())
        kind = "本地车队" if extra["delivery_type"] == "local" else "LTL 承运商"
        log_event(c, order_id, "freight", f"🚛 卡派订单：{kind}，{extra['pallets']} 托 / {extra['weight_lb']} lb / Class {extra['freight_class']}")
        res = send_email(c, o['email'], *booking_email(o), order_id=o["id"])
        log_event(c, order_id, "email", f"📧 预约送货邮件：{res}")


def validate_cart(c, items):
    clean, subtotal = [], 0
    for it in items or []:
        pid, qty = it.get("id"), it.get("qty")
        p = c.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY_PER_ITEM:
            raise ValueError(f"Invalid item in cart: {pid}")
        if p["stock"] < qty:
            raise ValueError(f"Only {p['stock']} left of {p['name']}.")
        clean.append({"id": pid, "name": p["name"], "price": p["price"], "qty": qty, "ship_mode": p["ship_mode"] or "freight"})
        subtotal += p["price"] * qty
    if not clean:
        raise ValueError("Your cart is empty.")
    return clean, subtotal


def create_order(c, items, subtotal, order_id, test=False, lang="en"):
    fulfillment = "freight" if any(i["ship_mode"] == "freight" for i in items) else "parcel"
    for attempt in range(8):  # another worker may grab the same number at the same moment
        no = next_order_no(c)
        try:
            c.execute(
                "INSERT INTO orders (id, order_no, status, items_json, subtotal, test, created_at, fulfillment, lang) VALUES (?,?,?,?,?,?,?,?,?)",
                (order_id, no, "pending", json.dumps(items), subtotal, int(test), now(), fulfillment, lang if lang in LANGS else "en"),
            )
            break
        except sqlite3.IntegrityError:
            if attempt == 7 or c.execute("SELECT 1 FROM orders WHERE id=?", (order_id,)).fetchone():
                raise
            time.sleep(0.02 * (attempt + 1))
    log_event(c, order_id, "created", "测试订单已创建" if test else "顾客发起结账")
    return no, fulfillment


def shipping_choices(fulfillment, subtotal, lang="en"):
    """(service, amount, label, min days, max days) offered at checkout."""
    free = subtotal >= fr.FREE_OVER
    L = SHIP_LABELS.get(lang, SHIP_LABELS["en"])
    if fulfillment == "parcel":
        return [("standard", 0 if free else fr.PARCEL_FEE, L["free_standard"] if free else L["standard"], 3, 7)]
    return [("curbside", 0 if free else fr.CURBSIDE_FEE, L["curbside"] + (L["free"] if free else ""), 5, 14),
            ("threshold", fr.THRESHOLD_FEE, L["threshold"], 5, 14),
            ("white_glove", fr.WHITE_GLOVE_FEE, L["white_glove"], 7, 21)]


def product_dir(pid):
    """Upload folder for an existing product. Never lets an id point outside UPLOAD_DIR."""
    if not re.fullmatch(r"[a-z0-9-]{1,40}", pid or ""):
        abort(404, "找不到商品")
    with db() as c:
        if not c.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
            abort(404, "找不到商品")
    path = (UPLOAD_DIR / pid).resolve()
    if UPLOAD_DIR.resolve() not in path.parents:
        abort(404, "找不到商品")
    return path


def save_jpeg(file_storage, dest: Path, max_side=1600):
    """Re-encode uploads with Pillow so only real images are stored."""
    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        abort(400, "不是有效的图片文件（支持 JPG / PNG / WebP）")
    img.thumbnail((max_side, max_side))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=86, optimize=True)


def paid_test_order(c, items, subtotal, oid, customer=None, service=None, lang="en"):
    no, fulfillment = create_order(c, items, subtotal, oid, test=True, lang=lang)
    name, email, phone, l1, l2, city, st, z = customer or random.choice(SAMPLE_CUSTOMERS)
    choices = shipping_choices(fulfillment, subtotal)
    pick = next((x for x in choices if x[0] == service), None) or random.choice(choices)
    mark_paid(c, oid, dict(total=subtotal + pick[1], shipping=pick[1], email=email, phone=phone, name=name,
                           line1=l1, line2=l2, city=city, state=st, zip=z))


# ------------------------------------------------------------------ app
app = Flask(__name__, static_folder="public", static_url_path="")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # Render/Railway put a proxy in front
app.config.update(MAX_CONTENT_LENGTH=60 * 1024 * 1024, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=DOMAIN.startswith("https"),
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=12))


def persistent_secret():
    """One signing key shared by every worker process (stored in the database), so an admin
    login works no matter which process answers. SECRET_KEY in .env overrides it."""
    with db() as c:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('secret_key', ?)", (secrets.token_hex(32),))
        return c.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()[0]


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.get("/healthz")
def healthz():
    with db() as c:
        c.execute("SELECT 1").fetchone()
    return jsonify(ok=True, payments="stripe" if stripe.api_key else ("demo" if DEMO_CHECKOUT else "off"))


@app.errorhandler(400)
@app.errorhandler(429)
@app.errorhandler(503)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(404)
def json_error(e):
    return jsonify(error=getattr(e, "description", str(e))), e.code


def int_arg(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        abort(400, f"{name} 要填数字")


def require_admin(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            abort(401, "请先登录后台")
        return fn(*a, **kw)
    return wrapper


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/checkout")
def checkout_link():
    """Checkout URL for Facebook / Instagram shops: /checkout?products=SKU:QTY,... — the store page builds the cart."""
    return send_from_directory(app.static_folder, "index.html")


@app.get("/uploads/<path:p>")
def uploads(p):
    return send_from_directory(UPLOAD_DIR, p, max_age=3600)


# ---------------------------------------------------------------- storefront
@app.get("/api/store")
def store_info():
    with db() as c:
        links = social.links_public(social.load(c))
    return jsonify(**STORE, demo_checkout=DEMO_CHECKOUT, social=links)


@app.get("/api/products")
def products():
    with db() as c:
        rows = c.execute("SELECT * FROM products WHERE active=1 ORDER BY sort, name").fetchall()
    return jsonify([product_json(r) for r in rows])


# ---------------------------------------------------------------- sharing: product pages for social previews, catalog feed
def get_product(c, pid):
    r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone() if pid else None
    return product_json(r) if r else None


def _font(size):
    for f in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def og_card(p, dest):
    """A simple 1200x630 picture for products without a photo (name, price, store)."""
    img = Image.new("RGB", (1200, 630), (243, 244, 240))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1200, 120), fill=(47, 90, 76))
    d.text((60, 36), STORE["name"], font=_font(46), fill=(255, 255, 255))
    words, lines, cur = p["name"].split(), [], ""
    for w in words:
        if d.textlength((cur + " " + w).strip(), font=_font(76)) > 1080 and cur:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    y = 190
    for ln in lines[:3]:
        d.text((60, y), ln, font=_font(76), fill=(29, 42, 38)); y += 92
    d.text((60, y + 20), cents(p["price"]), font=_font(64), fill=(176, 138, 62))
    if p.get("dim"):
        d.text((60, 540), p["dim"], font=_font(34), fill=(95, 109, 104))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=88)


@app.get("/og/<pid>.jpg")
def og_image(pid):
    """Picture that social sites show for a product link: the real photo when there is one."""
    with db() as c:
        p = get_product(c, pid)
    if not p:
        abort(404)
    real = social.image_path(p)
    if real:
        return send_from_directory(UPLOAD_DIR, real.split("/uploads/", 1)[1].split("?")[0], max_age=3600) if real.startswith("/uploads/") \
            else send_from_directory(app.static_folder, real.lstrip("/"), max_age=3600)
    dest = UPLOAD_DIR / "_og" / f"{pid}-{p['price']}-{zlib.crc32(p['name'].encode())}.jpg"
    if not dest.exists():
        og_card(p, dest)
    return send_from_directory(dest.parent, dest.name, max_age=3600)


@app.get("/p/<pid>")
def share_page(pid):
    """What Facebook, WhatsApp, Pinterest etc. read when someone shares a product. People are sent on to the store."""
    with db() as c:
        p = get_product(c, pid)
    if not p or not p["active"]:
        return send_from_directory(app.static_folder, "index.html")
    url = f"{DOMAIN}/p/{pid}"
    img = DOMAIN + (social.image_path(p) or f"/og/{pid}.jpg")
    title = f"{p['name']} · {cents(p['price'])}"
    desc = " · ".join(x for x in (p.get("dim"), p.get("mat"), "Free delivery over $500" if p["price"] < 50000 else "Free delivery") if x)
    ld = {"@context": "https://schema.org", "@type": "Product", "name": p["name"], "image": [img], "sku": p.get("sku"),
          "description": desc, "brand": {"@type": "Brand", "name": STORE["name"]},
          "offers": {"@type": "Offer", "price": f"{p['price'] / 100:.2f}", "priceCurrency": "USD", "url": url,
                     "availability": "https://schema.org/" + ("InStock" if p["stock"] > 0 else "OutOfStock")}}
    from html import escape as h
    target = f"/?p={pid}" + ("&" + request.query_string.decode() if request.query_string else "")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{h(title)}</title>
<meta name="description" content="{h(desc)}"><link rel="canonical" href="{h(url)}">
<meta property="og:type" content="product"><meta property="og:site_name" content="{h(STORE['name'])}">
<meta property="og:title" content="{h(title)}"><meta property="og:description" content="{h(desc)}">
<meta property="og:url" content="{h(url)}"><meta property="og:image" content="{h(img)}">
<meta property="product:price:amount" content="{p['price'] / 100:.2f}"><meta property="product:price:currency" content="USD">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{h(title)}"><meta name="twitter:image" content="{h(img)}">
<script type="application/ld+json">{json.dumps(ld).replace("</", "<\\/")}</script>
<style>body{{font-family:system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;color:#1d2a26}}img{{width:100%;border-radius:8px}}a{{display:inline-block;background:#2f5a4c;color:#fff;padding:12px 18px;border-radius:6px;text-decoration:none}}</style>
</head><body><img src="{h(img)}" alt=""><h1>{h(p['name'])}</h1><p>{h(cents(p['price']))} · {h(desc)}</p>
<p><a href="{h(target)}">🛒 View in store</a></p><script>location.replace({json.dumps(target)})</script></body></html>"""
    return page, 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=300"}


@app.get("/feeds/products.xml")
def product_feed():
    """Catalog for Facebook / Instagram Shops, Google Merchant Center, Pinterest and TikTok (they fetch it daily)."""
    lang = request.args.get("lang") if request.args.get("lang") in ("en", "es") else "en"
    with db() as c:
        rows = c.execute("SELECT * FROM products WHERE active=1 ORDER BY sort, name").fetchall()
    xml = social.feed_xml([product_json(r) for r in rows], DOMAIN, STORE["name"], lang)
    return xml, 200, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "public, max-age=900"}


@app.post("/api/checkout")
def checkout():
    body = request.get_json(silent=True) or {}
    lang = body.get("lang") if body.get("lang") in LANGS else "en"
    if not PAYMENTS_READY:
        abort(503, "Online payment isn't set up yet. Please contact us to order.")
    with db() as c:
        try:
            items, subtotal = validate_cart(c, body.get("items"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if DEMO_CHECKOUT:
            oid = "demo_" + os.urandom(6).hex()
            paid_test_order(c, items, subtotal, oid, lang=lang)
            return jsonify(url=f"/success.html?session_id={oid}")

    fulfillment = "freight" if any(i["ship_mode"] == "freight" for i in items) else "parcel"
    try:
        s = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"quantity": i["qty"], "price_data": {
                "currency": "usd", "unit_amount": i["price"], "product_data": {"name": i["name"]}}} for i in items],
            shipping_address_collection={"allowed_countries": ["US"]},
            shipping_options=[{"shipping_rate_data": {
                "type": "fixed_amount", "display_name": label, "fixed_amount": {"amount": amt, "currency": "usd"},
                "delivery_estimate": {"minimum": {"unit": "business_day", "value": lo},
                                      "maximum": {"unit": "business_day", "value": hi}}}}
                for _, amt, label, lo, hi in shipping_choices(fulfillment, subtotal, lang)],
            locale={"zh": "zh", "es": "es"}.get(lang, "en"),
            expires_at=int(datetime.now(timezone.utc).timestamp()) + 30 * 60,
            phone_number_collection={"enabled": True},
            # automatic_tax={"enabled": True},   # turn on after enabling Stripe Tax
            success_url=f"{DOMAIN}/success.html?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN}/?canceled=1",
        )
    except stripe.StripeError as e:
        return jsonify(error=e.user_message or str(e)), 502
    with db() as c:
        create_order(c, items, subtotal, s.id, lang=lang)
    return jsonify(url=s.url)


def paid_info(obj):
    ship = (obj.get("collected_information") or {}).get("shipping_details") or obj.get("shipping_details") or {}
    a = ship.get("address") or {}
    cust = obj.get("customer_details") or {}
    return dict(total=obj.get("amount_total"), shipping=(obj.get("total_details") or {}).get("amount_shipping", 0),
                email=cust.get("email"), phone=cust.get("phone"), name=ship.get("name") or cust.get("name"),
                line1=a.get("line1"), line2=a.get("line2"), city=a.get("city"), state=a.get("state"),
                zip=a.get("postal_code"), payment_intent=obj.get("payment_intent"))


@app.post("/webhook")
def webhook():
    payload = request.get_data()
    try:
        stripe.Webhook.construct_event(payload, request.headers.get("Stripe-Signature", ""), WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        abort(400)
    event = json.loads(payload)
    kind, obj = event.get("type"), event["data"]["object"]
    with db() as c:
        c.execute("BEGIN IMMEDIATE")  # one webhook at a time touches an order
        if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded") and obj.get("payment_status") == "paid":
            mark_paid(c, obj["id"], paid_info(obj))
        elif kind == "checkout.session.completed":
            # bank transfer / ACH: the customer finished checkout, money arrives in a few days
            if c.execute("UPDATE orders SET payment_intent=? WHERE id=? AND status='pending'",
                         (obj.get("payment_intent"), obj["id"])).rowcount:
                log_event(c, obj["id"], "payment", "⏳ 顾客已下单，等银行确认付款（ACH 等方式需要几天）")
        elif kind in ("checkout.session.expired", "checkout.session.async_payment_failed"):
            if c.execute("UPDATE orders SET status='canceled' WHERE id=? AND status='pending'", (obj["id"],)).rowcount:
                log_event(c, obj["id"], "canceled", "顾客未完成付款，结账已过期" if kind.endswith("expired") else "银行付款失败，订单已取消")
        elif kind == "charge.refunded" and obj.get("payment_intent"):
            r = c.execute("SELECT id, refunded FROM orders WHERE payment_intent=?", (obj["payment_intent"],)).fetchone()
            amount = int(obj.get("amount_refunded") or 0)
            if r and amount > (r["refunded"] or 0):
                c.execute("UPDATE orders SET refunded=? WHERE id=?", (amount, r["id"]))
                log_event(c, r["id"], "refund", f"💸 Stripe 已退款，累计 {cents(amount)}")
    return "", 200


@app.get("/api/order/<oid>")
def public_order(oid):
    with db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        settings = fr.load_settings(c)
    if not r:
        abort(404, "Order not found")
    o = order_json(r, settings)
    keep = ["order_no", "status", "items", "subtotal", "shipping", "total", "email", "ship_name", "ship_city",
            "ship_state", "ship_method", "carrier", "tracking", "tracking_url", "fulfillment", "schedule_url",
            "appt_date", "appt_window_label", "delivery_type"]
    out = {k: o.get(k) for k in keep}
    out["freight_status"] = fr.FREIGHT_EN.get(o.get("freight_status") or "", "")
    return jsonify(out)


# ---------------------------------------------------------------- customer: book delivery
def order_for_token(c, oid, token):
    r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not r or not r["sched_token"] or not hmac.compare_digest(r["sched_token"], token or ""):
        abort(404, "This booking link isn't valid. Please use the link from your email.")
    return r


def schedule_payload(c, r, settings):
    o = order_json(r, settings)
    return dict(
        order_no=o["order_no"], name=o["ship_name"],
        address=[x for x in [o["ship_line1"], o["ship_line2"], f"{o['ship_city']}, {o['ship_state']} {o['ship_zip']}"] if x],
        items=[{"id": i.get("id"), "name": i["name"], "qty": i["qty"], "freight": i.get("ship_mode") == "freight"} for i in o["items"]],
        service=fr.SERVICES[o["service"]]["en"], delivery_type=o["delivery_type"], ltl_note=settings["ltl_note"],
        appt_date=o["appt_date"], appt_window=o["appt_window"], appt_window_label=o["appt_window_label"],
        home_type=o["home_type"], floor=o["floor"], elevator=o["elevator"], liftgate=o["liftgate"],
        access_notes=o["access_notes"], can_change=fr.can_customer_change(settings, r),
        status=fr.FREIGHT_EN.get(o["freight_status"] or "", ""), cutoff_hour=settings["cutoff_hour"],
        store_phone=STORE["phone"], slots=fr.slots_for(c, settings, r))


@app.get("/api/schedule/<oid>")
def schedule_get(oid):
    with db() as c:
        r = order_for_token(c, oid, request.args.get("t"))
        return jsonify(schedule_payload(c, r, fr.load_settings(c)))


@app.post("/api/schedule/<oid>")
def schedule_post(oid):
    b = request.get_json(silent=True) or {}
    with db() as c:
        c.execute("BEGIN IMMEDIATE")  # capacity check and booking happen together
        r = order_for_token(c, oid, b.get("t"))
        settings = fr.load_settings(c)
        if not fr.can_customer_change(settings, r):
            abort(400, f"This delivery can't be changed online anymore. Please call us at {STORE['phone']}.")
        slot = next((s for s in fr.slots_for(c, settings, r) if s["date"] == b.get("date")), None)
        win = slot and next((w for w in slot["windows"] if w["id"] == b.get("window")), None)
        if not win:
            abort(400, "Please pick an available day and time window.")
        if win["full"]:
            abort(400, "That window just filled up. Please pick another one.")
        home = b.get("home_type") if b.get("home_type") in ("house", "apartment", "business") else "house"
        try:
            floor = max(1, min(60, int(b.get("floor") or 1)))
        except (TypeError, ValueError):
            floor = 1
        changed = bool(r["appt_date"])
        c.execute("""UPDATE orders SET appt_date=?, appt_window=?, scheduled_at=?, home_type=?, floor=?, elevator=?,
                     liftgate=?, access_notes=?, freight_status=CASE WHEN freight_status='unscheduled' THEN 'scheduled'
                     ELSE freight_status END WHERE id=?""",
                  (b["date"], b["window"], now(), home, floor, int(bool(b.get("elevator"))),
                   int(bool(b.get("liftgate", True))), str(b.get("access_notes") or "")[:500], oid))
        o = order_json(c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(), settings)
        log_event(c, oid, "schedule", f"📅 顾客{'改约' if changed else '预约'}：{o['appt_date']} {o['appt_window_label']}")
        res = send_email(c, o['email'], *appt_email(o, settings, 'changed' if changed else 'booked'), order_id=o["id"])
        log_event(c, oid, "email", f"📧 预约确认邮件：{res}")
        return jsonify(schedule_payload(c, c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(), settings))


# ---------------------------------------------------------------- admin: auth
def password_problem():
    if not ADMIN_PASSWORD:
        return "服务器还没设置 ADMIN_PASSWORD，请在 .env 里设置后重启"
    if not LOCAL and (len(ADMIN_PASSWORD) < 10 or ADMIN_PASSWORD.lower() in WEAK_PASSWORDS):
        return "ADMIN_PASSWORD 太弱了：正式网站要至少 10 位，不能用示例密码。改好 .env 后重启"
    return None


@app.get("/api/admin/me")
def admin_me():
    return jsonify(admin=bool(session.get("admin")), configured=not password_problem(), problem=password_problem(),
                   demo_checkout=DEMO_CHECKOUT, payments_ready=PAYMENTS_READY)


@app.post("/api/admin/login")
def admin_login():
    problem = password_problem()
    if problem:
        abort(403, problem)
    ip = request.remote_addr or "?"
    since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS login_fails (ip TEXT, at TEXT)")
        fails = c.execute("SELECT COUNT(*) FROM login_fails WHERE ip=? AND at>?", (ip, since)).fetchone()[0]
        if fails >= 5:
            abort(429, "密码错太多次了，请 10 分钟后再试")
        pw = (request.get_json(silent=True) or {}).get("password", "")
        if not pw or not hmac.compare_digest(str(pw), ADMIN_PASSWORD):
            c.execute("INSERT INTO login_fails (ip, at) VALUES (?,?)", (ip, now()))
            c.execute("DELETE FROM login_fails WHERE at<?", (since,))
            c.commit()
            abort(401, "密码不对")
        c.execute("DELETE FROM login_fails WHERE ip=?", (ip,))
    session.clear()
    session["admin"] = True
    session.permanent = True
    return jsonify(ok=True)


@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return jsonify(ok=True)


# ---------------------------------------------------------------- refunds
GONE = ("picked_up", "in_transit", "out_for_delivery", "delivered", "exception")


def refund_guard(r, confirm):
    """A refund needs the order number typed back, and is refused once the goods may have left."""
    if r["status"] != "paid" or r["shipped_at"] or (r["freight_status"] or "") in GONE:
        abort(400, "这单的货已经发出去过，不能在这里退款。等货退回仓库后，到 Stripe 后台退款，这里会自动记下来")
    if str(confirm or "").strip().upper() != r["order_no"].upper():
        abort(400, "退款前请输入完整的订单号确认")


def refund_order(c, r):
    """Full refund of what's left on a Stripe payment. Raises 502 (and changes nothing) if Stripe says no."""
    left = (r["total"] or 0) - (r["refunded"] or 0)
    if r["test"] or not r["payment_intent"]:
        log_event(c, r["id"], "refund", "💸 测试订单，没有真实付款，不用退款")
        return
    if left <= 0:
        return
    if not stripe.api_key:
        abort(400, "服务器没有 Stripe 密钥，没法退款")
    try:
        stripe.Refund.create(payment_intent=r["payment_intent"], amount=left, reason="requested_by_customer",
                             metadata={"order_no": r["order_no"]})
    except stripe.StripeError as e:
        abort(502, f"Stripe 退款失败，订单没有取消：{getattr(e, 'user_message', None) or e}")
    c.execute("UPDATE orders SET refunded=? WHERE id=?", ((r["refunded"] or 0) + left, r["id"]))
    log_event(c, r["id"], "refund", f"💸 已通过 Stripe 退款 {cents(left)}")


# ---------------------------------------------------------------- admin: orders
def order_filter(args):
    """WHERE clause for the order list and the CSV export: status tab, search box, date range."""
    where, params = [], []
    status = args.get("status")
    if status in STATUSES:
        where.append("status=?"); params.append(status)
    elif status == "unscheduled":
        where.append("status='paid' AND freight_status='unscheduled'")
    q = (args.get("q") or "").strip()
    if q:
        like = f"%{q.lower()}%"
        digits = re.sub(r"\D", "", q)
        cond = ["LOWER(order_no) LIKE ?", "LOWER(ship_name) LIKE ?", "LOWER(email) LIKE ?", "LOWER(ship_city) LIKE ?",
                "ship_zip LIKE ?", "LOWER(items_json) LIKE ?", "LOWER(COALESCE(tracking,'')||' '||COALESCE(pro,'')) LIKE ?"]
        params += [like] * len(cond)
        if len(digits) >= 4:  # phone numbers in any format
            cond.append("REPLACE(REPLACE(REPLACE(REPLACE(phone,'(',''),')',''),'-',''),' ','') LIKE ?")
            params.append(f"%{digits}%")
        where.append("(" + " OR ".join(cond) + ")")
    for key, op in (("from", ">="), ("to", "<=")):
        v = args.get(key)
        if v and fr._is_date(v):
            where.append(f"substr(COALESCE(paid_at, created_at),1,10) {op} ?"); params.append(v)
    return (" WHERE " + " AND ".join(where)) if where else "", params


@app.get("/api/admin/orders")
@require_admin
def admin_orders():
    where, args = order_filter(request.args)
    q = "SELECT * FROM orders" + where
    with db() as c:
        settings = fr.load_settings(c)
        rows = c.execute(q + " ORDER BY created_at DESC LIMIT 500", args).fetchall()
        counts = dict(c.execute("SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall())
        counts["to_print"] = c.execute("SELECT COUNT(*) FROM orders WHERE status IN ('paid','shipped') AND labels_printed_at IS NULL").fetchone()[0]
        counts["unscheduled"] = c.execute("SELECT COUNT(*) FROM orders WHERE freight_status='unscheduled' AND status='paid'").fetchone()[0]
    return jsonify(orders=[order_json(r, settings) for r in rows], counts=counts)


@app.get("/api/admin/orders.csv")
@require_admin
def admin_orders_csv():
    """Orders as a spreadsheet for bookkeeping (UTF-8 with BOM so Excel shows Chinese correctly)."""
    import csv, io
    where, args = order_filter(request.args)
    with db() as c:
        settings = fr.load_settings(c)
        whs = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM warehouses")}
        rows = c.execute("SELECT * FROM orders" + where + " ORDER BY created_at", args).fetchall()
    out = io.StringIO()
    raw = csv.writer(out)

    class SafeWriter:  # a name like "=HYPERLINK(...)" must not run as a formula in Excel
        @staticmethod
        def writerow(row):
            raw.writerow([("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v for v in row])
    w = SafeWriter
    w.writerow(["order_no", "status", "paid_at", "customer", "email", "phone", "address", "city", "state", "zip",
                "items", "subtotal", "shipping", "total", "service", "warehouse", "carrier", "tracking",
                "delivery", "appointment", "pro", "freight_status", "test"])
    for r in rows:
        o = order_json(r, settings)
        w.writerow([o["order_no"], o["status"], (o["paid_at"] or "")[:16].replace("T", " "), o["ship_name"], o["email"], o["phone"],
                    " ".join(x for x in [o["ship_line1"], o["ship_line2"]] if x), o["ship_city"], o["ship_state"], o["ship_zip"],
                    "; ".join(f"{i['name']} x{i['qty']}" for i in o["items"]),
                    f"{(o['subtotal'] or 0) / 100:.2f}", f"{(o['shipping'] or 0) / 100:.2f}", f"{(o['total'] or 0) / 100:.2f}",
                    o["ship_method"] or "", whs.get(o["warehouse_id"], ""), o["carrier"] or o["freight_carrier"] or "", o["tracking"] or "",
                    o["delivery_type"] or "", f"{o['appt_date'] or ''} {o.get('appt_window_label') or ''}".strip(), o["pro"] or "",
                    o["freight_status"] or "", "yes" if o["test"] else ""])
    name = f"mujian-orders-{datetime.now(fr.TZ):%Y%m%d}.csv"
    return app.response_class("\ufeff" + out.getvalue(), mimetype="text/csv",
                              headers={"Content-Disposition": f"attachment; filename={name}"})


@app.get("/api/admin/orders/<oid>")
@require_admin
def admin_order(oid):
    with db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        settings = fr.load_settings(c)
        if not r:
            abort(404, "找不到这个订单")
        o = order_json(r, settings, with_events=True)
        o["units"] = handling_units(o, products_by_id(c))
        o["notes"] = tasks.notes_for(c, oid)
        o["todos"] = tasks.list_todos(c, "all", oid)
        o["print_notes"] = tasks.printables(c, oid)
        o["warehouse_name"] = (c.execute("SELECT name FROM warehouses WHERE id=?", (r["warehouse_id"],)).fetchone() or [""])[0]
    return jsonify(o)


def handling_units(o, products):
    """Split an order into what gets a label: one pallet per big piece (or the pallet count set
    in the admin, filled round-robin), one box per small piece."""
    big, small = [], []
    for it in o["items"]:
        p = products.get(it["id"])
        piece = {"id": it["id"], "name": it["name"], "sku": (p["sku"] if p else "") or "",
                 "weight": (p["weight_lb"] if p else 0) or 0}
        mode = it.get("ship_mode") or (p["ship_mode"] if p else "freight")
        for _ in range(it["qty"]):
            (big if o.get("fulfillment") == "freight" and mode == "freight" else small).append(piece)
    units = []
    if big:
        n = max(1, min(len(big), o.get("pallets") or len(big)))
        pallets = [[] for _ in range(n)]
        for i, piece in enumerate(big):
            pallets[i % n].append(piece)
        units += [("pallet", ps) for ps in pallets]
    units += [("box", [piece]) for piece in small]
    out = []
    for i, (kind, pieces) in enumerate(units, 1):
        lines = {}
        for pc in pieces:
            ln = lines.setdefault(pc["id"], {"name": pc["name"], "sku": pc["sku"], "qty": 0})
            ln["qty"] += 1
        out.append({"id": f"{o['order_no']}-{i:02d}", "n": i, "of": len(units), "kind": kind,
                    "items": list(lines.values()),
                    "weight_lb": sum(pc["weight"] for pc in pieces) + (40 if kind == "pallet" else 0)})
    return out


@app.get("/api/admin/print-queue")
@require_admin
def admin_print_queue():
    """Paid orders whose labels have not been printed yet, with everything a label needs."""
    with db() as c:
        settings = fr.load_settings(c)
        prods = products_by_id(c)
        rows = c.execute("""SELECT * FROM orders WHERE status IN ('paid','shipped') AND labels_printed_at IS NULL
                            ORDER BY paid_at LIMIT 50""").fetchall()
        out = []
        for r in rows:
            o = order_json(r, settings)
            o["units"] = handling_units(o, prods)
            o["print_notes"] = tasks.printables(c, r["id"])
            out.append(o)
    return jsonify(orders=out, store=STORE)


@app.post("/api/admin/print-queue/ack")
@require_admin
def admin_print_ack():
    b = request.get_json(silent=True) or {}
    ids = [i for i in (b.get("ids") or []) if isinstance(i, str)][:100]
    who = str(b.get("by") or "后台")[:40]
    with db() as c:
        for oid in ids:
            r = c.execute("SELECT labels_printed_at FROM orders WHERE id=?", (oid,)).fetchone()
            if not r:
                continue
            c.execute("UPDATE orders SET labels_printed_at=? WHERE id=?", (now(), oid))
            log_event(c, oid, "labels", f"🖨️ 托盘/箱子标签已打印（{who}）" if not r[0] else f"🖨️ 标签重新打印（{who}）")
    return jsonify(ok=True, count=len(ids))


def apply_freight_patch(c, r, body, settings):
    oid, sets, notes = r["id"], {}, []
    if "delivery_type" in body and body["delivery_type"] in ("local", "ltl") and body["delivery_type"] != r["delivery_type"]:
        sets["delivery_type"] = body["delivery_type"]
        notes.append("改为" + ("本地车队配送" if body["delivery_type"] == "local" else "LTL 承运商"))
    if "appt_date" in body or "appt_window" in body:
        d, w = body.get("appt_date") or None, body.get("appt_window") or None
        if d and not fr._is_date(d):
            abort(400, "日期格式不对")
        if w and w not in [x["id"] for x in settings["windows"]]:
            abort(400, "没有这个时段")
        if (d, w) != (r["appt_date"], r["appt_window"]):
            sets.update(appt_date=d, appt_window=w, scheduled_at=now() if d else None)
            if d and r["freight_status"] == "unscheduled":
                sets["freight_status"] = "scheduled"
            notes.append(f"后台{'安排' if d else '清除'}送货时间{('：' + d + ' ' + fr.window_label(settings, w)) if d else ''}")
    for k, conv in [("truck", str), ("pro", str), ("freight_carrier", str), ("pickup_date", str),
                    ("freight_class", str), ("access_notes", str)]:
        if k in body and (body[k] or "") != (r[k] or ""):
            v = conv(body[k] or "").strip()[:120]
            if k == "pickup_date" and v and not fr._is_date(v):
                abort(400, "提货日期格式不对")
            sets[k] = v or None
            label = {"truck": "车辆", "pro": "PRO 单号", "freight_carrier": "承运商", "pickup_date": "提货日期",
                     "freight_class": "货物等级", "access_notes": "送货备注"}[k]
            notes.append(f"{label}：{v or '—'}")
    for k, label in [("stop_no", "站序"), ("pallets", "托盘数"), ("weight_lb", "重量 lb"), ("floor", "楼层")]:
        if k in body and body[k] not in ("", None) and str(body[k]) != str(r[k] or ""):
            try:
                sets[k] = max(0, int(body[k]))
            except (TypeError, ValueError):
                abort(400, f"{label}要填数字")
            notes.append(f"{label}：{sets[k]}")
    for k, label in [("liftgate", "尾板"), ("elevator", "电梯")]:
        if k in body and int(bool(body[k])) != (r[k] or 0):
            sets[k] = int(bool(body[k]))
            notes.append(f"{label}：{'要' if sets[k] else '不要'}")
    fs = body.get("freight_status")
    if fs and fs != r["freight_status"]:
        if fs not in fr.FREIGHT_STATUSES:
            abort(400, "没有这个卡派状态")
        if fs not in ("unscheduled", "exception") and not (sets.get("appt_date") or r["appt_date"]):
            abort(400, "还没有送货时间，先帮顾客预约或在上面安排日期")
        sets["freight_status"] = fs
        notes.append(f"🚛 卡派状态：{fr.FREIGHT_ZH[fs]}")
        new_main = fr.main_status_for(fs, r["status"])
        if new_main != r["status"]:
            sets["status"] = new_main
            if new_main == "shipped" and not r["shipped_at"]:
                sets["shipped_at"] = now()
            if new_main == "delivered":
                sets["delivered_at"] = now()
    if sets:
        c.execute(f"UPDATE orders SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), oid))
        log_event(c, oid, "freight", "；".join(notes))
        o = order_json(c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(), settings)
        if "appt_date" in sets and sets["appt_date"]:
            kind = "changed" if r["appt_date"] else "booked"
            res = send_email(c, o['email'], *appt_email(o, settings, kind), order_id=o["id"])
            log_event(c, oid, "email", f"📧 预约通知邮件：{res}")
        if sets.get("status") == "shipped" and r["status"] != "shipped":
            res = send_email(c, o['email'], *shipped_email(o), order_id=o["id"])
            log_event(c, oid, "email", f"📧 发货通知邮件：{res}")


@app.post("/api/admin/orders/<oid>")
@require_admin
def admin_update_order(oid):
    body = request.get_json(silent=True) or {}
    with db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not r:
            abort(404, "找不到这个订单")
        settings = fr.load_settings(c)
        if "note" in body and str(body["note"]).strip():  # older clients: one note per save
            tasks.add_note(c, oid, body["note"])
            log_event(c, oid, "note", "📝 加了一条备注")
        if "carrier" in body or "tracking" in body:
            carrier = body.get("carrier", r["carrier"]) or ""
            tracking = re.sub(r"\s+", "", body.get("tracking", r["tracking"]) or "")
            if carrier and carrier not in CARRIERS:
                abort(400, "不支持的快递公司")
            if (carrier, tracking) != (r["carrier"] or "", r["tracking"] or ""):
                c.execute("UPDATE orders SET carrier=?, tracking=? WHERE id=?", (carrier, tracking, oid))
                if tracking:
                    log_event(c, oid, "tracking", f"快递单号：{carrier} {tracking}")
        wh = body.get("warehouse_id")
        if wh and wh != r["warehouse_id"]:
            if r["status"] != "paid":
                abort(400, "只有待发货的订单能换发货仓库")
            if not inv.warehouse_exists(c, wh):
                abort(400, "没有这个仓库")
            c.execute("UPDATE orders SET warehouse_id=? WHERE id=?", (wh, oid))
            name = c.execute("SELECT name FROM warehouses WHERE id=?", (wh,)).fetchone()[0]
            log_event(c, oid, "warehouse", f"🏬 发货仓库改为 {name}")
            for it in json.loads(r["items_json"]):
                inv.sync_product(c, it["id"])
            r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if r["fulfillment"] == "freight":
            apply_freight_patch(c, r, body, settings)
            r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        new = body.get("status")
        if new and new != r["status"]:
            allowed = {"paid": {"shipped", "canceled"}, "shipped": {"delivered", "paid"},
                       "delivered": {"shipped"}, "pending": {"canceled"}, "canceled": set()}
            if r["fulfillment"] == "freight" and new != "canceled":
                abort(400, "卡派订单请用下面的卡派状态按钮来更新")
            if new not in allowed.get(r["status"], set()):
                abort(400, f"不能从「{r['status']}」改成「{new}」")
            if new == "canceled" and body.get("refund"):
                refund_guard(r, body.get("confirm"))
                refund_order(c, r)
            stamp = {"shipped": "shipped_at", "delivered": "delivered_at"}.get(new)
            c.execute(f"UPDATE orders SET status=?{', ' + stamp + '=?' if stamp else ''} WHERE id=?",
                      (new, now(), oid) if stamp else (new, oid))
            labels = {"shipped": "标记为已发货", "delivered": "标记为已送达", "canceled": "订单已取消（占用的库存已释放）",
                      "paid": "退回到待发货"}
            log_event(c, oid, new, labels[new])
            if new == "shipped":
                o = order_json(c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
                res = send_email(c, o['email'], *shipped_email(o), order_id=o["id"])
                log_event(c, oid, "email", f"📧 发货通知邮件：{res}")
        inv.settle_order(c, oid)
        for it in json.loads(r["items_json"]):
            inv.sync_product(c, it["id"])
    return admin_order(oid)


@app.post("/api/admin/orders/<oid>/resend-booking")
@require_admin
def admin_resend_booking(oid):
    with db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not r or r["fulfillment"] != "freight":
            abort(404, "这不是卡派订单")
        o = order_json(r)
        res = send_email(c, o['email'], *booking_email(o), order_id=o["id"])
        log_event(c, oid, "email", f"📧 重发预约链接：{res}")
    return admin_order(oid)


@app.post("/api/admin/test-order")
@require_admin
def admin_test_order():
    with db() as c:
        stock = c.execute("SELECT id, stock, ship_mode FROM products WHERE active=1 AND stock>0").fetchall()
        if not stock:
            abort(400, "所有商品都没有库存了，先去商品管理补货")
        picks = random.sample(stock, k=min(len(stock), random.choice([1, 1, 2, 3])))
        items, subtotal = validate_cart(c, [{"id": p["id"], "qty": 1} for p in picks])
        oid = "test_" + os.urandom(6).hex()
        paid_test_order(c, items, subtotal, oid)
    return admin_order(oid)


# ---------------------------------------------------------------- admin: deliveries & settings
@app.get("/api/admin/deliveries")
@require_admin
def admin_deliveries():
    start = request.args.get("from") or fr.today().isoformat()
    if not fr._is_date(start):
        abort(400, "日期格式不对")
    days = max(1, min(42, int_arg("days", 14)))
    end = (date.fromisoformat(start) + timedelta(days=days - 1)).isoformat()
    with db() as c:
        settings = fr.load_settings(c)
        rows = c.execute("""SELECT * FROM orders WHERE fulfillment='freight' AND status!='canceled'
                            AND appt_date BETWEEN ? AND ? ORDER BY appt_date, appt_window, stop_no, order_no""",
                         (start, end)).fetchall()
        waiting = c.execute("""SELECT * FROM orders WHERE fulfillment='freight' AND status='paid'
                               AND (appt_date IS NULL OR freight_status='unscheduled') ORDER BY paid_at""").fetchall()
    by_day = {}
    for r in rows:
        by_day.setdefault(r["appt_date"], []).append(order_json(r, settings))
    out = []
    for i in range(days):
        d = date.fromisoformat(start) + timedelta(days=i)
        ds = d.isoformat()
        orders = by_day.get(ds, [])
        out.append({"date": ds, "weekday": d.isoweekday(), "open": d.isoweekday() in settings["weekdays"] and ds not in settings["blackout"],
                    "windows": [{"id": w["id"], "label": w["label"],
                                 "local": sum(1 for o in orders if o["appt_window"] == w["id"] and o["delivery_type"] == "local")}
                                for w in settings["windows"]],
                    "orders": orders})
    return jsonify(days=out, waiting=[order_json(r, settings) for r in waiting], settings=settings)


@app.get("/api/admin/settings")
@require_admin
def admin_settings():
    with db() as c:
        return jsonify(fr.load_settings(c))


@app.post("/api/admin/settings")
@require_admin
def admin_save_settings():
    try:
        s = fr.clean_settings(request.get_json(silent=True) or {})
    except ValueError as e:
        abort(400, str(e))
    with db() as c:
        c.execute("INSERT INTO settings (key, value) VALUES ('delivery', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (json.dumps(s),))
    return jsonify(s)


def send_reminders():
    """Email customers whose delivery is tomorrow. Safe to run more than once a day."""
    tomorrow = (fr.today() + timedelta(days=1)).isoformat()
    sent = []
    with db() as c:
        settings = fr.load_settings(c)
        rows = c.execute("""SELECT * FROM orders WHERE fulfillment='freight' AND appt_date=? AND status!='canceled'
                            AND (reminded_for IS NULL OR reminded_for!=?)""", (tomorrow, tomorrow)).fetchall()
        for r in rows:
            if not c.execute("UPDATE orders SET reminded_for=? WHERE id=? AND (reminded_for IS NULL OR reminded_for!=?)",
                             (tomorrow, r["id"], tomorrow)).rowcount:
                continue
            o = order_json(r, settings)
            res = send_email(c, o["email"], *appt_email(o, settings, "reminder"), order_id=o["id"])
            log_event(c, r["id"], "email", f"📧 送货前一天提醒：{res}")
            sent.append(o["order_no"])
    return sent


@app.post("/api/admin/send-reminders")
@require_admin
def admin_send_reminders():
    return jsonify(sent=send_reminders())


# ---------------------------------------------------------------- admin: products
@app.get("/api/admin/products")
@require_admin
def admin_products():
    with db() as c:
        rows = c.execute("SELECT * FROM products ORDER BY sort, name").fetchall()
    return jsonify([product_json(r) for r in rows])


@app.post("/api/admin/products")
@require_admin
def admin_save_product():
    p = request.get_json(silent=True) or {}
    pid = p.get("id") or re.sub(r"[^a-z0-9]+", "-", (p.get("name") or "item").lower()).strip("-")[:30] + "-" + os.urandom(2).hex()
    if not re.fullmatch(r"[a-z0-9-]{1,40}", pid):
        abort(400, "商品编号只能用小写字母、数字和横线")
    name = (p.get("name") or "").strip()
    try:
        price = int(round(float(p.get("price", 0))))
        stock = int(p.get("stock", 0))
        weight = int(float(p.get("weight_lb") or 0))
        box = [float(p.get(k) or 0) or None for k in ("box_l", "box_w", "box_h")]
    except (TypeError, ValueError):
        abort(400, "价格、库存、重量和尺寸要填数字")
    if not name or price <= 0 or stock < 0:
        abort(400, "请填写商品名称、大于 0 的价格和不小于 0 的库存")
    ship_mode = "parcel" if p.get("ship_mode") == "parcel" else "freight"
    fclass = str(p.get("freight_class") or "")
    if fclass and fclass not in fr.FREIGHT_CLASSES:
        abort(400, "货物等级不在 NMFC 标准等级里")
    fields = dict(name=name, cat=p.get("cat") or "Living", art=p.get("art") or "cabinet", price=price,
                  dim=p.get("dim") or "", mat=p.get("mat") or "", lead=p.get("lead") or "",
                  active=int(bool(p.get("active", True))), ship_mode=ship_mode, weight_lb=weight,
                  box_l=box[0], box_w=box[1], box_h=box[2], freight_class=fclass, updated_at=now())
    sku = re.sub(r"\s+", "", str(p.get("sku") or "")).upper()[:32] or "MJ-" + pid.replace("-", "").upper()
    if not re.fullmatch(r"[A-Z0-9._/-]{2,32}", sku):
        abort(400, "SKU 只能用字母、数字和 - . _ /，2 到 32 位")
    fields["sku"] = sku
    tr_ = {}
    for lg in ("zh", "es"):
        v = (p.get("i18n") or {}).get(lg) or {}
        entry = {k: str(v.get(k) or "").strip()[:120] for k in ("name", "mat") if str(v.get(k) or "").strip()}
        if entry:
            tr_[lg] = entry
    fields["i18n"] = json.dumps(tr_, ensure_ascii=False)
    with db() as c:
        was = c.execute("SELECT active FROM products WHERE id=?", (pid,)).fetchone()
        if c.execute("SELECT 1 FROM products WHERE sku=? AND id!=?", (sku, pid)).fetchone():
            abort(400, f"SKU {sku} 已经被别的商品用了")
        if c.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
            c.execute(f"UPDATE products SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), pid))
        else:
            sort = c.execute("SELECT COALESCE(MAX(sort),0)+1 FROM products").fetchone()[0]
            c.execute(f"INSERT INTO products (id, sort, stock, {', '.join(fields)}) VALUES (?, ?, 0, {', '.join('?' * len(fields))})",
                      (pid, sort, *fields.values()))
            wh = p.get("warehouse_id") or inv.warehouses(c)[0]["id"]
            if stock:
                inv.move(c, pid, wh, stock, "init", "", "新商品期初库存")
            inv.sync_product(c, pid)
        if fields["active"] and (not was or not was[0]):
            auto_post_new(c, pid)
        r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_json(r))


def auto_post_new(c, pid):
    """New (or back on sale) product: post it once, 15 minutes later so there's time to add photos."""
    s = social.load(c)
    targets = [t for t in s["targets"] if social.configured(s).get(t)]
    if s["auto_new"] and targets and not c.execute("SELECT 1 FROM social_posts WHERE product_id=? AND kind='auto'", (pid,)).fetchone():
        social.queue(c, targets, product_id=pid, kind="auto", delay_min=15)


@app.delete("/api/admin/products/<pid>")
@require_admin
def admin_delete_product(pid):
    folder = product_dir(pid)
    with db() as c:
        held = c.execute("SELECT COALESCE(SUM(qty),0) FROM stock WHERE product_id=?", (pid,)).fetchone()[0]
        if held:
            abort(400, f"还有 {held} 件库存，先在库存里调整为 0，或者改成下架")
        c.execute("DELETE FROM products WHERE id=?", (pid,))
        c.execute("DELETE FROM stock WHERE product_id=?", (pid,))
    shutil.rmtree(folder, ignore_errors=True)
    return jsonify(ok=True)


@app.post("/api/admin/products/<pid>/photo")
@require_admin
def admin_photo(pid):
    f = request.files.get("photo")
    if not f:
        abort(400, "没有收到图片")
    save_jpeg(f, product_dir(pid) / "photo.jpg")
    with db() as c:
        c.execute("UPDATE products SET photo=?, updated_at=? WHERE id=?",
                  (f"/uploads/{pid}/photo.jpg?v={int(datetime.now().timestamp())}", now(), pid))
        r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_json(r))


@app.post("/api/admin/products/<pid>/spin")
@require_admin
def admin_spin(pid):
    frames = request.files.getlist("frames")
    if not 8 <= len(frames) <= 72:
        abort(400, "360 图需要 8 到 72 张照片")
    v = int(datetime.now().timestamp())
    base = product_dir(pid)
    folder = base / f"spin-{v}"
    for i, f in enumerate(frames):
        save_jpeg(f, folder / f"{i:02d}.jpg", max_side=1000)
    for old in base.glob("spin-*"):
        if old != folder:
            shutil.rmtree(old, ignore_errors=True)
    with db() as c:
        c.execute("UPDATE products SET spin_base=?, spin_count=?, updated_at=? WHERE id=?",
                  (f"/uploads/{pid}/spin-{v}", len(frames), now(), pid))
        r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_json(r))


@app.delete("/api/admin/products/<pid>/spin")
@require_admin
def admin_spin_clear(pid):
    for old in product_dir(pid).glob("spin-*"):
        shutil.rmtree(old, ignore_errors=True)
    with db() as c:
        c.execute("UPDATE products SET spin_base=NULL, spin_count=0 WHERE id=?", (pid,))
        r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_json(r))


# ---------------------------------------------------------------- admin: inventory
def inv_call(fn):
    try:
        return fn()
    except ValueError as e:
        abort(400, str(e))
    except LookupError as e:
        abort(404, str(e))


@app.get("/api/admin/inventory")
@require_admin
def admin_inventory():
    with db() as c:
        return jsonify(inv.overview(c))


@app.post("/api/admin/inventory/adjust")
@require_admin
def admin_inventory_adjust():
    b = request.get_json(silent=True) or {}
    with db() as c:
        if "bin" in b:
            inv.set_bin(c, b.get("product_id"), b.get("warehouse_id"), b.get("bin"))
        if b.get("qty") not in (None, ""):
            try:
                q = int(b["qty"])
            except (TypeError, ValueError):
                abort(400, "数量要填整数")
            if not (b.get("reason") or "").strip():
                abort(400, "请写调整原因")
            inv_call(lambda: inv.adjust(c, b.get("product_id"), b.get("warehouse_id"), q, b["reason"].strip()[:40], str(b.get("note") or "")[:200]))
        return jsonify(inv.overview(c))


@app.post("/api/admin/inventory/receive")
@require_admin
def admin_inventory_receive():
    b = request.get_json(silent=True) or {}
    with db() as c:
        inv_call(lambda: inv.receive(c, b.get("warehouse_id"), b.get("lines"), str(b.get("ref") or "")[:40], str(b.get("note") or "")[:200]))
        return jsonify(inv.overview(c))


@app.post("/api/admin/inventory/transfer")
@require_admin
def admin_inventory_transfer():
    b = request.get_json(silent=True) or {}
    ref = str(b.get("ref") or "")[:40] or "DB" + datetime.now(fr.TZ).strftime("%y%m%d%H%M")
    with db() as c:
        inv_call(lambda: inv.transfer(c, b.get("from"), b.get("to"), b.get("lines"), ref, str(b.get("note") or "")[:200]))
        return jsonify(inv.overview(c))


@app.get("/api/admin/movements")
@require_admin
def admin_movements():
    a = request.args
    with db() as c:
        return jsonify(inv.movements(c, a.get("product"), a.get("warehouse"), a.get("kind"), int_arg("limit", 200)))


@app.post("/api/admin/warehouses")
@require_admin
def admin_save_warehouse():
    b = request.get_json(silent=True) or {}
    name = str(b.get("name") or "").strip()[:40]
    if not name:
        abort(400, "请填仓库名称")
    wid = b.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20] or "wh"
    with db() as c:
        exists = c.execute("SELECT 1 FROM warehouses WHERE id=?", (wid,)).fetchone()
        if not b.get("id") and exists:
            wid += "-" + os.urandom(2).hex()
            exists = None
        sort = b.get("sort")
        if sort in (None, ""):
            sort = c.execute("SELECT sort FROM warehouses WHERE id=?", (wid,)).fetchone()[0] if exists else \
                c.execute("SELECT COALESCE(MAX(sort),0)+1 FROM warehouses").fetchone()[0]
        vals = (name, str(b.get("address") or "")[:120], int(bool(b.get("ships", True))), int(sort))
        if exists:
            c.execute("UPDATE warehouses SET name=?, address=?, ships=?, sort=? WHERE id=?", (*vals, wid))
        else:
            c.execute("INSERT INTO warehouses (name, address, ships, sort, id) VALUES (?,?,?,?,?)", (*vals, wid))
        if not c.execute("SELECT 1 FROM warehouses WHERE ships=1").fetchone():
            abort(400, "至少要有一个仓库给顾客发货")
        inv.sync_all(c)
        return jsonify(inv.overview(c))


@app.delete("/api/admin/warehouses/<wid>")
@require_admin
def admin_delete_warehouse(wid):
    with db() as c:
        if c.execute("SELECT COALESCE(SUM(ABS(qty)),0) FROM stock WHERE warehouse_id=?", (wid,)).fetchone()[0]:
            abort(400, "仓库里还有库存，先调拨走再删除")
        if c.execute("SELECT 1 FROM orders WHERE warehouse_id=? AND status='paid'", (wid,)).fetchone():
            abort(400, "还有待发货订单从这个仓库发，先换仓库")
        c.execute("DELETE FROM warehouses WHERE id=?", (wid,))
        c.execute("DELETE FROM stock WHERE warehouse_id=?", (wid,))
        if not c.execute("SELECT 1 FROM warehouses WHERE ships=1").fetchone():
            abort(400, "至少要有一个仓库给顾客发货")
        return jsonify(inv.overview(c))


@app.get("/api/admin/counts")
@require_admin
def admin_counts():
    with db() as c:
        return jsonify(counts=inv.list_counts(c), reasons=inv.REASONS)


@app.post("/api/admin/counts")
@require_admin
def admin_new_count():
    b = request.get_json(silent=True) or {}
    cid = "pd_" + os.urandom(5).hex()
    with db() as c:
        inv_call(lambda: inv.create_count(c, cid, b.get("warehouse_id"), b.get("scope") or "all",
                                          b.get("product_ids"), b.get("blind"), str(b.get("note") or "")))
        return jsonify(inv.count_json(c, cid))


@app.get("/api/admin/counts/<cid>")
@require_admin
def admin_count(cid):
    with db() as c:
        cnt = inv.count_json(c, cid)
    if not cnt:
        abort(404, "找不到这个盘点单")
    return jsonify(cnt)


@app.post("/api/admin/counts/<cid>")
@require_admin
def admin_update_count(cid):
    b = request.get_json(silent=True) or {}
    with db() as c:
        inv_call(lambda: inv.update_count(c, cid, b.get("lines"), b.get("action") or "save"))
        return jsonify(inv.count_json(c, cid))


# ---------------------------------------------------------------- admin: email
@app.get("/api/admin/mail")
@require_admin
def admin_mail():
    with db() as c:
        return jsonify(mailer.public(mailer.load(c, STORE["email"])))


@app.post("/api/admin/mail")
@require_admin
def admin_mail_save():
    with db() as c:
        try:
            s = mailer.clean(request.get_json(silent=True) or {}, mailer.load(c, STORE["email"]))
        except ValueError as e:
            abort(400, str(e))
        c.execute("INSERT INTO settings (key, value) VALUES ('mail', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(s),))
        return jsonify(mailer.public(s))


@app.post("/api/admin/mail/test")
@require_admin
def admin_mail_test():
    to = str((request.get_json(silent=True) or {}).get("to") or "").strip()
    if "@" not in to:
        abort(400, "请填一个收件邮箱")
    with db() as c:
        s = mailer.load(c, STORE["email"])
    if not (s.get("host") and s.get("from_addr")):
        abort(400, "先填好发信服务器和发件地址并保存")
    L = mailer.STAFF.get(s.get("staff_lang", "zh"), mailer.STAFF["zh"])
    try:
        mailer.deliver(s, to, L["test"], L["test_body"])
        status, err = "sent", None
    except Exception as e:
        status, err = "failed", str(e)[:300]
    with db() as c:
        c.execute("INSERT INTO outbox (created_at, to_addr, subject, body, kind, status, error, sent_at, attempts) VALUES (?,?,?,?,?,?,?,?,1)",
                  (now(), to, L["test"], L["test_body"], "test", status, err, now() if status == "sent" else None))
    if err:
        abort(400, f"发送失败：{err}")
    return jsonify(ok=True)


@app.get("/api/admin/emails")
@require_admin
def admin_emails():
    st = request.args.get("status")
    q, args = "SELECT x.*, o.order_no FROM outbox x LEFT JOIN orders o ON o.id=x.order_id", []
    if st in ("queued", "sending", "sent", "failed", "not_configured", "held"):
        q, args = q + " WHERE x.status=?", [st]
    with db() as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY x.id DESC LIMIT 300", args)]
        counts = dict(c.execute("SELECT status, COUNT(*) FROM outbox GROUP BY status").fetchall())
    return jsonify(emails=rows, counts=counts)


@app.post("/api/admin/emails/<int:eid>/resend")
@require_admin
def admin_email_resend(eid):
    with db() as c:
        r = c.execute("SELECT * FROM outbox WHERE id=?", (eid,)).fetchone()
        if not r:
            abort(404, "找不到这封邮件")
        s = mailer.load(c, STORE["email"])
        res = mailer.queue(c, s, r["to_addr"], r["subject"], r["body"], r["kind"], r["order_id"])
    return jsonify(ok=True, status=res)


# ---------------------------------------------------------------- admin: to-dos & notes
def run_reminders():
    """Every minute: automatic to-dos, due to-do emails, tomorrow's delivery reminders, daily backup."""
    with db() as c:
        s = mailer.load(c, STORE["email"])
        due = tasks.run_engine(c, bool(s.get("notify_todos")), today=fr.today())
        L = mailer.STAFF.get(s.get("staff_lang", "zh"), mailer.STAFF["zh"])
        for t in due:
            if not c.execute("UPDATE todos SET reminded_at=? WHERE id=? AND reminded_at IS NULL", (now(), t["id"])).rowcount:
                continue  # another worker already sent this one
            if s.get("notify_todos"):
                local = tasks.parse(t["due_at"]).astimezone(fr.TZ)
                lines = [f"{L['due']} {local:%Y-%m-%d %H:%M} ({fr.TZ.key})"]
                if t["order_no"]:
                    lines.append(f"{L['order']} {t['order_no']} · {t['ship_name'] or ''}")
                if t["note"]:
                    lines += ["", t["note"]]
                lines += ["", L["open"].format(url=f"{DOMAIN}/admin.html")]
                notify_staff(c, "todo", lines, order_id=t["order_id"], title=t["title"])
    if store_hour() >= REMINDER_HOUR:  # customers hear about tomorrow's delivery once, after 10 AM
        send_reminders()
    try:
        social.flush(db, DOMAIN, get_product)
    except Exception as e:  # a platform being down must not stop reminders
        print("social posts:", e)
    try:
        backup.daily(DB_PATH, BACKUP_DIR, keep=BACKUP_KEEP)
    except Exception as e:  # a full disk must not stop reminders
        print("backup failed:", e)
    return len(due)


_last_engine = [0.0]


@app.get("/api/admin/todos")
@require_admin
def admin_todos():
    with db() as c:
        return jsonify(todos=tasks.list_todos(c, request.args.get("status", "open")), summary=tasks.summary(c))


@app.get("/api/admin/todos/summary")
@require_admin
def admin_todos_summary():
    import time as _t
    if _t.time() - _last_engine[0] > 30:
        _last_engine[0] = _t.time()
        run_reminders()
    with db() as c:
        return jsonify(tasks.summary(c))


@app.post("/api/admin/todos")
@require_admin
def admin_save_todo():
    b = request.get_json(silent=True) or {}
    with db() as c:
        try:
            tid = tasks.save_todo(c, b)
        except ValueError as e:
            abort(400, str(e))
        r = c.execute("SELECT * FROM todos WHERE id=?", (tid,)).fetchone()
        if r["order_id"] and not b.get("id"):
            log_event(c, r["order_id"], "todo", f"✅ 新待办：{r['title']}")
        if "status" in b and r["order_id"]:
            log_event(c, r["order_id"], "todo", f"{'☑ 完成待办' if b['status'] == 'done' else '↩️ 待办重新打开'}：{r['title']}")
        return jsonify(tasks.todo_json(c.execute(
            "SELECT t.*, o.order_no, o.ship_name FROM todos t LEFT JOIN orders o ON o.id=t.order_id WHERE t.id=?", (tid,)).fetchone()))


@app.delete("/api/admin/todos/<tid>")
@require_admin
def admin_delete_todo(tid):
    with db() as c:
        c.execute("UPDATE notes SET todo_id=NULL WHERE todo_id=?", (tid,))
        c.execute("DELETE FROM todos WHERE id=?", (tid,))
    return jsonify(ok=True)


@app.post("/api/admin/orders/<oid>/notes")
@require_admin
def admin_add_note(oid):
    b = request.get_json(silent=True) or {}
    with db() as c:
        if not c.execute("SELECT 1 FROM orders WHERE id=?", (oid,)).fetchone():
            abort(404, "找不到这个订单")
        remind = b.get("remind_at") or None
        if remind and not tasks.parse(remind):
            abort(400, "提醒时间格式不对")
        try:
            tasks.add_note(c, oid, b.get("text"), b.get("print", True), tasks.parse(remind).isoformat(timespec="seconds") if remind else None, b.get("email"))
        except ValueError as e:
            abort(400, str(e))
        log_event(c, oid, "note", "📝 加了一条备注" + ("（带提醒）" if remind else ""))
    return admin_order(oid)


@app.post("/api/admin/orders/<oid>/notes/<int:nid>")
@require_admin
def admin_update_note(oid, nid):
    b = request.get_json(silent=True) or {}
    with db() as c:
        if "print" in b:
            c.execute("UPDATE notes SET print=? WHERE id=? AND order_id=?", (int(bool(b["print"])), nid, oid))
    return admin_order(oid)


@app.delete("/api/admin/orders/<oid>/notes/<int:nid>")
@require_admin
def admin_delete_note(oid, nid):
    with db() as c:
        r = c.execute("SELECT todo_id FROM notes WHERE id=? AND order_id=?", (nid, oid)).fetchone()
        if r and r["todo_id"]:
            c.execute("DELETE FROM todos WHERE id=? AND status='open'", (r["todo_id"],))
        c.execute("DELETE FROM notes WHERE id=? AND order_id=?", (nid, oid))
        log_event(c, oid, "note", "🗑️ 删了一条备注")
    return admin_order(oid)


# ---------------------------------------------------------------- admin: housekeeping
@app.post("/api/admin/test-orders/purge")
@require_admin
def admin_purge_test_orders():
    """Delete every test order before going live; stock that test shipments took out goes back."""
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute("SELECT * FROM orders WHERE test=1").fetchall()
        for r in rows:
            if r["stock_out"] and r["warehouse_id"]:
                for it in json.loads(r["items_json"]):
                    inv.move(c, it["id"], r["warehouse_id"], it["qty"], "ship_reverse", r["order_no"], "删除测试订单")
        ids = [r["id"] for r in rows]
        for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
            marks = ",".join("?" * len(chunk))
            for table, col in (("events", "order_id"), ("notes", "order_id"), ("todos", "order_id"), ("outbox", "order_id"),
                               ("orders", "id")):
                c.execute(f"DELETE FROM {table} WHERE {col} IN ({marks})", chunk)
        inv.sync_all(c)
    return jsonify(deleted=len(ids))


@app.get("/api/admin/backup")
@require_admin
def admin_backup():
    """Download everything needed to restore the shop: the database plus uploaded photos."""
    data = backup.zip_bytes(DB_PATH, UPLOAD_DIR)
    name = f"mujian-backup-{datetime.now(fr.TZ):%Y%m%d-%H%M}.zip"
    return app.response_class(data, mimetype="application/zip",
                              headers={"Content-Disposition": f"attachment; filename={name}"})


@app.get("/api/admin/backups")
@require_admin
def admin_backups():
    return jsonify(dir=BACKUP_DIR, keep=BACKUP_KEEP, files=backup.listing(BACKUP_DIR))


init_db()
app.secret_key = os.getenv("SECRET_KEY") or persistent_secret()
if os.getenv("MAIL_WORKER", "1") != "0":
    mailer.start_worker(db, STORE["email"], tick=run_reminders)

# ---------------------------------------------------------------- admin: social media
@app.get("/api/admin/social")
@require_admin
def admin_social():
    with db() as c:
        s = social.load(c)
        no_photo = [p["name"] for p in (product_json(r) for r in c.execute("SELECT * FROM products WHERE active=1")) if not social.image_path(p)]
    return jsonify(**social.public(s), feed=f"{DOMAIN}/feeds/products.xml", share_base=f"{DOMAIN}/p/", local=LOCAL, no_photo=no_photo)


@app.post("/api/admin/social")
@require_admin
def admin_social_save():
    with db() as c:
        try:
            s = social.clean(request.get_json(silent=True) or {}, social.load(c))
        except ValueError as e:
            abort(400, str(e))
        c.execute("INSERT INTO settings (key, value) VALUES ('social', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(s),))
    return admin_social()


@app.post("/api/admin/social/check")
@require_admin
def admin_social_check():
    with db() as c:
        s = social.load(c)
    before = s.get("ig_user_id")
    res = social.check(s)
    if s.get("ig_user_id") != before:  # found the Instagram account through the Facebook page
        with db() as c:
            cur = social.load(c)
            cur["ig_user_id"] = s["ig_user_id"]
            c.execute("UPDATE settings SET value=? WHERE key='social'", (json.dumps(cur),))
    if not res:
        abort(400, "还没填任何平台的账号信息")
    return jsonify(results=res)


@app.post("/api/admin/social/post")
@require_admin
def admin_social_post():
    b = request.get_json(silent=True) or {}
    targets = [t for t in (b.get("targets") or []) if t in social.TARGETS]
    text = str(b.get("text") or "").strip()[:2000]
    pid = b.get("product_id") or None
    if not targets:
        abort(400, "选一个要发的平台")
    if not text and not pid:
        abort(400, "写点内容，或者选一个商品")
    if LOCAL:
        abort(400, "网站还在本机（localhost），社交平台拿不到图片和链接。部署上线后再发")
    with db() as c:
        if pid and not get_product(c, pid):
            abort(404, "找不到商品")
        ready = social.configured(social.load(c))
        bad = [t for t in targets if not ready[t]]
        if bad:
            abort(400, "还没接好：" + "、".join(bad))
        qid = social.queue(c, targets, text=text, product_id=pid)
    if not app.config.get("TESTING"):
        threading.Thread(target=lambda: social.flush(db, DOMAIN, get_product), daemon=True).start()
    return jsonify(id=qid)


@app.get("/api/admin/social/posts")
@require_admin
def admin_social_posts():
    with db() as c:
        return jsonify(posts=social.recent(c))


@app.get("/api/admin/social/caption/<pid>")
@require_admin
def admin_social_caption(pid):
    """Ready-made text (for TikTok, X or anywhere you post by hand)."""
    with db() as c:
        p = get_product(c, pid)
        s = social.load(c)
    if not p:
        abort(404, "找不到商品")
    return jsonify(text=social.caption(p, f"{DOMAIN}/p/{pid}", s), link=f"{DOMAIN}/p/{pid}",
                   image=DOMAIN + (social.image_path(p) or f"/og/{pid}.jpg"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reminders":
        print("delivery reminders:", send_reminders() or "nothing for tomorrow")
        print("due to-dos:", run_reminders())
        print("emails sent:", mailer.flush(db, STORE["email"]))
        sys.exit(0)
    print(f"🛍️  Store:  {DOMAIN}/\n🧭 Admin:  {DOMAIN}/admin.html")
    if DEMO_CHECKOUT:
        print("🧪 No STRIPE_SECRET_KEY set: checkout creates paid test orders (demo mode).")
    if password_problem():
        print("⚠️ ", password_problem())
    if not PAYMENTS_READY:
        print("⚠️  No STRIPE_SECRET_KEY: checkout is turned off. Add the key, or DEMO_MODE=1 for a demo site.")
    app.run(port=int(os.getenv("PORT", 4242)), debug=os.getenv("FLASK_DEBUG") == "1")
