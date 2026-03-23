from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _expect_mapping(payload: Any, label: str) -> list[str]:
    if isinstance(payload, Mapping):
        return []
    return [f"{label} must be an object"]


def validate_health_payload(payload: Any) -> list[str]:
    errors = _expect_mapping(payload, "health payload")
    if errors:
        return errors
    data = dict(payload)
    if data.get("status") != "ok":
        errors.append("health payload must include status='ok'")
    return errors


def validate_agent_card_payload(payload: Any) -> list[str]:
    errors = _expect_mapping(payload, "agent card")
    if errors:
        return errors
    data = dict(payload)
    required = ["name", "description"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        errors.append(f"agent card missing required fields: {', '.join(missing)}")

    if not (data.get("url") or data.get("endpoint") or data.get("base_url")):
        errors.append("agent card should declare url, endpoint, or base_url")
    return errors


def validate_endpoint_result(payload: Any) -> list[str]:
    errors = _expect_mapping(payload, "endpoint result")
    if errors:
        return errors
    data = dict(payload)
    for field in ("base_url", "timestamp", "checks"):
        if field not in data:
            errors.append(f"endpoint result missing field: {field}")
    if "checks" in data and not isinstance(data["checks"], Mapping):
        errors.append("endpoint result checks must be an object")
    return errors


def validate_submission_report(payload: Any) -> list[str]:
    errors = _expect_mapping(payload, "submission report")
    if errors:
        return errors
    data = dict(payload)
    for field in ("repo", "endpoints", "summary"):
        if field not in data:
            errors.append(f"submission report missing field: {field}")
    return errors
