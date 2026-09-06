from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    TOKEN: SecretStr
    ADMIN_IDS: list[int]
    TORRENT_DIR: str = "/mnt/foundation/torrents/incoming"
    UPTIME_DB: Path = Path("data/uptime.sqlite3")
    UPTIME_TIMEZONE: str = "Asia/Tbilisi"

    @field_validator("UPTIME_TIMEZONE")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )


settings = Settings()
