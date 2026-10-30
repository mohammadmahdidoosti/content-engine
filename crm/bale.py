"""یادآور صبحگاهی در بله: فهرست پیگیری‌های امروز برای فروشنده (و مدیر)."""
import logging
import os
import secrets
import threading
import time
from datetime import datetime

import httpx

from crm.core import TEHRAN, jalali, today_view
from crm.db import get_setting, set_setting

log = logging.getLogger("crm.bale")
API = "https://tapi.bale.ai/bot{token}/{method}"
SEND_AT = (8, 30)        # ساعت تهران


def _token() -> str:
    return os.environ.get("BALE_BOT_TOKEN", "").strip()


def call(method: str, **params) -> dict:
    t = _token()
    if not t:
        raise ValueError("BALE_BOT_TOKEN در Coolify تنظیم نشده")
    r = httpx.post(API.format(token=t, method=method), json=params, timeout=20)
    d = r.json()
    if not d.get("ok"):
        raise ValueError(f"بله: {d.get('description') or r.status_code}")
    return d.get("result")


def link_code() -> str:
    code = get_setting("bale.link_code")
    if not code:
        code = str(secrets.randbelow(900000) + 100000)
        set_setting("bale.link_code", code)
    return code


def link() -> dict:
    """فروشنده کد ۶ رقمی را به ربات می‌فرستد؛ اینجا گفتگوی او پیدا و ذخیره می‌شود."""
    code = link_code()
    chats = list(get_setting("bale.chats") or [])
    found = None
    for u in call("getUpdates", limit=100) or []:
        m = u.get("message") or {}
        if (m.get("text") or "").strip().endswith(code):
            ch = m.get("chat") or {}
            found = {"id": ch.get("id"), "name": " ".join(x for x in (ch.get("first_name"), ch.get("last_name")) if x) or ch.get("username") or ""}
    if not found or not found.get("id"):
        raise ValueError(f"پیام «{code}» به ربات بله نرسیده؛ اول کد را برای ربات بفرستید، بعد دوباره بزنید")
    if all(c["id"] != found["id"] for c in chats):
        chats.append(found)
        set_setting("bale.chats", chats)
    set_setting("bale.link_code", None)
    call("sendMessage", chat_id=found["id"], text="✅ یادآور CRM کاروان صنعت وصل شد. هر روز صبح فهرست پیگیری‌ها را می‌فرستم.")
    return {"linked": found, "chats": chats}


def unlink(chat_id) -> dict:
    chats = [c for c in (get_setting("bale.chats") or []) if str(c["id"]) != str(chat_id)]
    set_setting("bale.chats", chats)
    return {"chats": chats}


def morning_text(base_url: str = "") -> str:
    v = today_view()
    due, fresh = v["due"], v["no_followup"]
    lines = [f"☀️ پیگیری‌های امروز ({v['today']}): {len(due)} مورد"]
    for d in due[:25]:
        c = d["contact"] or {}
        late = " ⏰ عقب‌افتاده" if d["overdue"] else ""
        lines.append(f"• {c.get('name') or 'بدون نام'} — {d['title'][:40]} ({d['stage_name']}){late}")
    if len(due) > 25:
        lines.append(f"… و {len(due) - 25} مورد دیگر")
    if fresh:
        lines.append(f"\n🆕 {len(fresh)} درخواست بدون تاریخ پیگیری")
    if base_url:
        lines.append(f"\n👈 {base_url}")
    return "\n".join(lines)


def send_morning(base_url: str = "") -> int:
    chats = get_setting("bale.chats") or []
    if not chats or not _token():
        return 0
    text = morning_text(base_url)
    n = 0
    for c in chats:
        try:
            call("sendMessage", chat_id=c["id"], text=text)
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("ارسال بله ناموفق: %s", e)
    return n


def loop(base_url: str = "") -> None:
    def run():
        while True:
            try:
                now = datetime.now(TEHRAN)
                key = now.date().isoformat()
                if (now.hour, now.minute) >= SEND_AT and get_setting("bale.sent_day") != key:
                    set_setting("bale.sent_day", key)
                    n = send_morning(base_url)
                    log.info("یادآور صبحگاهی: %s گفتگو", n)
            except Exception:  # noqa: BLE001
                log.exception("یادآور بله")
            time.sleep(60)
    threading.Thread(target=run, daemon=True, name="bale").start()


__all__ = ["call", "link", "link_code", "unlink", "send_morning", "morning_text", "loop", "jalali"]
