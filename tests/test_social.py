"""Social media: profile links, product share pages, catalog feed and posting."""
import json
import xml.etree.ElementTree as ET

import pytest


@pytest.fixture()
def social_on(server, admin, monkeypatch):
    """Facebook + Instagram + Telegram connected, site 'online', HTTP calls recorded instead of sent."""
    calls = []

    def fake_http(method, url, data=None, timeout=25):
        calls.append((method, url, dict(data or {})))
        if url.endswith("/media"):
            return {"id": "c1"}
        if url.endswith("/media_publish"):
            return {"id": "ig1"}
        if "fields=permalink" in url:
            return {"permalink": "https://instagram.com/p/x"}
        if url.endswith("/photos"):
            return {"id": "ph1", "post_id": "111_222"}
        if "sendPhoto" in url or "sendMessage" in url:
            return {"result": {"message_id": 7, "chat": {"username": "mujianshop"}}}
        return {}
    monkeypatch.setattr(server.social, "_http", fake_http)
    monkeypatch.setattr(server, "LOCAL", False)
    monkeypatch.setattr(server, "DOMAIN", "https://shop.example.com")
    r = admin.post("/api/admin/social", json={"fb_page_id": "123", "fb_token": "EAAsecret", "ig_user_id": "17841",
                                               "tg_token": "1:tgsecret", "tg_chat": "@mujianshop", "auto_new": False})
    assert r.status_code == 200
    yield calls
    admin.post("/api/admin/social", json={"fb_token": "-", "tg_token": "-", "fb_page_id": "", "ig_user_id": "", "tg_chat": "",
                                           "auto_new": False, "links": {k: "" for k in server.social.LINKS}})
    with server.db() as c:
        c.execute("DELETE FROM social_posts")


def test_store_shows_profile_links(server, admin, client):
    admin.post("/api/admin/social", json={"links": {"instagram": "@mujian.home", "whatsapp": "(510) 555-0100",
                                                     "facebook": "https://facebook.com/mujianhome"}})
    s = client.get("/api/store").get_json()["social"]
    assert s["instagram"] == "https://www.instagram.com/mujian.home"
    assert s["whatsapp"] == "https://wa.me/15105550100"
    assert s["facebook"] == "https://facebook.com/mujianhome"
    assert "tiktok" not in s
    assert admin.post("/api/admin/social", json={"links": {"x": "not a url with spaces"}}).status_code == 400
    admin.post("/api/admin/social", json={"links": {k: "" for k in server.social.LINKS}})


def test_share_page_has_preview_tags(client):
    html = client.get("/p/tv1").get_data(as_text=True)
    assert 'property="og:title"' in html and "$" in html
    assert 'property="og:image"' in html and "/demo-spin/tv1/00.jpg" in html  # 360° frame stands in for a photo
    assert '"@type": "Product"' in html and '"priceCurrency": "USD"' in html
    assert 'location.replace("/?p=tv1")' in html


def test_preview_image_for_product_without_photo(client):
    r = client.get("/og/s1.jpg")
    assert r.status_code == 200 and r.data[:2] == b"\xff\xd8"
    assert client.get("/og/nope.jpg").status_code == 404


def test_catalog_feed(client, server):
    r = client.get("/feeds/products.xml")
    assert r.status_code == 200 and "xml" in r.content_type
    ns = {"g": "http://base.google.com/ns/1.0"}
    items = ET.fromstring(r.data).findall("./channel/item")
    with server.db() as c:
        assert len(items) == c.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0]
    first = items[0]
    assert first.find("g:price", ns).text.endswith(" USD")
    assert first.find("g:availability", ns).text in ("in_stock", "out_of_stock")
    assert first.find("link").text.split("/p/")[1]
    es = client.get("/feeds/products.xml?lang=es").get_data(as_text=True)
    assert "Sofá" in es or "sofá" in es.lower()


def test_tokens_stay_on_server(admin, social_on):
    s = admin.get("/api/admin/social").get_json()
    assert "fb_token" not in s and "tg_token" not in s
    assert s["has_fb_token"] and s["ready"] == {"facebook": True, "instagram": True, "telegram": True}
    # saving without the token keeps it
    admin.post("/api/admin/social", json={"hashtags": "#oak"})
    assert admin.get("/api/admin/social").get_json()["has_fb_token"]


