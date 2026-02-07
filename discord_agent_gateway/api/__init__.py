from __future__ import annotations

from fastapi import FastAPI

from .. import __version__
from ..attachments import AttachmentProxy
from ..config import Settings
from ..db import Database
from ..rate_limit import SlidingWindowRateLimiter
from ..webhook import GatewayWebhookManager
from .admin_routes import router as admin_router
from .agent_routes import router as agent_router
from .doc_routes import router as doc_router
from .state import GatewayState


def create_app(
    *,
    settings: Settings,
    db: Database,
    webhooks: GatewayWebhookManager,
    attachments: AttachmentProxy,
) -> FastAPI:
    app = FastAPI(title="Discord Agent Gateway", version=__version__)
    app.state.gateway = GatewayState(
        settings=settings,
        db=db,
        webhooks=webhooks,
        attachments=attachments,
        register_rate_limiter=SlidingWindowRateLimiter(
            max_events=settings.register_rate_limit_count,
            window_seconds=settings.register_rate_limit_window_seconds,
        ),
    )

    app.include_router(doc_router)
    app.include_router(agent_router)
    app.include_router(admin_router)
    return app
