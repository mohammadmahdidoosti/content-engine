"""
هشدار آب‌وهوا: پیش‌بینی ۷ روزه‌ی مرکز هر استان از Open-Meteo (رایگان، بدون کلید)
و تبدیل آن به «این هفته این کالا را در این استان تبلیغ کن».

قانون‌ها (هر کدام به یک بخش از نقشه وصل است):
- یخبندان (حداقل دما ≤ -۳)  ← ضدیخ بتن، گرمایش مرغداری، عایق، موتورخانه، موتور برق زمستان
- گرمای شدید (حداکثر ≥ ۴۰)   ← پمپ چاه، کولر صنعتی، موتور برق تابستان، پمپ ساختمانی، مخزن
- بارش سنگین (روزانه ≥ ۲۰ م‌م یا ۳ روزه ≥ ۴۰) ← کف‌کش و لجن‌کش، آب‌بندی
"""
import logging
from datetime import datetime, timezone

import httpx

from app.db.session import get_setting, set_setting

log = logging.getLogger("plan.weather")

# مرکز استان‌ها (عرض، طول)
PROVINCE_CENTERS = {
    "تهران": (35.69, 51.39), "اصفهان": (32.65, 51.67), "خراسان رضوی": (36.30, 59.60), "فارس": (29.59, 52.58),
    "آذربایجان شرقی": (38.08, 46.29), "البرز": (35.83, 50.99), "مازندران": (36.56, 53.06), "مرکزی": (34.09, 49.69),
    "قزوین": (36.27, 50.00), "گیلان": (37.28, 49.58), "خوزستان": (31.32, 48.67), "قم": (34.64, 50.88),
    "آذربایجان غربی": (37.55, 45.08), "سمنان": (35.57, 53.39), "کرمان": (30.28, 57.08), "یزد": (31.90, 54.37),
    "گلستان": (36.84, 54.44), "زنجان": (36.68, 48.48), "همدان": (34.80, 48.51), "کردستان": (35.31, 47.00),
    "اردبیل": (38.25, 48.30), "لرستان": (33.49, 48.36), "کرمانشاه": (34.31, 47.07), "هرمزگان": (27.18, 56.27),
    "بوشهر": (28.97, 50.84), "ایلام": (33.64, 46.42), "خراسان جنوبی": (32.87, 59.22), "خراسان شمالی": (37.47, 57.33),
    "سیستان و بلوچستان": (29.50, 60.86), "چهارمحال و بختیاری": (32.33, 50.86), "کهگیلویه و بویراحمد": (30.67, 51.59),
}

RULES = {
    "frost": {"label": "یخبندان", "icon": "❄️",
              "targets": [("BUILD_CHEM", 0), ("EGG_POULTRY", 0), ("FOAM", 0), ("VALVE_IND", 0), ("GENSET", 1)]},
    "heat": {"label": "گرمای شدید", "icon": "🔥",
             "targets": [("PUMP_WELL", 0), ("IND_COOLER", 0), ("GENSET", 0), ("PUMP_BUILDING", 0), ("TANK", 0)]},
    "rain": {"label": "بارش سنگین", "icon": "🌧",
             "targets": [("PUMP_WELL", 1), ("BUILD_CHEM", 1)]},
}


def _check(daily: dict) -> list[dict]:
    out = []
    days = daily.get("time") or []
    tmin = daily.get("temperature_2m_min") or []
    tmax = daily.get("temperature_2m_max") or []
    rain = daily.get("precipitation_sum") or []
    if tmin and min(x for x in tmin if x is not None) <= -3:
        v = min(x for x in tmin if x is not None)
        out.append({"kind": "frost", "value": v, "day": days[tmin.index(v)], "text": f"دمای حداقل تا {v:.0f}°"})
    if tmax and max(x for x in tmax if x is not None) >= 40:
        v = max(x for x in tmax if x is not None)
        out.append({"kind": "heat", "value": v, "day": days[tmax.index(v)], "text": f"دمای حداکثر تا {v:.0f}°"})
    r = [x or 0 for x in rain]
    three = max((sum(r[i:i + 3]) for i in range(max(1, len(r) - 2))), default=0)
    if r and (max(r) >= 20 or three >= 40):
        out.append({"kind": "rain", "value": round(max(max(r), three), 1), "day": days[r.index(max(r))],
                    "text": f"بارش تا {max(r):.0f} میلی‌متر در روز ({three:.0f} در ۳ روز)"})
    return out


def refresh(timeout: float = 30) -> dict:
    """یک درخواست برای همه‌ی استان‌ها (Open-Meteo چند مختصات را با هم می‌پذیرد)."""
    names = list(PROVINCE_CENTERS)
    lat = ",".join(str(PROVINCE_CENTERS[n][0]) for n in names)
    lon = ",".join(str(PROVINCE_CENTERS[n][1]) for n in names)
    import time
    params = {"latitude": lat, "longitude": lon, "forecast_days": 7, "timezone": "Asia/Tehran",
              "daily": "temperature_2m_min,temperature_2m_max,precipitation_sum"}
    r = None
    for attempt in range(4):   # آی‌پی مشترک Render گاهی ۴۲۹ می‌گیرد
        r = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=timeout, params=params,
                      headers={"User-Agent": "content-engine/1.0 (caravansanat)"})
        if r.status_code != 429:
            break
        time.sleep(10 * (attempt + 1))
    if r.status_code == 429:
        raise RuntimeError("سرویس آب‌وهوا موقتاً محدود کرده (۴۲۹)؛ چند دقیقه بعد دوباره امتحان می‌شود")
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = [data]
    alerts = []
    for name, d in zip(names, data):
        for a in _check(d.get("daily") or {}):
            a["province"] = name
            a.update({k: RULES[a["kind"]][k] for k in ("label", "icon")})
            alerts.append(a)
    set_setting("weather.alerts", {"at": datetime.now(timezone.utc).isoformat(), "alerts": alerts})
    log.info("هشدار آب‌وهوا: %d هشدار در %d استان", len(alerts), len({a["province"] for a in alerts}))
    return {"alerts": len(alerts)}


def current() -> dict:
    return get_setting("weather.alerts") or {"at": None, "alerts": []}


def boosts() -> dict[tuple[str, int], list[dict]]:
    """(plan_id, segment_index) ← هشدارهای فعال مربوط."""
    out: dict[tuple[str, int], list[dict]] = {}
    for a in current().get("alerts") or []:
        for t in RULES[a["kind"]]["targets"]:
            out.setdefault(t, []).append(a)
    return out
