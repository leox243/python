"""APScheduler 定時排程模組（多專案版）。"""
from __future__ import annotations

from loguru import logger
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import settings


# ─────────────────────────────────────────────────────────────────────────────
# 單一專案執行邏輯
# ─────────────────────────────────────────────────────────────────────────────

def run_project_job(project_id: int) -> None:
    """單一專案執行：抓取 → 儲存 → 通知。"""
    from database import get_project, save_tender, get_unnotified, mark_notified
    from scraper import scrape_all_keywords
    from notifier import notify

    project = get_project(project_id)
    if not project:
        logger.error(f"Project {project_id} not found, skipping")
        return

    kw_list     = [k.strip() for k in project["keywords"].split(",") if k.strip()]
    notify_days = project.get("notify_days") or settings.notify_days

    logger.info(
        f"=== [專案 {project_id}:{project['name']}] 開始抓取，"
        f"關鍵字：{kw_list}，回溯 {notify_days} 天 ==="
    )

    try:
        tenders = scrape_all_keywords(
            keywords=kw_list,
            notify_days=notify_days,
            project_id=project_id,
        )
        for t in tenders:
            saved = save_tender(t, project_id=project_id)
            if saved:
                logger.success(f"  NEW: [{t.get('case_no')}] {t.get('name', '')[:40]}")
    except Exception as e:
        logger.error(f"scrape_all_keywords error (project {project_id}): {e}")

    # 批次通知
    pending = get_unnotified(project_id=project_id)
    if pending:
        notify(pending, project=project)
        mark_notified([t["pk"] for t in pending], project_id=project_id)
    else:
        logger.info(f"[專案 {project_id}] 無新標案需通知")

    logger.info(f"=== [專案 {project_id}:{project['name']}] 本次執行完成 ===")


def run_job() -> None:
    """向下相容：舊 CLI 指令 / 測試用，執行預設專案（id=1）。"""
    from database import init_db
    init_db()
    run_project_job(1)


# ─────────────────────────────────────────────────────────────────────────────
# 觸發器工廠
# ─────────────────────────────────────────────────────────────────────────────

def _make_trigger(project: dict):
    """根據專案設定建立 CronTrigger 或 IntervalTrigger。"""
    from datetime import datetime, timedelta
    import pytz

    hour     = int(project.get("schedule_hour",     9))
    minute   = int(project.get("schedule_minute",   0))
    interval = int(project.get("schedule_interval", 1))

    if interval <= 1:
        return CronTrigger(hour=hour, minute=minute, timezone="Asia/Taipei")
    else:
        tz    = pytz.timezone("Asia/Taipei")
        now   = datetime.now(tz)
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if start <= now:
            start += timedelta(days=1)
        return IntervalTrigger(days=interval, start_date=start, timezone="Asia/Taipei")


def _trigger_label(project: dict) -> str:
    hour     = int(project.get("schedule_hour",     9))
    minute   = int(project.get("schedule_minute",   0))
    interval = int(project.get("schedule_interval", 1))
    if interval <= 1:
        return f"每天 {hour:02d}:{minute:02d}"
    else:
        return f"每 {interval} 天 {hour:02d}:{minute:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# 主排程器
# ─────────────────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """讀取所有 active 專案，各自建立排程。"""
    from database import init_db, get_all_projects

    init_db()

    scheduler = BlockingScheduler(timezone="Asia/Taipei")
    projects  = get_all_projects(active_only=True)

    if not projects:
        logger.warning("No active projects found. Falling back to .env settings (project_id=1).")
        # fallback：用 .env 建立一個簡單排程
        trigger = CronTrigger(
            hour=settings.schedule_hour,
            minute=settings.schedule_minute,
            timezone="Asia/Taipei",
        )
        scheduler.add_job(run_job, trigger, id="project_1", name="預設專案")
        logger.info(f"Fallback scheduler: 每天 {settings.schedule_hour:02d}:{settings.schedule_minute:02d}")
    else:
        for proj in projects:
            pid     = proj["id"]
            trigger = _make_trigger(proj)
            label   = _trigger_label(proj)
            scheduler.add_job(
                run_project_job,
                trigger,
                args=[pid],
                id=f"project_{pid}",
                name=f"專案 {pid}:{proj['name']}",
                replace_existing=True,
            )
            logger.info(f"  [專案 {pid}:{proj['name']}] 排程：{label} (Asia/Taipei)")

    logger.info(f"Scheduler started with {len(projects)} project(s). Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
