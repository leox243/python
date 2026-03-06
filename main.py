"""
標案爬蟲系統 – 命令列入口

用法：
  python main.py run          # 立即執行一次（抓取 + 儲存 + 通知）
  python main.py schedule     # 啟動排程（依 .env SCHEDULE_HOUR/MINUTE 每天執行）
  python main.py test         # 測試爬蟲（抓第一頁，不寫入 DB，不發通知）
  python main.py list         # 列出 DB 中最近 20 筆標案
"""
from __future__ import annotations

import sys
from loguru import logger

# ── Logger setup ──────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    level="INFO",
    colorize=True,
)
logger.add(
    "logs/tender_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="DEBUG",
    encoding="utf-8",
)


# ─────────────────────────────────────────────────────────────────────────────

def cmd_run() -> None:
    from scheduler import run_job
    run_job()


def cmd_schedule() -> None:
    from scheduler import start_scheduler
    start_scheduler()


def cmd_test() -> None:
    """Quick test: scrape first keyword, print results, no DB write."""
    from config import settings
    from scraper import scrape_keyword

    kw = settings.keyword_list[0] if settings.keyword_list else "資訊系統"
    logger.info(f"TEST MODE – keyword: '{kw}' (no DB write, no notifications)")

    # Override notify_days to 9999 so we see results regardless of date
    settings.__dict__["notify_days"] = 9999
    # Fetch only 1 page
    settings.__dict__["max_pages"] = 1

    tenders = scrape_keyword(kw, fetch_detail=False)

    if not tenders:
        logger.warning("No results found")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"{'案號':<15} {'公告日':>10}  {'截止投標':>12}  標案名稱")
    logger.info(f"{'─'*60}")
    for t in tenders[:20]:
        logger.info(
            f"{t.get('case_no',''):<15} "
            f"{t.get('notice_date',''):>10}  "
            f"{t.get('deadline',''):>12}  "
            f"{t.get('name','')[:30]}"
        )
    logger.info(f"{'='*60}")
    logger.info(f"共 {len(tenders)} 筆（最多顯示 20 筆）")


def cmd_list() -> None:
    """Show last 20 tenders from DB."""
    import sqlite3
    from config import settings

    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT case_no, name, org_name, notice_date, deadline, keywords, notified "
        "FROM tenders ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()

    if not rows:
        logger.info("DB is empty – run 'python main.py run' first")
        return

    logger.info(f"\n{'案號':<15} {'公告日':>10}  {'截止':>12}  {'已通知':>4}  標案名稱")
    logger.info("─" * 70)
    for r in rows:
        logger.info(
            f"{r['case_no']:<15} "
            f"{r['notice_date']:>10}  "
            f"{r['deadline']:>12}  "
            f"{'✓' if r['notified'] else '─':>4}  "
            f"{r['name'][:30]}"
        )


# ─────────────────────────────────────────────────────────────────────────────

COMMANDS = {
    "run":      cmd_run,
    "schedule": cmd_schedule,
    "test":     cmd_test,
    "list":     cmd_list,
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in COMMANDS:
        print(__doc__)
        sys.exit(0)
    COMMANDS[cmd]()
