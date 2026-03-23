from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def require_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def require_int_in_range(
    value: Any,
    field_name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer.")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be <= {maximum}.")
    return value


def validate_http_url(value: str, field_name: str = "url") -> str:
    cleaned = require_non_empty_string(value, field_name)
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid http/https URL.")
    return cleaned.rstrip("/")


def validate_path_exists(path: str | Path, field_name: str = "path") -> Path:
    file_path = Path(path)
    if not file_path.exists():
        raise ValueError(f"{field_name} does not exist: {file_path}")
    return file_path


def validate_dict(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object / dict.")
    return value


def validate_list_of_strings(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list.")
    cleaned: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name}[{index}] must be a non-empty string.")
        cleaned.append(item.strip())
    return cleaned


def validate_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean.")
    return value
