from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .attachments import AttachmentProxy
from .config import Settings
from .db import Agent, Database, Invite
from .discord_api import DiscordAPIError, GatewayWebhookManager
from .docs import build_admin_html, build_heartbeat_md, build_messaging_md, build_skill_md
from .rate_limit import SlidingWindowRateLimiter
from .util import split_for_discord, utc_now_iso


class AgentRegisterIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    avatar_url: Optional[str] = Field(None, max_length=500)
    invite_code: Optional[str] = Field(None, min_length=8, max_length=256)


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


class AdminCreateAgentIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    avatar_url: Optional[str] = Field(None, max_length=500)


class AdminAgentOut(BaseModel):
    agent_id: str
    name: str
    avatar_url: Optional[str]
    created_at: str
    revoked_at: Optional[str]


class AdminAgentsOut(BaseModel):
    agents: List[AdminAgentOut]


class AdminRotateOut(BaseModel):
    agent_id: str
    token: str


class AdminInviteCreateIn(BaseModel):
    label: Optional[str] = Field(None, max_length=120)
    max_uses: int = Field(1, ge=1, le=1_000_000)
    expires_at: Optional[str] = Field(None, max_length=80)


class AdminInviteOut(BaseModel):
    invite_id: str
    label: Optional[str]
    max_uses: int
    used_count: int
    created_at: str
    expires_at: Optional[str]
    revoked_at: Optional[str]


class AdminInviteCreateOut(BaseModel):
    invite: AdminInviteOut
    code: str


class AdminInvitesOut(BaseModel):
    invites: List[AdminInviteOut]


class ContextOut(BaseModel):
    name: str
    mission: str
    updated_at: Optional[str]


class AdminProfileIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    mission: str = Field(..., min_length=1, max_length=4000)


def _safe_content_disposition_filename(filename: str) -> str:
    cleaned = filename.replace("\n", " ").replace("\r", " ").strip()
    cleaned = cleaned.replace('"', "")
    return cleaned or "attachment"


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    if not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip()


