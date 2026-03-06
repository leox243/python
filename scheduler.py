"""APScheduler 定時排程模組。"""
from __future__ import annotations

from loguru import logger
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings


def run_job() -> None:
    """單次執行：抓取所有關鍵字 → 儲存 → 通知。"""
    from database import init_db, save_tender, get_unnotified, mark_notified
    from scraper import scrape_keyword
    from notifier import notify

    init_db()
    logger.info(f"=== 開始爬取，關鍵字：{settings.keyword_list} ===")

    for kw in settings.keyword_list:
        try:
            tenders = scrape_keyword(kw)
            for t in tenders:
                saved = save_tender(t)
                if saved:
                    logger.success(f"  NEW: [{t.get('case_no')}] {t.get('name', '')[:40]}")
        except Exception as e:
            logger.error(f"Keyword '{kw}' scrape error: {e}")

    # Collect all unnotified and send once (batch)
    pending = get_unnotified()
    if pending:
        notify(pending)
        mark_notified([t["pk"] for t in pending])
    else:
        logger.info("No new tenders to notify")

    logger.info("=== 本次執行完成 ===")


def start_scheduler() -> None:
    """啟動排程，依 .env 設定的 hour/minute 每天執行一次。"""
    scheduler = BlockingScheduler(timezone="Asia/Taipei")
    trigger = CronTrigger(
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        timezone="Asia/Taipei",
    )
    scheduler.add_job(run_job, trigger, id="tender_crawl", name="標案爬蟲")
    logger.info(
        f"Scheduler started – will run daily at "
        f"{settings.schedule_hour:02d}:{settings.schedule_minute:02d} (Asia/Taipei)"
    )
    logger.info("Press Ctrl+C to stop")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
