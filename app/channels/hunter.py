"""
شکارچی کانال.

۱. گلوله‌برفی (خودکار روی سرور): پست‌های کانال‌های خوبِ موجود خوانده می‌شود؛ هر کانالی که در آن‌ها لینک،
   @ یا فوروارد شده «نامزد» می‌شود. کانال‌هایی که با هم تبلیغ مبادله می‌کنند معمولاً هم‌شهر یا هم‌موضوع‌اند.
۲. جست‌وجوی گوگل (در مرورگر صاحب فروشگاه): برای خانه‌های خالی جدول پوشش عبارت ساخته می‌شود و
   لینک‌های t.me / eitaa.com / ble.ir نتیجه‌ها با «پل» پنل وارد فهرست نامزدها می‌شود.
۳. بررسی نامزد: پیش‌نمایش عمومی، عضو، بازدید، تبلیغ‌زدگی. اگر حداقل‌ها را داشت به کانال‌ها اضافه می‌شود.
۴. برچسب‌زنی با هوش مصنوعی: شهر/استان مخاطب، دسته‌های نقشه، نوع مخاطب، محلی یا سراسری.
۵. جدول پوشش: برای پیشنهادهای این ماه و ماه بعد، هر «دسته × استان» چند کانال مناسب دارد.

هیچ پیامی به کانال‌ها فرستاده نمی‌شود و عضو هیچ کانالی نمی‌شویم؛ فقط صفحه‌ی عمومی خوانده می‌شود.
"""
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import func

from app.db.marketing import Channel, ChannelCandidate, PlanCategory
from app.db.session import get_session, get_setting, set_setting

log = logging.getLogger("channels.hunter")
_PAREN = re.compile(r"\s*\(.*?\)")

MIN_MEMBERS = 800
MIN_VIEWS = 150
DELAY = 2.5            # ثانیه بین درخواست‌ها به t.me
TARGET_PER_CELL = 8    # هدف: برای هر «دسته × استان» حداقل این تعداد کانال مناسب

AUDIENCES = {"farmer": "کشاورز و باغدار", "contractor": "پیمانکار و ساختمانی", "industry": "صنعتی و کارخانه",
             "technician": "تعمیرکار و فنی", "local_news": "خبری و عمومی شهر", "classifieds": "نیازمندی و آگهی",
             "trade": "بازار و فروشندگان", "general": "عمومی سراسری", "irrelevant": "نامرتبط"}


def _now():
    return datetime.now(timezone.utc)


# ---------------- نامزدها ----------------
def add_candidates(refs: list[tuple[str, str]], source: str, found_via: str | None = None) -> dict:
    """refs: [(platform, username)]"""
    added = bumped = known = 0
    with get_session() as db:
        have = {(c.platform, c.username.lower()) for c in db.query(Channel.platform, Channel.username).all()}
        cand = {(c.platform, c.username.lower()): c for c in db.query(ChannelCandidate).all()}
        for plat, user in refs:
            key = (plat, user.lower())
            if key in have:
                known += 1
                continue
            c = cand.get(key)
            if c:
                c.hits = (c.hits or 1) + 1
                bumped += 1
            else:
                c = ChannelCandidate(platform=plat, username=user, source=source, found_via=(found_via or "")[:200] or None)
                db.add(c)
                cand[key] = c
                added += 1
        db.commit()
    return {"added": added, "bumped": bumped, "known": known}


def ingest_candidates(rows: list, source: str = "google") -> dict:
    """از پل پنل: ردیف [لینک, عبارت جست‌وجو]"""
    from app.channels.telegram import parse_channel_ref
    refs, via = [], {}
    for r in rows[:3000]:
        if not isinstance(r, list) or not r:
            continue
        ref = parse_channel_ref(str(r[0]))
        if ref and ref[0] in ("telegram", "eitaa", "bale"):
            refs.append(ref)
            via[ref] = str(r[1])[:200] if len(r) > 1 else None
    out = {"added": 0, "bumped": 0, "known": 0}
    for ref in refs:
        res = add_candidates([ref], source, via.get(ref))
        for k in out:
            out[k] += res[k]
    return out


