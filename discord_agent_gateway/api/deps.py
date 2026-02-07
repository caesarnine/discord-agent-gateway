from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from ..models import Agent, ChannelProfile
from .state import GatewayState


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    if not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip()


def get_gateway_state(request: Request) -> GatewayState:
    return request.app.state.gateway


def require_agent(
    authorization: Optional[str] = Header(default=None),
    state: GatewayState = Depends(get_gateway_state),
) -> Agent:
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <token>")
    agent = state.db.agent_by_token(token)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid token")
    return agent


def require_admin(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    authorization: Optional[str] = Header(default=None),
    state: GatewayState = Depends(get_gateway_state),
) -> None:
    configured = state.settings.admin_api_token
    if not configured:
        raise HTTPException(status_code=503, detail="Admin API disabled")

    token = (x_admin_token or _extract_bearer_token(authorization) or "").strip()
    if not token or not secrets.compare_digest(token, configured):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def current_profile(state: GatewayState = Depends(get_gateway_state)) -> ChannelProfile:
    return state.db.channel_profile_get(
        default_name=state.settings.profile_name,
        default_mission=state.settings.profile_mission,
    )
