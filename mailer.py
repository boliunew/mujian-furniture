"""Outgoing email: settings, a send queue (outbox table) and a background sender.

Every email is written to the outbox first, inside the caller's transaction, and a
worker thread sends it after the transaction commits. A slow or broken mail server
never blocks saving an order, and every message stays visible (and resendable) in
the admin.
"""
import json
import os
import smtplib
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr

DEFAULTS = {
    "host": "", "port": 587, "security": "starttls", "user": "", "password": "",
    "from_addr": "", "from_name": "", "staff": [], "staff_lang": "zh",
    "customer_emails": True, "notify_new_order": True, "notify_todos": True,
}
_wake = threading.Event()
_started = False


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, to_addr TEXT, subject TEXT, body TEXT,
        kind TEXT, order_id TEXT, status TEXT, error TEXT, sent_at TEXT, attempts INTEGER DEFAULT 0)""")
    if "claimed_at" not in {r[1] for r in c.execute("PRAGMA table_info(outbox)")}:
        c.execute("ALTER TABLE outbox ADD COLUMN claimed_at TEXT")


def load(c, store_email=""):
    """Mail settings from the admin, falling back to SMTP_* in .env."""
    s = dict(DEFAULTS)
    s.update({"host": os.getenv("SMTP_HOST", ""), "port": int(os.getenv("SMTP_PORT", 587) or 587),
              "user": os.getenv("SMTP_USER", ""), "password": os.getenv("SMTP_PASS", ""),
              "from_addr": os.getenv("MAIL_FROM", "") or store_email,
              "security": "ssl" if str(os.getenv("SMTP_PORT", "")) == "465" else "starttls"})
    row = c.execute("SELECT value FROM settings WHERE key='mail'").fetchone()
    if row:
        saved = json.loads(row[0])
        if not saved.get("password"):
            saved.pop("password", None)  # keep the .env password when the admin left it blank
        s.update(saved)
    return s


def public(s):
    out = {k: v for k, v in s.items() if k != "password"}
    out["has_password"] = bool(s.get("password"))
    out["configured"] = bool(s.get("host") and s.get("from_addr"))
    return out


def clean(body, current):
    s = dict(current)
    for k in ("host", "user", "from_addr", "from_name"):
        if k in body:
            s[k] = str(body.get(k) or "").strip()[:120]
    if body.get("password"):
        s["password"] = str(body["password"])[:200]
    if body.get("clear_password"):
        s["password"] = ""
    try:
        s["port"] = max(1, min(65535, int(body.get("port", s["port"]))))
    except (TypeError, ValueError):
        raise ValueError("端口要填数字")
    if body.get("security") in ("starttls", "ssl", "none"):
        s["security"] = body["security"]
    if "staff" in body:
        staff = body["staff"] if isinstance(body["staff"], list) else str(body["staff"]).replace(";", ",").split(",")
        s["staff"] = [e.strip() for e in staff if "@" in e.strip()][:10]
    if body.get("staff_lang") in ("zh", "en", "es"):
        s["staff_lang"] = body["staff_lang"]
    for k in ("customer_emails", "notify_new_order", "notify_todos"):
        if k in body:
            s[k] = bool(body[k])
    if s["from_addr"] and "@" not in s["from_addr"]:
        raise ValueError("发件地址格式不对")
    return s


def queue(c, settings, to, subject, body, kind="", order_id=None, hold=False):
    """Put an email in the outbox. Returns a short status for the order history.
    hold=True records it without sending (customer emails of test orders)."""
    if not to:
        return "没有收件地址，邮件未发送"
    configured = bool(settings.get("host") and settings.get("from_addr"))
    status = "held" if hold else ("queued" if configured else "not_configured")
    c.execute("INSERT INTO outbox (created_at, to_addr, subject, body, kind, order_id, status) VALUES (?,?,?,?,?,?,?)",
              (now(), to, subject, body, kind, order_id, status))
    _wake.set()
    if hold:
        return "测试订单，不发给顾客（已记录内容）"
    return f"已加入发送队列 → {to}" if configured else "未配置邮件服务，邮件未发送（已记录内容）"


def deliver(settings, to, subject, body):
    """Send one message right now. Raises on failure."""
    msg = EmailMessage()
    msg["From"] = formataddr((settings.get("from_name") or "", settings["from_addr"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    host, port, sec = settings["host"], int(settings["port"]), settings.get("security", "starttls")
    ctx = ssl.create_default_context()
    if sec == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    with server as s:
        if sec == "starttls":
            s.starttls(context=ctx)
        if settings.get("user"):
            s.login(settings["user"], settings.get("password", ""))
        s.send_message(msg)


def release_stuck(db, minutes=10):
    """A worker that died mid-send leaves rows in 'sending'; put them back in the queue."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with db() as c:
        return c.execute("UPDATE outbox SET status='queued' WHERE status='sending' AND (claimed_at IS NULL OR claimed_at<?)",
                         (cutoff,)).rowcount


