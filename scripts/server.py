"""
سرور پنل و API (Web Service روی Render). فقط کتابخانه‌ی استاندارد + SQLAlchemy.

عمومی:   GET /  (پنل)   GET /health   GET /r/<کد>  (لینک کوتاه تبلیغ)
محافظت‌شده با هدر X-Run-Token: همه‌ی /api/...  (فهرست کامل در ROUTES پایین فایل)
"""
import hmac
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, select

from app.brain.generate import create_job, run_job
from app.channels.telegram import check_channel, parse_channel_ref, score_channel
from app.config import get_settings
from app.db.marketing import Campaign, Channel, Click, PlanCategory, Placement, Product, WcCategory
from app.db.models import ContentType, Job, JobStatus
from app.db.session import get_engine, get_session, get_setting, init_db, set_setting
from app.logging_setup import setup_logging
from app.plan import campaigns as camps
from app.plan import cities, mapping, planner, sync, weather
from app.plan.data import groups, month_name, province_names, seed_plan, today_jalali
from app.tracking.links import short_url, visitor_hash
from app import assistant, bots

setup_logging()
log = logging.getLogger("server")

RUN_TOKEN = os.environ.get("RUN_TOKEN", "")
WEB_DIR = Path(__file__).resolve().parent.parent / "app" / "web"
MAX_WORKERS = int(os.environ.get("WORKERS", "2"))
MOCK = os.environ.get("LLM_MOCK") == "1"            # فقط برای تست محلی
NO_SCHEDULER = os.environ.get("NO_SCHEDULER") == "1"
TYPES = [t.value for t in ContentType]
_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="gen")
_sync_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync")


class HttpError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code, self.msg = code, msg


class Handled(Exception):
    """پاسخ قبلاً فرستاده شده."""


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _row(obj, *fields) -> dict:
    out = {}
    for f in fields:
        v = getattr(obj, f)
        out[f] = _iso(v) if isinstance(v, datetime) else v
    return out


def _bg(fn, *args, pool=None):
    def task():
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("کار پس‌زمینه شکست خورد: %s", getattr(fn, "__name__", fn))
    (pool or _pool).submit(task)


def _int(v, name: str, lo: int | None = None, hi: int | None = None, required=False):
    if v in (None, ""):
        if required:
            raise HttpError(400, f"{name} لازم است")
        return None
    try:
        n = int(str(v).replace(",", "").replace("٬", ""))
    except ValueError:
        raise HttpError(400, f"{name} باید عدد باشد") from None
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise HttpError(400, f"{name} خارج از محدوده است")
    return n


# =============== محتوا (استودیو) ===============
def _job_summary(j: Job) -> dict:
    out = j.output or {}
    return {"id": j.id, "topic": j.topic, "type": j.content_type.value, "status": j.status.value,
            "title": out.get("title") if isinstance(out, dict) else None, "model": j.llm_model,
            "cost_usd": j.cost_usd, "latency_sec": j.latency_sec, "attempts": j.attempts,
            "error": j.error, "created_at": _iso(j.created_at)}


def api_me(req, m):
    s = get_settings()
    return {"ok": True, "default_model": s.llm_model, "brand": s.brand_name, "types": TYPES,
            "jalali": today_jalali().strftime("%Y/%m/%d"), "groups": groups(), "provinces": province_names(),
            "orders_enabled": bool(s.wc_consumer_key), "short_links": bool(short_url("X"))}


def api_stats(req, m):
    with get_session() as db:
        rows = db.execute(select(Job.status, func.count(), func.coalesce(func.sum(Job.cost_usd), 0.0),
                                 func.avg(Job.latency_sec)).group_by(Job.status)).all()
        ad_cost = db.scalar(select(func.coalesce(func.sum(Placement.ad_cost_usd), 0.0))) or 0
    st = {"total": 0, "cost_usd": float(ad_cost), "by_status": {}, "avg_latency_sec": None}
    for s, cnt, cost, avg in rows:
        st["by_status"][s.value] = cnt
        st["total"] += cnt
        st["cost_usd"] += float(cost or 0)
        if avg is not None and s == JobStatus.generated:
            st["avg_latency_sec"] = round(float(avg), 1)
    return st


def api_jobs(req, m):
    limit = _int(req.q("limit", "50"), "limit", 1, 200)
    with get_session() as db:
        return [_job_summary(j) for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))]


def api_job(req, m):
    with get_session() as db:
        j = db.get(Job, m.group(1))
        if not j:
            raise HttpError(404, "پیدا نشد")
        d = _job_summary(j)
        d.update({"output": j.output, "prompt_tokens": j.prompt_tokens, "completion_tokens": j.completion_tokens})
        return d


