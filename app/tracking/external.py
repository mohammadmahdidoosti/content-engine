"""
داده‌ی بیرونی از پنل‌هایی که کلید API عمومی ندارند (پنل ترب، سرچ کنسول، آنالیتیکس).

جمع‌آوری در مرورگر صاحب فروشگاه انجام می‌شود (با نشست خودش، فقط خواندنی). صفحه‌ی منبع، پنل را در
پنجره‌ی «پل» (#bridge) باز می‌کند و داده را با postMessage می‌دهد؛ پنل فقط از ORIGINS می‌پذیرد و با توکن
خودش به /api/ext/import می‌فرستد. هیچ کلید یا رمزی بین دو صفحه جابه‌جا نمی‌شود.

منابع:
- torob_clicks : کلیک و هزینه‌ی کلیک هر کالا در ترب  ← به تفکیک دسته‌ی نقشه، خط مبنای کلیک روزانه
- gsc_queries  : عبارت‌های جست‌وجوی گوگل (کلیک، نمایش، رتبه) ← فرصت‌های سئو و موضوع محتوا
- ga_cities    : کاربران سایت به تفکیک شهر (آنالیتیکس)       ← علاقه‌ی هر شهر
- ga_sources   : نشست‌ها به تفکیک منبع/رسانه
"""
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from app.db.session import get_session, get_setting, set_setting

log = logging.getLogger("tracking.external")
SOURCES = {"channel_candidates", "torob_clicks", "torob_yearly", "gsc_queries", "ga_cities", "ga_sources"}
ORIGINS = {"https://panel.torob.com", "https://search.google.com", "https://analytics.google.com", "https://www.google.com"}
_COMMERCIAL = re.compile(r"قیمت|خرید|فروش|نمایندگی|لیست قیمت|بازار|ارزان|عمده")


# ---------------- ورود ----------------
def _slug(url: str) -> str:
    p = unquote(urlparse(url or "").path or "").rstrip("/")
    return p.rsplit("/", 1)[-1].replace("‌", "").lower() if p else ""


def ingest(payload: dict) -> dict:
    src = payload.get("source")
    if src not in SOURCES:
        raise ValueError("منبع نامعتبر")
    rows = payload.get("rows") or []
    if not isinstance(rows, list) or len(rows) > 60000:
        raise ValueError("ردیف‌ها نامعتبر")
    meta = payload.get("meta") or {}
    if src == "channel_candidates":                     # لینک کانال‌ها از نتیجه‌ی جست‌وجوی گوگل
        from app.channels.hunter import ingest_candidates
        res = ingest_candidates(rows, "google")
        return {"source": src, "rows": len(rows), **res}
    if src == "torob_yearly":
        data = _torob_yearly(rows, payload.get("urls") or [], meta)
    else:
        fn = {"torob_clicks": _torob, "gsc_queries": _gsc, "ga_cities": _simple, "ga_sources": _simple}[src]
        data = fn(rows, meta)
    data.update({"source": src, "at": datetime.now(timezone.utc).isoformat(),
                 "from": str(meta.get("from") or "")[:20] or None, "to": str(meta.get("to") or "")[:20] or None})
    set_setting(f"ext.{src}", data)
    _cache.pop(src, None)
    log.info("داده‌ی بیرونی %s: %d ردیف", src, len(rows))
    return {"source": src, "rows": len(rows)}


def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").replace("%", "").strip() or 0)
    except ValueError:
        return 0.0


def _torob(rows: list, meta: dict) -> dict:
    """ردیف: [لینک کالا در سایت, کلیک, هزینه (تومان), قیمت, نام]"""
    from app.db.marketing import PlanCategory, Product
    with get_session() as db:
        prods = db.query(Product.id, Product.permalink, Product.plan_id).all()
        names = dict(db.query(PlanCategory.id, PlanCategory.name).all())
        n_by_plan = Counter(p.plan_id for p in prods if p.plan_id)
    by_slug = {_slug(p.permalink): (p.id, p.plan_id) for p in prods if p.permalink}
    plan = defaultdict(lambda: {"clicks": 0, "cost": 0, "products": 0})
    top, unmatched = [], {"clicks": 0, "cost": 0, "products": 0}
    tot_c = tot_cost = 0
    for r in rows:
        if not isinstance(r, list) or len(r) < 3:
            continue
        url, clicks, cost = str(r[0])[:500], int(_num(r[1])), int(_num(r[2]))
        price = int(_num(r[3])) if len(r) > 3 else 0
        name = str(r[4])[:120] if len(r) > 4 else ""
        tot_c += clicks
        tot_cost += cost
        pid, pl = by_slug.get(_slug(url), (None, None))
        b = plan[pl] if pl else unmatched
        b["clicks"] += clicks
        b["cost"] += cost
        b["products"] += 1
        if len(top) < 60:
            top.append({"name": name, "clicks": clicks, "cost": cost, "price": price, "plan_id": pl, "product_id": pid})
    days = meta.get("daily") or []
    daily = [[str(d[0])[:10], int(_num(d[1]))] for d in days if isinstance(d, list) and len(d) >= 2][-90:]
    full = [c for _, c in daily[:-1]] or [c for _, c in daily]       # روز آخر معمولاً ناقص است
    plans = []
    for k, v in plan.items():
        v.update({"plan_id": k, "name": re.sub(r"\s*\(.*?\)", "", names.get(k, k))[:60],
                  "cpc": round(v["cost"] / v["clicks"]) if v["clicks"] else None,
                  "site_products": n_by_plan.get(k, 0),
                  "clicks_per_product": round(v["clicks"] / max(1, n_by_plan.get(k, 0)), 3)})
        plans.append(v)
    plans.sort(key=lambda x: -x["clicks"])
    status = meta.get("status") or {}
    return {"clicks": tot_c, "cost": tot_cost, "cpc": round(tot_cost / tot_c) if tot_c else None,
            "products": len(rows), "per_day": round(sum(full) / len(full), 1) if full else None,
            "daily": daily, "plans": plans, "unmatched": unmatched, "top": top,
            "balance": int(_num(meta.get("balance"))) if meta.get("balance") is not None else None,
            "status": {str(k)[:40]: int(_num(v)) for k, v in status.items()} if isinstance(status, dict) else {}}


