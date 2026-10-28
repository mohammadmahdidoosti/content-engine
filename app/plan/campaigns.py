"""کمپین‌ها، جایگاه‌های تبلیغ (کمپین × کانال)، ساخت متن تبلیغ با LLM و گزارش بازده."""
import logging
from collections import Counter, defaultdict

from pydantic import BaseModel, Field

from app.brain.llm import get_llm
from app.config import get_settings
from app.db.marketing import Campaign, Channel, Click, PlanCategory, Placement, Product, TrackedOrder, WcCategory
from app.db.session import get_session
from app.plan.data import groups, month_name
from app.plan.planner import channel_keywords
from app.text_utils import normalize_obj
from app.tracking.links import new_code, short_url, utm_url

log = logging.getLogger("campaigns")


# ---------------- کمپین ----------------
def _landing_url(db, plan_id: str) -> str | None:
    """صفحه‌ی دسته‌ای از سایت که بیشترین کالای این دسته‌ی نقشه را دارد."""
    cats = db.query(WcCategory).filter(WcCategory.plan_id == plan_id).all()
    if not cats:
        return None
    by_id = {c.id: c for c in cats}
    cnt = Counter()
    for (ids,) in db.query(Product.category_ids).filter(Product.plan_id == plan_id).all():
        for i in ids or []:
            if i in by_id:
                cnt[i] += 1
    best = (cnt.most_common(1)[0][0] if cnt else max(cats, key=lambda c: c.count).id)
    return by_id[best].permalink


def create_campaign(plan_id: str, segment_index: int, budget_toman: int | None = None,
                    city: str | None = None, province: str | None = None) -> Campaign:
    with get_session() as db:
        plan = db.get(PlanCategory, plan_id)
        if plan is None:
            raise ValueError("دسته‌ی نقشه پیدا نشد")
        segs = plan.segments or []
        if not 0 <= segment_index < len(segs):
            raise ValueError("بخش جغرافیایی نامعتبر است")
        seg = segs[segment_index]
        code = new_code("C", 3)
        while db.query(Campaign).filter(Campaign.code == code).first():
            code = new_code("C", 3)
        product_ids = [e["id"] for e in (plan.examples or []) if e.get("id")][:6]
        short = plan.name.split('(')[0].strip()
        camp = Campaign(
            code=code, plan_id=plan.id, segment=seg.get("name"), city=city,
            title=f"{short} — {city}" if city else f"{short} — {seg.get('name')}",
            provinces=[province] if city and province else (seg.get("provinces") or []),
            cities=city or seg.get("cities"), product_ids=product_ids,
            target_url=_landing_url(db, plan.id) or get_settings().brand_site_url, budget_toman=budget_toman,
            status="active",
            brief={
                "group": groups().get(plan.group, plan.group), "buyers": plan.buyers, "angle": plan.angle,
                "time": plan.time, "channels": plan.channels, "note": seg.get("note"),
                "peak": [month_name(m) for m in seg.get("peak") or []],
                "keywords": channel_keywords(plan, {**seg, "cities": city} if city else seg, limit=20),
            },
        )
        db.add(camp)
        db.commit()
        log.info("کمپین %s ساخته شد: %s", code, camp.title)
        return camp


# ---------------- جایگاه تبلیغ ----------------
def create_placement(campaign_id: int, channel_id: int | None) -> Placement:
    with get_session() as db:
        camp = db.get(Campaign, campaign_id)
        if camp is None:
            raise ValueError("کمپین پیدا نشد")
        ch = db.get(Channel, channel_id) if channel_id else None
        code = new_code("K")
        while db.query(Placement).filter(Placement.code == code).first():
            code = new_code("K")
        pl = Placement(code=code, campaign_id=camp.id, channel_id=ch.id if ch else None,
                       cost_toman=ch.price_toman if ch else None,
                       target_url=utm_url(camp.target_url, ch.username if ch else "manual", camp.code, code))
        db.add(pl)
        db.commit()
        return pl


