"""
سرور پنل و API (Web Service روی Render).

  GET  /                      پنل وب
  GET  /health                سلامت سرویس
  GET  /api/me                بررسی ورود + تنظیمات پیش‌فرض
  GET  /api/stats             آمار کلی
  GET  /api/jobs?limit=50     لیست jobها
  GET  /api/jobs/<id>         جزئیات یک job
  POST /api/generate          {"topic": "...", "types": ["text","infographic","video"], "model": "..."}
  POST /api/jobs/<id>/retry   اجرای دوباره‌ی یک job

همه‌ی مسیرهای /api با هدر X-Run-Token (مقدار RUN_TOKEN) محافظت می‌شوند.
فقط کتابخانه‌ی استاندارد پایتون؛ تولید محتوا در یک thread pool کوچک اجرا می‌شود.
"""
import hmac
import json
import logging
import os
import re
from datetime import timezone
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, select

from app.brain.generate import create_job, run_job
from app.config import get_settings
from app.db.models import ContentType, Job, JobStatus
from app.db.session import get_session, init_db
from app.logging_setup import setup_logging

setup_logging()
log = logging.getLogger("server")

RUN_TOKEN = os.environ.get("RUN_TOKEN", "")
WEB_DIR = Path(__file__).resolve().parent.parent / "app" / "web"
MAX_WORKERS = int(os.environ.get("WORKERS", "2"))
MOCK = os.environ.get("LLM_MOCK") == "1"  # فقط برای تست محلی
TYPES = [t.value for t in ContentType]
_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="gen")
_JOB_PATH = re.compile(r"^/api/jobs/([0-9a-f-]{36})(/retry)?$")


def _submit(job_id: str) -> None:
    def task():
        try:
            run_job(job_id, mock=MOCK)
        except Exception:  # noqa: BLE001
            log.exception("اجرای job %s شکست خورد", job_id[:8])
    _pool.submit(task)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite زمان را بدون منطقه‌ی زمانی برمی‌گرداند
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _summary(j: Job) -> dict:
    out = j.output or {}
    return {
        "id": j.id, "topic": j.topic, "type": j.content_type.value, "status": j.status.value,
        "title": out.get("title") if isinstance(out, dict) else None,
        "model": j.llm_model, "cost_usd": j.cost_usd, "latency_sec": j.latency_sec,
        "attempts": j.attempts, "error": j.error,
        "created_at": _iso(j.created_at),
    }


def _detail(j: Job) -> dict:
    d = _summary(j)
    d.update({"output": j.output, "platforms": j.platforms,
              "prompt_tokens": j.prompt_tokens, "completion_tokens": j.completion_tokens})
    return d


def recover_jobs() -> None:
    """بعد از ری‌استارت: jobهای نیمه‌کاره را دوباره در صف می‌گذارد."""
    with get_session() as db:
        stuck = db.scalars(select(Job).where(Job.status.in_([JobStatus.pending, JobStatus.generating]))).all()
        for j in stuck:
            j.status = JobStatus.pending
        db.commit()
        ids = [j.id for j in stuck]
    for jid in ids:
        _submit(jid)
    if ids:
        log.info("%d job نیمه‌کاره دوباره در صف قرار گرفت", len(ids))


