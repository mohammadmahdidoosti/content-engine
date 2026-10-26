"""
ارزیابی کانال‌ها.

تلگرام: از پیش‌نمایش عمومی t.me/s/<نام> تعداد عضو، بازدید ۲۰ پست آخر، تناوب انتشار
و سهم پست‌های تبلیغاتی خوانده می‌شود. برای ایتا، بله و روبیکا عددها را دستی وارد می‌کنید
و امتیاز با همان فرمول حساب می‌شود.

امتیاز (۰ تا ۱۰۰) یعنی «احتمال اینکه پول تبلیغ در این کانال دیده شود»، نه کیفیت مخاطب:
- دسترسی: میانگین بازدید هر پست
- سلامت: نسبت بازدید به عضو (خیلی کم ← عضو فیک؛ خیلی زیاد ← بازدید فیک یا کانال خیلی کوچک)
- شلوغی تبلیغ: کانالی که نصف پست‌هایش تبلیغ است، تبلیغ شما را گم می‌کند
- فعال بودن
"""
import html
import logging
import math
import re
from datetime import datetime, timezone

import httpx

from app.db.marketing import Channel
from app.db.session import get_session

log = logging.getLogger("channels")

_PLATFORM_HOSTS = {
    "t.me": "telegram", "telegram.me": "telegram", "eitaa.com": "eitaa",
    "ble.ir": "bale", "rubika.ir": "rubika", "instagram.com": "instagram",
}
_AD_WORDS = re.compile(r"تبلیغ|تبلیغات|#تبلیغ|رزرو|اسپانسر|حمایت\s*مالی|📣|کانال\s*زیر|عضو\s*شو|join", re.I)


def parse_channel_ref(line: str, default_platform: str = "telegram") -> tuple[str, str] | None:
    """«https://t.me/xyz» یا «@xyz» یا «eitaa.com/xyz» ← (platform, username)."""
    s = line.strip().strip("،,;")
    if not s:
        return None
    m = re.search(r"(?:https?://)?(?:www\.)?([a-z.]+\.(?:me|com|ir))/(?:s/)?(@?[A-Za-z0-9_]{3,64})", s, re.I)
    if m:
        host = m.group(1).lower()
        plat = _PLATFORM_HOSTS.get(host, "other")
        user = m.group(2).lstrip("@")
        if user.lower() in ("joinchat", "s", "c", "addlist") or user.startswith("+"):
            return None
        return plat, user
    m = re.fullmatch(r"@?([A-Za-z][A-Za-z0-9_]{3,64})", s)
    if m:
        return default_platform, m.group(1)
    return None


