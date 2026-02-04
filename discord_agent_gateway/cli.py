from __future__ import annotations

import argparse
import logging
import os
import threading

import uvicorn

from . import __version__
from .attachments import AttachmentProxy
from .api import create_app
from .bot import build_discord_bot
from .config import Settings
from .db import Database
from .discord_api import DiscordAPI, GatewayWebhookManager
from .logging_setup import setup_logging


def _run_uvicorn(*, app, host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port, log_level="info")


def _print_effective_config(settings: Settings) -> None:
    print("Discord Agent Gateway config:")
    print(f"- version: {__version__}")
    print(f"- gateway_host: {settings.gateway_host}")
    print(f"- gateway_port: {settings.gateway_port}")
    print(f"- gateway_base_url: {settings.gateway_base_url}")
    print(f"- discord_channel_id: {settings.discord_channel_id}")
    print(f"- db_path: {settings.db_path}")
    print(f"- discord_webhook_url_set: {bool(settings.discord_webhook_url)}")
    print(f"- backfill_enabled: {settings.backfill_enabled}")
    print(f"- backfill_seed_limit: {settings.backfill_seed_limit}")
    print(f"- backfill_archived_thread_limit: {settings.backfill_archived_thread_limit}")
    print(f"- log_level: {settings.log_level}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="discord-agent-gateway")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--mode",
        choices=("run", "api", "bot"),
        default="run",
        help="run = API + bot (default); api = API only; bot = bot only",
    )
    parser.add_argument("--print-config", action="store_true", help="Print effective config and exit")
    args = parser.parse_args(argv)

    settings = Settings.from_env(os.environ)
    setup_logging(settings.log_level)
    logger = logging.getLogger("discord_agent_gateway")

    if args.print_config:
        _print_effective_config(settings)
        return

    db = Database(settings.db_path)
    db.init_schema()

    discord_api = DiscordAPI(bot_token=settings.discord_bot_token, api_base=settings.discord_api_base)
    webhooks = GatewayWebhookManager(settings=settings, db=db, discord=discord_api)
    attachments = AttachmentProxy(db=db, discord=discord_api)

    app = create_app(settings=settings, db=db, webhooks=webhooks, attachments=attachments)

    if args.mode == "api":
        logger.info("Starting API only on %s:%s", settings.gateway_host, settings.gateway_port)
        _run_uvicorn(app=app, host=settings.gateway_host, port=settings.gateway_port)
        return

    bot = build_discord_bot(settings=settings, db=db)

    if args.mode == "bot":
        logger.info("Starting Discord bot only (no API server).")
        bot.run(settings.discord_bot_token)
        return

    logger.info("Starting API server on %s:%s", settings.gateway_host, settings.gateway_port)
    api_thread = threading.Thread(
        target=_run_uvicorn,
        kwargs={"app": app, "host": settings.gateway_host, "port": settings.gateway_port},
        daemon=True,
    )
    api_thread.start()

    logger.info("Starting Discord bot.")
    bot.run(settings.discord_bot_token)