class Handler(BaseHTTPRequestHandler):
    server_version = "content-engine"

    # ---------- helpers ----------
    def _json(self, code: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, ctype: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        given = self.headers.get("X-Run-Token", "")
        return bool(RUN_TOKEN) and hmac.compare_digest(given, RUN_TOKEN)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20_000:
            raise ValueError("درخواست خیلی بزرگ است")
        return json.loads(self.rfile.read(n) or b"{}")

    def log_message(self, fmt, *args):
        path = urlparse(self.path).path
        if path not in ("/health",) and not path.startswith("/api/jobs"):
            log.info("%s %s", self.command, path)

    # ---------- routes ----------
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html", "text/html; charset=utf-8")
        if u.path == "/health":
            return self._json(200, {"ok": True})
        if not u.path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        if not self._authorized():
            return self._json(401, {"error": "unauthorized"})

        if u.path == "/api/me":
            return self._json(200, {"ok": True, "default_model": get_settings().llm_model,
                                    "brand": get_settings().brand_name, "types": TYPES})

        if u.path == "/api/stats":
            with get_session() as db:
                rows = db.execute(select(Job.status, func.count(), func.coalesce(func.sum(Job.cost_usd), 0.0),
                                         func.avg(Job.latency_sec)).group_by(Job.status)).all()
            stats = {"total": 0, "cost_usd": 0.0, "by_status": {}, "avg_latency_sec": None}
            lat = []
            for st, cnt, cost, avg in rows:
                stats["by_status"][st.value] = cnt
                stats["total"] += cnt
                stats["cost_usd"] += float(cost or 0)
                if avg is not None and st == JobStatus.generated:
                    lat.append(float(avg))
            stats["avg_latency_sec"] = round(lat[0], 1) if lat else None
            return self._json(200, stats)

        if u.path == "/api/jobs":
            qs = parse_qs(u.query)
            limit = max(1, min(200, int((qs.get("limit") or ["50"])[0])))
            with get_session() as db:
                jobs = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)).all()
                return self._json(200, [_summary(j) for j in jobs])

        m = _JOB_PATH.match(u.path)
        if m and not m.group(2):
            with get_session() as db:
                j = db.get(Job, m.group(1))
                return self._json(200, _detail(j)) if j else self._json(404, {"error": "not found"})

        return self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        if not self._authorized():
            return self._json(401, {"error": "unauthorized"})

        if u.path == "/api/generate":
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "بدنه‌ی درخواست نامعتبر است"})
            topic = (body.get("topic") or "").strip()
            types = [t for t in (body.get("types") or []) if t in TYPES]
            model = (body.get("model") or "").strip() or None
            if not 3 <= len(topic) <= 300:
                return self._json(400, {"error": "موضوع باید بین ۳ تا ۳۰۰ کاراکتر باشد"})
            if not types:
                return self._json(400, {"error": "حداقل یک نوع محتوا انتخاب کنید"})
            if model and not re.fullmatch(r"[~\w./:-]{3,120}", model):
                return self._json(400, {"error": "اسم مدل نامعتبر است"})
            ids = []
            for t in types:
                jid = create_job(topic, t, model=model or get_settings().llm_model)
                _submit(jid)
                ids.append(jid)
            log.info("درخواست تولید: %s | %s | %s", topic[:60], types, model or "(default)")
            return self._json(202, {"job_ids": ids})

        m = _JOB_PATH.match(u.path)
        if m and m.group(2):
            with get_session() as db:
                j = db.get(Job, m.group(1))
                if not j:
                    return self._json(404, {"error": "not found"})
                if j.status in (JobStatus.pending, JobStatus.generating):
                    return self._json(409, {"error": "این job در حال اجراست"})
                j.status, j.error, j.output = JobStatus.pending, None, None
                db.commit()
            _submit(m.group(1))
            return self._json(202, {"job_id": m.group(1)})

        return self._json(404, {"error": "not found"})


def startup_test() -> None:
    """
    تست خودکار بعد از deploy، فقط وقتی STARTUP_TEST تنظیم شده باشد.
    فرمت: «موضوع|نوع۱,نوع۲|مدل» و چند مورد با ; — مدل اختیاری است.
    بعد از تست، این متغیر را خالی کنید تا با هر بیدار شدن سرویس هزینه نشود.
    """
    spec = os.environ.get("STARTUP_TEST", "").strip()
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        topic, _, rest = part.partition("|")
        types_s, _, model = rest.partition("|")
        types = [t for t in (types_s or "text").split(",") if t in TYPES]
        for t in types:
            _submit(create_job(topic.strip(), t, model=model.strip() or get_settings().llm_model))
        log.info("تست خودکار: %s | %s | %s", topic.strip()[:60], types, model.strip() or "(default)")


def main() -> None:
    init_db()
    recover_jobs()
    startup_test()
    if not RUN_TOKEN:
        log.warning("RUN_TOKEN تنظیم نشده؛ API و پنل قابل ورود نیستند.")
    port = int(os.environ.get("PORT", "10000"))
    log.info("پنل روی پورت %d آماده است (workers=%d)", port, MAX_WORKERS)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
