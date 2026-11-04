"""
Mujian Furniture — storefront + admin backend (Flask, SQLite, Stripe Checkout).

Run:
    pip install -r requirements.txt
    cp .env.example .env        # set ADMIN_PASSWORD (Stripe TEST keys optional)
    python server.py            # store: http://localhost:4242   admin: http://localhost:4242/admin.html
    python server.py reminders  # email tomorrow's delivery reminders (run daily from cron)

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
import smtplib
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

import stripe
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request, send_from_directory, session
from PIL import Image, ImageOps

load_dotenv()
import freight as fr  # noqa: E402  (reads STORE_TZ from .env)
import inventory as inv  # noqa: E402

BASE = Path(__file__).resolve().parent

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
DOMAIN = os.getenv("DOMAIN", "http://localhost:4242").rstrip("/")
DB_PATH = os.getenv("DB_PATH", str(BASE / "shop.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE / "uploads")))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
DEMO_CHECKOUT = not stripe.api_key  # no Stripe key → checkout creates a paid test order

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

SAMPLE_CUSTOMERS = [
    ("Emily Carter", "emily.carter@example.com", "(415) 555-0142", "742 Valencia St", "Apt 3", "San Francisco", "CA", "94110"),
    ("Maya Patel", "maya.patel@example.com", "(510) 555-0187", "2420 Telegraph Ave", "", "Berkeley", "CA", "94704"),
    ("James Nguyen", "james.nguyen@example.com", "(206) 555-0199", "1520 E Pike St", "", "Seattle", "WA", "98122"),
    ("Sofia Ramirez", "sofia.r@example.com", "(512) 555-0173", "908 W 34th St", "Unit B", "Austin", "TX", "78705"),
    ("Daniel Kim", "dkim@example.com", "(718) 555-0128", "215 Bedford Ave", "Fl 2", "Brooklyn", "NY", "11211"),
    ("Grace Liu", "grace.liu@example.com", "(626) 555-0116", "88 E Colorado Blvd", "", "Pasadena", "CA", "91105"),
]

PRODUCT_COLS = {"ship_mode": "TEXT DEFAULT 'freight'", "weight_lb": "INTEGER DEFAULT 0", "box_l": "REAL", "box_w": "REAL",
                "box_h": "REAL", "freight_class": "TEXT", "sku": "TEXT"}
ORDER_COLS = {"fulfillment": "TEXT DEFAULT 'parcel'", "service": "TEXT", "delivery_type": "TEXT", "freight_status": "TEXT",
              "appt_date": "TEXT", "appt_window": "TEXT", "sched_token": "TEXT", "scheduled_at": "TEXT",
              "home_type": "TEXT", "floor": "INTEGER", "elevator": "INTEGER", "liftgate": "INTEGER", "access_notes": "TEXT",
              "truck": "TEXT", "stop_no": "INTEGER", "pro": "TEXT", "freight_carrier": "TEXT", "pickup_date": "TEXT",
              "pallets": "INTEGER", "weight_lb": "INTEGER", "freight_class": "TEXT", "reminded_for": "TEXT",
              "warehouse_id": "TEXT", "stock_out": "INTEGER DEFAULT 0"}


# ------------------------------------------------------------------ helpers
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def add_columns(c, table, cols):
    have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    for name, decl in cols.items():
        if name not in have:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db():
    with db() as c:
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
    return {**{k: r[k] for k in keys}, "active": bool(r["active"]), "spin_frames": frames}


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
    n = c.execute("SELECT COUNT(*) FROM orders WHERE order_no LIKE ?", (f"MJ{day}-%",)).fetchone()[0]
    return f"MJ{day}-{n + 1:03d}"


def cents(n):
    return f"${n / 100:,.2f}"


def send_email(to, subject, body):
    """Send via SMTP if configured. Returns a short status string for the order timeline."""
    host = os.getenv("SMTP_HOST")
    if not host or not to:
        return "未配置邮件服务，邮件未发送（已记录内容）"
    msg = EmailMessage()
    msg["From"] = os.getenv("MAIL_FROM", STORE["email"])
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", 587)), timeout=15) as s:
            s.starttls()
            if os.getenv("SMTP_USER"):
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS", ""))
            s.send_message(msg)
        return f"已发送到 {to}"
    except Exception as e:  # keep the status change even if email fails
        return f"发送失败：{e}"


def shipped_email(o):
    lines = [f"Hi {o['ship_name'] or 'there'},", "", f"Good news: your Mujian order {o['order_no']} is on its way."]
    if o["carrier"] and o["tracking"]:
        lines.append(f"Carrier: {o['carrier']}   Tracking number: {o['tracking']}")
        if o["tracking_url"]:
            lines.append(f"Track it here: {o['tracking_url']}")
    lines += ["", "Items:"] + [f"  • {i['name']} × {i['qty']}" for i in o["items"]]
    lines += ["", "Thank you for shopping with us.", STORE["name"]]
    return "\n".join(lines)


def booking_email(o):
    return "\n".join([
        f"Hi {o['ship_name'] or 'there'},", "",
        f"Thanks for your order {o['order_no']}. Your furniture ships by truck, so we need you to pick a delivery day.",
        f"Book your delivery window here: {DOMAIN}/{schedule_path(o)}", "",
        "Someone 18 or older needs to be home to sign for the delivery.", "", STORE["name"]])


def appt_email(o, settings, kind="booked"):
    d = date.fromisoformat(o["appt_date"])
    when = f"{d.strftime('%A, %B')} {d.day}, {fr.window_label(settings, o['appt_window'])}"
    head = {"booked": "Your delivery is booked:", "changed": "Your delivery time has changed:",
            "reminder": "Reminder: your furniture arrives tomorrow:"}[kind]
    lines = [f"Hi {o['ship_name'] or 'there'},", "", head, f"  {when}", f"  {o['ship_line1']}, {o['ship_city']}"]
    if o["delivery_type"] == "ltl":
        lines += ["", settings["ltl_note"]]
    lines += ["", "Please keep the path from the truck to the room clear, and make sure someone 18+ is home.",
              f"Need a different time? {DOMAIN}/{schedule_path(o)}", "", STORE["name"]]
    return "\n".join(lines)


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
                  ship_zip=info.get("zip"), service=service, ship_method=fr.SERVICES[service]["en"], **extra)
    c.execute(f"UPDATE orders SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), order_id))
    wh = inv.allocate(c, items)
    c.execute("UPDATE orders SET warehouse_id=? WHERE id=?", (wh, order_id))
    for it in items:
        inv.sync_product(c, it["id"])
    log_event(c, order_id, "paid", f"已付款 {cents(info['total'])}")
    if extra:
        o = order_json(c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())
        kind = "本地车队" if extra["delivery_type"] == "local" else "LTL 承运商"
        log_event(c, order_id, "freight", f"🚛 卡派订单：{kind}，{extra['pallets']} 托 / {extra['weight_lb']} lb / Class {extra['freight_class']}")
        res = send_email(o['email'], f"Book your delivery for order {o['order_no']}", booking_email(o))
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


def create_order(c, items, subtotal, order_id, test=False):
    no = next_order_no(c)
    fulfillment = "freight" if any(i["ship_mode"] == "freight" for i in items) else "parcel"
    c.execute(
        "INSERT INTO orders (id, order_no, status, items_json, subtotal, test, created_at, fulfillment) VALUES (?,?,?,?,?,?,?,?)",
        (order_id, no, "pending", json.dumps(items), subtotal, int(test), now(), fulfillment),
    )
    log_event(c, order_id, "created", "测试订单已创建" if test else "顾客发起结账")
    return no, fulfillment


def shipping_choices(fulfillment, subtotal):
    """(service, amount, label, min days, max days) offered at checkout."""
    free = subtotal >= fr.FREE_OVER
    if fulfillment == "parcel":
        return [("standard", 0 if free else fr.PARCEL_FEE, "Free standard delivery" if free else "Standard delivery", 3, 7)]
    return [("curbside", 0 if free else fr.CURBSIDE_FEE, fr.SERVICES["curbside"]["en"] + (" · free" if free else ""), 5, 14),
            ("threshold", fr.THRESHOLD_FEE, fr.SERVICES["threshold"]["en"], 5, 14),
            ("white_glove", fr.WHITE_GLOVE_FEE, fr.SERVICES["white_glove"]["en"], 7, 21)]


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


def paid_test_order(c, items, subtotal, oid, customer=None, service=None):
    no, fulfillment = create_order(c, items, subtotal, oid, test=True)
    name, email, phone, l1, l2, city, st, z = customer or random.choice(SAMPLE_CUSTOMERS)
    choices = shipping_choices(fulfillment, subtotal)
    pick = next((x for x in choices if x[0] == service), None) or random.choice(choices)
    mark_paid(c, oid, dict(total=subtotal + pick[1], shipping=pick[1], email=email, phone=phone, name=name,
                           line1=l1, line2=l2, city=city, state=st, zip=z))


# ------------------------------------------------------------------ app
app = Flask(__name__, static_folder="public", static_url_path="")
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(32)
app.config.update(MAX_CONTENT_LENGTH=60 * 1024 * 1024, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=DOMAIN.startswith("https"))


@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(404)
def json_error(e):
    return jsonify(error=getattr(e, "description", str(e))), e.code


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


@app.get("/uploads/<path:p>")
def uploads(p):
    return send_from_directory(UPLOAD_DIR, p, max_age=3600)


# ---------------------------------------------------------------- storefront
@app.get("/api/store")
def store_info():
    return jsonify(**STORE, demo_checkout=DEMO_CHECKOUT)


@app.get("/api/products")
def products():
    with db() as c:
        rows = c.execute("SELECT * FROM products WHERE active=1 ORDER BY sort, name").fetchall()
    return jsonify([product_json(r) for r in rows])


@app.post("/api/checkout")
def checkout():
    body = request.get_json(silent=True) or {}
    with db() as c:
        try:
            items, subtotal = validate_cart(c, body.get("items"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if DEMO_CHECKOUT:
            oid = "demo_" + os.urandom(6).hex()
            paid_test_order(c, items, subtotal, oid)
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
                for _, amt, label, lo, hi in shipping_choices(fulfillment, subtotal)],
            phone_number_collection={"enabled": True},
            # automatic_tax={"enabled": True},   # turn on after enabling Stripe Tax
            success_url=f"{DOMAIN}/success.html?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN}/?canceled=1",
        )
    except stripe.StripeError as e:
        return jsonify(error=e.user_message or str(e)), 502
    with db() as c:
        create_order(c, items, subtotal, s.id)
    return jsonify(url=s.url)


@app.post("/webhook")
def webhook():
    payload = request.get_data()
    try:
        stripe.Webhook.construct_event(payload, request.headers.get("Stripe-Signature", ""), WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        abort(400)
    event = json.loads(payload)
    obj = event["data"]["object"]
    with db() as c:
        if event["type"] == "checkout.session.completed" and obj.get("payment_status") == "paid":
            ship = (obj.get("collected_information") or {}).get("shipping_details") or obj.get("shipping_details") or {}
            a = ship.get("address") or {}
            cust = obj.get("customer_details") or {}
            mark_paid(c, obj["id"], dict(
                total=obj.get("amount_total"), shipping=(obj.get("total_details") or {}).get("amount_shipping", 0),
                email=cust.get("email"), phone=cust.get("phone"), name=ship.get("name"), line1=a.get("line1"),
                line2=a.get("line2"), city=a.get("city"), state=a.get("state"), zip=a.get("postal_code")))
        elif event["type"] == "checkout.session.expired":
            c.execute("UPDATE orders SET status='canceled' WHERE id=? AND status='pending'", (obj["id"],))
            log_event(c, obj["id"], "canceled", "顾客未完成付款，结账已过期")
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
        items=[{"name": i["name"], "qty": i["qty"], "freight": i.get("ship_mode") == "freight"} for i in o["items"]],
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
        res = send_email(o['email'], f"Delivery booked for order {o['order_no']}", appt_email(o, settings, 'changed' if changed else 'booked'))
        log_event(c, oid, "email", f"📧 预约确认邮件：{res}")
        return jsonify(schedule_payload(c, c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(), settings))


# ---------------------------------------------------------------- admin: auth
@app.get("/api/admin/me")
def admin_me():
    return jsonify(admin=bool(session.get("admin")), configured=bool(ADMIN_PASSWORD), demo_checkout=DEMO_CHECKOUT)


@app.post("/api/admin/login")
def admin_login():
    if not ADMIN_PASSWORD:
        abort(403, "服务器还没设置 ADMIN_PASSWORD，请在 .env 里设置后重启")
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if not pw or not hmac.compare_digest(pw, ADMIN_PASSWORD):
        abort(401, "密码不对")
    session["admin"] = True
    session.permanent = True
    return jsonify(ok=True)


@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return jsonify(ok=True)


# ---------------------------------------------------------------- admin: orders
@app.get("/api/admin/orders")
@require_admin
def admin_orders():
    status = request.args.get("status")
    q, args = "SELECT * FROM orders", []
    if status in STATUSES:
        q, args = q + " WHERE status=?", [status]
    elif status == "unscheduled":
        q = q + " WHERE status='paid' AND freight_status='unscheduled'"
    with db() as c:
        settings = fr.load_settings(c)
        rows = c.execute(q + " ORDER BY created_at DESC LIMIT 500", args).fetchall()
        counts = dict(c.execute("SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall())
        counts["unscheduled"] = c.execute("SELECT COUNT(*) FROM orders WHERE freight_status='unscheduled' AND status='paid'").fetchone()[0]
    return jsonify(orders=[order_json(r, settings) for r in rows], counts=counts)


@app.get("/api/admin/orders/<oid>")
@require_admin
def admin_order(oid):
    with db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        settings = fr.load_settings(c)
    if not r:
        abort(404, "找不到这个订单")
    return jsonify(order_json(r, settings, with_events=True))


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
            res = send_email(o['email'], f"Delivery update for order {o['order_no']}", appt_email(o, settings, kind))
            log_event(c, oid, "email", f"📧 预约通知邮件：{res}")
        if sets.get("status") == "shipped" and r["status"] != "shipped":
            res = send_email(o['email'], f"Your order {o['order_no']} is on its way", shipped_email(o))
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
        if "note" in body:
            c.execute("UPDATE orders SET note=? WHERE id=?", (str(body["note"])[:2000], oid))
            log_event(c, oid, "note", "更新了备注")
        if "carrier" in body or "tracking" in body:
            carrier = body.get("carrier", r["carrier"]) or ""
            tracking = re.sub(r"\s+", "", body.get("tracking", r["tracking"]) or "")
            if carrier and carrier not in CARRIERS:
                abort(400, "不支持的快递公司")
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
            stamp = {"shipped": "shipped_at", "delivered": "delivered_at"}.get(new)
            c.execute(f"UPDATE orders SET status=?{', ' + stamp + '=?' if stamp else ''} WHERE id=?",
                      (new, now(), oid) if stamp else (new, oid))
            labels = {"shipped": "标记为已发货", "delivered": "标记为已送达", "canceled": "订单已取消（占用的库存已释放）",
                      "paid": "退回到待发货"}
            log_event(c, oid, new, labels[new])
            if new == "shipped":
                o = order_json(c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
                res = send_email(o['email'], f"Your order {o['order_no']} has shipped", shipped_email(o))
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
        res = send_email(o['email'], f"Book your delivery for order {o['order_no']}", booking_email(o))
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
    days = max(1, min(42, int(request.args.get("days", 14))))
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
            o = order_json(r, settings)
            res = send_email(o["email"], f"Your delivery is tomorrow ({o['order_no']})", appt_email(o, settings, "reminder"))
            c.execute("UPDATE orders SET reminded_for=? WHERE id=?", (tomorrow, r["id"]))
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
    with db() as c:
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
        r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_json(r))


@app.delete("/api/admin/products/<pid>")
@require_admin
def admin_delete_product(pid):
    with db() as c:
        held = c.execute("SELECT COALESCE(SUM(qty),0) FROM stock WHERE product_id=?", (pid,)).fetchone()[0]
        if held:
            abort(400, f"还有 {held} 件库存，先在库存里调整为 0，或者改成下架")
        c.execute("DELETE FROM products WHERE id=?", (pid,))
        c.execute("DELETE FROM stock WHERE product_id=?", (pid,))
    shutil.rmtree(UPLOAD_DIR / pid, ignore_errors=True)
    return jsonify(ok=True)


@app.post("/api/admin/products/<pid>/photo")
@require_admin
def admin_photo(pid):
    f = request.files.get("photo")
    if not f:
        abort(400, "没有收到图片")
    save_jpeg(f, UPLOAD_DIR / pid / "photo.jpg")
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
    folder = UPLOAD_DIR / pid / f"spin-{v}"
    for i, f in enumerate(frames):
        save_jpeg(f, folder / f"{i:02d}.jpg", max_side=1000)
    for old in (UPLOAD_DIR / pid).glob("spin-*"):
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
    for old in (UPLOAD_DIR / pid).glob("spin-*"):
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
        return jsonify(inv.movements(c, a.get("product"), a.get("warehouse"), a.get("kind"), a.get("limit", 200)))


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


init_db()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reminders":
        print("sent:", send_reminders() or "nothing for tomorrow")
        sys.exit(0)
    print(f"🛍️  Store:  {DOMAIN}/\n🧭 Admin:  {DOMAIN}/admin.html")
    if DEMO_CHECKOUT:
        print("🧪 No STRIPE_SECRET_KEY set: checkout creates paid test orders (demo mode).")
    if not ADMIN_PASSWORD:
        print("⚠️  ADMIN_PASSWORD is not set; admin login is disabled until you add it to .env")
    app.run(port=int(os.getenv("PORT", 4242)), debug=os.getenv("FLASK_DEBUG") == "1")