def api_generate(req, m):
    b = req.body()
    topic = (b.get("topic") or "").strip()
    types = [t for t in (b.get("types") or []) if t in TYPES]
    model = (b.get("model") or "").strip() or None
    if not 3 <= len(topic) <= 300:
        raise HttpError(400, "موضوع باید بین ۳ تا ۳۰۰ کاراکتر باشد")
    if not types:
        raise HttpError(400, "حداقل یک نوع محتوا انتخاب کنید")
    if model and not re.fullmatch(r"[~\w./:-]{3,120}", model):
        raise HttpError(400, "اسم مدل نامعتبر است")
    ids = []
    for t in types:
        jid = create_job(topic, t, model=model or get_settings().llm_model)
        _bg(run_job, jid, None, MOCK)
        ids.append(jid)
    return 202, {"job_ids": ids}


def api_job_retry(req, m):
    with get_session() as db:
        j = db.get(Job, m.group(1))
        if not j:
            raise HttpError(404, "پیدا نشد")
        if j.status in (JobStatus.pending, JobStatus.generating):
            raise HttpError(409, "در حال اجراست")
        j.status, j.error, j.output = JobStatus.pending, None, None
        db.commit()
    _bg(run_job, m.group(1), None, MOCK)
    return 202, {"job_id": m.group(1)}


# =============== همگام‌سازی و نقشه ===============
def api_sync_status(req, m):
    return sync.status()


def api_sync_run(req, m):
    if sync.is_running():
        raise HttpError(409, "همگام‌سازی در حال اجراست")
    force = bool(req.body().get("force"))
    _bg(sync.run_daily, 720, MOCK, force, pool=_sync_pool)
    return 202, {"started": True}


def api_cron(req, m):
    """برای زمان‌بند بیرونی (GitHub Actions): سرویس را بیدار می‌کند و اگر وقتش باشد همگام‌سازی را شروع می‌کند."""
    started = False
    if sync.due() and not sync.is_running():
        _bg(sync.run_daily, 720, MOCK, False, pool=_sync_pool)
        started = True
    return {"started": started, "running": sync.is_running()}


def _plan_row(p: PlanCategory) -> dict:
    d = _row(p, "id", "name", "group", "buyers", "angle", "time", "channels", "segments", "status", "source",
             "product_count", "in_stock_count", "new_7d", "median_price", "p90_price", "examples", "updated_at")
    d["group_name"] = groups().get(p.group, p.group)
    return d


def api_plan(req, m):
    with get_session() as db:
        rows = db.query(PlanCategory).order_by(PlanCategory.status.desc(), PlanCategory.product_count.desc()).all()
        return {"items": [_plan_row(p) for p in rows], "category_map": mapping.category_counts(),
                "month": today_jalali().month, "month_name": month_name(today_jalali().month)}


def api_plan_update(req, m):
    b = req.body()
    st = b.get("status")
    if st not in ("approved", "rejected", "base"):
        raise HttpError(400, "وضعیت نامعتبر")
    with get_session() as db:
        p = db.get(PlanCategory, m.group(1))
        if not p:
            raise HttpError(404, "پیدا نشد")
        p.status = st
        if st == "rejected":
            for c in db.query(WcCategory).filter(WcCategory.plan_id == p.id).all():
                c.plan_id, c.map_source = mapping.NONE, "rejected"
        db.commit()
    _bg(mapping.assign_products_and_stats, pool=_sync_pool)
    return {"ok": True}


def _run_proposals():
    created = mapping.propose_new_plans(max_new=8, min_products=5, mock=MOCK)
    log.info("ردیف‌های پیشنهادی ساخته شد: %s", created)
    mapping.assign_products_and_stats()


def api_plan_propose(req, m):
    if sync.is_running():
        raise HttpError(409, "همگام‌سازی در حال اجراست؛ چند دقیقه بعد امتحان کنید")
    _bg(_run_proposals, pool=_sync_pool)
    return 202, {"started": True}


def api_unmapped(req, m):
    with get_session() as db:
        rows = (db.query(WcCategory).filter(WcCategory.plan_id == mapping.NONE, WcCategory.count > 0)
                .order_by(WcCategory.count.desc()).limit(200).all())
        return [_row(c, "id", "name", "path", "count", "permalink") for c in rows]


def api_category_map(req, m):
    b = req.body()
    with get_session() as db:
        c = db.get(WcCategory, _int(m.group(1), "id"))
        if not c:
            raise HttpError(404, "پیدا نشد")
        pid = b.get("plan_id") or mapping.NONE
        if pid != mapping.NONE and not db.get(PlanCategory, pid):
            raise HttpError(400, "دسته‌ی نقشه نامعتبر")
        c.plan_id, c.map_source = pid, "manual"
        db.commit()
    _bg(mapping.assign_products_and_stats, pool=_sync_pool)
    return {"ok": True}


