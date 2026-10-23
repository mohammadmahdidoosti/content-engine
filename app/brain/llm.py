"""
کلاینت LLM روی OpenRouter (سازگار با API اوپن‌ای‌آی).

- خروجی JSON می‌خواهد و با Pydantic اعتبارسنجی می‌کند.
- اگر JSON خراب یا نامعتبر بود، خطا را به مدل برمی‌گرداند و دوباره می‌خواهد.
- خطاهای شبکه/سرور را با backoff دوباره امتحان می‌کند.
- مصرف توکن، هزینه و زمان را برمی‌گرداند.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from app.brain.prompts import repair_prompt
from app.config import get_settings

log = logging.getLogger("brain.llm")


@dataclass
class LLMResult:
    data: BaseModel
    model: str
    attempts: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    latency_sec: float = 0.0
    raw_outputs: list[str] = field(default_factory=list)


class LLMError(RuntimeError):
    def __init__(self, msg: str, raw_outputs: list[str] | None = None, attempts: int = 0):
        super().__init__(msg)
        self.raw_outputs = raw_outputs or []
        self.attempts = attempts


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> dict:
    """JSON را حتی اگر مدل آن را داخل ``` یا با متن اضافه فرستاده باشد بیرون می‌کشد."""
    text = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _format_validation_error(e: ValidationError) -> str:
    lines = []
    for err in e.errors()[:15]:
        loc = ".".join(str(x) for x in err["loc"])
        lines.append(f"- {loc}: {err['msg']}")
    return "\n".join(lines)


class OpenRouterLLM:
    def __init__(self, model: str | None = None):
        s = get_settings()
        if not s.openrouter_api_key or s.openrouter_api_key.startswith("sk-or-v1-xxxx"):
            raise LLMError("OPENROUTER_API_KEY در فایل .env تنظیم نشده است.")
        self.model = model or s.llm_model
        self.settings = s
        self.client = OpenAI(
            api_key=s.openrouter_api_key,
            base_url=s.openrouter_base_url,
            timeout=s.llm_timeout_sec,
            max_retries=0,  # retry را خودمان کنترل می‌کنیم
            default_headers={"HTTP-Referer": s.brand_site_url, "X-Title": "content-engine"},
        )

    def _call(self, messages: list[dict]):
        extra_body: dict = {
            # فقط providerهایی که response_format را پشتیبانی می‌کنند
            "provider": {"require_parameters": True},
            "usage": {"include": True},
        }
        if self.settings.llm_reasoning_effort:
            extra_body["reasoning"] = {"effort": self.settings.llm_reasoning_effort}

        delay = 2.0
        for net_try in range(1, 5):
            try:
                return self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.settings.llm_temperature,
                    response_format={"type": "json_object"},
                    extra_body=extra_body,
                )
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                log.warning("خطای موقت (%s)، تلاش مجدد %d بعد از %.0f ثانیه", type(e).__name__, net_try, delay)
            except APIStatusError as e:
                if e.status_code >= 500:
                    log.warning("خطای سرور %s، تلاش مجدد %d", e.status_code, net_try)
                else:
                    # 400/401/402/404 ... دوباره امتحان کردن فایده ندارد
                    raise LLMError(f"OpenRouter خطای {e.status_code} داد: {e.message}") from e
            time.sleep(delay)
            delay *= 2
        raise LLMError("OpenRouter بعد از چند تلاش پاسخ نداد.")

    def generate_json(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        raws: list[str] = []
        p_tok = c_tok = 0
        cost: float | None = None
        t0 = time.monotonic()

        max_tries = self.settings.llm_max_retries
        for attempt in range(1, max_tries + 1):
            log.info("درخواست به %s (تلاش %d/%d)", self.model, attempt, max_tries)
            resp = self._call(messages)

            if resp.usage:
                p_tok += resp.usage.prompt_tokens or 0
                c_tok += resp.usage.completion_tokens or 0
                c = getattr(resp.usage, "cost", None)
                if c is None and getattr(resp.usage, "model_extra", None):
                    c = resp.usage.model_extra.get("cost")
                if c is not None:
                    cost = (cost or 0) + float(c)

            if not resp.choices:
                raise LLMError("پاسخ خالی از OpenRouter (بدون choices).", raws, attempt)
            content = resp.choices[0].message.content or ""
            raws.append(content)

            try:
                data = schema.model_validate(extract_json(content))
                return LLMResult(
                    data=data, model=getattr(resp, "model", self.model) or self.model, attempts=attempt,
                    prompt_tokens=p_tok, completion_tokens=c_tok, cost_usd=cost,
                    latency_sec=round(time.monotonic() - t0, 2), raw_outputs=raws,
                )
            except json.JSONDecodeError as e:
                errors = f"- JSON قابل خواندن نیست: {e}"
            except ValidationError as e:
                errors = _format_validation_error(e)

            log.warning("خروجی نامعتبر در تلاش %d:\n%s", attempt, errors)
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": repair_prompt(errors)},
            ]

        raise LLMError(f"بعد از {max_tries} تلاش، خروجی معتبر به دست نیامد.", raws, max_tries)


class MockLLM:
    """برای تست بدون اینترنت و بدون هزینه: نمونه‌های ثابت برمی‌گرداند."""

    model = "mock"

    def generate_json(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
        from app.brain.mock_data import MOCK
        from app.brain.schemas import SCHEMAS

        key = next(k for k, v in SCHEMAS.items() if v is schema)
        data = schema.model_validate(MOCK[key])
        return LLMResult(data=data, model="mock", attempts=1, raw_outputs=[json.dumps(MOCK[key], ensure_ascii=False)])


def get_llm(model: str | None = None, mock: bool = False):
    return MockLLM() if mock else OpenRouterLLM(model)
