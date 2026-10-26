from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import Base

_engine = None
_Session = None


def _normalize_url(url: str) -> str:
    """آدرس Render (postgres:// یا postgresql://) را به درایور psycopg3 تبدیل می‌کند."""
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
    Base.metadata.create_all(get_engine())


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
