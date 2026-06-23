"""
core/db.py
دیتابیس مشترک برای سیستم چند رباتی.
هر ربات با bot_id خودش جدا می‌مونه، ادمین‌ها مشترکن.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime

# روی Railway باید یه Volume وصل بشه و مسیرش تو env var زیر تنظیم بشه
# (مثلاً Mount Path = /data). اگه تنظیم نشه، فایل کنار کد ساخته می‌شه
# و با هر دیپلوی/ریستارت پاک می‌شه - فقط برای تست لوکال مناسبه.
DB_DIR = os.environ.get("DB_DIR", ".")
DB_FILE = os.path.join(DB_DIR, "multibot.db")
_db_lock = threading.Lock()


def _get_conn():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kv_store (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            data TEXT NOT NULL
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO kv_store (id, data) VALUES (1, ?)
    """, (json.dumps({
        # رباتای فایل‌شیر ثبت‌شده: {"bot_id": {...}}
        "bots": {},
        # ادمین‌های سراسری - رو همه رباتا (مدیر + فایل‌شیرها) اعمال می‌شه
        "global_admins": [],
        # داده‌ی هر ربات فایل‌شیر، با کلید bot_id جدا می‌مونه
        # "bot_data": {"bot_id": {"users": {}, "files": {}, "banned": [], ...}}
        "bot_data": {},
    }),))
    conn.commit()
    conn.close()


init_db()


def load_db():
    with _db_lock:
        conn = _get_conn()
        row = conn.execute("SELECT data FROM kv_store WHERE id = 1").fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
        return {"bots": {}, "global_admins": [], "bot_data": {}}


def save_db(db):
    with _db_lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE kv_store SET data = ? WHERE id = 1",
            (json.dumps(db, ensure_ascii=False),)
        )
        conn.commit()
        conn.close()


class db_transaction:
    """
    استفاده برای تغییرات امن همزمان:
        with db_transaction() as db:
            db["bot_data"][bot_id]["files"][code]["downloads"] += 1
    """
    def __enter__(self):
        _db_lock.acquire()
        self._conn = _get_conn()
        row = self._conn.execute("SELECT data FROM kv_store WHERE id = 1").fetchone()
        self._db = json.loads(row[0]) if row else {"bots": {}, "global_admins": [], "bot_data": {}}
        return self._db

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self._conn.execute(
                    "UPDATE kv_store SET data = ? WHERE id = 1",
                    (json.dumps(self._db, ensure_ascii=False),)
                )
                self._conn.commit()
        finally:
            self._conn.close()
            _db_lock.release()
        return False


def empty_bot_data():
    """ساختار خالی برای دیتای یه ربات فایل‌شیر جدید"""
    return {
        "users": {}, "files": {}, "banned": [], "texts": {},
        "join_channels": [], "required_task": None, "packs": {},
        # کانال دوم برای ارسال پست اطلاع‌رسانی (اختیاری)
        "channel2": None,
        # قالب ثابت (footer) پست کانال دوم - متغیرها: {name}, {link}
        "channel2_template": "📁 {name}",
        # متن دکمه‌ی شیشه‌ای
        "channel2_btn_text": "📥 دریافت فایل",
        # تایمر حذف خودکار پیام فایل (ثانیه، 0 = غیرفعال)
        "auto_delete_seconds": 0,
    }


def is_global_admin(user_id):
    db = load_db()
    return user_id in db.get("global_admins", [])
