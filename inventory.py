"""Multi-warehouse inventory: on-hand per warehouse, reservations, stock ledger, counts.

Model
- stock.qty is what is physically on the shelf in that warehouse.
- A paid order reserves its items in the warehouse it ships from (orders.warehouse_id).
  available = qty - reserved. The storefront sells the sum of available across
  warehouses that ship to customers; products.stock caches that number.
- Goods leave on-hand when the order ships (status shipped/delivered); reverting
  the status puts them back. Every change writes one row to movements.
- A count snapshots on-hand when it starts; posting applies (counted - snapshot)
  to the current quantity, so sales that shipped mid-count are not lost.
"""
import json
from datetime import datetime, timezone

KINDS = {
    "init": "期初", "receive": "入库", "ship": "发货出库", "ship_reverse": "撤销出库",
    "adjust": "手动调整", "count": "盘点调整", "transfer_out": "调拨出", "transfer_in": "调拨入",
}
REASONS = ["盘点差异", "损坏", "丢失", "录错", "样品/展示", "退货回库", "其他"]
SEED_WAREHOUSES = [
    ("main", "Oakland Warehouse", "1200 Industrial Way, Oakland, CA 94607", 1, 0),
    ("showroom", "SF Showroom", "350 Brannan St, San Francisco, CA 94107", 0, 1),
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_tables(c):
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS warehouses (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT, ships INTEGER DEFAULT 1, sort INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS stock (
            product_id TEXT, warehouse_id TEXT, qty INTEGER DEFAULT 0, bin TEXT,
            PRIMARY KEY (product_id, warehouse_id)
        );
        CREATE TABLE IF NOT EXISTS movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, product_id TEXT, warehouse_id TEXT,
            delta INTEGER, qty_after INTEGER, kind TEXT, ref TEXT, note TEXT
        );
        CREATE TABLE IF NOT EXISTS counts (
            id TEXT PRIMARY KEY, no TEXT, warehouse_id TEXT, scope TEXT, status TEXT, blind INTEGER DEFAULT 0,
            note TEXT, created_at TEXT, posted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS count_lines (
            count_id TEXT, product_id TEXT, bin TEXT, expected INTEGER, counted INTEGER, reason TEXT,
            PRIMARY KEY (count_id, product_id)
        );
        CREATE INDEX IF NOT EXISTS mv_prod ON movements(product_id, id);
        """
    )
    if c.execute("SELECT COUNT(*) FROM warehouses").fetchone()[0] == 0:
        c.executemany("INSERT INTO warehouses (id, name, address, ships, sort) VALUES (?,?,?,?,?)", SEED_WAREHOUSES)


def migrate_from_product_stock(c):
    """First run on an older database: products.stock was 'available' and paid orders
    had already taken their items off. Rebuild physical on-hand in the main warehouse."""
    if c.execute("SELECT COUNT(*) FROM stock").fetchone()[0]:
        return
    main = c.execute("SELECT id FROM warehouses ORDER BY sort LIMIT 1").fetchone()[0]
    held = {}
    for o in c.execute("SELECT items_json FROM orders WHERE status='paid'"):
        for it in json.loads(o["items_json"]):
            held[it["id"]] = held.get(it["id"], 0) + it["qty"]
    c.execute("UPDATE orders SET warehouse_id=? WHERE warehouse_id IS NULL AND status!='pending'", (main,))
    c.execute("UPDATE orders SET stock_out=1 WHERE status IN ('shipped','delivered')")
    for p in c.execute("SELECT id, stock FROM products").fetchall():
        qty = (p["stock"] or 0) + held.get(p["id"], 0)
        c.execute("INSERT INTO stock (product_id, warehouse_id, qty) VALUES (?,?,?)", (p["id"], main, qty))
        if qty:
            c.execute("INSERT INTO movements (at, product_id, warehouse_id, delta, qty_after, kind, ref, note) VALUES (?,?,?,?,?,?,?,?)",
                      (now(), p["id"], main, qty, qty, "init", "", "期初库存"))
    sync_all(c)


# ------------------------------------------------------------------ reads
def warehouses(c):
    return [dict(r) for r in c.execute("SELECT * FROM warehouses ORDER BY sort, name")]


def reserved_map(c):
    """{(product_id, warehouse_id): qty held by paid, unshipped orders}"""
    out = {}
    for o in c.execute("SELECT items_json, warehouse_id FROM orders WHERE status='paid' AND warehouse_id IS NOT NULL"):
        for it in json.loads(o["items_json"]):
            k = (it["id"], o["warehouse_id"])
            out[k] = out.get(k, 0) + it["qty"]
    return out


def on_hand(c, pid, wid):
    r = c.execute("SELECT qty FROM stock WHERE product_id=? AND warehouse_id=?", (pid, wid)).fetchone()
    return r["qty"] if r else 0


def sync_product(c, pid, reserved=None, ships=None):
    reserved = reserved if reserved is not None else reserved_map(c)
    ships = ships if ships is not None else {r["id"] for r in c.execute("SELECT id FROM warehouses WHERE ships=1")}
    avail = 0
    for s in c.execute("SELECT warehouse_id, qty FROM stock WHERE product_id=?", (pid,)):
        if s["warehouse_id"] in ships:
            avail += max(0, s["qty"] - reserved.get((pid, s["warehouse_id"]), 0))
    c.execute("UPDATE products SET stock=? WHERE id=?", (avail, pid))


def sync_all(c):
    reserved = reserved_map(c)
    ships = {r["id"] for r in c.execute("SELECT id FROM warehouses WHERE ships=1")}
    for p in c.execute("SELECT id FROM products").fetchall():
        sync_product(c, p["id"], reserved, ships)


def overview(c):
    whs = warehouses(c)
    reserved = reserved_map(c)
    stock = {(r["product_id"], r["warehouse_id"]): r for r in c.execute("SELECT * FROM stock")}
    rows = []
    for p in c.execute("SELECT * FROM products ORDER BY sort, name"):
        cells, tot_q, tot_r = {}, 0, 0
        for w in whs:
            s = stock.get((p["id"], w["id"]))
            q, r = (s["qty"] if s else 0), reserved.get((p["id"], w["id"]), 0)
            cells[w["id"]] = {"qty": q, "reserved": r, "available": q - r, "bin": s["bin"] if s else None}
            tot_q, tot_r = tot_q + q, tot_r + r
        rows.append({"id": p["id"], "name": p["name"], "sku": p["sku"], "cat": p["cat"], "art": p["art"],
                     "photo": p["photo"], "active": bool(p["active"]), "sellable": p["stock"],
                     "total": tot_q, "reserved": tot_r, "cells": cells})
    return {"warehouses": whs, "products": rows}


# ------------------------------------------------------------------ writes
def move(c, pid, wid, delta, kind, ref="", note=""):
    if not delta:
        return on_hand(c, pid, wid)
    c.execute("INSERT INTO stock (product_id, warehouse_id, qty) VALUES (?,?,0) ON CONFLICT DO NOTHING", (pid, wid))
    c.execute("UPDATE stock SET qty = qty + ? WHERE product_id=? AND warehouse_id=?", (delta, pid, wid))
    after = on_hand(c, pid, wid)
    c.execute("INSERT INTO movements (at, product_id, warehouse_id, delta, qty_after, kind, ref, note) VALUES (?,?,?,?,?,?,?,?)",
              (now(), pid, wid, delta, after, kind, ref or "", note or ""))
    return after


def allocate(c, items):
    """Pick the first shipping warehouse that can fill every line; else the one that fills most."""
    whs = [w["id"] for w in c.execute("SELECT id FROM warehouses WHERE ships=1 ORDER BY sort, name")]
    if not whs:
        whs = [w["id"] for w in c.execute("SELECT id FROM warehouses ORDER BY sort, name")]
    reserved = reserved_map(c)
    best, best_score = whs[0] if whs else None, -1
    for w in whs:
        score = sum(min(it["qty"], max(0, on_hand(c, it["id"], w) - reserved.get((it["id"], w), 0))) for it in items)
        if score == sum(it["qty"] for it in items):
            return w
        if score > best_score:
            best, best_score = w, score
    return best


def settle_order(c, oid):
    """Move goods out of on-hand when an order ships; put them back if that is undone."""
    o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o or not o["warehouse_id"]:
        return
    should = o["status"] in ("shipped", "delivered")
    items = json.loads(o["items_json"])
    if should and not o["stock_out"]:
        for it in items:
            move(c, it["id"], o["warehouse_id"], -it["qty"], "ship", o["order_no"])
        c.execute("UPDATE orders SET stock_out=1 WHERE id=?", (oid,))
    elif not should and o["stock_out"]:
        for it in items:
            move(c, it["id"], o["warehouse_id"], it["qty"], "ship_reverse", o["order_no"], "订单退回未发货")
        c.execute("UPDATE orders SET stock_out=0 WHERE id=?", (oid,))
    for it in items:
        sync_product(c, it["id"])


def clean_lines(c, lines):
    out = {}
    for ln in lines or []:
        pid = ln.get("product_id")
        try:
            q = int(ln.get("qty"))
        except (TypeError, ValueError):
            raise ValueError("数量要填整数")
        if not c.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
            raise ValueError(f"找不到商品 {pid}")
        if q <= 0:
            raise ValueError("数量要大于 0")
        out[pid] = out.get(pid, 0) + q
    if not out:
        raise ValueError("至少要有一行商品")
    return out


def warehouse_exists(c, wid):
    return bool(c.execute("SELECT 1 FROM warehouses WHERE id=?", (wid,)).fetchone())


def receive(c, wid, lines, ref, note=""):
    if not warehouse_exists(c, wid):
        raise ValueError("没有这个仓库")
    for pid, q in clean_lines(c, lines).items():
        move(c, pid, wid, q, "receive", ref, note)
        sync_product(c, pid)


def transfer(c, src, dst, lines, ref, note=""):
    if src == dst or not warehouse_exists(c, src) or not warehouse_exists(c, dst):
        raise ValueError("请选两个不同的仓库")
    reserved = reserved_map(c)
    clean = clean_lines(c, lines)
    for pid, q in clean.items():
        free = on_hand(c, pid, src) - reserved.get((pid, src), 0)
        if q > free:
            name = c.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()[0]
            raise ValueError(f"{name} 在调出仓库只有 {max(free, 0)} 件可调（已扣除待发货占用）")
    for pid, q in clean.items():
        move(c, pid, src, -q, "transfer_out", ref, note)
        move(c, pid, dst, q, "transfer_in", ref, note)
        sync_product(c, pid)


def adjust(c, pid, wid, new_qty, reason, note=""):
    if not warehouse_exists(c, wid):
        raise ValueError("没有这个仓库")
    if new_qty < 0:
        raise ValueError("数量不能小于 0")
    delta = new_qty - on_hand(c, pid, wid)
    move(c, pid, wid, delta, "adjust", reason or "", note)
    sync_product(c, pid)
    return delta


def set_bin(c, pid, wid, b):
    c.execute("INSERT INTO stock (product_id, warehouse_id, qty, bin) VALUES (?,?,0,?) "
              "ON CONFLICT(product_id, warehouse_id) DO UPDATE SET bin=excluded.bin", (pid, wid, (b or "").strip()[:20] or None))


# ------------------------------------------------------------------ counts
def next_count_no(c):
    day = datetime.now().strftime("%y%m%d")
    n = c.execute("SELECT COUNT(*) FROM counts WHERE no LIKE ?", (f"PD{day}-%",)).fetchone()[0]
    return f"PD{day}-{n + 1:02d}"


def create_count(c, cid, wid, scope, product_ids=None, blind=False, note=""):
    if not warehouse_exists(c, wid):
        raise ValueError("没有这个仓库")
    q = "SELECT id FROM products"
    args = []
    if scope.startswith("cat:"):
        q, args = q + " WHERE cat=?", [scope[4:]]
    elif scope == "ids":
        ids = [p for p in (product_ids or []) if p]
        if not ids:
            raise ValueError("请至少选一件商品")
        q, args = q + f" WHERE id IN ({','.join('?' * len(ids))})", ids
    elif scope != "all":
        raise ValueError("盘点范围不对")
    pids = [r["id"] for r in c.execute(q, args)]
    if not pids:
        raise ValueError("这个范围里没有商品")
    no = next_count_no(c)
    c.execute("INSERT INTO counts (id, no, warehouse_id, scope, status, blind, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
              (cid, no, wid, scope, "open", int(bool(blind)), note[:200], now()))
    for pid in pids:
        s = c.execute("SELECT qty, bin FROM stock WHERE product_id=? AND warehouse_id=?", (pid, wid)).fetchone()
        c.execute("INSERT INTO count_lines (count_id, product_id, bin, expected) VALUES (?,?,?,?)",
                  (cid, pid, s["bin"] if s else None, s["qty"] if s else 0))
    return no


def count_json(c, cid, lines=True):
    r = c.execute("SELECT * FROM counts WHERE id=?", (cid,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["blind"] = bool(out["blind"])
    ls = c.execute("""SELECT l.*, p.name, p.sku, p.art, p.photo FROM count_lines l JOIN products p ON p.id=l.product_id
                      WHERE l.count_id=? ORDER BY COALESCE(l.bin,'~'), p.sort, p.name""", (cid,)).fetchall()
    out["total"] = len(ls)
    out["done"] = sum(1 for l in ls if l["counted"] is not None)
    out["variances"] = sum(1 for l in ls if l["counted"] is not None and l["counted"] != l["expected"])
    out["net"] = sum((l["counted"] - l["expected"]) for l in ls if l["counted"] is not None)
    out["warehouse_name"] = (c.execute("SELECT name FROM warehouses WHERE id=?", (r["warehouse_id"],)).fetchone() or [""])[0]
    if lines:
        out["lines"] = [dict(l) for l in ls]
    return out


def list_counts(c):
    return [count_json(c, r["id"], lines=False) for r in c.execute("SELECT id FROM counts ORDER BY created_at DESC LIMIT 100")]


def update_count(c, cid, lines, action):
    cnt = c.execute("SELECT * FROM counts WHERE id=?", (cid,)).fetchone()
    if not cnt:
        raise LookupError("找不到这个盘点单")
    if cnt["status"] in ("posted", "canceled"):
        raise ValueError("这张盘点单已经结束，不能再改")
    for ln in lines or []:
        counted = ln.get("counted")
        if counted in ("", None):
            counted = None
        else:
            try:
                counted = int(counted)
            except (TypeError, ValueError):
                raise ValueError("实盘数量要填整数")
            if counted < 0:
                raise ValueError("实盘数量不能小于 0")
        reason = ln.get("reason") if ln.get("reason") in REASONS else None
        c.execute("UPDATE count_lines SET counted=?, reason=? WHERE count_id=? AND product_id=?",
                  (counted, reason, cid, ln.get("product_id")))
    if action == "review":
        c.execute("UPDATE counts SET status='review' WHERE id=?", (cid,))
    elif action == "reopen":
        c.execute("UPDATE counts SET status='open' WHERE id=?", (cid,))
    elif action == "cancel":
        c.execute("UPDATE counts SET status='canceled' WHERE id=?", (cid,))
    elif action == "post":
        missing = c.execute("SELECT COUNT(*) FROM count_lines WHERE count_id=? AND counted IS NULL", (cid,)).fetchone()[0]
        if missing:
            raise ValueError(f"还有 {missing} 件没盘，先填完（没有就填 0）")
        for l in c.execute("SELECT * FROM count_lines WHERE count_id=?", (cid,)).fetchall():
            delta = l["counted"] - l["expected"]
            if delta:
                move(c, l["product_id"], cnt["warehouse_id"], delta, "count", cnt["no"], l["reason"] or "盘点差异")
                sync_product(c, l["product_id"])
        c.execute("UPDATE counts SET status='posted', posted_at=? WHERE id=?", (now(), cid))


def movements(c, product=None, warehouse=None, kind=None, limit=200):
    q = """SELECT m.*, p.name, p.sku, w.name AS warehouse_name FROM movements m
           LEFT JOIN products p ON p.id=m.product_id LEFT JOIN warehouses w ON w.id=m.warehouse_id WHERE 1=1"""
    args = []
    for col, v in (("m.product_id", product), ("m.warehouse_id", warehouse), ("m.kind", kind)):
        if v:
            q += f" AND {col}=?"
            args.append(v)
    q += " ORDER BY m.id DESC LIMIT ?"
    args.append(max(1, min(1000, int(limit))))
    return [{**dict(r), "kind_label": KINDS.get(r["kind"], r["kind"])} for r in c.execute(q, args)]
