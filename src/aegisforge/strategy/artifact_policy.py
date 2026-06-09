from __future__ import annotations

"""Artifact policy decisions for AegisForge strategy.

This module decides whether an artifact/deliverable is required and what shape
that deliverable should take.  It intentionally stays small and deterministic so
planner/router code can call it without network access, filesystem writes, or
hidden benchmark assumptions.

v0.4 adds SkillsBench/BenchFlow filesystem-output-primary semantics:
- SkillsBench tasks require real sandbox files when a task filesystem is visible;
- A2A artifacts/artifact_refs are preserved as diagnostic/compatibility signals;
- concrete output families such as office_pptx, office_xlsx, code_solution,
  security_config, lean_solution, and pdf_document must win over generic output.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping


ARTIFACT_POLICY_VERSION = "artifact_policy_v0_4_skillsbench_filesystem_output_primary_2026_06_09"


SKILLSBENCH_OUTPUT_ROOTS = [
    "/root/answer.json",
    "/root/output",
    "/app/workspace",
    "/app/output",
    "/output",
    "/workspace",
    "/home/github/build/failed",
]

SKILLSBENCH_FAMILY_OUTPUTS: dict[str, list[str]] = {
    "json_output": ["answer.json", "solution.json", "validation_notes.md"],
    "csv_output": ["answer.csv", "analysis.csv", "summary.json"],
    "code_solution": ["solution.py", "patch_0.diff", "repair_manifest.json"],
    "office_xlsx": ["answer.xlsx", "analysis.csv", "workbook_result.json"],
    "office_pptx": ["answer.pptx", "presentation_result.json", "validation_notes.md"],
    "office_docx": ["answer.docx", "fields_or_edits.json", "validation_notes.md"],
    "pdf_document": ["answer.pdf", "fields_or_edits.json", "validation_notes.md"],
    "lean_solution": ["solution.lean", "proof_notes.md", "solution.json"],
    "security_config": ["security_report.json", "findings.csv", "security_config.yaml"],
    "media_output": ["media_manifest.json", "asset_notes.md", "output_plan.json"],
    "general_file_output": ["answer.json", "skillsbench_deliverable.md", "validation_notes.md"],
}


@dataclass(slots=True)
class ArtifactPolicyDecision:
    required: bool
    artifact_kind: str
    strict_format: bool
    required_sections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # v0.4 optional fields.  Defaults keep older callers compatible.
    output_channel: str = "a2a_artifact"
    artifact_refs_role: str = "primary_or_unspecified"
    filesystem_primary: bool = False
    solver_family: str = ""
    preferred_output_roots: list[str] = field(default_factory=list)
    preferred_outputs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "artifact_kind": self.artifact_kind,
            "strict_format": self.strict_format,
            "required_sections": list(self.required_sections),
            "notes": list(self.notes),
            "output_channel": self.output_channel,
            "artifact_refs_role": self.artifact_refs_role,
            "filesystem_primary": self.filesystem_primary,
            "solver_family": self.solver_family,
            "preferred_output_roots": list(self.preferred_output_roots),
            "preferred_outputs": list(self.preferred_outputs),
            "metadata": dict(self.metadata),
        }


class ArtifactPolicy:
    """Infer artifact expectations from task metadata, mode, and track family."""

    version = ARTIFACT_POLICY_VERSION

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
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactPolicyDecision:
        normalized_task_type = (task_type or "reasoning").strip().lower()
        normalized_track = self._normalize_track(track)
        normalized_format = (requested_format or "").strip().lower().replace("-", "_")
        normalized_mode = self._normalize_mode(assessment_mode)
        normalized_family = self._normalize_family(scenario_family)
        normalized_risk = self._normalize_risk(expected_risk)
        safe_max_turns = max(1, self._safe_int(max_turns, default=1))
        metadata = {str(k): v for k, v in dict(metadata or {}).items()}

        # SkillsBench is output-contract driven.  Even if the incoming request is
        # not explicitly marked artifact_generation, the evaluator expects files.
        if normalized_track == "skillsbench":
            return self._decide_skillsbench(
                artifact_required=artifact_required,
                task_type=normalized_task_type,
                requested_format=normalized_format,
                scenario_family=normalized_family,
                artifact_mode=artifact_mode,
                strict_mode=strict_mode,
                metadata=metadata,
            )

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
                output_channel="none",
                artifact_refs_role="not_applicable",
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
            output_channel="a2a_artifact",
            artifact_refs_role="primary_or_unspecified",
            filesystem_primary=False,
            solver_family="",
            preferred_output_roots=[],
            preferred_outputs=[],
            metadata={"policy_version": ARTIFACT_POLICY_VERSION},
        )

    def _decide_skillsbench(
        self,
        *,
        artifact_required: bool,
        task_type: str,
        requested_format: str,
        scenario_family: str,
        artifact_mode: str | None,
        strict_mode: bool,
        metadata: Mapping[str, Any],
    ) -> ArtifactPolicyDecision:
        family = self._infer_skillsbench_family(
            scenario_family=scenario_family,
            requested_format=requested_format,
            artifact_mode=artifact_mode,
            metadata=metadata,
        )

        artifact_kind = self._normalize_artifact_kind(artifact_mode, fallback="filesystem_output")
        if artifact_kind in {"structured_response", "artifact", "file", "none"}:
            artifact_kind = "filesystem_output"

        preferred_outputs = list(SKILLSBENCH_FAMILY_OUTPUTS.get(family, SKILLSBENCH_FAMILY_OUTPUTS["general_file_output"]))
        sections = [
            "task_identity",
            "output_contract",
            "filesystem_write",
            "artifact_refs_diagnostics",
            "verification",
            "final",
        ]
        notes = [
            "SkillsBench/BenchFlow standard-v1 uses filesystem-output-primary policy.",
            "Write required outputs into the visible task sandbox before terminal completion.",
            "Preserve A2A artifacts/artifact_refs as diagnostic and compatibility evidence, not as the assumed scoring channel.",
            f"Solver-aligned family selected: {family}.",
        ]

        if artifact_required:
            notes.append("Incoming request explicitly required an artifact.")
        if task_type == "artifact_generation":
            notes.append("Task type is artifact_generation.")
        if requested_format:
            notes.append(f"Requested format considered: {requested_format}.")
        if strict_mode:
            notes.append("Strict mode active; output contract should be treated as a hard constraint.")

        if family in {"office_pptx", "office_xlsx", "office_docx", "pdf_document"}:
            sections = self._merge_sections(sections, ["binary_file_validity", "format_preservation"])
            notes.append("Office/PDF family detected; emit a real minimal valid binary file when a full solver is unavailable.")
        elif family == "code_solution":
            sections = self._merge_sections(sections, ["patch_or_source", "build_repair_manifest"])
            notes.append("Code/build family detected; prefer solver output and valid patch/source files over generic text.")
        elif family == "lean_solution":
            sections = self._merge_sections(sections, ["formal_statement", "proof_file"])
            notes.append("Formal reasoning family detected; prefer .lean output.")
        elif family == "security_config":
            sections = self._merge_sections(sections, ["defensive_scope", "config_or_findings"])
            notes.append("Security config family detected; keep outputs defensive and evaluator-local.")

        return ArtifactPolicyDecision(
            required=True,
            artifact_kind=artifact_kind,
            strict_format=True,
            required_sections=self._dedupe(sections),
            notes=self._dedupe(notes),
            output_channel="filesystem_output_primary",
            artifact_refs_role="diagnostic_and_compatibility_signal",
            filesystem_primary=True,
            solver_family=family,
            preferred_output_roots=list(SKILLSBENCH_OUTPUT_ROOTS),
            preferred_outputs=preferred_outputs,
            metadata={
                "policy_version": ARTIFACT_POLICY_VERSION,
                "track": "skillsbench",
                "task_set": str(metadata.get("task_set") or "standard-v1"),
                "condition": str(metadata.get("condition") or "with_skills"),
                "scoring_channel": "filesystem_output_primary",
                "a2a_artifacts": "diagnostic_and_compatibility_signal",
            },
        )

    def _infer_skillsbench_family(
        self,
        *,
        scenario_family: str,
        requested_format: str,
        artifact_mode: str | None,
        metadata: Mapping[str, Any],
    ) -> str:
        candidates = [
            scenario_family,
            requested_format,
            artifact_mode or "",
            str(metadata.get("family") or ""),
            str(metadata.get("contract_family") or ""),
            str(metadata.get("output_family") or ""),
            str(metadata.get("environment_family_hint") or ""),
            str(metadata.get("task_id") or ""),
            str(metadata.get("canonical_task_id") or ""),
            str(metadata.get("name") or ""),
        ]
        blob = " ".join(candidates).lower().replace("_", "-")

        # Task-id and output-family hints first.
        if any(token in blob for token in ("exceltable-in-ppt", "pptx-reference-formatting", "office-pptx", "pptx", "powerpoint", "presentation", "slides")):
            return "office_pptx"
        if any(token in blob for token in ("office-xlsx", "xlsx", "xls", "excel", "spreadsheet")):
            return "office_xlsx"
        if any(token in blob for token in ("office-docx", "docx", "word-document", "offer-letter")):
            return "office_docx"
        if any(token in blob for token in ("pdf-document", "pdf-form", "pdf", "court-form", "paper-anonymizer")):
            return "pdf_document"
        if any(token in blob for token in ("lean-solution", "lean4", "lean", "formal-reasoning")):
            return "lean_solution"
        if any(token in blob for token in ("security-config", "security-audit", "cybersecurity", "firewall", "policy", "detection", "audit")):
            return "security_config"
        if any(token in blob for token in ("code-solution", "software-patch", "fix-build", "patch", "diff", "python", "solution-py")):
            return "code_solution"
        if any(token in blob for token in ("csv-output", "csv")):
            return "csv_output"
        if any(token in blob for token in ("json-output", "json", "data-json")):
            return "json_output"
        if any(token in blob for token in ("media-output", "media-processing", "video", "audio", "obj", "threejs")):
            return "media_output"

        return "general_file_output"

    def _sections_for(
        self,
        *,
        track: str,
        mode: str,
        family: str,
        artifact_kind: str,
        normal_user: bool,
    ) -> list[str]:
        if track == "skillsbench":
            return ["task_identity", "output_contract", "filesystem_write", "artifact_refs_diagnostics", "final"]

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
        if track == "skillsbench":
            return "filesystem_output"
        if track == "security":
            return "attack_plan" if mode == "attacker" else "guarded_response"
        if track == "tau2":
            return "action_payload"
        if track == "mcu":
            return "attack_plan" if mode == "attacker" else "guarded_response"
        return "structured_response"

    @staticmethod
    def _normalize_artifact_kind(value: str | None, *, fallback: str) -> str:
        text = (value or "").strip().lower().replace("-", "_")
        if not text:
            return fallback
        aliases = {
            "file_output": "filesystem_output",
            "sandbox_file_output": "filesystem_output",
            "filesystem_first": "filesystem_output",
            "filesystem_output_primary": "filesystem_output",
            "artifact_first": "filesystem_output",
        }
        return aliases.get(text, text)

    @staticmethod
    def _normalize_track(value: str | None) -> str:
        raw = (value or "openenv").strip().lower().replace("_", "-")
        aliases = {
            "security-arena": "security",
            "agent-safety": "security",
            "cybersecurity": "security",
            "pi-bench": "pibench",
            "pibench": "pibench",
            "tau-2": "tau2",
            "mcu-minecraft": "mcu",
            "skillsbench": "skillsbench",
            "skillsbench-agentbeats": "skillsbench",
            "skillsbench-leaderboard": "skillsbench",
            "benchflow": "skillsbench",
            "benchflow-ai": "skillsbench",
            "standard-v1": "skillsbench",
            "with-skills": "skillsbench",
            "general-purpose-agent": "skillsbench",
            "filesystem-first": "skillsbench",
            "filesystem-output-primary": "skillsbench",
            "sandbox-file-output": "skillsbench",
            "artifact-first": "skillsbench",
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
        raw = (value or "general").strip().lower().replace("-", "_")
        aliases = {
            "prompt_injection_and_jailbreaking": "prompt_injection",
            "jailbreaking": "jailbreak",
            "pii": "pii_leakage",
            "prompt_leakage": "prompt_extraction",
            "presentation": "office_pptx",
            "pptx": "office_pptx",
            "ppt": "office_pptx",
            "slides": "office_pptx",
            "slide_deck": "office_pptx",
            "spreadsheet": "office_xlsx",
            "excel": "office_xlsx",
            "xlsx": "office_xlsx",
            "docx": "office_docx",
            "document": "office_docx",
            "pdf": "pdf_document",
            "pdf_output": "pdf_document",
            "formal_reasoning": "lean_solution",
            "lean": "lean_solution",
            "software_patch": "code_solution",
            "bugswarm_build_repair": "code_solution",
            "security_audit": "security_config",
            "data_json": "json_output",
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


def validate_artifact_policy_selftest() -> dict[str, Any]:
    policy = ArtifactPolicy()
    errors: list[str] = []

    skillsbench = policy.decide(
        artifact_required=False,
        task_type="reasoning",
        track="skillsbench",
        scenario_family="exceltable-in-ppt",
        metadata={"task_set": "standard-v1", "condition": "with_skills"},
    )
    if not skillsbench.required:
        errors.append("SkillsBench should require filesystem output artifacts")
    if skillsbench.output_channel != "filesystem_output_primary":
        errors.append(f"unexpected SkillsBench output channel: {skillsbench.output_channel}")
    if skillsbench.solver_family != "office_pptx":
        errors.append(f"expected office_pptx, got {skillsbench.solver_family}")
    if "answer.pptx" not in skillsbench.preferred_outputs:
        errors.append("pptx preferred output missing")

    security = policy.decide(
        artifact_required=False,
        task_type="reasoning",
        track="security",
        assessment_mode="attacker",
        scenario_family="prompt_injection",
    )
    if not security.required or security.artifact_kind != "attack_plan":
        errors.append("security attacker compatibility changed unexpectedly")

    tau2 = policy.decide(
        artifact_required=True,
        task_type="reasoning",
        track="tau2",
        assessment_mode="defender",
    )
    if tau2.artifact_kind != "action_payload" or not tau2.strict_format:
        errors.append("tau2 compatibility changed unexpectedly")

    no_artifact = policy.decide(
        artifact_required=False,
        task_type="reasoning",
        track="openenv",
        assessment_mode="defender",
    )
    if no_artifact.required:
        errors.append("default non-artifact task should not require artifact")

    return {
        "ok": not errors,
        "errors": errors,
        "version": ARTIFACT_POLICY_VERSION,
        "skillsbench": skillsbench.as_dict(),
    }

__all__ = [
    "ARTIFACT_POLICY_VERSION",
    "ArtifactPolicyDecision",
    "ArtifactPolicy",
    "validate_artifact_policy_selftest",
]
