import hashlib
from datetime import datetime, timezone
from typing import List


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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

