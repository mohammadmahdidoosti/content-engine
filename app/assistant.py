"""
دستیار قیمت و مشاوره (مغز مشترک ربات تلگرام، ربات بله و پنجره‌ی گفت‌وگوی سایت).

۱. سؤال را می‌گیرد و در محصولات همگام‌شده‌ی سایت جست‌وجو می‌کند.
۲. با مدل سبک، جوابی کوتاه می‌نویسد که فقط از کالاها و قیمت‌های واقعی استفاده می‌کند
   و مخاطب را به ثبت سفارش در سایت می‌برد (فرایند فروش کاروان صنعت).
۳. هر سؤال (با شهر، اگر گفته شود) ثبت می‌شود؛ این داده نشان می‌دهد مردم کجا دنبال چه هستند.
"""
import hashlib
import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, or_
from sqlalchemy.orm import Mapped, mapped_column

from app.brain.llm import get_llm
from app.config import get_settings
from app.db.marketing import Product
from app.db.models import Base
from app.db.session import get_session

log = logging.getLogger("assistant")
FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_STOP = {"قیمت", "چند", "چنده", "است", "هست", "دارید", "دارین", "موجود", "میخوام", "می‌خواهم", "سلام", "لطفا",
         "لطفاً", "برای", "یک", "یه", "این", "اون", "رو", "را", "با", "و", "از", "به", "که", "چه", "چی", "مدل",
         "خرید", "میخواستم", "میخواهم", "دارم", "ممنون", "مرسی", "کجا", "چطور", "آیا", "هم", "تا", "در"}


class AssistantLog(Base):
    __tablename__ = "assistant_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    channel: Mapped[str] = mapped_column(String(20))           # site | telegram | bale | panel
    user: Mapped[str | None] = mapped_column(String(32))        # هش (بدون ذخیره‌ی شناسه‌ی واقعی)
    question: Mapped[str] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(80))
    intent: Mapped[str | None] = mapped_column(String(20))
    product_ids: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[float | None] = mapped_column(Float)


class _Answer(BaseModel):
    answer: str = Field(..., max_length=900, description="جواب کوتاه فارسی برای پیام‌رسان؛ حداکثر ۶ خط")
    product_ids: list[int] = Field(default_factory=list, max_length=4, description="کالاهایی که پیشنهاد دادی")
    city: str | None = Field(None, description="اگر کاربر شهر یا استانش را گفت")
    intent: str = Field("other", description="price | advice | stock | shipping | other")


def _tokens(q: str) -> list[str]:
    q = (q or "").translate(FA_DIGITS).replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک")
    q = re.sub(r"[؟?!.,،:؛()\[\]\"'«»]", " ", q)
    return [t for t in q.split() if (len(t) >= 2 or t.isdigit()) and t not in _STOP][:8]


_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _variants(t: str) -> list[str]:
    """نام کالاها گاهی عدد فارسی دارند و گاهی لاتین."""
    return list({t, t.translate(_TO_FA)})


def _match(name: str, t: str) -> bool:
    n = (name or "").replace("\u200c", " ")
    return any(v in n for v in _variants(t))


def search_products(q: str, limit: int = 8) -> list[Product]:
    toks = _tokens(q)
    if not toks:
        return []
    words = [t for t in toks if not t.isdigit()] or toks
    with get_session() as db:
        # اول: کالاهایی که همه‌ی کلمه‌ها را دارند؛ اگر کم بود، آن‌هایی که بیشترشان را دارند
        qry = db.query(Product)
        for t in words:
            qry = qry.filter(or_(*[Product.name.ilike(f"%{v}%") for v in _variants(t)]))
        cand = qry.limit(600).all()
        if len(cand) < limit and len(words) > 1:
            cand += (db.query(Product).filter(or_(*[Product.name.ilike(f"%{v}%") for t in words for v in _variants(t)]))
                     .limit(600).all())
    seen, scored = set(), []
    for p in cand:
        if p.id in seen:
            continue
        seen.add(p.id)
        hit = sum(1 for t in toks if _match(p.name, t))
        if not hit:
            continue
        scored.append((hit / len(toks) + (0.15 if p.in_stock else 0) + (0.05 if p.price else 0), p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:limit]]


def _user_hash(channel: str, uid: str | None) -> str | None:
    if not uid:
        return None
    return hashlib.sha256(f"{channel}|{uid}".encode()).hexdigest()[:32]


