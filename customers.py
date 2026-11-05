"""Customer address book: every order grouped by person (email, else phone, else name),
plus what staff write about them (tags and a "remember this person" memo).

Everything about a customer except tags and memo comes from their orders, so the book is
always up to date and nothing has to be typed twice. The admin page turns the raw data
into traits (home, delivery habits, tastes, things mentioned in notes).
"""
import json
import re
from datetime import datetime, timezone

COUNTED = ("paid", "shipped", "delivered")   # orders that count toward spend


def init_tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS customers (
        key TEXT PRIMARY KEY, tags TEXT DEFAULT '[]', memo TEXT DEFAULT '', updated_at TEXT)""")


def digits(s):
    return re.sub(r"\D", "", s or "")[-10:]


def key_of(o):
    email = (o["email"] or "").strip().lower()
    if email:
        return email
    if digits(o["phone"]):
        return "tel:" + digits(o["phone"])
    name = re.sub(r"\s+", " ", (o["ship_name"] or "").strip().lower())
    return "name:" + name if name else ""


def addr(o):
    city = ", ".join(x for x in (o["ship_city"], " ".join(x for x in (o["ship_state"], o["ship_zip"]) if x)) if x)
    return "\n".join(x for x in (o["ship_line1"], o["ship_line2"], city) if x)


def _orders(c, where="", args=()):
    return c.execute(f"SELECT * FROM orders WHERE status!='pending' {where} ORDER BY COALESCE(paid_at, created_at) DESC", args).fetchall()


def _saved(c):
    return {r["key"]: {"tags": json.loads(r["tags"] or "[]"), "memo": r["memo"] or ""} for r in c.execute("SELECT * FROM customers")}


def book(c):
    """One row per customer, newest activity first."""
    saved, people = _saved(c), {}
    for o in _orders(c):
        k = key_of(o)
        if not k:
            continue
        p = people.get(k)
        if not p:   # newest order first, so the first one we see has the latest name / phone / address
            p = people[k] = {"key": k, "name": o["ship_name"] or "", "email": (o["email"] or "").strip(), "phone": o["phone"] or "",
                             "city": o["ship_city"] or "", "state": o["ship_state"] or "", "address": addr(o),
                             "orders": 0, "spent": 0, "canceled": 0, "first_at": None, "last_at": None,
                             "last_order": o["order_no"], "lang": o["lang"] or "en", "test": True, "addresses": []}
        when = o["paid_at"] or o["created_at"]
        if o["status"] in COUNTED:
            p["orders"] += 1
            p["spent"] += (o["total"] or 0) - (o["refunded"] or 0)
        elif o["status"] == "canceled":
            p["canceled"] += 1
        p["first_at"] = when if not p["first_at"] or (when and when < p["first_at"]) else p["first_at"]
        p["last_at"] = p["last_at"] or when
        p["test"] = p["test"] and bool(o["test"])
        a = addr(o)
        if a and a not in p["addresses"]:
            p["addresses"].append(a)
        p["phone"] = p["phone"] or o["phone"] or ""
    out = []
    for k, p in people.items():
        p.update(saved.get(k, {"tags": [], "memo": ""}))
        out.append(p)
    return out


def profile(c, key):
    """Everything about one customer: their orders (with delivery details) and every note on them."""
    p = next((x for x in book(c) if x["key"] == key), None)
    if not p:
        return None
    rows = [o for o in _orders(c) if key_of(o) == key]
    products = {r["id"]: r for r in c.execute("SELECT id, cat, mat FROM products")}
    orders = []
    for o in rows:
        items = json.loads(o["items_json"])
        for it in items:
            pr = products.get(it.get("id"))
            it["cat"] = pr["cat"] if pr else ""
            it["mat"] = pr["mat"] if pr else ""
        orders.append({k: o[k] for k in ("id", "order_no", "status", "total", "refunded", "paid_at", "created_at", "fulfillment",
                                         "service", "delivery_type", "appt_date", "appt_window", "home_type", "floor", "elevator",
                                         "liftgate", "access_notes", "lang", "test", "ship_city", "ship_state")} | {"items": items})
    ids = [o["id"] for o in rows]
    notes = []
    if ids:
        q = ",".join("?" * len(ids))
        notes = [dict(r) for r in c.execute(
            f"""SELECT n.id, n.order_id, n.text, n.created_at, o.order_no FROM notes n JOIN orders o ON o.id=n.order_id
                WHERE n.order_id IN ({q}) ORDER BY n.id DESC""", ids)]
    return {**p, "orders_list": orders, "notes": notes}


def save(c, key, tags=None, memo=None):
    cur = _saved(c).get(key, {"tags": [], "memo": ""})
    if tags is not None:
        clean = []
        for t in tags:
            t = re.sub(r"\s+", " ", str(t)).strip()[:24]
            if t and t not in clean:
                clean.append(t)
        cur["tags"] = clean[:12]
    if memo is not None:
        cur["memo"] = str(memo).strip()[:2000]
    c.execute("""INSERT INTO customers (key, tags, memo, updated_at) VALUES (?,?,?,?)
                 ON CONFLICT(key) DO UPDATE SET tags=excluded.tags, memo=excluded.memo, updated_at=excluded.updated_at""",
              (key, json.dumps(cur["tags"], ensure_ascii=False), cur["memo"], datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return cur


def _v(s):
    return str(s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def vcards(people, store_name):
    """vCard 3.0 for phones / Google Contacts / Outlook."""
    out = []
    for p in people:
        if p["test"]:
            continue
        name = p["name"] or p["email"] or p["phone"]
        parts = name.split(" ", 1)
        lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{_v(name)}",
                 f"N:{_v(parts[1] if len(parts) > 1 else '')};{_v(parts[0])};;;", f"ORG:{_v(store_name)} customer"]
        if p["phone"]:
            lines.append(f"TEL;TYPE=CELL:{_v(p['phone'])}")
        if p["email"]:
            lines.append(f"EMAIL;TYPE=INTERNET:{_v(p['email'])}")
        if p["address"]:
            a = p["address"].split("\n")
            street = ", ".join(a[:-1])
            m = re.match(r"(.*?),\s*([A-Z]{2})?\s*(\d{5})?", a[-1] or "")
            lines.append(f"ADR;TYPE=HOME:;;{_v(street)};{_v(m.group(1) if m else a[-1])};{_v(m.group(2) if m else '')};{_v(m.group(3) if m else '')};USA")
        note = " · ".join(x for x in (", ".join(p["tags"]), p["memo"], f"{p['orders']} orders, ${p['spent'] / 100:,.0f}") if x)
        lines.append(f"NOTE:{_v(note)}")
        if p["tags"]:
            lines.append("CATEGORIES:" + ",".join(_v(t) for t in p["tags"]))
        lines.append("END:VCARD")
        out.append("\r\n".join(lines))
    return "\r\n".join(out) + "\r\n"
