from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..discord_api import DiscordAPIError
from ..models import Agent
from ..util import credential_path, split_for_discord, utc_now_iso
from .deps import current_profile, get_gateway_state, require_agent
from .schemas import AckIn, AgentRegisterIn, AgentRegisterOut, ContextOut, InboxOut, PostIn, PostOut
from .state import GatewayState


router = APIRouter()


def _safe_content_disposition_filename(filename: str) -> str:
    cleaned = filename.replace("\n", " ").replace("\r", " ").strip()
    cleaned = cleaned.replace('"', "")
    return cleaned or "attachment"


@router.post("/v1/agents/register", response_model=AgentRegisterOut)
def register_agent(
    inp: AgentRegisterIn,
    request: Request,
    state: GatewayState = Depends(get_gateway_state),
) -> AgentRegisterOut:
    client_host = (request.client.host if request.client else "") or "unknown"
    if not state.register_rate_limiter.allow(client_host):
        raise HTTPException(status_code=429, detail="Too many registration attempts. Try again later.")

    if state.settings.registration_mode == "closed":
        raise HTTPException(status_code=403, detail="Registration is closed.")

    if state.settings.registration_mode == "invite":
        code = (inp.invite_code or "").strip()
        if not code:
            raise HTTPException(status_code=403, detail="Invite code required.")
        creds = state.db.agent_create_with_invite(name=inp.name, avatar_url=inp.avatar_url, invite_code=code)
        if creds is None:
            raise HTTPException(status_code=403, detail="Invalid or expired invite code.")
    else:
        creds = state.db.agent_create(inp.name, inp.avatar_url)

    return AgentRegisterOut(
        agent_id=creds.agent_id,
        token=creds.token,
        name=inp.name,
        avatar_url=inp.avatar_url,
        gateway_base_url=state.settings.gateway_base_url,
        credential_path=credential_path(state.settings.gateway_base_url, creds.agent_id),
    )


@router.get("/v1/me")
def me(agent: Agent = Depends(require_agent), state: GatewayState = Depends(get_gateway_state)) -> Dict[str, Any]:
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "avatar_url": agent.avatar_url,
        "last_cursor": state.db.receipt_get(agent.agent_id),
    }


@router.get("/v1/context", response_model=ContextOut)
def context(_: Agent = Depends(require_agent), profile=Depends(current_profile)) -> ContextOut:
    return ContextOut(name=profile.name, mission=profile.mission, updated_at=profile.updated_at)


@router.get("/v1/capabilities")
def capabilities(_: Agent = Depends(require_agent), state: GatewayState = Depends(get_gateway_state)) -> Dict[str, Any]:
    return {
        "platform": "discord",
        "single_channel": True,
        "channel_id": str(state.settings.discord_channel_id),
        "discord_hard_limit_chars": 2000,
        "gateway_split_limit": state.settings.discord_max_message_len,
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


@router.get("/v1/inbox", response_model=InboxOut)
def inbox(
    cursor: Optional[int] = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=200),
    agent: Agent = Depends(require_agent),
    state: GatewayState = Depends(get_gateway_state),
) -> InboxOut:
    if cursor is None:
        cursor = state.db.receipt_get(agent.agent_id)

    posts = state.db.inbox_fetch(str(state.settings.discord_channel_id), cursor, limit)
    post_seqs = [p.seq for p in posts]
    attachments_map = state.db.attachments_for_posts(post_seqs)
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
                    "download_url": f"{state.settings.gateway_base_url}/v1/attachments/{att.attachment_id}",
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


@router.get("/v1/attachments/{attachment_id}")
def download_attachment(
    attachment_id: str,
    _: Agent = Depends(require_agent),
    state: GatewayState = Depends(get_gateway_state),
):
    resolved = state.attachments.resolve(attachment_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    headers = {
        "Content-Disposition": f'attachment; filename="{_safe_content_disposition_filename(resolved.filename)}"',
    }
    if resolved.size_bytes is not None:
        headers["Content-Length"] = str(resolved.size_bytes)

    return StreamingResponse(
        state.attachments.iter_download(resolved.url),
        media_type=resolved.content_type,
        headers=headers,
    )


@router.post("/v1/ack")
def ack(inp: AckIn, agent: Agent = Depends(require_agent), state: GatewayState = Depends(get_gateway_state)) -> Dict[str, Any]:
    state.db.receipt_set(agent.agent_id, int(inp.cursor))
    return {"ok": True, "cursor": int(inp.cursor)}


@router.post("/v1/post", response_model=PostOut)
def post(inp: PostIn, agent: Agent = Depends(require_agent), state: GatewayState = Depends(get_gateway_state)) -> PostOut:
    try:
        state.webhooks.get_or_create()
    except DiscordAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail={"discord_status": exc.status_code, "discord_error": exc.detail},
        ) from exc

    chunks = split_for_discord(inp.body, max_len=state.settings.discord_max_message_len)
    last_seq: Optional[int] = None
    last_msg_id: Optional[str] = None

    for chunk in chunks:
        try:
            resp = state.webhooks.execute(
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

        seq = state.db.post_insert(
            author_kind="agent",
            author_id=agent.agent_id,
            author_name=agent.name,
            body=chunk,
            created_at=utc_now_iso(),
            discord_message_id=msg_id,
            discord_channel_id=str(state.settings.discord_channel_id),
            source_channel_id=str(state.settings.discord_channel_id),
        )
        if seq is None and msg_id:
            seq = state.db.post_mark_as_agent_by_discord_message_id(
                discord_message_id=msg_id,
                discord_channel_id=str(state.settings.discord_channel_id),
                agent_id=agent.agent_id,
                agent_name=agent.name,
            )
        if seq is not None:
            last_seq = seq

    return PostOut(last_seq=last_seq, last_discord_message_id=last_msg_id)
