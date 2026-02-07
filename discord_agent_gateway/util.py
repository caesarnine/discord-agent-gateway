import hashlib
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlparse


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def gateway_slug(base_url: str) -> str:
    """Derive a filesystem-safe directory name from a gateway base URL.

    Examples:
        http://localhost:8000   -> localhost_8000
        https://gw.example.com  -> gw.example.com_443
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return f"{host}_{port}"


def credential_path(base_url: str, agent_id: str) -> str:
    """Suggested credential file path for a (gateway, agent) pair."""
    slug = gateway_slug(base_url)
    return f"~/.config/discord-agent-gateway/{slug}/{agent_id}.json"


def parse_iso_utc(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def split_for_discord(text: str, *, max_len: int) -> List[str]:
    """
    Split long messages into Discord-safe chunks.

    Prefer splitting on paragraph breaks/newlines/spaces.
    """
    text = (text or "").strip()
    if not text:
        return [""]

    parts: List[str] = []
    i, n = 0, len(text)

    while i < n:
        j = min(n, i + max_len)
        if j < n:
            window = text[i:j]
            cut = window.rfind("\n\n")
            if cut == -1:
                cut = window.rfind("\n")
            if cut == -1:
                cut = window.rfind(" ")
            if cut > 200:
                j = i + cut

        chunk = text[i:j].strip()
        if chunk:
            parts.append(chunk)
        i = j

    return parts if parts else [""]
