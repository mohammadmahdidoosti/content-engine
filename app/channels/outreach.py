"""
پیام به ادمین کانال‌ها برای گرفتن تعرفه‌ی تبلیغ.

جریان کار:
۱. پیش‌نویس: برای کانال‌های یک کمپین، به ازای هر «آیدی سفارش تبلیغ» یک پیام ساخته می‌شود
   (اگر یک نفر ادمین چند کانال است، همه در یک پیام).
۲. تأیید: صاحب فروشگاه در پنل پیام‌ها را می‌بیند/ویرایش می‌کند و «تأیید ارسال» را می‌زند. بدون تأیید هیچ پیامی نمی‌رود.
۳. ارسال: افزونه‌ی کروم خود او (با حساب تلگرام خودش) آهسته می‌فرستد: حداکثر ۳ پیام در هر اجرا، ۴ تا ۸ دقیقه فاصله،
   حداکثر DAILY_CAP پیام در روز؛ به هر آیدی فقط یک بار؛ با اولین هشدار تلگرام همه‌چیز متوقف می‌شود.
۴. جواب: افزونه فقط گفتگوی همین ادمین‌ها را باز می‌کند و جوابشان را می‌خواند؛ هوش مصنوعی قیمت/مدت/ساعت را
   بیرون می‌کشد و روی کانال و تبلیغ‌های کمپین می‌نشاند.

هیچ قول پرداخت یا رزروی داده نمی‌شود؛ پیام فقط تعرفه می‌پرسد.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.db.session import get_session, get_setting, set_setting

log = logging.getLogger("channels.outreach")
DAILY_CAP = 15
PER_RUN = 3
REPLY_WINDOW_DAYS = 7


def _now():
    return datetime.now(timezone.utc)


class Outreach(Base):
    __tablename__ = "outreach"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, index=True)   # پروژه‌ی جدا (channel_ids ← project_channels)
    platform: Mapped[str] = mapped_column(String(20), default="telegram")
    handle: Mapped[str] = mapped_column(String(80), index=True)          # بدون @
    peer: Mapped[str | None] = mapped_column(String(40))                  # شناسه‌ی گفتگو در تلگرام (برای خواندن جواب)
    channel_ids: Mapped[list] = mapped_column(JSON, default=list)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft | approved | sent | replied | failed | skipped | manual
    error: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply: Mapped[str | None] = mapped_column(Text)
    reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parsed: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _handle_of(ch) -> str | None:
    for x in [ch.admin_contact] + list(ch.ad_contacts or []):
        m = re.match(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$", (x or "").strip())
        if m and m.group(1).lower() != (ch.username or "").lower() and not m.group(1).lower().endswith("bot"):
            return m.group(1)
    return None


_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]+")


def _clean_title(t: str) -> str:
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", t or "")).strip(" |-")


def message_text(titles: list[str]) -> str:
    from app.config import get_settings
    s = get_settings()
    titles = [_clean_title(t) for t in titles]
    site = re.sub(r"^https?://(www\.)?", "", s.brand_site_url).rstrip("/")
    where = f"کانال «{titles[0]}»" if len(titles) == 1 else "کانال‌های " + "، ".join(f"«{t}»" for t in titles)
    return (f"سلام، وقت بخیر\n"
            f"از فروشگاه {s.brand_name} ({site}) هستیم؛ ابزار و ماشین‌آلات کشاورزی و صنعتی.\n"
            f"برای تبلیغ در {where} لطفاً تعرفه‌ی یک پست (عکس + متن) را بفرمایید:\n"
            f"- مدت ماندن پست و امکان پین\n- ساعت‌های خالی این هفته\nممنون")


def project_text(intro: str, titles: list[str], kind: str = "price") -> str:
    titles = [_clean_title(t) for t in titles]
    where = f"کانال «{titles[0]}»" if len(titles) == 1 else "کانال‌های " + "، ".join(f"«{t}»" for t in titles)
    if kind == "partner":       # همکاری رسانه‌ای (انجمن‌ها و شوراها معمولاً تبلیغ نمی‌فروشند)
        return (f"سلام، وقت بخیر\n{intro.strip()}\n"
                f"فکر می‌کنیم این مسابقه برای مخاطبان {where} مفید است و خوشحال می‌شویم به‌عنوان «حامی رسانه‌ای» "
                f"معرفی‌اش کنید.\nاگر امکان همکاری هست بفرمایید چه شرایطی برایتان مناسب است تا هماهنگ کنیم.\nممنون")
    return (f"سلام، وقت بخیر\n{intro.strip()}\n"
            f"برای تبلیغ در {where} لطفاً تعرفه‌ی یک پست (عکس + متن) را بفرمایید:\n"
            f"- مدت ماندن پست و امکان پین\n- ساعت‌های خالی این هفته\nممنون")


def draft_project(project_id: int, channel_ids: list[int], kind: str = "price") -> dict:
    """پیش‌نویس برای کانال‌های انتخاب‌شده‌ی یک پروژه‌ی جدا (با معرفی همان پروژه، نه کاروان صنعت)."""
    from app.projects import Project, ProjectChannel
    made = skipped = 0
    with get_session() as db:
        p = db.get(Project, project_id)
        if not p or not (p.intro or "").strip():
            raise ValueError("اول «معرفی فرستنده» را برای این پروژه بنویسید (مثلاً «از دبیرخانه‌ی مسابقه‌ی … هستیم»)")
        chans = [c for c in db.query(ProjectChannel).filter(ProjectChannel.project_id == project_id,
                                                            ProjectChannel.id.in_([int(i) for i in channel_ids])).all()]
        groups: dict[str, list] = {}
        no_contact = []
        for c in chans:
            h = _handle_of(type("C", (), {"admin_contact": None, "ad_contacts": c.ad_contacts, "username": c.username})())
            if not h:
                no_contact.append(c.title or c.username)
                continue
            groups.setdefault(h, []).append(c)
        recent = _now() - timedelta(days=30)
        for h, cs in groups.items():
            old = [o for o in db.query(Outreach).all() if o.handle.lower() == h.lower()]
            if any(o.project_id == project_id for o in old) or any(
                    o.status in ("sent", "replied") and _aware(o.sent_at) and _aware(o.sent_at) > recent for o in old):
                skipped += 1
                continue
            db.add(Outreach(project_id=project_id, platform="telegram", handle=h, channel_ids=[c.id for c in cs],
                            text=project_text(p.intro, [c.title or c.username for c in cs], kind), status="draft"))
            made += 1
        db.commit()
    return {"made": made, "skipped": skipped, "no_contact": no_contact}


def draft(campaign_id: int) -> dict:
    """برای کانال‌های تبلیغ‌های این کمپین پیش‌نویس می‌سازد (تکراری نمی‌سازد)."""
    from app.db.marketing import Channel, Placement
    made = skipped = 0
    with get_session() as db:
        chans = [db.get(Channel, pl.channel_id) for pl in db.query(Placement).filter(Placement.campaign_id == campaign_id).all()
                 if pl.channel_id]
        groups: dict[tuple, list] = {}
        no_contact = []
        for ch in chans:
            if not ch:
                continue
            h = _handle_of(ch)
            if not h:
                no_contact.append(ch.title or ch.username)
                continue
            groups.setdefault((ch.platform, h.lower(), h), []).append(ch)
        recent = _now() - timedelta(days=30)
        for (plat, hl, h), cs in groups.items():
            old = [o for o in db.query(Outreach).filter(Outreach.platform == plat).all() if o.handle.lower() == hl]
            if any(o.campaign_id == campaign_id for o in old) or any(
                    o.status in ("sent", "replied") and _aware(o.sent_at) and _aware(o.sent_at) > recent for o in old):
                skipped += 1
                continue
            db.add(Outreach(campaign_id=campaign_id, platform=plat, handle=h, channel_ids=[c.id for c in cs],
                            text=message_text([c.title or c.username for c in cs]),
                            status="draft" if plat == "telegram" else "manual"))
            made += 1
        db.commit()
    return {"made": made, "skipped": skipped, "no_contact": no_contact}


def _row(o: Outreach, chans: dict) -> dict:
    return {"id": o.id, "campaign_id": o.campaign_id, "project_id": o.project_id, "platform": o.platform, "handle": o.handle, "status": o.status,
            "text": o.text, "error": o.error, "reply": o.reply, "parsed": o.parsed,
            "channels": [{"id": i, "title": (chans[i].title or chans[i].username) if i in chans else str(i)}
                         for i in o.channel_ids or []],
            "sent_at": o.sent_at.isoformat() if o.sent_at else None,
            "reply_at": o.reply_at.isoformat() if o.reply_at else None}


def listing(campaign_id: int | None = None, project_id: int | None = None) -> dict:
    from app.db.marketing import Channel
    from app.projects import ProjectChannel
    with get_session() as db:
        q = db.query(Outreach)
        if campaign_id:
            q = q.filter(Outreach.campaign_id == campaign_id)
        elif project_id:
            q = q.filter(Outreach.project_id == project_id)
        else:
            q = q.filter(Outreach.project_id.is_(None))
        rows = q.order_by(Outreach.id).all()
        ids = {i for o in rows for i in (o.channel_ids or [])}
        model = ProjectChannel if project_id else Channel
        chans = {c.id: c for c in db.query(model).filter(model.id.in_(ids)).all()} if ids else {}
        return {"items": [_row(o, chans) for o in rows], "paused": get_setting("outreach.paused"),
                "sent_today": sent_today(db), "daily_cap": DAILY_CAP}


def update(oid: int, b: dict) -> dict:
    with get_session() as db:
        o = db.get(Outreach, oid)
        if not o:
            raise ValueError("پیام پیدا نشد")
        if "text" in b and o.status in ("draft", "approved", "manual"):
            t = str(b["text"]).strip()
            if not t or len(t) > 1500:
                raise ValueError("متن پیام خالی یا خیلی طولانی است")
            o.text = t
        if b.get("status") in ("skipped", "draft", "sent") and o.status in ("draft", "approved", "manual", "skipped", "failed"):
            o.status = b["status"]          # «sent» دستی: وقتی خودش از گوشی فرستاده
            if o.status == "sent":
                o.sent_at = _now()
        db.commit()
        return {"ok": True}


def approve(ids: list[int]) -> dict:
    n = 0
    with get_session() as db:
        for o in db.query(Outreach).filter(Outreach.id.in_([int(i) for i in ids]), Outreach.status == "draft").all():
            o.status, o.approved_at, o.error = "approved", _now(), None
            n += 1
        db.commit()
    return {"approved": n}


def resume() -> dict:
    set_setting("outreach.paused", None)
    return {"ok": True}


def sent_today(db) -> int:
    start = _now() - timedelta(hours=24)
    return sum(1 for o in db.query(Outreach).filter(Outreach.sent_at.isnot(None)).all() if _aware(o.sent_at) > start)


def tasks() -> dict:
    """برای افزونه: پیام‌های تأییدشده برای ارسال + پیام‌های فرستاده‌شده برای خواندن جواب."""
    with get_session() as db:
        paused = get_setting("outreach.paused")
        left = max(0, DAILY_CAP - sent_today(db))
        send = [] if paused else [
            {"id": o.id, "handle": o.handle, "lines": o.text.split("\n")}
            for o in db.query(Outreach).filter(Outreach.status == "approved", Outreach.platform == "telegram")
            .order_by(Outreach.approved_at, Outreach.id).limit(min(PER_RUN, left)).all()]
        since = _now() - timedelta(days=REPLY_WINDOW_DAYS)
        check = [{"id": o.id, "handle": o.handle, "peer": o.peer,
                  "first": o.text.split("\n")[1] if "\n" in o.text else o.text}
                 for o in db.query(Outreach).filter(Outreach.status.in_(["sent", "replied"]), Outreach.platform == "telegram").all()
                 if _aware(o.sent_at) and _aware(o.sent_at) > since][:20]
    return {"send": send, "check": check, "paused": paused, "gap_min": [4, 8]}


_STOP = re.compile(r"too many|flood|spam|limit|محدود|اسپم|بیش از حد", re.I)


def ingest_results(rows: list) -> dict:
    """[{id, ok, error, stop}] از افزونه."""
    n, stop = 0, None
    with get_session() as db:
        for r in rows[:20]:
            if not isinstance(r, dict) or not str(r.get("id", "")).isdigit():
                continue
            o = db.get(Outreach, int(r["id"]))
            if not o or o.status != "approved":
                continue
            err = str(r.get("error") or "")[:300] or None
            if str(r.get("peer") or "").lstrip("-").isdigit():
                o.peer = str(r["peer"])[:40]
            if r.get("ok"):
                o.status, o.sent_at, o.error = "sent", _now(), None
            elif r.get("skip"):
                o.status, o.error = "skipped", err
            else:
                o.status, o.error = "failed", err
            if r.get("stop") or (err and _STOP.search(err)):
                stop = err or "هشدار تلگرام"
            n += 1
        db.commit()
    if stop:
        set_setting("outreach.paused", {"at": _now().isoformat(), "reason": stop})
    _mark_contacted()
    return {"updated": n}


def _mark_contacted():
    """تبلیغ‌های کمپین که به ادمینشان پیام رفته ← «تماس با ادمین»."""
    from app.db.marketing import Placement
    with get_session() as db:
        for o in db.query(Outreach).filter(Outreach.status.in_(["sent", "replied"]), Outreach.campaign_id.isnot(None)).all():
            for pl in db.query(Placement).filter(Placement.campaign_id == o.campaign_id,
                                                 Placement.channel_id.in_(o.channel_ids or [0])).all():
                if pl.status == "draft":
                    pl.status = "contacted"
        db.commit()


class _Quote(BaseModel):
    price_toman: int | None = Field(None, description="قیمت یک پست معمولی به تومان (اگر «۸۰۰» گفته و منظور هزار تومان است، ۸۰۰۰۰۰)")
    pin_price_toman: int | None = Field(None, description="قیمت پست با پین، به تومان")
    duration_hours: int | None = Field(None, description="مدت ماندن پست به ساعت (۲۴ ساعته=۲۴، دائمی=0)")
    slots: str | None = Field(None, description="ساعت‌ها/روزهای خالی به زبان خودش، کوتاه")
    per_channel: list[dict] = Field(default_factory=list, description="اگر برای چند کانال قیمت جدا داد: [{title, price_toman}]")
    summary: str = Field("", description="خلاصه‌ی یک‌خطی جواب ادمین")


def parse_reply(text: str, titles: list[str], mock: bool = False) -> dict:
    if mock:
        nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))]
        v = max(nums) if nums else None
        if v and v < 10000:
            v *= 1000
        return {"price_toman": v, "summary": text[:80]}
    from app.brain.llm import get_llm
    from app.config import get_settings
    system = ("جواب ادمین کانال تلگرام به درخواست تعرفه‌ی تبلیغ را می‌خوانی و عددها را دقیق بیرون می‌کشی. "
              "ادمین‌های ایرانی معمولاً قیمت را به هزار تومان می‌گویند (۸۰۰ یعنی ۸۰۰ هزار تومان، «۱.۵ م» یعنی ۱٬۵۰۰٬۰۰۰ تومان)؛ "
              "خروجی همیشه به تومان. اگر قیمتی نگفته، خالی بگذار. چیزی نساز. خروجی فقط JSON.")
    user = f"کانال‌ها: {'، '.join(titles)}\n\nجواب ادمین:\n{text[:2000]}\n\nاسکیما: {_Quote.model_json_schema()}"
    r = get_llm(get_settings().llm_fast_model).generate_json(system, user, _Quote)
    return r.data.model_dump()


def ingest_replies(rows: list, mock: bool = False) -> dict:
    """[{id, text}] ← جواب ادمین؛ قیمت روی کانال و تبلیغ‌های همان کمپین می‌نشیند."""
    from app.db.marketing import Channel, Placement
    from app.projects import ProjectChannel
    n = 0
    for r in rows[:30]:
        if not isinstance(r, dict) or not str(r.get("id", "")).isdigit():
            continue
        text = str(r.get("text") or "").strip()[:3000]
        with get_session() as db:
            o = db.get(Outreach, int(r["id"]))
            if not o or not text or (o.reply or "") == text:
                continue
            M = ProjectChannel if o.project_id else Channel
            titles = [(db.get(M, i).title or db.get(M, i).username) for i in o.channel_ids or [] if db.get(M, i)]
        try:
            q = parse_reply(text, titles, mock=mock)
        except Exception as e:  # noqa: BLE001
            log.warning("خواندن جواب ادمین ناموفق: %s", e)
            q = {"summary": "خوانده نشد؛ متن جواب را ببینید"}
        with get_session() as db:
            o = db.get(Outreach, int(r["id"]))
            o.reply, o.reply_at, o.parsed, o.status = text, _now(), q, "replied"
            if str(r.get("peer") or "").lstrip("-").isdigit():
                o.peer = str(r["peer"])[:40]
            per = {str(x.get("title") or "").strip(): x.get("price_toman") for x in q.get("per_channel") or []}
            M = ProjectChannel if o.project_id else Channel
            for cid in o.channel_ids or []:
                ch = db.get(M, cid)
                if not ch:
                    continue
                price = per.get((ch.title or "").strip()) or q.get("price_toman")
                if price and 10000 <= int(price) <= 500_000_000:
                    ch.price_toman = int(price)
                    if o.project_id:
                        continue
                    for pl in db.query(Placement).filter(Placement.campaign_id == o.campaign_id,
                                                         Placement.channel_id == cid).all():
                        if not pl.cost_toman:
                            pl.cost_toman = int(price)
            db.commit()
        n += 1
    return {"replies": n}