def api_products(req, m):
    q = (req.q("q") or "").strip()
    plan = req.q("plan")
    limit = _int(req.q("limit", "50"), "limit", 1, 200)
    with get_session() as db:
        qry = db.query(Product)
        if plan:
            qry = qry.filter(Product.plan_id == plan)
        if q:
            qry = qry.filter(Product.name.ilike(f"%{q}%"))
        if req.q("new") == "1":
            qry = qry.order_by(Product.first_seen_at.desc())
        else:
            qry = qry.order_by(Product.price.desc().nullslast())
        return [_row(p, "id", "name", "price", "in_stock", "image", "permalink", "brand", "plan_id", "first_seen_at")
                for p in qry.limit(limit).all()]


def _with_prices(items: list[dict], key: str = "examples") -> list[dict]:
    from app.plan.pricing import checks_for
    ids = [e["id"] for it in items for e in (it.get(key) or []) if e.get("id")]
    ch = checks_for(ids) if ids else {}
    for it in items:
        for e in it.get(key) or []:
            if e.get("id") in ch:
                e["price_check"] = ch[e["id"]]
    return items


def api_recommendations(req, m):
    month = _int(req.q("month"), "month", 1, 12)
    r = planner.recommendations(limit=_int(req.q("limit", "12"), "limit", 1, 50), month=month)
    _with_prices(r["items"])
    return r


def api_pricing_run(req, m):
    from app.plan.pricing import candidates, check_products
    if sync.is_running():
        raise HttpError(409, "همگام‌سازی در حال اجراست")
    _bg(lambda: check_products(candidates(), max_checks=40), pool=_sync_pool)
    return 202, {"started": True}


def api_history(req, m):
    from app.tracking.history import summary
    return summary()


def api_history_run(req, m):
    from app.tracking.history import sync_history
    if sync.is_running():
        raise HttpError(409, "همگام‌سازی در حال اجراست")
    _bg(lambda: sync_history(max_pages=40), pool=_sync_pool)
    return 202, {"started": True}


def api_pricing_status(req, m):
    from app.plan.pricing import PriceCheck
    with get_session() as db:
        rows = dict(db.query(PriceCheck.status, func.count()).group_by(PriceCheck.status).all())
    return {"by_status": rows, "last_run_at": get_setting("pricing.last_run_at"),
            "last_error": get_setting("pricing.last_error")}


# =============== تنظیمات ===============
def api_settings(req, m):
    out = {k: planner.cfg(k) for k in planner.DEFAULTS}
    out["planner.weights"] = {**planner.DEFAULT_WEIGHTS, **(out["planner.weights"] or {})}
    out["groups"] = groups()
    out["orders_enabled"] = bool(get_settings().wc_consumer_key)
    return out


def api_settings_update(req, m):
    b = req.body()
    if "budget.monthly_toman" in b:
        set_setting("budget.monthly_toman", _int(b["budget.monthly_toman"], "بودجه", 0, 10**12))
    if "budget.avg_ad_price_toman" in b:
        set_setting("budget.avg_ad_price_toman", _int(b["budget.avg_ad_price_toman"], "قیمت تبلیغ", 0, 10**11))
    if "planner.pilot_size" in b:
        set_setting("planner.pilot_size", _int(b["planner.pilot_size"], "تعداد کمپین", 1, 10))
    if "planner.weights" in b:
        w = b["planner.weights"] or {}
        try:
            cur = {**planner.DEFAULT_WEIGHTS, **(get_setting("planner.weights") or {})}
            cur.update({k: max(0.0, min(3.0, float(v))) for k, v in w.items() if k in groups()})
            set_setting("planner.weights", cur)
        except (TypeError, ValueError):
            raise HttpError(400, "وزن‌ها نامعتبرند") from None
    return api_settings(req, m)


# =============== کانال‌ها ===============
CH_FIELDS = ("id", "platform", "username", "title", "description", "members", "avg_views", "view_rate",
             "posts_per_day", "ad_ratio", "last_post_days", "score", "flags", "provinces", "topics",
             "price_toman", "admin_contact", "notes", "status", "error", "checked_at", "created_at")


def _ch_row(c: Channel) -> dict:
    d = _row(c, *CH_FIELDS)
    _, _, d["cpm"] = score_channel(c.members, c.avg_views, c.posts_per_day, c.ad_ratio, c.last_post_days, c.price_toman)
    d["url"] = {"telegram": f"https://t.me/{c.username}", "eitaa": f"https://eitaa.com/{c.username}",
                "bale": f"https://ble.ir/{c.username}", "rubika": f"https://rubika.ir/{c.username}",
                "instagram": f"https://instagram.com/{c.username}"}.get(c.platform)
    return d


