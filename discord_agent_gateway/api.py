from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .db import Agent, Database
from .discord_api import DiscordAPIError, GatewayWebhookManager
from .docs import build_heartbeat_md, build_messaging_md, build_skill_md
from .util import split_for_discord, utc_now_iso


class AgentRegisterIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    avatar_url: Optional[str] = Field(None, max_length=500)


class AgentRegisterOut(BaseModel):
    agent_id: str
    token: str
    name: str
    avatar_url: Optional[str]


class PostIn(BaseModel):
    body: str = Field(..., min_length=1)


class PostOut(BaseModel):
    last_seq: Optional[int]
    last_discord_message_id: Optional[str]


class InboxOut(BaseModel):
    cursor: int
    next_cursor: int
    events: List[Dict[str, Any]]


class AckIn(BaseModel):
    cursor: int = Field(..., ge=0)


def create_app(*, settings: Settings, db: Database, webhooks: GatewayWebhookManager) -> FastAPI:
    app = FastAPI(title="Discord Agent Gateway", version=__version__)

    skill_md = build_skill_md(settings)
    heartbeat_md = build_heartbeat_md()
    messaging_md = build_messaging_md()

    def require_agent(authorization: Optional[str] = Header(default=None)) -> Agent:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <token>")
        token = authorization.split(" ", 1)[1].strip()
        agent = db.agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail="Invalid token")
        return agent

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        try:
            creds = webhooks.get_or_create()
            webhook_id = creds.webhook_id
            ok = True
            error = None
        except Exception as exc:  # noqa: BLE001 - healthz should never crash
            webhook_id = None
            ok = False
            error = str(exc)

        return {
            "ok": ok,
            "error": error,
            "channel_id": str(settings.discord_channel_id),
            "webhook_id": webhook_id,
            "db_path": str(settings.db_path),
            "version": __version__,
        }

    @app.get("/skill.md", response_class=PlainTextResponse)
    def get_skill_md() -> str:
        return skill_md

    @app.get("/heartbeat.md", response_class=PlainTextResponse)
    def get_heartbeat_md() -> str:
        return heartbeat_md

    @app.get("/messaging.md", response_class=PlainTextResponse)
    def get_messaging_md() -> str:
        return messaging_md

    @app.post("/v1/agents/register", response_model=AgentRegisterOut)
    def register_agent(inp: AgentRegisterIn) -> AgentRegisterOut:
        creds = db.agent_create(inp.name, inp.avatar_url)
        return AgentRegisterOut(
            agent_id=creds.agent_id,
            token=creds.token,
            name=inp.name,
            avatar_url=inp.avatar_url,
        )

    @app.get("/v1/me")
    def me(agent: Agent = Depends(require_agent)) -> Dict[str, Any]:
        return {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "avatar_url": agent.avatar_url,
            "last_cursor": db.receipt_get(agent.agent_id),
        }

    @app.get("/v1/capabilities")
    def capabilities(_: Agent = Depends(require_agent)) -> Dict[str, Any]:
        return {
            "platform": "discord",
            "single_channel": True,
            "channel_id": str(settings.discord_channel_id),
            "discord_hard_limit_chars": 2000,
            "gateway_split_limit": settings.discord_max_message_len,
            "mentions_enabled": False,
            "identity_fields": ["author_kind", "author_id", "author_name", "is_self", "is_human"],
        }

    @app.get("/v1/inbox", response_model=InboxOut)
    def inbox(
        cursor: Optional[int] = Query(None, ge=0),
        limit: int = Query(50, ge=1, le=200),
        agent: Agent = Depends(require_agent),
    ) -> InboxOut:
        if cursor is None:
            cursor = db.receipt_get(agent.agent_id)

        posts = db.inbox_fetch(str(settings.discord_channel_id), cursor, limit)
        next_cursor = cursor
        events: List[Dict[str, Any]] = []

        for post in posts:
            next_cursor = max(next_cursor, post.seq)

            is_self = (post.author_kind == "agent" and post.author_id == agent.agent_id)
            is_human = (post.author_kind == "human")

            events.append(
                {
                    "seq": post.seq,
                    "author_kind": post.author_kind,
                    "author_id": post.author_id,
                    "author_name": post.author_name,
                    "is_self": is_self,
                    "is_human": is_human,
                    "body": post.body,
                    "created_at": post.created_at,
                    "discord_message_id": post.discord_message_id,
                }
            )

        return InboxOut(cursor=cursor, next_cursor=next_cursor, events=events)

    @app.post("/v1/ack")
    def ack(inp: AckIn, agent: Agent = Depends(require_agent)) -> Dict[str, Any]:
        db.receipt_set(agent.agent_id, int(inp.cursor))
        return {"ok": True, "cursor": int(inp.cursor)}

    @app.post("/v1/post", response_model=PostOut)
    def post(inp: PostIn, agent: Agent = Depends(require_agent)) -> PostOut:
        try:
            webhooks.get_or_create()
        except DiscordAPIError as exc:
            raise HTTPException(status_code=502, detail={"discord_status": exc.status_code, "discord_error": exc.detail}) from exc

        chunks = split_for_discord(inp.body, max_len=settings.discord_max_message_len)
        last_seq: Optional[int] = None
        last_msg_id: Optional[str] = None

        for chunk in chunks:
            try:
                resp = webhooks.execute(
                    content=chunk,
                    username=agent.name,
                    avatar_url=agent.avatar_url,
                    wait=True,
                )
            except DiscordAPIError as exc:
                raise HTTPException(status_code=502, detail={"discord_status": exc.status_code, "discord_error": exc.detail}) from exc

            msg_id = str(resp.get("id") or "") or None
            last_msg_id = msg_id or last_msg_id

            seq = db.post_insert(
                author_kind="agent",
                author_id=agent.agent_id,
                author_name=agent.name,
                body=chunk,
                created_at=utc_now_iso(),
                discord_message_id=msg_id,
                discord_channel_id=str(settings.discord_channel_id),
            )
            if seq is None and msg_id:
                seq = db.post_mark_as_agent_by_discord_message_id(
                    discord_message_id=msg_id,
                    discord_channel_id=str(settings.discord_channel_id),
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                )
            if seq is not None:
                last_seq = seq

        return PostOut(last_seq=last_seq, last_discord_message_id=last_msg_id)

    return app
