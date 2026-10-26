"""
خواندن محصولات و دسته‌ها از Store API عمومی ووکامرس (بدون کلید).

سرور سایت کند است، پس:
- درخواست‌ها پشت‌سرهم و با فاصله فرستاده می‌شوند (نه موازی)،
- فقط فیلدهای لازم خواسته می‌شوند (_fields)،
- همگام‌سازی تکه‌تکه و قابل ادامه است (مکان‌نما در جدول settings).
"""
import html
import logging
import re
import time

import httpx

from app.config import get_settings

log = logging.getLogger("woo.store")

PRODUCT_FIELDS = ("id,name,permalink,sku,prices,on_sale,is_in_stock,images,categories,"
                  "attributes,brands,short_description")
CATEGORY_FIELDS = "id,name,slug,parent,count,permalink"
_TAG = re.compile(r"<[^>]+>")


class StoreClient:
    def __init__(self, base_url: str | None = None, delay: float = 1.5, timeout: float = 90):
        self.base = (base_url or get_settings().brand_site_url).rstrip("/") + "/wp-json/wc/store/v1"
        self.delay = delay
        self.client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "content-engine/1.0 (caravansanat marketing sync)", "Accept": "application/json"},
            follow_redirects=True,
        )
        self._last = 0.0

    def _get(self, path: str, params: dict) -> tuple[list, int, int]:
        """برمی‌گرداند: (داده‌ها، تعداد کل، تعداد صفحات)."""
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        err = None
        for attempt in range(1, 4):
            try:
                r = self.client.get(self.base + path, params=params)
                self._last = time.monotonic()
                if r.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return (r.json(), int(r.headers.get("x-wp-total") or 0), int(r.headers.get("x-wp-totalpages") or 0))
            except (httpx.HTTPError, ValueError) as e:
                err = e
                log.warning("خطا در %s (تلاش %d): %s", path, attempt, e)
                time.sleep(5 * attempt)
        raise RuntimeError(f"دریافت {path} ناموفق بود: {err}")

    def products(self, page: int, per_page: int = 100, orderby: str = "date", order: str = "desc"):
        return self._get("/products", {"per_page": per_page, "page": page, "orderby": orderby,
                                       "order": order, "_fields": PRODUCT_FIELDS})

    def categories(self, page: int, per_page: int = 100):
        return self._get("/products/categories", {"per_page": per_page, "page": page,
                                                  "_fields": CATEGORY_FIELDS})


def _int_price(v) -> int | None:
    try:
        n = int(str(v or "").strip() or 0)
        return n if n > 0 else None
    except ValueError:
        return None


def parse_product(p: dict) -> dict:
    prices = p.get("prices") or {}
    minor = int(prices.get("currency_minor_unit") or 0)
    div = 10 ** minor if minor else 1
    price = _int_price(prices.get("price"))
    regular = _int_price(prices.get("regular_price"))
    brand = None
    for b in p.get("brands") or []:
        brand = b.get("name")
        break
    if not brand:
        for a in p.get("attributes") or []:
            if (a.get("name") or "").strip() in ("برند", "brand", "Brand") and a.get("terms"):
                brand = a["terms"][0].get("name")
                break
    imgs = p.get("images") or []
    desc = html.unescape(_TAG.sub(" ", p.get("short_description") or ""))
    return {
        "id": int(p["id"]),
        "name": html.unescape(p.get("name") or "").strip(),
        "sku": p.get("sku") or None,
        "permalink": p.get("permalink"),
        "price": price // div if price else None,
        "regular_price": regular // div if regular else None,
        "on_sale": bool(p.get("on_sale")),
        "in_stock": bool(p.get("is_in_stock", True)),
        "image": (imgs[0].get("thumbnail") or imgs[0].get("src")) if imgs else None,
        "brand": brand,
        "short_desc": re.sub(r"\s+", " ", desc).strip()[:400] or None,
        "category_ids": [int(c["id"]) for c in (p.get("categories") or []) if c.get("id")],
    }


def parse_category(c: dict) -> dict:
    return {
        "id": int(c["id"]),
        "name": html.unescape(c.get("name") or "").strip(),
        "slug": c.get("slug"),
        "parent": int(c.get("parent") or 0),
        "count": int(c.get("count") or 0),
        "permalink": c.get("permalink"),
    }
