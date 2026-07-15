"""Application settings — reads from environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # Database
    database_url: str = "sqlite:///./data/orchestrator.db"

    # Auth
    jwt_secret: str = "change-me"
    jwt_ttl_hours: int = 72
    admin_emails: str = ""

    # Coolify API
    coolify_api_url: str = "https://coolify.kechlab.com"
    coolify_api_token: str = ""
    coolify_project_uuid: str = "tso4ocs4k0g0oso8wg04k4c8"
    coolify_environment: str = "production"
    coolify_server_uuid: str = "mggo8cg8kokwcgk48sw0o4c4"
    coolify_destination_uuid: str = "xl2ufkvx4pjnjpj8hgsoq9wd"

    # LLM
    openrouter_api_key: str = ""
    base_domain: str = "kechlab.com"

    # Plan
    plan_price_cents: int = 2900
    plan_monthly_credits_eur: float = 20.0
    default_model: str = "openai/gpt-4o-mini"


@lru_cache
def get_settings() -> Settings:
    return Settings()
