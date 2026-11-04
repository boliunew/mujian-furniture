"""End-to-end checks of the store, admin, freight, inventory, labels, notes, to-dos and email.
Run: pip install pytest && pytest -q
"""
import io
import json
import socketserver
import threading

from PIL import Image


def product(client, pid):
    return next(p for p in client.get("/api/products").get_json() if p["id"] == pid)


def cell(admin, pid, wid="main"):
    inv = admin.get("/api/admin/inventory").get_json()
    return next(p for p in inv["products"] if p["id"] == pid)["cells"][wid]


# ------------------------------------------------------------------ security
def test_admin_requires_login(client):
    assert client.get("/api/admin/orders").status_code == 401
    assert client.post("/api/admin/login", json={"password": "nope"}).status_code == 401


def test_login_rate_limit(server):
    c = server.app.test_client()
    codes = [c.post("/api/admin/login", json={"password": "wrong"}, environ_base={"REMOTE_ADDR": "9.9.9.9"}).status_code
             for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    # another address is not affected
    ok = c.post("/api/admin/login", json={"password": "test-password-123"}, environ_base={"REMOTE_ADDR": "8.8.8.8"})
    assert ok.status_code == 200


def test_session_key_is_shared(server):
    with server.db() as c:
        stored = c.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()[0]
    assert server.app.secret_key == stored  # every worker process reads the same key


def test_upload_path_cannot_escape(server, admin):
    import pytest
    from werkzeug.exceptions import NotFound
    for bad in ("..", "../etc", "a/b", "", "nope-not-here"):
        with pytest.raises(NotFound):
            server.product_dir(bad)
    img = io.BytesIO(); Image.new("RGB", (10, 10)).save(img, "JPEG"); img.seek(0)
    r = admin.post("/api/admin/products/../photo", data={"photo": (img, "a.jpg")}, content_type="multipart/form-data")
    assert r.status_code in (404, 405)
    assert admin.delete("/api/admin/products/nope-not-here").status_code == 404


def test_security_headers_and_health(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_live_site_without_stripe_refuses_checkout(server, monkeypatch):
    monkeypatch.setattr(server, "PAYMENTS_READY", False)
    r = server.app.test_client().post("/api/checkout", json={"items": [{"id": "c1", "qty": 1}]})
    assert r.status_code == 503


# ------------------------------------------------------------------ store & orders
def test_checkout_demo_and_order_page(client, admin):
    before = product(client, "c1")["stock"]
    r = client.post("/api/checkout", json={"items": [{"id": "c1", "qty": 2}], "lang": "es"}).get_json()
    oid = r["url"].split("=")[1]
    assert client.get(f"/api/order/{oid}").get_json()["status"] == "paid"
    assert product(client, "c1")["stock"] == before - 2  # reserved, not yet shipped
    o = admin.get(f"/api/admin/orders/{oid}").get_json()
    assert o["lang"] == "es" and o["warehouse_id"] == "main"


def test_bad_cart(client):
    assert client.post("/api/checkout", json={"items": [{"id": "zzz", "qty": 1}]}).status_code == 400
    assert client.post("/api/checkout", json={"items": []}).status_code == 400


def test_search_and_csv(admin, buy):
    oid = buy([("l1", 1)], customer=3)
    no = admin.get(f"/api/admin/orders/{oid}").get_json()["order_no"]
    found = admin.get("/api/admin/orders?q=" + no).get_json()["orders"]
    assert [o["id"] for o in found] == [oid]
    phone = "".join(ch for ch in admin.get(f"/api/admin/orders/{oid}").get_json()["phone"] if ch.isdigit())[:6]
    assert any(o["id"] == oid for o in admin.get("/api/admin/orders?q=" + phone).get_json()["orders"])  # phone digits
    csv = admin.get("/api/admin/orders.csv?q=" + no)
    assert csv.status_code == 200 and no in csv.get_data(as_text=True)


def test_parcel_ship_moves_stock(admin, buy):
    oid = buy([("t2", 1)])
    before = cell(admin, "t2")["qty"]
    r = admin.post(f"/api/admin/orders/{oid}", json={"status": "shipped", "carrier": "USPS", "tracking": "9400 1"}).get_json()
    assert r["status"] == "shipped" and "usps.com" in r["tracking_url"]
    assert cell(admin, "t2")["qty"] == before - 1
    admin.post(f"/api/admin/orders/{oid}", json={"status": "paid"})
    assert cell(admin, "t2")["qty"] == before  # undoing the shipment puts it back


def test_cancel_releases_reservation(client, admin, buy):
    before = product(client, "sh1")["stock"]
    oid = buy([("sh1", 2)])
    assert product(client, "sh1")["stock"] == before - 2
    admin.post(f"/api/admin/orders/{oid}", json={"status": "canceled"})
    assert product(client, "sh1")["stock"] == before


# ------------------------------------------------------------------ freight
def test_freight_booking_capacity_and_status(admin, client, buy):
    admin.post("/api/admin/settings", json={**admin.get("/api/admin/settings").get_json(), "capacity": 1})
    a = buy([("tv1", 1)], customer=1)          # Berkeley → own trucks
    b = buy([("t1", 1)], customer=0)           # San Francisco → own trucks
    tok = lambda oid: admin.get(f"/api/admin/orders/{oid}").get_json()["schedule_url"].split("t=")[1]
    sa = client.get(f"/api/schedule/{a}?t={tok(a)}").get_json()
    day = sa["slots"][0]["date"]
    assert client.post(f"/api/schedule/{a}", json={"t": tok(a), "date": day, "window": "am"}).status_code == 200
    r = client.post(f"/api/schedule/{b}", json={"t": tok(b), "date": day, "window": "am"})
    assert r.status_code == 400  # window full
    assert client.get(f"/api/schedule/{a}?t=bad").status_code == 404
    o = admin.post(f"/api/admin/orders/{a}", json={"freight_status": "picked_up"}).get_json()
    assert o["status"] == "shipped" and o["stock_out"] == 1
    admin.post("/api/admin/settings", json={**admin.get("/api/admin/settings").get_json(), "capacity": 4})


# ------------------------------------------------------------------ inventory
def test_receive_transfer_count(admin):
    admin.post("/api/admin/inventory/receive", json={"warehouse_id": "main", "ref": "PO-1", "lines": [{"product_id": "l1", "qty": 5}]})
    q = cell(admin, "l1")["qty"]
    r = admin.post("/api/admin/inventory/transfer", json={"from": "main", "to": "showroom", "lines": [{"product_id": "l1", "qty": 999}]})
    assert r.status_code == 400
    cnt = admin.post("/api/admin/counts", json={"warehouse_id": "main", "scope": "ids", "product_ids": ["l1"], "blind": True}).get_json()
    assert admin.post(f"/api/admin/counts/{cnt['id']}", json={"action": "post"}).status_code == 400  # not counted yet
    admin.post(f"/api/admin/counts/{cnt['id']}", json={"lines": [{"product_id": "l1", "counted": q - 1, "reason": "损坏"}], "action": "post"})
    assert cell(admin, "l1")["qty"] == q - 1


# ------------------------------------------------------------------ labels, notes, to-dos
def test_units_notes_and_print_queue(admin, buy):
    oid = buy([("s1", 1), ("l1", 2)])
    o = admin.get(f"/api/admin/orders/{oid}").get_json()
    assert [u["kind"] for u in o["units"]] == ["pallet", "box", "box"]
    admin.post(f"/api/admin/orders/{oid}/notes", json={"text": "Glass top", "print": True})
    admin.post(f"/api/admin/orders/{oid}/notes", json={"text": "internal only", "print": False})
    admin.post("/api/admin/todos", json={"title": "Call before", "order_id": oid, "print": True})
    o = admin.get(f"/api/admin/orders/{oid}").get_json()
    assert o["print_notes"] == ["Glass top", "☐ Call before"]
    q = admin.get("/api/admin/print-queue").get_json()
    assert any(x["id"] == oid and x["print_notes"] for x in q["orders"])
    admin.post("/api/admin/print-queue/ack", json={"ids": [oid]})
    assert not any(x["id"] == oid for x in admin.get("/api/admin/print-queue").get_json()["orders"])


def test_todo_due_and_auto(server, admin, buy):
    t = admin.post("/api/admin/todos", json={"title": "Old", "due_at": "2020-01-01T00:00:00Z"}).get_json()
    assert t["overdue"]
    assert admin.post("/api/admin/todos", json={"title": "x", "order_no": "NOPE"}).status_code == 400
    oid = buy([("b1", 1)], customer=2)
    with server.db() as c:
        c.execute("UPDATE orders SET paid_at='2020-01-01T00:00:00+00:00' WHERE id=?", (oid,))
    server.run_reminders(); server.run_reminders()  # running twice must not duplicate
    auto = [x for x in admin.get("/api/admin/todos").get_json()["todos"] if x["order_id"] == oid and x["source"] == "auto"]
    assert len(auto) == 2  # not booked + labels not printed


def test_oversell_is_flagged(server, admin):
    with server.db() as c:
        its, sub = server.validate_cart(c, [{"id": "b1", "qty": 1}])
        server.create_order(c, its, sub, "stripe_late")
        c.execute("UPDATE products SET stock=0 WHERE id='b1'")  # sold out while the customer was paying
        server.mark_paid(c, "stripe_late", dict(total=sub, shipping=0, email="x@y.z", name="Late", zip="10001"))
    o = admin.get("/api/admin/orders/stripe_late").get_json()
    assert o["status"] == "paid" and any("库存不足" in e["text"] for e in o["events"])
    assert any("库存不足" in x["title"] for x in o["todos"])


# ------------------------------------------------------------------ email
class _SMTP(socketserver.StreamRequestHandler):
    got = []

    def handle(self):
        w = lambda x: self.wfile.write((x + "\r\n").encode())
        w("220 test"); data, buf = False, []
        while True:
            line = self.rfile.readline().decode(errors="ignore")
            if not line:
                break
            s = line.strip()
            if data:
                if s == ".":
                    data = False; _SMTP.got.append("".join(buf)); buf = []; w("250 ok")
                else:
                    buf.append(line)
                continue
            u = s.upper()
            if u.startswith(("EHLO", "HELO")): w("250 hi")
            elif u.startswith("DATA"): data = True; w("354 go")
            elif u.startswith("QUIT"): w("221 bye"); break
            else: w("250 ok")


def test_email_queue_and_test_send(server, admin, client):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTP)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = admin.post("/api/admin/mail", json={"host": "127.0.0.1", "port": srv.server_address[1], "security": "none",
                                                 "from_addr": "orders@test.dev", "staff": "ops@test.dev"}).get_json()
        assert s["configured"] and "password" not in s
        assert admin.post("/api/admin/mail/test", json={"to": "me@test.dev"}).status_code == 200
        client.post("/api/checkout", json={"items": [{"id": "tv1", "qty": 1}], "lang": "zh"})
        server.mailer.flush(server.db, server.STORE["email"])
        subjects = [e["subject"] for e in admin.get("/api/admin/emails").get_json()["emails"][:4]]
        assert any("预约送货" in x for x in subjects) and any("新订单" in x for x in subjects)
        assert len(_SMTP.got) >= 3
    finally:
        srv.shutdown()
        admin.post("/api/admin/mail", json={"host": "", "from_addr": ""})


def test_translations_cover_all_ui_strings():
    """Every t("…") in the admin has English and Spanish; every store string has Chinese and Spanish."""
    import re
    D = json.load(open("tools/i18n/compiled.json", encoding="utf-8"))  # written by tools/i18n/build_i18n.py
    missing = []
    for f in ["public/admin.html", "public/admin-tasks.js", "public/admin-labels.js", "public/admin-freight.js", "public/admin-inventory.js"]:
        for m in re.finditer(r'\bt\("((?:[^"\\]|\\.)*)"', open(f, encoding="utf-8").read()):
            k = json.loads('"' + m.group(1) + '"')
            if re.search(r"[㐀-鿿]", k) and (k not in D["en"] or k not in D["es"]):
                missing.append(k)
    for f in ["public/index.html", "public/success.html", "public/schedule.html"]:
        for m in re.finditer(r'\bt\("((?:[^"\\]|\\.)*)"', open(f, encoding="utf-8").read()):
            k = json.loads('"' + m.group(1) + '"')
            if re.search(r"[A-Za-z]{2}", k) and k not in ("Mujian",) and (k not in D["zh"] or k not in D["es"]):
                missing.append(k)
    assert not missing, missing
