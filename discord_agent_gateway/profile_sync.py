from __future__ import annotations

import logging
from typing import Any, Protocol

from .config import Settings
from .db import Database
from .discord_api import DiscordAPIError


class ChannelLookupProtocol(Protocol):
    def get_channel(self, *, channel_id: int) -> dict[str, Any]: ...


def upsert_discord_channel_profile(*, db: Database, channel_name: str | None, channel_topic: str | None) -> None:
    """
    Persist the latest Discord channel metadata.

    Empty values are stored as empty strings so clearing a channel topic/name
    does not leave stale values in settings.
    """
    normalized_name = (channel_name or "").strip()
    normalized_topic = (channel_topic or "").strip()
    db.setting_set("discord_channel_name", normalized_name)
    db.setting_set("discord_channel_topic", normalized_topic)


def sync_discord_channel_profile(
    *,
    settings: Settings,
    db: Database,
    discord: ChannelLookupProtocol,
    logger: logging.Logger,
) -> bool:
    """
    Refresh channel name/topic from Discord API.

    Returns True when metadata was fetched and persisted, else False.
    """
    try:
        channel = discord.get_channel(channel_id=settings.discord_channel_id)
    except DiscordAPIError:
        logger.warning(
            "Failed to fetch channel metadata for DISCORD_CHANNEL_ID=%s",
            settings.discord_channel_id,
            exc_info=True,
        )
        return False
    except Exception:
        logger.warning(
            "Unexpected error while fetching channel metadata for DISCORD_CHANNEL_ID=%s",
            settings.discord_channel_id,
            exc_info=True,
        )
        return False

    name = channel.get("name")
    topic = channel.get("topic")
    upsert_discord_channel_profile(
        db=db,
        channel_name=(str(name) if isinstance(name, str) else None),
        channel_topic=(str(topic) if isinstance(topic, str) else None),
    )
    logger.info(
        "Synced channel profile metadata: name=%s topic_len=%s",
        (str(name).strip() if isinstance(name, str) and str(name).strip() else "<empty>"),
        len(str(topic).strip()) if isinstance(topic, str) else 0,
    )
    return True
