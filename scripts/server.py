"""
سرور کوچک برای تست روی Render (Web Service رایگان).

- GET  /health                       بررسی سلامت
- POST /run?type=all&topic=...        اجرای تولید (هدر X-Run-Token لازم است)
- GET  /results?token=...             آخرین خروجی‌ها به صورت JSON

اگر RUN_ON_STARTUP=1 باشد، بعد از هر deploy یک بار تست اجرا می‌شود و نتیجه در لاگ Render می‌آید.
بدون وابستگی اضافه (فقط کتابخانه‌ی استاندارد).
"""
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.logging_setup import setup_logging

setup_logging()
log = logging.getLogger("server")

OUT_DIR = Path("outputs")
RUN_TOKEN = os.environ.get("RUN_TOKEN", "")
_lock = threading.Lock()
_state = {"running": False, "last_exit": None}


def run_generate(topic: str | None, ctype: str, models: str | None) -> None:
    if not _lock.acquire(blocking=False):
        log.warning("یک اجرا در جریان است؛ درخواست جدید رد شد.")
        return
    _state["running"] = True
    try:
        cmd = [sys.executable, "-m", "scripts.generate", "--type", ctype, "--out", str(OUT_DIR)]
        if topic:
            cmd += ["--topic", topic]
        if models:
            cmd += ["--models", models]
        log.info("▶ شروع اجرا: type=%s models=%s topic=%s", ctype, models or "(LLM_MODEL)", topic or "(پیش‌فرض)")
        rc = subprocess.call(cmd)  # خروجی مستقیم به لاگ Render می‌رود
        _state["last_exit"] = rc
        log.info("■ پایان اجرا (exit=%s)", rc)
        # چاپ خروجی‌ها در لاگ تا بدون دسترسی به سرور هم قابل‌بررسی باشند
        for f in sorted(OUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)[-6:]:
            print(f"\n===== {f.name} =====\n{f.read_text(encoding='utf-8')}", flush=True)
    finally:
        _state["running"] = False
        _lock.release()


def _authorized(handler: BaseHTTPRequestHandler, qs: dict) -> bool:
    if not RUN_TOKEN:
        return False
    given = handler.headers.get("X-Run-Token") or (qs.get("token") or [""])[0]
    return hmac.compare_digest(given, RUN_TOKEN)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # لاگ درخواست‌ها بدون نمایش token
        log.info("%s %s", self.command, urlparse(self.path).path)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path in ("/", "/health"):
            return self._send(200, {"ok": True, **_state})
        if u.path == "/results":
            if not _authorized(self, qs):
                return self._send(401, {"error": "unauthorized"})
            files = sorted(OUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)[-6:]
            return self._send(200, {f.name: json.loads(f.read_text(encoding="utf-8")) for f in files})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path != "/run":
            return self._send(404, {"error": "not found"})
        if not _authorized(self, qs):
            return self._send(401, {"error": "unauthorized"})
        if _state["running"]:
            return self._send(409, {"error": "already running"})
        ctype = (qs.get("type") or ["all"])[0]
        if ctype not in ("text", "infographic", "video", "all"):
            return self._send(400, {"error": "bad type"})
        topic = (qs.get("topic") or [None])[0]
        models = (qs.get("models") or [None])[0]
        threading.Thread(target=run_generate, args=(topic, ctype, models), daemon=True).start()
        return self._send(202, {"started": True, "type": ctype})


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    port = int(os.environ.get("PORT", "10000"))
    if os.environ.get("RUN_ON_STARTUP") == "1":
        threading.Thread(
            target=run_generate,
            args=(os.environ.get("TEST_TOPIC") or None, os.environ.get("TEST_TYPE", "all"),
                  os.environ.get("TEST_MODELS") or None),
            daemon=True,
        ).start()
    if not RUN_TOKEN:
        log.warning("RUN_TOKEN تنظیم نشده؛ /run و /results غیرفعال‌اند.")
    log.info("سرور روی پورت %d آماده است", port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
