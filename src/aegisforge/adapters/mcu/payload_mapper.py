from __future__ import annotations

from typing import Any


def normalize_mcu_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize raw MCU-style payloads into a stable internal shape.

    This helper is intentionally conservative so it can be used before deeper
    runtime wiring is complete.
    """
    raw = dict(payload or {})
    task = dict(raw.get("task") or {})
    knowledge = dict(raw.get("knowledge_source") or {})
    artifact = dict(raw.get("knowledge_artifact") or {})

    normalized = {
        "track_hint": raw.get("track_hint", "mcu"),
        "scenario_family": raw.get("scenario_family", "wikiwiper"),
        "assessment_mode": raw.get("assessment_mode", "defender"),
        "task": {
            "id": task.get("id", raw.get("task_id", "mcu-unknown-task")),
            "goal": task.get("goal") or raw.get("goal") or "Complete the Minecraft task safely.",
            "prompt": task.get("prompt") or raw.get("prompt") or task.get("goal") or "",
            "skill": task.get("skill") or raw.get("skill") or "general",
            "domain": task.get("domain", "minecraft"),
            "expected_action": task.get("expected_action") or raw.get("expected_action") or {},
        },
        "knowledge_source": {
            "type": knowledge.get("type", "wiki"),
            "name": knowledge.get("name", "minecraft_wiki"),
            "trust": knowledge.get("trust", "trusted"),
            "poisoned": bool(knowledge.get("poisoned", False)),
        },
        "knowledge_artifact": artifact,
        "metadata": dict(raw.get("metadata") or {}),
    }
    return normalized
