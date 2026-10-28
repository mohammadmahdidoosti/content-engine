from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import Base

_engine = None
_Session = None


def _normalize_url(url: str) -> str:
    """آدرس دیتابیس (postgres:// یا postgresql://) را به درایور psycopg3 تبدیل می‌کند."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_normalize_url(get_settings().database_url), pool_pre_ping=True, future=True)
    return _engine


def get_session():
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _Session()


def init_db() -> None:
    """ساخت جدول‌ها (در مراحل بعد به Alembic منتقل می‌شود)."""
    from app.db import marketing  # noqa: F401  (ثبت جدول‌های بازاریابی)
    from app.plan import pricing  # noqa: F401  (جدول مقایسه‌ی قیمت)
    from app import assistant  # noqa: F401  (جدول پرسش‌های دستیار)
    from app.tracking import history  # noqa: F401  (جدول تاریخچه‌ی سفارش‌ها)
    from app.tracking import calls  # noqa: F401  (دفتر تماس)
    Base.metadata.create_all(get_engine())
    _add_missing_columns()


def _add_missing_columns() -> None:
    """مهاجرت ساده: ستون‌های جدیدِ مدل‌ها را (nullable) به جدول‌های موجود اضافه می‌کند."""
    import logging

    from sqlalchemy import inspect, text
    eng = get_engine()
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=eng.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}'))
                logging.getLogger("db").info("ستون جدید اضافه شد: %s.%s", table.name, col.name)


def get_setting(key: str, default=None):
    from app.db.marketing import Setting
    with get_session() as db:
        s = db.get(Setting, key)
        return s.value if s is not None else default


def set_setting(key: str, value) -> None:
    from app.db.marketing import Setting
    with get_session() as db:
        s = db.get(Setting, key)
        if s is None:
            db.add(Setting(key=key, value=value))
        else:
            s.value = value
        db.commit()
