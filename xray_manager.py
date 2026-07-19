"""
xray_manager.py
================
جایگزین relay_vless.py و xhttp_siz10.py

به‌جای پیاده‌سازی دستی پروتکل VLESS/XHTTP در Python، این ماژول باینری رسمی
Xray-core را به‌عنوان یک subprocess اجرا و مدیریت می‌کند. تمام رمزنگاری،
framing و منطق پروتکل به Xray-core سپرده می‌شود که پیاده‌سازی مرجع و
به‌شدت تست‌شده‌ی این پروتکل‌هاست.

معماری:
    main.py / pages.py  (Control Plane: داشبورد، ربات تلگرام، دیتابیس کاربران)
              │
              ▼
    XrayManager (این فایل)  →  فرزند subprocess: باینری xray
              │
              ▼
    فایل config.json تولیدشده از روی لیست کاربران دیتابیس شما

نکته‌ی مهم دیپلوی روی Railway:
    باینری xray باید در Dockerfile دانلود و کنار main.py کپی شود، مثلاً:

        FROM python:3.12-slim
        ARG XRAY_VERSION=v25.6.8
        RUN apt-get update && apt-get install -y curl unzip ca-certificates \
            && curl -L -o /tmp/xray.zip \
               https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-64.zip \
            && unzip /tmp/xray.zip -d /usr/local/bin/xray-core \
            && chmod +x /usr/local/bin/xray-core/xray \
            && rm /tmp/xray.zip
        ENV XRAY_BINARY_PATH=/usr/local/bin/xray-core/xray
        ...

    مسیر باینری از طریق env var ``XRAY_BINARY_PATH`` به این ماژول داده می‌شود
    (پیش‌فرض: ``/usr/local/bin/xray-core/xray``).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("xray_manager")


# ---------------------------------------------------------------------------
# مدل ساده‌ی هر کاربر/کانفیگ (باید با مدل دیتابیس فعلی شما هماهنگ بشه)
# ---------------------------------------------------------------------------
@dataclass
class ProxyUser:
    uuid: str                      # UUID کلاینت VLESS
    email: str                     # شناسه‌ی یکتا (برای آمار مصرف هر کاربر در Xray)
    path: str                      # مسیر WS/XHTTP، مثلاً "/in-l0d3m219k0"
    enabled: bool = True
    flow: str = ""                 # معمولاً خالی برای WS/XHTTP (فقط TCP+Reality لازمش داره)


@dataclass
class InboundSpec:
    """مشخصات یک Inbound که Xray باید بسازه."""
    tag: str                       # مثلا "in-ws-8081"
    listen_port: int               # پورت داخلی که Xray روش گوش میده (127.0.0.1)
    network: str                   # "ws" یا "xhttp"
    host: str                      # هدر Host موردانتظار
    base_path: str                 # مسیر پایه، کاربرها زیر این مسیر جدا میشن یا با path خودشون
    users: list[ProxyUser] = field(default_factory=list)


# ---------------------------------------------------------------------------
# مدیر اصلی
# ---------------------------------------------------------------------------
class XrayManager:
    def __init__(
        self,
        binary_path: Optional[str] = None,
        config_dir: str = "/data/xray",
        api_port: int = 10085,
        log_level: str = "warning",
    ):
        self.binary_path = binary_path or os.environ.get(
            "XRAY_BINARY_PATH", "/usr/local/bin/xray-core/xray"
        )
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "config.json"
        self.api_port = api_port

        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._stop_watchdog = threading.Event()
        self._inbounds: list[InboundSpec] = []

        self._verify_binary()

    # ------------------------------------------------------------------
    # بررسی وجود باینری
    # ------------------------------------------------------------------
    def _verify_binary(self) -> None:
        if not Path(self.binary_path).is_file():
            raise FileNotFoundError(
                f"باینری Xray-core در مسیر {self.binary_path} پیدا نشد. "
                "مطمئن شوید در Dockerfile دانلود شده و ENV XRAY_BINARY_PATH "
                "درست ست شده باشد."
            )
        if not os.access(self.binary_path, os.X_OK):
            os.chmod(self.binary_path, 0o755)

    # ------------------------------------------------------------------
    # ساخت config.json از روی لیست Inboundها
    # ------------------------------------------------------------------
    def build_config(self, inbounds: list[InboundSpec]) -> dict:
        xray_inbounds = []

        for ib in inbounds:
            clients = [
                {
                    "id": u.uuid,
                    "email": u.email,
                    "flow": u.flow,
                }
                for u in ib.users
                if u.enabled
            ]

            stream_settings: dict
            if ib.network == "ws":
                stream_settings = {
                    "network": "ws",
                    "security": "none",
                    "wsSettings": {
                        "path": ib.base_path,
                        "host": ib.host,
                    },
                }
            elif ib.network == "xhttp":
                stream_settings = {
                    "network": "xhttp",
                    "security": "none",
                    "xhttpSettings": {
                        "path": ib.base_path,
                        "host": ib.host,
                        "mode": "auto",
                    },
                }
            else:
                raise ValueError(f"network نامعتبر: {ib.network}")

            xray_inbounds.append(
                {
                    "tag": ib.tag,
                    "listen": "127.0.0.1",   # فقط از داخل کانتینر قابل‌دسترسیه
                    "port": ib.listen_port,
                    "protocol": "vless",
                    "settings": {
                        "clients": clients,
                        "decryption": "none",
                    },
                    "streamSettings": stream_settings,
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["http", "tls", "quic"],
                    },
                }
            )

        # Inbound مخصوص API (برای آمار مصرف / کنترل آینده از طریق gRPC)
        xray_inbounds.append(
            {
                "listen": "127.0.0.1",
                "port": self.api_port,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
                "tag": "api",
            }
        )

        config = {
            "log": {"loglevel": "warning"},
            "api": {
                "tag": "api",
                "services": ["HandlerService", "StatsService", "LoggerService"],
            },
            "stats": {},
            "policy": {
                "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
                "system": {"statsInboundUplink": True, "statsInboundDownlink": True},
            },
            "inbounds": xray_inbounds,
            "outbounds": [
                {"protocol": "freedom", "tag": "direct"},
                {"protocol": "blackhole", "tag": "blocked"},
            ],
            "routing": {
                "rules": [
                    {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                ]
            },
        }
        return config

    def write_config(self, inbounds: list[InboundSpec]) -> None:
        config = self.build_config(inbounds)
        tmp_path = self.config_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
        tmp_path.replace(self.config_path)  # نوشتن اتمیک، جلوگیری از کانفیگ نصفه
        self._inbounds = inbounds
        logger.info("config.json بازنویسی شد (%d inbound)", len(inbounds))

    # ------------------------------------------------------------------
    # مدیریت پروسه
    # ------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self.is_running():
                logger.warning("Xray از قبل در حال اجراست، start نادیده گرفته شد")
                return
            if not self.config_path.exists():
                raise RuntimeError("config.json وجود ندارد؛ اول write_config را صدا بزنید")

            self._process = subprocess.Popen(
                [self.binary_path, "run", "-c", str(self.config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            logger.info("Xray-core شروع شد (pid=%s)", self._process.pid)

            # لاگ‌های Xray رو تو یه ترد جدا بخون تا subprocess بلاک نشه
            threading.Thread(target=self._pipe_logs, daemon=True).start()

            # چک اولیه: اگه ظرف ۲ ثانیه کرش کرد یعنی کانفیگ خرابه
            time.sleep(2)
            if not self.is_running():
                raise RuntimeError(
                    "Xray-core بلافاصله بعد از استارت متوقف شد؛ لاگ‌ها را چک کنید "
                    "(معمولاً یعنی config.json نامعتبره)"
                )

            if self._watchdog_thread is None:
                self._stop_watchdog.clear()
                self._watchdog_thread = threading.Thread(
                    target=self._watchdog_loop, daemon=True
                )
                self._watchdog_thread.start()

    def _pipe_logs(self) -> None:
        if not self._process or not self._process.stdout:
            return
        for line in self._process.stdout:
            logger.info("[xray] %s", line.rstrip())

    def stop(self) -> None:
        with self._lock:
            self._stop_watchdog.set()
            if self._process and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            self._process = None
            logger.info("Xray-core متوقف شد")

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def restart(self) -> None:
        """ری‌استارت کنترل‌شده: برای اعمال تغییرات کاربر بعد از write_config صدا زده میشه."""
        with self._lock:
            logger.info("در حال ری‌استارت Xray-core...")
            self.stop()
            self.start()

    def reload_users(self, inbounds: list[InboundSpec]) -> None:
        """
        نقطه‌ی ورودی اصلی برای Control Plane: هر وقت کاربری اضافه/حذف/ویرایش شد،
        این متد رو با لیست کامل و به‌روز Inboundها صدا بزنید.
        """
        self.write_config(inbounds)
        self.restart()

    # ------------------------------------------------------------------
    # واچ‌داگ: اگه Xray به هر دلیلی کرش کرد، خودکار بالا بیارش
    # ------------------------------------------------------------------
    def _watchdog_loop(self, check_interval: float = 5.0) -> None:
        consecutive_failures = 0
        while not self._stop_watchdog.is_set():
            time.sleep(check_interval)
            with self._lock:
                if self._process is None:
                    continue  # عمداً متوقف شده (stop() صدا زده شده)
                if self._process.poll() is not None:
                    consecutive_failures += 1
                    logger.error(
                        "Xray-core غیرمنتظره متوقف شد (تلاش %d)", consecutive_failures
                    )
                    if consecutive_failures > 5:
                        logger.critical(
                            "بیش از ۵ بار پشت‌سرهم کرش کرد؛ واچ‌داگ متوقف شد. "
                            "کانفیگ یا باینری را دستی بررسی کنید."
                        )
                        return
                    backoff = min(2 ** consecutive_failures, 60)
                    time.sleep(backoff)
                    try:
                        self.start()
                        consecutive_failures = 0
                    except Exception:
                        logger.exception("تلاش مجدد برای استارت Xray شکست خورد")
                else:
                    consecutive_failures = 0

    # ------------------------------------------------------------------
    # آمار مصرف هر کاربر (از طریق CLI خود xray، بدون نیاز به gRPC stub)
    # ------------------------------------------------------------------
    def get_user_traffic(self, email: str) -> dict:
        """
        بازگشت: {"uplink": bytes, "downlink": bytes}
        از subcommand داخلی ``xray api statsquery`` استفاده می‌کنه که
        نیازی به کامپایل protobuf stub نداره.
        """
        pattern = f"user>>>{email}>>>traffic"
        try:
            result = subprocess.run(
                [
                    self.binary_path, "api", "statsquery",
                    "--server", f"127.0.0.1:{self.api_port}",
                    "-pattern", pattern,
                ],
                capture_output=True, text=True, timeout=5, check=True,
            )
            data = json.loads(result.stdout or "{}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
            logger.exception("خواندن آمار مصرف کاربر %s شکست خورد", email)
            return {"uplink": 0, "downlink": 0}

        uplink = downlink = 0
        for stat in data.get("stat", []):
            name = stat.get("name", "")
            value = int(stat.get("value", 0))
            if name.endswith(">>>uplink"):
                uplink = value
            elif name.endswith(">>>downlink"):
                downlink = value
        return {"uplink": uplink, "downlink": downlink}


# ---------------------------------------------------------------------------
# مثال استفاده (این بخش را داخل main.py صدا بزنید)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    manager = XrayManager(config_dir="/tmp/xray-test")

    demo_inbound = InboundSpec(
        tag="in-ws-main",
        listen_port=8081,
        network="ws",
        host="example.up.railway.app",
        base_path="/in1",
        users=[
            ProxyUser(uuid=str(uuid.uuid4()), email="demo-user", path="/in1"),
        ],
    )

    manager.write_config([demo_inbound])
    manager.start()

    print("Xray-core در حال اجراست. برای توقف Ctrl+C بزنید.")
    try:
        while True:
            time.sleep(10)
            traffic = manager.get_user_traffic("demo-user")
            print("مصرف demo-user:", traffic)
    except KeyboardInterrupt:
        manager.stop()
