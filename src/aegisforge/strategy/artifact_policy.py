from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ArtifactPolicyDecision:
    required: bool
    artifact_kind: str
    strict_format: bool
    required_sections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "artifact_kind": self.artifact_kind,
            "strict_format": self.strict_format,
            "required_sections": list(self.required_sections),
            "notes": list(self.notes),
        }


class ArtifactPolicy:
    """Infer artifact expectations from task metadata, mode, and track family."""

    def decide(
        self,
        *,
        artifact_required: bool,
        task_type: str,
        track: str,
        requested_format: str | None = None,
        assessment_mode: str = "defender",
        scenario_family: str = "general",
        artifact_mode: str | None = None,
        strict_mode: bool = False,
        normal_user: bool = False,
        max_turns: int = 1,
        expected_risk: str | None = None,
    ) -> ArtifactPolicyDecision:
        normalized_task_type = (task_type or "reasoning").strip().lower()
        normalized_track = self._normalize_track(track)
        normalized_format = (requested_format or "").strip().lower()
        normalized_mode = self._normalize_mode(assessment_mode)
        normalized_family = self._normalize_family(scenario_family)
        normalized_risk = self._normalize_risk(expected_risk)
        safe_max_turns = max(1, self._safe_int(max_turns, default=1))

        required = bool(artifact_required or normalized_task_type == "artifact_generation")
        notes: list[str] = []

        if normalized_track == "security" and normalized_mode == "attacker":
            required = True
            notes.append("Security attacker runs should emit an explicit attack-oriented artifact.")
        elif normalized_track == "security" and normalized_mode == "defender" and artifact_required:
            notes.append("Security defender run explicitly requests an artifact.")

        if not required:
            return ArtifactPolicyDecision(
                required=False,
                artifact_kind="none",
                strict_format=False,
                required_sections=[],
                notes=["No artifact policy required for this task."],
            )

        strict_format = False
        artifact_kind = self._normalize_artifact_kind(
            artifact_mode,
            fallback=self._default_artifact_kind(
                track=normalized_track,
                mode=normalized_mode,
            ),
        )

        if normalized_format in {"json", "yaml", "toml"}:
            strict_format = True
            artifact_kind = normalized_format
            notes.append("Explicit structured format requested.")
        elif strict_mode:
            strict_format = True
            notes.append("Strict mode active; artifact formatting should be treated as hard constraint.")

        required_sections = self._sections_for(
            track=normalized_track,
            mode=normalized_mode,
            family=normalized_family,
            artifact_kind=artifact_kind,
            normal_user=normal_user,
        )

        if normalized_track == "security":
            notes.append(f"Security artifact posture selected for mode={normalized_mode}.")
            notes.append(f"Scenario family considered: {normalized_family}.")
            if normalized_risk:
                notes.append(f"Expected risk considered: {normalized_risk}.")
            if safe_max_turns > 1:
                notes.append(f"Artifact should remain stable across repeated passes (max_turns={safe_max_turns}).")
            if normal_user and normalized_mode == "defender":
                notes.append("Artifact should preserve normal-user utility while maintaining security boundaries.")

        elif normalized_track == "tau2":
            artifact_kind = "action_payload" if artifact_kind in {"none", "structured_response"} else artifact_kind
            strict_format = True
            required_sections = ["goal", "actions", "status"]
            notes.append("tau2 track favors strict action payloads.")

        elif normalized_track == "mcu":
            notes.append(f"MCU artifact posture selected for mode={normalized_mode}.")

        else:
            if artifact_kind == "none":
                artifact_kind = "structured_response"
            notes.append("Default structured response artifact selected.")

        if normalized_risk == "critical":
            strict_format = True
            notes.append("Critical-risk task detected; artifact format tightened.")
            if normalized_track == "security" and normalized_mode == "defender":
                required_sections = self._merge_sections(
                    required_sections,
                    ["risk", "attack_signals", "final"],
                )

        return ArtifactPolicyDecision(
            required=True,
            artifact_kind=artifact_kind,
            strict_format=strict_format,
            required_sections=self._dedupe(required_sections),
            notes=self._dedupe(notes),
        )

    def _sections_for(
        self,
        *,
        track: str,
        mode: str,
        family: str,
        artifact_kind: str,
        normal_user: bool,
    ) -> list[str]:
        if track == "tau2":
            return ["goal", "actions", "status"]

        if track == "mcu":
            if mode == "attacker":
                return ["assessment", "target_dependency", "payload", "expected_deviation", "final"]
            return ["assessment", "risk", "knowledge_assessment", "source_handling", "final"]

        if track != "security":
            if artifact_kind in {"json", "yaml", "toml"}:
                return ["summary", "evidence", "final"]
            return ["summary", "evidence", "final"]

        base_attacker = ["assessment", "target_dependency", "payload", "expected_deviation", "final"]
        base_defender = ["assessment", "risk", "attack_signals", "safe_handling", "allowed_response", "final"]

        if mode == "attacker":
            sections = list(base_attacker)
            if family in {"prompt_injection", "indirect_injection", "jailbreak"}:
                sections.insert(3, "fallback_vector")
            elif family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
                sections.insert(3, "exposure_goal")
            elif family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
                sections.insert(3, "output_shape")
            elif family in {"supply_chain", "dependency_attack"}:
                sections.insert(3, "trust_surface")
            return self._dedupe(sections)

        sections = list(base_defender)
        if family in {"prompt_injection", "indirect_injection", "jailbreak"}:
            sections.insert(4, "instruction_isolation")
        elif family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
            sections.insert(4, "exposure_check")
        elif family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
            sections.insert(4, "sanitization")
        elif family in {"supply_chain", "dependency_attack"}:
            sections.insert(4, "dependency_trust_check")

        if normal_user:
            sections = self._merge_sections(sections, ["allowed_response", "final"])

        return self._dedupe(sections)

    @staticmethod
    def _default_artifact_kind(*, track: str, mode: str) -> str:
        if track == "security":
            return "attack_plan" if mode == "attacker" else "guarded_response"
        if track == "tau2":
            return "action_payload"
        if track == "mcu":
            return "attack_plan" if mode == "attacker" else "guarded_response"
        return "structured_response"

    @staticmethod
    def _normalize_artifact_kind(value: str | None, *, fallback: str) -> str:
        text = (value or "").strip().lower()
        if not text:
            return fallback
        return text

    @staticmethod
    def _normalize_track(value: str | None) -> str:
        raw = (value or "openenv").strip().lower()
        aliases = {
            "security_arena": "security",
            "agent_safety": "security",
            "cybersecurity": "security",
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _normalize_mode(value: str | None) -> str:
        raw = (value or "").strip().lower()
        aliases = {
            "attack": "attacker",
            "offense": "attacker",
            "offensive": "attacker",
            "red": "attacker",
            "defense": "defender",
            "defensive": "defender",
            "blue": "defender",
            "guardian": "defender",
            "safe": "defender",
        }
        normalized = aliases.get(raw, raw)
        if normalized in {"attacker", "defender"}:
            return normalized
        raise ValueError(f"Unsupported assessment_mode: {value!r}")

    @staticmethod
    def _normalize_family(value: str | None) -> str:
        raw = (value or "general").strip().lower()
        aliases = {
            "prompt_injection_and_jailbreaking": "prompt_injection",
            "jailbreaking": "jailbreak",
            "pii": "pii_leakage",
            "prompt_leakage": "prompt_extraction",
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _normalize_risk(value: str | None) -> str | None:
        if value is None:
            return None
        raw = value.strip().lower()
        return raw if raw in {"low", "medium", "high", "critical"} else None

    @staticmethod
    def _safe_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in items:
            key = item.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(key)
        return ordered

    @staticmethod
    def _merge_sections(existing: list[str], additional: list[str]) -> list[str]:
        merged = list(existing)
        for item in additional:
            if item not in merged:
                merged.append(item)
        return merged
    