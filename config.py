from __future__ import annotations

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── 搜尋設定 ──────────────────────────────────────────────────────────────
    keywords: str = "資訊系統"
    tender_status_type: str = "招標"
    max_pages: int = 3
    notify_days: int = 3

    # ── 排程 ──────────────────────────────────────────────────────────────────
    schedule_hour: int = 9
    schedule_minute: int = 0
    schedule_interval: int = 1   # 每幾天執行一次（1 = 每天）

    # ── 資料庫 ────────────────────────────────────────────────────────────────
    db_path: str = "tenders.db"

    # ── Google OAuth2 ─────────────────────────────────────────────────────────
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_refresh_token: str = ""

    # Google Sheets
    google_sheet_id: str = ""
    google_sheet_name: str = "標案通知"

    # ── Email ─────────────────────────────────────────────────────────────────
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    # ── 計算屬性 ──────────────────────────────────────────────────────────────
    @property
    def keyword_list(self) -> List[str]:
        return [k.strip() for k in self.keywords.split(",") if k.strip()]

    @property
    def email_recipients(self) -> List[str]:
        return [e.strip() for e in self.email_to.split(",") if e.strip()]

    @property
    def google_enabled(self) -> bool:
        return bool(
            self.google_oauth_client_id
            and self.google_oauth_client_secret
            and self.google_oauth_refresh_token
            and self.google_sheet_id
        )

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.email_to)


settings = Settings()
