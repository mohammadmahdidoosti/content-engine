"""
سنجش اثر کمپین با خط مبنا (اثر اضافه، حتی وقتی مشتری روی لینک نزده).

برای هر تبلیغ منتشرشده، میانگین روزانه‌ی این شاخص‌ها در ۷ روز قبل از انتشار با ۷ روز بعد مقایسه می‌شود:
- تماس‌ها (دفتر تماس پنل)
- سفارش‌های ثبت‌شده در سایت (همه، شامل پرداخت‌نشده‌ها که در فرایند شما مشتری واقعی‌اند)
- کلیک ترب (از آخرین داده‌ی واردشده‌ی ترب؛ اثر جانبی: کسی که آگهی را دیده و بعد در ترب جست‌وجو کرده)
- اگر دسته و استان کمپین معلوم است، تماس و سفارش همان دسته/استان جدا هم حساب می‌شود (دقیق‌تر)

اگر چند تبلیغ هم‌زمان منتشر شده باشند، اثرشان از هم جدا نمی‌شود؛ گزارش این را نشان می‌دهد.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import jdatetime

from app.db.session import get_session

WINDOW = 7


def _tehran(d: datetime) -> date:
    return (d + timedelta(hours=3, minutes=30)).date()


def _j2g(s: str) -> date | None:
    try:
        y, m, d = (int(x) for x in s.replace("-", "/").split("/")[:3])
        return jdatetime.date(y, m, d).togregorian()
    except Exception:  # noqa: BLE001
        return None


def _series() -> dict[str, Counter]:
    """شاخص ← {تاریخ میلادی: مقدار}"""
    from app.db.marketing import Product
    from app.tracking.calls import CallLog
    from app.tracking.history import OrderRow
    from app.tracking.external import get
    out = {"calls": Counter(), "orders": Counter(), "torob": Counter()}
    seg_calls, seg_orders = Counter(), Counter()
    with get_session() as db:
        for r in db.query(CallLog).all():
            out["calls"][_tehran(r.ts)] += 1
            if r.plan_id and r.province:
                seg_calls[(_tehran(r.ts), r.plan_id, r.province)] += 1
        prod_plan = dict(db.query(Product.id, Product.plan_id).all())
        for o in db.query(OrderRow).all():
            if not o.created or o.status in ("cancelled", "refunded", "trash"):
                continue
            try:
                d = datetime.fromisoformat(o.created[:19]).date()
            except ValueError:
                continue
            out["orders"][d] += 1
            for li in o.items or []:
                pl = prod_plan.get(li.get("product_id"))
                if pl and o.state:
                    seg_orders[(d, pl, o.state)] += 1
    from app.db.session import get_setting
    for src in (get_setting("offline.sources") or {}).values():      # دفتر تماس اکسل (روز شمسی)
        for r in src.get("demand") or []:
            g = _j2g(r.get("day") or "")
            if g:
                out["calls"][g] += 1
                if r.get("plan") and r.get("prov") not in (None, "نامشخص"):
                    seg_calls[(g, r["plan"], r["prov"])] += 1
    for day, n in ((get("torob_clicks") or {}).get("daily") or []):
        g = _j2g(day)
        if g:
            out["torob"][g] += n
    out["_seg_calls"] = seg_calls
    out["_seg_orders"] = seg_orders
    return out


def _avg(c: Counter, start: date, days: int, today: date) -> tuple[float | None, int]:
    vals = [c.get(start + timedelta(days=i), 0) for i in range(days) if start + timedelta(days=i) <= today]
    return (round(sum(vals) / len(vals), 2) if vals else None), len(vals)


def _seg_sum(c: Counter, d: date, plan: str, provs: list[str]) -> int:
    return sum(c.get((d, plan, p), 0) for p in provs)


def placement_lift() -> list[dict]:
    from app.db.marketing import Campaign, Channel, Placement
    s = _series()
    today = _tehran(datetime.now(timezone.utc))
    with get_session() as db:
        camps = {c.id: c for c in db.query(Campaign).all()}
        chans = {c.id: (c.title or c.username) for c in db.query(Channel).all()}
        pls = db.query(Placement).filter(Placement.published_at.isnot(None)).order_by(Placement.published_at.desc()).limit(60).all()
        starts = sorted(_tehran(p.published_at) for p in pls)
        out = []
        for p in pls:
            d0 = _tehran(p.published_at)
            camp = camps.get(p.campaign_id)
            provs = [x.split(" (")[0] for x in (camp.provinces or [])] if camp else []
            row = {"id": p.id, "code": p.code, "channel": chans.get(p.channel_id), "campaign": camp.title if camp else None,
                   "published": d0.isoformat(), "cost": p.cost_toman or 0,
                   "overlap": sum(1 for x in starts if abs((x - d0).days) < WINDOW) - 1, "metrics": {}}
            for k in ("calls", "orders", "torob"):
                before, nb = _avg(s[k], d0 - timedelta(days=WINDOW), WINDOW, today)
                after, na = _avg(s[k], d0, WINDOW, today)
                row["metrics"][k] = {"before": before, "after": after, "days_after": na,
                                     "lift": round(after - before, 2) if before is not None and after is not None else None,
                                     "lift_pct": round(100 * (after - before) / before) if before and after is not None else None}
            if camp and provs:
                for k, src in (("seg_calls", "_seg_calls"), ("seg_orders", "_seg_orders")):
                    c = Counter({d0 + timedelta(days=i): _seg_sum(s[src], d0 + timedelta(days=i), camp.plan_id, provs)
                                 for i in range(-WINDOW, WINDOW)})
                    before, _ = _avg(c, d0 - timedelta(days=WINDOW), WINDOW, today)
                    after, na = _avg(c, d0, WINDOW, today)
                    row["metrics"][k] = {"before": before, "after": after, "days_after": na,
                                         "lift": round(after - before, 2) if before is not None and after is not None else None}
            # تماس/سفارش اضافه در کل پنجره ← هزینه به ازای هر مشتری اضافه
            extra = sum(max(0, (row["metrics"][k]["lift"] or 0)) * row["metrics"][k]["days_after"] for k in ("calls", "orders"))
            row["extra_leads"] = round(extra, 1)
            row["cost_per_extra_lead"] = round(row["cost"] / extra) if extra >= 1 and row["cost"] else None
            out.append(row)
    return out


def baselines() -> dict:
    """میانگین روزانه‌ی ۲۸ روز اخیر (برای نمایش خط مبنا)."""
    s = _series()
    today = _tehran(datetime.now(timezone.utc))
    return {k: _avg(s[k], today - timedelta(days=28), 28, today)[0] for k in ("calls", "orders", "torob")}