# ---------------- متن تبلیغ ----------------
class AdPost(BaseModel):
    headline: str = Field(..., max_length=90, description="تیتر کوتاه و درگیرکننده با یک ایموجی؛ bold می‌شود")
    body: str = Field(..., max_length=700, description="بدنه‌ی تبلیغ: مشکل/نیاز مخاطب، ۲ تا ۴ کالای واقعی با قیمت، "
                                                      "مزیت‌ها. بدون لینک و بدون هشتگ")
    cta: str = Field(..., max_length=160, description="دعوت به ثبت سفارش در سایت")
    button_text: str = Field(..., max_length=30, description="متن دکمه‌ی لینک، مثلاً «🛒 قیمت روز و ثبت سفارش»")
    short_version: str = Field(..., max_length=350, description="نسخه‌ی کوتاه برای تست A/B (بدون لینک)")
    hashtags: list[str] = Field(default_factory=list, max_length=4)
    featured_product_ids: list[int] = Field(default_factory=list, max_length=4)
    best_time: str = Field("", max_length=120)


def _fmt_price(n: int | None) -> str:
    return f"{n:,} تومان" if n else "قیمت روز در سایت"


def build_ad_prompt(db, pl: Placement) -> tuple[str, str]:
    s = get_settings()
    camp = db.get(Campaign, pl.campaign_id)
    plan = db.get(PlanCategory, camp.plan_id)
    ch = db.get(Channel, pl.channel_id) if pl.channel_id else None
    prods = [db.get(Product, i) for i in camp.product_ids or []]
    prods = [p for p in prods if p]
    if not prods and plan and plan.examples:
        lines = "\n".join(f"- {e.get('name')}" for e in plan.examples[:6])
    else:
        from app.plan.pricing import checks_for
        pc = checks_for([p.id for p in prods])

        def _mk(p):
            c = pc.get(p.id) or {}
            tag = ""
            if c.get("status") == "cheaper" and (c.get("ratio") or 1) <= 0.95:
                tag = f" | ✅ زیر قیمت بازار (کمترین قیمت ترب {c['market_min']:,} تومان در {c.get('shops') or 1} فروشگاه)"
            elif c.get("status") == "only_us":
                tag = " | ✅ فروشنده‌ی کم‌یاب (در ترب فقط ما)"
            return (f"- [{p.id}] {p.name} | {_fmt_price(p.price)} | {'موجود' if p.in_stock else 'ناموجود'}"
                    f"{' | برند ' + p.brand if p.brand else ''}{tag}")
        lines = "\n".join(_mk(p) for p in prods)
    b = camp.brief or {}
    system = (f"تو کپی‌رایتر تبلیغات کانالی (تلگرام، ایتا، بله) برای «{s.brand_name}» هستی: {s.brand_niche}.\n"
              f"لحن: {s.brand_tone}\n"
              f"فرایند فروش: {s.sales_process}\n"
              f"شرایط واقعی فروشگاه (فقط همین‌ها را بگو): {s.brand_facts}\n"
              "قوانین:\n- فقط کالاها و قیمت‌هایی را بیاور که در لیست هست؛ قیمت نساز.\n"
              "- تبلیغ باید برای همان شهر/منطقه و همان خریدار نوشته شود (اسم شهر یا منطقه را بیاور).\n"
              "- کوتاه، خوانا روی موبایل، با ۳ تا ۶ ایموجی کاربردی؛ بدون اغراق و وعده‌ی غیرواقعی.\n"
              "- هیچ شرط فروشی بیرون از «شرایط واقعی فروشگاه» نساز (مثلاً تخفیف، ارسال رایگان، گارانتی، «ارزان‌ترین»). "
              "از آن شرایط هم حداکثر ۲ مورد مرتبط بیاور. «زیر قیمت بازار» فقط برای کالای علامت‌دار ✅.\n"
              "- مخاطب را به ثبت سفارش در سایت هدایت کن («قیمت روز و ثبت سفارش در سایت؛ کارشناس ما برای "
              "تأیید و فاکتور با شما تماس می‌گیرد»)، نه به تماس برای پرسیدن قیمت.\n"
              "- لینک و شماره‌ی تلفن را ننویس؛ بعداً اضافه می‌شود.\n"
              "- اگر کنار کالایی «✅ زیر قیمت بازار» آمده، این مزیت را برجسته کن (مثلاً «زیر قیمت بازار»)؛ "
              "برای کالاهای دیگر ادعای ارزان‌تر بودن نکن.\n"
              "خروجی فقط JSON مطابق اسکیما.")
    user = (f"کمپین: {camp.title}\nگروه: {b.get('group')}\nخریداران: {b.get('buyers')}\n"
            f"زاویه‌ی پیام: {b.get('angle')}\nمنطقه: {camp.segment} — استان‌ها: {'، '.join(camp.provinces or [])}\n"
            f"شهرها: {camp.cities}\nنکته‌ی فصل: {b.get('note')}\nماه‌های اوج: {'، '.join(b.get('peak') or [])}\n"
            f"بهترین ساعت انتشار (از نقشه): {b.get('time')}\n\n"
            f"کانال مقصد: {(ch.title or ch.username) if ch else 'کانال محلی'}"
            f"{' — ' + (ch.description or '')[:200] if ch and ch.description else ''}"
            f"{f' — {ch.members:,} عضو' if ch and ch.members else ''}\n\n"
            f"کالاهای واقعی سایت (شناسه | نام | قیمت | موجودی):\n{lines}\n\n"
            "اسکیمای خروجی:\n" + str(AdPost.model_json_schema()))
    return system, user


