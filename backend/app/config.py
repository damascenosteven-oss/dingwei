from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./family-location.db"
    bootstrap_token: str = ""
    session_cookie_secure: bool = False
    session_ttl_days: int = 30
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    public_base_url: str = "http://localhost:5173"
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    expose_api_docs: bool = True
    sensitive_rate_limit_requests: int = Field(default=10, ge=1, le=1000)
    sensitive_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
