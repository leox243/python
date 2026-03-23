"""SQLite persistence layer（多專案版）"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

from loguru import logger

from config import settings

DB_PATH = settings.db_path


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Init & Migration
# ─────────────────────────────────────────────────────────────────────────────

def _needs_migration(conn: sqlite3.Connection) -> bool:
    """偵測舊 schema：tenders 無 project_id 欄位。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tenders)")}
    return "tenders" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} \
           and "project_id" not in cols


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """補齊新增欄位（不破壞現有資料）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tenders)")}
    if "detail_pending" not in cols:
        conn.execute("ALTER TABLE tenders ADD COLUMN detail_pending INTEGER DEFAULT 0")
        logger.info("DB migration: added detail_pending column")


def _migrate(conn: sqlite3.Connection) -> None:
    """舊 tenders → tenders_backup，新 tenders 含 project_id。"""
    logger.info("DB migration: renaming tenders → tenders_backup")
    conn.execute("ALTER TABLE tenders RENAME TO tenders_backup")
    _create_tenders(conn)
    conn.execute("""
        INSERT INTO tenders
            (project_id, pk, case_no, name, org_name, org_address,
             contact, phone, email, category, budget,
             notice_date, deadline, open_date, open_location,
             tender_method, location, period, notes, detail_url,
             keywords, notified, created_at)
        SELECT
            1, pk, case_no, name, org_name, org_address,
            contact, phone, email, category, budget,
            notice_date, deadline, open_date, open_location,
            tender_method, location, period, notes, detail_url,
            keywords, notified, created_at
        FROM tenders_backup
    """)
    conn.commit()
    old_cnt = conn.execute("SELECT COUNT(*) FROM tenders_backup").fetchone()[0]
    new_cnt = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
    logger.info(f"Migrated {new_cnt}/{old_cnt} tenders to new schema (project_id=1)")


def _create_tenders(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id    INTEGER NOT NULL DEFAULT 1,
            pk            TEXT NOT NULL,
            case_no       TEXT,
            name          TEXT,
            org_name      TEXT,
            org_address   TEXT,
            contact       TEXT,
            phone         TEXT,
            email         TEXT,
            category      TEXT,
            budget        TEXT,
            notice_date   TEXT,
            deadline      TEXT,
            open_date     TEXT,
            open_location TEXT,
            tender_method TEXT,
            location      TEXT,
            period        TEXT,
            notes         TEXT,
            detail_url    TEXT,
            keywords      TEXT,
            notified      INTEGER DEFAULT 0,
            detail_pending INTEGER DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(project_id, pk)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_t_project  ON tenders(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_t_notified ON tenders(notified)")


def init_db() -> None:
    with _conn() as conn:
        # ── Projects table ────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                keywords          TEXT DEFAULT '',
                email_to          TEXT DEFAULT '',
                sheet_id          TEXT DEFAULT '',
                notify_days       INTEGER DEFAULT 3,
                schedule_hour     INTEGER DEFAULT 9,
                schedule_minute   INTEGER DEFAULT 0,
                schedule_interval INTEGER DEFAULT 1,
                active            INTEGER DEFAULT 1,
                created_at        TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # ── Tenders table（含 migration）────────────────────────────────────
        if _needs_migration(conn):
            _migrate(conn)
        else:
            _create_tenders(conn)

        _ensure_columns(conn)
        conn.commit()

        # ── 若無任何專案，從 .env 建立預設專案 ──────────────────────────────
        cnt = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        if cnt == 0:
            _create_default_project(conn)

    logger.debug(f"Database ready: {Path(DB_PATH).resolve()}")


def _create_default_project(conn: sqlite3.Connection) -> None:
    """從 .env 現有設定建立第一個預設專案（id=1）。"""
    conn.execute("""
        INSERT INTO projects
            (name, keywords, email_to, sheet_id,
             notify_days, schedule_hour, schedule_minute, schedule_interval)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "預設專案",
        settings.keywords,
        settings.email_to,
        settings.google_sheet_id,
        settings.notify_days,
        settings.schedule_hour,
        settings.schedule_minute,
        settings.schedule_interval,
    ))
    conn.commit()
    logger.info("預設專案已從 .env 建立（id=1）")


# ─────────────────────────────────────────────────────────────────────────────
# Project CRUD
# ─────────────────────────────────────────────────────────────────────────────

def get_all_projects(active_only: bool = False) -> List[dict]:
    with _conn() as conn:
        sql = "SELECT * FROM projects"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY id"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]


