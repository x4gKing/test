#!/bin/sh
set -e

export PORT="${PORT:-8080}"

echo ">> در حال آماده‌سازی مسیر پروکسی از روی دیتابیس دائمی..."
export PROXY_PATH="$(python3 -c 'import database; print(database.ensure_proxy_path())')"
echo ">> PROXY_PATH=${PROXY_PATH}  (این مقدار بین ری‌استارت‌ها ثابت می‌مونه)"

echo ">> ساخت کانفیگ nginx برای پورت ${PORT}..."
envsubst '${PORT} ${PROXY_PATH}' < /app/nginx.conf.template > /etc/nginx/conf.d/default.conf

echo ">> شروع nginx..."
nginx -g 'daemon off;' &

echo ">> شروع اپلیکیشن Python (FastAPI + مدیریت Xray-core)..."
exec uvicorn app:app --host 127.0.0.1 --port 8000
