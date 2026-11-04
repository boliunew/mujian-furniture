"""Things that go wrong in real operation: duplicate webhooks, two workers at once, refunds, backups."""
import json
import threading
import time

import pytest


def stripe_event(server, monkeypatch, etype, obj):
    """Deliver a webhook as if Stripe signed it."""
    monkeypatch.setattr(server.stripe.Webhook, "construct_event", lambda *a, **k: None)
    return server.app.test_client().post("/webhook", data=json.dumps({"type": etype, "data": {"object": obj}}))


def pending_order(server, sid, items=(("c1", 1),)):
    with server.db() as c:
        its, sub = server.validate_cart(c, [{"id": i, "qty": q} for i, q in items])
        server.create_order(c, its, sub, sid)
    return sid


def session_obj(sid, status="paid", total=27800, pi="pi_test_1"):
    return {"id": sid, "payment_status": status, "amount_total": total, "payment_intent": pi,
            "total_details": {"amount_shipping": 4900},
            "customer_details": {"email": "buyer@example.com", "phone": "+14155550100"},
            "collected_information": {"shipping_details": {"name": "Buyer One", "address": {
                "line1": "1 Main St", "city": "Oakland", "state": "CA", "postal_code": "94607"}}}}


def o_no(server, sid):
    with server.db() as c:
        return c.execute("SELECT order_no FROM orders WHERE id=?", (sid,)).fetchone()[0]


def test_duplicate_webhook_counts_once(server, monkeypatch):
    sid = pending_order(server, "cs_dup_1")
    for _ in range(3):
        assert stripe_event(server, monkeypatch, "checkout.session.completed", session_obj(sid)).status_code == 200
    with server.db() as c:
        paid = c.execute("SELECT COUNT(*) FROM events WHERE order_id=? AND kind='paid'", (sid,)).fetchone()[0]
        mails = c.execute("SELECT COUNT(*) FROM outbox WHERE order_id=? AND kind='staff'", (sid,)).fetchone()[0]
        pi = c.execute("SELECT payment_intent FROM orders WHERE id=?", (sid,)).fetchone()[0]
    assert paid == 1
    assert mails <= 1
    assert pi == "pi_test_1"


