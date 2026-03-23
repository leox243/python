"""
政府電子採購網爬蟲 – 雙模式架構

主要策略：
  - 生產模式（優先）：直連 PCC readPublish 取即時清單 → 關鍵字過濾 → openfun.app 補 detail
    優點：無延遲，當天公告當天取得；detail 若 openfun.app 尚未更新則用基本資訊
  - 生產模式（備用）：/api/listbydate 第三方 API（直連失敗時自動切換）
  - 測試模式：/api/searchbytitle 按關鍵字搜尋（main.py test 指令用）

readPublish HTML 結構（每筆 tenderCase table）：
  <table class="tenderCase">
    <tr><td><a href="UNIT-TYPE-NO.xml"><序號> 機關名稱：案號 - 標案名稱</a></td></tr>
    <tr><td class="summary">[採購金額級距]...</td></tr>
  </table>

踩坑筆記：
1. readPublish 回傳 UTF-8 HTML（非 JSON），每日全量，無分頁
2. tenderCase 連結 href = filename.xml，filename 格式：UNIT-TYPE-NO
3. openfun.app detail 有 3~7 天延遲；直連 PCC 取得的當日標案 detail 可能為空
4. detail 空時仍可通知（只用 readPublish 的基本資訊）
5. 截止投標 有時會是 URL → 驗證格式，排除 http 開頭
6. 預算金額 可能是「未達公告金額」→ 保留合法文字
"""
from __future__ import annotations

import re
import time
import random
from datetime import datetime, timedelta
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config import settings

# ─────────────────────────────────────────────────────────────────────────────
# 常數
# ─────────────────────────────────────────────────────────────────────────────

BASE_API         = "https://pcc-api.openfun.app"
LIST_DATE_URL    = f"{BASE_API}/api/listbydate"    # 備用
SEARCH_URL       = f"{BASE_API}/api/searchbytitle"
PCC_DETAIL_BASE  = "https://web.pcc.gov.tw/tps/QueryTender/query/searchTenderDetail"
PCC_DISPLAY_BASE = "https://web.pcc.gov.tw/tenderDisplay/querytenderDisplayAlt.do"  # 官方標案顯示頁

# 直連 PCC 官網（即時，無延遲）
PCC_READ_PUBLISH = "https://web.pcc.gov.tw/prkms/tender/common/noticeDate/readPublish"

# 第三方 API 請求標頭
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

# 直連 PCC 請求標頭（完整瀏覽器模擬）
PCC_DIRECT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://web.pcc.gov.tw/prkms/tender/common/noticeDate/readPublish",
}

# 公告類型白名單：只保留招標類型（適用 openfun.app 來源）
INCLUDE_TYPES = {
    "公開招標公告",
    "選擇性招標公告",
    "限制性招標公告",
    "經公開評選或公開徵求之限制性招標公告",
    "公開取得報價單或企劃書公告",
    "公開取得企劃書公告",
    "財物出售公告",
}


# ─────────────────────────────────────────────────────────────────────────────
# Session helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _make_pcc_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(PCC_DIRECT_HEADERS)
    return s


def _sleep(lo: float = 0.8, hi: float = 1.8) -> None:
    time.sleep(random.uniform(lo, hi))


def _get_json(session: requests.Session, url: str, params: dict = None,
              retries: int = 4, backoff: float = 8.0) -> dict:
    wait = backoff
    for attempt in range(retries):
        try:
            r = session.get(url, params=params or {}, timeout=20)
            if r.status_code == 429:
                logger.warning(f"  429 rate limit – waiting {wait:.0f}s (attempt {attempt+1}/{retries})")
                time.sleep(wait)
                wait *= 2
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if attempt < retries - 1:
                logger.warning(f"  HTTP error {e} – retry in {wait:.0f}s")
                time.sleep(wait)
                wait *= 2
            else:
                raise
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f"  Request error {e} – retry in {wait:.0f}s")
                time.sleep(wait)
                wait *= 2
            else:
                raise
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# 日期處理
# ─────────────────────────────────────────────────────────────────────────────

