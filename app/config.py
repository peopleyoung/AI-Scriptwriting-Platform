from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Scriptwriting Platform"
    environment: str = "development"
    database_url: str = "sqlite:///./app.db"
    session_cookie_name: str = "scriptwriting_session"
    session_ttl_seconds: int = 60 * 60 * 8
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_secret: str | None = None
    auto_create_schema: bool = True
    csrf_cookie_name: str = "scriptwriting_csrf"
    login_max_attempts: int = 5
    login_window_seconds: int = 60
    external_encryption_key: str = "development-only-key"
    download_ttl_seconds: int = 900
    backup_dir: str = "./backups"
    trash_retention_days: int = 30
    max_running_generation_jobs: int = 1
    max_running_generation_jobs_per_user: int = 1
    max_running_generation_jobs_per_provider: int = 1
    model_timeout_seconds: float = 120
    creative_context_max_characters: int = 60000
    frontend_dist: str | None = None

    @model_validator(mode="after")
    def require_production_encryption_key(self):
        if self.environment == "production" and (len(self.external_encryption_key) < 32 or self.external_encryption_key == "development-only-key" or self.external_encryption_key.startswith("change-this")):
            raise ValueError("production requires APP_EXTERNAL_ENCRYPTION_KEY with at least 32 characters")
        return self

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

