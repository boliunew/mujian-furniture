"""
Mujian Furniture — Flask backend with Stripe Checkout.

Run:
    pip install -r requirements.txt
    cp .env.example .env        # then fill in your Stripe TEST keys
    python server.py            # http://localhost:4242

Prices live here on the server. The browser only sends product ids and
quantities, so nobody can change a price by editing the page.
"""
import os
import sqlite3
import json
from datetime import datetime, timezone

import stripe
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, abort

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
DOMAIN = os.getenv("DOMAIN", "http://localhost:4242").rstrip("/")
DB_PATH = os.getenv("DB_PATH", "orders.db")

# ---------------------------------------------------------------- catalog
# Prices in cents (Stripe uses the smallest currency unit).
PRODUCTS = {
    "s1":  {"name": "Haven 3-Seat Sofa",           "cat": "Living",  "art": "sofa",    "price": 189900, "dim": '83" W × 36" D × 31" H', "mat": "White oak frame · linen blend",     "lead": "2–3 weeks"},
    "c1":  {"name": "Sit Walnut Dining Chair",      "cat": "Dining",  "art": "chair",   "price":  34900, "dim": '18" W × 20" D × 31" H', "mat": "Black walnut · vegetable-tanned leather", "lead": "1 week"},
    "t1":  {"name": "Gather 6-Seat Dining Table",   "cat": "Dining",  "art": "table",   "price": 149900, "dim": '71" W × 35" D × 30" H', "mat": "Solid white oak · hardwax oil",     "lead": "3–4 weeks"},
    "b1":  {"name": "Rest Low Platform Bed, Queen", "cat": "Bedroom", "art": "bed",     "price": 179900, "dim": '64" W × 85" D × 35" H', "mat": "Cherry · mortise-and-tenon joints", "lead": "4 weeks"},
    "sh1": {"name": "Keep 3-Tier Open Bookshelf",   "cat": "Study",   "art": "shelf",   "price":  64900, "dim": '35" W × 13" D × 59" H', "mat": "White oak · adjustable shelves",    "lead": "2 weeks"},
    "l1":  {"name": "Glow Brass Floor Lamp",        "cat": "Lighting","art": "lamp",    "price":  39900, "dim": '16" W × 16" D × 62" H', "mat": "Brass shade · E26 socket",          "lead": "1 week"},
    "tv1": {"name": "Still 2-Door Media Console",   "cat": "Living",  "art": "cabinet", "price":  99900, "dim": '71" W × 17" D × 22" H', "mat": "Black walnut · push-to-open doors", "lead": "3 weeks"},
    "t2":  {"name": "Pause Round Side Table",       "cat": "Living",  "art": "table",   "price":  22900, "dim": '20" Ø × 22" H',         "mat": "White oak · solid legs",            "lead": "1 week"},
}

FREE_SHIPPING_CENTS = 50000      # free standard shipping on orders $500+
STANDARD_SHIPPING_CENTS = 4900   # $49
WHITE_GLOVE_CENTS = 14900        # $149, delivered and assembled
MAX_QTY_PER_ITEM = 10

