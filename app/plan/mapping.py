"""
نقشه‌ی پویا:
- نگاشت هر دسته‌ی ووکامرس به یک دسته‌ی نقشه (با LLM سبک، فقط برای دسته‌های جدید)،
- نسبت دادن هر محصول به دسته‌ی نقشه،
- به‌روز کردن آمار زنده‌ی هر دسته‌ی نقشه،
- پیشنهاد دسته‌ی جدید نقشه برای گروه‌های محصولی که در نقشه نیستند.
"""
import hashlib
import json
import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.brain.llm import get_llm
from app.config import get_settings
from app.db.marketing import PlanCategory, Product, WcCategory
from app.db.session import get_session, get_setting
from app.plan.data import groups, province_names

log = logging.getLogger("plan.mapping")
NONE = "NONE"


# ---------------- مسیر دسته‌ها ----------------
def rebuild_paths() -> None:
    with get_session() as db:
        cats = {c.id: c for c in db.query(WcCategory).all()}
        for c in cats.values():
            parts, cur, seen = [], c, set()
            while cur and cur.id not in seen:
                seen.add(cur.id)
                parts.append(cur.name)
                cur = cats.get(cur.parent)
            c.path = " › ".join(reversed(parts))
        db.commit()


# ---------------- نگاشت دسته‌ها با LLM ----------------
class _MapItem(BaseModel):
    id: int
    plan: str


class _MapResult(BaseModel):
    items: list[_MapItem] = Field(default_factory=list)


def _plan_catalog(db) -> list[PlanCategory]:
    return db.query(PlanCategory).filter(PlanCategory.status.in_(["base", "approved", "proposed"])).all()


def map_unmapped_categories(max_batches: int = 10, batch_size: int = 60, deadline: float | None = None,
                            mock: bool = False) -> dict:
    """دسته‌های ووکامرسی که هنوز نگاشت ندارند را به دسته‌های نقشه نسبت می‌دهد."""
    import time
    stats = {"batches": 0, "mapped": 0, "none": 0, "cost_usd": 0.0}
    with get_session() as db:
        plans = _plan_catalog(db)
        plan_ids = {p.id for p in plans}
        catalog = "\n".join(f"{p.id}: {p.name} [{groups().get(p.group, p.group)}]" for p in plans)
        todo = (db.query(WcCategory).filter(WcCategory.plan_id.is_(None), WcCategory.count > 0)
                .order_by(WcCategory.count.desc()).limit(max_batches * batch_size).all())
        if not todo:
            return stats
        llm = None if mock else get_llm(get_settings().llm_fast_model)
        system = ("تو متخصص دسته‌بندی کالاهای صنعتی و کشاورزی هستی. هر دسته‌ی فروشگاه را به نزدیک‌ترین "
                  "دسته‌ی نقشه‌ی تبلیغات نسبت بده. اگر هیچ دسته‌ای واقعاً مناسب نیست، NONE بگذار. "
                  "فقط JSON برگردان.")
        for i in range(0, len(todo), batch_size):
            if deadline and time.monotonic() > deadline:
                break
            batch = todo[i:i + batch_size]
            lines = "\n".join(f"{c.id} | {c.path or c.name} ({c.count} کالا)" for c in batch)
            user = (f"دسته‌های نقشه:\n{catalog}\n\nدسته‌های فروشگاه (شناسه | مسیر):\n{lines}\n\n"
                    'خروجی: {"items": [{"id": <شناسه>, "plan": "<شناسه‌ی دسته‌ی نقشه یا NONE>"}]} '
                    "برای همه‌ی دسته‌ها.")
            try:
                if mock:
                    res_items = [_MapItem(id=c.id, plan=_heuristic_plan(c, plans)) for c in batch]
                else:
                    r = llm.generate_json(system, user, _MapResult)
                    res_items = r.data.items
                    stats["cost_usd"] += r.cost_usd or 0
            except Exception as e:  # noqa: BLE001
                log.warning("نگاشت دسته‌ها ناموفق بود: %s", e)
                break
            got = {it.id: it.plan.strip() for it in res_items}
            for c in batch:
                p = got.get(c.id, NONE)
                c.plan_id = p if p in plan_ids else NONE
                c.map_source = "llm"
                stats["mapped" if c.plan_id != NONE else "none"] += 1
            db.commit()
            stats["batches"] += 1
    log.info("نگاشت دسته‌ها: %s", stats)
    return stats


