from pydantic_settings import BaseSettings
from pydantic import ConfigDict, field_validator


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    DATABASE_URL: str

    # LINE Bot
    LINE_CHANNEL_SECRET: str
    LINE_CHANNEL_ACCESS_TOKEN: str

    # Gemini
    GEMINI_API_KEY: str

    # App
    APP_ENV: str = "development"
    TASK_WORKFLOW_V2: bool = False
    TASK_COMMAND_SECRET: str = ""
    OUTBOX_MAX_ATTEMPTS: int = 5
    OUTBOX_BASE_DELAY_SECONDS: int = 15
    OUTBOX_MAX_DELAY_SECONDS: int = 3600
    OUTBOX_LEASE_SECONDS: int = 120
    WEBHOOK_MAX_ATTEMPTS: int = 8
    WEBHOOK_BASE_DELAY_SECONDS: int = 10
    WEBHOOK_MAX_DELAY_SECONDS: int = 900
    WEBHOOK_LEASE_SECONDS: int = 120
    WEBHOOK_RETENTION_DAYS: int = 30
    OUTBOX_RETENTION_DAYS: int = 90

    # External AI is opt-in because prompts and knowledge-base text leave this service.
    EXTERNAL_AI_ENABLED: bool = False

    # Override these with the deploying organization's legal details.
    PRIVACY_NOTICE_VERSION: str = "2026-09-27"
    PRIVACY_CONTROLLER_NAME: str = "鄰里守望平台營運單位"
    PRIVACY_CONTACT: str = "請透過 LINE 官方帳號聯絡管理員"

    # Comma-separated browser origins allowed to call the API cross-origin.
    CORS_ALLOWED_ORIGINS: str = ""

    # 決賽展演期間的臨時共用密碼閘（見 app/demo_auth.py）。留空 = 不啟用，
    # 本地開發/測試預設不受影響。
    DEMO_PASSWORD: str = ""

    # 設成 true 時，正式環境只要有管理員綁了 LINE，後台就要求用 LINE 登入（傳「後台」取得連結）。
    # 預設關閉：後台不需要登入。要保護後台請設 true，或設 DEMO_PASSWORD。
    ADMIN_LINE_LOGIN: bool = False

    # 官方帳號的 Basic ID（例如 @571hpppb）。留空會用 LINE API 自動查；掃碼加入要靠它組出連結。
    LINE_BOT_BASIC_ID: str = ""

    # Rich Menu 重建會建立新選單、切換完成後才刪舊的。只有管理員能按（後台「系統狀態」），
    # 以前還要到 Railway 暫時打開這個開關，太繞；需要鎖住時設 false。
    RICH_MENU_REBUILD_ENABLED: bool = True

    # LINE 裡全螢幕開啟的 App（LIFF）。ID 不是機密；前段數字就是 LINE Login channel ID，
    # 伺服器用它向 LINE 驗證 ID token。清空就回到只有聊天室按鈕的模式。
    LIFF_ID: str = "2011793999-3QbSnXoe"

    @property
    def LINE_LOGIN_CHANNEL_ID(self) -> str:
        return self.LIFF_ID.split("-", 1)[0] if self.LIFF_ID else ""

    # 機器人發給使用者的網頁表單連結要用的對外網址。
    PUBLIC_BASE_URL: str = "https://smart-emergency-production-d744.up.railway.app"

    @field_validator("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET",
                     "GEMINI_API_KEY", "TASK_COMMAND_SECRET", mode="before")
    @classmethod
    def strip_whitespace(cls, v):
        return v.strip() if isinstance(v, str) else v


settings = Settings()
