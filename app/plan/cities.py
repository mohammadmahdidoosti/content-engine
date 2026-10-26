"""
ماتریس «شهر × محصول».

دو منبع:
1. نقشه‌ی تبلیغات: هر بخش جغرافیایی فهرست شهرهایش را دارد (به ترتیب استان‌ها، جدا با «|»).
2. پروفایل تحقیقی شهرها (app/data/city_profiles.json): واقعیت‌های کشاورزی و صنعتی با منبع،
   و گروه‌های کالایی که در آن شهر تقاضای واقعی دارند (قدرت ۱ تا ۳).

خروجی برای هر شهر و ماه: گروه‌های کالا به ترتیب امتیاز، دلیل، کالاهای موجود سایت و عبارت‌های جست‌وجوی کانال.
"""
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from app.db.marketing import PlanCategory
from app.db.session import get_session
from app.plan import planner
from app.plan.pricing import plan_price_weight
from app.plan.data import groups, month_name, today_jalali

PROFILES_FILE = Path(__file__).resolve().parent.parent / "data" / "city_profiles.json"


def _clean_city(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\(.*?\)", "", s)).strip().replace("ي", "ی").replace("ك", "ک")


def _norm(s: str) -> str:
    """برای تطبیق: بدون نیم‌فاصله و فاصله."""
    return _clean_city(s).replace("‌", "").replace(" ", "")


def _prov(p: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", p or "").strip()


@lru_cache
def profiles() -> dict[str, dict]:
    if not PROFILES_FILE.exists():
        return {}
    try:
        rows = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return {_norm(r["city"]): r for r in rows if r.get("city")}


def city_index(plans: list[PlanCategory]) -> dict[str, dict]:
    """شهر ← {name, province, entries: [(plan_id, segment_index, position)]}"""
    idx: dict[str, dict] = {}
    for p in plans:
        for si, seg in enumerate(p.segments or []):
            provs = [_prov(x) for x in seg.get("provinces") or []]
            parts = (seg.get("cities") or "").split("|")
            aligned = len(parts) == len(provs)
            pos = 0
            for pi, part in enumerate(parts):
                prov = provs[pi] if aligned else (provs[0] if len(provs) == 1 else None)
                for raw in re.split(r"[،,]", part):
                    name = _clean_city(raw)
                    if not name or name in ("—", "-"):
                        continue
                    key = _norm(name)
                    e = idx.setdefault(key, {"name": name, "province": prov, "entries": []})
                    if not e["province"] and prov:
                        e["province"] = prov
                    e["entries"].append((p.id, si, pos))
                    pos += 1
    for key, prof in profiles().items():
        e = idx.setdefault(key, {"name": prof["city"], "province": _prov(prof.get("province")), "entries": []})
        e["profile"] = True
    return idx


def list_cities(q: str = "") -> list[dict]:
    with get_session() as db:
        plans = db.query(PlanCategory).filter(PlanCategory.status.in_(["base", "approved"])).all()
        idx = city_index(plans)
    qn = _norm(q) if q else ""
    out = [{"name": e["name"], "province": e["province"], "rows": len({x[0] for x in e["entries"]}),
            "profile": _norm(e["name"]) in profiles()}
           for k, e in idx.items() if not qn or qn in k]
    out.sort(key=lambda r: (-r["profile"], -r["rows"], r["name"]))
    return out


def city_plan(city: str, month: int | None = None, limit: int = 10) -> dict:
    m = month or today_jalali().month
    weights = {**planner.DEFAULT_WEIGHTS, **(planner.cfg("planner.weights") or {})}
    key = _norm(city)
    with get_session() as db:
        plans = db.query(PlanCategory).filter(PlanCategory.status.in_(["base", "approved"])).all()
        by_id = {p.id: p for p in plans}
        idx = city_index(plans)
        e = idx.get(key)
        if not e:
            raise ValueError("این شهر در نقشه یا پروفایل‌ها نیست")
        prof = profiles().get(key) or {}
        strength = {c["id"]: c for c in prof.get("categories") or [] if c.get("id") in by_id}

        cand: dict[str, dict] = defaultdict(dict)
        for pid, si, pos in e["entries"]:
            p = by_id[pid]
            seg = p.segments[si]
            t, why = planner._timing(seg, m)
            pos_w = 1.0 if pos < 3 else (0.9 if pos < 8 else 0.8)   # شهرهای اول فهرست، قطب اصلی‌اند
            cur = cand[pid]
            if t * pos_w > cur.get("t", -1):
                cur.update(t=t * pos_w, why=why, segment_index=si, segment=seg.get("name"), source="plan")
        for pid, c in strength.items():
            cur = cand[pid]
            if "t" not in cur:   # در نقشه برای این شهر نبود؛ زمان‌بندی را از بهترین بخش همان دسته بگیر
                best = max(((planner._timing(s, m), i) for i, s in enumerate(by_id[pid].segments or [])),
                           default=((0, ""), 0))
                (t, why), si = best
                if t <= 0 and c.get("strength", 0) >= 2:
                    t, why = 0.3, "تقاضای دائمی محلی (فصلی نیست یا اوجش نیست)"
                cur.update(t=t, why=why, segment_index=si, segment=by_id[pid].segments[si].get("name") if by_id[pid].segments else None,
                           source="research")
            cur["strength"] = c.get("strength")
            cur["reason"] = c.get("reason")

        items = []
        for pid, c in cand.items():
            if c.get("t", 0) <= 0:
                continue
            p = by_id[pid]
            s = c.get("strength") or 0
            score = (c["t"] * weights.get(p.group, 1.0) * planner._value_weight(p.p90_price, p.median_price)
                     * planner._stock_weight(p) * (1 + 0.3 * s) * planner.margin_weight(p.id, p.group)
                     * planner.performance_weight(p.id) * plan_price_weight(p.examples or []))
            if score <= 0:
                continue
            items.append({
                "plan_id": pid, "name": p.name, "group": p.group, "group_name": groups().get(p.group, p.group),
                "score": round(score, 3), "why": c["why"], "segment_index": c["segment_index"],
                "segment": c.get("segment"), "strength": c.get("strength"), "reason": c.get("reason"),
                "source": c["source"], "products": p.product_count, "in_stock": p.in_stock_count,
                "p90_price": p.p90_price, "examples": (p.examples or [])[:6], "time": p.time,
                "keywords": [k.format(c=e["name"]) for k in _kw_patterns(p.group)],
            })
    items.sort(key=lambda x: -x["score"])
    return {"city": e["name"], "province": e["province"], "month": m, "month_name": month_name(m),
            "profile": {k: prof.get(k) for k in ("summary", "agri", "industry", "other", "sources")} if prof else None,
            "items": items[:limit]}


def _kw_patterns(group: str) -> list[str]:
    if group in ("AGRI", "WATER"):
        return ["کشاورزی {c}", "کشاورزان {c}", "اخبار {c}", "نیازمندی {c}"]
    if group == "BUILD":
        return ["ساختمان {c}", "پیمانکاران {c}", "نیازمندی {c}", "اخبار {c}"]
    return ["شهرک صنعتی {c}", "صنعت {c}", "بازار {c}", "نیازمندی {c}"]
