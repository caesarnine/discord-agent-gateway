from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Optional

from .config import Settings
from .db import Database
from .discord_api import DiscordAPI, DiscordAPIError


@dataclass(frozen=True)
class WebhookCredentials:
    webhook_id: str
    webhook_token: str


def parse_webhook_url(url: str) -> WebhookCredentials:
    parts = url.strip().split("/")
    if len(parts) < 2:
        raise ValueError("Invalid DISCORD_WEBHOOK_URL.")
    webhook_id = parts[-2]
    webhook_token = parts[-1]
    if not webhook_id or not webhook_token:
        raise ValueError("Invalid DISCORD_WEBHOOK_URL.")
    return WebhookCredentials(webhook_id=webhook_id, webhook_token=webhook_token)


class GatewayWebhookManager:
    def __init__(self, *, settings: Settings, db: Database, discord: DiscordAPI):
        self._settings = settings
        self._db = db
        self._discord = discord
        self._lock = threading.Lock()
        self._cached: Optional[WebhookCredentials] = None

    def get_or_create(self) -> WebhookCredentials:
        with self._lock:
            if self._cached is not None:
                return self._cached

            if self._settings.discord_webhook_url:
                creds = parse_webhook_url(self._settings.discord_webhook_url)
                info = self._discord.get_webhook_with_token(
                    webhook_id=creds.webhook_id,
                    webhook_token=creds.webhook_token,
                )
                if info is None:
                    raise DiscordAPIError(status_code=400, message="Invalid DISCORD_WEBHOOK_URL (webhook not found)")
                webhook_channel_id = str(info.get("channel_id") or "")
                if webhook_channel_id and webhook_channel_id != str(self._settings.discord_channel_id):
                    raise DiscordAPIError(
                        status_code=400,
                        message="DISCORD_WEBHOOK_URL points to a different channel than DISCORD_CHANNEL_ID",
                        detail={
                            "webhook_channel_id": webhook_channel_id,
                            "discord_channel_id": str(self._settings.discord_channel_id),
                        },
                    )
                self._cached = creds
                return self._cached

            webhook_id = self._db.setting_get("gateway_webhook_id")
            webhook_token = self._db.setting_get("gateway_webhook_token")
            if webhook_id and webhook_token:
                creds = WebhookCredentials(webhook_id=webhook_id, webhook_token=webhook_token)
                info = self._discord.get_webhook_with_token(
                    webhook_id=creds.webhook_id,
                    webhook_token=creds.webhook_token,
                )
                if info is not None:
                    webhook_channel_id = str(info.get("channel_id") or "")
                    if webhook_channel_id and webhook_channel_id == str(self._settings.discord_channel_id):
                        self._cached = creds
                        return self._cached

            webhook = self._discord.create_webhook(channel_id=self._settings.discord_channel_id, name="AgentGateway")
            webhook_id = str(webhook["id"])
            webhook_token = str(webhook["token"])

            self._db.setting_set("gateway_webhook_id", webhook_id)
            self._db.setting_set("gateway_webhook_token", webhook_token)

            self._cached = WebhookCredentials(webhook_id=webhook_id, webhook_token=webhook_token)
            return self._cached

    def execute(
        self,
        *,
        content: str,
        username: Optional[str],
        avatar_url: Optional[str],
        wait: bool = True,
    ) -> dict[str, Any]:
        creds = self.get_or_create()
        return self._discord.execute_webhook(
            webhook_id=creds.webhook_id,
            webhook_token=creds.webhook_token,
            content=content,
            username=username,
            avatar_url=avatar_url,
            wait=wait,
        )
