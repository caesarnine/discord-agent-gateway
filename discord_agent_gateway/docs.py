from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from . import __version__
from .config import Settings
from .util import gateway_slug


_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


@lru_cache(maxsize=None)
def _load_template(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _render(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(key, value)
    return rendered


def build_skill_md(settings: Settings, *, profile_name: str, profile_mission: str) -> str:
    template = _load_template("skill.md")
    base_url = settings.gateway_base_url
    return _render(
        template,
        {
            "__VERSION__": __version__,
            "__BASE_URL__": base_url,
            "__SLUG__": gateway_slug(base_url),
            "__SPLIT_LIMIT__": str(settings.discord_max_message_len),
            "__REGISTRATION_MODE__": settings.registration_mode,
            "__PROFILE_NAME__": profile_name,
            "__PROFILE_MISSION__": profile_mission,
        },
    )


def build_heartbeat_md() -> str:
    return _load_template("heartbeat.md")


def build_messaging_md() -> str:
    return _load_template("messaging.md")


def build_admin_html() -> str:
    return _load_template("admin.html")
