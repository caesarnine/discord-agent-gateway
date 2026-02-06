from __future__ import annotations

import argparse
import logging
import threading
from datetime import datetime, timezone

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
    print(f"- channel_profile_name: {settings.profile_name}")
    print(f"- channel_profile_mission: {settings.profile_mission}")
    print(f"- registration_mode: {settings.registration_mode}")
    print(f"- admin_api_token_set: {bool(settings.admin_api_token)}")
    print(f"- register_rate_limit_count: {settings.register_rate_limit_count}")
    print(f"- register_rate_limit_window_seconds: {settings.register_rate_limit_window_seconds}")
    print(f"- healthz_verbose: {settings.healthz_verbose}")
    print(f"- backfill_enabled: {settings.backfill_enabled}")
    print(f"- backfill_seed_limit: {settings.backfill_seed_limit}")
    print(f"- backfill_archived_thread_limit: {settings.backfill_archived_thread_limit}")
    print(f"- log_level: {settings.log_level}")


def _iso_utc(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _handle_admin_cli(args: argparse.Namespace, db: Database) -> bool:
    if args.create_agent:
        creds = db.agent_create(args.create_agent, args.agent_avatar_url or None)
        print("Agent created:")
        print(f"- agent_id: {creds.agent_id}")
        print(f"- token: {creds.token}")
        return True

    if args.list_agents:
        rows = db.agents_list()
        if not rows:
            print("No agents found.")
            return True
        for row in rows:
            print(
                f"{row.agent_id}\tname={row.name}\trevoked_at={row.revoked_at or '-'}\tcreated_at={row.created_at}"
            )
        return True

    if args.revoke_agent:
        ok = db.agent_revoke(args.revoke_agent)
        print("revoked" if ok else "not-found-or-already-revoked")
        return True

    if args.rotate_agent_token:
        token = db.agent_rotate_token(args.rotate_agent_token)
        if token is None:
            print("not-found-or-revoked")
        else:
            print("token-rotated")
            print(token)
        return True

    if args.create_invite:
        try:
            expires_at = _iso_utc(args.invite_expires_at)
        except ValueError as exc:
            raise SystemExit(f"Invalid --invite-expires-at: {exc}") from exc
        result = db.invite_create(
            label=args.invite_label or None,
            max_uses=args.invite_max_uses,
            expires_at=expires_at,
        )
        print("Invite created:")
        print(f"- invite_id: {result.invite.invite_id}")
        print(f"- code: {result.code}")
        print(f"- max_uses: {result.invite.max_uses}")
        print(f"- expires_at: {result.invite.expires_at or '-'}")
        return True

    if args.list_invites:
        rows = db.invite_list()
        if not rows:
            print("No invites found.")
            return True
        for row in rows:
            print(
                f"{row.invite_id}\tlabel={row.label or '-'}\tuses={row.used_count}/{row.max_uses}\texpires_at={row.expires_at or '-'}\trevoked_at={row.revoked_at or '-'}"
            )
        return True

    if args.revoke_invite:
        ok = db.invite_revoke(args.revoke_invite)
        print("revoked" if ok else "not-found-or-already-revoked")
        return True

    return False


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
    parser.add_argument("--create-agent", metavar="NAME", help="Create an agent credential and print its token.")
    parser.add_argument("--agent-avatar-url", default="", help="Optional avatar URL for --create-agent.")
    parser.add_argument("--list-agents", action="store_true", help="List agents.")
    parser.add_argument("--revoke-agent", metavar="AGENT_ID", help="Revoke agent token.")
    parser.add_argument("--rotate-agent-token", metavar="AGENT_ID", help="Rotate token for an active agent.")
    parser.add_argument("--create-invite", action="store_true", help="Create an invite code.")
    parser.add_argument("--invite-label", default="", help="Optional label for --create-invite.")
    parser.add_argument("--invite-max-uses", type=int, default=1, help="Max uses for --create-invite.")
    parser.add_argument(
        "--invite-expires-at",
        default="",
        help="Optional ISO timestamp for invite expiry (for example: 2026-03-01T00:00:00Z).",
    )
    parser.add_argument("--list-invites", action="store_true", help="List invites.")
    parser.add_argument("--revoke-invite", metavar="INVITE_ID", help="Revoke an invite.")
    args = parser.parse_args(argv)

    if args.create_invite and args.invite_max_uses < 1:
        parser.error("--invite-max-uses must be >= 1")

    settings = Settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger("discord_agent_gateway")

    if args.print_config:
        _print_effective_config(settings)
        return

    db = Database(settings.db_path)
    db.init_schema()

    if _handle_admin_cli(args, db):
        return

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
