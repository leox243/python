# 標案爬蟲系統

政府電子採購網（web.pcc.gov.tw）全文檢索爬蟲
依關鍵字定期搜尋新標案 → 儲存至 SQLite → 通知至 Google Sheets / Email

## 架構

```
政府電子採購網
  ↓  GET /readBulletion?querySentence=<關鍵字>  （純 HTTP，無需 JS 渲染）
scraper.py  ──→  database.py (SQLite 去重)
                      ↓ 有新標案
               notifier.py  ──→  Google Sheets（新增列）
                            ──→  Email（HTML 表格）
scheduler.py  每日定時觸發（APScheduler）
```

## 快速開始

### 1. 安裝

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### 2. 設定

```bash
copy .env.example .env
# 用編輯器開啟 .env，填入關鍵字、Email 或 Google Sheets 設定
```

### 3. 測試爬蟲（不寫 DB、不發通知）

```bash
python main.py test
```

### 4. 立即執行一次

```bash
python main.py run
```

### 5. 啟動每日排程

```bash
python main.py schedule
```

### 6. 查看已抓到的標案

```bash
python main.py list
```

---

## .env 主要設定

| 項目 | 說明 | 範例 |
|------|------|------|
| `KEYWORDS` | 搜尋關鍵字（逗號分隔） | `資訊系統,軟體開發` |
| `TENDER_STATUS_TYPE` | 標案種類 | `招標` |
| `NOTIFY_DAYS` | 僅通知幾天內的標案（0=全部） | `3` |
| `MAX_PAGES` | 每關鍵字最多抓幾頁（100筆/頁） | `3` |
| `SCHEDULE_HOUR` | 每天幾點執行 | `9` |
| `GOOGLE_SHEET_ID` | Google 試算表 ID | `1BxiMVs0X...` |
| `EMAIL_TO` | 收件人（逗號分隔） | `you@example.com` |

---

## Google Sheets 設定

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)，建立 OAuth 2.0 Client ID（桌面應用程式）
2. 執行 `python tools/get_google_token.py`，依指示授權
3. 將輸出的三個值填入 `.env`
4. 建立一個 Google 試算表，將 ID 填入 `.env GOOGLE_SHEET_ID`

---

## Email 設定（Gmail）

1. Gmail 開啟「兩步驗證」
2. 前往「應用程式密碼」產生一組密碼
3. 填入 `.env` 的 `SMTP_PASSWORD`

---

## 技術說明

- 搜尋為純 GET 請求，**無需 Playwright/Selenium**
- 分頁參數：`d-3611040-p=<頁碼>`
- 詳細頁面：`/tps/QueryTender/query/searchTenderDetail?pkPmsMain=<base64>`
- 請求間隔：2–5 秒隨機延遲，避免觸發限速
- 去重依據：`case_no`（標案案號）存入 SQLite
