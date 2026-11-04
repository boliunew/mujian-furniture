"""
Mujian Furniture — storefront + admin backend (Flask, SQLite, Stripe Checkout).

Run:
    pip install -r requirements.txt
    cp .env.example .env        # fill in ADMIN_PASSWORD (and Stripe TEST keys if you have them)
    python server.py            # store: http://localhost:4242   admin: http://localhost:4242/admin.html

Without Stripe keys the store runs in demo checkout mode: "Checkout" creates a
paid test order immediately, so you can try the whole admin flow locally.
"""
import io
import json
import os
import random
import re
import shutil
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

import stripe
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request, send_from_directory, session
from PIL import Image, ImageOps

load_dotenv()
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
}

FREE_SHIPPING_CENTS = 50000
STANDARD_SHIPPING_CENTS = 4900
WHITE_GLOVE_CENTS = 14900
MAX_QTY_PER_ITEM = 10
STATUSES = ["pending", "paid", "shipped", "delivered", "canceled"]
CARRIERS = {
    "USPS": "https://tools.usps.com/go/TrackConfirmAction?tLabels={}",
    "UPS": "https://www.ups.com/track?tracknum={}",
    "FedEx": "https://www.fedex.com/fedextrack/?trknbr={}",
    "Freight": "",
}

SEED_PRODUCTS = [
    ("s1", "Haven 3-Seat Sofa", "Living", "sofa", 189900, '83" W × 36" D × 31" H', "White oak frame · linen blend", "2–3 weeks", 6),
    ("c1", "Sit Walnut Dining Chair", "Dining", "chair", 34900, '18" W × 20" D × 31" H', "Black walnut · vegetable-tanned leather", "1 week", 24),
    ("t1", "Gather 6-Seat Dining Table", "Dining", "table", 149900, '71" W × 35" D × 30" H', "Solid white oak · hardwax oil", "3–4 weeks", 4),
    ("b1", "Rest Low Platform Bed, Queen", "Bedroom", "bed", 179900, '64" W × 85" D × 35" H', "Cherry · mortise-and-tenon joints", "4 weeks", 3),
    ("sh1", "Keep 3-Tier Open Bookshelf", "Study", "shelf", 64900, '35" W × 13" D × 59" H', "White oak · adjustable shelves", "2 weeks", 10),
    ("l1", "Glow Brass Floor Lamp", "Lighting", "lamp", 39900, '16" W × 16" D × 62" H', "Brass shade · E26 socket", "1 week", 15),
    ("tv1", "Still 2-Door Media Console", "Living", "cabinet", 99900, '71" W × 17" D × 22" H', "Black walnut · push-to-open doors", "3 weeks", 5),
    ("t2", "Pause Round Side Table", "Living", "table", 22900, '20" Ø × 22" H', "White oak · solid legs", "1 week", 2),
]
DEMO_SPIN = {"tv1": ("/demo-spin/tv1", 24)}  # sample 360 frames shipped with the repo

SAMPLE_CUSTOMERS = [
    ("Emily Carter", "emily.carter@example.com", "(415) 555-0142", "742 Valencia St", "Apt 3", "San Francisco", "CA", "94110"),
    ("James Nguyen", "james.nguyen@example.com", "(206) 555-0199", "1520 E Pike St", "", "Seattle", "WA", "98122"),
    ("Sofia Ramirez", "sofia.r@example.com", "(512) 555-0173", "908 W 34th St", "Unit B", "Austin", "TX", "78705"),
    ("Daniel Kim", "dkim@example.com", "(718) 555-0128", "215 Bedford Ave", "Fl 2", "Brooklyn", "NY", "11211"),
    ("Grace Liu", "grace.liu@example.com", "(626) 555-0116", "88 E Colorado Blvd", "", "Pasadena", "CA", "91105"),
]


