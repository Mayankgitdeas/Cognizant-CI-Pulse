"""
config.py — Application configuration

Reads settings from environment variables (or a .env file in development).
All other modules import `settings` to access config values.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All app configuration in one place."""

    # Database
    database_url: str = "sqlite:///./pulse.db"

    # Admin panel credentials
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # Session signing
    session_secret: str = "please-change-this-to-a-random-long-string"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Logging
    log_level: str = "INFO"

    # Signal retention — older than this gets auto-pruned
    signal_retention_days: int = 90

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
