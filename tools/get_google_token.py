"""
取得 Google OAuth2 Refresh Token（一次性操作）

使用前置步驟：
  1. 前往 https://console.cloud.google.com/
  2. 建立或選擇一個專案
  3. 啟用 "Google Sheets API" 和 "Google Drive API"
  4. 建立「OAuth 2.0 用戶端 ID」（類型選「桌面應用程式」）
  5. 下載 JSON，或直接複製 client_id 和 client_secret 填入下方

執行：
  python tools/get_google_token.py

完成後，將輸出的三個值填入 .env：
  GOOGLE_OAUTH_CLIENT_ID=...
  GOOGLE_OAUTH_CLIENT_SECRET=...
  GOOGLE_OAUTH_REFRESH_TOKEN=...
"""
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def main():
    print("=" * 60)
    print("Google OAuth2 Refresh Token 取得工具")
    print("=" * 60)
    print()

    client_id = input("請輸入 Client ID: ").strip()
    if not client_id:
        print("Error: Client ID 不可為空")
        sys.exit(1)

    client_secret = input("請輸入 Client Secret: ").strip()
    if not client_secret:
        print("Error: Client Secret 不可為空")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    print()
    print("=" * 60)
    print("成功！請將以下三行加入 .env 檔案：")
    print("=" * 60)
    print(f"GOOGLE_OAUTH_CLIENT_ID={client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)


if __name__ == "__main__":
    main()
