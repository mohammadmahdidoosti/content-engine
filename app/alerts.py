"""
هشدارهای صفحه‌ی خانه: چیزهایی که اگر دیده نشوند پول یا داده از دست می‌رود.
هر هشدار: level (critical | warn | info)، متن، و لینک بخش مربوط در پنل.
"""
from datetime import datetime, timezone

from app.db.session import get_setting


def _age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).total_seconds() / 86400
    except ValueError:
        return None


def collect() -> list[dict]:
    out: list[dict] = []
    add = lambda lvl, text, link=None: out.append({"level": lvl, "text": text, "link": link})  # noqa: E731

    # دیتابیس و پشتیبان
    try:
        from app import backup
        b = backup.status()
        dl = b.get("db_days_left")
        if dl is not None and dl <= 10:
            add("critical" if dl <= 3 else "warn",
                f"دیتابیس {dl} روز دیگر منقضی می‌شود؛ پشتیبان بگیرید و منتقل کنید.", "#settings")
    except Exception:  # noqa: BLE001
        pass

    # ترب
    from app.tracking.external import get
    t = get("torob_clicks") or {}
    if t:
        age = _age_days(t.get("at"))
        per_day_cost = (t.get("per_day") or 0) * (t.get("cpc") or 0)
        if t.get("balance") is not None and per_day_cost:
            days = t["balance"] / per_day_cost
            if days <= 7:
                add("critical" if days <= 3 else "warn",
                    f"شارژ حساب ترب حدود {days:.0f} روز دیگر تمام می‌شود (موجودی {t['balance']:,} تومان در زمان آخرین به‌روزرسانی).", "#report")
        daily = [n for _, n in (t.get("daily") or [])][:-1]
        if len(daily) >= 10:
            base = sum(daily) / len(daily)
            last3 = sum(daily[-3:]) / 3
            if base and last3 < 0.7 * base:
                add("warn", f"کلیک ترب در ۳ روز آخر {last3:.0f} در روز بوده، {100 - 100 * last3 / base:.0f}٪ کمتر از میانگین ماه.", "#report")
        tech = (t.get("status") or {}).get("page_deleted_temporary_technical") or 0
        if tech >= 200:
            add("warn", f"{tech:,} کالا در ترب «حذف موقت به‌خاطر مشکل فنی» است و دیده نمی‌شود؛ نصب افزونه‌ی رسمی ترب برای ووکامرس احتمالاً حلش می‌کند.", "#report")
        if age is not None and age > 7:
            add("info", f"داده‌ی ترب {age:.0f} روز است به‌روز نشده (از Claude بخواهید «داده‌ی ترب را به‌روز کن»).", "#report")

    # همگام‌سازی سایت
    last = _age_days(get_setting("sync.last_daily_at"))
    if last is not None and last > 2:
        add("warn", f"همگام‌سازی محصولات سایت {last:.0f} روز است اجرا نشده (سرویس روی سرور متوقف بوده یا زمان‌بند خاموش است؛ NO_SCHEDULER در Coolify).", "#settings")

    # تماس‌ها
    try:
        from app.tracking.calls import stats
        st = stats(30)
        if st["total"] >= 20 and (st.get("no_city_pct") or 0) > 30:
            add("info", f"{st['no_city_pct']}٪ تماس‌ها شهر ندارند؛ پرسیدن شهر، برنامه‌ی شهرها را دقیق‌تر می‌کند.", "#calls")
        unk = next((s for s in st["sources"] if s["source"] == "unknown"), None)
        if st["total"] >= 20 and unk and unk["calls"] / st["total"] > 0.4:
            add("info", "بیش از ۴۰٪ تماس‌ها منبع آشنایی ندارند؛ بدون آن سود هر آگهی معلوم نمی‌شود.", "#calls")
    except Exception:  # noqa: BLE001
        pass

    if get_setting("hunter.paused"):
        add("info", "شکارچی کانال متوقف است.", "#channels")
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda a: order[a["level"]])
