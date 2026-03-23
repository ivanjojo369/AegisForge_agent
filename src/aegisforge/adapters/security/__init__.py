from __future__ import annotations
import re
from .adapter import SecurityAdapter
from .config import SecurityAdapterConfig

__all__ = ["SecurityAdapter", "SecurityAdapterConfig"]

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"BEGIN PRIVATE KEY"),
]

def security_sanitize_inbound(text: str) -> str:
    return text.replace("\x00", "").strip()

def security_format_outbound(text: str) -> str:
    redacted = text
    for pat in _SECRET_PATTERNS:
        redacted = pat.sub("[REDACTED]", redacted)

    if len(redacted) > 8000:
        redacted = redacted[:8000] + "\n\n[Truncated]"
    return redacted