_STOP = {"و", "از", "با", "در", "برای", "انواع", "لوازم", "تجهیزات", "قطعات", "سایر"}


def _tokens(s: str) -> set[str]:
    import re
    return {t for t in re.split(r"[\s،,()؛:/\-–—|›]+", s or "") if len(t) >= 3 and t not in _STOP}


def _heuristic_plan(c: WcCategory, plans: list[PlanCategory]) -> str:
    """نگاشت ساده با هم‌پوشانی کلمات (برای تست بدون LLM)."""
    ct = _tokens(c.name)
    best, score = NONE, 0
    for p in plans:
        o = len(ct & _tokens(p.name))
        if o > score:
            best, score = p.id, o
    return best


# ---------------- محصولات و آمار ----------------
def assign_products_and_stats() -> dict:
    """هر محصول را به دسته‌ی نقشه‌ی مربوط نسبت می‌دهد و آمار هر دسته را از نو حساب می‌کند."""
    initial_done_at = get_setting("sync.initial_done_at")
    new_since = datetime.now(timezone.utc) - timedelta(days=7)
    if initial_done_at:
        # محصولاتی که در بارگذاری اولیه آمده‌اند «جدید» حساب نشوند
        new_since = max(new_since, datetime.fromisoformat(initial_done_at))
    with get_session() as db:
        cats = {c.id: c for c in db.query(WcCategory).all()}
        per_plan: dict[str, list[Product]] = defaultdict(list)
        changed = 0
        for p in db.query(Product).yield_per(2000):
            best, depth = None, -1
            for cid in p.category_ids or []:
                c = cats.get(cid)
                if c and c.plan_id and c.plan_id != NONE:
                    d = (c.path or "").count("›")
                    if d > depth:
                        best, depth = c.plan_id, d
            if p.plan_id != best:
                p.plan_id = best
                changed += 1
            if best:
                per_plan[best].append(p)
        db.commit()

        if not any(c.plan_id and c.plan_id != NONE for c in cats.values()):
            return {"products_changed": changed, "plans": 0, "note": "هنوز نگاشتی انجام نشده؛ آمار فایل نقشه حفظ شد"}
        for plan in db.query(PlanCategory).all():
            items = per_plan.get(plan.id, [])
            prices = sorted(p.price for p in items if p.price)
            in_stock = [p for p in items if p.in_stock]
            plan.product_count = len(items)
            plan.in_stock_count = len(in_stock)
            plan.median_price = int(statistics.median(prices)) if prices else None
            plan.p90_price = prices[int(len(prices) * 0.9) - 1] if len(prices) >= 10 else (prices[-1] if prices else None)
            plan.new_7d = sum(1 for p in items if initial_done_at and _aware(p.first_seen_at) > new_since)
            showcase = sorted((p for p in in_stock if p.image and p.price), key=lambda p: -(p.price or 0))[:8]
            plan.examples = [{"id": p.id, "name": p.name, "price": p.price, "image": p.image,
                              "url": p.permalink} for p in showcase]
        db.commit()
    return {"products_changed": changed, "plans": len(per_plan)}


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------- پیشنهاد دسته‌ی جدید ----------------
class _Segment(BaseModel):
    name: str
    provinces: list[str]
    cities: str = ""
    peak: list[int]
    good: list[int] = Field(default_factory=list)
    note: str = ""


class _Proposal(BaseModel):
    name: str = Field(..., max_length=200)
    group: str
    buyers: str
    angle: str
    time: str
    channels: str
    segments: list[_Segment] = Field(..., min_length=1, max_length=4)


