"""داده‌ی پایه‌ی نقشه (فایل نقشه‌ی تبلیغات)، تاریخ شمسی و بارگذاری اولیه در دیتابیس."""
import json
import logging
from functools import lru_cache
from pathlib import Path

import jdatetime

from app.db.marketing import PlanCategory
from app.db.session import get_session

log = logging.getLogger("plan.data")
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "adplan.json"

MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


@lru_cache
def plan_file() -> dict:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def groups() -> dict[str, str]:
    return plan_file()["groups"]


def provinces() -> list[dict]:
    return plan_file()["prov"]


def province_names() -> list[str]:
    return [p["name"] for p in provinces()]


def _tehran_today():
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)).date()


def today_jalali() -> jdatetime.date:
    """تاریخ امروز به وقت تهران (سرور Render روی UTC است)."""
    return jdatetime.date.fromgregorian(date=_tehran_today())


def month_name(m: int) -> str:
    return MONTHS[(m - 1) % 12]


def seed_plan() -> int:
    """اگر جدول نقشه خالی است، ۴۸ دسته‌ی پایه را از فایل نقشه وارد می‌کند."""
    with get_session() as db:
        if db.query(PlanCategory).count():
            return 0
        n = 0
        for c in plan_file()["cats"]:
            db.add(PlanCategory(
                id=c["id"], name=c["name"], group=c["group"], buyers=c.get("buyers"),
                angle=c.get("angle"), time=c.get("time"), channels=c.get("channels"),
                segments=c.get("segments") or [], status="base", source="plan",
                product_count=c.get("count") or 0, examples=[{"name": e} for e in (c.get("examples") or [])],
            ))
            n += 1
        db.commit()
    log.info("نقشه‌ی پایه وارد شد: %d دسته", n)
    return n
