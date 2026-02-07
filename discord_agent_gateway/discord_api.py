from __future__ import annotations

import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from . import __version__


class DiscordAPIError(RuntimeError):
    def __init__(self, *, status_code: int, message: str, detail: Any | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail

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

    def get_webhook_with_token(self, *, webhook_id: str, webhook_token: str) -> Optional[dict[str, Any]]:
        """
        Fetch webhook metadata using the webhook token (does not require bot auth).
        Useful for validating which channel a webhook belongs to.
        """
        url = f"{self._api_base}/webhooks/{webhook_id}/{webhook_token}"
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
        *,
        webhook_id: str,
        webhook_token: str,
        content: str,
        username: Optional[str],
        avatar_url: Optional[str],
        wait: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._api_base}/webhooks/{webhook_id}/{webhook_token}"
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
