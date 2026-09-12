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

    # 決賽展演期間的臨時共用密碼閘（見 app/demo_auth.py）。留空 = 不啟用，
    # 本地開發/測試預設不受影響。
    DEMO_PASSWORD: str = ""

    @field_validator("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET",
                     "GEMINI_API_KEY", mode="before")
    @classmethod
    def strip_whitespace(cls, v):
        return v.strip() if isinstance(v, str) else v


settings = Settings()
