"""
政府電子採購網 全文檢索爬蟲

API 特性（經逆向分析確認）：
- 搜尋結果：純 GET 請求，無需 JS 渲染
- 分頁參數：d-3611040-p=<頁碼>（第一頁不需帶）
- 詳細頁面：GET /tps/QueryTender/query/searchTenderDetail?pkPmsMain=<base64>
- 每頁最多 100 筆
"""
from __future__ import annotations

import math
import re
import time
import random
from datetime import datetime, timedelta
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config import settings

BASE_URL = "https://web.pcc.gov.tw"
SEARCH_URL = f"{BASE_URL}/prkms/tender/common/bulletion/readBulletion"
DETAIL_URL = f"{BASE_URL}/tps/QueryTender/query/searchTenderDetail"
INDEX_URL  = f"{BASE_URL}/prkms/tender/common/bulletion/indexBulletion"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": INDEX_URL,
}


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    """Create a session and pre-fetch the index page to obtain cookies."""
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(INDEX_URL, timeout=20)
        logger.debug("Session cookie obtained from index page")
    except Exception as e:
        logger.warning(f"Index page pre-fetch failed (continuing anyway): {e}")
    return s


def _minguo_year() -> int:
    return datetime.now().year - 1911


def _sleep() -> None:
    """Random sleep between requests to avoid rate limiting."""
    time.sleep(random.uniform(2.0, 5.0))


# ─────────────────────────────────────────────────────────────────────────────
# Search / results page
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_search_page(
    session: requests.Session, keyword: str, page: int, page_size: int = 100
) -> str:
    params: dict = {
        "querySentence":   keyword,
        "tenderStatusType": settings.tender_status_type,
        "sortCol":         "TENDER_NOTICE_DATE",
        "timeRange":       str(_minguo_year()),
        "pageSize":        str(page_size),
    }
    if page > 1:
        params["d-3611040-p"] = str(page)
        _sleep()

    r = session.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def _parse_total(html: str) -> int:
    """Extract total result count from pagination text."""
    m = re.search(r"共有\s*([\d,]+)\s*筆", html)
    if m:
        return int(m.group(1).replace(",", ""))
    return 0


def _parse_results(html: str, keyword: str) -> List[dict]:
    """Parse list of tenders from a search results page."""
    soup = BeautifulSoup(html, "lxml")
    tenders = []

    for a in soup.select("a[href*='tpam?pk=']"):
        href = a.get("href", "")
        pk_m = re.search(r"pk=([A-Za-z0-9+/=]+)", href)
        if not pk_m:
            continue
        pk = pk_m.group(1)

        # Link text: "案號\n\n標案名稱"
        parts = [p.strip() for p in a.get_text("\n").split("\n") if p.strip()]
        case_no = parts[0] if parts else ""
        name    = parts[1] if len(parts) > 1 else ""

        # Walk up to the <tr> to get other columns
        row  = a.find_parent("tr")
        cols = row.find_all("td") if row else []

        # Col positions (0-indexed): 0=項次 1=種類 2=機關名稱 3=案號/名稱 4=公告日 5=決標日 6=截止 7=公開閱
        org_name     = cols[2].get_text(strip=True) if len(cols) > 2 else ""
        notice_date  = cols[4].get_text(strip=True) if len(cols) > 4 else ""
        deadline     = cols[6].get_text(strip=True) if len(cols) > 6 else ""

        tenders.append({
            "pk":          pk,
            "case_no":     case_no,
            "name":        name,
            "org_name":    org_name,
            "notice_date": notice_date,
            "deadline":    deadline,
            "detail_url":  f"{DETAIL_URL}?pkPmsMain={pk}",
            "keywords":    keyword,
            # detail fields filled in by fetch_detail()
        })

    return tenders


