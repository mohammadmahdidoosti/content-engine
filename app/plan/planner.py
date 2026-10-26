"""
برنامه‌ریز کمپین: بر اساس تاریخ امروز، نقشه‌ی تبلیغات و داده‌ی زنده‌ی سایت پیشنهاد می‌دهد
«این هفته کدام دسته را، در کدام استان و شهرها، با چه بودجه‌ای تبلیغ کنیم».

امتیاز = زمان‌بندی × وزن گروه × وزن ارزش کالا × موجودی × اطمینان داده
"""
import math
import re

from app.db.marketing import PlanCategory
from app.db.session import get_session, get_setting
from app.plan.data import groups, month_name, today_jalali

DEFAULT_WEIGHTS = {  # کشاورزی و دستگاه‌های بزرگ صنعتی اولویت دارند
    "AGRI": 1.35, "WATER": 1.2, "HYD": 1.2, "ELEC": 1.0, "PNEU": 1.0, "PIPE": 0.9, "BUILD": 0.9, "IND": 1.0,
}
DEFAULTS = {
    "budget.monthly_toman": 10_000_000,
    "budget.avg_ad_price_toman": 600_000,
    "planner.weights": DEFAULT_WEIGHTS,
    "planner.pilot_size": 3,
}


def cfg(key: str):
    v = get_setting(key)
    return DEFAULTS.get(key) if v is None else v


def _timing(seg: dict, m: int) -> tuple[float, str]:
    nxt, nxt2 = m % 12 + 1, (m + 1) % 12 + 1
    peak, good = seg.get("peak") or [], seg.get("good") or []
    if nxt in peak:
        return 1.0, f"اوج {month_name(nxt)} است؛ الان وقت شروع تبلیغ است (۴ تا ۶ هفته قبل)"
    if m in peak:
        return 0.85, f"همین {month_name(m)} اوج است"
    if nxt2 in peak:
        return 0.6, f"اوج {month_name(nxt2)} است؛ زمان آماده‌سازی و تست کانال‌ها"
    if m in good or nxt in good:
        return 0.45, "ماه مناسب (نه اوج)"
    return 0.0, ""


def _value_weight(p90: int | None, median: int | None) -> float:
    ref = p90 or median
    if not ref:
        return 1.0
    # ۱ میلیون تومان ← ۰.۹ | ۱۰ میلیون ← ۱.۱۵ | ۱۰۰ میلیون ← ۱.۴ | ۱ میلیارد ← ۱.۶
    return max(0.8, min(1.6, 0.9 + 0.25 * (math.log10(ref) - 6)))


def _stock_weight(plan: PlanCategory) -> float:
    n = plan.in_stock_count if plan.in_stock_count else plan.product_count
    if not n:
        return 0.0
    return min(1.0, 0.35 + math.log10(1 + n) / 3)


def _conf_weight(seg: dict) -> float:
    c = seg.get("conf") or ""
    return 1.1 if "بالا" in c else (0.85 if "هوش مصنوعی" in c else 1.0)


def _cities(seg: dict) -> list[str]:
    return [c.strip() for c in re.split(r"[،,|]", seg.get("cities") or "") if c.strip()]


def channel_keywords(plan: PlanCategory, seg: dict, limit: int = 12) -> list[str]:
    """عبارت‌های جست‌وجوی کانال (در تلگرام/ایتا/بله) برای این کمپین."""
    cities = _cities(seg)[:6] or seg.get("provinces", [])[:3]
    hint = (seg.get("name") or "") + " " + (plan.buyers or "")[:60]
    if re.search(r"ساختمان|پیمانکار|سیلاب|آپارتمان|لوله‌کش|تاسیسات", hint):
        pats = ["ساختمان {c}", "پیمانکاران {c}", "نیازمندی {c}", "اخبار {c}"]
    elif re.search(r"کارخانه|صنعت|نت |تعمیرکار|تابلوساز|کارگاه", hint):
        pats = ["شهرک صنعتی {c}", "صنعت {c}", "بازار ابزار {c}", "نیازمندی {c}"]
    elif plan.group in ("AGRI", "WATER"):
        pats = ["کشاورزی {c}", "کشاورزان {c}", "اخبار {c}", "نیازمندی {c}"]
    elif plan.group == "BUILD":
        pats = ["ساختمان {c}", "پیمانکاران {c}", "نیازمندی {c}", "اخبار {c}"]
    else:
        pats = ["شهرک صنعتی {c}", "صنعت {c}", "بازار ابزار {c}", "نیازمندی {c}"]
    out = []
    for c in cities:
        for p in pats:
            out.append(p.format(c=c))
    return out[:limit]


def recommendations(limit: int = 12, month: int | None = None) -> dict:
    today = today_jalali()
    m = month or today.month
    weights = {**DEFAULT_WEIGHTS, **(cfg("planner.weights") or {})}
    recs = []
    with get_session() as db:
        plans = db.query(PlanCategory).filter(PlanCategory.status.in_(["base", "approved"])).all()
        for p in plans:
            best = None
            for idx, seg in enumerate(p.segments or []):
                t, why = _timing(seg, m)
                if t <= 0:
                    continue
                score = (t * weights.get(p.group, 1.0) * _value_weight(p.p90_price, p.median_price)
                         * _stock_weight(p) * _conf_weight(seg))
                if score <= 0:
                    continue
                if best is None or score > best["score"]:
                    best = {
                        "plan_id": p.id, "segment_index": idx, "score": round(score, 3),
                        "name": p.name, "group": p.group, "group_name": groups().get(p.group, p.group),
                        "segment": seg.get("name"), "provinces": seg.get("provinces", []),
                        "cities": seg.get("cities"), "note": seg.get("note"), "why": why,
                        "peak": seg.get("peak", []), "buyers": p.buyers, "angle": p.angle, "time": p.time,
                        "channels": p.channels, "products": p.product_count, "in_stock": p.in_stock_count,
                        "median_price": p.median_price, "p90_price": p.p90_price,
                        "examples": (p.examples or [])[:4], "keywords": channel_keywords(p, seg),
                    }
            if best:
                recs.append(best)
    recs.sort(key=lambda r: -r["score"])
    recs = recs[:limit]

    # تقسیم بودجه‌ی ماهانه بین کمپین‌های آزمایشی
    budget = int(cfg("budget.monthly_toman") or 0)
    avg_price = int(cfg("budget.avg_ad_price_toman") or 600_000)
    pilot = recs[: int(cfg("planner.pilot_size") or 3)]
    total = sum(r["score"] for r in pilot) or 1
    for r in recs:
        r["pilot"] = r in pilot
        if r["pilot"] and budget:
            b = int(round(budget * r["score"] / total / 100_000) * 100_000)
            r["budget_toman"] = b
            r["est_placements"] = max(1, b // avg_price) if avg_price else None
    return {"jalali": today.strftime("%Y/%m/%d"), "month": m, "month_name": month_name(m),
            "budget_toman": budget, "avg_ad_price_toman": avg_price, "items": recs}