def flush(db, store_email="", limit=20):
    """Send queued emails. Safe to run from several processes: each row is claimed first."""
    sent = 0
    release_stuck(db)
    with db() as c:
        settings = load(c, store_email)
        if not (settings.get("host") and settings.get("from_addr")):
            return 0
        ids = [r[0] for r in c.execute("SELECT id FROM outbox WHERE status='queued' ORDER BY id LIMIT ?", (limit,))]
    for i in ids:
        with db() as c:
            claimed = c.execute("UPDATE outbox SET status='sending', attempts=attempts+1, claimed_at=? WHERE id=? AND status='queued'",
                                (now(), i)).rowcount
            row = c.execute("SELECT * FROM outbox WHERE id=?", (i,)).fetchone()
        if not claimed:
            continue
        try:
            deliver(settings, row["to_addr"], row["subject"], row["body"])
            status, err = "sent", None
            sent += 1
        except Exception as e:
            status, err = ("queued" if row["attempts"] < 3 else "failed"), str(e)[:300]
        with db() as c:
            c.execute("UPDATE outbox SET status=?, error=?, sent_at=? WHERE id=?",
                      (status, err, now() if status == "sent" else None, i))
    return sent


def start_worker(db, store_email="", tick=None):
    """Background sender. `tick` runs every minute too (to-do reminders)."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        last_tick = 0
        while True:
            _wake.wait(timeout=15)
            _wake.clear()
            try:
                flush(db, store_email)
            except Exception as e:
                print("mail worker:", e)
            if tick and time.time() - last_tick > 60:
                last_tick = time.time()
                try:
                    tick()
                except Exception as e:
                    print("reminder tick:", e)

    threading.Thread(target=loop, name="mailer", daemon=True).start()


STAFF = {
    "zh": {"new_order": "🛒 新订单 {no}：{total}", "todo": "⏰ 待办到期：{title}", "open": "在后台打开：{url}",
           "items": "商品：", "ship_to": "收货：", "due": "到期时间：", "order": "关联订单：", "test": "测试邮件 · 木间后台",
           "test_body": "这是一封测试邮件。能收到说明邮件系统已经接好了。"},
    "en": {"new_order": "🛒 New order {no}: {total}", "todo": "⏰ To-do due: {title}", "open": "Open in the admin: {url}",
           "items": "Items:", "ship_to": "Ship to:", "due": "Due:", "order": "Order:", "test": "Test email · Mujian admin",
           "test_body": "This is a test email. If you got it, email is set up."},
    "es": {"new_order": "🛒 Pedido nuevo {no}: {total}", "todo": "⏰ Tarea vencida: {title}", "open": "Abrir en el panel: {url}",
           "items": "Productos:", "ship_to": "Envío a:", "due": "Vence:", "order": "Pedido:", "test": "Correo de prueba · Panel Mujian",
           "test_body": "Este es un correo de prueba. Si te llegó, el correo está configurado."},
}
