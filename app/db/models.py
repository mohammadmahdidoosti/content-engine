import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ContentType(str, enum.Enum):
    text = "text"
    infographic = "infographic"
    video = "video"


class JobStatus(str, enum.Enum):
    pending = "pending"        # ساخته شده، هنوز شروع نشده
    generating = "generating"  # در حال تولید با LLM
    generated = "generated"    # JSON معتبر آماده است
    failed = "failed"          # خطا (جزئیات در error)
    # مراحل بعدی: rendering, rendered, awaiting_approval, approved, scheduled, published


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[ContentType] = mapped_column(Enum(ContentType, name="content_type"), nullable=False)
    platforms: Mapped[list] = mapped_column(JSON, default=list)  # ["telegram", "instagram"]
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), default=JobStatus.pending, nullable=False
    )

    llm_model: Mapped[str | None] = mapped_column(String(200))
    output: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    latency_sec: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    def __repr__(self) -> str:
        return f"<Job {self.id[:8]} {self.content_type.value} {self.status.value}>"
