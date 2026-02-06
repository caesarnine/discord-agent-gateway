from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Environment-driven configuration.

    Loads from:
    - Process environment variables
    - Optional `.env` file in the working directory (if present)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = Field("INFO", validation_alias="LOG_LEVEL")

    discord_bot_token: str = Field(..., validation_alias="DISCORD_BOT_TOKEN")
    discord_channel_id: int = Field(..., validation_alias="DISCORD_CHANNEL_ID")
    discord_webhook_url: str = Field("", validation_alias="DISCORD_WEBHOOK_URL")

    db_path: Path = Field(Path("data/agent_gateway.db"), validation_alias="DB_PATH")

    gateway_host: str = Field("127.0.0.1", validation_alias="GATEWAY_HOST")
    # Accept Railway's injected PORT without requiring an extra mapping variable.
    gateway_port: int = Field(8000, validation_alias=AliasChoices("GATEWAY_PORT", "PORT"))
    gateway_base_url: str = Field("", validation_alias="GATEWAY_BASE_URL")

    discord_api_base: str = Field("https://discord.com/api/v10", validation_alias="DISCORD_API_BASE")
    discord_max_message_len: int = Field(1900, validation_alias="DISCORD_MAX_MESSAGE_LEN")
    profile_name: str = Field(
        "Shared Agent Room",
        validation_alias=AliasChoices("CHANNEL_PROFILE_NAME", "CHANNEL_NAME"),
    )
    profile_mission: str = Field(
        "Collaborate with humans and other agents in this Discord channel. Read first, then respond when useful.",
        validation_alias=AliasChoices("CHANNEL_PROFILE_MISSION", "CHANNEL_MISSION"),
    )

    registration_mode: Literal["closed", "invite", "open"] = Field("closed", validation_alias="REGISTRATION_MODE")
    admin_api_token: str = Field("", validation_alias="ADMIN_API_TOKEN")
    register_rate_limit_count: int = Field(10, validation_alias="REGISTER_RATE_LIMIT_COUNT")
    register_rate_limit_window_seconds: int = Field(60, validation_alias="REGISTER_RATE_LIMIT_WINDOW_SECONDS")

    healthz_verbose: bool = Field(False, validation_alias="HEALTHZ_VERBOSE")

    backfill_enabled: bool = Field(True, validation_alias="BACKFILL_ENABLED")
    backfill_seed_limit: int = Field(200, validation_alias="BACKFILL_SEED_LIMIT")
    backfill_archived_thread_limit: int = Field(25, validation_alias="BACKFILL_ARCHIVED_THREAD_LIMIT")

    @model_validator(mode="after")
    def _normalize_and_validate(self) -> "Settings":
        log_level = (self.log_level or "INFO").upper().strip() or "INFO"
        object.__setattr__(self, "log_level", log_level)

        discord_webhook_url = (self.discord_webhook_url or "").strip()
        object.__setattr__(self, "discord_webhook_url", discord_webhook_url)

        discord_api_base = (self.discord_api_base or "").strip() or "https://discord.com/api/v10"
        object.__setattr__(self, "discord_api_base", discord_api_base)

        profile_name = (self.profile_name or "").strip() or "Shared Agent Room"
        object.__setattr__(self, "profile_name", profile_name)

        profile_mission = (self.profile_mission or "").strip()
        if not profile_mission:
            profile_mission = (
                "Collaborate with humans and other agents in this Discord channel. "
                "Read first, then respond when useful."
            )
        object.__setattr__(self, "profile_mission", profile_mission)

        registration_mode = (self.registration_mode or "closed").strip().lower()
        object.__setattr__(self, "registration_mode", registration_mode)

        admin_api_token = (self.admin_api_token or "").strip()
        object.__setattr__(self, "admin_api_token", admin_api_token)

        gateway_host = (self.gateway_host or "127.0.0.1").strip() or "127.0.0.1"
        object.__setattr__(self, "gateway_host", gateway_host)

        base = (self.gateway_base_url or "").strip()
        if not base:
            docs_host = gateway_host
            if docs_host in ("0.0.0.0", "::"):
                docs_host = "127.0.0.1"
            base = f"http://{docs_host}:{self.gateway_port}"
        base = base.rstrip("/")
        object.__setattr__(self, "gateway_base_url", base)

        errors: list[str] = []
        if not self.discord_bot_token:
            errors.append("Missing DISCORD_BOT_TOKEN.")
        if not self.discord_channel_id:
            errors.append("Missing DISCORD_CHANNEL_ID.")

        if not (1 <= self.gateway_port <= 65535):
            errors.append("GATEWAY_PORT must be between 1 and 65535.")
        if not (1 <= self.discord_max_message_len <= 2000):
            errors.append("DISCORD_MAX_MESSAGE_LEN must be between 1 and 2000.")
        if len(self.profile_name) > 120:
            errors.append("CHANNEL_PROFILE_NAME must be <= 120 characters.")
        if len(self.profile_mission) > 4000:
            errors.append("CHANNEL_PROFILE_MISSION must be <= 4000 characters.")
        if self.registration_mode not in {"closed", "invite", "open"}:
            errors.append("REGISTRATION_MODE must be one of: closed, invite, open.")
        if self.register_rate_limit_count <= 0:
            errors.append("REGISTER_RATE_LIMIT_COUNT must be > 0.")
        if self.register_rate_limit_window_seconds <= 0:
            errors.append("REGISTER_RATE_LIMIT_WINDOW_SECONDS must be > 0.")
        if self.backfill_seed_limit < 0:
            errors.append("BACKFILL_SEED_LIMIT must be >= 0.")
        if self.backfill_archived_thread_limit < 0:
            errors.append("BACKFILL_ARCHIVED_THREAD_LIMIT must be >= 0.")

        if errors:
            raise ValueError(" ".join(errors))

        return self
