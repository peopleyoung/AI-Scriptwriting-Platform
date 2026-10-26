from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Placeholders that must never survive into a production deployment.
_WEAK_PASSWORDS = {"password", "password1", "passw0rd", "admin", "admin123", "changeme", "secret", "1234567890", "qwertyuiop", "letmein1"}


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
    trash_retention_days: int = Field(default=30, ge=1)
    max_running_generation_jobs: int = 1
    max_running_generation_jobs_per_user: int = 1
    max_running_generation_jobs_per_provider: int = 1
    model_timeout_seconds: float = 120
    creative_context_max_characters: int = 60000
    frontend_dist: str | None = None

    @model_validator(mode="after")
    def enforce_production_secrets(self):
        if self.environment != "production":
            return self
        key = self.external_encryption_key
        if len(key) < 32 or key == "development-only-key" or key.startswith("change-this"):
            raise ValueError("production requires APP_EXTERNAL_ENCRYPTION_KEY with at least 32 characters and no placeholder value")
        if self.bootstrap_admin_password is not None:
            password = self.bootstrap_admin_password
            if len(password) < 12 or password.startswith("change-this") or password.lower() in _WEAK_PASSWORDS:
                raise ValueError("production requires APP_BOOTSTRAP_ADMIN_PASSWORD with at least 12 characters and no placeholder or common weak value")
        if self.bootstrap_secret is not None:
            if len(self.bootstrap_secret) < 16 or "change-this" in self.bootstrap_secret.lower():
                raise ValueError("production requires APP_BOOTSTRAP_SECRET with at least 16 characters and no placeholder value")
        return self

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore", hide_input_in_errors=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

