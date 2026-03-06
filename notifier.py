"""
通知模組：Google Sheets 追加 + Email HTML 通知

Google Sheets：每筆新標案新增一列
Email：HTML 表格，列出所有新標案

認證優先順序（同 SPIDER 專案）：
  1. OAuth2 refresh token（.env GOOGLE_OAUTH_* 設定）
  2. 無 Google 設定 → 跳過
"""
from __future__ import annotations

import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

from loguru import logger

from config import settings

# Google Sheets column headers
SHEET_HEADERS = [
    "關鍵字", "標案名稱", "機關名稱", "預算金額",
    "公告日期", "截止投標", "開標時間", "開標地點",
    "標的分類", "招標方式", "履約地點", "履約期限",
    "聯絡人", "聯絡電話", "Email", "詳細連結",
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ─────────────────────────────────────────────────────────────────────────────
# Google Sheets
# ─────────────────────────────────────────────────────────────────────────────

def _get_credentials():
    """Build OAuth2 credentials from refresh token."""
    from google.oauth2.credentials import Credentials
    return Credentials(
        token=None,
        refresh_token=settings.google_oauth_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        scopes=SCOPES,
    )


def _ensure_headers(service, spreadsheet_id: str, sheet_name: str) -> None:
    """Add header row if the sheet is empty."""
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:A1",
    ).execute()
    if not result.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [SHEET_HEADERS]},
        ).execute()
        logger.debug("Sheet headers written")


def _tender_to_row(t: dict) -> list:
    return [
        t.get("keywords", ""),
        t.get("name", ""),
        t.get("org_name", ""),
        t.get("budget", ""),
        t.get("notice_date", ""),
        t.get("deadline", ""),
        t.get("open_date", ""),
        t.get("open_location", ""),
        t.get("category", ""),
        t.get("tender_method", ""),
        t.get("location", ""),
        t.get("period", ""),
        t.get("contact", ""),
        t.get("phone", ""),
        t.get("email", ""),
        t.get("detail_url", ""),
    ]


def append_to_sheet(tenders: List[dict]) -> bool:
    """Append new tenders to Google Sheet. Returns True on success."""
    if not settings.google_enabled:
        logger.debug("Google Sheets not configured, skipping")
        return False
    if not tenders:
        return True

    try:
        from googleapiclient.discovery import build
        creds   = _get_credentials()
        service = build("sheets", "v4", credentials=creds)
        sid     = settings.google_sheet_id
        sname   = settings.google_sheet_name

        _ensure_headers(service, sid, sname)

        rows = [_tender_to_row(t) for t in tenders]
        service.spreadsheets().values().append(
            spreadsheetId=sid,
            range=f"{sname}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
        logger.info(f"Google Sheets: appended {len(rows)} row(s)")
        return True
    except Exception as e:
        logger.error(f"Google Sheets append failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────────────────────────────────────

_EMAIL_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: Arial, "Microsoft JhengHei", sans-serif; font-size:14px; color:#333; }}
  h2   {{ color:#1a73e8; }}
  table{{ border-collapse:collapse; width:100%; margin-top:12px; }}
  th   {{ background:#1a73e8; color:#fff; padding:8px 10px; text-align:left; }}
  td   {{ border:1px solid #ddd; padding:7px 10px; vertical-align:top; }}
  tr:nth-child(even){{ background:#f5f8ff; }}
  a    {{ color:#1a73e8; }}
  .badge{{ background:#e8f0fe; border-radius:4px; padding:2px 6px; font-size:12px; }}
</style>
</head>
<body>
<h2>標案通知 – {count} 筆新標案</h2>
<p>搜尋關鍵字：{keywords}　｜　通知時間：{ts}</p>
<table>
<thead>
<tr>
  <th>標案名稱</th>
  <th>機關名稱</th>
  <th>預算金額</th>
  <th>公告日期</th>
  <th>截止投標</th>
  <th>關鍵字</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
<br>
<p style="font-size:12px;color:#999;">
  本信件由標案爬蟲系統自動寄出，資料來源：
  <a href="https://web.pcc.gov.tw">政府電子採購網</a>
</p>
</body>
</html>
"""

_ROW_TEMPLATE = """\
<tr>
  <td><a href="{url}">{name}</a></td>
  <td>{org_name}</td>
  <td>{budget}</td>
  <td>{notice_date}</td>
  <td>{deadline}</td>
  <td><span class="badge">{keywords}</span></td>
</tr>
"""


def send_email(tenders: List[dict]) -> bool:
    """Send HTML email with tender list. Returns True on success."""
    if not settings.email_enabled:
        logger.debug("Email not configured, skipping")
        return False
    if not tenders:
        return True

    rows_html = "".join(
        _ROW_TEMPLATE.format(
            url=t.get("detail_url", ""),
            name=t.get("name", ""),
            org_name=t.get("org_name", ""),
            budget=t.get("budget", ""),
            notice_date=t.get("notice_date", ""),
            deadline=t.get("deadline", ""),
            keywords=t.get("keywords", ""),
        )
        for t in tenders
    )

    html_body = _EMAIL_TEMPLATE.format(
        count=len(tenders),
        keywords=settings.keywords,
        ts=datetime.now().strftime("%Y-%m-%d %H:%M"),
        rows=rows_html,
    )

    subject = f"【標案通知】{len(tenders)} 筆新標案 – {datetime.now().strftime('%Y/%m/%d')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = settings.email_from or settings.smtp_user
    msg["To"]      = ", ".join(settings.email_recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.sendmail(
                settings.smtp_user,
                settings.email_recipients,
                msg.as_bytes(),
            )
        logger.info(f"Email sent to {settings.email_recipients}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Combined
# ─────────────────────────────────────────────────────────────────────────────

def notify(tenders: List[dict]) -> None:
    """Send all configured notifications for a list of new tenders."""
    if not tenders:
        logger.info("No new tenders to notify")
        return

    logger.info(f"Sending notifications for {len(tenders)} tender(s) ...")
    append_to_sheet(tenders)
    send_email(tenders)
