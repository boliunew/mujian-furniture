"""To-dos and order notes, plus the reminder engine.

- A note belongs to an order. It can print on labels/slips/manifests, and it can carry a
  reminder time, which creates a linked to-do.
- A to-do has an optional due time and order. When it's due, the admin bell lights up and,
  if asked, the staff list gets an email.
- The engine also opens to-dos on its own for things that slip through (and closes them
  when they're handled): freight orders nobody has booked, labels not printed, LTL
  shipments close to their date without a PRO number.
"""
import secrets
from datetime import datetime, timedelta, timezone


def now_dt():
    return datetime.now(timezone.utc)


def now():
    return now_dt().isoformat(timespec="seconds")


def parse(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def init_tables(c):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS todos (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, note TEXT, due_at TEXT, order_id TEXT,
            status TEXT DEFAULT 'open', done_at TEXT, created_at TEXT, source TEXT DEFAULT 'manual',
            email INTEGER DEFAULT 0, reminded_at TEXT, print INTEGER DEFAULT 0, auto_key TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, text TEXT, created_at TEXT,
            print INTEGER DEFAULT 1, remind_at TEXT, todo_id TEXT);
        CREATE INDEX IF NOT EXISTS notes_order ON notes(order_id);
        CREATE INDEX IF NOT EXISTS todos_order ON todos(order_id);
    """)
    # older databases kept one note per order in orders.note
    if not c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]:
        for r in c.execute("SELECT id, note, created_at FROM orders WHERE note IS NOT NULL AND TRIM(note)!=''").fetchall():
            c.execute("INSERT INTO notes (order_id, text, created_at, print) VALUES (?,?,?,1)", (r[0], r[1], r[2] or now()))


def todo_json(r):
    d = dict(r)
    d["email"], d["print"] = bool(d["email"]), bool(d["print"])
    due = parse(d["due_at"]) if d["due_at"] else None
    d["overdue"] = bool(due and d["status"] == "open" and due <= now_dt())
    return d


def list_todos(c, status="open", order_id=None):
    q = """SELECT t.*, o.order_no, o.ship_name FROM todos t LEFT JOIN orders o ON o.id=t.order_id WHERE 1=1"""
    args = []
    if status in ("open", "done"):
        q += " AND t.status=?"; args.append(status)
    if order_id:
        q += " AND t.order_id=?"; args.append(order_id)
    q += " ORDER BY CASE WHEN t.due_at IS NULL THEN 1 ELSE 0 END, t.due_at, t.created_at DESC LIMIT 500"
    return [todo_json(r) for r in c.execute(q, args)]


def summary(c):
    cutoff_today = (now_dt() + timedelta(hours=24)).isoformat(timespec="seconds")
    rows = c.execute("SELECT id, title, due_at, order_id FROM todos WHERE status='open'").fetchall()
    overdue = [dict(r) for r in rows if r["due_at"] and parse(r["due_at"]) <= now_dt()]
    soon = [r for r in rows if r["due_at"] and now() < r["due_at"] <= cutoff_today]
    return {"open": len(rows), "overdue": len(overdue), "soon": len(soon), "due_items": overdue[:20]}


def save_todo(c, b, source="manual"):
    title = str(b.get("title") or "").strip()[:200]
    if not title:
        raise ValueError("请写待办内容")
    due = b.get("due_at") or None
    if due and not parse(due):
        raise ValueError("到期时间格式不对")
    order_id = b.get("order_id") or None
    if not order_id and b.get("order_no"):
        r = c.execute("SELECT id FROM orders WHERE order_no=?", (str(b["order_no"]).strip().upper(),)).fetchone()
        if not r:
            raise ValueError("找不到这个订单号")
        order_id = r[0]
    if order_id and not c.execute("SELECT 1 FROM orders WHERE id=?", (order_id,)).fetchone():
        raise ValueError("找不到这个订单")
    tid = b.get("id")
    fields = dict(title=title, note=str(b.get("note") or "")[:1000], due_at=parse(due).isoformat(timespec="seconds") if due else None,
                  order_id=order_id, email=int(bool(b.get("email"))), print=int(bool(b.get("print")) and bool(order_id)))
    if tid and c.execute("SELECT 1 FROM todos WHERE id=?", (tid,)).fetchone():
        old = c.execute("SELECT due_at FROM todos WHERE id=?", (tid,)).fetchone()
        if old[0] != fields["due_at"]:
            fields["reminded_at"] = None  # a new due time gets a new reminder
        c.execute(f"UPDATE todos SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), tid))
    else:
        tid = "td_" + secrets.token_hex(5)
        c.execute(f"INSERT INTO todos (id, created_at, source, {', '.join(fields)}) VALUES (?,?,?,{','.join('?' * len(fields))})",
                  (tid, now(), source, *fields.values()))
    if "status" in b and b["status"] in ("open", "done"):
        set_status(c, tid, b["status"])
    return tid


def set_status(c, tid, status):
    c.execute("UPDATE todos SET status=?, done_at=? WHERE id=?", (status, now() if status == "done" else None, tid))


def notes_for(c, order_id):
    return [{**dict(r), "print": bool(r["print"])} for r in c.execute("SELECT * FROM notes WHERE order_id=? ORDER BY id", (order_id,))]


def add_note(c, order_id, text, print_=True, remind_at=None, email=False):
    text = str(text or "").strip()[:1000]
    if not text:
        raise ValueError("备注不能是空的")
    todo_id = None
    if remind_at:
        todo_id = save_todo(c, {"title": "📝 " + text[:120], "due_at": remind_at, "order_id": order_id, "email": email,
                                "print": False}, source="note")
    c.execute("INSERT INTO notes (order_id, text, created_at, print, remind_at, todo_id) VALUES (?,?,?,?,?,?)",
              (order_id, text, now(), int(bool(print_)), remind_at, todo_id))
    return todo_id


def printables(c, order_id):
    """What prints on this order's documents: notes marked to print + open to-dos marked to print."""
    out = [r["text"] for r in c.execute("SELECT text FROM notes WHERE order_id=? AND print=1 ORDER BY id", (order_id,))]
    out += [("☐ " + r["title"]) for r in c.execute(
        "SELECT title FROM todos WHERE order_id=? AND print=1 AND status='open' ORDER BY due_at, created_at", (order_id,))]
    return out


# ------------------------------------------------------------------ engine
AUTO = {
    "book": "📅 催顾客预约送货（付款两天了还没约）",
    "labels": "🏷️ 打印托盘标签（付款两小时了还没打印）",
    "pro": "📞 联系承运商约提货（快到送货日了还没有 PRO 单号）",
}


def _auto(c, key, kind, order_id, want, email):
    row = c.execute("SELECT id, status FROM todos WHERE auto_key=?", (key,)).fetchone()
    if want and not row:
        c.execute("""INSERT OR IGNORE INTO todos (id, title, due_at, order_id, status, created_at, source, email, auto_key)
                     VALUES (?,?,?,?, 'open', ?, 'auto', ?, ?)""",
                  ("td_" + secrets.token_hex(5), AUTO[kind], now(), order_id, now(), int(email), key))
        return "opened"
    if not want and row and row["status"] == "open":
        set_status(c, row["id"], "done")
        return "closed"
    return None


def run_engine(c, email_staff=True, today=None):
    """Open/close automatic to-dos. Returns due to-dos that still need an email reminder."""
    t = now_dt()
    for o in c.execute("SELECT * FROM orders WHERE status IN ('paid','shipped')").fetchall():
        paid = parse(o["paid_at"]) if o["paid_at"] else None
        if o["fulfillment"] == "freight":
            _auto(c, f"book:{o['id']}", "book", o["id"],
                  o["status"] == "paid" and o["freight_status"] == "unscheduled" and paid and t - paid > timedelta(hours=48), email_staff)
            soon = False
            if o["appt_date"] and today:
                try:
                    days = (datetime.fromisoformat(o["appt_date"]).date() - today).days
                    soon = 0 <= days <= 2
                except ValueError:
                    pass
            _auto(c, f"pro:{o['id']}", "pro", o["id"],
                  o["delivery_type"] == "ltl" and o["status"] == "paid" and soon and not o["pro"], email_staff)
        _auto(c, f"labels:{o['id']}", "labels", o["id"],
              o["status"] == "paid" and not o["labels_printed_at"] and paid and t - paid > timedelta(hours=2), email_staff)
    # close automatic to-dos whose order was canceled or delivered
    for r in c.execute("""SELECT t.id FROM todos t JOIN orders o ON o.id=t.order_id
                          WHERE t.source='auto' AND t.status='open' AND o.status IN ('canceled','delivered')""").fetchall():
        set_status(c, r["id"], "done")
    due = c.execute("""SELECT t.*, o.order_no, o.ship_name FROM todos t LEFT JOIN orders o ON o.id=t.order_id
                       WHERE t.status='open' AND t.email=1 AND t.reminded_at IS NULL AND t.due_at IS NOT NULL""").fetchall()
    return [r for r in due if parse(r["due_at"]) <= t]
