"""
پروژه‌های جدا: ارزیابی و قیمت‌سنجی فهرست کانال‌های یک موضوع دیگر (مثلاً «مسابقه‌ی کارآفرینی»).

کاملاً جدا از جریان کاروان صنعت: جدول‌های خودش را دارد و به کانال‌ها، شکارچی، کمپین‌ها و آمار فروشگاه
چیزی نمی‌نویسد. (فقط قیمت‌های واقعی کانال‌های اصلی را برای «قیمت هر بازدید» می‌خواند.)

مراحل هر پروژه (در پس‌زمینه، آهسته):
۱. سنجش: صفحه‌ی عمومی t.me/s/<کانال> ← همان معیارهای دقیق (بازدید مورد انتظار آگهی، شلوغی تبلیغ، نشانه‌های فیک).
۲. «علاقه‌ی مخاطب»: بازدید پست‌های مربوط به موضوع پروژه ÷ بقیه‌ی پست‌های همان کانال (کلمه‌های کلیدی پروژه).
۳. ربط به موضوع: هوش مصنوعی (مدل سریع) هر کانال را با موضوع و مخاطب پروژه می‌سنجد (۰ تا ۱).
۴. قیمت تخمینی: بازدید مورد انتظار × «قیمت هر بازدید» که از قیمت‌های واقعی گرفته‌شده از ادمین‌ها حساب می‌شود.
"""
import logging
import re
import statistics
import time
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import JSON, BigInteger, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.db.session import get_session

log = logging.getLogger("projects")
DELAY = 2.5                  # ثانیه بین درخواست‌ها به t.me
DEFAULT_TOMAN_PER_VIEW = 150  # اگر هنوز قیمت واقعی کافی نداریم (برچسب «تخمینی» دارد)
MIN_PRICE = 100_000


def _now():
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    topic: Mapped[str] = mapped_column(Text)                       # موضوع و مخاطب، به زبان صاحب پروژه
    keywords: Mapped[list] = mapped_column(JSON, default=list)      # برای «علاقه‌ی مخاطب»
    intro: Mapped[str | None] = mapped_column(Text)                 # معرفی فرستنده در پیام به ادمین‌ها
    status: Mapped[str] = mapped_column(String(20), default="active")   # active | paused
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProjectChannel(Base):
    __tablename__ = "project_channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    platform: Mapped[str] = mapped_column(String(20), default="telegram")
    username: Mapped[str] = mapped_column(String(80))
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    sample: Mapped[str | None] = mapped_column(Text)
    members: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict | None] = mapped_column(JSON)
    expected_ad_views: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    flags: Mapped[list | None] = mapped_column(JSON)
    topic_lift: Mapped[float | None] = mapped_column(Float)
    topic_posts: Mapped[int | None] = mapped_column(Integer)
    fit: Mapped[float | None] = mapped_column(Float)                # ربط به موضوع پروژه (۰ تا ۱)
    audience: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(80))
    ad_contacts: Mapped[list | None] = mapped_column(JSON)
    est_price: Mapped[int | None] = mapped_column(BigInteger)
    price_toman: Mapped[int | None] = mapped_column(BigInteger)     # قیمت واقعی (دستی)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | checked | error
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------- ساخت پروژه ----------------
_REF = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})|^@?([A-Za-z][A-Za-z0-9_]{3,31})$")


def parse_refs(text: str) -> list[str]:
    out, seen = [], set()
    for line in re.split(r"[\s,،]+", text or ""):
        m = _REF.search(line.strip())
        if not m:
            continue
        u = m.group(1) or m.group(2)
        if u.lower() in ("joinchat", "share", "addstickers", "proxy", "s") or u.lower() in seen:
            continue
        seen.add(u.lower())
        out.append(u)
    return out


class _Kw(BaseModel):
    keywords: list[str] = Field(default_factory=list, description="۲۰ تا ۳۵ کلمه یا ریشه‌ی کوتاه فارسی (و چند انگلیسی) که در پست‌های مرتبط با این موضوع می‌آید")


