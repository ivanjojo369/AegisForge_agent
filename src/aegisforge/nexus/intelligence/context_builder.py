"""Build bounded, explicit context from host-provided *already authorized* text.

Does not open paths or read a repository; the Workspace policy is authoritative.
"""
from __future__ import annotations
from pathlib import PurePosixPath


def _safe_relative_name(name: str) -> bool:
    if type(name) is not str or not name or "\\" in name or "\x00" in name or ":" in name:
        return False
    p = PurePosixPath(name)
    return not p.is_absolute() and all(s not in {"", ".", ".."} for s in name.split("/"))


def build_context(files: dict[str, str], *, max_chars: int = 12_000,
                  max_files: int = 20) -> dict[str, object]:
    if (not isinstance(files, dict) or type(max_chars) is not int or
            type(max_files) is not int or max_chars < 0 or max_files < 0):
        raise ValueError("invalid context request")
    if len(files) > 10_000:
        raise ValueError("too many context candidates")
    remaining = max_chars
    selected: list[dict[str, str]] = []
    omitted: list[str] = []
    for name in sorted(files):
        content = files[name]
        if not _safe_relative_name(name) or type(content) is not str:
            raise ValueError("unsafe context entry")
        if len(selected) >= max_files or len(content) > remaining:
            omitted.append(name)
            continue
        selected.append({"path": name, "content": content})
        remaining -= len(content)
    return {"schema": "aegisforge.nexus.intelligence.context.v1",
            "files": selected, "omitted": omitted,
            "truncated": bool(omitted), "authorization_inferred": False}


__all__ = ["build_context"]
