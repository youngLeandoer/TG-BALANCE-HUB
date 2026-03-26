from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")
    # Bot
    BOT_TOKEN: str
    BOT_ADMINS: str
    
    # Database
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str
    DB_HOST: str = "postgres"
    DB_PORT: int = 5432
    
    # Redis
    REDIS_URL: str = "redis://redis:6379/0"
    
    # Security
    ENCRYPTION_KEY: str
    
    # Web
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8000
    WEB_DOMAIN: str
    
    # Celery
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE_PATH: str = "/app/logs/bot.log"
    APP_TIMEZONE: str = "Europe/Moscow"

    # Mango Office scraping
    MANGO_DASHBOARD_URL: str = "https://lk.mango-office.ru/"
    MANGO_AUTH_URL: str = "https://lk.mango-office.ru/login"
    MANGO_OTP_IMAP_HOST: str = ""
    MANGO_OTP_IMAP_PORT: int = 993
    MANGO_OTP_IMAP_USER: str = ""
    MANGO_OTP_IMAP_PASSWORD: str = ""
    MANGO_OTP_EMAIL_FROM: str = "order@dokatka.ru"
    INTERNAL_UPDATE_TOKEN: str = ""

    # Alerts & daily group report
    LOW_BALANCE_THRESHOLD_RUB: float = 1000.0
    DAILY_STATS_ENABLED: bool = False
    DAILY_STATS_CHAT_ID: int = 0
    DAILY_STATS_HOUR_UTC: int = 7
    DAILY_STATS_MINUTE_UTC: int = 0
    # If > 0, overrides daily schedule and sends report every N minutes (useful for testing).
    DAILY_STATS_INTERVAL_MINUTES: int = 0
    
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )
    
    @property
    def admin_ids(self) -> List[int]:
        return [int(x.strip()) for x in self.BOT_ADMINS.split(",") if x.strip()]
    
@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