def _num(s: str) -> int:
    s = s.strip().replace(",", "").replace(" ", "")
    mult = 1
    if s[-1:].upper() == "K":
        mult, s = 1_000, s[:-1]
    elif s[-1:].upper() == "M":
        mult, s = 1_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def fetch_telegram(username: str, timeout: float = 30) -> dict:
    r = httpx.get(f"https://t.me/s/{username}", timeout=timeout, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (compatible; content-engine/1.0)"})
    r.raise_for_status()
    t = r.text
    if "tgme_channel_info" not in t:
        raise ValueError("پیش‌نمایش عمومی ندارد (کانال خصوصی است، گروه است یا وجود ندارد)")
    title = re.search(r'class="tgme_channel_info_header_title"[^>]*>(?:<span[^>]*>)?(.*?)</', t, re.S)
    desc = re.search(r'class="tgme_channel_info_description"[^>]*>(.*?)</div>', t, re.S)
    counters = dict((typ.strip().lower(), _num(val)) for val, typ in re.findall(
        r'<span class="counter_value">([^<]+)</span>\s*<span class="counter_type">([^<]+)</span>', t))
    members = counters.get("subscribers") or counters.get("subscriber") or counters.get("members") or 0
    views = [_num(v) for v in re.findall(r'<span class="tgme_widget_message_views">([^<]+)</span>', t)]
    dates = [datetime.fromisoformat(d) for d in re.findall(r'<time datetime="([^"]+)"', t)]
    texts = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', t, re.S)
    clean = [html.unescape(re.sub(r"<[^>]+>", " ", x)) for x in texts]
    ads = sum(1 for x in clean if _AD_WORDS.search(x) or len(re.findall(r"t\.me/|@[A-Za-z]", x)) >= 3)
    return {
        "title": html.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip() if title else None,
        "description": html.unescape(re.sub(r"<[^>]+>", " ", desc.group(1))).strip()[:500] if desc else None,
        "members": members, "views": views, "dates": dates, "posts": len(clean), "ads": ads,
    }


def score_channel(members: int | None, avg_views: int | None, posts_per_day: float | None = None,
                  ad_ratio: float | None = None, last_post_days: float | None = None,
                  price_toman: int | None = None) -> tuple[float, list[str], float | None]:
    flags: list[str] = []
    if not avg_views:
        return 0.0, ["بازدید نامشخص"], None
    reach = min(1.0, math.log10(max(avg_views, 1)) / 4.5)          # ۳۰ هزار بازدید ≈ ۱
    health = 1.0
    if members:
        vr = avg_views / members
        if vr < 0.03:
            health = 0.35
            flags.append("بازدید خیلی کم نسبت به عضو؛ احتمال عضو فیک")
        elif vr < 0.08:
            health = 0.7
        elif vr > 1.2:
            health = 0.6
            flags.append("بازدید بیشتر از عضو؛ احتمال بازدید فیک")
    clutter = 1.0
    if ad_ratio is not None:
        if ad_ratio > 0.5:
            clutter = 0.5
            flags.append("بیش از نیمی از پست‌ها تبلیغ است")
        elif ad_ratio > 0.3:
            clutter = 0.8
    activity = 1.0
    if last_post_days is not None and last_post_days > 7:
        activity = 0.5
        flags.append(f"{int(last_post_days)} روز است پست نگذاشته")
    if posts_per_day is not None and posts_per_day > 40:
        activity *= 0.8
        flags.append("پست‌گذاری خیلی زیاد؛ تبلیغ زود پایین می‌رود")
    score = round(100 * reach * health * clutter * activity, 1)
    cpm = round(price_toman / avg_views * 1000) if price_toman and avg_views else None
    return score, flags, cpm


def check_channel(channel_id: int) -> Channel:
    with get_session() as db:
        ch = db.get(Channel, channel_id)
        if ch is None:
            raise ValueError("کانال پیدا نشد")
        try:
            if ch.platform == "telegram":
                d = fetch_telegram(ch.username)
                ch.title = d["title"] or ch.title
                ch.description = d["description"]
                ch.members = d["members"] or ch.members
                v = d["views"][-20:]
                ch.avg_views = int(sum(v) / len(v)) if v else None
                ds = sorted(d["dates"])
                if len(ds) >= 2:
                    span = max((ds[-1] - ds[0]).total_seconds() / 86400, 0.25)
                    ch.posts_per_day = round(len(ds) / span, 2)
                if ds:
                    ch.last_post_days = round((datetime.now(timezone.utc) - ds[-1]).total_seconds() / 86400, 1)
                ch.ad_ratio = round(d["ads"] / d["posts"], 2) if d["posts"] else None
            ch.view_rate = round(ch.avg_views / ch.members, 3) if ch.avg_views and ch.members else None
            ch.score, ch.flags, _ = score_channel(ch.members, ch.avg_views, ch.posts_per_day, ch.ad_ratio,
                                                  ch.last_post_days, ch.price_toman)
            ch.status, ch.error = "checked", None
        except Exception as e:  # noqa: BLE001
            ch.status, ch.error = "error", str(e)[:300]
            log.info("بررسی کانال %s ناموفق: %s", ch.username, e)
        ch.checked_at = datetime.now(timezone.utc)
        db.commit()
        return ch


def recheck_stale(limit: int = 10, days: int = 7) -> dict:
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as db:
        ids = [c.id for c in db.query(Channel).filter(Channel.platform == "telegram")
               .filter((Channel.checked_at.is_(None)) | (Channel.checked_at < cutoff)).limit(limit).all()]
    for i in ids:
        check_channel(i)
    return {"checked": len(ids)}