def _torob_yearly(rows: list, urls: list, meta: dict) -> dict:
    """ردیف: [ماه «1404-08», اندیس لینک در urls, کلیک, هزینه] ← کلیک ماهانه‌ی هر دسته و شاخص فصلی.

    مشکل اصلی: فروشگاه در طول سال هم بزرگ شده و هم ترکیب کالاهایش عوض شده (مثلاً پارسال بیشتر هیدرولیک و
    پنوماتیک داشت). پس شاخص فصلی بر اساس «کلیک به ازای هر کالای فعال» ساخته می‌شود:
      شدت دسته در ماه = کلیک دسته ÷ تعداد کالاهای کلیک‌خورده‌ی دسته
      نسبت به شدت کل فروشگاه در همان ماه، و بعد نسبت به میانگین خود دسته در ماه‌های معتبر.
    ماهی که دسته در آن کمتر از ۳۰٪ بیشترین تعداد کالای فعالش را داشته «نامعتبر» است و شاخص ندارد.
    """
    from app.db.marketing import PlanCategory, Product
    with get_session() as db:
        prods = db.query(Product.permalink, Product.plan_id).all()
        names = dict(db.query(PlanCategory.id, PlanCategory.name).all())
    by_slug = {_slug(p.permalink): p.plan_id for p in prods if p.permalink}
    plan_of = [by_slug.get(_slug(str(u))) for u in urls[:20000]]
    pm: dict = defaultdict(Counter)            # plan ← {ماه: کلیک}
    act: dict = defaultdict(Counter)           # plan ← {ماه: تعداد کالای کلیک‌خورده}
    month_tot, month_cost, month_act = Counter(), Counter(), Counter()
    for r in rows:
        if not isinstance(r, list) or len(r) < 3:
            continue
        m, i, c = str(r[0])[:7], int(_num(r[1])), int(_num(r[2]))
        if not re.match(r"^14\d\d-\d\d$", m) or c <= 0:
            continue
        pl = (plan_of[i] if 0 <= i < len(plan_of) else None) or "_unmatched"
        month_tot[m] += c
        month_act[m] += 1
        month_cost[m] += int(_num(r[3])) if len(r) > 3 else 0
        pm[pl][m] += c
        act[pl][m] += 1
    months = sorted(month_tot)
    inten = {m: month_tot[m] / month_act[m] for m in months if month_act[m]}
    plans = {}
    for pl, cnt in pm.items():
        mx = max(act[pl].values() or [0])
        ok = [m for m in months if mx and act[pl][m] >= max(3, 0.3 * mx)]
        base = sum(act[pl][m] * inten[m] for m in ok)
        rbar = (sum(cnt[m] for m in ok) / base) if base else 0
        idx = {}
        for m in ok:
            exp = act[pl][m] * inten[m] * rbar
            idx[int(m[5:])] = round((cnt[m] + 5) / (exp + 5), 2)      # کلید: شماره‌ی ماه شمسی
        tot = sum(cnt.values())
        plans[pl] = {"plan_id": pl, "name": re.sub(r"\s*\(.*?\)", "", names.get(pl, pl))[:60], "clicks": tot,
                     "months_active": len(ok), "by_month": [cnt[m] for m in months],
                     "active_products": [act[pl][m] for m in months], "season": idx,
                     "peak_months": [k for k, v in sorted(idx.items(), key=lambda x: -x[1]) if v >= 1.25][:3]}
    weekly = [[str(w[0])[:10], int(_num(w[1]))] for w in (meta.get("weekly") or []) if isinstance(w, list) and len(w) >= 2]
    return {"months": months, "month_clicks": [month_tot[m] for m in months], "month_cost": [month_cost[m] for m in months],
            "month_products": [month_act[m] for m in months],
            "clicks": sum(month_tot.values()), "cost": sum(month_cost.values()),
            "plans": sorted(plans.values(), key=lambda x: -x["clicks"]), "weekly": weekly[-60:]}