def get_project(project_id: int) -> Optional[dict]:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(r) if r else None


def create_project(data: dict) -> int:
    cols = ("name", "keywords", "email_to", "sheet_id",
            "notify_days", "schedule_hour", "schedule_minute", "schedule_interval", "active")
    vals = tuple(data.get(c, "") for c in cols)
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO projects ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
            vals,
        )
        conn.commit()
        return cur.lastrowid


def update_project(project_id: int, data: dict) -> None:
    allowed = ("name", "keywords", "email_to", "sheet_id",
               "notify_days", "schedule_hour", "schedule_minute", "schedule_interval", "active")
    fields  = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k}=?" for k in fields)
    vals       = list(fields.values()) + [project_id]
    with _conn() as conn:
        conn.execute(f"UPDATE projects SET {set_clause} WHERE id=?", vals)
        conn.commit()


def delete_project(project_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM tenders  WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id=?",         (project_id,))
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Tender CRUD（多專案版）
# ─────────────────────────────────────────────────────────────────────────────

def is_pk_known(pk: str, project_id: int = 1) -> bool:
    with _conn() as conn:
        return conn.execute(
            "SELECT 1 FROM tenders WHERE pk=? AND project_id=?", (pk, project_id)
        ).fetchone() is not None


def save_tender(t: dict, project_id: int = 1) -> bool:
    """Insert tender. Returns True if newly inserted."""
    cols = (
        "pk", "case_no", "name", "org_name", "org_address",
        "contact", "phone", "email", "category", "budget",
        "notice_date", "deadline", "open_date", "open_location",
        "tender_method", "location", "period", "notes",
        "detail_url", "keywords", "detail_pending",
    )
    values = {c: t.get(c, "") for c in cols}
    values["detail_pending"] = int(t.get("detail_pending", 0))
    values["project_id"] = project_id

    all_cols = ("project_id",) + cols
    placeholders = ", ".join(f":{c}" for c in all_cols)
    sql = f"INSERT OR IGNORE INTO tenders ({', '.join(all_cols)}) VALUES ({placeholders})"
    with _conn() as conn:
        cur = conn.execute(sql, values)
        conn.commit()
        return cur.rowcount > 0


def update_tender_detail(pk: str, project_id: int, detail_data: dict) -> None:
    """更新 tender 的詳細欄位，並清除 detail_pending 旗標。"""
    fields = ("case_no", "name", "org_name", "org_address", "contact", "phone",
              "email", "category", "budget", "notice_date", "deadline",
              "open_date", "open_location", "tender_method", "location",
              "period", "notes", "detail_url")
    updates = {f: detail_data[f] for f in fields if f in detail_data and detail_data[f]}
    updates["detail_pending"] = 0
    set_clause = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [pk, project_id]
    with _conn() as conn:
        conn.execute(
            f"UPDATE tenders SET {set_clause} WHERE pk=? AND project_id=?", vals
        )
        conn.commit()


def get_unnotified(project_id: int = 1) -> List[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tenders WHERE notified=0 AND project_id=? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_notified(pk_list: List[str], project_id: int = 1) -> None:
    if not pk_list:
        return
    with _conn() as conn:
        conn.executemany(
            "UPDATE tenders SET notified=1 WHERE pk=? AND project_id=?",
            [(pk, project_id) for pk in pk_list],
        )
        conn.commit()
    logger.debug(f"Marked {len(pk_list)} tenders as notified (project {project_id})")


def get_project_stats(project_id: int) -> dict:
    """快速統計：總筆數 / 今日 / 未通知。"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    with _conn() as conn:
        total      = conn.execute("SELECT COUNT(*) FROM tenders WHERE project_id=?", (project_id,)).fetchone()[0]
        today_cnt  = conn.execute(
            "SELECT COUNT(*) FROM tenders WHERE project_id=? AND created_at LIKE ?",
            (project_id, f"{today}%"),
        ).fetchone()[0]
        unnotified = conn.execute(
            "SELECT COUNT(*) FROM tenders WHERE project_id=? AND notified=0",
            (project_id,),
        ).fetchone()[0]
    return {"total": total, "today": today_cnt, "unnotified": unnotified}


# ─────────────────────────────────────────────────────────────────────────────
# Legacy aliases（保持 CLI 相容）
# ─────────────────────────────────────────────────────────────────────────────

def is_known(case_no: str) -> bool:
    with _conn() as conn:
        return conn.execute(
            "SELECT 1 FROM tenders WHERE case_no=?", (case_no,)
        ).fetchone() is not None
