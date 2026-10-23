import logging

from app.config import get_settings


def setup_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
