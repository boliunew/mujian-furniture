"""Social media: profile links, a product catalog feed, and posts to Facebook / Instagram / Telegram.

Posts are queued in the `social_posts` table (inside the caller's transaction) and sent by the
background worker, like emails. Each platform is tried on its own: if Instagram fails and
Facebook works, only Instagram is retried. Tokens never leave the server.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

GRAPH = "https://graph.facebook.com/" + os.getenv("META_GRAPH_VERSION", "v23.0")
LINKS = ["instagram", "facebook", "tiktok", "pinterest", "youtube", "x", "whatsapp"]
TARGETS = ["facebook", "instagram", "telegram"]
DEFAULTS = {
    "links": {k: "" for k in LINKS},
    "fb_page_id": "", "fb_token": "", "ig_user_id": "", "tg_token": "", "tg_chat": "",
    "auto_new": False, "lang": "en", "hashtags": "#furniture #solidwood #homedecor #interiordesign",
    "targets": ["facebook", "instagram", "telegram"],
}
SECRETS = ("fb_token", "tg_token")
MAX_TRIES = 3


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS social_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, run_at TEXT, kind TEXT, product_id TEXT,
        text TEXT, targets TEXT, status TEXT, results TEXT, attempts INTEGER DEFAULT 0, claimed_at TEXT, done_at TEXT)""")


# ---------------------------------------------------------------- settings
def load(c):
    s = json.loads(json.dumps(DEFAULTS))
    row = c.execute("SELECT value FROM settings WHERE key='social'").fetchone()
    if row:
        saved = json.loads(row[0])
        s["links"].update(saved.pop("links", {}) or {})
        s.update(saved)
    return s


def configured(s):
    """Which platforms have what they need to post."""
    return {"facebook": bool(s.get("fb_page_id") and s.get("fb_token")),
            "instagram": bool(s.get("ig_user_id") and s.get("fb_token")),
            "telegram": bool(s.get("tg_token") and s.get("tg_chat"))}


def public(s):
    out = {k: v for k, v in s.items() if k not in SECRETS}
    for k in SECRETS:
        out["has_" + k] = bool(s.get(k))
    out["ready"] = configured(s)
    return out


def links_public(s):
    """Profile links shown in the store footer (only the filled ones, as full URLs)."""
    out = {}
    for k in LINKS:
        v = str((s.get("links") or {}).get(k) or "").strip()
        if not v:
            continue
        if k == "whatsapp":
            digits = "".join(ch for ch in v if ch.isdigit())
            if v.startswith("http"):
                out[k] = v
            elif digits:
                out[k] = "https://wa.me/" + (digits if len(digits) > 10 else "1" + digits)
        elif v.startswith("http"):
            out[k] = v
        else:  # just a handle
            h = v.lstrip("@")
            out[k] = {"instagram": "https://www.instagram.com/{}", "facebook": "https://www.facebook.com/{}",
                      "tiktok": "https://www.tiktok.com/@{}", "pinterest": "https://www.pinterest.com/{}",
                      "youtube": "https://www.youtube.com/@{}", "x": "https://x.com/{}"}[k].format(h)
    return out


def clean(body, old):
    s = json.loads(json.dumps(old))
    links = body.get("links")
    if isinstance(links, dict):
        for k in LINKS:
            if k in links:
                v = str(links[k] or "").strip()[:200]
                if v and k != "whatsapp" and not (v.startswith("https://") or v.startswith("http://") or len(v) <= 60 and " " not in v):
                    raise ValueError(f"{k} 链接不对：填完整网址（https://…）或者账号名")
                s["links"][k] = v
    for k in ("fb_page_id", "ig_user_id", "tg_chat"):
        if k in body:
            s[k] = str(body[k] or "").strip()[:80]
    for k in SECRETS:  # blank = keep the saved token; "-" = remove it
        v = str(body.get(k) or "").strip()
        if v == "-":
            s[k] = ""
        elif v:
            s[k] = v[:600]
    if "auto_new" in body:
        s["auto_new"] = bool(body["auto_new"])
    if body.get("lang") in ("en", "es", "both"):
        s["lang"] = body["lang"]
    if "hashtags" in body:
        s["hashtags"] = " ".join(str(body["hashtags"] or "").split())[:300]
    if isinstance(body.get("targets"), list):
        s["targets"] = [t for t in TARGETS if t in body["targets"]]
    return s