def _yyyymmdd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def _parse_yyyymmdd(date_int: int) -> Optional[datetime]:
    try:
        s = str(date_int)
        return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except Exception:
        return None


def _to_minguo(dt: datetime) -> str:
    return f"{dt.year - 1911}/{dt.month:02d}/{dt.day:02d}"


def _to_roc_datestr(dt: datetime) -> str:
    """Convert datetime → PCC readPublish 民國日期格式 115年03月17日"""
    return f"{dt.year - 1911}年{dt.month:02d}月{dt.day:02d}日"


# ─────────────────────────────────────────────────────────────────────────────
# Detail API（openfun.app）
# ─────────────────────────────────────────────────────────────────────────────

def _get_detail(session: requests.Session, tender_api_url: str) -> dict:
    if not tender_api_url:
        return {}
    try:
        _sleep()
        data = _get_json(session, tender_api_url)
    except Exception as e:
        logger.warning(f"  Detail API error: {e}")
        return {}

    records = data.get("records", [])
    for rec in records:
        d = rec.get("detail") or {}
        if d.get("採購資料:標案名稱"):
            return d
    for rec in records:
        d = rec.get("detail") or {}
        if d:
            return d
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# 欄位清理
# ─────────────────────────────────────────────────────────────────────────────

def _clean_budget(val: str) -> str:
    if not val:
        return ""
    val = val.strip()
    return "" if val.startswith("http") else val


def _clean_date(val: str) -> str:
    if not val:
        return ""
    val = val.strip()
    if val.startswith("http"):
        return ""
    return val if re.match(r"\d+/\d+/\d+", val) else ""


