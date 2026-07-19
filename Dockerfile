FROM python:3.12-slim

# --- ابزارهای سیستمی: nginx (روتینگ)، gettext-base (envsubst)، curl/unzip (دانلود Xray) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip nginx gettext-base ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

# --- دانلود همیشه‌آخرین نسخه‌ی رسمی Xray-core (لینوکس ۶۴بیتی) ---
# از /releases/latest/download همیشه به آخرین ریلیز واقعی روی گیت‌هاب ریدایرکت میشه،
# پس نیازی به پین کردن دستی شماره نسخه نیست.
RUN curl -fL -o /tmp/xray.zip \
        https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip \
    && mkdir -p /usr/local/bin/xray-core \
    && unzip -q /tmp/xray.zip -d /usr/local/bin/xray-core \
    && chmod +x /usr/local/bin/xray-core/xray \
    && rm /tmp/xray.zip
ENV XRAY_BINARY_PATH=/usr/local/bin/xray-core/xray

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh

ENV DATA_DIR=/data
RUN mkdir -p /data

# ⚠️ حتماً بعد از دیپلوی، یه Volume دائمی رو مسیر /data وصل کنید
# (Railway → Settings → Volumes → Mount Path: /data)
# وگرنه با هر ری‌استارت، کاربرها و مسیر پروکسی از نو ساخته میشن.

EXPOSE 8080
CMD ["./start.sh"]
