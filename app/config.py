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

    # 決賽展演期間的臨時共用密碼閘（見 app/demo_auth.py）。留空 = 不啟用，
    # 本地開發/測試預設不受影響。
    DEMO_PASSWORD: str = ""

    # 設成 true 時，正式環境只要有管理員綁了 LINE，後台就要求用 LINE 登入（傳「後台」取得連結）。
    # 預設關閉：後台不需要登入。要保護後台請設 true，或設 DEMO_PASSWORD。
    ADMIN_LINE_LOGIN: bool = False

    # 官方帳號的 Basic ID（例如 @571hpppb）。留空會用 LINE API 自動查；掃碼加入要靠它組出連結。
    LINE_BOT_BASIC_ID: str = ""

    # Rich Menu 重建會刪除、建立並上傳所有選單圖片。預設關閉，僅在受控維運時暫時開啟。
    RICH_MENU_REBUILD_ENABLED: bool = False

    # 機器人發給使用者的網頁表單連結要用的對外網址。
    PUBLIC_BASE_URL: str = "https://smart-emergency-production-d744.up.railway.app"

    @field_validator("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET",
                     "GEMINI_API_KEY", "TASK_COMMAND_SECRET", mode="before")
    @classmethod
    def strip_whitespace(cls, v):
        return v.strip() if isinstance(v, str) else v


settings = Settings()
