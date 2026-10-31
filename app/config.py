"""Application configuration loaded from environment variables and an optional .env file."""

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.constants import MIN_POLL_INTERVAL_SECONDS

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_timezone: str = "Asia/Jakarta"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    puln_base_url: str = "https://puln.seatin.id"
    puln_session_date: date | None = None
    puln_session_slug: str | None = None
    puln_session_url: str | None = None
    puln_poll_interval_seconds: float = Field(default=10, ge=MIN_POLL_INTERVAL_SECONDS)
    puln_max_poll_interval_seconds: float = Field(default=60, ge=MIN_POLL_INTERVAL_SECONDS)
    puln_request_timeout_seconds: float = Field(default=10, gt=0, le=60)

    database_url: str = "sqlite:///./data/puln_assistant.db"

    target_product_ids: Annotated[list[int], NoDecode] = Field(default_factory=lambda: [100])
    max_customers: int = Field(default=10, ge=1, le=10)

    @field_validator("puln_session_date", "puln_session_slug", "puln_session_url", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("target_product_ids", mode="before")
    @classmethod
    def _split_product_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("app_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @field_validator("puln_base_url")
    @classmethod
    def _https_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("PULN_BASE_URL must be an https URL")
        return value.rstrip("/")

    @field_validator("puln_session_slug")
    @classmethod
    def _safe_slug(cls, value: str | None) -> str | None:
        if value is not None and not _SLUG_PATTERN.fullmatch(value):
            raise ValueError("PULN_SESSION_SLUG may only contain lowercase letters, digits and -")
        return value

    @model_validator(mode="after")
    def _check_cross_field_rules(self) -> Self:
        if self.puln_max_poll_interval_seconds < self.puln_poll_interval_seconds:
            raise ValueError("PULN_MAX_POLL_INTERVAL_SECONDS must be >= PULN_POLL_INTERVAL_SECONDS")
        if self.puln_session_url is not None:
            session = urlsplit(self.puln_session_url)
            base = urlsplit(self.puln_base_url)
            if session.scheme != "https" or session.hostname != base.hostname:
                raise ValueError("PULN_SESSION_URL must be an https URL on the PULN_BASE_URL host")
        return self


def load_settings(env_file: Path | str | None = ".env") -> Settings:
    """Load settings; pass ``env_file=None`` to read only real environment variables."""
    return Settings(_env_file=env_file)
