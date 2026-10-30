"""
دیتابیس CRM کاروان صنعت — جدا از پنل بازاریابی (اطلاعات شخصی مشتری فقط اینجاست).
CRM_DATABASE_URL (Postgres در Coolify) یا پیش‌فرض sqlite محلی برای تست.
"""
import os
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

_engine = None
_Session = None


def _now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "crm_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    pw_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="sales")     # admin | sales
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Contact(Base):
    __tablename__ = "crm_contacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)   # 09xxxxxxxxx یا ثابت
    city: Mapped[str | None] = mapped_column(String(80))
    province: Mapped[str | None] = mapped_column(String(60))
    activity: Mapped[str | None] = mapped_column(String(120))        # زمینه‌ی فعالیت (کشاورز، پیمانکار…)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Deal(Base):
    __tablename__ = "crm_deals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("crm_contacts.id"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")      # کالای درخواستی
    category: Mapped[str | None] = mapped_column(String(80), index=True)
    source: Mapped[str | None] = mapped_column(String(40), index=True)   # ترب، سایت، کد آگهی، …
    ad_code: Mapped[str | None] = mapped_column(String(20), index=True)
    stage: Mapped[str] = mapped_column(String(20), default="new", index=True)
    amount: Mapped[int | None] = mapped_column(BigInteger)           # مبلغ فروش (تومان)
    profit: Mapped[int | None] = mapped_column(BigInteger)
    lost_reason: Mapped[str | None] = mapped_column(String(60))
    next_at: Mapped[datetime | None] = mapped_column(Date, index=True)   # تاریخ پیگیری بعدی (میلادی)
    note: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(String(40))           # manual | leads_import | woo
    ext_key: Mapped[str | None] = mapped_column(String(64), index=True)   # برای جلوگیری از ورود تکراری
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Activity(Base):
    __tablename__ = "crm_activities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("crm_deals.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    kind: Mapped[str] = mapped_column(String(20), default="note")    # note | call | stage | followup
    text: Mapped[str] = mapped_column(Text, default="")
    user: Mapped[str | None] = mapped_column(String(60))


class Setting(Base):
    __tablename__ = "crm_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | None] = mapped_column(JSON)


def get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("CRM_DATABASE_URL") or "sqlite:///crm.db"
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        _engine = create_engine(url, pool_pre_ping=True, future=True)
    return _engine


def session():
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _Session()


def init_db() -> None:
    Base.metadata.create_all(get_engine())
    eng = get_engine()
    insp = inspect(eng)
    with eng.begin() as conn:                     # ستون‌های تازه (مهاجرت ساده)
        for t in Base.metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(t.name)}
            for col in t.columns:
                if col.name not in have:
                    conn.execute(text(f"ALTER TABLE {t.name} ADD COLUMN {col.name} {col.type.compile(dialect=eng.dialect)}"))


def get_setting(key, default=None):
    with session() as db:
        s = db.get(Setting, key)
        return s.value if s is not None else default


def set_setting(key, value) -> None:
    with session() as db:
        s = db.get(Setting, key)
        if s is None:
            db.add(Setting(key=key, value=value))
        else:
            s.value = value
        db.commit()