# ---------------------------------------------------------------- content
SHIP = {"en": {"freight": "🚛 Truck delivery, you pick the day", "parcel": "📦 Ships to your door"},
        "es": {"freight": "🚛 Entrega en camión, tú eliges el día", "parcel": "📦 Envío a tu puerta"}}
SHOP = {"en": "🛒 Shop:", "es": "🛒 Compra:"}


def money(c):
    return f"${c / 100:,.2f}".replace(".00", "")


def name_in(p, lang):
    tr = p.get("i18n") or {}
    return ((tr.get(lang) or {}).get("name") if lang != "en" else None) or p["name"]


def mat_in(p, lang):
    tr = p.get("i18n") or {}
    return ((tr.get(lang) or {}).get("mat") if lang != "en" else None) or p.get("mat") or ""


def caption(p, link, s):
    """Post text for a product, in the store's posting language(s)."""
    langs = ["en", "es"] if s.get("lang") == "both" else [s.get("lang") or "en"]
    parts = []
    for lg in langs:
        lines = [f"🪵 {name_in(p, lg)} · {money(p['price'])}"]
        spec = " · ".join(x for x in (p.get("dim"), mat_in(p, lg)) if x)
        if spec:
            lines.append(spec)
        lines.append(SHIP[lg]["freight" if p.get("ship_mode") != "parcel" else "parcel"])
        parts.append("\n".join(lines))
    text = "\n\n".join(parts) + f"\n\n{SHOP[langs[0]]} {link}"
    if s.get("hashtags"):
        text += "\n\n" + s["hashtags"]
    return text


def image_path(p):
    """Best real picture of a product: its photo, else the first 360° frame (site-relative), else None."""
    if p.get("photo"):
        return p["photo"]
    if p.get("spin_frames"):
        return p["spin_frames"][0]
    return None


# ---------------------------------------------------------------- catalog feed (Google / Meta / Pinterest / TikTok)
GOOGLE_CATEGORY = "436"  # Furniture


