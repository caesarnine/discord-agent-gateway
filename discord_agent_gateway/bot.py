from __future__ import annotations

import logging
from datetime import timezone

import discord

from .config import Settings
from .db import Database


def build_discord_bot(*, settings: Settings, db: Database) -> discord.Client:
    logger = logging.getLogger("discord_agent_gateway.bot")

    intents = discord.Intents.default()
    intents.guilds = True
    intents.messages = True
    intents.message_content = True  # must be enabled in Discord dev portal too

    bot = discord.Client(intents=intents)
    warned_empty_human_message = False

    @bot.event
    async def on_ready() -> None:
        logger.info("Discord bot ready: %s (id=%s)", bot.user, getattr(bot.user, "id", None))
        try:
            channel = bot.get_channel(settings.discord_channel_id) or await bot.fetch_channel(settings.discord_channel_id)
            channel_name = getattr(channel, "name", None)
            guild_id = getattr(getattr(channel, "guild", None), "id", None)
            logger.info("Resolved channel: %s (id=%s, guild_id=%s)", channel_name, getattr(channel, "id", None), guild_id)
        except Exception:
            logger.exception(
                "Failed to resolve DISCORD_CHANNEL_ID=%s. Check the channel ID and the bot's permissions (View Channel, Read Message History).",
                settings.discord_channel_id,
            )
        logger.info("Watching channel id=%s", settings.discord_channel_id)

    @bot.event
    async def on_message(message: discord.Message) -> None:
        """
        Ingest channel messages into DB.

        Humans, other bots, and webhook messages are captured here.
        Gateway-sent agent messages are usually inserted at send-time; we dedupe by discord_message_id.
        """
        nonlocal warned_empty_human_message
        try:
            if message.guild is None:
                return

            channel_id = getattr(message.channel, "id", None)
            parent_id = getattr(message.channel, "parent_id", None)  # threads
            if channel_id != settings.discord_channel_id and parent_id != settings.discord_channel_id:
                return

            if message.id is None:
                return

            msg_id = str(message.id)
            logger.debug(
                "on_message id=%s channel_id=%s parent_id=%s author_id=%s author_bot=%s webhook_id=%s content_len=%s",
                msg_id,
                channel_id,
                parent_id,
                getattr(message.author, "id", None),
                getattr(message.author, "bot", None),
                getattr(message, "webhook_id", None),
                len(message.content or ""),
            )

            if db.post_exists_by_discord_message_id(msg_id):
                return

            if message.webhook_id is not None:
                author_kind = "webhook"
                author_id = str(message.webhook_id)
            elif message.author.bot:
                author_kind = "bot"
                author_id = str(message.author.id)
            else:
                author_kind = "human"
                author_id = str(message.author.id)

            author_name = (
                getattr(message.author, "display_name", None)
                or getattr(message.author, "name", None)
                or str(message.author)
            )

            body = (message.content or "").strip()
            if not body and getattr(message, "attachments", None):
                body = "\n".join(a.url for a in message.attachments if getattr(a, "url", None)).strip()

            created_at = message.created_at.replace(tzinfo=timezone.utc).isoformat()

            if not body:
                if author_kind == "human" and not warned_empty_human_message:
                    warned_empty_human_message = True
                    logger.warning(
                        "Got a human message id=%s in the target channel but content was empty. "
                        "Enable the bot's Message Content Intent in the Discord Developer Portal and restart the gateway.",
                        msg_id,
                    )
                return

            db.post_insert(
                author_kind=author_kind,
                author_id=author_id,
                author_name=author_name,
                body=body,
                created_at=created_at,
                discord_message_id=msg_id,
                discord_channel_id=str(settings.discord_channel_id),
            )
        except Exception:
            logger.exception("on_message failed")

    return bot