def ask(question: str, channel: str = "site", user_id: str | None = None, mock: bool = False) -> dict:
    s = get_settings()
    question = (question or "").strip()[:500]
    prods = search_products(question)
    lines = "\n".join(
        f"[{p.id}] {p.name} | {f'{p.price:,} تومان' if p.price else 'قیمت در سایت'} | "
        f"{'موجود' if p.in_stock else 'ناموجود'} | {p.permalink}" for p in prods) or "(کالای مرتبطی پیدا نشد)"
    system = (
        f"تو دستیار فروش «{s.brand_name}» ({s.brand_site_url}) هستی؛ {s.brand_niche}.\n"
        f"لحن: {s.brand_tone}\nفرایند فروش: {s.sales_process}\n"
        "قوانین:\n- فقط از کالاها و قیمت‌های لیست زیر استفاده کن؛ قیمت یا مشخصات نساز.\n"
        "- جواب کوتاه بده (حداکثر ۶ خط)، مناسب پیام‌رسان.\n"
        "- اگر کالای مناسب هست: نام و قیمت روز را بگو و بگو «سفارش را در سایت ثبت کنید؛ کارشناس ما برای تأیید و "
        "فاکتور تماس می‌گیرد». لینک را ننویس؛ خودکار اضافه می‌شود.\n"
        "- اگر سؤال فنی است (مثلاً کدام پمپ برای چاه ۸۰ متری)، با احتیاط راهنمایی کن و اطلاعات لازم را بپرس "
        "(عمق، دبی، تک‌فاز/سه‌فاز و ...). اگر مطمئن نیستی، بگو کارشناس راهنمایی می‌کند.\n"
        f"- اگر کالایی پیدا نشد: بگو در سایت جست‌وجو کند یا با پشتیبانی تماس بگیرد ({s.brand_phone}).\n"
        "- اگر شهر را نگفته و درباره‌ی ارسال پرسید، بگو به همه‌ی شهرها ارسال داریم و شهرش را بپرس.\n"
        "خروجی فقط JSON مطابق اسکیما.")
    user = f"سؤال مشتری: {question}\n\nکالاهای مرتبط در سایت:\n{lines}\n\nاسکیما: {_Answer.model_json_schema()}"
    cost = 0.0
    try:
        if mock:
            a = _Answer(answer=("این کالاها را داریم:\n" + "\n".join(f"• {p.name}: {p.price:,} تومان" for p in prods[:3] if p.price))
                        if prods else "کالای مرتبطی پیدا نکردم.", product_ids=[p.id for p in prods[:3]], intent="price")
        else:
            r = get_llm(s.llm_fast_model).generate_json(system, user, _Answer)
            a, cost = r.data, (r.cost_usd or 0.0)
    except Exception as e:  # noqa: BLE001
        log.warning("دستیار: خطای LLM: %s", e)
        a = _Answer(answer=("این کالاها را پیدا کردم؛ قیمت روز و ثبت سفارش در لینک‌های زیر:" if prods else
                            f"کالای مرتبطی پیدا نکردم. لطفاً در سایت جست‌وجو کنید یا با {s.brand_phone} تماس بگیرید."),
                    product_ids=[p.id for p in prods[:3]], intent="price")
    by_id = {p.id: p for p in prods}
    chosen = [by_id[i] for i in a.product_ids if i in by_id] or prods[:3]
    links = [{"id": p.id, "name": p.name, "price": p.price, "url": p.permalink, "image": p.image,
              "in_stock": p.in_stock} for p in chosen[:4]]
    with get_session() as db:
        db.add(AssistantLog(ts=datetime.now(timezone.utc), channel=channel, user=_user_hash(channel, user_id),
                            question=question, city=(a.city or None), intent=a.intent[:20] if a.intent else None,
                            product_ids=[x["id"] for x in links], answer=a.answer, cost_usd=cost))
        db.commit()
    return {"answer": a.answer, "products": links, "city": a.city, "intent": a.intent}


def stats(limit: int = 50) -> dict:
    from collections import Counter
    with get_session() as db:
        rows = db.query(AssistantLog).order_by(AssistantLog.id.desc()).limit(2000).all()
        recent = [{"ts": r.ts.isoformat() if r.ts else None, "channel": r.channel, "question": r.question,
                   "city": r.city, "intent": r.intent, "answer": r.answer, "products": r.product_ids}
                  for r in rows[:limit]]
        cities = Counter(r.city for r in rows if r.city)
        prods = Counter(i for r in rows for i in (r.product_ids or []))
        names = {p.id: p.name for p in db.query(Product).filter(Product.id.in_(list(prods)[:200])).all()} if prods else {}
    return {"total": len(rows), "by_channel": dict(Counter(r.channel for r in rows)),
            "top_cities": cities.most_common(15),
            "top_products": [{"id": i, "name": names.get(i), "count": n} for i, n in prods.most_common(15)],
            "recent": recent}
