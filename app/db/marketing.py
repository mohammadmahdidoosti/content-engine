"""
جدول‌های بخش بازاریابی: محصولات سایت، دسته‌های ووکامرس، نقشه‌ی تبلیغات (پویا)،
کانال‌ها، کمپین‌ها، جایگاه‌های تبلیغ، کلیک‌ها، سفارش‌ها و تنظیمات.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Setting(Base):
    """تنظیمات و وضعیت‌های کلید-مقدار (بودجه، وزن‌ها، مکان‌نمای همگام‌سازی و ...)."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | float | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class WcCategory(Base):
    __tablename__ = "wc_categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str | None] = mapped_column(String(400))
    parent: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str | None] = mapped_column(Text)          # «ریشه › زیردسته › ...»
    count: Mapped[int] = mapped_column(Integer, default=0)
    permalink: Mapped[str | None] = mapped_column(Text)
    plan_id: Mapped[str | None] = mapped_column(String(40), index=True)   # دسته‌ی نقشه
    map_source: Mapped[str | None] = mapped_column(String(20))           # inherit | llm | manual | none
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # شناسه‌ی ووکامرس
    name: Mapped[str] = mapped_column(Text)
    sku: Mapped[str | None] = mapped_column(String(120))
    permalink: Mapped[str | None] = mapped_column(Text)
    price: Mapped[int | None] = mapped_column(BigInteger)        # تومان
    regular_price: Mapped[int | None] = mapped_column(BigInteger)
    on_sale: Mapped[bool] = mapped_column(Boolean, default=False)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    image: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(200))
    short_desc: Mapped[str | None] = mapped_column(Text)
    category_ids: Mapped[list] = mapped_column(JSON, default=list)
    plan_id: Mapped[str | None] = mapped_column(String(40), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PlanCategory(Base):
    """یک ردیف از نقشه‌ی تبلیغات. پایه از فایل نقشه می‌آید و با محصولات سایت به‌روز می‌شود."""
    __tablename__ = "plan_categories"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    group: Mapped[str] = mapped_column(String(20))
    buyers: Mapped[str | None] = mapped_column(Text)
    angle: Mapped[str | None] = mapped_column(Text)
    time: Mapped[str | None] = mapped_column(Text)
    channels: Mapped[str | None] = mapped_column(Text)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="base")   # base | proposed | approved | rejected
    source: Mapped[str] = mapped_column(String(20), default="plan")   # plan | llm
    # آمار زنده از سایت
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    in_stock_count: Mapped[int] = mapped_column(Integer, default=0)
    new_7d: Mapped[int] = mapped_column(Integer, default=0)
    median_price: Mapped[int | None] = mapped_column(BigInteger)
    p90_price: Mapped[int | None] = mapped_column(BigInteger)
    examples: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20), default="telegram")  # telegram | eitaa | bale | rubika | instagram | other
    username: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    members: Mapped[int | None] = mapped_column(Integer)
    avg_views: Mapped[int | None] = mapped_column(Integer)
    view_rate: Mapped[float | None] = mapped_column(Float)      # بازدید / عضو
    posts_per_day: Mapped[float | None] = mapped_column(Float)
    ad_ratio: Mapped[float | None] = mapped_column(Float)       # سهم پست‌های تبلیغاتی
    last_post_days: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    provinces: Mapped[list] = mapped_column(JSON, default=list)
    topics: Mapped[str | None] = mapped_column(Text)
    price_toman: Mapped[int | None] = mapped_column(BigInteger)  # قیمت یک پست تبلیغ
    admin_contact: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | checked | error | blocked
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # شکارچی و برچسب‌ها
    source: Mapped[str | None] = mapped_column(String(20))         # manual | snowball | google | eitaa | bale
    found_via: Mapped[str | None] = mapped_column(String(200))     # کانال مادر یا عبارت جست‌وجو
    cities: Mapped[list | None] = mapped_column(JSON)              # شهرهای مخاطب (فارسی)
    plan_ids: Mapped[list | None] = mapped_column(JSON)            # دسته‌های نقشه‌ی مرتبط
    audience: Mapped[str | None] = mapped_column(String(30))       # farmer | contractor | industry | technician | local_news | classifieds | general
    scope: Mapped[str | None] = mapped_column(String(20))          # city | province | national
    relevant: Mapped[bool | None] = mapped_column(Boolean)
    sample: Mapped[str | None] = mapped_column(Text)               # چند پست آخر (برای برچسب‌زدن)
    tagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # آخرین گلوله‌برفی
    # معیارهای دقیق (app/channels/metrics.py)
    metrics: Mapped[dict | None] = mapped_column(JSON)
    expected_ad_views: Mapped[int | None] = mapped_column(Integer)
    ad_contacts: Mapped[list | None] = mapped_column(JSON)          # آیدی/شماره‌ی سفارش تبلیغ از توضیح کانال
    members_history: Mapped[list | None] = mapped_column(JSON)     # [[تاریخ, عضو], …] برای تشخیص جهش عضو


