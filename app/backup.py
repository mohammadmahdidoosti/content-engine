"""
پشتیبان کامل دیتابیس (همه‌ی جدول‌ها) به یک فایل JSON فشرده و بازگردانی آن.

دیتابیس رایگان Render ۳۰ روز بعد از ساخت منقضی می‌شود؛ با این فایل، داده‌ها در چند دقیقه به دیتابیس تازه
یا VPS منتقل می‌شوند. اطلاعات شخصی در دیتابیس نیست (نه نام، نه تلفن)؛ ولی داده‌ی تجاری هست، پس فایل را
فقط خودتان نگه دارید.
"""
import gzip
import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import DateTime, Date, delete, insert, select

from app.db.models import Base
from app.db.session import get_engine, get_setting, init_db, set_setting

log = logging.getLogger("backup")
FORMAT = "content-engine-backup/1"


def _tables():
    init_db()   # همه‌ی مدل‌ها ثبت شوند
    return list(Base.metadata.sorted_tables)


def _enc(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def export_bytes() -> tuple[bytes, dict]:
    out = {"format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    counts = {}
    with get_engine().connect() as conn:
        for t in _tables():
            cols = [c.name for c in t.columns]
            rows = [[_enc(r[i]) for i in range(len(cols))] for r in conn.execute(select(t)).all()]
            out["tables"][t.name] = {"columns": cols, "rows": rows}
            counts[t.name] = len(rows)
    data = gzip.compress(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
    meta = {"at": out["created_at"], "bytes": len(data), "counts": counts}
    try:
        set_setting("backup.last", meta)
    except Exception:  # noqa: BLE001
        pass
    log.info("پشتیبان: %d جدول، %d بایت", len(counts), len(data))
    return data, meta


def restore_bytes(data: bytes) -> dict:
    try:
        raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        doc = json.loads(raw)
    except (OSError, ValueError) as e:
        raise ValueError("فایل پشتیبان خوانده نشد") from e
    if doc.get("format") != FORMAT:
        raise ValueError("این فایل پشتیبان این ابزار نیست")
    tables = _tables()
    src = doc.get("tables") or {}
    done = {}
    with get_engine().begin() as conn:
        for t in reversed(tables):                        # اول فرزندها پاک شوند
            if t.name in src:
                conn.execute(delete(t))
        for t in tables:
            block = src.get(t.name)
            if not block:
                continue
            cols = [c for c in block["columns"] if c in t.columns]
            idx = {c: block["columns"].index(c) for c in cols}
            dt_cols = {c.name: type(c.type) for c in t.columns if isinstance(c.type, (DateTime, Date))}
            batch = []
            for r in block["rows"]:
                rec = {}
                for c in cols:
                    v = r[idx[c]]
                    if v is not None and c in dt_cols and isinstance(v, str):
                        v = datetime.fromisoformat(v) if issubclass(dt_cols[c], DateTime) else date.fromisoformat(v)
                    rec[c] = v
                batch.append(rec)
                if len(batch) >= 1000:
                    conn.execute(insert(t), batch)
                    batch = []
            if batch:
                conn.execute(insert(t), batch)
            done[t.name] = len(block["rows"])
        _fix_sequences(conn, tables)
    log.info("بازگردانی پشتیبان: %s", done)
    return {"restored": done, "backup_created_at": doc.get("created_at")}


def _fix_sequences(conn, tables) -> None:
    """در Postgres، شمارنده‌ی کلیدهای خودکار بعد از درج دستی باید جلو برود."""
    if conn.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    for t in tables:
        for c in t.primary_key.columns:
            if c.autoincrement is True or (c.autoincrement == "auto" and str(c.type) in ("INTEGER", "BIGINT")):
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{t.name}', '{c.name}'), "
                    f"COALESCE((SELECT MAX({c.name}) FROM {t.name}), 0) + 1, false) "
                    f"WHERE pg_get_serial_sequence('{t.name}', '{c.name}') IS NOT NULL"))


def status() -> dict:
    import os
    exp = os.environ.get("DB_EXPIRES_AT") or get_setting("db.expires_at") or "2026-11-22T16:43:14+00:00"
    days = None
    try:
        days = (datetime.fromisoformat(exp) - datetime.now(timezone.utc)).days
    except ValueError:
        pass
    return {"last": get_setting("backup.last"), "db_expires_at": exp, "db_days_left": days}