def suggest_keywords(topic: str, mock: bool = False) -> list[str]:
    if mock:
        return [w for w in re.findall(r"[؀-ۿ]{3,}", topic)][:10]
    from app.brain.llm import get_llm
    from app.config import get_settings
    r = get_llm(get_settings().llm_fast_model).generate_json(
        "برای سنجش «علاقه‌ی مخاطب کانال‌های تلگرام به یک موضوع»، کلمه‌های کلیدی کوتاه بده که در متن پست‌های مرتبط "
        "می‌آید (ریشه‌ی کلمه بهتر است، مثلاً «کارآفرین» به‌جای «کارآفرینی»). کلمه‌های خیلی عام نده. خروجی فقط JSON.",
        f"موضوع و مخاطب: {topic}\n\nاسکیما: {_Kw.model_json_schema()}", _Kw)
    return [k.strip() for k in r.data.keywords if 2 <= len(k.strip()) <= 30][:40]


def create(name: str, topic: str, links: str, mock: bool = False) -> dict:
    name, topic = (name or "").strip()[:200], (topic or "").strip()[:2000]
    if not name or not topic:
        raise ValueError("نام و موضوع پروژه لازم است")
    refs = parse_refs(links)
    if not refs:
        raise ValueError("هیچ لینک یا آیدی کانال تلگرامی پیدا نشد")
    try:
        kws = suggest_keywords(topic, mock=mock)
    except Exception as e:  # noqa: BLE001
        log.warning("کلمه‌های کلیدی ساخته نشد: %s", e)
        kws = []
    with get_session() as db:
        p = Project(name=name, topic=topic, keywords=kws)
        db.add(p)
        db.flush()
        for u in refs[:5000]:
            db.add(ProjectChannel(project_id=p.id, username=u))
        db.commit()
        return {"id": p.id, "channels": len(refs[:5000]), "keywords": kws}


def add_links(pid: int, links: str) -> dict:
    refs = parse_refs(links)
    with get_session() as db:
        have = {u.lower() for (u,) in db.query(ProjectChannel.username).filter(ProjectChannel.project_id == pid).all()}
        new = [u for u in refs if u.lower() not in have]
        for u in new:
            db.add(ProjectChannel(project_id=pid, username=u))
        db.commit()
    return {"added": len(new), "duplicates": len(refs) - len(new)}


def update(pid: int, b: dict) -> dict:
    with get_session() as db:
        p = db.get(Project, pid)
        if not p:
            raise ValueError("پروژه پیدا نشد")
        if "name" in b and str(b["name"]).strip():
            p.name = str(b["name"]).strip()[:200]
        if "intro" in b:
            p.intro = str(b["intro"]).strip()[:400] or None
        if "status" in b and b["status"] in ("active", "paused"):
            p.status = b["status"]
        if "keywords" in b and isinstance(b["keywords"], list):
            p.keywords = [str(k).strip()[:30] for k in b["keywords"] if str(k).strip()][:60]
            # کلمه‌ها عوض شد ← «علاقه‌ی مخاطب» دوباره از نمونه‌ی ذخیره‌شده حساب شود
            rx = _kw_rx(p.keywords)
            for c in db.query(ProjectChannel).filter(ProjectChannel.project_id == pid, ProjectChannel.metrics.isnot(None)).all():
                c.topic_lift, c.topic_posts = _lift((c.metrics or {}).get("_posts") or [], rx)
        db.commit()
    return {"ok": True}


def set_price(pcid: int, price: int | None) -> dict:
    with get_session() as db:
        c = db.get(ProjectChannel, pcid)
        if not c:
            raise ValueError("کانال پیدا نشد")
        c.price_toman = int(price) if price else None
        db.commit()
    return {"ok": True}


def delete(pid: int) -> dict:
    with get_session() as db:
        db.query(ProjectChannel).filter(ProjectChannel.project_id == pid).delete()
        db.query(Project).filter(Project.id == pid).delete()
        db.commit()
    return {"ok": True}


# ---------------- سنجش ----------------
def _kw_rx(kws: list[str]):
    ks = [re.escape(k) for k in kws if k]
    return re.compile("|".join(ks), re.I) if ks else None


def _lift(posts: list[dict], rx) -> tuple[float | None, int]:
    """posts: [{v: بازدید، t: متن}] پست‌های محتوایی بالغ."""
    if not rx:
        return None, 0
    hit = [p["v"] for p in posts if p.get("v") and rx.search(p.get("t") or "")]
    rest = [p["v"] for p in posts if p.get("v") and not rx.search(p.get("t") or "")]
    if len(hit) >= 3 and len(rest) >= 3 and statistics.median(rest):
        return round(max(0.3, min(3.0, statistics.median(hit) / statistics.median(rest))), 2), len(hit)
    return None, len(hit)


