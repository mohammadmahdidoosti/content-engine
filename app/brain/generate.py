"""تولید محتوا برای یک موضوع و ثبت در جدول jobs."""
import logging

from app.brain.llm import LLMError, LLMResult, get_llm
from app.brain.prompts import system_prompt, user_prompt
from app.brain.schemas import SCHEMAS
from app.db.models import ContentType, Job, JobStatus
from app.db.session import get_session

log = logging.getLogger("brain.generate")


def generate_content(topic: str, content_type: str, model: str | None = None, mock: bool = False) -> LLMResult:
    """فقط تولید، بدون دیتابیس."""
    if content_type not in SCHEMAS:
        raise ValueError(f"نوع محتوای نامعتبر: {content_type}")
    llm = get_llm(model, mock=mock)
    return llm.generate_json(system_prompt(), user_prompt(topic, content_type), SCHEMAS[content_type])


def create_and_run_job(
    topic: str, content_type: str, platforms: list[str] | None = None,
    model: str | None = None, mock: bool = False,
) -> Job:
    """یک job می‌سازد، تولید را اجرا می‌کند و نتیجه یا خطا را ذخیره می‌کند."""
    with get_session() as db:
        job = Job(topic=topic, content_type=ContentType(content_type),
                  platforms=platforms or ["telegram", "instagram"], status=JobStatus.generating)
        db.add(job)
        db.commit()
        log.info("job %s ساخته شد (%s)", job.id[:8], content_type)

        try:
            res = generate_content(topic, content_type, model=model, mock=mock)
            job.status = JobStatus.generated
            job.output = res.data.model_dump(mode="json")
            job.llm_model = res.model
            job.attempts = res.attempts
            job.prompt_tokens = res.prompt_tokens
            job.completion_tokens = res.completion_tokens
            job.cost_usd = res.cost_usd
            job.latency_sec = res.latency_sec
        except LLMError as e:
            job.status = JobStatus.failed
            job.error = str(e)
            job.attempts = e.attempts
            job.llm_model = model or None
            if e.raw_outputs:
                job.output = {"_raw_last": e.raw_outputs[-1][:5000]}
            log.error("job %s شکست خورد: %s", job.id[:8], e)
        except Exception as e:  # noqa: BLE001
            job.status = JobStatus.failed
            job.error = f"{type(e).__name__}: {e}"
            log.exception("خطای پیش‌بینی‌نشده در job %s", job.id[:8])

        db.commit()
        return job
