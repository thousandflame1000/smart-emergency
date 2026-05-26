from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str

    # LINE Bot
    LINE_CHANNEL_SECRET: str
    LINE_CHANNEL_ACCESS_TOKEN: str

    # Gemini
    GEMINI_API_KEY: str

    # App
    APP_ENV: str = "development"

    @field_validator("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET",
                     "GEMINI_API_KEY", mode="before")
    @classmethod
    def strip_whitespace(cls, v):
        return v.strip() if isinstance(v, str) else v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