def api_channels(req, m):
    with get_session() as db:
        rows = db.query(Channel).order_by(Channel.score.desc().nullslast(), Channel.id.desc()).all()
        return [_ch_row(c) for c in rows]


def api_channels_bulk(req, m):
    b = req.body()
    text = b.get("text") or ""
    plat = b.get("platform") or "telegram"
    provs = [p for p in (b.get("provinces") or []) if p in province_names()]
    topics = (b.get("topics") or "").strip()[:300] or None
    added, skipped, bad = [], 0, []
    with get_session() as db:
        for line in re.split(r"[\n\s]+", text)[:300]:
            ref = parse_channel_ref(line, plat)
            if not ref:
                if line.strip():
                    bad.append(line.strip()[:60])
                continue
            p, u = ref
            if db.query(Channel).filter(Channel.platform == p, func.lower(Channel.username) == u.lower()).first():
                skipped += 1
                continue
            c = Channel(platform=p, username=u, provinces=provs, topics=topics)
            db.add(c)
            db.flush()
            added.append(c.id)
        db.commit()
    for cid in added:
        _bg(check_channel, cid)
    return {"added": len(added), "skipped": skipped, "invalid": bad[:20]}


def api_channel_update(req, m):
    b = req.body()
    with get_session() as db:
        c = db.get(Channel, _int(m.group(1), "id"))
        if not c:
            raise HttpError(404, "پیدا نشد")
        for k in ("members", "avg_views", "price_toman"):
            if k in b:
                setattr(c, k, _int(b[k], k, 0, 10**11))
        for k in ("admin_contact", "notes", "topics", "title"):
            if k in b:
                setattr(c, k, (b[k] or "").strip()[:500] or None)
        if "provinces" in b:
            c.provinces = [p for p in b["provinces"] or [] if p in province_names()]
        if "status" in b and b["status"] in ("new", "checked", "blocked"):
            c.status = b["status"]
        c.view_rate = round(c.avg_views / c.members, 3) if c.avg_views and c.members else None
        c.score, c.flags, _ = score_channel(c.members, c.avg_views, c.posts_per_day, c.ad_ratio,
                                            c.last_post_days, c.price_toman)
        db.commit()
        return _ch_row(c)


def api_channel_check(req, m):
    c = check_channel(_int(m.group(1), "id"))
    with get_session() as db:
        return _ch_row(db.get(Channel, c.id))


def api_channel_delete(req, m):
    with get_session() as db:
        c = db.get(Channel, _int(m.group(1), "id"))
        if not c:
            raise HttpError(404, "پیدا نشد")
        if db.query(Placement).filter(Placement.channel_id == c.id).first():
            c.status = "blocked"
        else:
            db.delete(c)
        db.commit()
    return {"ok": True}


# =============== کمپین و تبلیغ ===============
PL_FIELDS = ("id", "code", "campaign_id", "channel_id", "status", "cost_toman", "publish_at", "target_url", "ad",
             "ad_status", "ad_error", "ad_cost_usd", "ad_variant", "calls", "orders", "revenue_toman", "notes", "created_at")


def _camp_row(db, c: Campaign, metrics: dict | None = None) -> dict:
    d = _row(c, "id", "code", "plan_id", "segment", "title", "provinces", "cities", "product_ids", "target_url",
             "budget_toman", "status", "brief", "created_at")
    pls = db.query(Placement).filter(Placement.campaign_id == c.id).order_by(Placement.id).all()
    chans = {ch.id: ch for ch in db.query(Channel).filter(Channel.id.in_([p.channel_id for p in pls if p.channel_id]))}
    metrics = metrics or camps.placement_metrics(db)
    d["placements"] = []
    for p in pls:
        r = _row(p, *PL_FIELDS)
        r["short_url"] = short_url(p.code)
        r["metrics"] = metrics.get(p.id)
        ch = chans.get(p.channel_id)
        r["channel"] = {"id": ch.id, "title": ch.title or ch.username, "username": ch.username,
                        "platform": ch.platform, "members": ch.members, "avg_views": ch.avg_views,
                        "score": ch.score} if ch else None
        d["placements"].append(r)
    d["spent"] = sum((p.cost_toman or 0) for p in pls if p.status in ("paid", "published", "done"))
    from app.plan.pricing import checks_for
    pc = checks_for(c.product_ids or [])
    d["products"] = [{**_row(p, "id", "name", "price", "image", "permalink", "in_stock"), "price_check": pc.get(p.id)}
                     for p in db.query(Product).filter(Product.id.in_(c.product_ids or [])).all()]
    return d


