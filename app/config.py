from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "~deepseek/deepseek-pro-latest"
    llm_reasoning_effort: str = ""
    llm_temperature: float = 0.7
    llm_max_retries: int = 3
    llm_timeout_sec: int = 120

    # DB
    database_url: str = "sqlite:///./content_engine.db"

    # Brand
    brand_name: str = "کاراوان صنعت"
    brand_site_url: str = "https://caravansanat.com"
    brand_niche: str = "کاراوان، کمپینگ، سفر با کاراوان و تجهیزات آن"
    brand_tone: str = "صمیمی، کاربردی و قابل‌اعتماد؛ محاوره‌ی مؤدبانه، بدون اغراق تبلیغاتی"

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