def _build_tender(rec: dict, detail: dict, keyword: str) -> dict:
    """
    組合搜尋結果（rec）與詳細 API（detail）→ 標案 dict。
    detail 為空時（openfun.app 尚未更新），使用 rec 基本資訊。
    """
    brief    = rec.get("brief", {})
    filename = rec.get("filename", "")

    pk = detail.get("pkPmsMain", "")
    if not pk:
        m = re.search(r"pkPmsMain=([A-Za-z0-9+/=]+)", detail.get("url", ""))
        pk = m.group(1) if m else filename

    org_name = (
        detail.get("機關資料:機關名稱")
        or rec.get("unit_name")
        or ""
    )

    notice_date = _clean_date(
        detail.get("招標資料:公告日", "")
        or detail.get("公告資料:公告日", "")
        or rec.get("_basic_notice_date", "")
    )
    # rec.date 是 readPublish 的抓取日期，非公告日
    # 只有 detail 有資料時才當 fallback（detail_pending 時留空，避免填入錯誤日期）
    if not notice_date and detail:
        d = rec.get("date", 0)
        if d:
            dt = _parse_yyyymmdd(d)
            if dt:
                notice_date = _to_minguo(dt)

    deadline  = _clean_date(
        detail.get("領投開標:截止投標", "")
        or detail.get("截止投標", "")
        or rec.get("_basic_deadline", "")
    )
    open_date = _clean_date(detail.get("領投開標:開標時間", ""))

    # detail_url 優先順序：
    # 1. openfun.app detail 的官方 PCC URL（pkPmsMain 可用）
    # 2. PCC indexTenderBasic 查到的官方 URL（pkPmsMain 可用）
    # 3. querytenderDisplayAlt fallback（filename 格式）
    pcc_pk = detail.get("pkPmsMain", "")
    if not pcc_pk:
        m = re.search(r"pkPmsMain=([A-Za-z0-9+/=]+)", detail.get("url", ""))
        if m:
            pcc_pk = m.group(1)
    if not pcc_pk:
        pcc_pk = rec.get("_basic_pk", "")

    if pcc_pk:
        detail_url = f"{PCC_DETAIL_BASE}?pkPmsMain={pcc_pk}"
    else:
        detail_url = (
            rec.get("_pcc_detail_url", "")
            or (f"{PCC_DISPLAY_BASE}?category=TC&fnCategoryDate={filename}" if filename else "")
        )

    # 預算：detail 有就用；PCC basic 次之；readPublish 金額級距最後
    budget = _clean_budget(detail.get("採購資料:預算金額", ""))
    if not budget:
        budget = rec.get("_basic_budget", "") or rec.get("_budget_range", "")

    notes_raw = detail.get("其他:附加說明", "") or ""
    notes = notes_raw[:200]

    return {
        "pk":            pk,
        "case_no":       detail.get("採購資料:標案案號", "") or rec.get("job_number", ""),
        "name":          detail.get("採購資料:標案名稱", "") or brief.get("title", ""),
        "org_name":      org_name,
        "org_address":   detail.get("機關資料:機關地址", ""),
        "contact":       detail.get("機關資料:聯絡人", ""),
        "phone":         detail.get("機關資料:聯絡電話", ""),
        "email":         detail.get("機關資料:電子郵件信箱", ""),
        "category":      detail.get("採購資料:標的分類", ""),
        "budget":        budget,
        "notice_date":   notice_date,
        "deadline":      deadline,
        "open_date":     open_date,
        "open_location": detail.get("領投開標:開標地點", ""),
        "tender_method": detail.get("招標資料:招標方式", ""),
        "location":      detail.get("其他:履約地點", ""),
        "period":        detail.get("其他:履約期限", ""),
        "notes":         notes,
        "detail_url":    detail_url,
        "keywords":      keyword,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 直連 PCC：readPublish（即時，主要使用）
# ─────────────────────────────────────────────────────────────────────────────

def _parse_budget_range(summary_text: str) -> str:
    """從 summary 提取採購金額級距。"""
    m = re.search(r"\[採購金額級距\]([^\[]+)", summary_text)
    return m.group(1).strip() if m else ""


def _fetch_pcc_direct(pcc_session: requests.Session, dt: datetime) -> Optional[list]:
    """
    直連 PCC readPublish 取指定日期的全量即時標案清單。
    成功回傳 records list；失敗回傳 None（呼叫端會 fallback 到 openfun.app）。

    readPublish HTML 結構：
      每筆 <table class="tenderCase">
        <tr><td><a href="FILENAME.xml"><n> 機關名稱：案號 - 標案名稱</a></td></tr>
        <tr><td class="summary">[採購金額級距]...</td></tr>
    """
    roc_str  = _to_roc_datestr(dt)
    date_int = int(_yyyymmdd(dt))

    try:
        _sleep(1.0, 2.0)
        r = pcc_session.get(PCC_READ_PUBLISH, params={"dateStr": roc_str}, timeout=30)
        r.raise_for_status()

        html = r.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")

        cases = soup.find_all("table", class_="tenderCase")
        if not cases:
            logger.debug(f"readPublish {roc_str}: 無 tenderCase（可能是假日或無資料）")
            return None

        records = []
        for case in cases:
            link = case.find("a", href=re.compile(r"\.xml$"))
            if not link:
                continue

            filename   = link.get("href", "").replace(".xml", "")
            title_full = link.get_text(strip=True)

            # Parse: "<n> 機關名稱：案號 - 標案名稱"
            title_full = re.sub(r"^<\d+>\s*", "", title_full)  # 去序號
            parts = title_full.split(" - ", 1)
            tender_name = parts[-1].strip()
            org_name, case_no = "", ""
            if len(parts) > 1:
                prefix = parts[0]
                op = prefix.split("：", 1)
                org_name = op[0].strip()
                case_no  = op[1].strip() if len(op) > 1 else ""

            # 採購金額級距（from summary td）
            summary_td = case.find("td", class_="summary")
            budget_range = _parse_budget_range(summary_td.get_text() if summary_td else "")

            # 構建 tender_api_url（for detail fetch）
            dash = filename.find("-")
            tender_api_url = ""
            if dash > 0:
                unit_id = filename[:dash]
                job_no  = filename[dash + 1:]
                tender_api_url = f"{BASE_API}/api/tender?unit_id={unit_id}&job_number={job_no}"

            # PCC 官方標案顯示連結（detail_pending 時使用）
            pcc_detail_url = f"{PCC_DISPLAY_BASE}?category=TC&fnCategoryDate={filename}"

            records.append({
                "date":            date_int,
                "filename":        filename,
                "brief":           {"type": "direct_pcc", "title": tender_name},
                "unit_name":       org_name,
                "job_number":      case_no,
                "tender_api_url":  tender_api_url,
                "_budget_range":   budget_range,
                "_pcc_detail_url": pcc_detail_url,
            })

        return records

    except Exception as e:
        logger.warning(f"直連 PCC {roc_str} 失敗: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PCC indexTenderBasic 搜尋（直接取公告日期/截止投標/預算/pkPmsMain）
# ─────────────────────────────────────────────────────────────────────────────

PCC_BASIC_URL   = "https://web.pcc.gov.tw/prkms/tender/common/basic/indexTenderBasic"
PCC_BASIC_QUERY = "https://web.pcc.gov.tw/prkms/tender/common/basic/readTenderBasic"

# 嘗試順序：招標公告 → 公開徵求 → 政府採購預告
_BASIC_TENDER_TYPES = ["TENDER_DECLARATION", "SEARCH_APPEAL", "PREDICT"]


def _init_basic_session(pcc_session: requests.Session) -> None:
    """模擬使用者開啟 PCC 查詢頁面，取得 Session cookie。"""
    try:
        # 先造訪首頁，再到查詢頁，模擬正常瀏覽流程
        pcc_session.headers.update({"Referer": "https://web.pcc.gov.tw/"})
        pcc_session.get("https://web.pcc.gov.tw/", timeout=10)
        _sleep(1.0, 2.5)
        pcc_session.headers.update({"Referer": "https://web.pcc.gov.tw/"})
        pcc_session.get(PCC_BASIC_URL, timeout=10)
        _sleep(0.5, 1.5)
    except Exception:
        pass


def _parse_basic_row(row) -> Optional[dict]:
    """解析 tb_01 表格的一列，回傳 dict 或 None。"""
    cells = row.find_all("td")
    if len(cells) < 9:
        return None
    # cells[2]：標案案號 + 標案名稱（名稱藏在 JS 字串中）
    name_cell = cells[2]
    case_no_text = name_cell.get_text(separator="\n").split("\n")[0].strip()
    name_match = re.search(r'pageCode2Img\("([^"]+)"\)', str(name_cell))
    tender_name = name_match.group(1) if name_match else ""
    # pkPmsMain 在 href ?pk=VALUE
    pk_match = re.search(r'\?pk=([A-Za-z0-9+/=]+)', str(name_cell))
    pk = pk_match.group(1) if pk_match else ""
    return {
        "org_name":    cells[1].get_text(strip=True),
        "case_no":     case_no_text,
        "name":        tender_name,
        "pk":          pk,
        "notice_date": cells[6].get_text(strip=True),
        "deadline":    cells[7].get_text(strip=True),
        "budget":      cells[8].get_text(strip=True),
        "detail_url":  f"{PCC_DETAIL_BASE}?pkPmsMain={pk}" if pk else "",
    }


def _fetch_basic_detail(
    pcc_session: requests.Session,
    tender_name: str,
    case_no: str = "",
    org_name: str = "",
) -> Optional[dict]:
    """
    用標案名稱到 PCC indexTenderBasic 查詢，回傳含公告日期/截止/預算/pkPmsMain 的 dict。

    搜尋策略：
    - dateType=isNow（當日公告），配合每日執行；
    - 依序嘗試 招標公告 → 公開徵求 → 政府採購預告。
    - 比對優先順序：案號完全符合 > 標案名稱包含關係。
    - 搜尋間隔 1~2 秒，避免被限流。
    """
    # 名稱正規化：去掉多餘空格（例如 "115 年" → "115年"）
    search_name = re.sub(r"\s+", "", tender_name).strip() if tender_name else ""

    for tender_type in _BASIC_TENDER_TYPES:
        # isNow（當日）→ isSpdt（等標期內，捕捉昨天以前的更正公告）
        for date_type in ("isNow", "isSpdt"):
            try:
                # 模擬人類：每次查詢前有自然停頓
                _sleep(2.0, 4.0)
                pcc_session.headers.update({
                    "Referer": PCC_BASIC_URL,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://web.pcc.gov.tw",
                })
                r = pcc_session.post(
                    PCC_BASIC_QUERY,
                    data={
                        "pageSize": "20",
                        "firstSearch": "false",
                        "searchType": "basic",
                        "isBinding": "N",
                        "isLogIn": "N",
                        "tenderName": search_name,
                        "tenderType": tender_type,
                        "dateType": date_type,
                    },
                    timeout=15,
                )
            except Exception as e:
                logger.debug(f"_fetch_basic_detail POST error ({tender_type}/{date_type}): {e}")
                continue

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            tbl  = soup.find("table", class_="tb_01")
            if not tbl:
                continue

            candidates = []
            for row in tbl.find_all("tr")[1:]:
                parsed = _parse_basic_row(row)
                if not parsed:
                    continue
                if parsed["name"] == "" and parsed["case_no"] == "":
                    continue
                # 比對：案號完全符合（最優先）
                if case_no and parsed["case_no"] == case_no:
                    logger.debug(f"    PCC basic match (case_no/{date_type}): {parsed['name']}")
                    return parsed
                # 比對：標案名稱（去空格後）相互包含
                norm_parsed = re.sub(r"\s+", "", parsed["name"])
                if search_name and (search_name in norm_parsed or norm_parsed in search_name):
                    candidates.append(parsed)

            if candidates:
                best = max(candidates, key=lambda x: len(x["name"]))
                logger.debug(f"    PCC basic match (name/{date_type}): {best['name']}")
                return best

    return None


# ─────────────────────────────────────────────────────────────────────────────
# 生產模式：雙模式 scrape（直連 PCC 優先，openfun.app 備用）
# ─────────────────────────────────────────────────────────────────────────────

def scrape_all_keywords(
    keywords: Optional[List[str]] = None,
    notify_days: Optional[int] = None,
    project_id: int = 1,
    fetch_detail: bool = True,
) -> List[dict]:
    """
    按日期取最近 notify_days 天的完整清單，再用所有關鍵字過濾。

    資料來源優先順序：
    1. 直連 PCC readPublish（即時，當天公告當天取得）
    2. openfun.app listbydate（備用，有 3~7 天延遲）

    直連 PCC 的 detail 資料：
    - 若 openfun.app 已處理 → 取完整 detail（預算、截止日、聯絡資訊等）
    - 若 openfun.app 尚未更新 → 只用 readPublish 基本資訊（仍會發通知）
    """
    from database import is_pk_known

    if keywords is None:
        keywords = settings.keyword_list
    if notify_days is None:
        notify_days = settings.notify_days

    session     = _make_session()      # openfun.app
    pcc_session = _make_pcc_session()  # 直連 PCC
    _init_basic_session(pcc_session)   # 取得 indexTenderBasic session cookie
    today       = datetime.now()
    new_tenders: List[dict] = []
    seen_pks: set = set()

    for days_ago in range(notify_days):
        dt       = today - timedelta(days=days_ago)
        date_str = _yyyymmdd(dt)

        logger.info(f"Fetching date {date_str} ...")

        # ── 1. 優先直連 PCC readPublish ──────────────────────────────────
        records = _fetch_pcc_direct(pcc_session, dt)
        if records is not None:
            source = "直連PCC"
        else:
            # ── 2. 備用：openfun.app listbydate ──────────────────────────
            logger.info(f"  直連失敗或無資料，改用 openfun.app ...")
            try:
                _sleep(1.5, 3.0)
                data    = _get_json(session, LIST_DATE_URL, params={"date": date_str})
                records = data.get("records", [])
                source  = "openfun.app"
            except Exception as e:
                logger.error(f"listbydate {date_str} error: {e}")
                continue

        logger.info(f"  [{source}] {len(records)} records on {date_str}")

        for rec in records:
            brief      = rec.get("brief", {})
            brief_type = brief.get("type", "")
            title      = brief.get("title", "") or ""
            filename   = rec.get("filename", "")
            org_name   = rec.get("unit_name", "") or ""

            # ── 公告類型白名單（只對 openfun.app 來源套用）─────────────
            if brief_type != "direct_pcc":
                if brief_type not in INCLUDE_TYPES:
                    continue

            # ── 關鍵字過濾（標案名稱 + 機關名稱）──────────────────────
            search_text = title + " " + org_name
            matched_kw  = next((kw for kw in keywords if kw in search_text), None)
            if not matched_kw:
                continue

            # ── 快速預判已知（用 filename）────────────────────────────
            if filename and (filename in seen_pks or is_pk_known(filename, project_id)):
                continue

            # ── 抓取詳細資料（三層 fallback）──────────────────────────
            # 1. openfun.app detail（最完整）
            # 2. PCC indexTenderBasic（公告日/截止/預算/pkPmsMain，即時）
            # 3. 僅用 readPublish 基本資訊（detail_pending=True）
            detail = {}
            detail_pending = False
            logger.info(f"  [{matched_kw}] {org_name[:20]} – {title[:40]}")

            if fetch_detail and rec.get("tender_api_url"):
                detail = _get_detail(session, rec["tender_api_url"])

            if not detail:
                # ── 2. 直接查 PCC indexTenderBasic ────────────────────
                case_no  = rec.get("job_number", "")
                basic    = _fetch_basic_detail(pcc_session, title, case_no, org_name)
                if basic:
                    logger.info(f"    (PCC basic 補齊：{basic['notice_date']} / {basic['deadline']} / {basic['budget']})")
                    # 將 basic 結果注入 rec，讓 _build_tender 可以使用
                    rec["_basic_notice_date"] = basic["notice_date"]
                    rec["_basic_deadline"]    = basic["deadline"]
                    rec["_basic_budget"]      = basic["budget"]
                    rec["_basic_pk"]          = basic["pk"]
                    rec["_basic_detail_url"]  = basic["detail_url"]
                else:
                    logger.info(f"    (PCC basic 查無資料，使用基本資訊)")
                    detail_pending = True

            # ── 組合 + 最終去重 ─────────────────────────────────────────
            t = _build_tender(rec, detail, matched_kw)
            if not t["pk"]:
                t["pk"] = filename
            t["detail_pending"] = detail_pending

            if t["pk"] in seen_pks or is_pk_known(t["pk"], project_id):
                continue

            seen_pks.add(t["pk"])
            new_tenders.append(t)

    logger.info(f"Total: {len(new_tenders)} new tender(s) across all keywords (project {project_id})")
    return new_tenders


# ─────────────────────────────────────────────────────────────────────────────
# 測試模式：searchbytitle（main.py test 指令用）
# ─────────────────────────────────────────────────────────────────────────────

def scrape_keyword(keyword: str, fetch_detail: bool = True) -> List[dict]:
    """
    以單一關鍵字搜尋（相關性排序）。
    僅供 main.py test 指令使用；生產請用 scrape_all_keywords()。
    """
    from database import is_pk_known

    session     = _make_session()
    new_tenders: List[dict] = []
    seen_pks:   set = set()

    for page in range(1, settings.max_pages + 1):
        if page > 1:
            _sleep()
        logger.info(f"[{keyword}] Fetching page {page} ...")
        try:
            data = _get_json(session, SEARCH_URL, params={"query": keyword, "page": page})
        except Exception as e:
            logger.error(f"[{keyword}] Page {page} error: {e}")
            break

        records     = data.get("records", [])
        total_pages = data.get("total_pages", 1)
        if page == 1:
            logger.info(f"[{keyword}] Total: {data.get('total_records',0)} / {total_pages} pages")

        if not records:
            break

        for rec in records:
            brief      = rec.get("brief", {})
            brief_type = brief.get("type", "")
            date_int   = rec.get("date", 0)
            filename   = rec.get("filename", "")

            if brief_type not in INCLUDE_TYPES:
                continue

            if date_int:
                dt = _parse_yyyymmdd(date_int)
                if dt and settings.notify_days > 0:
                    if datetime.now() - dt > timedelta(days=settings.notify_days):
                        continue

            if filename and (filename in seen_pks or is_pk_known(filename)):
                continue

            detail = {}
            if fetch_detail:
                logger.info(f"  Detail: [{rec.get('job_number','')}] {brief.get('title','')[:40]}")
                detail = _get_detail(session, rec.get("tender_api_url", ""))

            t = _build_tender(rec, detail, keyword)
            if not t["pk"]:
                t["pk"] = filename

            if t["pk"] in seen_pks or is_pk_known(t["pk"]):
                continue

            seen_pks.add(t["pk"])
            new_tenders.append(t)

        if page >= total_pages:
            break

    logger.info(f"[{keyword}] {len(new_tenders)} new tender(s)")
    return new_tenders


# ─────────────────────────────────────────────────────────────────────────────
# 補完整資料（供 Web UI 手動觸發）
# ─────────────────────────────────────────────────────────────────────────────

def fetch_tender_detail(pk: str) -> Optional[dict]:
    """
    依 pk（filename）從 openfun.app 補抓完整 detail。
    成功回傳 detail dict；失敗或無資料回傳 None。
    """
    # pk 格式：UNIT-TYPE-NO，例如 TIQ-1-70972364
    # unit_id = 第一個 "-" 之前；job_number = 其餘
    dash = pk.find("-")
    if dash <= 0:
        return None

    unit_id = pk[:dash]
    job_no  = pk[dash + 1:]
    api_url = f"{BASE_API}/api/tender?unit_id={unit_id}&job_number={job_no}"

    session = _make_session()
    try:
        detail = _get_detail(session, api_url)
        if not detail:
            return None

        # 整理成 update_tender_detail 可用的格式
        notice_date = _clean_date(
            detail.get("招標資料:公告日", "")
            or detail.get("公告資料:公告日", "")
        )
        deadline  = _clean_date(detail.get("領投開標:截止投標", "") or detail.get("截止投標", ""))
        open_date = _clean_date(detail.get("領投開標:開標時間", ""))
        budget    = _clean_budget(detail.get("採購資料:預算金額", ""))
        pcc_pk = detail.get("pkPmsMain", "")
        if not pcc_pk:
            m = re.search(r"pkPmsMain=([A-Za-z0-9+/=]+)", detail.get("url", ""))
            if m:
                pcc_pk = m.group(1)
        detail_url = (
            f"{PCC_DETAIL_BASE}?pkPmsMain={pcc_pk}" if pcc_pk
            else f"{PCC_PUBLIC_BASE}/{pk}"
        )
        notes_raw = detail.get("其他:附加說明", "") or ""

        return {
            "case_no":       detail.get("採購資料:標案案號", ""),
            "name":          detail.get("採購資料:標案名稱", ""),
            "org_name":      detail.get("機關資料:機關名稱", ""),
            "org_address":   detail.get("機關資料:機關地址", ""),
            "contact":       detail.get("機關資料:聯絡人", ""),
            "phone":         detail.get("機關資料:聯絡電話", ""),
            "email":         detail.get("機關資料:電子郵件信箱", ""),
            "category":      detail.get("採購資料:標的分類", ""),
            "budget":        budget,
            "notice_date":   notice_date,
            "deadline":      deadline,
            "open_date":     open_date,
            "open_location": detail.get("領投開標:開標地點", ""),
            "tender_method": detail.get("招標資料:招標方式", ""),
            "location":      detail.get("其他:履約地點", ""),
            "period":        detail.get("其他:履約期限", ""),
            "notes":         notes_raw[:200],
            "detail_url":    detail_url,
        }
    except Exception as e:
        logger.warning(f"fetch_tender_detail({pk}) error: {e}")
        return None