def feed_xml(products, domain, store_name, lang="en"):
    """RSS 2.0 with Google's g: fields. Meta, Pinterest and TikTok catalogs read the same file."""
    items = []
    for p in products:
        img = image_path(p) or f"/og/{p['id']}.jpg"
        extra = [f for f in (p.get("spin_frames") or [])[::max(1, len(p.get("spin_frames") or []) // 6)][:6] if f != img]
        name = name_in(p, lang)
        desc = f"{name}. {p.get('dim') or ''}. {mat_in(p, lang)}. " + SHIP["es" if lang == "es" else "en"]["freight" if p.get("ship_mode") != "parcel" else "parcel"][2:] + "."
        fields = [
            ("g:id", p.get("sku") or p["id"]), ("title", name), ("description", " ".join(desc.split())),
            ("link", f"{domain}/p/{p['id']}"), ("g:image_link", domain + img),
            *[("g:additional_image_link", domain + f) for f in extra],
            ("g:availability", "in_stock" if p.get("stock", 0) > 0 else "out_of_stock"),
            ("g:price", f"{p['price'] / 100:.2f} USD"), ("g:brand", store_name), ("g:condition", "new"),
            ("g:google_product_category", GOOGLE_CATEGORY), ("g:product_type", p.get("cat") or "Furniture"),
            ("g:mpn", p.get("sku") or p["id"]), ("g:identifier_exists", "no"),
        ]
        if p.get("weight_lb"):
            fields.append(("g:shipping_weight", f"{p['weight_lb']} lb"))
        items.append("<item>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in fields) + "</item>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0"><channel>'
            f"<title>{escape(store_name)}</title><link>{escape(domain)}/</link><description>{escape(store_name)} products</description>"
            + "".join(items) + "</channel></rss>\n")


# ---------------------------------------------------------------- posting
class PostError(Exception):
    pass


class Permanent(PostError):
    """Trying again won't help (e.g. Instagram post without a picture)."""


def _http(method, url, data=None, timeout=25):
    """Small JSON HTTP helper (no extra packages). Tests replace this."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode() or "{}")
        except Exception:
            j = {}
        msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else j.get("description")
        raise PostError(msg or f"HTTP {e.code}")
    except urllib.error.URLError as e:
        raise PostError(f"连不上：{e.reason}")


def post_facebook(s, text, image, link):
    if image:
        r = _http("POST", f"{GRAPH}/{s['fb_page_id']}/photos", {"url": image, "caption": text, "access_token": s["fb_token"]})
        pid = r.get("post_id") or r.get("id")
    else:
        r = _http("POST", f"{GRAPH}/{s['fb_page_id']}/feed", {"message": text, "link": link, "access_token": s["fb_token"]})
        pid = r.get("id")
    return {"id": pid, "url": f"https://www.facebook.com/{pid}" if pid else ""}


def post_instagram(s, text, image, link):
    if not image:
        raise Permanent("Instagram 只能发带图片的帖子（选一个商品，或者换别的平台）")
    r = _http("POST", f"{GRAPH}/{s['ig_user_id']}/media", {"image_url": image, "caption": text, "access_token": s["fb_token"]})
    if not r.get("id"):
        raise PostError("Instagram 没收到图片")
    done = _http("POST", f"{GRAPH}/{s['ig_user_id']}/media_publish", {"creation_id": r["id"], "access_token": s["fb_token"]})
    url = ""
    try:
        url = _http("GET", f"{GRAPH}/{done['id']}?" + urllib.parse.urlencode({"fields": "permalink", "access_token": s["fb_token"]})).get("permalink", "")
    except Exception:
        pass
    return {"id": done.get("id"), "url": url}


def post_telegram(s, text, image, link):
    api = f"https://api.telegram.org/bot{s['tg_token']}"
    if image:
        r = _http("POST", api + "/sendPhoto", {"chat_id": s["tg_chat"], "photo": image, "caption": text[:1024]})
    else:
        r = _http("POST", api + "/sendMessage", {"chat_id": s["tg_chat"], "text": text})
    m = r.get("result") or {}
    chat = m.get("chat") or {}
    url = f"https://t.me/{chat['username']}/{m.get('message_id')}" if chat.get("username") else ""
    return {"id": m.get("message_id"), "url": url}


SENDERS = {"facebook": post_facebook, "instagram": post_instagram, "telegram": post_telegram}


def check(s):
    """Test each connection without posting anything. May fill in the Instagram account id."""
    out = {}
    if s.get("fb_page_id") and s.get("fb_token"):
        try:
            r = _http("GET", f"{GRAPH}/{s['fb_page_id']}?" + urllib.parse.urlencode(
                {"fields": "name,instagram_business_account{id,username}", "access_token": s["fb_token"]}))
            out["facebook"] = {"ok": True, "text": f"✅ Facebook 主页：{r.get('name', '')}"}
            ig = r.get("instagram_business_account") or {}
            if ig.get("id"):
                s["ig_user_id"] = s.get("ig_user_id") or ig["id"]
                out["instagram"] = {"ok": True, "text": f"✅ Instagram：@{ig.get('username', '')}"}
            else:
                out["instagram"] = {"ok": False, "text": "⚠️ 这个 Facebook 主页还没绑定 Instagram 商业/创作者账号"}
        except PostError as e:
            out["facebook"] = {"ok": False, "text": f"❌ Facebook：{e}"}
    if s.get("tg_token") and s.get("tg_chat"):
        try:
            me = _http("GET", f"https://api.telegram.org/bot{s['tg_token']}/getMe").get("result") or {}
            ch = _http("GET", f"https://api.telegram.org/bot{s['tg_token']}/getChat?" + urllib.parse.urlencode({"chat_id": s["tg_chat"]})).get("result") or {}
            out["telegram"] = {"ok": True, "text": f"✅ Telegram：@{me.get('username', '')} → {ch.get('title') or ch.get('username') or s['tg_chat']}"}
        except PostError as e:
            out["telegram"] = {"ok": False, "text": f"❌ Telegram：{e}"}
    return out


def queue(c, targets, text="", product_id=None, kind="manual", delay_min=0):
    run_at = (datetime.now(timezone.utc) + timedelta(minutes=delay_min)).isoformat(timespec="seconds")
    c.execute("""INSERT INTO social_posts (created_at, run_at, kind, product_id, text, targets, status, results)
                 VALUES (?,?,?,?,?,?,'queued','{}')""", (now(), run_at, kind, product_id, text, json.dumps(targets)))
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def release_stuck(db, minutes=10):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with db() as c:
        c.execute("UPDATE social_posts SET status='queued' WHERE status='sending' AND (claimed_at IS NULL OR claimed_at<?)", (cutoff,))


def flush(db, domain, get_product, limit=5):
    """Send due posts. `get_product(c, id)` returns the product as the store shows it (or None)."""
    release_stuck(db)
    with db() as c:
        s = load(c)
        ids = [r[0] for r in c.execute("SELECT id FROM social_posts WHERE status='queued' AND run_at<=? ORDER BY id LIMIT ?", (now(), limit))]
    sent = 0
    for i in ids:
        with db() as c:
            if not c.execute("UPDATE social_posts SET status='sending', attempts=attempts+1, claimed_at=? WHERE id=? AND status='queued'",
                             (now(), i)).rowcount:
                continue
            row = dict(c.execute("SELECT * FROM social_posts WHERE id=?", (i,)).fetchone())
            p = get_product(c, row["product_id"]) if row["product_id"] else None
        results = json.loads(row["results"] or "{}")
        text, image, link = row["text"] or "", None, f"{domain}/"
        if row["product_id"]:
            if not p:
                results["_"] = {"ok": False, "error": "商品已经删除"}
            else:
                link = f"{domain}/p/{p['id']}"
                text = text or caption(p, link, s)
                image = domain + (image_path(p) or f"/og/{p['id']}.jpg")
        ready = configured(s)
        for t in json.loads(row["targets"] or "[]"):
            if (results.get(t) or {}).get("ok") or (results.get(t) or {}).get("final") or "_" in results:
                continue
            if not ready.get(t):
                results[t] = {"ok": False, "error": "还没接好这个平台", "final": True}
                continue
            try:
                link_t = link + (("&" if "?" in link else "?") + f"utm_source={t}&utm_medium=social" if "/p/" in link else "")
                body = text.replace(link, link_t) if link in text else text
                results[t] = {"ok": True, **SENDERS[t](s, body, image, link_t)}
                sent += 1
            except Exception as e:  # noqa: BLE001 — one platform failing must not stop the others
                results[t] = {"ok": False, "error": str(e)[:300], "final": isinstance(e, Permanent)}
        failed = [t for t, r in results.items() if not r.get("ok")]
        retry = failed and "_" not in results and row["attempts"] < MAX_TRIES and \
            any(not r.get("final") for r in results.values() if not r.get("ok"))
        status = "done" if not failed else ("queued" if retry else ("partial" if len(failed) < len(results) else "failed"))
        nxt = (datetime.now(timezone.utc) + timedelta(minutes=5 * row["attempts"])).isoformat(timespec="seconds")
        with db() as c:
            c.execute("UPDATE social_posts SET status=?, results=?, run_at=?, done_at=?, text=? WHERE id=?",
                      (status, json.dumps(results, ensure_ascii=False), nxt if retry else row["run_at"],
                       None if retry else now(), text, i))
    return sent


def recent(c, limit=30):
    out = []
    for r in c.execute("""SELECT sp.*, p.name AS product_name FROM social_posts sp LEFT JOIN products p ON p.id=sp.product_id
                          ORDER BY sp.id DESC LIMIT ?""", (limit,)):
        d = dict(r)
        d["targets"] = json.loads(d["targets"] or "[]")
        d["results"] = json.loads(d["results"] or "{}")
        out.append(d)
    return out
