from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Application
    APP_NAME: str = "Enterprise-AI-Agent"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_SECRET_KEY: str = "change-this-super-secret-key-in-production-min-32-chars"
    DEBUG: bool = Field(default=False, validation_alias="APP_DEBUG")
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database & Cache
    DATABASE_URL: str = "postgresql+asyncpg://postgres:odoo@localhost:5432/ai_agent_db"
    REDIS_URL: str = "redis://localhost:6379/0"

    # LLM Providers
    GEMINI_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    DEFAULT_LLM_PROVIDER: Literal["gemini", "openai"] = "gemini"
    DEFAULT_LLM_MODEL: str = "gemini-2.5-flash"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = "default-secret"

    # Odoo Cloud (External ERP API)
    ODOO_URL: str = "https://your-company.odoo.com"
    ODOO_DB: str = "odoo"
    ODOO_ADMIN_USERNAME: str = "bot_agent@company.com"
    ODOO_USERNAME: str | None = None
    ODOO_API_KEY: str = ""
    ODOO_WEBHOOK_SECRET: str = ""

    @property
    def odoo_user(self) -> str:
        return self.ODOO_USERNAME or self.ODOO_ADMIN_USERNAME


    # Google Integration
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/oauth/google/callback"

    # Security & Encryption
    TOKEN_ENCRYPTION_KEY: str = "W3u59Z0b-4j_1KqgW72Z46v1qG8k0e3HwKxP0aM9tN4="

    # Emergency Kill Switch
    EMERGENCY_KILL_SWITCH: bool = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