# ---------------- گلوله‌برفی ----------------
def snowball_step(deadline: float, max_parents: int = 6) -> dict:
    """از کانال‌های خوب (امتیاز ≥ ۴۰ و مرتبط) که ۱۴ روز اخیر خزیده نشده‌اند، نام کانال‌های دیگر را بیرون می‌کشد."""
    from app.channels.telegram import fetch_telegram
    cutoff = _now() - timedelta(days=14)
    with get_session() as db:
        parents = (db.query(Channel).filter(Channel.platform == "telegram", Channel.status == "checked",
                                            Channel.score >= 40, Channel.relevant.isnot(False))
                   .filter((Channel.crawled_at.is_(None)) | (Channel.crawled_at < cutoff))
                   .order_by(Channel.score.desc()).limit(max_parents).all())
        parents = [(p.id, p.username) for p in parents]
    found = Counter()
    done = 0
    for pid, user in parents:
        if time.monotonic() > deadline:
            break
        mentions = []
        try:
            d = fetch_telegram(user)
            mentions += d["mentions"]
            if d.get("min_post_id") and time.monotonic() < deadline:   # یک صفحه‌ی قدیمی‌تر
                time.sleep(DELAY)
                mentions += fetch_telegram(user, before=d["min_post_id"])["mentions"]
        except Exception as e:  # noqa: BLE001
            log.info("گلوله‌برفی %s: %s", user, e)
        res = add_candidates([("telegram", m) for m in set(mentions)], "snowball", user)
        found[user] = res["added"]
        with get_session() as db:
            ch = db.get(Channel, pid)
            ch.crawled_at = _now()
            db.commit()
        done += 1
        time.sleep(DELAY)
    return {"parents": done, "new_candidates": sum(found.values())}