# ─────────────────────────────────────────────────────────────────────────────
# Detail page
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_detail(session: requests.Session, pk: str) -> str:
    _sleep()
    url = f"{DETAIL_URL}?pkPmsMain={pk}"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def _parse_detail(html: str, pk: str) -> dict:
    """
    Extract field values from the detail page.
    The page uses <td> label / <td> value pairs in table rows.
    """
    soup = BeautifulSoup(html, "lxml")

    def fv(label: str) -> str:
        """Find first cell whose text == label, return next sibling cell text."""
        for td in soup.find_all("td"):
            if td.get_text(strip=True) == label:
                row = td.find_parent("tr")
                if not row:
                    continue
                cells = row.find_all("td")
                for i, cell in enumerate(cells):
                    if cell is td and i + 1 < len(cells):
                        return cells[i + 1].get_text(" ", strip=True)
        return ""

    return {
        "pk":           pk,
        "org_name":     fv("機關名稱"),
        "org_address":  fv("機關地址"),
        "contact":      fv("聯絡人"),
        "phone":        fv("聯絡電話"),
        "email":        fv("電子郵件信箱"),
        "case_no":      fv("標案案號"),
        "name":         fv("標案名稱"),
        "category":     fv("標的分類"),
        "budget":       fv("預算金額"),
        "notice_date":  fv("公告日"),
        "deadline":     fv("截止投標"),
        "open_date":    fv("開標時間"),
        "open_location": fv("開標地點"),
        "tender_method": fv("招標方式"),
        "location":     fv("履約地點"),
        "period":       fv("履約期限"),
        "notes":        fv("附加說明"),
        "detail_url":   f"{DETAIL_URL}?pkPmsMain={pk}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Date filter helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_recent(notice_date_str: str, days: int) -> bool:
    """
    Return True if notice_date (民國 format '115/03/06') is within last N days.
    If days == 0, always return True.
    """
    if days <= 0 or not notice_date_str:
        return True
    m = re.match(r"(\d+)/(\d+)/(\d+)", notice_date_str)
    if not m:
        return True
    year = int(m.group(1)) + 1911
    month = int(m.group(2))
    day   = int(m.group(3))
    try:
        tender_dt = datetime(year, month, day)
    except ValueError:
        return True
    return datetime.now() - tender_dt <= timedelta(days=days)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def scrape_keyword(keyword: str, fetch_detail: bool = True) -> List[dict]:
    """
    Search for keyword, collect all result pages, optionally fetch detail pages.
    Returns list of tender dicts ready for DB insertion.
    """
    from database import is_known

    session  = _make_session()
    all_tenders: List[dict] = []
    page_size = 100

    # ── Page 1 ──────────────────────────────────────────────────────────────
    logger.info(f"[{keyword}] Fetching page 1 ...")
    html   = _fetch_search_page(session, keyword, page=1, page_size=page_size)
    total  = _parse_total(html)
    results = _parse_results(html, keyword)
    all_tenders.extend(results)

    total_pages = min(math.ceil(total / page_size), settings.max_pages)
    logger.info(f"[{keyword}] Total: {total} results / {total_pages} page(s) to fetch")

    # ── Subsequent pages ─────────────────────────────────────────────────────
    for page in range(2, total_pages + 1):
        logger.info(f"[{keyword}] Fetching page {page} ...")
        try:
            html    = _fetch_search_page(session, keyword, page=page, page_size=page_size)
            results = _parse_results(html, keyword)
            if not results:
                logger.debug(f"[{keyword}] Page {page} returned no results, stopping")
                break
            all_tenders.extend(results)
        except Exception as e:
            logger.error(f"[{keyword}] Page {page} fetch error: {e}")
            break

    logger.info(f"[{keyword}] Collected {len(all_tenders)} tenders from search pages")

    # ── Date filter + dedup + detail fetch ───────────────────────────────────
    new_tenders: List[dict] = []
    for t in all_tenders:
        # Skip if already in DB
        if is_known(t["case_no"]):
            logger.debug(f"  SKIP (known): {t['case_no']}")
            continue

        # Skip if too old
        if not _is_recent(t["notice_date"], settings.notify_days):
            logger.debug(f"  SKIP (old):  {t['case_no']} {t['notice_date']}")
            continue

        # Fetch detail page for full info
        if fetch_detail:
            try:
                logger.info(f"  Detail: {t['case_no']} {t['name'][:30]}")
                detail_html = _fetch_detail(session, t["pk"])
                detail = _parse_detail(detail_html, t["pk"])
                # Merge: detail wins over list page for most fields
                t.update({k: v for k, v in detail.items() if v})
                t["keywords"] = t.get("keywords", keyword)  # preserve keyword
            except Exception as e:
                logger.error(f"  Detail fetch failed for {t['case_no']}: {e}")

        new_tenders.append(t)

    logger.info(f"[{keyword}] {len(new_tenders)} new tender(s) to save")
    return new_tenders
