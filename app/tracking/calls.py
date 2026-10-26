"""
دفتر تماس در پنل (جایگزین شیت اکسل تماس‌ها).

هر تماس: شهر، کالا، «از کجا با ما آشنا شد» (و اگر از آگهی، کد آگهی)، نتیجه و مبلغ.
نام و شماره‌ی تماس‌گیرنده ثبت نمی‌شود؛ اگر در یادداشت عدد طولانی (مثل شماره تلفن) نوشته شود حذف می‌شود.

این داده سه کار می‌کند:
- تماس‌های هر آگهی (با کد آگهی) خودکار در گزارش کمپین شمرده می‌شود ← هزینه‌ی هر تماس و سود خالص واقعی
- تقاضای هر استان و دسته در امتیاز برنامه اثر می‌گذارد (مثل دفتر تماس اکسل)
- خط مبنای تماس روزانه برای سنجش اثر کمپین
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.db.session import get_session

SOURCES = {"torob": "ترب", "google": "گوگل", "site": "سایت (مستقیم)", "telegram_ad": "آگهی تلگرام", "eitaa_ad": "آگهی ایتا/بله",
           "instagram": "اینستاگرام", "bot": "ربات/دستیار قیمت", "referral": "معرفی دیگران", "returning": "مشتری قبلی",
           "other": "سایر", "unknown": "نمی‌داند"}
OUTCOMES = {"bought": "خرید کرد", "invoice": "پیش‌فاکتور فرستاده شد", "price_only": "فقط قیمت پرسید",
            "not_available": "کالا را نداشتیم", "expensive": "گران بود", "pending": "پیگیری", "other": "سایر"}


class CallLog(Base):
    __tablename__ = "call_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)        # تاریخ شمسی 1405/08/04
    city: Mapped[str | None] = mapped_column(String(80))
    province: Mapped[str | None] = mapped_column(String(60))
    product: Mapped[str | None] = mapped_column(String(200))
    plan_id: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(20), default="unknown")
    ad_code: Mapped[str | None] = mapped_column(String(20), index=True)
    outcome: Mapped[str] = mapped_column(String(20), default="pending")
    amount_toman: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(300))


def _clean_note(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"[\d۰-۹٠-٩][\d۰-۹٠-٩\s\-]{6,}", "…", s)      # شماره‌تلفن و عددهای طولانی
    return s.strip()[:300] or None


def _jalali_today() -> str:
    from app.plan.data import today_jalali
    t = today_jalali()
    return f"{t.year}/{t.month:02d}/{t.day:02d}"


def add(data: dict) -> dict:
    from app.tracking.offline import classify, province
    city = (data.get("city") or "").strip()[:80] or None
    prov = (data.get("province") or "").strip()[:60] or None
    if city and not prov:
        p = province(None, city)
        prov = None if p == "نامشخص" else p
    product = (data.get("product") or "").strip()[:200] or None
    plan = (data.get("plan_id") or "").strip()[:40] or (classify(product) if product else None)
    if plan == "OTHER":
        plan = None
    src = data.get("source") if data.get("source") in SOURCES else "unknown"
    out = data.get("outcome") if data.get("outcome") in OUTCOMES else "pending"
    code = (data.get("ad_code") or "").strip().upper()[:20] or None
    amt = data.get("amount_toman")
    try:
        amt = int(str(amt).replace(",", "")) if amt not in (None, "") else None
    except ValueError:
        amt = None
    with get_session() as db:
        row = CallLog(ts=datetime.now(timezone.utc), day=(data.get("day") or _jalali_today())[:10], city=city, province=prov,
                      product=product, plan_id=plan, source=src, ad_code=code, outcome=out, amount_toman=amt,
                      note=_clean_note(data.get("note")))
        db.add(row)
        db.commit()
        return _row(row)


def update(cid: int, data: dict) -> dict:
    with get_session() as db:
        row = db.get(CallLog, cid)
        if not row:
            raise ValueError("پیدا نشد")
        if data.get("outcome") in OUTCOMES:
            row.outcome = data["outcome"]
        if "amount_toman" in data:
            try:
                row.amount_toman = int(str(data["amount_toman"]).replace(",", "")) if data["amount_toman"] not in (None, "") else None
            except ValueError:
                pass
        if "note" in data:
            row.note = _clean_note(data["note"])
        db.commit()
        return _row(row)


def delete(cid: int) -> None:
    with get_session() as db:
        row = db.get(CallLog, cid)
        if row:
            db.delete(row)
            db.commit()


def _row(r: CallLog) -> dict:
    return {"id": r.id, "ts": r.ts.isoformat() if r.ts else None, "day": r.day, "city": r.city, "province": r.province,
            "product": r.product, "plan_id": r.plan_id, "source": r.source, "ad_code": r.ad_code, "outcome": r.outcome,
            "amount_toman": r.amount_toman, "note": r.note}


def recent(limit: int = 100) -> list[dict]:
    with get_session() as db:
        return [_row(r) for r in db.query(CallLog).order_by(CallLog.id.desc()).limit(limit).all()]


def stats(days: int = 60) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as db:
        rows = db.query(CallLog).filter(CallLog.ts >= since).all()
    by_day = Counter(r.day for r in rows)
    src = defaultdict(lambda: {"calls": 0, "bought": 0, "amount": 0})
    for r in rows:
        s = src[r.source]
        s["calls"] += 1
        if r.outcome == "bought":
            s["bought"] += 1
            s["amount"] += r.amount_toman or 0
    return {"total": len(rows), "by_day": sorted(by_day.items())[-45:],
            "per_day": round(len(rows) / len(by_day), 1) if by_day else None,
            "sources": sorted(({"source": k, "label": SOURCES.get(k, k), **v,
                                "conv_pct": round(100 * v["bought"] / v["calls"], 1) if v["calls"] else None}
                               for k, v in src.items()), key=lambda x: -x["calls"]),
            "outcomes": Counter(r.outcome for r in rows).most_common(),
            "provinces": Counter(r.province or "نامشخص" for r in rows).most_common(15),
            "plans": Counter(r.plan_id for r in rows if r.plan_id).most_common(15),
            "no_city_pct": round(100 * sum(1 for r in rows if not r.city) / len(rows)) if rows else None,
            "labels": {"sources": SOURCES, "outcomes": OUTCOMES}}


def by_ad_code() -> dict[str, dict]:
    """کد آگهی ← {calls, bought, amount}؛ برای گزارش کمپین."""
    out: dict = defaultdict(lambda: {"calls": 0, "bought": 0, "amount": 0})
    with get_session() as db:
        for r in db.query(CallLog).filter(CallLog.ad_code.isnot(None)).all():
            o = out[r.ad_code]
            o["calls"] += 1
            if r.outcome == "bought":
                o["bought"] += 1
                o["amount"] += r.amount_toman or 0
    return dict(out)


def plan_province_counts() -> dict[tuple[str, str], float]:
    """(دسته، استان) ← تقاضا؛ هر تماس نصف یک فاکتور، هر خرید یک فاکتور."""
    out: dict = defaultdict(float)
    with get_session() as db:
        for r in db.query(CallLog).filter(CallLog.plan_id.isnot(None), CallLog.province.isnot(None)).all():
            out[(r.plan_id, r.province)] += 1.0 if r.outcome == "bought" else 0.5
    return dict(out)


def daily_counts() -> dict[str, int]:
    with get_session() as db:
        return dict(Counter(d for (d,) in db.query(CallLog.day).all()))
