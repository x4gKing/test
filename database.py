"""
database.py
===========
لایه‌ی ذخیره‌سازی دائمی پروژه (به‌جای دیکشنری in-memory نسخه‌ی قبلی).

از SQLite استفاده شده چون:
  - نیازی به سرویس جدا (مثل Postgres/Redis) نداره، یه فایله.
  - اگه یه Volume دائمی رو مسیر DATA_DIR وصل کنید، این فایل بین
    ری‌استارت‌های Railway هم باقی می‌مونه (برخلاف نسخه‌ی in-memory قبلی).

⚠️ خیلی مهم: برای اینکه داده‌ها واقعاً دائمی بمونن، حتماً باید تو تنظیمات
سرویس Railway (Settings → Volumes) یه Volume به مسیر /data وصل کنید،
وگرنه با هر Deploy جدید همه‌چیز از صفر شروع میشه.
"""

import os
import secrets
import sqlite3
import string

DATA_DIR = os.environ.get("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "x4g.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )


def ensure_proxy_path() -> str:
    """
    اگه مسیر پروکسی (مثل /a1b2c3) قبلاً ساخته نشده، یه مسیر تصادفی و امن
    میسازه و همیشه همونو نگه می‌داره (تا با هر ری‌استارت عوض نشه).
    هم start.sh (برای nginx) و هم app.py از همین متد استفاده می‌کنن که
    مسیر همیشه یکی بمونه.
    """
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'proxy_path'"
        ).fetchone()
        if row:
            return row["value"]

        alphabet = string.ascii_lowercase + string.digits
        random_part = "".join(secrets.choice(alphabet) for _ in range(14))
        path = f"/{random_part}"
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('proxy_path', ?)", (path,)
        )
        return path


def get_proxy_path() -> str:
    return ensure_proxy_path()


def list_users(only_enabled: bool = False) -> list[dict]:
    init_db()
    with _connect() as conn:
        query = "SELECT * FROM users"
        if only_enabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY id DESC"
        return [dict(row) for row in conn.execute(query).fetchall()]


def get_user(user_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def add_user(uuid_str: str, label: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (uuid, label) VALUES (?, ?)", (uuid_str, label)
        )


def toggle_user(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET enabled = 1 - enabled WHERE id = ?", (user_id,))


def delete_user(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