def measure(pc_id: int, rx) -> None:
    from app.channels.metrics import ad_contacts, compute, score
    from app.channels.telegram import fetch_telegram
    with get_session() as db:
        c = db.get(ProjectChannel, pc_id)
        user = c.username
    try:
        d = fetch_telegram(user)
        posts = d["post_list"]
        if len(posts) < 15 and d.get("min_post_id"):
            time.sleep(DELAY)
            try:
                posts = fetch_telegram(user, before=d["min_post_id"])["post_list"] + posts
            except Exception:  # noqa: BLE001
                pass
        m = compute(posts, d["members"], user)
        # نمونه‌ی فشرده‌ی پست‌های محتوایی بالغ برای حساب دوباره‌ی «علاقه‌ی مخاطب» با کلمه‌های تازه
        cut = _now() - timedelta(hours=24)
        m["_posts"] = [{"v": p["views"], "t": (p.get("text") or "")[:300]} for p in posts
                       if p.get("views") and p.get("kind") == "content" and (not p.get("ts") or p["ts"] < cut)][-60:]
        s, flags, _ = score(m, d["members"]) if m.get("expected_ad_views") else (0.0, ["بازدید نامشخص"], None)
        lift, n = _lift(m["_posts"], rx)
        with get_session() as db:
            c = db.get(ProjectChannel, pc_id)
            c.title, c.description, c.members = d["title"], d["description"], d["members"]
            c.sample = "\n".join(d["texts"][-8:])[:3000]
            c.metrics, c.expected_ad_views, c.score, c.flags = m, m.get("expected_ad_views"), s, flags
            c.topic_lift, c.topic_posts = lift, n
            c.ad_contacts = ad_contacts(d["description"], user)
            c.status, c.error, c.checked_at = "checked", None, _now()
            db.commit()
    except Exception as e:  # noqa: BLE001
        with get_session() as db:
            c = db.get(ProjectChannel, pc_id)
            c.status, c.error, c.checked_at = "error", str(e)[:300], _now()
            db.commit()


class _T(BaseModel):
    id: int
    fit: float = Field(0.0, description="۰ تا ۱: تبلیغِ این موضوع در این کانال چقدر به مخاطب درست می‌رسد")
    audience: str = Field("", description="مخاطب کانال در چند کلمه، مثلاً «دانشجویان مهندسی شریف»")
    city: str = Field("", description="شهر/دانشگاه اگر محلی است، وگرنه خالی")


class _Ts(BaseModel):
    items: list[_T]


def tag(pid: int, deadline: float, batch: int = 15, mock: bool = False) -> int:
    with get_session() as db:
        p = db.get(Project, pid)
        topic = p.topic
        rows = (db.query(ProjectChannel).filter(ProjectChannel.project_id == pid, ProjectChannel.status == "checked",
                                                ProjectChannel.tagged_at.is_(None)).limit(batch * 4).all())
        items = [(c.id, c.username, c.title, c.description, c.sample) for c in rows]
    done = 0
    for i in range(0, len(items), batch):
        if time.monotonic() > deadline:
            break
        chunk = items[i:i + batch]
        if mock:
            tags = [_T(id=c[0], fit=0.5, audience="آزمایشی") for c in chunk]
        else:
            from app.brain.llm import get_llm
            from app.config import get_settings
            text = "\n\n".join(f"[{cid}] @{u} — {t or ''}\nتوضیح: {(d or '')[:250]}\nپست‌ها: {(s or '')[:700]}"
                               for cid, u, t, d, s in chunk)
            try:
                r = get_llm(get_settings().llm_fast_model).generate_json(
                    "تو تحلیل‌گر رسانه‌ی ایرانی هستی. برای هر کانال تلگرام بگو تبلیغِ موضوع زیر در آن چقدر به مخاطب درست "
                    "می‌رسد (fit ۰ تا ۱؛ ۱ یعنی مخاطب دقیقاً همان است، ۰٫۵ یعنی تا حدی، ۰ یعنی بی‌ربط) و مخاطبش کیست. "
                    f"موضوع و مخاطب هدف: {topic}\nخروجی فقط JSON.",
                    text + f"\n\nاسکیما: {_Ts.model_json_schema()}", _Ts)
                tags = r.data.items
            except Exception as e:  # noqa: BLE001
                log.warning("برچسب پروژه ناموفق: %s", e)
                break
        with get_session() as db:
            for tg in tags:
                c = db.get(ProjectChannel, tg.id)
                if not c or c.project_id != pid:
                    continue
                c.fit = round(max(0.0, min(1.0, float(tg.fit))), 2)
                c.audience, c.city = (tg.audience or "")[:120], (tg.city or "")[:80] or None
                c.tagged_at = _now()
                done += 1
            db.commit()
    return done


