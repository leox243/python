"""
標案通知系統 – Web UI（Flask，多專案版）

路由：
  GET  /                        → 專案列表儀表板
  GET  /projects/new            → 新增專案
  GET  /projects/<id>/edit      → 編輯專案
  GET  /projects/<id>           → 專案標案列表
  GET  /settings                → 全域設定（SMTP / OAuth）

API：
  GET  /api/projects                    → 所有專案 + 統計
  POST /api/projects                    → 新增專案
  PUT  /api/projects/<id>               → 更新專案
  DELETE /api/projects/<id>             → 刪除專案
  POST /api/projects/<id>/run           → 立即執行
  GET  /api/projects/<id>/tenders       → 專案標案（分頁）
  GET  /api/settings                    → 全域設定
  POST /api/settings                    → 儲存全域設定

用法：
  python main.py web            # 啟動 Web UI（預設 http://127.0.0.1:5050）
  python main.py web --port 8080
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path

from dotenv import set_key
from flask import Flask, jsonify, render_template, request
from loguru import logger

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"

app = Flask(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _settings():
    """建立新的 Settings 實例以讀取最新 .env。"""
    from config import Settings
    return Settings(_env_file=str(ENV_FILE))


def _sheet_url(sheet_id: str) -> str:
    if not sheet_id:
        return ""
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"


def _extract_sheet_id(url_or_id: str) -> str:
    """從 Google Sheets URL 或 ID 字串取出 sheet ID。"""
    url_or_id = (url_or_id or "").strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    if m:
        return m.group(1)
    if "/" not in url_or_id:
        return url_or_id
    return ""


def _project_to_dict(proj: dict) -> dict:
    """補充 sheet_url、next_run_label 等前端用欄位。"""
    d = dict(proj)
    d["sheet_url"]      = _sheet_url(d.get("sheet_id", ""))
    interval            = int(d.get("schedule_interval", 1))
    hour                = int(d.get("schedule_hour",     9))
    minute              = int(d.get("schedule_minute",   0))
    if interval <= 1:
        d["next_run_label"] = f"每天 {hour:02d}:{minute:02d}"
    else:
        d["next_run_label"] = f"每 {interval} 天 {hour:02d}:{minute:02d}"
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Page routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html", active="dashboard")


@app.route("/projects/new")
def project_new():
    return render_template("projects/form.html", active="dashboard", project=None)


@app.route("/projects/<int:project_id>/edit")
def project_edit(project_id: int):
    from database import get_project
    proj = get_project(project_id)
    if not proj:
        return "Project not found", 404
    return render_template("projects/form.html", active="dashboard", project=proj)


@app.route("/projects/<int:project_id>")
def project_detail(project_id: int):
    from database import get_project
    proj = get_project(project_id)
    if not proj:
        return "Project not found", 404
    return render_template("projects/detail.html", active="dashboard", project=proj)


@app.route("/settings")
def settings_page():
    return render_template("settings.html", active="settings")


@app.route("/logs")
def logs_page():
    return render_template("logs.html", active="logs")


# ─────────────────────────────────────────────────────────────────────────────
# API – Projects CRUD
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/projects", methods=["GET"])
def api_list_projects():
    from database import get_all_projects, get_project_stats
    projects = get_all_projects()
    result = []
    for p in projects:
        d = _project_to_dict(p)
        stats = get_project_stats(p["id"])
        d.update(stats)
        d["keywords_list"] = [k.strip() for k in (p.get("keywords") or "").split(",") if k.strip()]
        result.append(d)
    return jsonify(result)


@app.route("/api/projects", methods=["POST"])
def api_create_project():
    from database import create_project
    data = request.get_json() or {}

    # sheet_url → sheet_id
    if "sheet_url" in data:
        data["sheet_id"] = _extract_sheet_id(data.pop("sheet_url"))

    new_id = create_project(data)
    logger.info(f"Project created: id={new_id} name={data.get('name')}")
    return jsonify({"ok": True, "id": new_id}), 201


@app.route("/api/projects/<int:project_id>", methods=["PUT"])
def api_update_project(project_id: int):
    from database import update_project
    data = request.get_json() or {}

    if "sheet_url" in data:
        data["sheet_id"] = _extract_sheet_id(data.pop("sheet_url"))

    update_project(project_id, data)
    logger.info(f"Project updated: id={project_id}")
    return jsonify({"ok": True})


@app.route("/api/projects/<int:project_id>", methods=["DELETE"])
def api_delete_project(project_id: int):
    from database import delete_project
    delete_project(project_id)
    logger.info(f"Project deleted: id={project_id}")
    return jsonify({"ok": True})


@app.route("/api/projects/<int:project_id>/run", methods=["POST"])
def api_run_project(project_id: int):
    """背景執行指定專案。"""
    def _run():
        try:
            from database import init_db
            from scheduler import run_project_job
            init_db()
            run_project_job(project_id)
        except Exception as e:
            logger.error(f"Manual run error (project {project_id}): {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"ok": True, "message": "已開始執行，約 1–3 分鐘後重新整理查看結果"})


# ─────────────────────────────────────────────────────────────────────────────
# API – Tenders（按專案）
# ─────────────────────────────────────────────────────────────────────────────

_SORTABLE_COLS = {"keywords", "name", "org_name", "budget", "notice_date", "deadline", "created_at"}


@app.route("/api/projects/<int:project_id>/tenders")
def api_project_tenders(project_id: int):
    from database import _conn
    page     = max(1, int(request.args.get("page",     1)))
    per_page = int(request.args.get("per_page", 50))
    keyword  = request.args.get("keyword", "").strip()
    sort_by  = request.args.get("sort_by",  "notice_date")
    sort_dir = request.args.get("sort_dir", "desc").lower()
    offset   = (page - 1) * per_page

    if sort_by  not in _SORTABLE_COLS: sort_by  = "notice_date"
    if sort_dir not in ("asc", "desc"): sort_dir = "desc"

    where_parts = ["project_id=?"]
    args_base   = [project_id]
    if keyword:
        where_parts.append("keywords LIKE ?")
        args_base.append(f"%{keyword}%")
    where = "WHERE " + " AND ".join(where_parts)
    order = f"ORDER BY {sort_by} {sort_dir.upper()}" + ("" if sort_by == "created_at" else ", created_at DESC")

    with _conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM tenders {where}", args_base
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT pk, case_no, name, org_name, budget, notice_date, deadline, "
            f"open_date, keywords, detail_url, notified, detail_pending, created_at "
            f"FROM tenders {where} {order} "
            f"LIMIT ? OFFSET ?",
            args_base + [per_page, offset],
        ).fetchall()

    return jsonify({
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "items":    [dict(r) for r in rows],
    })


@app.route("/api/tenders/<pk>/fetch-detail", methods=["POST"])
def api_fetch_tender_detail(pk: str):
    """手動補抓 openfun.app detail（供 UI 按鈕使用）。"""
    from database import _conn, update_tender_detail
    from scraper import fetch_tender_detail

    # 確認 tender 存在
    with _conn() as conn:
        row = conn.execute(
            "SELECT project_id FROM tenders WHERE pk=?", (pk,)
        ).fetchone()
    if not row:
        return jsonify({"ok": False, "message": "找不到此標案"}), 404

    project_id = row["project_id"]

    # 背景補抓
    detail = fetch_tender_detail(pk)
    if not detail:
        return jsonify({"ok": False, "message": "openfun.app 尚未更新，請稍後再試"}), 200

    update_tender_detail(pk, project_id, detail)
    logger.info(f"Detail updated for tender {pk}")
    return jsonify({"ok": True, "message": "資料已補完", "data": detail})


@app.route("/api/logs")
def api_logs():
    """讀取 logs/ 目錄下的日誌檔。"""
    import os
    logs_dir    = BASE_DIR / "logs"
    date_str    = request.args.get("date", "").strip()
    lines_count = int(request.args.get("lines", 400))

    if not logs_dir.exists():
        return jsonify({"files": [], "content": "", "date": "", "error": "logs 目錄不存在"})

    log_files = sorted(
        [f.stem.replace("tender_", "") for f in logs_dir.glob("tender_*.log")],
        reverse=True,
    )

    if not date_str:
        date_str = log_files[0] if log_files else ""

    if not date_str:
        return jsonify({"files": log_files, "content": "", "date": ""})

    log_file = logs_dir / f"tender_{date_str}.log"
    if not log_file.exists():
        return jsonify({"files": log_files, "content": "", "date": date_str, "error": "找不到該日期的 log"})

    with open(log_file, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    last_lines = all_lines[-lines_count:]
    return jsonify({
        "files":       log_files,
        "date":        date_str,
        "content":     "".join(last_lines),
        "total_lines": len(all_lines),
        "showing":     len(last_lines),
    })


# ─────────────────────────────────────────────────────────────────────────────
# API – Global settings (SMTP / OAuth)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    s = _settings()
    return jsonify({
        # SMTP
        "smtp_host":     s.smtp_host,
        "smtp_port":     s.smtp_port,
        "smtp_user":     s.smtp_user,
        "smtp_password": s.smtp_password,
        "email_from":    s.email_from,
        # Google OAuth
        "google_oauth_client_id":     s.google_oauth_client_id,
        "google_oauth_client_secret": s.google_oauth_client_secret,
        "google_oauth_refresh_token": s.google_oauth_refresh_token,
        "google_sheet_name":          s.google_sheet_name,
        # status flags
        "email_enabled":  s.email_enabled,
        "google_enabled": s.google_enabled,
    })


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json() or {}

    mapping = {
        "smtp_host":                  "SMTP_HOST",
        "smtp_port":                  "SMTP_PORT",
        "smtp_user":                  "SMTP_USER",
        "smtp_password":              "SMTP_PASSWORD",
        "email_from":                 "EMAIL_FROM",
        "google_oauth_client_id":     "GOOGLE_OAUTH_CLIENT_ID",
        "google_oauth_client_secret": "GOOGLE_OAUTH_CLIENT_SECRET",
        "google_oauth_refresh_token": "GOOGLE_OAUTH_REFRESH_TOKEN",
        "google_sheet_name":          "GOOGLE_SHEET_NAME",
    }
    for field, env_key in mapping.items():
        if field in data and data[field] is not None:
            set_key(str(ENV_FILE), env_key, str(data[field]))

    logger.info("Global settings updated via Web UI")
    return jsonify({"ok": True, "message": "全域設定已儲存"})


# ─────────────────────────────────────────────────────────────────────────────
# 向下相容：舊 /api/run（執行專案 1）
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
def api_run():
    return api_run_project(1)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def start_web(host: str = "127.0.0.1", port: int = 5050) -> None:
    from database import init_db
    init_db()
    logger.info(f"Web UI 啟動：http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)
