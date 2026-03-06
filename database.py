"""SQLite persistence layer."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List

from loguru import logger

from config import settings


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tenders (
                pk           TEXT PRIMARY KEY,
                case_no      TEXT UNIQUE,
                name         TEXT,
                org_name     TEXT,
                org_address  TEXT,
                contact      TEXT,
                phone        TEXT,
                email        TEXT,
                category     TEXT,
                budget       TEXT,
                notice_date  TEXT,
                deadline     TEXT,
                open_date    TEXT,
                open_location TEXT,
                tender_method TEXT,
                location     TEXT,
                period       TEXT,
                notes        TEXT,
                detail_url   TEXT,
                keywords     TEXT,
                notified     INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_case_no  ON tenders(case_no)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notified ON tenders(notified)")
        conn.commit()
    logger.debug(f"Database ready: {Path(settings.db_path).resolve()}")


def is_known(case_no: str) -> bool:
    with _conn() as conn:
        return conn.execute(
            "SELECT 1 FROM tenders WHERE case_no=?", (case_no,)
        ).fetchone() is not None


def save_tender(t: dict) -> bool:
    """Insert tender. Returns True if newly inserted, False if duplicate."""
    cols = (
        "pk", "case_no", "name", "org_name", "org_address",
        "contact", "phone", "email", "category", "budget",
        "notice_date", "deadline", "open_date", "open_location",
        "tender_method", "location", "period", "notes",
        "detail_url", "keywords",
    )
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = f"INSERT OR IGNORE INTO tenders ({', '.join(cols)}) VALUES ({placeholders})"
    with _conn() as conn:
        cur = conn.execute(sql, {c: t.get(c, "") for c in cols})
        conn.commit()
        return cur.rowcount > 0


def get_unnotified() -> List[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tenders WHERE notified=0 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_notified(pk_list: List[str]) -> None:
    if not pk_list:
        return
    with _conn() as conn:
        conn.executemany(
            "UPDATE tenders SET notified=1 WHERE pk=?",
            [(pk,) for pk in pk_list],
        )
        conn.commit()
    logger.debug(f"Marked {len(pk_list)} tenders as notified")
