"""Freight delivery: settings, appointment slots, freight specs and status rules.

Big pieces ship by truck. Orders to ZIPs in the local list go on our own trucks
(customer books a time window, capacity-limited); everything else goes LTL and
the customer's choice is a requested date the carrier confirms by phone.
"""
import json
import math
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.getenv("STORE_TZ", "America/Los_Angeles"))

DEFAULT_SETTINGS = {
    "windows": [{"id": "am", "label": "8 AM – 12 PM"}, {"id": "pm", "label": "12 PM – 5 PM"}],
    "capacity": 4,                 # local stops per window per day
    "weekdays": [1, 2, 3, 4, 5, 6],  # ISO weekdays we deliver (1 = Monday)
    "lead_days": 3,                # first bookable day is at least this many days out
    "horizon_days": 30,
    "blackout": [],                # ["2026-11-26", ...]
    "cutoff_hour": 17,             # customer can change until this hour the day before
    "local_zips": "940,941,943,944,945,946,947,948,949,950,951",
    "trucks": ["Truck 1", "Truck 2"],
    "ltl_note": "The carrier will call 24–48 hours before delivery to confirm a 4-hour window.",
}

# Service levels chosen at checkout (shipping option). Fees in cents.
SERVICES = {
    "standard":    {"en": "Standard parcel delivery", "zh": "快递"},
    "curbside":    {"en": "Freight · curbside (liftgate)", "zh": "卡派 · 门口（尾板卸货）"},
    "threshold":   {"en": "Freight · inside first door", "zh": "卡派 · 送进门"},
    "white_glove": {"en": "White-glove · room of choice + assembly", "zh": "白手套 · 进屋安装"},
}
PARCEL_FEE, CURBSIDE_FEE, THRESHOLD_FEE, WHITE_GLOVE_FEE = 4900, 7900, 9900, 14900
FREE_OVER = 50000

FREIGHT_STATUSES = ["unscheduled", "scheduled", "picked_up", "in_transit", "out_for_delivery", "delivered", "exception"]
FREIGHT_ZH = {"unscheduled": "待预约", "scheduled": "已预约", "picked_up": "已装车/已提货", "in_transit": "运输中",
              "out_for_delivery": "派送中", "delivered": "已签收", "exception": "异常"}
FREIGHT_EN = {"unscheduled": "Awaiting your booking", "scheduled": "Delivery booked", "picked_up": "Picked up",
              "in_transit": "In transit", "out_for_delivery": "Out for delivery", "delivered": "Delivered",
              "exception": "Delayed — we'll contact you"}
FREIGHT_CLASSES = ["50", "55", "60", "65", "70", "77.5", "85", "92.5", "100", "110", "125", "150", "175", "200", "250", "300", "400", "500"]


def today():
    return datetime.now(TZ).date()


def load_settings(c):
    row = c.execute("SELECT value FROM settings WHERE key='delivery'").fetchone()
    s = dict(DEFAULT_SETTINGS)
    if row:
        s.update(json.loads(row["value"]))
    return s


def clean_settings(body):
    s = dict(DEFAULT_SETTINGS)
    w = [x for x in body.get("windows", []) if str(x.get("id", "")).strip() and str(x.get("label", "")).strip()]
    if not w:
        raise ValueError("至少要有一个送货时段")
    s["windows"] = [{"id": str(x["id"]).strip()[:12], "label": str(x["label"]).strip()[:40]} for x in w][:6]
    for k, lo, hi in [("capacity", 1, 50), ("lead_days", 0, 30), ("horizon_days", 7, 120), ("cutoff_hour", 0, 23)]:
        try:
            s[k] = max(lo, min(hi, int(body.get(k, s[k]))))
        except (TypeError, ValueError):
            raise ValueError(f"{k} 要填数字")
    s["weekdays"] = sorted({int(d) for d in body.get("weekdays", []) if 1 <= int(d) <= 7}) or [1, 2, 3, 4, 5]
    s["blackout"] = sorted({d for d in body.get("blackout", []) if _is_date(d)})
    s["local_zips"] = ",".join(z.strip() for z in str(body.get("local_zips", "")).replace(" ", ",").split(",") if z.strip().isdigit())
    s["trucks"] = [t.strip()[:30] for t in body.get("trucks", []) if t.strip()][:12] or ["Truck 1"]
    s["ltl_note"] = str(body.get("ltl_note", s["ltl_note"]))[:300]
    return s


