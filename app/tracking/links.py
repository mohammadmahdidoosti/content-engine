"""لینک‌های ردیابی: UTM برای Order Attribution ووکامرس + لینک کوتاه روی همین سرویس."""
import hashlib
import os
import secrets
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from app.config import get_settings

_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # بدون 0/O و 1/I برای خواندن راحت


def new_code(prefix: str, n: int = 4) -> str:
    return prefix + "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _safe_url(url: str) -> str:
    """مسیرهای فارسی را percent-encode می‌کند تا لینک در پیام‌رسان‌ها نشکند."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, quote(p.path, safe="/%"), p.query, p.fragment))


def utm_url(target: str, source: str, campaign: str, content: str, medium: str = "channel_ad") -> str:
    target = _safe_url(target or get_settings().brand_site_url + "/shop/")
    q = urlencode({"utm_source": source[:60], "utm_medium": medium, "utm_campaign": campaign,
                   "utm_content": content})
    return target + ("&" if "?" in target else "?") + q


def short_url(code: str) -> str | None:
    base = (get_settings().public_base_url or "").rstrip("/")
    return f"{base}/r/{code}" if base else None


def visitor_hash(ip: str, ua: str) -> str:
    salt = os.environ.get("RUN_TOKEN", "salt")
    return hashlib.sha256(f"{salt}|{ip}|{ua}".encode()).hexdigest()[:32]
