"""
تحلیل سفارش‌های قبلی ووکامرس (مرحله‌ی ۴).

با کلید فقط‌خواندنی، سفارش‌ها (شهر/استان خریدار، کالاها، مبلغ، منبع ورود) خوانده و
خلاصه می‌شوند تا معلوم شود مشتری‌های واقعی از کجا و برای چه کالایی آمده‌اند.
اطلاعات شخصی (نام، تلفن، آدرس دقیق، ایمیل) ذخیره نمی‌شود؛ فقط شهر و استان.
"""
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import JSON, BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.db.models import Base
from app.db.session import get_session, get_setting, set_setting

log = logging.getLogger("tracking.history")


class OrderRow(Base):
    __tablename__ = "order_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created: Mapped[str | None] = mapped_column(String(40), index=True)
    status: Mapped[str | None] = mapped_column(String(40))
    total_toman: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str | None] = mapped_column(String(80))       # نام استان (یا کد اگر نام پیدا نشد)
    city: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str | None] = mapped_column(String(120))     # منبع ورود (utm یا نوع منبع)
    items: Mapped[list] = mapped_column(JSON, default=list)     # [{product_id, qty, total}]


def _client():
    s = get_settings()
    return httpx.Client(timeout=90, auth=(s.wc_consumer_key, s.wc_consumer_secret),
                        headers={"User-Agent": "content-engine/1.0"})


def _states(c) -> dict[str, str]:
    cached = get_setting("orders.state_names")
    if cached:
        return cached
    base = get_settings().brand_site_url.rstrip("/") + "/wp-json/wc/v3"
    try:
        r = c.get(base + "/data/countries/ir")
        r.raise_for_status()
        m = {st["code"]: st["name"] for st in r.json().get("states") or []}
    except Exception as e:  # noqa: BLE001
        log.info("نام استان‌ها خوانده نشد: %s", e)
        m = {}
    if m:
        set_setting("orders.state_names", m)
    return m


def _meta(o: dict, key: str) -> str | None:
    for m in o.get("meta_data") or []:
        if m.get("key") == key:
            return str(m.get("value") or "") or None
    return None


def sync_history(days: int = 540, max_pages: int = 30, deadline: float | None = None) -> dict:
    """سفارش‌ها را صفحه‌به‌صفحه (جدیدترین اول) می‌خواند؛ بار اول تا ۱۸ ماه قبل، بعد فقط تازه‌ها."""
    import time
    from app.tracking.orders import enabled
    if not enabled():
        return {"skipped": "کلید ووکامرس تنظیم نشده"}
    s = get_settings()
    url = s.brand_site_url.rstrip("/") + "/wp-json/wc/v3/orders"
    last = get_setting("history.last_sync_at")
    after = (datetime.fromisoformat(last) - timedelta(days=2)) if last else datetime.now(timezone.utc) - timedelta(days=days)
    start = int(get_setting("history.page") or 1) if not last else 1
    seen = 0
    with _client() as c:
        names = _states(c)
        for page in range(start, start + max_pages):
            if deadline and time.monotonic() > deadline:
                set_setting("history.page", page)
                return {"orders": seen, "complete": False, "next_page": page}
            r = c.get(url, params={"per_page": 100, "page": page, "after": after.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "orderby": "date", "order": "desc"})
            r.raise_for_status()
            orders = r.json()
            if not orders:
                break
            with get_session() as db:
                for o in orders:
                    b = o.get("billing") or {}
                    sh = o.get("shipping") or {}
                    st = b.get("state") or sh.get("state")
                    src = (_meta(o, "_wc_order_attribution_utm_source") or _meta(o, "_wc_order_attribution_source_type")
                           or None)
                    row = OrderRow(
                        id=int(o["id"]), created=o.get("date_created"), status=o.get("status"),
                        total_toman=int(float(o.get("total") or 0)), state=names.get(st, st) if st else None,
                        city=(b.get("city") or sh.get("city") or "").strip()[:120] or None, source=src,
                        items=[{"product_id": li.get("product_id"), "qty": li.get("quantity"),
                                "total": int(float(li.get("total") or 0))} for li in o.get("line_items") or []])
                    db.merge(row)
                    seen += 1
                db.commit()
        else:
            set_setting("history.page", start + max_pages)
            return {"orders": seen, "complete": False, "next_page": start + max_pages}
    set_setting("history.page", 1)
    set_setting("history.last_sync_at", datetime.now(timezone.utc).isoformat())
    log.info("تاریخچه‌ی سفارش‌ها: %d سفارش خوانده شد", seen)
    return {"orders": seen, "complete": True}


GOOD = {"completed", "processing", "on-hold", "pending"}   # «pending» = سفارش ثبت‌شده‌ای که هنوز تأیید نشده


def summary() -> dict:
    from app.db.marketing import PlanCategory, Product
    with get_session() as db:
        rows = db.query(OrderRow).all()
        prod_plan = dict(db.query(Product.id, Product.plan_id).all())
        prod_name = dict(db.query(Product.id, Product.name).all())
        plan_name = dict(db.query(PlanCategory.id, PlanCategory.name).all())
    by_state, by_city, by_status, by_source = Counter(), Counter(), Counter(), Counter()
    rev_state = Counter()
    plan_state: dict[str, Counter] = defaultdict(Counter)
    top_products, plan_total = Counter(), Counter()
    months = Counter()
    for o in rows:
        by_status[o.status] += 1
        if o.status not in GOOD:
            continue
        stt = o.state or "نامشخص"
        by_state[stt] += 1
        rev_state[stt] += o.total_toman or 0
        if o.city:
            by_city[f"{o.city} ({stt})"] += 1
        by_source[o.source or "نامشخص"] += 1
        if o.created:
            months[o.created[:7]] += 1
        for li in o.items or []:
            pid = li.get("product_id")
            top_products[pid] += li.get("qty") or 1
            pl = prod_plan.get(pid)
            if pl:
                plan_state[pl][stt] += 1
                plan_total[pl] += 1
    return {
        "orders": sum(by_state.values()), "all_orders": len(rows), "by_status": dict(by_status),
        "last_sync_at": get_setting("history.last_sync_at"),
        "states": [{"state": k, "orders": v, "revenue": rev_state[k]} for k, v in by_state.most_common(31)],
        "cities": [{"city": k, "orders": v} for k, v in by_city.most_common(30)],
        "sources": by_source.most_common(12), "months": sorted(months.items())[-18:],
        "products": [{"id": k, "name": prod_name.get(k), "qty": v} for k, v in top_products.most_common(20)],
        "plans": [{"plan_id": k, "name": plan_name.get(k), "orders": v,
                   "top_states": plan_state[k].most_common(5)} for k, v in plan_total.most_common(25)],
    }


def province_demand() -> dict[tuple[str, str], int]:
    """(plan_id, استان) ← تعداد سفارش؛ برای اثر دادن داده‌ی خود فروشگاه در برنامه."""
    from app.db.marketing import Product
    out: Counter = Counter()
    with get_session() as db:
        prod_plan = dict(db.query(Product.id, Product.plan_id).all())
        for o in db.query(OrderRow).all():
            if o.status not in GOOD or not o.state:
                continue
            for li in o.items or []:
                pl = prod_plan.get(li.get("product_id"))
                if pl:
                    out[(pl, o.state)] += 1
    return dict(out)
