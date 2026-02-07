from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentRegisterIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    avatar_url: Optional[str] = Field(None, max_length=500)
    invite_code: Optional[str] = Field(None, min_length=8, max_length=256)


class AgentRegisterOut(BaseModel):
    agent_id: str
    token: str
    name: str
    avatar_url: Optional[str]
    gateway_base_url: str
    credential_path: str


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
