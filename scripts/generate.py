"""
تولید محتوا برای یک موضوع (مرحله ۱).

مثال‌ها:
  python -m scripts.generate --topic "چک‌لیست ۷ موردی قبل از حرکت با کاراوان" --type all
  python -m scripts.generate --topic "..." --type video --model anthropic/claude-sonnet-<نسخه>
  python -m scripts.generate --topic "..." --type text --models "~deepseek/deepseek-pro-latest,anthropic/claude-sonnet-<نسخه>"
  python -m scripts.generate --topic "..." --type all --mock      # بدون اینترنت و هزینه
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from app.brain.generate import create_and_run_job
from app.config import get_settings
from app.db.models import JobStatus
from app.db.session import init_db
from app.logging_setup import setup_logging

DEFAULT_TOPIC = "چک‌لیست ۷ موردی قبل از حرکت با کاراوان"
TYPES = ["text", "infographic", "video"]


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def main() -> int:
    ap = argparse.ArgumentParser(description="تولید محتوا با LLM")
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--type", choices=TYPES + ["all"], default="all")
    ap.add_argument("--model", help="مدل OpenRouter (پیش‌فرض: LLM_MODEL در .env)")
    ap.add_argument("--models", help="چند مدل با کاما، برای مقایسه")
    ap.add_argument("--mock", action="store_true", help="بدون فراخوانی API")
    ap.add_argument("--out", default="outputs")
    args = ap.parse_args()

    setup_logging()
    log = logging.getLogger("generate")
    init_db()

    types = TYPES if args.type == "all" else [args.type]
    if args.mock:
        models = ["mock"]
    elif args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        models = [args.model or get_settings().llm_model]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, total_cost, failed = [], 0.0, 0

    log.info("موضوع: %s | انواع: %s | مدل‌ها: %s", args.topic, types, models)
    for model in models:
        for ctype in types:
            job = create_and_run_job(args.topic, ctype, model=None if args.mock else model, mock=args.mock)
            fname = out_dir / f"{job.id[:8]}_{ctype}_{_safe(model)}.json"
            fname.write_text(json.dumps({
                "job_id": job.id, "topic": job.topic, "type": ctype, "model": job.llm_model,
                "status": job.status.value, "error": job.error, "attempts": job.attempts,
                "tokens": {"prompt": job.prompt_tokens, "completion": job.completion_tokens},
                "cost_usd": job.cost_usd, "latency_sec": job.latency_sec, "output": job.output,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            ok = job.status == JobStatus.generated
            failed += not ok
            total_cost += job.cost_usd or 0
            rows.append((model, ctype, "✅" if ok else "❌", job.attempts, job.latency_sec,
                         job.cost_usd, str(fname)))

    print("\n" + "=" * 100)
    print(f"{'model':<40} {'type':<12} {'ok':<3} {'try':<4} {'sec':<7} {'cost$':<10} file")
    print("-" * 100)
    for m, t, ok, att, sec, cost, f in rows:
        cost_s = f"{cost:.5f}" if cost is not None else "-"
        print(f"{m:<40} {t:<12} {ok:<3} {att or 0:<4} {sec or 0:<7} {cost_s:<10} {f}")
    print("-" * 100)
    print(f"total cost: ${total_cost:.5f} | failed: {failed}/{len(rows)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
