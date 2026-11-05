"""Customer address book: grouping orders by person, notes, tags/memo, vCard export."""
from urllib.parse import quote


def test_orders_group_into_one_customer(server, admin, buy):
    a = buy([("c1", 1)], customer=2)
    b = buy([("l1", 1)], customer=2)
    o = admin.get(f"/api/admin/orders/{a}").get_json()
    key = o["customer_key"]
    assert key and key == admin.get(f"/api/admin/orders/{b}").get_json()["customer_key"]
    admin.post(f"/api/admin/orders/{a}/notes", json={"text": "Has a big dog, call 10 min before arriving"})
    book = admin.get("/api/admin/customers").get_json()["customers"]
    me = next(p for p in book if p["key"] == key)
    assert me["orders"] >= 2 and me["spent"] > 0 and me["name"]
    p = admin.get("/api/admin/customers/" + quote(key, safe="")).get_json()
    assert {x["order_no"] for x in p["orders_list"]} >= {o["order_no"]}
    assert any("dog" in n["text"] for n in p["notes"])
    assert all("items" in x for x in p["orders_list"]) and p["orders_list"][0]["items"][0].get("cat") is not None


def test_tags_and_memo(server, admin, buy):
    oid = buy([("c1", 1)], customer=3)
    key = admin.get(f"/api/admin/orders/{oid}").get_json()["customer_key"]
    url = "/api/admin/customers/" + quote(key, safe="")
    r = admin.post(url, json={"tags": ["⭐ VIP", " ⭐ VIP ", "Designer"], "memo": "Prefers Saturday mornings"}).get_json()
    assert r["tags"] == ["⭐ VIP", "Designer"] and r["memo"] == "Prefers Saturday mornings"
    r = admin.post(url, json={"memo": "Son receives deliveries"}).get_json()
    assert r["tags"] == ["⭐ VIP", "Designer"]  # memo-only save keeps tags
    d = admin.get("/api/admin/customers").get_json()
    assert "Designer" in d["tags"]
    assert admin.post("/api/admin/customers/nobody%40example.com", json={"memo": "x"}).status_code == 404
    assert admin.get("/api/admin/customers/nobody%40example.com").status_code == 404


def test_vcard_export_skips_test_orders(server, admin, buy):
    real = buy([("c1", 1)], customer=4)
    test_only = buy([("c1", 1)], customer=5)
    with server.db() as c:
        c.execute("UPDATE orders SET test=0 WHERE id=?", (real,))
    real_o = admin.get(f"/api/admin/orders/{real}").get_json()
    test_o = admin.get(f"/api/admin/orders/{test_only}").get_json()
    r = admin.get("/api/admin/customers.vcf")
    assert r.status_code == 200 and "text/vcard" in r.content_type
    body = r.get_data(as_text=True)
    assert body.count("BEGIN:VCARD") >= 1 and real_o["email"] in body
    assert test_o["email"] not in body or test_o["email"] == real_o["email"]
    with server.db() as c:
        c.execute("UPDATE orders SET test=1 WHERE id=?", (real,))


def test_customers_need_login(client):
    assert client.get("/api/admin/customers").status_code == 401
    assert client.get("/api/admin/customers.vcf").status_code == 401
