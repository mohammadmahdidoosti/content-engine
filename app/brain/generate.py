"""تولید محتوا برای یک موضوع و ثبت در جدول jobs."""
import logging

from app.brain.llm import LLMError, LLMResult, get_llm
from app.brain.prompts import system_prompt, user_prompt
from app.brain.schemas import SCHEMAS
from app.db.models import ContentType, Job, JobStatus
from app.db.session import get_session
from app.text_utils import normalize_obj

log = logging.getLogger("brain.generate")


def generate_content(topic: str, content_type: str, model: str | None = None, mock: bool = False) -> LLMResult:
    """فقط تولید، بدون دیتابیس."""
    if content_type not in SCHEMAS:
        raise ValueError(f"نوع محتوای نامعتبر: {content_type}")
    llm = get_llm(model, mock=mock)
    return llm.generate_json(system_prompt(), user_prompt(topic, content_type), SCHEMAS[content_type])


def create_job(topic: str, content_type: str, platforms: list[str] | None = None,
               model: str | None = None) -> str:
    """یک job در وضعیت pending می‌سازد و id آن را برمی‌گرداند."""
    with get_session() as db:
        job = Job(topic=topic, content_type=ContentType(content_type),
                  platforms=platforms or ["telegram", "instagram"],
                  status=JobStatus.pending, llm_model=model)
        db.add(job)
        db.commit()
        log.info("job %s ساخته شد (%s)", job.id[:8], content_type)
        return job.id


def run_job(job_id: str, model: str | None = None, mock: bool = False) -> Job:
    """job را اجرا می‌کند و نتیجه یا خطا را ذخیره می‌کند."""
    with get_session() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise ValueError(f"job پیدا نشد: {job_id}")
        job.status = JobStatus.generating
        db.commit()
        model = model or job.llm_model

        try:
            res = generate_content(job.topic, job.content_type.value, model=model, mock=mock)
            job.status = JobStatus.generated
            job.output = normalize_obj(res.data.model_dump(mode="json"))
            job.llm_model = res.model
            job.attempts = res.attempts
            job.prompt_tokens = res.prompt_tokens
            job.completion_tokens = res.completion_tokens
            job.cost_usd = res.cost_usd
            job.latency_sec = res.latency_sec
            log.info("job %s آماده شد (%.1fs, $%s)", job.id[:8], res.latency_sec, res.cost_usd)
        except LLMError as e:
            job.status = JobStatus.failed
            job.error = str(e)
            job.attempts = e.attempts
            if e.raw_outputs:
                job.output = {"_raw_last": e.raw_outputs[-1][:5000]}
            log.error("job %s شکست خورد: %s", job.id[:8], e)
        except Exception as e:  # noqa: BLE001
            job.status = JobStatus.failed
            job.error = f"{type(e).__name__}: {e}"
            log.exception("خطای پیش‌بینی‌نشده در job %s", job.id[:8])

        db.commit()
        return job


def create_and_run_job(topic: str, content_type: str, platforms: list[str] | None = None,
                       model: str | None = None, mock: bool = False) -> Job:
    job_id = create_job(topic, content_type, platforms, model=None if mock else model)
    return run_job(job_id, model=model, mock=mock)