def propose_new_plans(max_new: int = 6, min_products: int = 10, mock: bool = False,
                      deadline: float | None = None) -> list[str]:
    """
    دسته‌هایی از سایت که در نقشه نیستند (NONE) را بر اساس ریشه گروه می‌کند و برای
    بزرگ‌ترین گروه‌ها یک ردیف پیشنهادی نقشه می‌سازد (نیاز به تأیید شما در پنل).
    """
    created = []
    with get_session() as db:
        none = db.query(WcCategory).filter(WcCategory.plan_id == NONE, WcCategory.count > 0).all()
        by_root: dict[str, list[WcCategory]] = defaultdict(list)
        for c in none:
            by_root[(c.path or c.name).split(" › ")[0]].append(c)
        ranked = sorted(by_root.items(), key=lambda kv: -sum(c.count for c in kv[1]))
        existing = {p.name for p in db.query(PlanCategory).all()}
        import time as _t
        for root, cs in ranked:
            if len(created) >= max_new or (deadline and _t.monotonic() > deadline):
                break
            total = sum(c.count for c in cs)
            if total < min_products or root in existing:
                continue
            sub = ", ".join(c.name for c in sorted(cs, key=lambda c: -c.count)[:15])
            prov = "، ".join(province_names())
            grp = ", ".join(f"{k}={v}" for k, v in groups().items())
            if mock:
                data = _Proposal(name=root, group="IND", buyers="—", angle="—", time="—", channels="—",
                                 segments=[_Segment(name="سراسری", provinces=["تهران"], peak=[1], good=[])])
            else:
                user = (f"گروه محصولی «{root}» با {total} کالا در فروشگاه کاروان صنعت هست که در نقشه‌ی تبلیغات نیست.\n"
                        f"زیردسته‌ها: {sub}\n\n"
                        "مثل یک کارشناس بازاریابی B2B ایران، برای تبلیغ کانالی (تلگرام، ایتا، بله) این گروه "
                        "یک ردیف نقشه بساز: خریداران واقعی، زاویه‌ی پیام، بهترین ساعت انتشار، نوع کانال‌ها، و ۱ تا ۴ "
                        "بخش جغرافیایی با استان‌ها، شهرها، ماه‌های اوج (peak) و ماه‌های خوب (good). ماه‌ها شماره‌ی "
                        f"ماه شمسی‌اند (۱=فروردین ... ۱۲=اسفند). استان‌ها فقط از این لیست: {prov}\n"
                        f"group یکی از: {grp}\n"
                        "اگر تقاضا فصلی نیست، peak را ماه‌هایی بگذار که بودجه‌ی خرید سازمان‌ها و کارخانه‌ها باز است. "
                        "فقط JSON مطابق اسکیما:\n" + json.dumps(_Proposal.model_json_schema(), ensure_ascii=False))
                try:
                    r = get_llm().generate_json("تو کارشناس بازاریابی صنعتی و کشاورزی ایران هستی. فقط JSON بده.",
                                                user, _Proposal)
                    data = r.data
                except Exception as e:  # noqa: BLE001
                    log.warning("پیشنهاد دسته برای %s ناموفق: %s", root, e)
                    continue
            pid = "NEW_" + hashlib.md5(root.encode("utf-8")).hexdigest()[:8].upper()
            segs = []
            for s in data.segments:
                d = s.model_dump()
                d["provinces"] = [p for p in d["provinces"] if p in province_names()] or ["تهران"]
                d["peak"] = [m for m in d["peak"] if 1 <= m <= 12]
                d["good"] = [m for m in d["good"] if 1 <= m <= 12]
                d["conf"] = "پیشنهاد هوش مصنوعی"
                segs.append(d)
            db.add(PlanCategory(id=pid, name=data.name or root, group=data.group if data.group in groups() else "IND",
                                buyers=data.buyers, angle=data.angle, time=data.time, channels=data.channels,
                                segments=segs, status="proposed", source="llm"))
            for c in cs:
                c.plan_id, c.map_source = pid, "proposal"
            db.commit()
            created.append(pid)
            log.info("دسته‌ی پیشنهادی جدید: %s (%d کالا)", root, total)
    return created


def category_counts() -> dict:
    with get_session() as db:
        rows = db.query(WcCategory.plan_id, WcCategory.count).all()
    c = Counter()
    for plan_id, cnt in rows:
        key = "unmapped" if plan_id is None else ("none" if plan_id == NONE else "mapped")
        c[key] += 1
    return dict(c)
