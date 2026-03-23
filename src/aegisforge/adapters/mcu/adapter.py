from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .payload_mapper import normalize_mcu_payload


@dataclass(slots=True)
class MCUPayload:
    task_id: str
    goal: str
    prompt: str
    skill: str = "general"
    domain: str = "minecraft"
    expected_action: dict[str, Any] = field(default_factory=dict)
    knowledge_source: dict[str, Any] = field(default_factory=dict)
    knowledge_artifact: dict[str, Any] = field(default_factory=dict)
    assessment_mode: str = "defender"
    scenario_family: str = "wikiwiper"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MCUDecision:
    should_use_source: bool
    source_confidence: str
    source_risk: str
    rationale: str
    recommended_mode: str
    evidence: list[str] = field(default_factory=list)


class MCUAdapter:
    """Minimal adapter for Minecraft Benchmark / MCU-AgentBeats tasks.

    This adapter does not execute simulator actions by itself. It prepares a
    stable, policy-friendly interpretation of a Minecraft task so the main
    runtime can decide how to act.
    """

    name = "mcu"

    def parse_payload(self, payload: dict[str, Any] | None) -> MCUPayload:
        normalized = normalize_mcu_payload(payload)
        task = normalized["task"]
        return MCUPayload(
            task_id=task["id"],
            goal=task["goal"],
            prompt=task["prompt"],
            skill=task["skill"],
            domain=task["domain"],
            expected_action=dict(task.get("expected_action") or {}),
            knowledge_source=dict(normalized.get("knowledge_source") or {}),
            knowledge_artifact=dict(normalized.get("knowledge_artifact") or {}),
            assessment_mode=str(normalized.get("assessment_mode") or "defender"),
            scenario_family=str(normalized.get("scenario_family") or "wikiwiper"),
            metadata=dict(normalized.get("metadata") or {}),
        )

    def assess_knowledge_source(self, payload: MCUPayload) -> MCUDecision:
        source = payload.knowledge_source
        artifact = payload.knowledge_artifact
        poisoned = bool(source.get("poisoned", False))
        text = " ".join(str(v) for v in artifact.values())
        lower = text.lower()

        priority_abuse_markers = [
            "system note",
            "ignore prior knowledge",
            "prefer the wiki",
            "override",
        ]
        recipe_tamper_markers = [
            "requires 1 stick",
            "extra ingredient",
            "hidden note",
        ]

        evidence: list[str] = []
        risk = "low"
        should_use = True
        confidence = "normal"
        rationale = "No immediate poisoning indicators detected."

        if poisoned:
            risk = "high"
            confidence = "low"
            should_use = False
            rationale = "Knowledge source is explicitly marked as poisoned in scenario metadata."
            evidence.append("metadata: knowledge_source.poisoned=true")

        for marker in priority_abuse_markers:
            if marker in lower:
                risk = "high"
                should_use = False
                confidence = "low"
                evidence.append(f"marker:{marker}")
                rationale = "Source contains instruction-hierarchy abuse markers."

        for marker in recipe_tamper_markers:
            if marker in lower:
                risk = "high"
                should_use = False
                confidence = "low"
                evidence.append(f"marker:{marker}")
                rationale = "Source appears to contain tampered recipe guidance."

        recommended_mode = payload.assessment_mode
        return MCUDecision(
            should_use_source=should_use,
            source_confidence=confidence,
            source_risk=risk,
            rationale=rationale,
            recommended_mode=recommended_mode,
            evidence=evidence,
        )

    def build_runtime_context(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        parsed = self.parse_payload(payload)
        decision = self.assess_knowledge_source(parsed)
        return {
            "track_hint": "mcu",
            "scenario_family": parsed.scenario_family,
            "assessment_mode": parsed.assessment_mode,
            "task_id": parsed.task_id,
            "goal": parsed.goal,
            "prompt": parsed.prompt,
            "skill": parsed.skill,
            "knowledge_source": parsed.knowledge_source,
            "knowledge_artifact": parsed.knowledge_artifact,
            "knowledge_decision": {
                "should_use_source": decision.should_use_source,
                "source_confidence": decision.source_confidence,
                "source_risk": decision.source_risk,
                "rationale": decision.rationale,
                "evidence": list(decision.evidence),
            },
            "expected_action": parsed.expected_action,
            "metadata": parsed.metadata,
        }