def test_parallel_webhooks_count_once(server, monkeypatch):
    sid = pending_order(server, "cs_par_1")
    monkeypatch.setattr(server.stripe.Webhook, "construct_event", lambda *a, **k: None)
    body = json.dumps({"type": "checkout.session.completed", "data": {"object": session_obj(sid)}})
    codes = []
    ts = [threading.Thread(target=lambda: codes.append(server.app.test_client().post("/webhook", data=body).status_code))
          for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes == [200] * 4
    with server.db() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE order_id=? AND kind='paid'", (sid,)).fetchone()[0] == 1


def test_async_payment(server, monkeypatch):
    sid = pending_order(server, "cs_async_1")
    stripe_event(server, monkeypatch, "checkout.session.completed", session_obj(sid, status="unpaid"))
    with server.db() as c:
        assert c.execute("SELECT status FROM orders WHERE id=?", (sid,)).fetchone()[0] == "pending"
    stripe_event(server, monkeypatch, "checkout.session.async_payment_succeeded", session_obj(sid))
    with server.db() as c:
        assert c.execute("SELECT status FROM orders WHERE id=?", (sid,)).fetchone()[0] == "paid"
    sid2 = pending_order(server, "cs_async_2")
    stripe_event(server, monkeypatch, "checkout.session.completed", session_obj(sid2, status="unpaid"))
    stripe_event(server, monkeypatch, "checkout.session.async_payment_failed", session_obj(sid2, status="unpaid"))
    with server.db() as c:
        assert c.execute("SELECT status FROM orders WHERE id=?", (sid2,)).fetchone()[0] == "canceled"


def test_order_numbers_unique_under_load(server):
    errors, ids = [], []

    def make(i):
        try:
            sid = f"cs_load_{i}"
            pending_order(server, sid, (("c1", 1),))
            ids.append(sid)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=make, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    with server.db() as c:
        nos = [r[0] for r in c.execute(f"SELECT order_no FROM orders WHERE id IN ({','.join('?' * len(ids))})", ids)]
    assert len(set(nos)) == 12


def test_todo_reminder_emails_once_with_two_workers(server, admin, buy):
    oid = buy([("l1", 1)])
    admin.post("/api/admin/mail", json={"host": "127.0.0.1", "port": 2525, "from_addr": "a@b.co", "staff": "ops@b.co",
                                        "notify_todos": True})
    admin.post("/api/admin/todos", json={"title": "Call buyer", "due_at": "2020-01-01T00:00:00Z", "order_id": oid, "email": True})
    ts = [threading.Thread(target=server.run_reminders) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    with server.db() as c:
        n = c.execute("SELECT COUNT(*) FROM outbox WHERE subject LIKE '%Call buyer%'").fetchone()[0]
    admin.post("/api/admin/mail", json={"host": "", "staff": ""})
    assert n == 1


def test_stuck_sending_emails_are_retried(server):
    with server.db() as c:
        c.execute("""INSERT INTO outbox (created_at, to_addr, subject, body, kind, status, attempts, claimed_at)
                     VALUES ('2020-01-01T00:00:00+00:00','x@y.co','s','b','staff','sending',1,'2020-01-01T00:00:00+00:00')""")
        eid = c.execute("SELECT MAX(id) FROM outbox").fetchone()[0]
    server.mailer.release_stuck(server.db)
    with server.db() as c:
        assert c.execute("SELECT status FROM outbox WHERE id=?", (eid,)).fetchone()[0] == "queued"


def test_test_orders_do_not_email_customers(server, buy):
    oid = buy([("tv1", 1)], customer=1)
    with server.db() as c:
        rows = c.execute("SELECT status FROM outbox WHERE order_id=? AND kind='customer'", (oid,)).fetchall()
    assert rows and all(r[0] == "held" for r in rows)


def test_purge_test_orders_restores_stock(server, admin, buy):
    before = {p["id"]: p for p in admin.get("/api/admin/inventory").get_json()["products"]}
    oid = buy([("l1", 1)])
    admin.post(f"/api/admin/orders/{oid}", json={"status": "shipped", "carrier": "USPS", "tracking": "9400"})
    r = admin.post("/api/admin/test-orders/purge").get_json()
    assert r["deleted"] >= 1
    after = {p["id"]: p for p in admin.get("/api/admin/inventory").get_json()["products"]}
    assert after["l1"]["total"] == before["l1"]["total"]
    assert admin.get(f"/api/admin/orders/{oid}").status_code == 404
    with server.db() as c:
        assert not c.execute("SELECT 1 FROM orders WHERE test=1").fetchone()


def test_cancel_refunds_stripe_payment(server, admin, monkeypatch):
    sid = pending_order(server, "cs_refund_1", (("l1", 1),))
    stripe_event(server, monkeypatch, "checkout.session.completed", session_obj(sid, total=44800, pi="pi_refund_1"))
    calls = []

    class FakeRefund:
        @staticmethod
        def create(**kw):
            calls.append(kw)
            return {"id": "re_1", "amount": 44800, "status": "succeeded"}
    monkeypatch.setattr(server.stripe, "Refund", FakeRefund)
    monkeypatch.setattr(server.stripe, "api_key", "sk_test_x")
    o = admin.post(f"/api/admin/orders/{sid}", json={"status": "canceled", "refund": True, "confirm": o_no(server, sid)}).get_json()
    assert o["status"] == "canceled" and o["refunded"] == 44800
    assert calls and calls[0]["payment_intent"] == "pi_refund_1"
    # a refund made in the Stripe dashboard is recorded too, once
    stripe_event(server, monkeypatch, "charge.refunded", {"payment_intent": "pi_refund_1", "amount_refunded": 44800})
    with server.db() as c:
        assert c.execute("SELECT refunded FROM orders WHERE id=?", (sid,)).fetchone()[0] == 44800


def test_refund_failure_keeps_order(server, admin, monkeypatch):
    sid = pending_order(server, "cs_refund_2", (("l1", 1),))
    stripe_event(server, monkeypatch, "checkout.session.completed", session_obj(sid, pi="pi_refund_2"))

    class BrokenRefund:
        @staticmethod
        def create(**kw):
            raise server.stripe.StripeError("card network down")
    monkeypatch.setattr(server.stripe, "Refund", BrokenRefund)
    monkeypatch.setattr(server.stripe, "api_key", "sk_test_x")
    r = admin.post(f"/api/admin/orders/{sid}", json={"status": "canceled", "refund": True, "confirm": o_no(server, sid)})
    assert r.status_code == 502
    assert admin.get(f"/api/admin/orders/{sid}").get_json()["status"] == "paid"


def test_backup_download_and_daily(server, admin, tmp_path):
    r = admin.get("/api/admin/backup")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    import io
    import sqlite3
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert "shop.db" in z.namelist()
    db = tmp_path / "restored.db"
    db.write_bytes(z.read("shop.db"))
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM products").fetchone()[0] >= 8
    p = server.backup.daily(server.DB_PATH, str(tmp_path / "bk"), keep=2)
    assert p and p.endswith(".db")
    assert server.backup.daily(server.DB_PATH, str(tmp_path / "bk"), keep=2) is None  # once a day


def test_csv_blocks_formulas(server, admin, buy):
    oid = buy([("c1", 1)])
    with server.db() as c:
        c.execute("UPDATE orders SET ship_name='=HYPERLINK(\"http://evil\")' WHERE id=?", (oid,))
    body = admin.get("/api/admin/orders.csv").get_data(as_text=True)
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body


def test_bad_query_params_are_400_not_500(admin):
    assert admin.get("/api/admin/deliveries?days=abc").status_code == 400
    assert admin.get("/api/admin/movements?limit=abc").status_code == 400


@pytest.mark.parametrize("hour,expect", [(9, False), (11, True)])
def test_day_before_reminders_run_automatically(server, admin, buy, monkeypatch, hour, expect):
    from datetime import datetime, timedelta
    oid = buy([("tv1", 1)], customer=1)
    tomorrow = (server.fr.today() + timedelta(days=1)).isoformat()
    with server.db() as c:
        c.execute("UPDATE orders SET appt_date=?, appt_window='am', freight_status='scheduled', reminded_for=NULL WHERE id=?",
                  (tomorrow, oid))
    monkeypatch.setattr(server, "store_hour", lambda: hour)
    server.run_reminders()
    with server.db() as c:
        got = c.execute("SELECT reminded_for FROM orders WHERE id=?", (oid,)).fetchone()[0]
    assert (got == tomorrow) is expect


def test_refund_needs_order_number_and_goods_in_hand(server, admin, monkeypatch):
    calls = []

    class FakeRefund:
        @staticmethod
        def create(**kw):
            calls.append(kw)
            return {"id": "re_x"}
    monkeypatch.setattr(server.stripe, "Refund", FakeRefund)
    monkeypatch.setattr(server.stripe, "api_key", "sk_test_x")
    sid = pending_order(server, "cs_guard_1", (("l1", 1),))
    stripe_event(server, monkeypatch, "checkout.session.completed", session_obj(sid, pi="pi_guard_1"))
    url = f"/api/admin/orders/{sid}"
    # no / wrong order number: nothing happens
    assert admin.post(url, json={"status": "canceled", "refund": True}).status_code == 400
    assert admin.post(url, json={"status": "canceled", "refund": True, "confirm": "MJ-1"}).status_code == 400
    # shipped, then put back to "paid": still no refund from here
    admin.post(url, json={"status": "shipped", "carrier": "USPS", "tracking": "9400"})
    admin.post(url, json={"status": "paid"})
    r = admin.post(url, json={"status": "canceled", "refund": True, "confirm": o_no(server, sid)})
    assert r.status_code == 400 and "Stripe" in r.get_json()["error"]
    assert not calls
    assert admin.get(url).get_json()["status"] == "paid"
