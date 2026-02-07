from __future__ import annotations

import logging
from datetime import timezone

import discord

from .config import Settings
from .db import Database
from .models import Attachment
from .profile_sync import upsert_discord_channel_profile


def _ingest_discord_message(*, message: discord.Message, settings: Settings, db: Database) -> None:
    root_channel_id = str(settings.discord_channel_id)
    source_channel_id = str(getattr(message.channel, "id", settings.discord_channel_id))

    if message.id is None:
        return
    msg_id = str(message.id)

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
    created_at = message.created_at.replace(tzinfo=timezone.utc).isoformat()

    attachments = list(getattr(message, "attachments", []) or [])

    if not body and not attachments:
        return

    seq = db.post_insert(
        author_kind=author_kind,
        author_id=author_id,
        author_name=author_name,
        body=body,
        created_at=created_at,
        discord_message_id=msg_id,
        discord_channel_id=root_channel_id,
        source_channel_id=source_channel_id,
    )
    if seq is None:
        seq = db.post_seq_by_discord_message_id(discord_message_id=msg_id, discord_channel_id=root_channel_id)

    if seq is not None and attachments:
        db.attachments_insert(
            [
                Attachment(
                    attachment_id=str(a.id),
                    post_seq=seq,
                    discord_message_id=msg_id,
                    source_channel_id=source_channel_id,
                    filename=str(getattr(a, "filename", "")) or "attachment",
                    url=str(getattr(a, "url", "")) or None,
                    proxy_url=str(getattr(a, "proxy_url", "")) or None,
                    content_type=getattr(a, "content_type", None),
                    size_bytes=(int(getattr(a, "size", 0)) or None),
                    height=(int(getattr(a, "height", 0)) or None),
                    width=(int(getattr(a, "width", 0)) or None),
                )
                for a in attachments
            ]
        )

    db.ingestion_state_set(source_channel_id=source_channel_id, last_message_id=msg_id)


async def _backfill_channel(
    *,
    channel: discord.abc.Messageable,
    settings: Settings,
    db: Database,
    logger: logging.Logger,
) -> None:
    source_channel_id = str(getattr(channel, "id", ""))
    if not source_channel_id:
        return

    last_message_id = db.ingestion_state_get(source_channel_id)

    kwargs = {"oldest_first": True}
    if last_message_id:
        kwargs["after"] = discord.Object(id=int(last_message_id))
        kwargs["limit"] = None
        logger.info("Backfill channel_id=%s after=%s", source_channel_id, last_message_id)
    else:
        if settings.backfill_seed_limit <= 0:
            return
        kwargs["limit"] = settings.backfill_seed_limit
        logger.info("Backfill channel_id=%s seed_last=%s", source_channel_id, settings.backfill_seed_limit)

    async for message in channel.history(**kwargs):
        if message.guild is None:
            continue
        _ingest_discord_message(message=message, settings=settings, db=db)


async def _backfill_root_and_threads(
    *,
    bot: discord.Client,
    root_channel: discord.TextChannel,
    settings: Settings,
    db: Database,
    logger: logging.Logger,
) -> None:
    await _backfill_channel(channel=root_channel, settings=settings, db=db, logger=logger)

    thread_ids: set[int] = set()

    # 1) Any threads we have state for (covers archived threads too).
    for cid in db.ingestion_state_source_channels():
        if cid and cid != str(root_channel.id):
            try:
                thread_ids.add(int(cid))
            except ValueError:
                continue

    # 2) Currently active threads in the guild (filter to this channel).
    try:
        for thread in await root_channel.guild.active_threads():
            if getattr(thread, "parent_id", None) == root_channel.id:
                thread_ids.add(int(thread.id))
    except Exception:
        logger.exception("Failed to enumerate active threads for backfill.")

    # 3) Recently archived threads (best-effort).
    limit = settings.backfill_archived_thread_limit
    if limit > 0:
        try:
            async for thread in root_channel.archived_threads(limit=limit):
                thread_ids.add(int(thread.id))
        except Exception:
            logger.exception("Failed to enumerate archived public threads for backfill.")
        try:
            async for thread in root_channel.archived_threads(private=True, joined=True, limit=limit):
                thread_ids.add(int(thread.id))
        except Exception:
            logger.exception("Failed to enumerate archived private threads for backfill.")

    for thread_id in sorted(thread_ids):
        try:
            ch = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
            if isinstance(ch, discord.Thread) and getattr(ch, "parent_id", None) == root_channel.id:
                await _backfill_channel(channel=ch, settings=settings, db=db, logger=logger)
        except Exception:
            logger.debug("Skipping thread_id=%s (not accessible)", thread_id, exc_info=True)


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
            channel_topic = getattr(channel, "topic", None)
            guild_id = getattr(getattr(channel, "guild", None), "id", None)
            logger.info("Resolved channel: %s (id=%s, guild_id=%s)", channel_name, getattr(channel, "id", None), guild_id)

            upsert_discord_channel_profile(db=db, channel_name=channel_name, channel_topic=channel_topic)

            if settings.backfill_enabled and isinstance(channel, discord.TextChannel):
                await _backfill_root_and_threads(bot=bot, root_channel=channel, settings=settings, db=db, logger=logger)
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

            body = (message.content or "").strip()
            attachments = list(getattr(message, "attachments", []) or [])

            if not body and not attachments:
                is_human = (message.webhook_id is None) and (not getattr(message.author, "bot", False))
                if is_human and not warned_empty_human_message:
                    warned_empty_human_message = True
                    logger.warning(
                        "Got a human message id=%s in the target channel but content was empty. "
                        "Enable the bot's Message Content Intent in the Discord Developer Portal and restart the gateway.",
                        msg_id,
                    )
                return

            _ingest_discord_message(message=message, settings=settings, db=db)
        except Exception:
            logger.exception("on_message failed")

    return bot
