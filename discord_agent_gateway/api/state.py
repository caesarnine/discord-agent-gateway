from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ..config import Settings
from ..db import Database
from ..rate_limit import SlidingWindowRateLimiter


class WebhookManagerProtocol(Protocol):
    def get_or_create(self) -> Any: ...

    def execute(
        self,
        *,
        content: str,
        username: Optional[str],
        avatar_url: Optional[str],
        wait: bool = True,
    ) -> dict[str, Any]: ...


class AttachmentProxyProtocol(Protocol):
    def resolve(self, attachment_id: str) -> Any: ...

    def iter_download(self, url: str): ...


@dataclass(frozen=True)
class GatewayState:
    settings: Settings
    db: Database
    webhooks: WebhookManagerProtocol
    attachments: AttachmentProxyProtocol
    register_rate_limiter: SlidingWindowRateLimiter