def api_campaigns(req, m):
    with get_session() as db:
        metrics = camps.placement_metrics(db)
        return [_camp_row(db, c, metrics) for c in db.query(Campaign).order_by(Campaign.id.desc()).all()]


def api_campaign_create(req, m):
    b = req.body()
    city = (b.get("city") or "").strip()[:80] or None
    prov = (b.get("province") or "").strip()[:60] or None
    c = camps.create_campaign(b.get("plan_id") or "", _int(b.get("segment_index"), "segment_index", 0, required=True),
                              _int(b.get("budget_toman"), "بودجه", 0), city=city, province=prov)
    with get_session() as db:
        return 201, _camp_row(db, db.get(Campaign, c.id))


def api_campaign_update(req, m):
    b = req.body()
    with get_session() as db:
        c = db.get(Campaign, _int(m.group(1), "id"))
        if not c:
            raise HttpError(404, "پیدا نشد")
        if b.get("status") in ("draft", "active", "done"):
            c.status = b["status"]
        if "budget_toman" in b:
            c.budget_toman = _int(b["budget_toman"], "بودجه", 0)
        if "target_url" in b and (b["target_url"] or "").startswith(get_settings().brand_site_url):
            c.target_url = b["target_url"]
        if "product_ids" in b:
            c.product_ids = [int(x) for x in b["product_ids"] or [] if str(x).isdigit()][:8]
        db.commit()
        return _camp_row(db, c)


def api_placement_create(req, m):
    b = req.body()
    ids = b.get("channel_ids") or [b.get("channel_id")]
    out = []
    for cid in ids:
        pl = camps.create_placement(_int(b.get("campaign_id"), "campaign_id", required=True), _int(cid, "channel_id"))
        out.append(pl.id)
        if b.get("generate", True):
            _bg(camps.generate_ad, pl.id, MOCK)
    return 201, {"placement_ids": out}


def api_placement_update(req, m):
    b = req.body()
    with get_session() as db:
        p = db.get(Placement, _int(m.group(1), "id"))
        if not p:
            raise HttpError(404, "پیدا نشد")
        if b.get("status") in ("draft", "contacted", "agreed", "paid", "published", "done", "cancelled"):
            p.status = b["status"]
        if b.get("ad_variant") in ("full", "short", ""):
            p.ad_variant = b["ad_variant"] or None
        if "cost_toman" in b:
            p.cost_toman = _int(b["cost_toman"], "هزینه", 0, 10**12)
        for k in ("calls", "orders", "revenue_toman"):
            if k in b:
                setattr(p, k, _int(b[k], k, 0, 10**12) or 0)
        for k in ("publish_at", "notes"):
            if k in b:
                setattr(p, k, (b[k] or "").strip()[:500] or None)
        if "ad_text" in b and p.ad:
            ad = dict(p.ad)
            ad["full_text"] = (b["ad_text"] or "")[:4000]
            p.ad = ad
        db.commit()
        return _row(p, *PL_FIELDS)


def api_placement_ad(req, m):
    pid = _int(m.group(1), "id")
    with get_session() as db:
        p = db.get(Placement, pid)
        if not p:
            raise HttpError(404, "پیدا نشد")
        if p.ad_status == "generating":
            raise HttpError(409, "در حال ساخت است")
    _bg(camps.generate_ad, pid, MOCK)
    return 202, {"ok": True}


def api_report(req, m):
    return camps.report()


def api_overview(req, m):
    rec = planner.recommendations(limit=5)
    st = sync.status()
    with get_session() as db:
        plan_counts = dict(db.query(PlanCategory.status, func.count()).group_by(PlanCategory.status).all())
        n_channels = db.query(Channel).count()
        n_camps = db.query(Campaign).filter(Campaign.status == "active").count()
        new_products = db.query(Product).order_by(Product.first_seen_at.desc()).limit(6).all() \
            if st.get("initial_done_at") else []
    _with_prices(rec["items"])
    return {"jalali": rec["jalali"], "month_name": rec["month_name"], "budget_toman": rec["budget_toman"],
            "recommendations": rec["items"], "sync": {k: st[k] for k in ("running", "products", "in_stock", "categories",
                                                                         "site_total", "initial_done_at", "last_daily_at")},
            "weather": weather.current(),
            "plan": plan_counts, "channels": n_channels, "active_campaigns": n_camps, "report": camps.report()["total"],
            "new_products": [_row(p, "id", "name", "price", "image", "permalink", "first_seen_at") for p in new_products]}


# =============== شهرها، آب‌وهوا، سود ===============
def api_cities(req, m):
    return cities.list_cities(req.q("q") or "")[:400]


