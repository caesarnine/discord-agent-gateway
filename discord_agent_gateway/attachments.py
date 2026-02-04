from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from .db import Attachment, Database
from .discord_api import DiscordAPI, DiscordAPIError


ALLOWED_DISCORD_CDN_HOSTS = {
    "cdn.discordapp.com",
    "media.discordapp.net",
}


@dataclass(frozen=True)
class ResolvedDownload:
    filename: str
    content_type: str
    size_bytes: Optional[int]
    url: str


class AttachmentProxy:
    def __init__(self, *, db: Database, discord: DiscordAPI):
        self._db = db
        self._discord = discord

    def resolve(self, attachment_id: str) -> Optional[ResolvedDownload]:
        attachment = self._db.attachment_get(attachment_id)
        if attachment is None:
            return None

        url = self._resolve_url(attachment)
        if url is None:
            return None

        content_type = attachment.content_type or "application/octet-stream"
        return ResolvedDownload(
            filename=attachment.filename,
            content_type=content_type,
            size_bytes=attachment.size_bytes,
            url=url,
        )

    def _resolve_url(self, attachment: Attachment) -> Optional[str]:
        # Prefer fresh URLs from the Discord API (some CDN URLs are time-limited).
        try:
            msg = self._discord.get_channel_message(
                channel_id=int(attachment.source_channel_id),
                message_id=int(attachment.discord_message_id),
            )
            for att in msg.get("attachments", []) or []:
                if str(att.get("id")) != attachment.attachment_id:
                    continue
                url = att.get("url") or att.get("proxy_url")
                if url:
                    return self._validate_cdn_url(str(url))
        except Exception:
            # Fallback to the stored URLs below.
            pass

        for candidate in (attachment.url, attachment.proxy_url):
            if candidate:
                try:
                    return self._validate_cdn_url(str(candidate))
                except ValueError:
                    continue
        return None

    def _validate_cdn_url(self, url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            raise ValueError("Attachment download URL must be https.")
        if host not in ALLOWED_DISCORD_CDN_HOSTS:
            raise ValueError(f"Refusing to proxy non-Discord host: {host}")
        return url

    def iter_download(self, url: str):
        # Let DiscordAPI handle streaming + errors.
        return self._discord.iter_download(url)

