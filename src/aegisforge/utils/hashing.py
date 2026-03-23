from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_json_hash(payload: dict[str, Any] | list[Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return stable_text_hash(normalized)


def file_sha256(path: str | Path) -> str:
    path = Path(path)
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def short_hash(value: str, *, length: int = 12) -> str:
    return stable_text_hash(value)[:length]
