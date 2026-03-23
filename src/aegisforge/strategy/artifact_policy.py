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
    """Infer artifact expectations from task metadata and task type."""

    def decide(
        self,
        *,
        artifact_required: bool,
        task_type: str,
        track: str,
        requested_format: str | None = None,
    ) -> ArtifactPolicyDecision:
        task_type = (task_type or "reasoning").lower()
        track = (track or "openenv").lower()
        requested_format = (requested_format or "").lower().strip()

        required = artifact_required or task_type == "artifact_generation"
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

        if requested_format in {"json", "yaml", "toml"}:
            artifact_kind = requested_format
            strict_format = True
            notes.append("Explicit structured format requested.")
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
