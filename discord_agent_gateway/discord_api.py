from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from . import __version__
from .config import Settings
from .db import Database


class DiscordAPIError(RuntimeError):
    def __init__(self, *, status_code: int, message: str, detail: Any | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


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


class DiscordAPI:
    def __init__(self, *, bot_token: str, api_base: str):
        self._bot_token = bot_token
        self._api_base = api_base.rstrip("/")
        self._http = httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0))

    def _bot_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self._bot_token}",
            "Content-Type": "application/json",
            "User-Agent": f"discord-agent-gateway/{__version__}",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self._api_base}{path}"
        for _ in range(5):
            resp = self._http.request(method, url, headers=self._bot_headers(), params=params, json=json)

            if resp.status_code == 429:
                retry_after = 1.0
                try:
                    retry_after = float(resp.json().get("retry_after", 1.0))
                except Exception:
                    retry_after = 1.0
                time.sleep(min(10.0, retry_after))
                continue

            if 200 <= resp.status_code < 300:
                return resp.json() if resp.content else {}

            try:
                err = resp.json()
            except Exception:
                err = {"message": resp.text}
            raise DiscordAPIError(status_code=resp.status_code, message="Discord API error", detail=err)

        raise DiscordAPIError(status_code=429, message="Discord rate limit retry exhausted")

    def get_webhook(self, webhook_id: str) -> Optional[dict[str, Any]]:
        try:
            return self.request("GET", f"/webhooks/{webhook_id}")
        except DiscordAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    def get_webhook_with_token(self, creds: WebhookCredentials) -> Optional[dict[str, Any]]:
        """
        Fetch webhook metadata using the webhook token (does not require bot auth).
        Useful for validating which channel a webhook belongs to.
        """
        url = f"{self._api_base}/webhooks/{creds.webhook_id}/{creds.webhook_token}"
        resp = self._http.get(url)
        if resp.status_code == 404:
            return None
        if 200 <= resp.status_code < 300:
            return resp.json() if resp.content else {}
        try:
            err = resp.json()
        except Exception:
            err = {"message": resp.text}
        raise DiscordAPIError(status_code=resp.status_code, message="Discord webhook error", detail=err)

    def get_channel_message(self, *, channel_id: int, message_id: int) -> dict[str, Any]:
        return self.request("GET", f"/channels/{channel_id}/messages/{message_id}")

    def create_webhook(self, *, channel_id: int, name: str) -> dict[str, Any]:
        return self.request("POST", f"/channels/{channel_id}/webhooks", json={"name": name})

    def execute_webhook(
        self,
        creds: WebhookCredentials,
        *,
        content: str,
        username: Optional[str],
        avatar_url: Optional[str],
        wait: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._api_base}/webhooks/{creds.webhook_id}/{creds.webhook_token}"
        params = {"wait": "true" if wait else "false"}
        body: dict[str, Any] = {
            "content": content,
            "allowed_mentions": {"parse": []},
        }
        if username:
            body["username"] = username
        if avatar_url:
            body["avatar_url"] = avatar_url

        for _ in range(5):
            resp = self._http.post(url, params=params, json=body)

            if resp.status_code == 429:
                retry_after = 1.0
                try:
                    retry_after = float(resp.json().get("retry_after", 1.0))
                except Exception:
                    retry_after = 1.0
                time.sleep(min(10.0, retry_after))
                continue

            if 200 <= resp.status_code < 300:
                return resp.json() if (wait and resp.content) else {}

            try:
                err = resp.json()
            except Exception:
                err = {"message": resp.text}
            raise DiscordAPIError(status_code=resp.status_code, message="Discord webhook error", detail=err)

        raise DiscordAPIError(status_code=429, message="Webhook rate limit retry exhausted")

    def iter_download(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise DiscordAPIError(status_code=400, message="Unsupported URL scheme for download")
        with self._http.stream("GET", url, follow_redirects=True) as resp:
            if not (200 <= resp.status_code < 300):
                raise DiscordAPIError(status_code=resp.status_code, message="Download failed", detail=resp.text)
            for chunk in resp.iter_bytes():
                yield chunk


class GatewayWebhookManager:
    def __init__(self, *, settings: Settings, db: Database, discord: DiscordAPI):
        self._settings = settings
        self._db = db
        self._discord = discord
        self._lock = threading.Lock()
        self._cached: Optional[WebhookCredentials] = None

    def get_or_create(self) -> WebhookCredentials:
        """
        Ensure a webhook exists for outbound messages.

        Priority order:
        1) Use DISCORD_WEBHOOK_URL if set.
        2) Reuse DB-stored webhook id/token if it still exists.
        3) Create a new webhook in the channel (requires Manage Webhooks) and store it in DB.
        """
        with self._lock:
            if self._cached is not None:
                return self._cached

            if self._settings.discord_webhook_url:
                creds = parse_webhook_url(self._settings.discord_webhook_url)
                info = self._discord.get_webhook_with_token(creds)
                if info is None:
                    raise DiscordAPIError(status_code=400, message="Invalid DISCORD_WEBHOOK_URL (webhook not found)")
                webhook_channel_id = str(info.get("channel_id") or "")
                if webhook_channel_id and webhook_channel_id != str(self._settings.discord_channel_id):
                    raise DiscordAPIError(
                        status_code=400,
                        message="DISCORD_WEBHOOK_URL points to a different channel than DISCORD_CHANNEL_ID",
                        detail={"webhook_channel_id": webhook_channel_id, "discord_channel_id": str(self._settings.discord_channel_id)},
                    )
                self._cached = creds
                return self._cached

            webhook_id = self._db.setting_get("gateway_webhook_id")
            webhook_token = self._db.setting_get("gateway_webhook_token")
            if webhook_id and webhook_token:
                creds = WebhookCredentials(webhook_id=webhook_id, webhook_token=webhook_token)
                info = self._discord.get_webhook_with_token(creds)
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
            creds,
            content=content,
            username=username,
            avatar_url=avatar_url,
            wait=wait,
        )