def api_city_plan(req, m):
    city = (req.q("city") or "").strip()
    if not city:
        raise HttpError(400, "نام شهر لازم است")
    r = cities.city_plan(city, month=_int(req.q("month"), "month", 1, 12),
                         limit=_int(req.q("limit", "10"), "limit", 1, 30))
    _with_prices(r["items"])
    return r


def api_weather(req, m):
    return weather.current()


def api_weather_refresh(req, m):
    try:
        return weather.refresh()
    except Exception as e:  # noqa: BLE001
        raise HttpError(503, str(e)[:200]) from None


def api_margins(req, m):
    b = req.body()
    cur = dict(planner.cfg("planner.margins") or {})
    for k, v in (b.get("margins") or {}).items():
        if v in ("low", "mid", "high"):
            cur[str(k)[:40]] = v
        elif v in ("", None):
            cur.pop(str(k), None)
    set_setting("planner.margins", cur)
    return {"planner.margins": cur}


# =============== لینک کوتاه ===============
def public_redirect(req, m):
    code = m.group(1).upper()
    with get_session() as db:
        p = db.query(Placement).filter(Placement.code == code).first()
        if not p:
            raise Redirect(get_settings().brand_site_url)
        db.add(Click(code=code, visitor=visitor_hash(req.client_ip(), req.headers.get("User-Agent", "")),
                     referer=(req.headers.get("Referer") or "")[:300] or None))
        db.commit()
        raise Redirect(p.target_url or get_settings().brand_site_url)


# =============== دستیار، ربات‌ها، ویجت ===============
_rate: dict = {}
ALLOWED_ORIGINS = {"https://caravansanat.com", "https://www.caravansanat.com"}


def _rate_ok(ip: str, limit: int = 20, window: int = 600) -> bool:
    now = time.time()
    q = [t for t in _rate.get(ip, []) if now - t < window]
    if len(q) >= limit:
        _rate[ip] = q
        return False
    q.append(now)
    _rate[ip] = q
    if len(_rate) > 5000:
        _rate.clear()
    return True


def public_ask(req, m):
    if not _rate_ok(req.client_ip()):
        raise HttpError(429, "تعداد پرسش‌ها زیاد شد؛ چند دقیقه بعد دوباره امتحان کنید")
    b = req.body()
    q = (b.get("q") or "").strip()
    if not 2 <= len(q) <= 300:
        raise HttpError(400, "پرسش کوتاه یا بلند است")
    return assistant.ask(q, channel="site", user_id=(b.get("session") or req.client_ip())[:64], mock=MOCK)


def api_ext_import(req, m):
    """ورود داده‌ی پنل‌های بیرونی (ترب، سرچ کنسول، آنالیتیکس). از صفحه‌ی «پل» پنل خودمان فرستاده می‌شود:
    صفحه‌ی منبع (با نشست خود صاحب فروشگاه) داده را با postMessage به پنل می‌دهد و پنل با توکن خودش ارسال می‌کند."""
    from app.tracking import external
    try:
        payload = json.loads(req.raw(max_bytes=4_000_000) or b"{}")
        return external.ingest(payload if isinstance(payload, dict) else {})
    except (ValueError, json.JSONDecodeError) as e:
        raise HttpError(400, str(e)[:200]) from None
    finally:
        planner._hist_cache["at"] = 0.0


def api_ext(req, m):
    from app.tracking import external
    return external.summary()


def public_widget(req, m):
    req._send(200, (WEB_DIR / "widget.js").read_bytes(), "application/javascript; charset=utf-8",
              {"Cache-Control": "public, max-age=600", "Access-Control-Allow-Origin": "*"})
    raise Handled()


def public_bot(req, m):
    platform, sec = m.group(1), m.group(2)
    if platform not in bots.APIS or not bots.secret(platform) or not hmac.compare_digest(sec, bots.secret(platform)):
        raise HttpError(404, "not found")
    upd = req.body()
    _bg(bots.handle_update, platform, upd)
    return {"ok": True}


def api_assistant_ask(req, m):
    q = (req.body().get("q") or "").strip()
    if not q:
        raise HttpError(400, "پرسش خالی است")
    return assistant.ask(q, channel="panel", mock=MOCK)


def api_assistant_stats(req, m):
    s = assistant.stats()
    s["bots"] = bots.configured()
    return s


def api_bots_setup(req, m):
    base = get_settings().public_base_url or os.environ.get("RENDER_EXTERNAL_URL", "")
    if not base:
        raise HttpError(400, "آدرس عمومی سرویس معلوم نیست")
    return bots.setup_webhooks(base)


