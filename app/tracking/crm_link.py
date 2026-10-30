"""اتصال به CRM جدا (crm.caravansanat.com): فقط عددها به تفکیک کد آگهی و منبع؛ نام و شماره‌ی مشتری هرگز به اینجا نمی‌آید.
CRM_URL و CRM_STATS_TOKEN در Coolify (همان CRM_STATS_TOKEN سرویس CRM)."""
import logging
import os
import time

import httpx

log = logging.getLogger("tracking.crm")
_cache = {"at": 0.0, "data": None}


def stats(max_age: int = 120) -> dict | None:
    url, tok = os.environ.get("CRM_URL", "").rstrip("/"), os.environ.get("CRM_STATS_TOKEN", "")
    if not url or len(tok) < 16:
        return None
    if _cache["data"] is not None and time.time() - _cache["at"] < max_age:
        return _cache["data"]
    try:
        r = httpx.get(url + "/api/stats", headers={"X-CRM-Token": tok}, timeout=10)
        r.raise_for_status()
        _cache.update(at=time.time(), data=r.json())
    except Exception as e:  # noqa: BLE001
        log.info("CRM در دسترس نیست: %s", e)
        _cache["at"] = time.time()
    return _cache["data"]
