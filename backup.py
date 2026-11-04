"""Backups of the shop database.

- daily(): once a day (driven by the minute tick) copy shop.db with SQLite's online backup
  API into BACKUP_DIR and keep the newest N. Safe while the shop is running.
- zip_bytes(): a downloadable zip with the database and uploaded photos, for an off-site copy.

Restore: stop the server, put the backup file in place of shop.db (and unzip uploads/), start it.
"""
import io
import os
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path


def _copy(src, dest):
    with sqlite3.connect(src, timeout=30) as a, sqlite3.connect(dest) as b:
        a.backup(b)
    b.close()
    a.close()


def daily(db_path, folder, keep=14):
    """Make today's backup if it doesn't exist yet. Returns its path, or None if already done
    (or another worker is making it right now)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"shop-{datetime.now():%Y%m%d}.db"
    if target.exists():
        return None
    lock = target.with_suffix(".lock")
    try:  # only one worker makes today's copy
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < 600:
            return None
        lock.unlink(missing_ok=True)  # left behind by a crash; take over
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    try:
        tmp = folder / f".{target.name}.{os.getpid()}.part"
        _copy(db_path, tmp)
        os.replace(tmp, target)
        for old in sorted(folder.glob("shop-*.db"))[:-keep]:
            old.unlink(missing_ok=True)
        return str(target)
    finally:
        lock.unlink(missing_ok=True)


def listing(folder):
    folder = Path(folder)
    if not folder.exists():
        return []
    return [{"name": p.name, "bytes": p.stat().st_size,
             "at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes")}
            for p in sorted(folder.glob("shop-*.db"), reverse=True)]


def zip_bytes(db_path, upload_dir):
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "shop.db"
        _copy(db_path, snap)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "shop.db")
            up = Path(upload_dir)
            if up.exists():
                for f in up.rglob("*"):
                    if f.is_file():
                        z.write(f, "uploads/" + f.relative_to(up).as_posix())
            z.writestr("RESTORE.txt", "Stop the server. Put shop.db where DB_PATH points and the uploads/ folder where "
                                      "UPLOAD_DIR points. Start the server.\n停掉服务器，把 shop.db 放到 DB_PATH 指向的位置，"
                                      "uploads/ 放到 UPLOAD_DIR，再启动。\n")
    return buf.getvalue()
