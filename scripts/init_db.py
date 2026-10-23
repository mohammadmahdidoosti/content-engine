"""ساخت جدول‌ها و بررسی اتصال دیتابیس."""
import logging

from sqlalchemy import inspect, text

from app.config import get_settings
from app.db.session import get_engine, init_db
from app.logging_setup import setup_logging


def main() -> None:
    setup_logging()
    log = logging.getLogger("init_db")
    url = get_settings().database_url
    log.info("اتصال به دیتابیس: %s", url.split("@")[-1])
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    init_db()
    tables = inspect(get_engine()).get_table_names()
    log.info("✅ دیتابیس آماده است. جدول‌ها: %s", tables)


if __name__ == "__main__":
    main()