# ---------------------------------------------------------------- database
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                   session_id   TEXT PRIMARY KEY,
                   status       TEXT NOT NULL,          -- pending | paid | expired
                   items_json   TEXT NOT NULL,
                   subtotal     INTEGER NOT NULL,
                   total        INTEGER,
                   email        TEXT,
                   ship_name    TEXT,
                   ship_address TEXT,
                   created_at   TEXT NOT NULL,
                   paid_at      TEXT
               )"""
        )


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- app
app = Flask(__name__, static_folder="public", static_url_path="")


@app.get("/")
def index():
    return send_from_directory("public", "index.html")


@app.get("/api/products")
def products():
    return jsonify([{"id": pid, **p} for pid, p in PRODUCTS.items()])


@app.post("/api/checkout")
def create_checkout():
    if not stripe.api_key:
        return jsonify(error="Server is missing STRIPE_SECRET_KEY. Add it to .env."), 500

    body = request.get_json(silent=True) or {}
    items = body.get("items") or []

    # Validate the cart against the server's own catalog.
    clean, line_items, subtotal = [], [], 0
    for it in items:
        pid, qty = it.get("id"), it.get("qty")
        if pid not in PRODUCTS or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY_PER_ITEM:
            return jsonify(error=f"Invalid item in cart: {pid}"), 400
        p = PRODUCTS[pid]
        subtotal += p["price"] * qty
        clean.append({"id": pid, "qty": qty})
        line_items.append({
            "quantity": qty,
            "price_data": {
                "currency": "usd",
                "unit_amount": p["price"],
                "product_data": {"name": p["name"], "description": f'{p["dim"]} · {p["mat"]}'},
            },
        })
    if not line_items:
        return jsonify(error="Your cart is empty."), 400

    standard_fee = 0 if subtotal >= FREE_SHIPPING_CENTS else STANDARD_SHIPPING_CENTS
    shipping_options = [
        {"shipping_rate_data": {
            "type": "fixed_amount",
            "display_name": "Free standard delivery" if standard_fee == 0 else "Standard delivery (self-assembly)",
            "fixed_amount": {"amount": standard_fee, "currency": "usd"},
            "delivery_estimate": {"minimum": {"unit": "business_day", "value": 5},
                                  "maximum": {"unit": "business_day", "value": 10}},
        }},
        {"shipping_rate_data": {
            "type": "fixed_amount",
            "display_name": "White-glove delivery & assembly",
            "fixed_amount": {"amount": WHITE_GLOVE_CENTS, "currency": "usd"},
            "delivery_estimate": {"minimum": {"unit": "business_day", "value": 7},
                                  "maximum": {"unit": "business_day", "value": 14}},
        }},
    ]

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            shipping_address_collection={"allowed_countries": ["US"]},
            shipping_options=shipping_options,
            phone_number_collection={"enabled": True},
            # Turn this on after you set up Stripe Tax in the Dashboard:
            # automatic_tax={"enabled": True},
            success_url=f"{DOMAIN}/success.html?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN}/?canceled=1",
        )
    except stripe.StripeError as e:
        return jsonify(error=e.user_message or str(e)), 502

    with db() as conn:
        conn.execute(
            "INSERT INTO orders (session_id, status, items_json, subtotal, created_at) VALUES (?,?,?,?,?)",
            (session.id, "pending", json.dumps(clean), subtotal, now()),
        )
    return jsonify(url=session.url)


@app.post("/webhook")
def webhook():
    """Stripe calls this after payment. This is the only place an order becomes 'paid'."""
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)  # verifies the signature
    except (ValueError, stripe.SignatureVerificationError):
        abort(400)

    event = json.loads(payload)  # plain dicts, same across stripe-python versions
    obj = event["data"]["object"]
    if event["type"] == "checkout.session.completed" and obj.get("payment_status") == "paid":
        details = obj.get("collected_information") or {}
        ship = details.get("shipping_details") or obj.get("shipping_details") or {}
        addr = ship.get("address") or {}
        address = ", ".join(x for x in [addr.get("line1"), addr.get("line2"), addr.get("city"),
                                        addr.get("state"), addr.get("postal_code")] if x)
        email = (obj.get("customer_details") or {}).get("email")
        with db() as conn:
            conn.execute(
                """UPDATE orders SET status='paid', total=?, email=?, ship_name=?, ship_address=?, paid_at=?
                   WHERE session_id=?""",
                (obj.get("amount_total"), email, ship.get("name"), address, now(), obj["id"]),
            )
        print(f"✅ Paid: {obj['id']}  ${obj.get('amount_total', 0) / 100:.2f}  {email}")
    elif event["type"] == "checkout.session.expired":
        with db() as conn:
            conn.execute("UPDATE orders SET status='expired' WHERE session_id=?", (obj["id"],))

    return "", 200


@app.get("/api/order/<session_id>")
def order_status(session_id):
    """Used by success.html to show the order. Reads our own DB, filled in by the webhook."""
    with db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE session_id=?", (session_id,)).fetchone()
    if not row:
        abort(404)
    return jsonify(
        order_no="MJ-" + session_id[-8:].upper(),
        status=row["status"],
        total=row["total"],
        email=row["email"],
        ship_name=row["ship_name"],
        ship_address=row["ship_address"],
        items=[{**PRODUCTS[i["id"]], "qty": i["qty"]} for i in json.loads(row["items_json"]) if i["id"] in PRODUCTS],
    )


@app.get("/admin/orders")
def admin_orders():
    """Plain order list for you. Protect or remove this before going live."""
    if request.args.get("key") != os.getenv("ADMIN_KEY", "") or not os.getenv("ADMIN_KEY"):
        abort(403)
    with db() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 200").fetchall()
    return jsonify([dict(r) for r in rows])


init_db()

if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 4242)), debug=os.getenv("FLASK_DEBUG") == "1")
