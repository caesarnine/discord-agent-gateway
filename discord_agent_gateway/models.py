from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Agent:
    agent_id: str
    name: str
    avatar_url: Optional[str]


@dataclass(frozen=True)
class AgentAdmin:
    agent_id: str
    name: str
    avatar_url: Optional[str]
    created_at: str
    revoked_at: Optional[str]


@dataclass(frozen=True)
class AgentCredentials:
    agent_id: str
    token: str


@dataclass(frozen=True)
class Post:
    seq: int
    post_id: str
    author_kind: str
    author_id: str
    author_name: Optional[str]
    body: str
    created_at: str
    discord_message_id: Optional[str]
    source_channel_id: str


@dataclass(frozen=True)
class Attachment:
    attachment_id: str
    post_seq: int
    discord_message_id: str
    source_channel_id: str
    filename: str
    url: Optional[str]
    proxy_url: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    height: Optional[int]
    width: Optional[int]


@dataclass(frozen=True)
class Invite:
    invite_id: str
    label: Optional[str]
    max_uses: int
    used_count: int
    created_at: str
    expires_at: Optional[str]
    revoked_at: Optional[str]


@dataclass(frozen=True)
class InviteCreateResult:
    invite: Invite
    code: str


@dataclass(frozen=True)
class ChannelProfile:
    name: str
    mission: str
    updated_at: Optional[str]
