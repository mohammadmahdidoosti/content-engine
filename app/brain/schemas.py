"""
اسکیماهای خروجی LLM برای سه نوع محتوا.

قانون کلی: ساختار سخت‌گیرانه اعتبارسنجی می‌شود (اگر خراب بود، دوباره درخواست
می‌دهیم)، ولی چیزهای جزئی مثل آیکون ناشناخته یا جمع مدت صحنه‌ها خودکار اصلاح
می‌شوند تا بی‌دلیل retry نکنیم.
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# آیکون‌هایی که قالب‌های اینفوگرافیک (مرحله ۴) پشتیبانی خواهند کرد
ICONS = [
    "check", "warning", "tire", "link", "light", "weight", "box", "gas", "window",
    "tool", "map", "route", "tent", "caravan", "car", "water", "battery", "fire",
    "sun", "moon", "snow", "money", "clock", "calendar", "star", "heart", "shield",
    "phone", "wifi", "food", "first_aid", "trash", "lock", "info",
]


def _clean_hashtags(tags: list[str]) -> list[str]:
    out, seen = [], set()
    for t in tags:
        t = t.strip().replace(" ", "_")
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ---------------- متنی ----------------
class TextPost(BaseModel):
    title: str = Field(..., max_length=120, description="تیتر کوتاه و جذاب، با یک ایموجی مرتبط")
    telegram_text: str = Field(
        ..., max_length=3500,
        description="متن کامل پست تلگرام. خطوط جدا، ایموجی متعادل، ** برای bold. بدون هشتگ و لینک.",
    )
    instagram_caption: str = Field(
        ..., max_length=2000,
        description="کپشن اینستاگرام: خط اول قلاب، بعد بدنه، بعد CTA. بدون هشتگ.",
    )
    cta: str = Field(..., max_length=120, description="دعوت به اقدام کوتاه")
    hashtags: list[str] = Field(..., min_length=3, max_length=12)
    product_keywords: list[str] = Field(
        default_factory=list, max_length=8,
        description="کلیدواژه‌های فارسی برای پیدا کردن محصولات مرتبط در فروشگاه",
    )

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return _clean_hashtags(v)


# ---------------- اینفوگرافیک ----------------
class InfoItem(BaseModel):
    icon: str = Field(..., description=f"یکی از: {', '.join(ICONS)}")
    title: str = Field(..., max_length=40)
    desc: str = Field(..., max_length=70, description="حداکثر ۸ کلمه؛ روی تصویر نمایش داده می‌شود")

    @field_validator("icon")
    @classmethod
    def _icon(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in ICONS else "check"


class Infographic(BaseModel):
    template: Literal["checklist", "steps", "comparison", "stats"]
    title: str = Field(..., max_length=60)
    subtitle: str | None = Field(None, max_length=90)
    items: list[InfoItem] = Field(..., min_length=3, max_length=10)
    tip: str | None = Field(None, max_length=140, description="یک نکته‌ی طلایی کوتاه")
    cta: str = Field(..., max_length=60)
    caption: str = Field(..., max_length=2000, description="کپشن همراه پست، بدون هشتگ")
    hashtags: list[str] = Field(..., min_length=3, max_length=12)
    product_keywords: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return _clean_hashtags(v)


# ---------------- ویدئو ----------------
class Visual(BaseModel):
    type: Literal["stock", "product", "icon", "text_only", "ai_clip"]
    query: str = Field(..., max_length=150, description="عبارت جست‌وجو یا توصیف تصویر (انگلیسی برای stock/ai_clip)")


class Scene(BaseModel):
    index: int
    duration_sec: float = Field(..., ge=1.5, le=12)
    on_screen_text: str = Field(..., max_length=60, description="متن کوتاه روی تصویر")
    voiceover: str = Field(..., max_length=260, description="متن گوینده‌ی فارسی، محاوره‌ای و روان")
    visual: Visual


class VideoScript(BaseModel):
    title: str = Field(..., max_length=80)
    aspect: Literal["9:16", "1:1", "16:9"] = "9:16"
    hook: str = Field(..., max_length=120, description="۳ ثانیه‌ی اول؛ باید بیننده را نگه دارد")
    scenes: list[Scene] = Field(..., min_length=3, max_length=14)
    duration_sec: float = 0
    cta: str = Field(..., max_length=100)
    caption: str = Field(..., max_length=2000)
    hashtags: list[str] = Field(..., min_length=3, max_length=12)
    music_mood: str = Field("upbeat", max_length=40)
    product_keywords: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return _clean_hashtags(v)

    @model_validator(mode="after")
    def _fix_timing(self):
        for i, s in enumerate(self.scenes, start=1):
            s.index = i
        self.duration_sec = round(sum(s.duration_sec for s in self.scenes), 1)
        if not 10 <= self.duration_sec <= 95:
            raise ValueError(f"مجموع مدت صحنه‌ها باید بین ۱۰ تا ۹۵ ثانیه باشد (الان {self.duration_sec})")
        return self


SCHEMAS: dict[str, type[BaseModel]] = {
    "text": TextPost,
    "infographic": Infographic,
    "video": VideoScript,
}