def generate_ad(placement_id: int, mock: bool = False) -> None:
    with get_session() as db:
        pl = db.get(Placement, placement_id)
        pl.ad_status, pl.ad_error = "generating", None
        db.commit()
        system, user = build_ad_prompt(db, pl)
    try:
        if mock:
            data = AdPost(headline="🌱 بذر اصل قبل از فصل کاشت", body="نمونه‌ی آزمایشی", cta="ثبت سفارش در سایت",
                          button_text="🛒 قیمت روز", short_version="نمونه", hashtags=["#کشاورزی"])
            cost = 0.0
        else:
            r = get_llm().generate_json(system, user, AdPost)
            data, cost = r.data, r.cost_usd
        ad = normalize_obj(data.model_dump())
        with get_session() as db:
            pl = db.get(Placement, placement_id)
            link = short_url(pl.code) or pl.target_url
            tags = " ".join(h if h.startswith("#") else "#" + h for h in ad.get("hashtags") or [])
            ad["link"] = link
            ad["utm_url"] = pl.target_url
            ad["full_text"] = (f"**{ad['headline']}**\n\n{ad['body']}\n\n{ad['cta']}\n👈 {link}"
                               + (f"\n\n{tags}" if tags else ""))
            ad["short_text"] = f"{ad['short_version']}\n👈 {link}"
            ids = set(ad.get("featured_product_ids") or [])
            camp = db.get(Campaign, pl.campaign_id)
            imgs = []
            for pid in (camp.product_ids or []):
                p = db.get(Product, pid)
                if p and p.image and (not ids or p.id in ids):
                    imgs.append({"id": p.id, "name": p.name, "image": p.image, "url": p.permalink})
            ad["images"] = imgs[:4]
            pl.ad, pl.ad_status, pl.ad_cost_usd = ad, "ready", cost
            db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("ساخت تبلیغ %s ناموفق: %s", placement_id, e)
        with get_session() as db:
            pl = db.get(Placement, placement_id)
            pl.ad_status, pl.ad_error = "failed", str(e)[:500]
            db.commit()


