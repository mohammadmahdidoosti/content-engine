"""
معیارهای دقیق کانال برای تبلیغ («بازدید واقعی آگهی»).

ورودی: پست‌های آخر کانال (بازدید، زمان، متن، واکنش) + تعداد عضو؛ برای تلگرام از صفحه‌ی عمومی t.me/s،
برای ایتا از نسخه‌ی وب در مرورگر صاحب فروشگاه. همه‌ی پیام‌رسان‌ها با یک فرمول سنجیده می‌شوند.

هر پست یکی از سه نوع است:
- join_ad    : «جوین تبلیغاتی» (لینک عضویت، چند آیدی کانال، «عضو شو»)
- product_ad : تبلیغ کالا/خدمات (#تبلیغ، شماره تلفن، قیمت، سفارش…)
- content    : محتوای خود کانال

معیارها:
- median_views          : میانه‌ی بازدید پست‌های قدیمی‌تر از ۲۴ ساعت (پست تازه هنوز بازدیدش کامل نشده)
- view_rate             : median_views ÷ عضو
- ad_view_ratio         : میانه‌ی بازدید پست‌های تبلیغاتی ÷ میانه‌ی بازدید محتوا (مخاطب از تبلیغ رد می‌شود یا نه)
- expected_ad_views     : بازدید مورد انتظار آگهی ما = median_views × ad_view_ratio (پیش‌فرض ۰٫۸)
- ads_per_day, join_ads_per_day : شلوغی تبلیغ
- ad_life_hours         : میانه‌ی فاصله‌ی هر تبلیغ تا پست بعدی (آگهی چند ساعت بالای کانال می‌ماند)
- views_cv              : پراکندگی بازدیدها؛ خیلی یکنواخت (< ۰٫۰۸) یعنی احتمال بازدید خریدنی
- reactions_per_1k      : واکنش به ازای هر ۱۰۰۰ بازدید
"""
import math
import re
import statistics
from datetime import datetime, timedelta, timezone

_JOIN = re.compile(r"t\.me/\+|t\.me/joinchat|joinchat|عضو\s*شو|جوین|join\b|لینک\s*(?:کانال|عضویت)|بزن\s*رو|کلیک\s*کن", re.I)
_PRODUCT = re.compile(r"#تبلیغ|تبلیغات|رزرو|حمایت\s*مالی|اسپانسر|(?:09|۰۹)[\d۰-۹]{9}|تومان|ریال|قیمت|سفارش|خرید|فروش|"
                      r"تخفیف|ارسال\s*(?:رایگان|به)|واتساپ|دایرکت|📞|☎", re.I)
_MENTION = re.compile(r"(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{3,31})")
_AD_CONTACT = re.compile(r"(?:تبلیغ|تبلیغات|رزرو|سفارش|تعرفه|ادمین|ارتباط)[^\n@]{0,40}?(@[A-Za-z][A-Za-z0-9_]{3,31})", re.I)
_PHONE = re.compile(r"(?:09|۰۹)[\d۰-۹]{9}")


def classify_post(text: str, self_name: str = "") -> str:
    t = text or ""
    others = {m.lower() for m in _MENTION.findall(t)} - {self_name.lower()}
    if _JOIN.search(t) or len(others) >= 3:
        return "join_ad"
    if _PRODUCT.search(t) and (len(t) < 1500):
        return "product_ad"
    return "content"


def ad_contacts(description: str | None, self_name: str = "") -> list[str]:
    """آیدی/شماره‌ی «سفارش تبلیغ» از توضیح کانال (اطلاعات عمومی کسب‌وکار، نه شخصی). خود کانال حذف می‌شود و
    آیدی‌هایی مثل tabligh/ads/admin جلوتر می‌آیند."""
    d = description or ""
    out = [m for m in _AD_CONTACT.findall(d) if m.lstrip("@").lower() != self_name.lower()]
    out.sort(key=lambda x: 0 if re.search(r"tab|ads|adv|admin|sefaresh|rezerv", x, re.I) else 1)
    if re.search(r"تبلیغ|رزرو|تعرفه", d):
        out += _PHONE.findall(d)
    seen, res = set(), []
    for x in out:
        if x.lower() not in seen:
            seen.add(x.lower())
            res.append(x)
    return res[:4]


def _median(xs):
    return statistics.median(xs) if xs else None


