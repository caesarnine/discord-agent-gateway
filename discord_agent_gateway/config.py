from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    log_level: str

    discord_bot_token: str
    discord_channel_id: int
    discord_webhook_url: str

    db_path: Path

    gateway_host: str
    gateway_port: int
    gateway_base_url: str

    discord_api_base: str
    discord_max_message_len: int

    backfill_enabled: bool
    backfill_seed_limit: int
    backfill_archived_thread_limit: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Settings":
        log_level = (environ.get("LOG_LEVEL") or "INFO").upper().strip() or "INFO"

        discord_bot_token = (environ.get("DISCORD_BOT_TOKEN") or "").strip()
        discord_webhook_url = (environ.get("DISCORD_WEBHOOK_URL") or "").strip()

        channel_raw = (environ.get("DISCORD_CHANNEL_ID") or "").strip()
        try:
            discord_channel_id = int(channel_raw) if channel_raw else 0
        except ValueError as exc:
            raise ValueError("DISCORD_CHANNEL_ID must be an integer.") from exc

        db_path = Path((environ.get("DB_PATH") or "data/agent_gateway.db")).expanduser()

        gateway_host = (environ.get("GATEWAY_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        port_raw = (environ.get("GATEWAY_PORT") or "8000").strip() or "8000"
        try:
            gateway_port = int(port_raw)
        except ValueError as exc:
            raise ValueError("GATEWAY_PORT must be an integer.") from exc

        gateway_base_url = (environ.get("GATEWAY_BASE_URL") or "").strip()
        if not gateway_base_url:
            docs_host = gateway_host
            if docs_host in ("0.0.0.0", "::"):
                docs_host = "127.0.0.1"
            gateway_base_url = f"http://{docs_host}:{gateway_port}"
        gateway_base_url = gateway_base_url.rstrip("/")

        discord_api_base = (environ.get("DISCORD_API_BASE") or "https://discord.com/api/v10").strip()
        if not discord_api_base:
            discord_api_base = "https://discord.com/api/v10"

        max_len_raw = (environ.get("DISCORD_MAX_MESSAGE_LEN") or "").strip()
        if max_len_raw:
            try:
                discord_max_message_len = int(max_len_raw)
            except ValueError as exc:
                raise ValueError("DISCORD_MAX_MESSAGE_LEN must be an integer.") from exc
        else:
            discord_max_message_len = 1900

        backfill_enabled_raw = (environ.get("BACKFILL_ENABLED") or "true").strip().lower()
        backfill_enabled = backfill_enabled_raw not in ("0", "false", "no", "off")

        seed_raw = (environ.get("BACKFILL_SEED_LIMIT") or "200").strip() or "200"
        try:
            backfill_seed_limit = int(seed_raw)
        except ValueError as exc:
            raise ValueError("BACKFILL_SEED_LIMIT must be an integer.") from exc

        archived_raw = (environ.get("BACKFILL_ARCHIVED_THREAD_LIMIT") or "25").strip() or "25"
        try:
            backfill_archived_thread_limit = int(archived_raw)
        except ValueError as exc:
            raise ValueError("BACKFILL_ARCHIVED_THREAD_LIMIT must be an integer.") from exc

        settings = cls(
            log_level=log_level,
            discord_bot_token=discord_bot_token,
            discord_channel_id=discord_channel_id,
            discord_webhook_url=discord_webhook_url,
            db_path=db_path,
            gateway_host=gateway_host,
            gateway_port=gateway_port,
            gateway_base_url=gateway_base_url,
            discord_api_base=discord_api_base,
            discord_max_message_len=discord_max_message_len,
            backfill_enabled=backfill_enabled,
            backfill_seed_limit=backfill_seed_limit,
            backfill_archived_thread_limit=backfill_archived_thread_limit,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        errors: list[str] = []

        if not self.discord_bot_token:
            errors.append("Missing DISCORD_BOT_TOKEN.")
        if not self.discord_channel_id:
            errors.append("Missing DISCORD_CHANNEL_ID.")

        if not (1 <= self.gateway_port <= 65535):
            errors.append("GATEWAY_PORT must be between 1 and 65535.")

        if not (1 <= self.discord_max_message_len <= 2000):
            errors.append("DISCORD_MAX_MESSAGE_LEN must be between 1 and 2000.")

        if self.backfill_seed_limit < 0:
            errors.append("BACKFILL_SEED_LIMIT must be >= 0.")
        if self.backfill_archived_thread_limit < 0:
            errors.append("BACKFILL_ARCHIVED_THREAD_LIMIT must be >= 0.")

        if errors:
            raise ValueError(" ".join(errors))