# ---------------- بررسی نامزدها ----------------
def _fetch_eitaa(username: str) -> dict:
    """پیش‌نمایش عمومی ایتا (eitaa.com/<name>). ساختار صفحه ممکن است عوض شود؛ اگر نشد، خطا برمی‌گرداند."""
    import html as h

    import httpx
    from app.channels.telegram import _num
    r = httpx.get(f"https://eitaa.com/{username}", timeout=30, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (compatible; content-engine/1.0)"})
    r.raise_for_status()
    t = r.text
    title = re.search(r'<meta property="og:title" content="([^"]+)"', t)
    desc = re.search(r'<meta property="og:description" content="([^"]*)"', t)
    mem = re.search(r'([\d,.]+\s*[KkMm]?)\s*(?:عضو|members|subscribers)', t)
    views = [_num(v) for v in re.findall(r'class="[^"]*(?:etme_widget_message_views|message_views)[^"]*"[^>]*>([^<]+)<', t)]
    if not title:
        raise ValueError("صفحه‌ی عمومی ایتا خوانده نشد")
    texts = [re.sub(r"\s+", " ", h.unescape(re.sub(r"<[^>]+>", " ", x))).strip()[:400]
             for x in re.findall(r'class="[^"]*message_text[^"]*"[^>]*>(.*?)</div>', t, re.S)]
    return {"title": h.unescape(title.group(1)), "description": h.unescape(desc.group(1)) if desc else None,
            "members": _num(mem.group(1)) if mem else 0, "views": views, "texts": texts}


def check_candidates(deadline: float, limit: int = 60) -> dict:
    from app.channels.telegram import check_channel, fetch_telegram
    with get_session() as db:
        rows = (db.query(ChannelCandidate).filter(ChannelCandidate.status == "new")
                .order_by(ChannelCandidate.hits.desc(), ChannelCandidate.id).limit(limit).all())
        todo = [(c.id, c.platform, c.username, c.source, c.found_via) for c in rows]
    added = rejected = 0
    for cid, plat, user, src, via in todo:
        if time.monotonic() > deadline:
            break
        reason, new_id = None, None
        try:
            if plat == "telegram":
                d = fetch_telegram(user)
            elif plat == "eitaa":
                d = _fetch_eitaa(user)
            else:
                raise ValueError("بررسی خودکار این پیام‌رسان هنوز ممکن نیست")
            v = (d.get("views") or [])[-20:]
            avg = int(sum(v) / len(v)) if v else 0
            if (d.get("members") or 0) < MIN_MEMBERS:
                reason = f"عضو کم ({d.get('members') or 0})"
            elif plat == "telegram" and avg < MIN_VIEWS:
                reason = f"بازدید کم ({avg})"
            else:
                with get_session() as db:
                    ch = Channel(platform=plat, username=user, title=d.get("title"), description=d.get("description"),
                                 members=d.get("members"), avg_views=avg or None, source=src, found_via=via,
                                 sample="\n".join((d.get("texts") or [])[-8:])[:3000], status="new")
                    db.add(ch)
                    db.flush()
                    new_id = ch.id
                    db.commit()
        except Exception as e:  # noqa: BLE001
            reason = str(e)[:180]
        if new_id and plat == "telegram":
            try:
                check_channel(new_id)
            except Exception:  # noqa: BLE001
                pass
        with get_session() as db:
            c = db.get(ChannelCandidate, cid)
            c.status = "added" if new_id else "rejected"
            c.reason = reason
            c.checked_at = _now()
            db.commit()
        added += bool(new_id)
        rejected += not new_id
        time.sleep(DELAY)
    return {"added": added, "rejected": rejected}


# ---------------- برچسب‌زنی ----------------
class _Tag(BaseModel):
    id: int
    cities: list[str] = Field(default_factory=list, description="شهرهای مخاطب، فارسی؛ اگر سراسری است خالی")
    provinces: list[str] = Field(default_factory=list, description="استان‌های مخاطب از فهرست داده‌شده")
    scope: str = Field("national", description="city | province | national")
    audience: str = Field("general", description="|".join(AUDIENCES))
    plan_ids: list[str] = Field(default_factory=list, max_length=6, description="شناسه‌ی دسته‌های نقشه که مخاطب این کانال احتمالاً می‌خرد")
    relevant: bool = Field(True, description="آیا تبلیغ کالای صنعتی/کشاورزی/ساختمانی در این کانال معنی دارد؟")


class _Tags(BaseModel):
    items: list[_Tag]


def tag_channels(deadline: float, batch: int = 12, mock: bool = False) -> dict:
    from app.brain.llm import get_llm
    from app.config import get_settings
    from app.plan.data import province_names
    with get_session() as db:
        rows = (db.query(Channel).filter(Channel.tagged_at.is_(None), Channel.status.in_(["checked", "new"]))
                .order_by(Channel.score.desc().nullslast()).limit(batch * 3).all())
        items = [(c.id, c.username, c.title, c.description, c.sample) for c in rows]
        plans = db.query(PlanCategory.id, PlanCategory.name).filter(PlanCategory.status.in_(["base", "approved"])).all()
    if not items:
        return {"tagged": 0}
    plan_list = "\n".join(f"{p.id}: {_PAREN.sub('', p.name)[:70]}" for p in plans)
    provs = "، ".join(province_names())
    done = 0
    for i in range(0, len(items), batch):
        if time.monotonic() > deadline:
            break
        chunk = items[i:i + batch]
        if mock:
            tags = [_Tag(id=c[0]) for c in chunk]
        else:
            text = "\n\n".join(f"[{cid}] @{u} — {t or ''}\nتوضیح: {(d or '')[:300]}\nپست‌ها: {(s or '')[:900]}"
                               for cid, u, t, d, s in chunk)
            system = ("تو تحلیل‌گر رسانه‌ی ایرانی هستی. برای هر کانال تلگرام/ایتا بگو مخاطبش کجاست و چه کسانی‌اند، "
                      "تا فروشگاه «کاروان صنعت» (کالای صنعتی، کشاورزی و ساختمانی) بداند تبلیغ هر کالا را کجا بدهد.\n"
                      f"استان‌ها: {provs}\nانواع مخاطب: {', '.join(f'{k}={v}' for k, v in AUDIENCES.items())}\n"
                      f"دسته‌های نقشه:\n{plan_list}\n"
                      "قواعد: اگر نام یا محتوا به شهر خاصی اشاره دارد scope=city و آن شهر و استانش را بنویس. "
                      "کانال خبری عمومی یک شهر برای همه‌ی دسته‌ها مفید است ولی plan_ids را فقط برای دسته‌هایی بنویس که "
                      "واقعاً به مخاطبش می‌خورد (مثلاً شهر کشاورزی ← بذر، آبیاری، پمپ چاه). کانال سرگرمی، سیاسی، "
                      "شرط‌بندی، مهاجرت یا ارز دیجیتال ← relevant=false. خروجی فقط JSON.")
            try:
                r = get_llm(get_settings().llm_fast_model).generate_json(
                    system, text + f"\n\nاسکیما: {_Tags.model_json_schema()}", _Tags)
                tags = r.data.items
            except Exception as e:  # noqa: BLE001
                log.warning("برچسب‌زنی ناموفق: %s", e)
                break
        valid_plans = {p.id for p in plans}
        valid_provs = set(province_names())
        with get_session() as db:
            for tg in tags:
                ch = db.get(Channel, tg.id)
                if not ch:
                    continue
                ch.cities = [c.strip()[:40] for c in tg.cities][:8]
                ch.provinces = [p for p in tg.provinces if p in valid_provs][:6] or (ch.provinces or [])
                ch.scope = tg.scope if tg.scope in ("city", "province", "national") else "national"
                ch.audience = tg.audience if tg.audience in AUDIENCES else "general"
                ch.plan_ids = [p for p in tg.plan_ids if p in valid_plans][:6]
                ch.relevant = bool(tg.relevant) and ch.audience != "irrelevant"
                ch.tagged_at = _now()
                done += 1
            db.commit()
    return {"tagged": done}


# ---------------- جدول پوشش ----------------
def _fit(ch: Channel, plan_id: str, group: str | None, province: str) -> float:
    """این کانال برای «دسته در استان» چقدر مناسب است (۰ تا ۱)."""
    if ch.relevant is False or ch.status == "blocked":
        return 0.0
    in_prov = province in (ch.provinces or [])
    topical = plan_id in (ch.plan_ids or [])
    if in_prov and topical:
        return 1.0
    if in_prov and ch.audience in ("local_news", "classifieds", "trade"):
        return 0.6          # کانال عمومی همان شهر/استان
    if topical and (ch.scope == "national" or not ch.provinces):
        return 0.5          # کانال تخصصی سراسری
    return 0.0


def coverage(months_ahead: int = 1, limit: int = 30) -> dict:
    from app.plan.planner import recommendations
    from app.plan.data import today_jalali
    m = today_jalali().month
    recs = []
    seen = set()
    for k in range(months_ahead + 1):
        for r in recommendations(limit=limit, month=(m + k - 1) % 12 + 1)["items"]:
            key = (r["plan_id"], r["segment_index"])
            if key not in seen:
                seen.add(key)
                recs.append(r)
    with get_session() as db:
        chans = db.query(Channel).filter(Channel.status != "blocked").all()
        groups = dict(db.query(PlanCategory.id, PlanCategory.group).all())
        cells = []
        for r in recs:
            for prov in [re.sub(r"\s*\(.*?\)", "", p).strip() for p in r.get("provinces") or []][:6]:
                good = sorted(((round(_fit(c, r["plan_id"], groups.get(r["plan_id"]), prov) * (c.score or 30) / 100, 3), c)
                               for c in chans), key=lambda x: -x[0])
                good = [(f, c) for f, c in good if f > 0]
                eff = round(sum(min(1.0, f * 1.6) for f, _ in good), 1)
                cells.append({"plan_id": r["plan_id"], "name": r["name"][:60], "segment": r.get("segment"),
                              "province": prov, "score": r["score"], "channels": len(good), "effective": eff,
                              "gap": max(0, TARGET_PER_CELL - len(good)),
                              "top": [{"id": c.id, "username": c.username, "title": c.title, "fit": f}
                                      for f, c in good[:5]]})
    cells.sort(key=lambda x: (-x["gap"], -x["score"]))
    tot = len(cells)
    return {"target": TARGET_PER_CELL, "cells": cells, "covered": sum(1 for c in cells if not c["gap"]), "total": tot}


_PATS = {"AGRI": ["کشاورزی {c}", "کشاورزان {c}", "{c}", "نیازمندی {c}"],
         "WATER": ["کشاورزی {c}", "کشاورزان {c}", "{c}", "نیازمندی {c}"],
         "BUILD": ["ساختمان {c}", "مصالح ساختمانی {c}", "{c}", "نیازمندی {c}"],
         "PIPE": ["ساختمان {c}", "تاسیسات {c}", "{c}", "نیازمندی {c}"]}
_PATS_DEFAULT = ["صنعت {c}", "شهرک صنعتی {c}", "{c}", "بازار ابزار {c}"]


def gap_queries(max_queries: int = 40) -> list[dict]:
    """عبارت‌های جست‌وجوی گوگل برای خانه‌های خالی جدول پوشش (اول پرامتیازترها)."""
    cov = coverage()
    out, seen = [], set()
    with get_session() as db:
        plans = {p.id: p for p in db.query(PlanCategory).all()}
    for cell in cov["cells"]:
        if not cell["gap"]:
            continue
        p = plans.get(cell["plan_id"])
        if not p:
            continue
        seg = next((s for s in p.segments or [] if s.get("name") == cell["segment"]), None) or {}
        cities = _cities_of(seg, cell["province"]) or [cell["province"]]
        pats = _PATS.get(p.group, _PATS_DEFAULT)
        for c in cities[:2]:
            for pat in pats[:3]:
                q = "site:t.me " + pat.format(c=c)
                if q not in seen:
                    seen.add(q)
                    out.append({"q": q, "plan_id": cell["plan_id"], "province": cell["province"], "city": c})
        if len(out) >= max_queries:
            break
    return out[:max_queries]


def _cities_of(seg: dict, province: str) -> list[str]:
    provs = [re.sub(r"\s*\(.*?\)", "", x).strip() for x in seg.get("provinces") or []]
    parts = (seg.get("cities") or "").split("|")
    if len(parts) == len(provs) and province in provs:
        part = parts[provs.index(province)]
    else:
        part = parts[0] if parts else ""
    return [re.sub(r"\s*\(.*?\)", "", c).strip() for c in re.split(r"[،,]", part) if c.strip()][:3]


# ---------------- اجرای دوره‌ای ----------------
def run(budget_sec: int = 240, mock: bool = False) -> dict:
    deadline = time.monotonic() + budget_sec
    stats = {"at": _now().isoformat()}
    for name, fn in (("tag", lambda: tag_channels(min(deadline, time.monotonic() + 90), mock=mock)),
                     ("check", lambda: check_candidates(deadline)),
                     ("snowball", lambda: snowball_step(deadline))):
        if time.monotonic() > deadline:
            break
        try:
            stats[name] = fn()
        except Exception as e:  # noqa: BLE001
            stats[name] = {"error": str(e)[:200]}
            log.warning("شکارچی %s: %s", name, e)
    hist = (get_setting("hunter.runs") or [])[-30:]
    hist.append(stats)
    set_setting("hunter.runs", hist)
    return stats


def status() -> dict:
    with get_session() as db:
        cand = dict(db.query(ChannelCandidate.status, func.count()).group_by(ChannelCandidate.status).all())
        by_src = dict(db.query(Channel.source, func.count()).group_by(Channel.source).all())
        total = db.query(Channel).count()
        tagged = db.query(Channel).filter(Channel.tagged_at.isnot(None)).count()
        relevant = db.query(Channel).filter(Channel.relevant.is_(True)).count()
        aud = dict(db.query(Channel.audience, func.count()).group_by(Channel.audience).all())
        provs = Counter()
        for (pv,) in db.query(Channel.provinces).filter(Channel.relevant.isnot(False)).all():
            for x in pv or []:
                provs[x] += 1
    return {"candidates": cand, "channels": total, "by_source": {str(k or "manual"): v for k, v in by_src.items()},
            "tagged": tagged, "relevant": relevant, "audience": {str(k): v for k, v in aud.items()},
            "provinces": provs.most_common(31), "runs": (get_setting("hunter.runs") or [])[-5:],
            "paused": bool(get_setting("hunter.paused"))}
