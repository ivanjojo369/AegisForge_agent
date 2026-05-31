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
    """Infer artifact expectations from task metadata, track, and mode.

    CyberGym note:
    CyberGym is not a report-style security task. It expects a PoC/input artifact
    that can be materialized as /tmp/poc by the benchmark harness. The executor
    normally owns that contract, but this policy keeps fallback/routed paths from
    degrading into prose when a CyberGym task reaches the general agent layer.
    """

    _STRUCTURED_FORMATS = {"json", "yaml", "toml"}

    _TRACK_ALIASES = {
        "open-env": "openenv",
        "open_env": "openenv",
        "mcu-minecraft": "mcu",
        "mcu_minecraft": "mcu",
        "minecraft": "mcu",
        "minecraft benchmark": "mcu",
        "minecraft-benchmark": "mcu",
        "mcu-agentbeats": "mcu",
        "mcu_agentbeats": "mcu",
        "tau²": "tau2",
        "tau-2": "tau2",
        "tau_2": "tau2",
        "pi-bench": "pibench",
        "pi_bench": "pibench",
        "agent-safety": "pibench",
        "agent_safety": "pibench",
        "cyber-gym": "cybergym",
        "cyber_gym": "cybergym",
        "cybergym-green": "cybergym",
        "cybergym_green": "cybergym",
        "cybersecurity-agent": "cybergym",
        "cybersecurity_agent": "cybergym",
        "cybersecurity": "cybergym",
        "staticshipscam": "cybergym",
        "gymjailbreak": "cybergym",
        "net-arena": "netarena",
        "net_arena": "netarena",
    }

    def decide(
        self,
        *,
        artifact_required: bool,
        task_type: str,
        track: str,
        requested_format: str | None = None,
        assessment_mode: str = "defender",
        scenario_family: str | None = None,
    ) -> ArtifactPolicyDecision:
        task_type = self._normalize_token(task_type or "reasoning")
        track = self._normalize_track(track or "openenv")
        requested_format = self._normalize_token(requested_format or "")
        assessment_mode = self._normalize_token(assessment_mode or "defender")
        scenario_family = self._normalize_token(scenario_family or "")

        # CyberGym is artifact-native even when the upstream request did not mark
        # artifact_required. Other tracks keep the previous explicit requirement.
        required = artifact_required or task_type == "artifact_generation" or track == "cybergym"

        artifact_kind = "none"
        strict_format = False
        required_sections: list[str] = []
        notes: list[str] = []

        if not required:
            return ArtifactPolicyDecision(
                required=False,
                artifact_kind="none",
                strict_format=False,
                notes=["No artifact policy required for this task."],
            )

        if track == "cybergym":
            artifact_kind = "poc_file"
            strict_format = True
            required_sections = [
                "artifact_name:PoC",
                "file_name:poc",
                "file_bytes:base64",
                "mime_type:application/octet-stream",
            ]
            notes.extend(
                [
                    "CyberGym expects a proof-of-concept input artifact, not a prose report.",
                    "The benchmark harness materializes the submitted file as /tmp/poc.",
                    "Primary output should be deterministic PoC bytes; explanatory text must not replace the artifact.",
                    "Executor-level CyberGym handling should own this contract when attachments are present.",
                ]
            )
        elif requested_format in self._STRUCTURED_FORMATS:
            artifact_kind = requested_format
            strict_format = True
            notes.append("Explicit structured format requested.")
        elif track == "mcu":
            if assessment_mode == "attacker":
                artifact_kind = "attack_plan"
                strict_format = True
                required_sections = ["target_dependency", "payload", "expected_deviation"]
                notes.append("MCU attacker mode favors compact attack-plan artifacts.")
            else:
                artifact_kind = "guarded_response"
                required_sections = ["knowledge_assessment", "action_plan", "confidence"]
                notes.append("MCU defender mode favors guarded-response artifacts.")

            if scenario_family == "wikiwiper":
                required_sections.append("source_handling")
                notes.append("WikiWiper adds explicit source-handling requirements.")
        elif track == "security":
            artifact_kind = "report"
            required_sections = ["assessment", "risk", "recommended_action"]
            notes.append("Security track defaults to report-like artifact posture.")
        elif track == "tau2":
            artifact_kind = "action_payload"
            strict_format = True
            required_sections = ["goal", "actions", "status"]
            notes.append("tau2 track favors strict action payloads.")
        else:
            artifact_kind = "structured_response"
            required_sections = ["summary", "evidence", "final"]
            notes.append("OpenEnv track defaults to structured response artifacts.")

        return ArtifactPolicyDecision(
            required=True,
            artifact_kind=artifact_kind,
            strict_format=strict_format,
            required_sections=required_sections,
            notes=notes,
        )

    @classmethod
    def _normalize_track(cls, value: Any) -> str:
        normalized = cls._normalize_token(value)
        return cls._TRACK_ALIASES.get(normalized, normalized)

    @staticmethod
    def _normalize_token(value: Any) -> str:
        return str(value or "").strip().lower()