def api_offline(req, m):
    from app.tracking.offline import summary
    s = summary() or {}
    if s.get("plans"):
        with get_session() as db:
            names = dict(db.query(PlanCategory.id, PlanCategory.name).all())
        for k, v in s["plans"].items():
            v["name"] = names.get(k, "متفرقه" if k == "OTHER" else k)
    if (s.get("demand") or {}).get("plans"):
        with get_session() as db:
            names = dict(db.query(PlanCategory.id, PlanCategory.name).all())
        s["demand"]["plans"] = [[re.sub(r"\s*\(.*?\)", "", names.get(k, k))[:45], v] for k, v in s["demand"]["plans"]]
    return s


def api_offline_import(req, m):
    """فایل اکسل فروش (بدنه‌ی خام). فقط آمار جمعی ذخیره می‌شود؛ خود فایل و نام/تلفن‌ها نه."""
    from app.tracking.offline import import_file
    data = req.raw(max_bytes=15_000_000)
    if not data[:2] == b"PK":
        raise HttpError(400, "فایل باید xlsx باشد")
    try:
        s = import_file(data)
    except ValueError as e:
        raise HttpError(400, str(e)) from None
    planner._hist_cache["at"] = 0.0   # امتیازها با داده‌ی تازه دوباره حساب شوند
    return {k: s[k] for k in ("invoices", "revenue", "profit", "margin_pct", "avg_profit", "max_cost_per_call")} | \
        {"calls": s["calls"]["total"], "demand": (s.get("demand") or {}).get("total", 0)}


# =============== مسیرها ===============
ROUTES = [
    ("GET", r"/api/me", api_me), ("GET", r"/api/stats", api_stats), ("GET", r"/api/overview", api_overview),
    ("GET", r"/api/jobs", api_jobs), ("GET", r"/api/jobs/([0-9a-f-]{36})", api_job),
    ("POST", r"/api/generate", api_generate), ("POST", r"/api/jobs/([0-9a-f-]{36})/retry", api_job_retry),
    ("GET", r"/api/sync", api_sync_status), ("POST", r"/api/sync", api_sync_run),
    ("GET", r"/api/cron", api_cron), ("POST", r"/api/cron", api_cron),
    ("GET", r"/api/plan", api_plan), ("POST", r"/api/plan/propose", api_plan_propose),
    ("POST", r"/api/plan/([\w-]+)", api_plan_update),
    ("GET", r"/api/categories/unmapped", api_unmapped), ("POST", r"/api/categories/(\d+)", api_category_map),
    ("GET", r"/api/products", api_products), ("GET", r"/api/recommendations", api_recommendations),
    ("GET", r"/api/settings", api_settings), ("POST", r"/api/settings", api_settings_update),
    ("GET", r"/api/channels", api_channels), ("POST", r"/api/channels/bulk", api_channels_bulk),
    ("POST", r"/api/channels/(\d+)", api_channel_update), ("POST", r"/api/channels/(\d+)/check", api_channel_check),
    ("POST", r"/api/channels/(\d+)/delete", api_channel_delete),
    ("GET", r"/api/campaigns", api_campaigns), ("POST", r"/api/campaigns", api_campaign_create),
    ("POST", r"/api/campaigns/(\d+)", api_campaign_update),
    ("POST", r"/api/placements", api_placement_create), ("POST", r"/api/placements/(\d+)", api_placement_update),
    ("POST", r"/api/placements/(\d+)/ad", api_placement_ad),
    ("GET", r"/api/report", api_report),
    ("GET", r"/api/cities", api_cities), ("GET", r"/api/cities/plan", api_city_plan),
    ("GET", r"/api/weather", api_weather), ("POST", r"/api/weather/refresh", api_weather_refresh),
    ("POST", r"/api/settings/margins", api_margins),
    ("POST", r"/api/assistant/ask", api_assistant_ask), ("GET", r"/api/assistant", api_assistant_stats),
    ("POST", r"/api/bots/setup", api_bots_setup),
    ("GET", r"/api/history", api_history), ("POST", r"/api/history/run", api_history_run),
    ("GET", r"/api/pricing", api_pricing_status), ("POST", r"/api/pricing/run", api_pricing_run),
    ("GET", r"/api/offline", api_offline), ("POST", r"/api/offline/import", api_offline_import),
    ("GET", r"/api/ext", api_ext), ("POST", r"/api/ext/import", api_ext_import),
]
_COMPILED = [(meth, re.compile("^" + pat + "$"), fn) for meth, pat, fn in ROUTES]
_PUBLIC = [("GET", re.compile(r"^/r/([A-Za-z0-9]{3,12})$"), public_redirect),
           ("POST", re.compile(r"^/public/ask$"), public_ask),
           ("GET", re.compile(r"^/widget\.js$"), public_widget),
           ("POST", re.compile(r"^/bot/(telegram|bale)/([0-9a-f]{24})$"), public_bot)]