def toman_per_view() -> tuple[float, int]:
    """قیمت هر بازدید آگهی از قیمت‌های واقعی (کانال‌های اصلی و پروژه‌ها)؛ میانه."""
    from app.db.marketing import Channel
    with get_session() as db:
        xs = [c.price_toman / c.expected_ad_views for c in db.query(Channel).filter(Channel.price_toman.isnot(None),
                                                                                    Channel.expected_ad_views > 0).all()]
        xs += [c.price_toman / c.expected_ad_views for c in db.query(ProjectChannel).filter(
            ProjectChannel.price_toman.isnot(None), ProjectChannel.expected_ad_views > 0).all()]
    xs = [x for x in xs if 10 <= x <= 5000]
    return (round(statistics.median(xs), 1), len(xs)) if xs else (DEFAULT_TOMAN_PER_VIEW, 0)


def estimate(c: ProjectChannel, tpv: float) -> int | None:
    if not c.expected_ad_views:
        return None
    return max(MIN_PRICE, int(round(c.expected_ad_views * tpv, -4)))


def run_once(budget_sec: int = 240, mock: bool = False) -> dict:
    """یک دور کار برای همه‌ی پروژه‌های فعال: سنجش آهسته + برچسب + قیمت تخمینی."""
    deadline = time.monotonic() + budget_sec
    stats = {"measured": 0, "tagged": 0}
    with get_session() as db:
        projects = [(p.id, p.keywords) for p in db.query(Project).filter(Project.status == "active").all()]
    for pid, kws in projects:
        rx = _kw_rx(kws or [])
        with get_session() as db:
            todo = [c.id for c in db.query(ProjectChannel).filter(ProjectChannel.project_id == pid,
                                                                  ProjectChannel.status == "new").limit(200).all()]
        for cid in todo:
            if time.monotonic() > deadline - 30:
                break
            measure(cid, rx)
            stats["measured"] += 1
            time.sleep(0 if mock else DELAY)
        if time.monotonic() < deadline:
            stats["tagged"] += tag(pid, deadline, mock=mock)
    tpv, _ = toman_per_view()
    with get_session() as db:
        for c in db.query(ProjectChannel).filter(ProjectChannel.expected_ad_views.isnot(None)).all():
            c.est_price = estimate(c, tpv)
        db.commit()
    return stats


# ---------------- خواندن، بودجه، خروجی ----------------
def _value(c: ProjectChannel) -> float:
    """امتیاز نهایی برای مرتب‌سازی: ربط × کیفیت × علاقه‌ی مخاطب."""
    fit = c.fit if c.fit is not None else 0.5
    lift = max(0.7, min(1.5, c.topic_lift)) if c.topic_lift else 1.0
    return round((c.score or 0) * fit * lift, 1)


def _row(c: ProjectChannel) -> dict:
    price = c.price_toman or c.est_price
    m = c.metrics or {}
    return {"id": c.id, "username": c.username, "title": c.title, "members": c.members,
            "median_views": m.get("median_views"), "view_rate": m.get("view_rate"),
            "expected_ad_views": c.expected_ad_views, "ads_per_day": m.get("ads_per_day"),
            "score": c.score, "flags": c.flags or [], "fit": c.fit, "audience": c.audience, "city": c.city,
            "topic_lift": c.topic_lift, "topic_posts": c.topic_posts, "value": _value(c),
            "est_price": c.est_price, "price_toman": c.price_toman, "price_is_estimate": not c.price_toman,
            "cpm": round(price / c.expected_ad_views * 1000) if price and c.expected_ad_views else None,
            "ad_contacts": c.ad_contacts or [], "status": c.status, "error": c.error,
            "last_post_days": m.get("last_post_days")}