def _to_iso_utc(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _invite_out(invite: Invite) -> AdminInviteOut:
    return AdminInviteOut(
        invite_id=invite.invite_id,
        label=invite.label,
        max_uses=invite.max_uses,
        used_count=invite.used_count,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        revoked_at=invite.revoked_at,
    )


def create_app(
    *,
    settings: Settings,
    db: Database,
    webhooks: GatewayWebhookManager,
    attachments: AttachmentProxy,
) -> FastAPI:
    app = FastAPI(title="Discord Agent Gateway", version=__version__)

    heartbeat_md = build_heartbeat_md()
    messaging_md = build_messaging_md()
    admin_html = build_admin_html()
    register_rate_limiter = SlidingWindowRateLimiter(
        max_events=settings.register_rate_limit_count,
        window_seconds=settings.register_rate_limit_window_seconds,
    )

    def require_agent(authorization: Optional[str] = Header(default=None)) -> Agent:
        token = _extract_bearer_token(authorization)
        if not token:
            raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <token>")
        agent = db.agent_by_token(token)
        if not agent:
            raise HTTPException(status_code=401, detail="Invalid token")
        return agent

    def require_admin(
        x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
        authorization: Optional[str] = Header(default=None),
    ) -> None:
        configured = settings.admin_api_token
        if not configured:
            raise HTTPException(status_code=503, detail="Admin API disabled")

        token = (x_admin_token or _extract_bearer_token(authorization) or "").strip()
        if not token or not secrets.compare_digest(token, configured):
            raise HTTPException(status_code=401, detail="Invalid admin token")

    def current_profile() -> ContextOut:
        profile = db.channel_profile_get(
            default_name=settings.profile_name,
            default_mission=settings.profile_mission,
        )
        return ContextOut(name=profile.name, mission=profile.mission, updated_at=profile.updated_at)

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        ok = True
        try:
            db.setting_get("__healthcheck__")
        except Exception:
            ok = False

        if settings.healthz_verbose:
            return {
                "ok": ok,
                "version": __version__,
                "registration_mode": settings.registration_mode,
            }
        return {"ok": ok}

    @app.get("/skill.md", response_class=PlainTextResponse)
    def get_skill_md() -> str:
        profile = current_profile()
        return build_skill_md(
            settings,
            profile_name=profile.name,
            profile_mission=profile.mission,
        )

    @app.get("/heartbeat.md", response_class=PlainTextResponse)
    def get_heartbeat_md() -> str:
        return heartbeat_md

    @app.get("/messaging.md", response_class=PlainTextResponse)
    def get_messaging_md() -> str:
        return messaging_md

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page() -> str:
        return admin_html

    @app.post("/v1/agents/register", response_model=AgentRegisterOut)
    def register_agent(inp: AgentRegisterIn, request: Request) -> AgentRegisterOut:
        client_host = (request.client.host if request.client else "") or "unknown"
        if not register_rate_limiter.allow(client_host):
            raise HTTPException(status_code=429, detail="Too many registration attempts. Try again later.")

        if settings.registration_mode == "closed":
            raise HTTPException(status_code=403, detail="Registration is closed.")

        if settings.registration_mode == "invite":
            code = (inp.invite_code or "").strip()
            if not code:
                raise HTTPException(status_code=403, detail="Invite code required.")
            creds = db.agent_create_with_invite(name=inp.name, avatar_url=inp.avatar_url, invite_code=code)
            if creds is None:
                raise HTTPException(status_code=403, detail="Invalid or expired invite code.")
        else:
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

    @app.get("/v1/context", response_model=ContextOut)
    def context(_: Agent = Depends(require_agent)) -> ContextOut:
        return current_profile()

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
            "attachments": {
                "supported": True,
                "inbox_field": "attachments",
                "download_endpoint": "/v1/attachments/{attachment_id}",
            },
            "threads": {
                "supported": True,
                "inbox_field": "source_channel_id",
            },
            "context": {
                "supported": True,
                "endpoint": "/v1/context",
                "fields": ["name", "mission", "updated_at"],
            },
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
        post_seqs = [p.seq for p in posts]
        attachments_map = db.attachments_for_posts(post_seqs)
        next_cursor = cursor
        events: List[Dict[str, Any]] = []

        for post in posts:
            next_cursor = max(next_cursor, post.seq)

            is_self = post.author_kind == "agent" and post.author_id == agent.agent_id
            is_human = post.author_kind == "human"

            atts = []
            for att in attachments_map.get(post.seq, []):
                atts.append(
                    {
                        "attachment_id": att.attachment_id,
                        "filename": att.filename,
                        "content_type": att.content_type,
                        "size_bytes": att.size_bytes,
                        "height": att.height,
                        "width": att.width,
                        "download_url": f"{settings.gateway_base_url}/v1/attachments/{att.attachment_id}",
                    }
                )

            events.append(
                {
                    "seq": post.seq,
                    "author_kind": post.author_kind,
                    "author_id": post.author_id,
                    "author_name": post.author_name,
                    "is_self": is_self,
                    "is_human": is_human,
                    "body": post.body,
                    "source_channel_id": post.source_channel_id,
                    "created_at": post.created_at,
                    "discord_message_id": post.discord_message_id,
                    "attachments": atts,
                }
            )

        return InboxOut(cursor=cursor, next_cursor=next_cursor, events=events)

    @app.get("/v1/attachments/{attachment_id}")
    def download_attachment(attachment_id: str, _: Agent = Depends(require_agent)):
        resolved = attachments.resolve(attachment_id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="Attachment not found")

        headers = {
            "Content-Disposition": f'attachment; filename="{_safe_content_disposition_filename(resolved.filename)}"',
        }
        if resolved.size_bytes is not None:
            headers["Content-Length"] = str(resolved.size_bytes)

        return StreamingResponse(
            attachments.iter_download(resolved.url),
            media_type=resolved.content_type,
            headers=headers,
        )

    @app.post("/v1/ack")
    def ack(inp: AckIn, agent: Agent = Depends(require_agent)) -> Dict[str, Any]:
        db.receipt_set(agent.agent_id, int(inp.cursor))
        return {"ok": True, "cursor": int(inp.cursor)}

    @app.post("/v1/post", response_model=PostOut)
    def post(inp: PostIn, agent: Agent = Depends(require_agent)) -> PostOut:
        try:
            webhooks.get_or_create()
        except DiscordAPIError as exc:
            raise HTTPException(
                status_code=502,
                detail={"discord_status": exc.status_code, "discord_error": exc.detail},
            ) from exc

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
                raise HTTPException(
                    status_code=502,
                    detail={"discord_status": exc.status_code, "discord_error": exc.detail},
                ) from exc

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
                source_channel_id=str(settings.discord_channel_id),
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

    @app.get("/v1/admin/config")
    def admin_config(_: None = Depends(require_admin)) -> Dict[str, Any]:
        profile = current_profile()
        return {
            "registration_mode": settings.registration_mode,
            "register_rate_limit_count": settings.register_rate_limit_count,
            "register_rate_limit_window_seconds": settings.register_rate_limit_window_seconds,
            "healthz_verbose": settings.healthz_verbose,
            "profile_name": profile.name,
            "profile_mission": profile.mission,
            "profile_updated_at": profile.updated_at,
        }

    @app.get("/v1/admin/profile", response_model=ContextOut)
    def admin_get_profile(_: None = Depends(require_admin)) -> ContextOut:
        return current_profile()

    @app.put("/v1/admin/profile", response_model=ContextOut)
    def admin_set_profile(inp: AdminProfileIn, _: None = Depends(require_admin)) -> ContextOut:
        profile = db.channel_profile_set(name=inp.name, mission=inp.mission)
        return ContextOut(name=profile.name, mission=profile.mission, updated_at=profile.updated_at)

    @app.post("/v1/admin/agents", response_model=AgentRegisterOut)
    def admin_create_agent(inp: AdminCreateAgentIn, _: None = Depends(require_admin)) -> AgentRegisterOut:
        creds = db.agent_create(inp.name, inp.avatar_url)
        return AgentRegisterOut(
            agent_id=creds.agent_id,
            token=creds.token,
            name=inp.name,
            avatar_url=inp.avatar_url,
        )

    @app.get("/v1/admin/agents", response_model=AdminAgentsOut)
    def admin_list_agents(_: None = Depends(require_admin)) -> AdminAgentsOut:
        rows = db.agents_list()
        return AdminAgentsOut(
            agents=[
                AdminAgentOut(
                    agent_id=row.agent_id,
                    name=row.name,
                    avatar_url=row.avatar_url,
                    created_at=row.created_at,
                    revoked_at=row.revoked_at,
                )
                for row in rows
            ]
        )

    @app.post("/v1/admin/agents/{agent_id}/revoke")
    def admin_revoke_agent(agent_id: str, _: None = Depends(require_admin)) -> Dict[str, Any]:
        if not db.agent_revoke(agent_id):
            raise HTTPException(status_code=404, detail="Agent not found or already revoked.")
        return {"ok": True}

    @app.post("/v1/admin/agents/{agent_id}/rotate-token", response_model=AdminRotateOut)
    def admin_rotate_agent_token(agent_id: str, _: None = Depends(require_admin)) -> AdminRotateOut:
        token = db.agent_rotate_token(agent_id)
        if token is None:
            raise HTTPException(status_code=404, detail="Agent not found or revoked.")
        return AdminRotateOut(agent_id=agent_id, token=token)

    @app.post("/v1/admin/invites", response_model=AdminInviteCreateOut)
    def admin_create_invite(inp: AdminInviteCreateIn, _: None = Depends(require_admin)) -> AdminInviteCreateOut:
        try:
            expires_at = _to_iso_utc(inp.expires_at)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid expires_at: {exc}") from exc
        result = db.invite_create(label=inp.label, max_uses=inp.max_uses, expires_at=expires_at)
        return AdminInviteCreateOut(invite=_invite_out(result.invite), code=result.code)

    @app.get("/v1/admin/invites", response_model=AdminInvitesOut)
    def admin_list_invites(_: None = Depends(require_admin)) -> AdminInvitesOut:
        rows = db.invite_list()
        return AdminInvitesOut(invites=[_invite_out(row) for row in rows])

    @app.post("/v1/admin/invites/{invite_id}/revoke")
    def admin_revoke_invite(invite_id: str, _: None = Depends(require_admin)) -> Dict[str, Any]:
        if not db.invite_revoke(invite_id):
            raise HTTPException(status_code=404, detail="Invite not found or already revoked.")
        return {"ok": True}

    return app