class ChannelCandidate(Base):
    """کانالی که پیدا شده ولی هنوز بررسی نشده (از گلوله‌برفی، گوگل، ایتا…)."""
    __tablename__ = "channel_candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20), default="telegram")
    username: Mapped[str] = mapped_column(String(120), index=True)
    source: Mapped[str] = mapped_column(String(20))
    found_via: Mapped[str | None] = mapped_column(String(200))
    hits: Mapped[int] = mapped_column(Integer, default=1)          # چند بار دیده شده
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | added | rejected
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    plan_id: Mapped[str] = mapped_column(String(40))
    segment: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    provinces: Mapped[list] = mapped_column(JSON, default=list)
    cities: Mapped[str | None] = mapped_column(Text)
    product_ids: Mapped[list] = mapped_column(JSON, default=list)
    target_url: Mapped[str | None] = mapped_column(Text)
    budget_toman: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | active | done
    brief: Mapped[dict | None] = mapped_column(JSON)   # خلاصه‌ی کمپین (چرا، کی، کجا، چطور)
    city: Mapped[str | None] = mapped_column(String(80))   # کمپین مخصوص یک شهر
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Placement(Base):
    """یک تبلیغ در یک کانال، برای یک کمپین."""
    __tablename__ = "placements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft | contacted | agreed | paid | published | done | cancelled
    cost_toman: Mapped[int | None] = mapped_column(BigInteger)
    publish_at: Mapped[str | None] = mapped_column(String(40))   # متن آزاد، مثلاً «۱۲ آذر ساعت ۲۱»
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # وقتی وضعیت «منتشر شد» زده شد
    target_url: Mapped[str | None] = mapped_column(Text)
    ad: Mapped[dict | None] = mapped_column(JSON)               # متن و تصویر تبلیغ
    ad_status: Mapped[str] = mapped_column(String(20), default="none")  # none | generating | ready | failed
    ad_error: Mapped[str | None] = mapped_column(Text)
    ad_cost_usd: Mapped[float | None] = mapped_column(Float)
    ad_variant: Mapped[str | None] = mapped_column(String(10))   # full | short (برای مقایسه‌ی A/B)
    city: Mapped[str | None] = mapped_column(String(80))         # اگر تبلیغ برای یک شهر خاص است
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)       # تماس‌ها (دستی)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    revenue_toman: Mapped[int] = mapped_column(BigInteger, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Click(Base):
    __tablename__ = "clicks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    visitor: Mapped[str | None] = mapped_column(String(32))   # هش ip+ua (بدون ذخیره‌ی ip)
    referer: Mapped[str | None] = mapped_column(Text)


class TrackedOrder(Base):
    """سفارش‌های ووکامرس که به یک تبلیغ نسبت داده شده‌اند."""
    __tablename__ = "tracked_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)    # شناسه‌ی سفارش ووکامرس
    code: Mapped[str | None] = mapped_column(String(20), index=True)
    source: Mapped[str | None] = mapped_column(String(200))
    total_toman: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str | None] = mapped_column(String(40))
    created: Mapped[str | None] = mapped_column(String(40))


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column(Boolean)
    stats: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
