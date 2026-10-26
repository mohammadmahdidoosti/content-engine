"""
برنامه‌ریز کمپین: بر اساس تاریخ امروز، نقشه‌ی تبلیغات و داده‌ی زنده‌ی سایت پیشنهاد می‌دهد
«این هفته کدام دسته را، در کدام استان و شهرها، با چه بودجه‌ای تبلیغ کنیم».

امتیاز = زمان‌بندی × وزن گروه × وزن ارزش کالا × موجودی × اطمینان داده
         × حاشیه‌ی سود (اگر وارد شده) × عملکرد واقعی تبلیغ‌های قبلی × هشدار آب‌وهوا
"""
import math
import re
import time
from collections import Counter
from statistics import median

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
    "planner.margins": {},     # plan_id یا گروه ← low | mid | high
}
MARGIN_FACTORS = {"low": 0.8, "mid": 1.0, "high": 1.3}


def _strip(p: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", p or "").strip()


def cfg(key: str):
    v = get_setting(key)
    return DEFAULTS.get(key) if v is None else v


def margin_weight(plan_id: str, group: str | None = None) -> float:
    """حاشیه‌ی سود: اول تنظیم خود دسته، بعد تنظیم گروه."""
    mg = cfg("planner.margins") or {}
    lvl = mg.get(plan_id) or (mg.get(group) if group else None)
    if lvl in MARGIN_FACTORS:
        return MARGIN_FACTORS[lvl]
    try:  # اگر دستی تنظیم نشده: سود واقعی هر فاکتور از فایل فروش آفلاین
        from app.tracking.offline import profit_factor
        return profit_factor(plan_id) or 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def _torob_w(plan_id: str) -> float:
    try:
        from app.tracking.external import torob_weight
        return torob_weight(plan_id)
    except Exception:  # noqa: BLE001
        return 1.0


def _unit_economics(plan_id: str) -> dict:
    """از فایل فروش آفلاین: سود هر فروش و سقف هزینه‌ی هر تماس (اگر فایل بارگذاری شده باشد)."""
    try:
        from app.tracking.offline import plan_stats, summary
        st = plan_stats(plan_id) or {}
        if (st.get("invoices") or 0) < 4:
            s = summary() or {}
            st = {"avg_profit": s.get("avg_profit"), "max_cost_per_call": s.get("max_cost_per_call"),
                  "margin_pct": s.get("margin_pct"), "invoices": 0}
        return {"profit_per_sale": st.get("avg_profit"), "max_cost_per_call": st.get("max_cost_per_call"),
                "real_margin_pct": st.get("margin_pct"), "past_invoices": st.get("invoices") or 0}
    except Exception:  # noqa: BLE001
        return {}


_perf_cache: dict = {"at": 0.0, "data": {}}


def performance_by_plan() -> dict[str, dict]:
    """هزینه به ازای هر مشتری بالقوه، برای هر دسته‌ی نقشه، از تبلیغ‌های پرداخت‌شده."""
    if time.monotonic() - _perf_cache["at"] < 60:
        return _perf_cache["data"]
    from app.db.marketing import Campaign, Placement
    from app.plan.campaigns import placement_metrics
    out: dict[str, dict] = {}
    with get_session() as db:
        m = placement_metrics(db)
        camps = {c.id: c.plan_id for c in db.query(Campaign).all()}
        for pl in db.query(Placement).filter(Placement.status.in_(["published", "done"])).all():
            x = m.get(pl.id) or {}
            d = out.setdefault(camps.get(pl.campaign_id), {"spent": 0, "leads": 0, "placements": 0, "revenue": 0})
            d["spent"] += x.get("cost") or 0
            d["leads"] += x.get("leads") or 0
            d["revenue"] += x.get("revenue") or 0
            d["placements"] += 1
    for d in out.values():
        d["cpl"] = d["spent"] / d["leads"] if d["leads"] else None
    _perf_cache.update(at=time.monotonic(), data=out)
    return out


def performance_weight(plan_id: str) -> float:
    """
    یادگیری از نتیجه: دسته‌ای که با حداقل ۲ تبلیغ منتشرشده هزینه‌ی هر مشتری‌اش از میانه کمتر است
    امتیاز بیشتری می‌گیرد (تا ×۱.۶) و برعکس (تا ×۰.۶). دسته‌ی بی‌نتیجه با ۳ تبلیغ یا بیشتر ×۰.۶.
    """
    perf = performance_by_plan()
    d = perf.get(plan_id)
    if not d or d["placements"] < 2:
        return 1.0
    cpls = [x["cpl"] for x in perf.values() if x.get("cpl") and x["placements"] >= 2]
    if d["cpl"] is None:
        return 0.6 if d["placements"] >= 3 and d["spent"] > 0 else 1.0
    if not cpls:
        return 1.0
    return max(0.6, min(1.6, median(cpls) / d["cpl"]))


_hist_cache: dict = {"at": 0.0, "data": {}}


def history_weight(plan_id: str, provinces: list[str]) -> tuple[float, int]:
    """داده‌ی خود فروشگاه: اگر از این استان‌ها قبلاً برای این دسته سفارش آمده، امتیاز بیشتر (تا ×۱.۴)."""
    if time.monotonic() - _hist_cache["at"] > 300:
        try:
            from app.tracking.history import province_demand
            from app.tracking.offline import plan_province_counts
            d = Counter(province_demand())
            d.update(plan_province_counts())   # فاکتورهای تلفنی/آفلاین هم حساب می‌شوند
            _hist_cache["data"] = dict(d)
        except Exception:  # noqa: BLE001
            _hist_cache["data"] = {}
        _hist_cache["at"] = time.monotonic()
    n = sum(_hist_cache["data"].get((plan_id, _strip(p)), 0) for p in provinces)
    return (1 + min(0.4, 0.1 * math.log2(1 + n)) if n else 1.0), n


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
    from app.plan.pricing import plan_price_weight
    from app.plan.weather import boosts
    wx = boosts()
    with get_session() as db:
        plans = db.query(PlanCategory).filter(PlanCategory.status.in_(["base", "approved"])).all()
        for p in plans:
            best = None
            price_w = plan_price_weight(p.examples or [])
            for idx, seg in enumerate(p.segments or []):
                t, why = _timing(seg, m)
                alerts = [a for a in wx.get((p.id, idx), []) if a["province"] in [_strip(x) for x in seg.get("provinces", [])]]
                if alerts:
                    t = max(t, 0.9)
                    why = " · ".join(sorted({f"{a['icon']} {a['label']} در {a['province']}" for a in alerts})) + (f" — {why}" if why else "")
                if t <= 0:
                    continue
                score = (t * weights.get(p.group, 1.0) * _value_weight(p.p90_price, p.median_price)
                         * _stock_weight(p) * _conf_weight(seg) * margin_weight(p.id, p.group)
                         * performance_weight(p.id) * price_w * (1.25 if alerts else 1.0)
                         * history_weight(p.id, seg.get("provinces", []))[0] * _torob_w(p.id))
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
                        "weather": alerts, "margin": margin_weight(p.id, p.group),
                        "performance": performance_weight(p.id), "price_weight": price_w,
                        "past_orders": history_weight(p.id, seg.get("provinces", []))[1],
                        **_unit_economics(p.id), "torob_weight": _torob_w(p.id),
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
