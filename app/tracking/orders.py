"""
خواندن سفارش‌های ووکامرس و نسبت دادن آن‌ها به تبلیغ‌ها.

ووکامرس (از نسخه‌ی ۸.۵) منبع هر سفارش را در متادیتای سفارش ذخیره می‌کند
(_wc_order_attribution_utm_content و ...). لینک‌های تبلیغ ما utm_content=<کد تبلیغ> دارند،
پس هر سفارشی که از آن لینک ثبت شود به همان تبلیغ وصل می‌شود.

نیاز به کلید REST فقط-خواندنی ووکامرس دارد (WC_CONSUMER_KEY / WC_CONSUMER_SECRET).
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings
from app.db.marketing import Placement, TrackedOrder
from app.db.session import get_session, get_setting, set_setting

log = logging.getLogger("tracking.orders")


def enabled() -> bool:
    s = get_settings()
    return bool(s.wc_consumer_key and s.wc_consumer_secret)


def _meta(order: dict, key: str) -> str | None:
    for m in order.get("meta_data") or []:
        if m.get("key") == key:
            return str(m.get("value") or "") or None
    return None


def sync_orders(days_back: int = 3, max_pages: int = 5) -> dict:
    if not enabled():
        return {"skipped": "کلید ووکامرس تنظیم نشده"}
    s = get_settings()
    since = get_setting("orders.last_sync_at")
    after = (datetime.fromisoformat(since) - timedelta(days=1)) if since else datetime.now(timezone.utc) - timedelta(days=days_back)
    url = s.brand_site_url.rstrip("/") + "/wp-json/wc/v3/orders"
    with get_session() as db:
        codes = {p.code for p in db.query(Placement.code).all()}
    found = seen = 0
    with httpx.Client(timeout=60, auth=(s.wc_consumer_key, s.wc_consumer_secret)) as c:
        for page in range(1, max_pages + 1):
            r = c.get(url, params={"per_page": 50, "page": page, "after": after.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "orderby": "date", "order": "desc"})
            r.raise_for_status()
            orders = r.json()
            if not orders:
                break
            with get_session() as db:
                for o in orders:
                    seen += 1
                    code = (_meta(o, "_wc_order_attribution_utm_content") or "").strip().upper()
                    if code not in codes:
                        continue
                    t = db.get(TrackedOrder, int(o["id"])) or TrackedOrder(id=int(o["id"]))
                    t.code = code
                    t.source = _meta(o, "_wc_order_attribution_utm_source")
                    t.total_toman = int(float(o.get("total") or 0))
                    t.status = o.get("status")
                    t.created = o.get("date_created")
                    db.merge(t)
                    found += 1
                db.commit()
    set_setting("orders.last_sync_at", datetime.now(timezone.utc).isoformat())
    log.info("سفارش‌ها: %d بررسی، %d مربوط به تبلیغ", seen, found)
    return {"seen": seen, "attributed": found}
