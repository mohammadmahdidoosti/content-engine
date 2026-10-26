"""
مقایسه‌ی قیمت با بازار (ترب).

برای هر کالای کاندید تبلیغ، نامش در ترب جست‌وجو می‌شود؛ نزدیک‌ترین نتیجه‌ها (به‌خصوص با کد مدل
یکسان) پیدا می‌شوند و کمترین قیمت فروشنده‌های دیگر (به جز خود کاروان صنعت) با قیمت سایت مقایسه می‌شود.

آرام و کم کار می‌کند (هر ۳ ثانیه یک درخواست، چند ده کالا در روز) و نتیجه چند روز معتبر می‌ماند.
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.db.session import get_session, get_setting, set_setting

log = logging.getLogger("plan.pricing")
OWN_SHOP = "کاروان صنعت"
API = "https://api.torob.com/v4/base-product/search/"
FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class PriceCheck(Base):
    __tablename__ = "price_checks"
    product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    our_price: Mapped[int | None] = mapped_column(BigInteger)
    market_min: Mapped[int | None] = mapped_column(BigInteger)
    shops: Mapped[int | None] = mapped_column(Integer)
    ratio: Mapped[float | None] = mapped_column(Float)          # قیمت ما ÷ کمترین قیمت بازار
    status: Mapped[str] = mapped_column(String(20))              # cheaper | equal | pricier | only_us | no_match | error
    match_name: Mapped[str | None] = mapped_column(Text)
    match_url: Mapped[str | None] = mapped_column(Text)


def _tokens(s: str) -> set[str]:
    s = (s or "").translate(FA_DIGITS).lower().replace("‌", " ")
    s = re.sub(r"[()\[\]،,:؛/\\+]", " ", s)
    stop = {"مدل", "برند", "با", "و", "در", "از", "سری", "عدد", "عددی", "کد", "اصل", "ساخت"}
    return {t for t in s.split() if len(t) >= 2 and t not in stop}


def _model_codes(s: str) -> set[str]:
    s = (s or "").translate(FA_DIGITS).upper()
    return {m for m in re.findall(r"[A-Z0-9][A-Z0-9\-./]{2,}", s) if re.search(r"\d", m) and re.search(r"[A-Z]", m)}


def similarity(ours: str, theirs: str) -> float:
    a, b = _tokens(ours), _tokens(theirs)
    if not a or not b:
        return 0.0
    j = len(a & b) / len(a | b)
    ca, cb = _model_codes(ours), _model_codes(theirs)
    if ca and cb:
        if ca & cb:
            j = max(j, 0.75) + 0.15
        else:
            j *= 0.5          # کد مدل متفاوت ← احتمالاً کالای دیگری است
    return min(1.0, j)


def _shops(text: str) -> tuple[int, bool]:
    t = (text or "").translate(FA_DIGITS)
    m = re.search(r"(\d+)\s*فروشگاه", t)
    if m:
        return int(m.group(1)), False
    return 1, OWN_SHOP in t


def search(q: str, timeout: float = 20) -> list[dict]:
    r = httpx.get(API, timeout=timeout, params={"page": 0, "sort": "popularity", "size": 12, "q": q,
                                                "source": "next_desktop"},
                  headers={"User-Agent": "Mozilla/5.0 (compatible; content-engine/1.0)", "Accept": "application/json"})
    r.raise_for_status()
    return r.json().get("results") or []


def compare(name: str, our_price: int | None, results: list[dict]) -> dict:
    matches = []
    for x in results:
        sim = similarity(name, x.get("name1") or "")
        if sim < 0.55 or not x.get("price"):
            continue
        n, is_us = _shops(x.get("shop_text"))
        matches.append({"sim": sim, "price": int(x["price"]), "shops": n, "us": is_us,
                        "name": x.get("name1"), "url": "https://torob.com" + (x.get("web_client_absolute_url") or "")})
    others = [m for m in matches if not m["us"]]
    if not matches:
        return {"status": "no_match"}
    if not others:
        return {"status": "only_us", "shops": 1, "match_name": matches[0]["name"], "match_url": matches[0]["url"]}
    best = min(others, key=lambda m: m["price"])
    shops = max(m["shops"] for m in others)
    out = {"market_min": best["price"], "shops": shops, "match_name": best["name"], "match_url": best["url"]}
    if our_price:
        ratio = our_price / best["price"]
        out["ratio"] = round(ratio, 3)
        out["status"] = "cheaper" if ratio <= 0.97 else ("equal" if ratio <= 1.03 else "pricier")
    else:
        out["status"] = "no_price"
    return out


def check_products(product_ids: list[int], max_checks: int = 60, max_age_days: int = 3,
                   delay: float = 3.0, deadline: float | None = None) -> dict:
    from app.db.marketing import Product
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    stats = {"checked": 0, "cheaper": 0, "pricier": 0, "errors": 0}
    with get_session() as db:
        fresh = {pc.product_id for pc in db.query(PriceCheck).filter(PriceCheck.product_id.in_(product_ids)).all()
                 if pc.checked_at and (pc.checked_at if pc.checked_at.tzinfo else pc.checked_at.replace(tzinfo=timezone.utc)) > cutoff}
        todo = [db.get(Product, i) for i in product_ids if i not in fresh]
        todo = [p for p in todo if p and p.price][:max_checks]
    for p in todo:
        if deadline and time.monotonic() > deadline:
            break
        try:
            res = compare(p.name, p.price, search(p.name))
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            res = {"status": "error", "match_name": str(e)[:200]}
            if stats["errors"] >= 3 and stats["checked"] == 0:
                log.warning("ترب در دسترس نیست: %s", e)
                set_setting("pricing.last_error", str(e)[:300])
                break
        with get_session() as db:
            pc = db.get(PriceCheck, p.id) or PriceCheck(product_id=p.id)
            pc.checked_at, pc.our_price = datetime.now(timezone.utc), p.price
            pc.market_min, pc.shops, pc.ratio = res.get("market_min"), res.get("shops"), res.get("ratio")
            pc.status, pc.match_name, pc.match_url = res["status"], res.get("match_name"), res.get("match_url")
            db.merge(pc)
            db.commit()
        stats["checked"] += 1
        if res["status"] in ("cheaper", "pricier"):
            stats[res["status"]] += 1
        time.sleep(delay)
    if stats["checked"]:
        set_setting("pricing.last_run_at", datetime.now(timezone.utc).isoformat())
    return stats


def candidates(limit_recs: int = 12) -> list[int]:
    """کالاهای نمونه‌ی پیشنهادهای برنامه و کمپین‌های فعال."""
    from app.db.marketing import Campaign, PlanCategory
    from app.plan.planner import recommendations
    ids: list[int] = []
    rec_ids = [r["plan_id"] for r in recommendations(limit=limit_recs)["items"]]
    with get_session() as db:
        for pid in rec_ids:
            p = db.get(PlanCategory, pid)
            ids += [e["id"] for e in (p.examples or []) if e.get("id")] if p else []
        for c in db.query(Campaign).filter(Campaign.status == "active").all():
            ids += list(c.product_ids or [])
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def checks_for(product_ids: list[int]) -> dict[int, dict]:
    with get_session() as db:
        rows = db.query(PriceCheck).filter(PriceCheck.product_id.in_(product_ids)).all()
        return {r.product_id: {"status": r.status, "market_min": r.market_min, "shops": r.shops, "ratio": r.ratio,
                               "match_name": r.match_name, "match_url": r.match_url} for r in rows}


def plan_price_weight(examples: list[dict]) -> float:
    """رقابت‌پذیری قیمت دسته (میانه‌ی نسبت قیمت ما به بازار در کالاهای نمونه)."""
    ids = [e["id"] for e in examples or [] if e.get("id")]
    if not ids:
        return 1.0
    ch = checks_for(ids)
    ratios = sorted(c["ratio"] for c in ch.values() if c.get("ratio"))
    only = sum(1 for c in ch.values() if c["status"] == "only_us")
    if not ratios:
        return 1.1 if only else 1.0           # فقط ما می‌فروشیم ← رقیب کم
    med = ratios[len(ratios) // 2]
    if med <= 0.97:
        return 1.2
    if med <= 1.03:
        return 1.0
    if med <= 1.10:
        return 0.85
    return 0.7
