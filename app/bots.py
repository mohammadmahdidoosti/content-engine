"""
ربات‌های تلگرام و بله (هر دو API مشابه دارند) با webhook.

- توکن‌ها: TELEGRAM_BOT_TOKEN و BALE_BOT_TOKEN (در Environment سرویس؛ خود کاربر وارد می‌کند)
- آدرس webhook: <SERVICE_URL>/bot/<platform>/<secret>  ← secret از روی توکن ساخته می‌شود
- هر پیام متنی به دستیار (app.assistant.ask) داده می‌شود و جواب با دکمه‌ی لینک کالاها برمی‌گردد.
"""
import hashlib
import logging
import os

import httpx

from app.assistant import ask
from app.config import get_settings

log = logging.getLogger("bots")

APIS = {"telegram": "https://api.telegram.org/bot{token}/", "bale": "https://tapi.bale.ai/bot{token}/"}
ENV = {"telegram": "TELEGRAM_BOT_TOKEN", "bale": "BALE_BOT_TOKEN"}
WELCOME = ("سلام 👋 به دستیار {brand} خوش آمدید.\n"
           "اسم یا مدل کالا را بنویسید تا قیمت روز و لینک ثبت سفارش را بفرستم.\n"
           "مثال: «پمپ شناور ۳ اسب» یا «بذر خربزه جباری»")


def token(platform: str) -> str:
    return os.environ.get(ENV[platform], "").strip()


def secret(platform: str) -> str | None:
    t = token(platform)
    return hashlib.sha256(f"wh|{platform}|{t}".encode()).hexdigest()[:24] if t else None


def configured() -> dict:
    return {p: bool(token(p)) for p in APIS}


def _call(platform: str, method: str, payload: dict, timeout: float = 20) -> dict:
    url = APIS[platform].format(token=token(platform)) + method
    r = httpx.post(url, json=payload, timeout=timeout)
    try:
        return r.json()
    except ValueError:
        return {"ok": False, "status": r.status_code}


def setup_webhooks(base_url: str) -> dict:
    out = {}
    for p in APIS:
        if not token(p):
            out[p] = "توکن تنظیم نشده"
            continue
        url = f"{base_url.rstrip('/')}/bot/{p}/{secret(p)}"
        try:
            res = _call(p, "setWebhook", {"url": url, "allowed_updates": ["message"]})
            me = _call(p, "getMe", {})
            out[p] = {"ok": res.get("ok"), "description": res.get("description"),
                      "bot": (me.get("result") or {}).get("username")}
        except Exception as e:  # noqa: BLE001
            out[p] = {"ok": False, "description": str(e)[:200]}
    return out


def handle_update(platform: str, update: dict) -> None:
    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat or not text:
        return
    if msg.get("chat", {}).get("type") not in (None, "private"):
        return  # فقط گفت‌وگوی خصوصی؛ در گروه‌ها جواب نمی‌دهد
    if text.startswith("/start") or text in ("سلام", "شروع"):
        _call(platform, "sendMessage", {"chat_id": chat, "text": WELCOME.format(brand=get_settings().brand_name)})
        return
    _call(platform, "sendChatAction", {"chat_id": chat, "action": "typing"})
    res = ask(text, channel=platform, user_id=str(chat))
    buttons = [[{"text": f"🛒 {p['name'][:40]}", "url": p["url"]}] for p in res["products"] if p.get("url")]
    payload = {"chat_id": chat, "text": res["answer"]}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    r = _call(platform, "sendMessage", payload)
    if not r.get("ok"):
        log.warning("ارسال پیام %s ناموفق: %s", platform, r.get("description"))