def compute(posts: list[dict], members: int | None, self_name: str = "", now: datetime | None = None) -> dict:
    """posts: [{views:int, ts:datetime|None, text:str, reactions:int}] به ترتیب زمان (قدیمی ← جدید)."""
    now = now or datetime.now(timezone.utc)
    ps = [p for p in posts if p.get("views") is not None]
    for p in ps:
        p["kind"] = classify_post(p.get("text") or "", self_name)
    mature = [p for p in ps if p.get("ts") and now - p["ts"] > timedelta(hours=24)] or ps
    views = [p["views"] for p in mature if p["views"]]
    med = _median(views)
    content_v = [p["views"] for p in mature if p["kind"] == "content" and p["views"]]
    ad_v = [p["views"] for p in mature if p["kind"] != "content" and p["views"]]
    ratio = None
    if len(ad_v) >= 2 and len(content_v) >= 3 and _median(content_v):
        ratio = round(max(0.1, min(1.5, _median(ad_v) / _median(content_v))), 2)
    ts = sorted(p["ts"] for p in ps if p.get("ts"))
    span_days = max((ts[-1] - ts[0]).total_seconds() / 86400, 0.25) if len(ts) >= 2 else None
    n_ads = sum(1 for p in ps if p["kind"] != "content")
    n_join = sum(1 for p in ps if p["kind"] == "join_ad")
    life = []
    ordered = sorted([p for p in ps if p.get("ts")], key=lambda p: p["ts"])
    for a, b in zip(ordered, ordered[1:]):
        if a["kind"] != "content":
            life.append((b["ts"] - a["ts"]).total_seconds() / 3600)
    cv = None
    if len(content_v) >= 8:
        mean = sum(content_v) / len(content_v)
        cv = round(statistics.pstdev(content_v) / mean, 3) if mean else None
    reacts = sum(p.get("reactions") or 0 for p in mature)
    tot_v = sum(views) or 0
    expected = round(med * (ratio if ratio is not None else 0.8)) if med else None
    return {
        "posts": len(ps), "median_views": round(med) if med else None,
        "view_rate": round(med / members, 3) if med and members else None,
        "ad_view_ratio": ratio, "expected_ad_views": expected,
        "posts_per_day": round(len(ts) / span_days, 2) if span_days else None,
        "ads_per_day": round(n_ads / span_days, 2) if span_days else None,
        "join_ads_per_day": round(n_join / span_days, 2) if span_days else None,
        "ad_share": round(n_ads / len(ps), 2) if ps else None,
        "ad_life_hours": round(_median(life), 1) if life else None,
        "views_cv": cv, "reactions_per_1k": round(1000 * reacts / tot_v, 2) if tot_v else None,
        "last_post_days": round((now - ts[-1]).total_seconds() / 86400, 1) if ts else None,
        "kinds": {k: sum(1 for p in ps if p["kind"] == k) for k in ("content", "product_ad", "join_ad")},
    }


def score(m: dict, members: int | None, price_toman: int | None = None) -> tuple[float, list[str], float | None]:
    """۰ تا ۱۰۰: «احتمال اینکه پول آگهی دیده شود» بر اساس بازدید واقعی آگهی."""
    flags: list[str] = []
    exp = m.get("expected_ad_views") or 0
    if not exp:
        return 0.0, ["بازدید نامشخص"], None
    reach = min(1.0, math.log10(max(exp, 1)) / 4.3)          # حدود ۲۰ هزار بازدید آگهی ≈ ۱
    health = 1.0
    vr = m.get("view_rate")
    if vr is not None:
        if vr < 0.03:
            health = 0.35
            flags.append(f"بازدید فقط {vr * 100:.1f}٪ عضو؛ احتمال عضو فیک")
        elif vr < 0.08:
            health = 0.7
        elif vr > 1.2:
            health = 0.6
            flags.append("بازدید بیشتر از عضو؛ احتمال بازدید خریدنی")
    if m.get("views_cv") is not None and m["views_cv"] < 0.08:
        health *= 0.6
        flags.append("بازدید همه‌ی پست‌ها تقریباً یکسان است؛ احتمال بازدید خریدنی")
    r = m.get("ad_view_ratio")
    if m.get("member_jump"):
        health *= 0.8
        flags.append(f"عضو کانال یک‌دفعه {m['member_jump']} برابر شده؛ احتمال عضو خریدنی")
    if r is not None and r < 0.5:
        flags.append(f"پست‌های تبلیغاتی {int(r * 100)}٪ پست‌های عادی دیده می‌شوند")
    clutter = 1.0
    apd = m.get("ads_per_day") or 0
    if apd > 8:
        clutter = 0.55
        flags.append(f"روزی {apd:.0f} تبلیغ؛ آگهی گم می‌شود")
    elif apd > 4:
        clutter = 0.8
    if (m.get("join_ads_per_day") or 0) > 5:
        clutter *= 0.85
        flags.append("جوین تبلیغاتی زیاد")
    life = 1.0
    if m.get("ad_life_hours") is not None and m["ad_life_hours"] < 0.5:
        life = 0.8
        flags.append("تبلیغ کمتر از نیم ساعت بالای کانال می‌ماند")
    activity = 1.0
    if (m.get("last_post_days") or 0) > 7:
        activity = 0.5
        flags.append(f"{int(m['last_post_days'])} روز است پست نگذاشته")
    s = round(100 * reach * health * clutter * life * activity, 1)
    cpm = round(price_toman / exp * 1000) if price_toman and exp else None
    return s, flags, cpm


_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def parse_count(s: str | None) -> int:
    """«1.39K»، «18.8K»، «1.6میلیون»، «154.5هزار»، «۱۸٫۸K» ← عدد."""
    if not s:
        return 0
    t = str(s).translate(_FA).replace("٫", ".").replace(",", "").replace("٬", "").strip()
    m = re.search(r"([\d.]+)\s*(K|M|هزار|میلیون)?", t, re.I)
    if not m:
        return 0
    v = float(m.group(1))
    mult = {"k": 1e3, "m": 1e6, "هزار": 1e3, "میلیون": 1e6}.get((m.group(2) or "").lower(), 1)
    return int(v * mult)