def season_factor(plan_id: str, month: int) -> tuple[float, float | None]:
    """تقاضای فصلی واقعی در ترب برای این ماه و ماه بعد (همین ماه‌ها در سال گذشته).
    خروجی: (ضریب بین ۰٫۸۵ و ۱٫۲, شاخص خام). فقط اگر دسته در سال حداقل ۱۰۰ کلیک و ۶ ماه فعال داشته باشد."""
    y = get("torob_yearly") or {}
    p = next((x for x in y.get("plans") or [] if x["plan_id"] == plan_id), None)
    if not p or p["clicks"] < 100 or p["months_active"] < 6:
        return 1.0, None
    s = {int(k): v for k, v in (p.get("season") or {}).items()}
    nxt = month % 12 + 1
    vals = [s[m] for m in (month, nxt) if m in s]
    if len(vals) < 2:          # این دو ماه در سال گذشته داده‌ی معتبر نداشته‌اند
        return 1.0, None
    raw = sum(vals) / len(vals)
    return round(max(0.85, min(1.2, raw ** 0.4)), 3), round(raw, 2)


def _gsc(rows: list, meta: dict) -> dict:
    """ردیف: [عبارت, کلیک, نمایش, CTR, رتبه]"""
    from app.tracking.offline import classify
    qs = []
    for r in rows:
        if not isinstance(r, list) or len(r) < 5 or not str(r[0]).strip():
            continue
        qs.append({"q": str(r[0])[:150], "c": int(_num(r[1])), "i": int(_num(r[2])), "p": round(_num(r[4]), 1)})
    by_plan = defaultdict(lambda: {"clicks": 0, "impr": 0, "queries": 0})
    for q in qs:
        pl = classify(q["q"])
        q["plan"] = pl
        b = by_plan[pl]
        b["clicks"] += q["c"]
        b["impr"] += q["i"]
        b["queries"] += 1
    com = [q for q in qs if _COMMERCIAL.search(q["q"])]
    quick = sorted([q for q in qs if 4 <= q["p"] <= 20 and q["i"] >= 40 and q["c"] / max(1, q["i"]) < 0.05],
                   key=lambda q: -q["i"])[:40]
    info = sorted([q for q in qs if not _COMMERCIAL.search(q["q"])], key=lambda q: -q["i"])[:40]
    head = sorted([q for q in qs if q["p"] > 20 and q["i"] >= 50], key=lambda q: -q["i"])[:30]   # کلمه‌ی کالا ولی رتبه‌ی دور
    return {"queries": len(qs), "clicks": sum(q["c"] for q in qs), "impr": sum(q["i"] for q in qs),
            "commercial": {"queries": len(com), "clicks": sum(q["c"] for q in com), "impr": sum(q["i"] for q in com),
                           "top": sorted(com, key=lambda q: -q["i"])[:30]},
            "quick_wins": quick, "informational": info, "far_head_terms": head,
            "plans": sorted(({"plan_id": k, **v} for k, v in by_plan.items()), key=lambda x: -x["impr"])[:25],
            "totals": {k: _num(v) for k, v in (meta.get("totals") or {}).items()} if isinstance(meta.get("totals"), dict) else {}}


def _simple(rows: list, meta: dict) -> dict:
    """ردیف: [نام (شهر یا منبع), عدد اصلی, ...]"""
    out = []
    for r in rows:
        if isinstance(r, list) and len(r) >= 2 and str(r[0]).strip():
            out.append([str(r[0])[:80], int(_num(r[1]))] + [int(_num(x)) for x in r[2:4]])
    out.sort(key=lambda x: -x[1])
    return {"rows": out[:200], "total": sum(x[1] for x in out)}


# ---------------- خواندن ----------------
_cache: dict = {}


def get(src: str) -> dict | None:
    import time
    hit = _cache.get(src)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    v = get_setting(f"ext.{src}")
    _cache[src] = (time.monotonic(), v)
    return v


def summary() -> dict:
    return {s: get(s) for s in sorted(SOURCES)}


def torob_weight(plan_id: str) -> float:
    """تقاضای واقعی خریدار در ترب: هرچه کلیک ماهانه‌ی کالاهای این دسته در ترب بیشتر، امتیاز کمی بیشتر (تا ×۱٫۲).
    جریمه نمی‌کند، چون بعضی دسته‌ها (مثل پنوماتیک) بیشتر تلفنی فروش می‌روند تا از ترب."""
    import math
    t = get("torob_clicks") or {}
    p = next((x for x in t.get("plans") or [] if x["plan_id"] == plan_id), None)
    if not p or p["clicks"] < 20:
        return 1.0
    return round(1 + min(0.2, 0.04 * math.log2(1 + p["clicks"] / 50)), 3)