# ---------------- گزارش ----------------
def placement_metrics(db) -> dict[int, dict]:
    clicks = Counter(code for (code,) in db.query(Click.code).all())
    orders = defaultdict(lambda: [0, 0])
    for t in db.query(TrackedOrder).all():
        if t.status in ("cancelled", "failed", "refunded"):
            continue
        orders[t.code][0] += 1
        orders[t.code][1] += t.total_toman or 0
    try:
        from app.tracking.calls import by_ad_code
        logged = by_ad_code()
    except Exception:  # noqa: BLE001
        logged = {}
    out = {}
    for pl in db.query(Placement).all():
        o_auto, rev_auto = orders.get(pl.code, [0, 0])
        lg = logged.get(pl.code) or {"calls": 0, "bought": 0, "amount": 0}
        cost = pl.cost_toman or 0
        o = o_auto + (pl.orders or 0) + lg["bought"]
        rev = rev_auto + (pl.revenue_toman or 0) + lg["amount"]
        c = clicks.get(pl.code, 0)
        n_calls = (pl.calls or 0) + lg["calls"]
        leads = o_auto + (pl.orders or 0) + n_calls          # تماسی که به خرید رسید یک بار شمرده می‌شود
        out[pl.id] = {
            "variant": pl.ad_variant, "clicks": c, "orders_auto": o_auto, "orders": o, "revenue": rev, "calls": n_calls,
            "calls_logged": lg["calls"],
            "leads": leads, "cost": cost,
            "cpc": round(cost / c) if c and cost else None,
            "cost_per_lead": round(cost / leads) if leads and cost else None,
            "roas": round(rev / cost, 2) if cost else None,
        }
    return out


def report() -> dict:
    from app.tracking.offline import margin_rate
    with get_session() as db:
        m = placement_metrics(db)
        camps = {c.id: c for c in db.query(Campaign).all()}
        chans = {c.id: c for c in db.query(Channel).all()}
        by_camp = defaultdict(lambda: Counter())
        by_chan = defaultdict(lambda: Counter())
        by_var = defaultdict(lambda: Counter())
        rows = []
        for pl in db.query(Placement).all():
            x = m[pl.id]
            spent = x["cost"] if pl.status in ("paid", "published", "done") else 0
            camp = camps.get(pl.campaign_id)
            rate = margin_rate(camp.plan_id if camp else None)
            est_profit = round(x["revenue"] * rate) if rate and x["revenue"] else 0
            for agg, key in ((by_camp, pl.campaign_id), (by_chan, pl.channel_id), (by_var, pl.ad_variant or "—")):
                agg[key].update({"spent": spent, "clicks": x["clicks"], "orders": x["orders"],
                                 "revenue": x["revenue"], "calls": x["calls"], "placements": 1,
                                 "est_profit": est_profit})
            ch = chans.get(pl.channel_id)
            rows.append({"id": pl.id, "code": pl.code, "status": pl.status,
                         "campaign": camps[pl.campaign_id].title if pl.campaign_id in camps else None,
                         "channel": (ch.title or ch.username) if ch else "—", **x})

        def fin(d: Counter) -> dict:
            d = dict(d)
            sp = d.get("spent", 0)
            leads = d.get("orders", 0) + d.get("calls", 0)
            d["roas"] = round(d.get("revenue", 0) / sp, 2) if sp else None
            d["cost_per_lead"] = round(sp / leads) if sp and leads else None
            # سود واقعی (فروش × نرخ سود همان دسته از فایل فروش) منهای هزینه‌ی تبلیغ
            d["net_profit"] = d.get("est_profit", 0) - sp if sp else None
            return d
        total = Counter()
        for d in by_camp.values():
            total.update(d)
        return {
            "total": fin(total),
            "campaigns": [{"id": k, "title": camps[k].title if k in camps else "—", **fin(v)} for k, v in by_camp.items()],
            "channels": [{"id": k, "title": ((chans[k].title or chans[k].username) if k in chans else "بدون کانال"),
                          **fin(v)} for k, v in by_chan.items()],
            "variants": [{"id": k, "title": {"full": "نسخه‌ی کامل", "short": "نسخه‌ی کوتاه"}.get(k, "نامشخص"), **fin(v)}
                         for k, v in by_var.items()],
            "placements": rows,
        }