class Handler(BaseHTTPRequestHandler):
    server_version = "content-engine"
    _body_cache = None

    # ---------- helpers ----------
    def q(self, key, default=None):
        v = parse_qs(urlparse(self.path).query).get(key)
        return v[0] if v else default

    def body(self) -> dict:
        if self._body_cache is None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 200_000:
                raise HttpError(413, "درخواست خیلی بزرگ است")
            try:
                self._body_cache = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise HttpError(400, "بدنه‌ی درخواست نامعتبر است") from None
            if not isinstance(self._body_cache, dict):
                raise HttpError(400, "بدنه باید شیء JSON باشد")
        return self._body_cache

    def raw(self, max_bytes: int) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            raise HttpError(400, "فایلی ارسال نشد")
        if n > max_bytes:
            raise HttpError(413, "فایل خیلی بزرگ است")
        return self.rfile.read(n)

    def client_ip(self) -> str:
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def _send(self, code: int, data: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _cors(self) -> dict:
        origin = self.headers.get("Origin", "")
        if urlparse(self.path).path == "/public/ask" and origin in ALLOWED_ORIGINS:
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                    "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
        return {}

    def _json(self, code: int, body):
        self._send(code, json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8", self._cors())

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain", self._cors())

    def _authorized(self) -> bool:
        return bool(RUN_TOKEN) and hmac.compare_digest(self.headers.get("X-Run-Token", ""), RUN_TOKEN)

    def log_message(self, fmt, *args):
        p = urlparse(self.path).path
        if self.command == "POST" or p.startswith("/r/"):
            log.info("%s %s", self.command, p)

    # ---------- dispatch ----------
    def _dispatch(self, method: str):
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if method in ("GET", "HEAD") and path in ("/", "/index.html"):
                return self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8",
                                  {"X-Frame-Options": "DENY", "Cache-Control": "no-cache"})
            if path == "/health":
                return self._json(200, {"ok": True})
            for meth, rx, fn in _PUBLIC:
                mm = rx.match(path)
                if mm and meth == method:
                    res = fn(self, mm)
                    code, body = res if isinstance(res, tuple) else (200, res)
                    return self._json(code, body)
            if not path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            if not self._authorized():
                return self._json(401, {"error": "unauthorized"})
            for meth, rx, fn in _COMPILED:
                mm = rx.match(path)
                if mm and meth == method:
                    res = fn(self, mm)
                    code, body = res if isinstance(res, tuple) else (200, res)
                    return self._json(code, body)
            return self._json(404, {"error": "not found"})
        except Handled:
            pass
        except Redirect as r:
            self._send(302, b"", "text/plain", {"Location": r.url})
        except HttpError as e:
            self._json(e.code, {"error": e.msg})
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception:  # noqa: BLE001
            log.exception("خطای سرور در %s %s", method, path)
            self._json(500, {"error": "خطای داخلی سرور"})

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")


# =============== راه‌اندازی ===============
def recover_jobs() -> None:
    with get_session() as db:
        stuck = db.scalars(select(Job).where(Job.status.in_([JobStatus.pending, JobStatus.generating]))).all()
        ids = [j.id for j in stuck]
        for j in stuck:
            j.status = JobStatus.pending
        for p in db.query(Placement).filter(Placement.ad_status == "generating").all():
            p.ad_status, p.ad_error = "failed", "سرویس وسط کار ری‌استارت شد؛ دوباره بسازید"
        db.commit()
    for jid in ids:
        _bg(run_job, jid, None, MOCK)


def scheduler_loop() -> None:
    """تا وقتی سرویس بیدار است، هر ۱۰ دقیقه اگر وقت همگام‌سازی باشد شروعش می‌کند."""
    time.sleep(20)
    while True:
        try:
            if sync.due() and not sync.is_running():
                _bg(sync.run_daily, 720, MOCK, False, pool=_sync_pool)
        except Exception:  # noqa: BLE001
            log.exception("خطای زمان‌بند")
        time.sleep(600)


def main() -> None:
    init_db()
    seed_plan()
    with get_session() as db:
        n_jobs = db.scalar(select(func.count()).select_from(Job))
        n_prod = db.query(Product).count()
    log.info("دیتابیس: %s | محتوا: %d | محصول: %d", get_engine().dialect.name, n_jobs, n_prod)
    recover_jobs()
    if not NO_SCHEDULER:
        threading.Thread(target=scheduler_loop, daemon=True, name="scheduler").start()
    if not RUN_TOKEN:
        log.warning("RUN_TOKEN تنظیم نشده؛ API و پنل قابل ورود نیستند.")
    port = int(os.environ.get("PORT", "10000"))
    log.info("پنل روی پورت %d آماده است", port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
