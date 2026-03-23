from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .validators import validate_agent_card_payload, validate_health_payload


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _join_url(base_url: str, suffix: str) -> str:
    return base_url.rstrip("/") + suffix


def fetch_json(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw)


def check_health(base_url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    url = _join_url(base_url, "/health")
    try:
        payload = fetch_json(url, timeout=timeout)
        errors = validate_health_payload(payload)
        return {
            "url": url,
            "ok": not errors,
            "status": payload.get("status"),
            "errors": errors,
            "payload": payload,
        }
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"url": url, "ok": False, "errors": [str(exc)], "payload": None}


def check_agent_card(
    base_url: str,
    *,
    card_url: str | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    url = card_url or _join_url(base_url, "/.well-known/agent-card.json")
    try:
        payload = fetch_json(url, timeout=timeout)
        errors = validate_agent_card_payload(payload)
        return {
            "url": url,
            "ok": not errors,
            "errors": errors,
            "payload": payload,
        }
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"url": url, "ok": False, "errors": [str(exc)], "payload": None}


def check_repo_artifacts(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root)
    required = [
        "README.md",
        "Dockerfile",
        "run.sh",
        "src/aegisforge/a2a_server.py",
    ]
    artifacts = {name: (root / name).exists() for name in required}
    return {
        "root": str(root.resolve()),
        "ok": all(artifacts.values()),
        "artifacts": artifacts,
        "missing": [name for name, exists in artifacts.items() if not exists],
    }


def run_submission_gate(
    *,
    base_url: str,
    card_url: str | None = None,
    repo_root: str | Path = ".",
    timeout: float = 8.0,
) -> dict[str, Any]:
    health = check_health(base_url, timeout=timeout)
    agent_card = check_agent_card(base_url, card_url=card_url, timeout=timeout)
    repo = check_repo_artifacts(repo_root)
    checks = {"health": health, "agent_card": agent_card, "repo": repo}
    ok = all(check["ok"] for check in checks.values())
    return {
        "base_url": base_url,
        "timestamp": _now(),
        "ok": ok,
        "checks": checks,
    }
