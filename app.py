"""
app.py
======
اپلیکیشن اصلی (Control Plane). این فایل جایگزین main.py / pages.py قدیمی میشه.

مسئولیت‌ها:
  - داشبورد وب برای ساخت/فعال‌غیرفعال‌کردن/حذف کاربر
  - ساخت لینک VLESS برای هر کاربر
  - هر تغییری که میدید، خودکار کانفیگ Xray-core رو بازسازی و ری‌لود می‌کنه

نکته‌ی امنیتی: داشبورد پشت HTTP Basic Auth هست. حتماً قبل از دیپلوی،
متغیرهای محیطی ADMIN_USER و ADMIN_PASS رو تو تنظیمات Railway ست کنید
(پیش‌فرض‌ها فقط برای تست محلی‌ان و امن نیستن).
"""

import os
import secrets
import uuid as uuid_lib
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import database as db
from xray_manager import InboundSpec, ProxyUser, XrayManager

# ---------------------------------------------------------------------------
# تنظیمات از روی متغیرهای محیطی
# ---------------------------------------------------------------------------
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "changeme")
# Railway این متغیر رو خودکار ست می‌کنه؛ اگه نبود، PUBLIC_HOST دستی رو بخون
PUBLIC_HOST = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get(
    "PUBLIC_HOST", "your-domain.up.railway.app"
)
DATA_DIR = os.environ.get("DATA_DIR", "/data")
INBOUND_LISTEN_PORT = 8081  # پورت داخلی Xray؛ nginx این پورت رو forward می‌کنه

app = FastAPI(title="X4G Panel")
templates = Jinja2Templates(directory="templates")
security = HTTPBasic()

xray = XrayManager(config_dir=os.path.join(DATA_DIR, "xray"))


# ---------------------------------------------------------------------------
# احراز هویت ساده‌ی داشبورد
# ---------------------------------------------------------------------------
def check_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_pass = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="نام کاربری یا رمز عبور اشتباهه",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ---------------------------------------------------------------------------
# هسته‌ی اصلی: بازسازی کانفیگ Xray از روی دیتابیس فعلی
# ---------------------------------------------------------------------------
def rebuild_and_reload() -> None:
    proxy_path = db.get_proxy_path()
    rows = db.list_users()

    users = [
        ProxyUser(
            uuid=row["uuid"],
            email=f"user-{row['id']}",
            path=proxy_path,
            enabled=bool(row["enabled"]),
        )
        for row in rows
    ]

    inbound = InboundSpec(
        tag="in-ws-main",
        listen_port=INBOUND_LISTEN_PORT,
        network="ws",
        host=PUBLIC_HOST,
        base_path=proxy_path,
        users=users,
    )
    xray.reload_users([inbound])


def build_vless_link(user_row: dict) -> str:
    proxy_path = db.get_proxy_path()
    label = quote(user_row["label"])
    return (
        f"vless://{user_row['uuid']}@{PUBLIC_HOST}:443"
        f"?encryption=none&security=none&type=ws"
        f"&host={PUBLIC_HOST}&path={quote(proxy_path)}"
        f"#{label}"
    )


# ---------------------------------------------------------------------------
# رویداد استارت‌آپ: دیتابیس رو آماده کن و Xray رو راه بنداز
# ---------------------------------------------------------------------------
@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    db.ensure_proxy_path()
    rebuild_and_reload()


# ---------------------------------------------------------------------------
# مسیرهای وب
# ---------------------------------------------------------------------------
@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, _user: str = Depends(check_auth)) -> HTMLResponse:
    rows = db.list_users()
    users = [{**row, "link": build_vless_link(row)} for row in rows]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "users": users,
            "xray_running": xray.is_running(),
            "public_host": PUBLIC_HOST,
        },
    )


@app.post("/dashboard/users")
def create_user(label: str = Form(...), _user: str = Depends(check_auth)) -> RedirectResponse:
    new_uuid = str(uuid_lib.uuid4())
    db.add_user(new_uuid, label.strip() or "کاربر بدون نام")
    rebuild_and_reload()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/dashboard/users/{user_id}/toggle")
def toggle_user(user_id: int, _user: str = Depends(check_auth)) -> RedirectResponse:
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="کاربر پیدا نشد")
    db.toggle_user(user_id)
    rebuild_and_reload()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/dashboard/users/{user_id}/delete")
def delete_user(user_id: int, _user: str = Depends(check_auth)) -> RedirectResponse:
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="کاربر پیدا نشد")
    db.delete_user(user_id)
    rebuild_and_reload()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    """برای مانیتورینگ سلامت سرویس (Xray-core زنده‌ست یا نه)."""
    return "ok" if xray.is_running() else "xray-down"