def get(pid: int) -> dict:
    with get_session() as db:
        p = db.get(Project, pid)
        if not p:
            raise ValueError("پروژه پیدا نشد")
        rows = db.query(ProjectChannel).filter(ProjectChannel.project_id == pid).all()
        st = {"total": len(rows), "checked": sum(r.status == "checked" for r in rows),
              "error": sum(r.status == "error" for r in rows), "waiting": sum(r.status == "new" for r in rows),
              "tagged": sum(r.tagged_at is not None for r in rows),
              "relevant": sum((r.fit or 0) >= 0.6 for r in rows)}
        items = sorted((_row(r) for r in rows), key=lambda x: (x["status"] != "checked", -x["value"]))
        tpv, n = toman_per_view()
        eta_min = round(st["waiting"] * (DELAY * 1.6) / 60 * (900 / 240)) if st["waiting"] else 0
        return {"id": p.id, "name": p.name, "topic": p.topic, "keywords": p.keywords or [], "status": p.status,
                "intro": p.intro or "",
                "stats": st, "items": items, "toman_per_view": tpv, "price_samples": n, "eta_min": eta_min}


def listing() -> list[dict]:
    with get_session() as db:
        out = []
        for p in db.query(Project).order_by(Project.id.desc()).all():
            n = db.query(ProjectChannel).filter(ProjectChannel.project_id == p.id).count()
            done = db.query(ProjectChannel).filter(ProjectChannel.project_id == p.id, ProjectChannel.status != "new").count()
            out.append({"id": p.id, "name": p.name, "topic": p.topic[:200], "status": p.status, "total": n, "done": done})
        return out


def budget_plan(pid: int, amount: int, min_fit: float = 0.6) -> dict:
    """بیشترین بازدیدِ مخاطبِ درست با این بودجه: کانال‌های مرتبط و سالم، به ترتیب «بازدید مفید به ازای هر تومان»."""
    with get_session() as db:
        rows = [c for c in db.query(ProjectChannel).filter(ProjectChannel.project_id == pid,
                                                           ProjectChannel.status == "checked").all()
                if (c.fit or 0) >= min_fit and c.expected_ad_views and (c.price_toman or c.est_price)
                and not any("فیک" in f or "خریدنی" in f for f in (c.flags or []))]
    def eff(c):
        lift = max(0.7, min(1.5, c.topic_lift)) if c.topic_lift else 1.0
        return c.expected_ad_views * (c.fit or 0.5) * lift / (c.price_toman or c.est_price)
    picked, left, views = [], int(amount), 0
    for c in sorted(rows, key=eff, reverse=True):
        price = c.price_toman or c.est_price
        if price <= left:
            picked.append({**_row(c), "price": price})
            left -= price
            views += c.expected_ad_views
    spent = int(amount) - left
    return {"amount": int(amount), "spent": spent, "left": left, "channels": len(picked), "views": views,
            "cpm": round(spent / views * 1000) if views else None, "items": picked,
            "estimated": sum(1 for x in picked if x["price_is_estimate"])}


def export_csv(pid: int) -> bytes:
    import csv
    import io
    d = get(pid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["کانال", "آیدی", "عضو", "میانه‌ی بازدید", "بازدید مورد انتظار آگهی", "امتیاز کیفیت", "ربط به موضوع",
                "مخاطب", "شهر/دانشگاه", "علاقه‌ی مخاطب به موضوع", "امتیاز نهایی", "قیمت (تومان)", "قیمت تخمینی است؟",
                "هزینه‌ی هر ۱۰۰۰ بازدید", "تبلیغ در روز", "هشدارها", "تماس تبلیغ", "وضعیت"])
    for x in d["items"]:
        w.writerow([x["title"] or "", "@" + x["username"], x["members"] or "", x["median_views"] or "",
                    x["expected_ad_views"] or "", x["score"] or "", x["fit"] if x["fit"] is not None else "",
                    x["audience"] or "", x["city"] or "", x["topic_lift"] or "", x["value"],
                    x["price_toman"] or x["est_price"] or "", "بله" if x["price_is_estimate"] else "خیر",
                    x["cpm"] or "", x["ads_per_day"] or "", "؛ ".join(x["flags"]), " ".join(x["ad_contacts"]),
                    {"checked": "سنجیده", "error": "خطا: " + (x["error"] or ""), "new": "در صف"}.get(x["status"], x["status"])])
    return ("﻿" + buf.getvalue()).encode("utf-8")          # BOM تا اکسل فارسی را درست نشان دهد
