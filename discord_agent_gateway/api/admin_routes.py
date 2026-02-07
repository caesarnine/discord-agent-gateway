from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ..models import Invite
from ..util import credential_path, parse_iso_utc
from .deps import current_profile, get_gateway_state, require_admin
from .schemas import (
    AdminAgentOut,
    AdminAgentsOut,
    AdminCreateAgentIn,
    AdminInviteCreateIn,
    AdminInviteCreateOut,
    AdminInviteOut,
    AdminInvitesOut,
    AdminProfileIn,
    AdminRotateOut,
    AgentRegisterOut,
    ContextOut,
)
from .state import GatewayState


router = APIRouter()


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


@router.get("/v1/admin/config")
def admin_config(
    _: None = Depends(require_admin),
    profile=Depends(current_profile),
    state: GatewayState = Depends(get_gateway_state),
) -> Dict[str, Any]:
    return {
        "registration_mode": state.settings.registration_mode,
        "register_rate_limit_count": state.settings.register_rate_limit_count,
        "register_rate_limit_window_seconds": state.settings.register_rate_limit_window_seconds,
        "healthz_verbose": state.settings.healthz_verbose,
        "profile_name": profile.name,
        "profile_mission": profile.mission,
        "profile_updated_at": profile.updated_at,
    }


@router.get("/v1/admin/profile", response_model=ContextOut)
def admin_get_profile(_: None = Depends(require_admin), profile=Depends(current_profile)) -> ContextOut:
    return ContextOut(name=profile.name, mission=profile.mission, updated_at=profile.updated_at)


@router.put("/v1/admin/profile", response_model=ContextOut)
def admin_set_profile(
    inp: AdminProfileIn,
    _: None = Depends(require_admin),
    state: GatewayState = Depends(get_gateway_state),
) -> ContextOut:
    profile = state.db.channel_profile_set(name=inp.name, mission=inp.mission)
    return ContextOut(name=profile.name, mission=profile.mission, updated_at=profile.updated_at)


@router.post("/v1/admin/agents", response_model=AgentRegisterOut)
def admin_create_agent(
    inp: AdminCreateAgentIn,
    _: None = Depends(require_admin),
    state: GatewayState = Depends(get_gateway_state),
) -> AgentRegisterOut:
    creds = state.db.agent_create(inp.name, inp.avatar_url)
    return AgentRegisterOut(
        agent_id=creds.agent_id,
        token=creds.token,
        name=inp.name,
        avatar_url=inp.avatar_url,
        gateway_base_url=state.settings.gateway_base_url,
        credential_path=credential_path(state.settings.gateway_base_url, creds.agent_id),
    )


@router.get("/v1/admin/agents", response_model=AdminAgentsOut)
def admin_list_agents(
    _: None = Depends(require_admin),
    state: GatewayState = Depends(get_gateway_state),
) -> AdminAgentsOut:
    rows = state.db.agents_list()
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


@router.post("/v1/admin/agents/{agent_id}/revoke")
def admin_revoke_agent(
    agent_id: str,
    _: None = Depends(require_admin),
    state: GatewayState = Depends(get_gateway_state),
) -> Dict[str, Any]:
    if not state.db.agent_revoke(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found or already revoked.")
    return {"ok": True}


@router.post("/v1/admin/agents/{agent_id}/rotate-token", response_model=AdminRotateOut)
def admin_rotate_agent_token(
    agent_id: str,
    _: None = Depends(require_admin),
    state: GatewayState = Depends(get_gateway_state),
) -> AdminRotateOut:
    token = state.db.agent_rotate_token(agent_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Agent not found or revoked.")
    return AdminRotateOut(agent_id=agent_id, token=token)


@router.post("/v1/admin/invites", response_model=AdminInviteCreateOut)
def admin_create_invite(
    inp: AdminInviteCreateIn,
    _: None = Depends(require_admin),
    state: GatewayState = Depends(get_gateway_state),
) -> AdminInviteCreateOut:
    try:
        expires_at = parse_iso_utc(inp.expires_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid expires_at: {exc}") from exc
    result = state.db.invite_create(label=inp.label, max_uses=inp.max_uses, expires_at=expires_at)
    return AdminInviteCreateOut(invite=_invite_out(result.invite), code=result.code)


@router.get("/v1/admin/invites", response_model=AdminInvitesOut)
def admin_list_invites(
    _: None = Depends(require_admin),
    state: GatewayState = Depends(get_gateway_state),
) -> AdminInvitesOut:
    rows = state.db.invite_list()
    return AdminInvitesOut(invites=[_invite_out(row) for row in rows])


@router.post("/v1/admin/invites/{invite_id}/revoke")
def admin_revoke_invite(
    invite_id: str,
    _: None = Depends(require_admin),
    state: GatewayState = Depends(get_gateway_state),
) -> Dict[str, Any]:
    if not state.db.invite_revoke(invite_id):
        raise HTTPException(status_code=404, detail="Invite not found or already revoked.")
    return {"ok": True}
