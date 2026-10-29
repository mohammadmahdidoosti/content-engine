from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "~deepseek/deepseek-pro-latest"
    llm_fast_model: str = "~deepseek/deepseek-flash-latest"   # برای کارهای سبک: دسته‌بندی، نگاشت
    llm_reasoning_effort: str = ""
    llm_temperature: float = 0.7
    llm_max_retries: int = 3
    llm_timeout_sec: int = 120

    # DB
    database_url: str = "sqlite:///./content_engine.db"

    # Brand
    brand_name: str = "کاروان صنعت"
    brand_site_url: str = "https://caravansanat.com"
    support_telegram: str = "Caravansanatsupport"   # پشتیبانی تلگرام (لینک «مشاوره» در آگهی‌ها)
    support_bale: str = "Caravansanatsupport"       # پشتیبانی بله
    brand_niche: str = (
        "فروشگاه آنلاین تجهیزات صنعتی، کشاورزی و ساختمانی با ارسال به کل کشور: بذر و نهاده، آبیاری و پمپ، "
        "هیدرولیک، پنوماتیک، برق صنعتی و الکتروموتور، لوله و اتصالات و شیرآلات، تاسیسات، ایمنی و ابزار"
    )
    brand_tone: str = (
        "تخصصی و قابل‌اعتماد ولی ساده؛ مثل یک فروشنده‌ی باتجربه که با کشاورز و تعمیرکار صمیمی و فنی حرف می‌زند؛ "
        "بدون اغراق"
    )
    brand_phone: str = "09192508938"
    # واقعیت‌های تأییدشده‌ی فروشگاه (۱۴۰۵/۰۸/۰۶، از خود صاحب فروشگاه). آگهی و دستیار فقط همین شرایط را می‌گویند.
    brand_facts: str = (
        "ضمانت اصالت کالا؛ فروش مستقیم و بدون واسطه؛ مشاوره‌ی رایگان قبل از خرید؛ ارسال به همه‌ی شهرهای ایران؛ "
        "خرید اقساطی فقط برای بعضی کالاها (همیشه با قید «برای برخی کالاها» و «شرایط را از کارشناس بپرسید»؛ "
        "هرگز نگو کالای مشخصی قسطی است)"
    )
    # فرایند فروش: مشتری در سایت سفارش ثبت می‌کند، فروش تماس می‌گیرد و فاکتور می‌فرستد
    sales_process: str = (
        "مشتری باید ابتدا سفارش را در سایت ثبت کند؛ بعد کارشناسان فروش برای تأیید تماس می‌گیرند و فاکتور می‌فرستند. "
        "قیمت روز روی سایت است. تبلیغ باید مخاطب را به ثبت سفارش در سایت هدایت کند، نه تماس برای پرسیدن قیمت."
    )

    # WooCommerce REST (اختیاری؛ فقط برای خواندن سفارش‌ها و نسبت دادن آن‌ها به تبلیغ)
    wc_consumer_key: str = ""
    wc_consumer_secret: str = ""

    # آدرس عمومی همین سرویس (لینک‌های کوتاه، webhook ربات‌ها)، مثلاً https://content.caravansanat.com
    public_base_url: str = Field("", validation_alias=AliasChoices("PUBLIC_BASE_URL"))

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
