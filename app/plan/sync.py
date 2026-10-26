"""
همگام‌سازی روزانه (تکه‌تکه و قابل ادامه):

1. دسته‌های ووکامرس (هر ۳ روز یک‌بار، صفحه‌به‌صفحه)
2. بار اول: همه‌ی محصولات به ترتیب شناسه (چند اجرا طول می‌کشد)
   بعد از آن: هر روز محصولات جدید + به‌روزرسانی چرخشی قیمت و موجودی
3. نگاشت دسته‌های جدید به نقشه (LLM سبک)
4. نسبت دادن محصولات و آمار زنده‌ی نقشه
5. پیشنهاد دسته‌ی جدید برای کالاهایی که در نقشه نیستند
6. سفارش‌های ووکامرس و کانال‌های قدیمی (اگر تنظیم شده باشد)

هر اجرا یک سقف زمانی دارد (پیش‌فرض ۱۲ دقیقه) و جایی که ماند را در settings نگه می‌دارد.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.db.marketing import Product, SyncRun, WcCategory
from app.db.session import get_session, get_setting, set_setting
from app.plan import mapping
from app.plan.data import seed_plan
from app.woo.store import StoreClient, parse_category, parse_product

log = logging.getLogger("plan.sync")
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _older_than(key: str, hours: float) -> bool:
    v = get_setting(key)
    if not v:
        return True
    try:
        return _now() - datetime.fromisoformat(v) > timedelta(hours=hours)
    except ValueError:
        return True


def is_running() -> bool:
    return _lock.locked()


# ---------------- مراحل ----------------
def sync_categories(client: StoreClient, deadline: float) -> dict:
    page = int(get_setting("sync.cat_page") or 1)
    n = 0
    while time.monotonic() < deadline:
        rows, _, pages = client.categories(page)
        log.info("دسته‌ها: صفحه‌ی %d از %s (%d ردیف)", page, pages, len(rows))
        with get_session() as db:
            for raw in rows:
                c = parse_category(raw)
                obj = db.get(WcCategory, c["id"])
                if obj is None:
                    db.add(WcCategory(**c))
                else:
                    for k, v in c.items():
                        setattr(obj, k, v)
            db.commit()
        n += len(rows)
        if not rows or page >= (pages or page):
            set_setting("sync.cat_page", 1)
            set_setting("sync.cat_done_at", _now().isoformat())
            mapping.rebuild_paths()
            return {"categories": n, "complete": True}
        page += 1
        set_setting("sync.cat_page", page)
    return {"categories": n, "complete": False, "next_page": page}


def _upsert_products(rows: list[dict]) -> tuple[int, int]:
    new = upd = 0
    with get_session() as db:
        for raw in rows:
            try:
                p = parse_product(raw)
            except (KeyError, ValueError, TypeError):
                continue
            obj = db.get(Product, p["id"])
            if obj is None:
                db.add(Product(**p))
                new += 1
            else:
                for k, v in p.items():
                    setattr(obj, k, v)
                upd += 1
        db.commit()
    return new, upd


def sync_products_full_pass(client: StoreClient, deadline: float, max_pages: int | None = None) -> dict:
    """گذر چرخشی روی همه‌ی محصولات (به ترتیب شناسه). بار اول = بارگذاری کامل."""
    page = int(get_setting("sync.full_page") or 1)
    new = upd = pages_done = 0
    while time.monotonic() < deadline and (max_pages is None or pages_done < max_pages):
        rows, total, pages = client.products(page, orderby="id", order="asc")
        log.info("محصولات: صفحه‌ی %d از %s", page, pages)
        a, b = _upsert_products(rows)
        new, upd, pages_done = new + a, upd + b, pages_done + 1
        if not rows or page >= (pages or page):
            set_setting("sync.full_page", 1)
            set_setting("sync.full_done_at", _now().isoformat())
            if not get_setting("sync.initial_done_at"):
                set_setting("sync.initial_done_at", _now().isoformat())
            set_setting("sync.site_total", total)
            return {"new": new, "updated": upd, "pages": pages_done, "complete": True}
        page += 1
        set_setting("sync.full_page", page)
        set_setting("sync.site_total", total)
    return {"new": new, "updated": upd, "pages": pages_done, "complete": False, "next_page": page}


def sync_new_products(client: StoreClient, deadline: float, max_pages: int = 10) -> dict:
    """محصولات تازه (ترتیب تاریخ نزولی) تا جایی که به صفحه‌ای کاملاً تکراری برسیم."""
    new = 0
    for page in range(1, max_pages + 1):
        if time.monotonic() > deadline:
            break
        rows, total, _ = client.products(page, orderby="date", order="desc")
        if not rows:
            break
        with get_session() as db:
            ids = [int(r["id"]) for r in rows if r.get("id")]
            known = {i for (i,) in db.query(Product.id).filter(Product.id.in_(ids)).all()}
        a, _ = _upsert_products(rows)
        new += a
        set_setting("sync.site_total", total)
        if len(known) == len(ids):
            break
    return {"new": new}


# ---------------- اجرای روزانه ----------------
def run_daily(budget_sec: int = 720, mock_llm: bool = False, force: bool = False) -> dict:
    if not _lock.acquire(blocking=False):
        return {"skipped": "already running"}
    deadline = time.monotonic() + budget_sec
    run_id = None
    stats: dict = {}
    log.info("همگام‌سازی روزانه شروع شد (سقف %d ثانیه)", budget_sec)
    try:
        with get_session() as db:
            r = SyncRun(kind="daily")
            db.add(r)
            db.commit()
            run_id = r.id
        seed_plan()
        client = StoreClient()

        if force or _older_than("sync.cat_done_at", 72) or int(get_setting("sync.cat_page") or 1) > 1:
            stats["categories"] = sync_categories(client, deadline)

        if not get_setting("sync.initial_done_at"):
            stats["products"] = sync_products_full_pass(client, deadline)
        else:
            stats["new_products"] = sync_new_products(client, deadline)
            # به‌روزرسانی چرخشی قیمت/موجودی: حدود ۴۰ صفحه در هر اجرا
            stats["refresh"] = sync_products_full_pass(client, deadline, max_pages=40)

        if time.monotonic() < deadline:
            stats["mapping"] = mapping.map_unmapped_categories(deadline=deadline - 60, mock=mock_llm)
        stats["assign"] = mapping.assign_products_and_stats()
        if get_setting("sync.initial_done_at") and time.monotonic() < deadline - 120:
            stats["proposals"] = mapping.propose_new_plans(mock=mock_llm)

        if time.monotonic() < deadline - 60:
            try:
                from app.tracking.orders import sync_orders
                stats["orders"] = sync_orders()
            except Exception as e:  # noqa: BLE001
                stats["orders"] = {"error": str(e)[:200]}
        if time.monotonic() < deadline - 60:
            try:
                from app.channels.telegram import recheck_stale
                stats["channels"] = recheck_stale(limit=10)
            except Exception as e:  # noqa: BLE001
                stats["channels"] = {"error": str(e)[:200]}

        set_setting("sync.last_daily_at", _now().isoformat())
        _finish(run_id, True, stats)
        log.info("همگام‌سازی روزانه تمام شد: %s", stats)
        return stats
    except Exception as e:  # noqa: BLE001
        log.exception("همگام‌سازی روزانه شکست خورد")
        _finish(run_id, False, stats, str(e))
        return {"error": str(e), **stats}
    finally:
        _lock.release()


def _finish(run_id, ok: bool, stats: dict, error: str | None = None) -> None:
    if run_id is None:
        return
    with get_session() as db:
        r = db.get(SyncRun, run_id)
        r.finished_at, r.ok, r.stats, r.error = _now(), ok, stats, error
        db.commit()


def due() -> bool:
    """آیا وقت اجرای بعدی است؟ (بار اول تا تمام شدن، هر بار؛ بعد روزی یک‌بار)."""
    if not get_setting("sync.initial_done_at") or int(get_setting("sync.cat_page") or 1) > 1:
        return True
    return _older_than("sync.last_daily_at", 20)


def status() -> dict:
    with get_session() as db:
        runs = db.query(SyncRun).order_by(SyncRun.id.desc()).limit(8).all()
        products = db.query(Product).count()
        in_stock = db.query(Product).filter(Product.in_stock.is_(True)).count()
        cats = db.query(WcCategory).count()
    return {
        "running": is_running(),
        "products": products, "in_stock": in_stock, "categories": cats,
        "site_total": get_setting("sync.site_total"),
        "initial_done_at": get_setting("sync.initial_done_at"),
        "last_daily_at": get_setting("sync.last_daily_at"),
        "full_page": get_setting("sync.full_page"),
        "cat_page": get_setting("sync.cat_page"),
        "category_map": mapping.category_counts(),
        "runs": [{"id": r.id, "kind": r.kind, "started_at": r.started_at.isoformat() if r.started_at else None,
                  "finished_at": r.finished_at.isoformat() if r.finished_at else None, "ok": r.ok,
                  "stats": r.stats, "error": r.error} for r in runs],
    }