def test_post_product_everywhere(server, admin, social_on):
    r = admin.post("/api/admin/social/post", json={"product_id": "tv1", "targets": ["facebook", "instagram", "telegram"]})
    assert r.status_code == 200
    server.social.flush(server.db, server.DOMAIN, server.get_product)
    post = admin.get("/api/admin/social/posts").get_json()["posts"][0]
    assert post["status"] == "done", post
    assert post["results"]["instagram"]["url"] == "https://instagram.com/p/x"
    assert post["results"]["telegram"]["url"] == "https://t.me/mujianshop/7"
    urls = [u for _, u, _ in social_on]
    assert any(u.endswith("/123/photos") for u in urls) and any(u.endswith("/17841/media_publish") for u in urls)
    fb = next(d for _, u, d in social_on if u.endswith("/photos"))
    assert fb["url"] == "https://shop.example.com/demo-spin/tv1/00.jpg"
    assert "https://shop.example.com/p/tv1?utm_source=facebook" in fb["caption"]
    assert "#" in fb["caption"]


def test_one_platform_failing_is_retried_alone(server, admin, social_on, monkeypatch):
    real = server.social._http

    def flaky(method, url, data=None, timeout=25):
        if url.endswith("/media"):
            raise server.social.PostError("Instagram is down")
        return real(method, url, data, timeout)
    monkeypatch.setattr(server.social, "_http", flaky)
    admin.post("/api/admin/social/post", json={"product_id": "s1", "text": "Weekend sale 🎉", "targets": ["facebook", "instagram"]})
    server.social.flush(server.db, server.DOMAIN, server.get_product)
    with server.db() as c:
        row = c.execute("SELECT * FROM social_posts ORDER BY id DESC").fetchone()
        assert row["status"] == "queued"  # instagram will be tried again
        res = json.loads(row["results"])
        assert res["facebook"]["ok"] and not res["instagram"]["ok"]
        c.execute("UPDATE social_posts SET run_at='2000-01-01' WHERE id=?", (row["id"],))
    monkeypatch.setattr(server.social, "_http", real)
    n_fb = sum(1 for _, u, _ in social_on if u.endswith("/photos"))
    server.social.flush(server.db, server.DOMAIN, server.get_product)
    assert sum(1 for _, u, _ in social_on if u.endswith("/photos")) == n_fb  # facebook not posted twice
    with server.db() as c:
        assert c.execute("SELECT status FROM social_posts ORDER BY id DESC").fetchone()[0] == "done"


def test_new_product_is_posted_once_later(server, admin, social_on):
    admin.post("/api/admin/social", json={"auto_new": True})
    p = admin.post("/api/admin/products", json={"name": "Oak Bench", "price": 39900, "stock": 3}).get_json()
    admin.post("/api/admin/products", json={**p, "price": 41900})  # editing doesn't post again
    with server.db() as c:
        rows = c.execute("SELECT * FROM social_posts WHERE product_id=?", (p["id"],)).fetchall()
    assert len(rows) == 1 and rows[0]["kind"] == "auto" and rows[0]["run_at"] > rows[0]["created_at"]
    admin.delete(f"/api/admin/products/{p['id']}")


def test_cannot_post_from_localhost(server, admin, monkeypatch):
    monkeypatch.setattr(server, "LOCAL", True)
    r = admin.post("/api/admin/social/post", json={"text": "hi", "targets": ["telegram"]})
    assert r.status_code == 400


def test_check_finds_instagram_account(server, admin, social_on, monkeypatch):
    def fake(method, url, data=None, timeout=25):
        if "graph.facebook.com" in url:
            return {"name": "Mujian Home", "instagram_business_account": {"id": "999", "username": "mujian.home"}}
        return {"result": {"username": "mujianbot", "title": "Mujian deals"}}
    monkeypatch.setattr(server.social, "_http", fake)
    admin.post("/api/admin/social", json={"ig_user_id": ""})
    res = admin.post("/api/admin/social/check").get_json()["results"]
    assert res["facebook"]["ok"] and "@mujian.home" in res["instagram"]["text"] and res["telegram"]["ok"]
    assert admin.get("/api/admin/social").get_json()["ig_user_id"] == "999"


def test_text_only_instagram_is_not_retried(server, admin, social_on):
    admin.post("/api/admin/social/post", json={"text": "Weekend sale 🎉", "targets": ["telegram", "instagram"]})
    server.social.flush(server.db, server.DOMAIN, server.get_product)
    with server.db() as c:
        row = c.execute("SELECT * FROM social_posts ORDER BY id DESC").fetchone()
    assert row["status"] == "partial"
    assert json.loads(row["results"])["telegram"]["ok"]
