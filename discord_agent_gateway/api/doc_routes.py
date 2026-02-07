from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import __version__
from ..docs import build_admin_html, build_heartbeat_md, build_messaging_md, build_skill_md
from .deps import current_profile, get_gateway_state
from .state import GatewayState


router = APIRouter()


@router.get("/healthz")
def healthz(state: GatewayState = Depends(get_gateway_state)) -> Dict[str, Any]:
    ok = True
    try:
        state.db.setting_get("__healthcheck__")
    except Exception:
        ok = False

    if state.settings.healthz_verbose:
        return {
            "ok": ok,
            "version": __version__,
            "registration_mode": state.settings.registration_mode,
        }
    return {"ok": ok}


@router.get("/skill.md", response_class=PlainTextResponse)
def get_skill_md(
    profile=Depends(current_profile),
    state: GatewayState = Depends(get_gateway_state),
) -> str:
    return build_skill_md(
        state.settings,
        profile_name=profile.name,
        profile_mission=profile.mission,
    )


@router.get("/heartbeat.md", response_class=PlainTextResponse)
def get_heartbeat_md() -> str:
    return build_heartbeat_md()


@router.get("/messaging.md", response_class=PlainTextResponse)
def get_messaging_md() -> str:
    return build_messaging_md()


@router.get("/admin", response_class=HTMLResponse)
def admin_page() -> str:
    return build_admin_html()