def _is_date(d):
    try:
        date.fromisoformat(d)
        return True
    except (TypeError, ValueError):
        return False


def is_local(settings, zip_code):
    z = (zip_code or "").strip()
    return any(z.startswith(p) for p in settings["local_zips"].split(",") if p)


def window_label(settings, wid):
    return next((w["label"] for w in settings["windows"] if w["id"] == wid), wid or "")


def bookable_dates(settings):
    first = today() + timedelta(days=settings["lead_days"])
    out = []
    for i in range(settings["horizon_days"] + 1):
        d = first + timedelta(days=i)
        if d.isoweekday() in settings["weekdays"] and d.isoformat() not in settings["blackout"]:
            out.append(d)
    return out


def slot_usage(c, start, end, exclude_id=None):
    rows = c.execute(
        """SELECT appt_date, appt_window, COUNT(*) n FROM orders
           WHERE delivery_type='local' AND appt_date BETWEEN ? AND ? AND status!='canceled' AND id!=?
           GROUP BY appt_date, appt_window""", (start, end, exclude_id or "")).fetchall()
    return {(r["appt_date"], r["appt_window"]): r["n"] for r in rows}


def slots_for(c, settings, order):
    dates = bookable_dates(settings)
    if not dates:
        return []
    local = order["delivery_type"] == "local"
    used = slot_usage(c, dates[0].isoformat(), dates[-1].isoformat(), order["id"]) if local else {}
    out = []
    for d in dates:
        ds = d.isoformat()
        wins = []
        for w in settings["windows"]:
            n = used.get((ds, w["id"]), 0)
            wins.append({"id": w["id"], "label": w["label"],
                         "left": settings["capacity"] - n if local else None,
                         "full": local and n >= settings["capacity"]})
        out.append({"date": ds, "weekday": d.strftime("%a"), "windows": wins})
    return out


def can_customer_change(settings, order):
    if order["status"] in ("canceled", "delivered") or order["freight_status"] in ("out_for_delivery", "delivered", "in_transit", "picked_up"):
        return False
    if not order["appt_date"]:
        return True
    d = date.fromisoformat(order["appt_date"])
    cutoff = datetime.combine(d - timedelta(days=1), datetime.min.time(), TZ).replace(hour=settings["cutoff_hour"])
    return datetime.now(TZ) < cutoff


def freight_specs(items, products):
    """Pallets, weight and class for the BOL. One pallet per big piece; 40 lb per pallet."""
    pallets, weight, cls = 0, 0, 0.0
    for it in items:
        p = products.get(it["id"])
        if not p or p["ship_mode"] != "freight":
            continue
        pallets += it["qty"]
        weight += (p["weight_lb"] or 0) * it["qty"]
        try:
            cls = max(cls, float(p["freight_class"] or 0))
        except ValueError:
            pass
    if not pallets:
        return 0, 0, ""
    c = ("%g" % cls) if cls else "125"
    return pallets, int(weight + 40 * pallets), c


def main_status_for(freight_status, current):
    """Keep the order's main status in step with the freight status."""
    if current == "canceled":
        return current
    if freight_status in ("picked_up", "in_transit", "out_for_delivery"):
        return "shipped"
    if freight_status == "delivered":
        return "delivered"
    if freight_status in ("unscheduled", "scheduled"):
        return "paid"
    return current


def pick_service(fulfillment, shipping):
    if fulfillment != "freight":
        return "standard"
    return {THRESHOLD_FEE: "threshold", WHITE_GLOVE_FEE: "white_glove"}.get(shipping, "curbside")


def pallet_count(n):
    return max(1, math.ceil(n))