# ------------------------------------------------------------------ helpers
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
            """
        )
        if c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            for i, p in enumerate(SEED_PRODUCTS):
                base, count = DEMO_SPIN.get(p[0], (None, 0))
                c.execute(
                    "INSERT INTO products (id,name,cat,art,price,dim,mat,lead,stock,sort,spin_base,spin_count,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*p, i, base, count, now()),
                )


def product_json(r):
    frames = [f"{r['spin_base']}/{i:02d}.jpg" for i in range(r["spin_count"] or 0)] if r["spin_base"] else []
    keys = ["id", "name", "cat", "art", "price", "dim", "mat", "lead", "stock", "active", "photo", "sort", "updated_at"]
    return {**{k: r[k] for k in keys}, "active": bool(r["active"]), "spin_frames": frames}


def order_json(r, with_events=False):
    o = dict(r)
    o["items"] = json.loads(o.pop("items_json"))
    o["test"] = bool(o["test"])
    tpl = CARRIERS.get(o.get("carrier") or "")
    o["tracking_url"] = tpl.format(o["tracking"]) if tpl and o.get("tracking") else ""
    if with_events:
        with db() as c:
            o["events"] = [dict(e) for e in c.execute(
                "SELECT at, kind, text FROM events WHERE order_id=? ORDER BY id", (o["id"],))]
    return o


def log_event(c, order_id, kind, text):
    c.execute("INSERT INTO events (order_id, at, kind, text) VALUES (?,?,?,?)", (order_id, now(), kind, text))


def next_order_no(c):
    day = datetime.now().strftime("%y%m%d")
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


def mark_paid(c, order_id, info):
    """Shared by the Stripe webhook, demo checkout and test orders. Decrements stock once."""
    row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row or row["status"] != "pending":
        return
    c.execute(
        """UPDATE orders SET status='paid', paid_at=?, total=?, shipping=?, email=?, phone=?, ship_name=?,
           ship_line1=?, ship_line2=?, ship_city=?, ship_state=?, ship_zip=?, ship_method=? WHERE id=?""",
        (now(), info["total"], info["shipping"], info.get("email"), info.get("phone"), info.get("name"),
         info.get("line1"), info.get("line2"), info.get("city"), info.get("state"), info.get("zip"),
         info.get("method"), order_id),
    )
    for it in json.loads(row["items_json"]):
        c.execute("UPDATE products SET stock = MAX(stock - ?, 0) WHERE id=?", (it["qty"], it["id"]))
    log_event(c, order_id, "paid", f"已付款 {cents(info['total'])}")


def ship_method_for(subtotal, shipping):
    if shipping == WHITE_GLOVE_CENTS:
        return "White-glove delivery & assembly"
    return "Free standard delivery" if shipping == 0 else "Standard delivery"


def validate_cart(c, items):
    clean, subtotal = [], 0
    for it in items or []:
        pid, qty = it.get("id"), it.get("qty")
        p = c.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY_PER_ITEM:
            raise ValueError(f"Invalid item in cart: {pid}")
        if p["stock"] < qty:
            raise ValueError(f"Only {p['stock']} left of {p['name']}.")
        clean.append({"id": pid, "name": p["name"], "price": p["price"], "qty": qty})
        subtotal += p["price"] * qty
    if not clean:
        raise ValueError("Your cart is empty.")
    return clean, subtotal


def create_order(c, items, subtotal, order_id, test=False):
    no = next_order_no(c)
    c.execute(
        "INSERT INTO orders (id, order_no, status, items_json, subtotal, test, created_at) VALUES (?,?,?,?,?,?,?)",
        (order_id, no, "pending", json.dumps(items), subtotal, int(test), now()),
    )
    log_event(c, order_id, "created", "测试订单已创建" if test else "顾客发起结账")
    return no


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
            create_order(c, items, subtotal, oid, test=True)
            name, email, phone, l1, l2, city, st, z = random.choice(SAMPLE_CUSTOMERS)
            ship = 0 if subtotal >= FREE_SHIPPING_CENTS else STANDARD_SHIPPING_CENTS
            mark_paid(c, oid, dict(total=subtotal + ship, shipping=ship, email=email, phone=phone, name=name,
                                   line1=l1, line2=l2, city=city, state=st, zip=z,
                                   method=ship_method_for(subtotal, ship)))
            return jsonify(url=f"/success.html?session_id={oid}")

    standard_fee = 0 if subtotal >= FREE_SHIPPING_CENTS else STANDARD_SHIPPING_CENTS
    rate = lambda name, amt, lo, hi: {"shipping_rate_data": {
        "type": "fixed_amount", "display_name": name, "fixed_amount": {"amount": amt, "currency": "usd"},
        "delivery_estimate": {"minimum": {"unit": "business_day", "value": lo},
                              "maximum": {"unit": "business_day", "value": hi}}}}
    try:
        s = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"quantity": i["qty"], "price_data": {
                "currency": "usd", "unit_amount": i["price"], "product_data": {"name": i["name"]}}} for i in items],
            shipping_address_collection={"allowed_countries": ["US"]},
            shipping_options=[rate(ship_method_for(subtotal, standard_fee), standard_fee, 5, 10),
                              rate("White-glove delivery & assembly", WHITE_GLOVE_CENTS, 7, 14)],
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
            shipping = (obj.get("total_details") or {}).get("amount_shipping", 0)
            row = c.execute("SELECT subtotal FROM orders WHERE id=?", (obj["id"],)).fetchone()
            mark_paid(c, obj["id"], dict(
                total=obj.get("amount_total"), shipping=shipping, email=cust.get("email"), phone=cust.get("phone"),
                name=ship.get("name"), line1=a.get("line1"), line2=a.get("line2"), city=a.get("city"),
                state=a.get("state"), zip=a.get("postal_code"),
                method=ship_method_for(row["subtotal"] if row else 0, shipping)))
        elif event["type"] == "checkout.session.expired":
            c.execute("UPDATE orders SET status='canceled' WHERE id=? AND status='pending'", (obj["id"],))
            log_event(c, obj["id"], "canceled", "顾客未完成付款，结账已过期")
    return "", 200


@app.get("/api/order/<oid>")
def public_order(oid):
    with db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not r:
        abort(404, "Order not found")
    o = order_json(r)
    keep = ["order_no", "status", "items", "subtotal", "shipping", "total", "email", "ship_name", "ship_city",
            "ship_state", "ship_method", "carrier", "tracking", "tracking_url"]
    return jsonify({k: o[k] for k in keep})


# ---------------------------------------------------------------- admin: auth
@app.get("/api/admin/me")
def admin_me():
    return jsonify(admin=bool(session.get("admin")), configured=bool(ADMIN_PASSWORD), demo_checkout=DEMO_CHECKOUT)


@app.post("/api/admin/login")
def admin_login():
    if not ADMIN_PASSWORD:
        abort(403, "服务器还没设置 ADMIN_PASSWORD，请在 .env 里设置后重启")
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if not pw or not __import__("hmac").compare_digest(pw, ADMIN_PASSWORD):
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
    with db() as c:
        rows = c.execute(q + " ORDER BY created_at DESC LIMIT 500", args).fetchall()
        counts = dict(c.execute("SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall())
    return jsonify(orders=[order_json(r) for r in rows], counts=counts)


@app.get("/api/admin/orders/<oid>")
@require_admin
def admin_order(oid):
    with db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not r:
        abort(404, "找不到这个订单")
    return jsonify(order_json(r, with_events=True))


@app.post("/api/admin/orders/<oid>")
@require_admin
def admin_update_order(oid):
    body = request.get_json(silent=True) or {}
    with db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not r:
            abort(404, "找不到这个订单")
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
        new = body.get("status")
        if new and new != r["status"]:
            allowed = {"paid": {"shipped", "canceled"}, "shipped": {"delivered", "paid"},
                       "delivered": {"shipped"}, "pending": {"canceled"}, "canceled": set()}
            if new not in allowed.get(r["status"], set()):
                abort(400, f"不能从「{r['status']}」改成「{new}」")
            stamp = {"shipped": "shipped_at", "delivered": "delivered_at"}.get(new)
            c.execute(f"UPDATE orders SET status=?{', ' + stamp + '=?' if stamp else ''} WHERE id=?",
                      (new, now(), oid) if stamp else (new, oid))
            if new == "canceled" and r["status"] == "paid":
                for it in json.loads(r["items_json"]):
                    c.execute("UPDATE products SET stock = stock + ? WHERE id=?", (it["qty"], it["id"]))
            labels = {"shipped": "标记为已发货", "delivered": "标记为已送达", "canceled": "订单已取消（库存已退回）",
                      "paid": "退回到待发货"}
            log_event(c, oid, new, labels[new])
            if new == "shipped":
                o = order_json(c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
                status = send_email(o["email"], f"Your order {o['order_no']} has shipped", shipped_email(o))
                log_event(c, oid, "email", f"📧 发货通知邮件：{status}")
    return admin_order(oid)


@app.post("/api/admin/test-order")
@require_admin
def admin_test_order():
    with db() as c:
        stock = c.execute("SELECT id, stock FROM products WHERE active=1 AND stock>0").fetchall()
        if not stock:
            abort(400, "所有商品都没有库存了，先去商品管理补货")
        picks = random.sample(stock, k=min(len(stock), random.choice([1, 1, 2, 3])))
        items, subtotal = validate_cart(c, [{"id": p["id"], "qty": 1} for p in picks])
        oid = "test_" + os.urandom(6).hex()
        create_order(c, items, subtotal, oid, test=True)
        name, email, phone, l1, l2, city, st, z = random.choice(SAMPLE_CUSTOMERS)
        white = random.random() < 0.3
        ship = WHITE_GLOVE_CENTS if white else (0 if subtotal >= FREE_SHIPPING_CENTS else STANDARD_SHIPPING_CENTS)
        mark_paid(c, oid, dict(total=subtotal + ship, shipping=ship, email=email, phone=phone, name=name, line1=l1,
                               line2=l2, city=city, state=st, zip=z, method=ship_method_for(subtotal, ship)))
    return admin_order(oid)


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
    except (TypeError, ValueError):
        abort(400, "价格和库存要填数字")
    if not name or price <= 0 or stock < 0:
        abort(400, "请填写商品名称、大于 0 的价格和不小于 0 的库存")
    fields = dict(name=name, cat=p.get("cat") or "Living", art=p.get("art") or "cabinet", price=price,
                  dim=p.get("dim") or "", mat=p.get("mat") or "", lead=p.get("lead") or "", stock=stock,
                  active=int(bool(p.get("active", True))), updated_at=now())
    with db() as c:
        if c.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
            c.execute(f"UPDATE products SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), pid))
        else:
            sort = c.execute("SELECT COALESCE(MAX(sort),0)+1 FROM products").fetchone()[0]
            c.execute(f"INSERT INTO products (id, sort, {', '.join(fields)}) VALUES (?, ?, {', '.join('?' * len(fields))})",
                      (pid, sort, *fields.values()))
        r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    return jsonify(product_json(r))


@app.delete("/api/admin/products/<pid>")
@require_admin
def admin_delete_product(pid):
    with db() as c:
        c.execute("DELETE FROM products WHERE id=?", (pid,))
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


init_db()

if __name__ == "__main__":
    print(f"🛍️  Store:  {DOMAIN}/\n🧭 Admin:  {DOMAIN}/admin.html")
    if DEMO_CHECKOUT:
        print("🧪 No STRIPE_SECRET_KEY set: checkout creates paid test orders (demo mode).")
    if not ADMIN_PASSWORD:
        print("⚠️  ADMIN_PASSWORD is not set; admin login is disabled until you add it to .env")
    app.run(port=int(os.getenv("PORT", 4242)), debug=os.getenv("FLASK_DEBUG") == "1")
