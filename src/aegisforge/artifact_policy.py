from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ARTIFACT_POLICY_VERSION = "artifact_policy_v0_2_skillsbench_filesystem_first_2026_06_03"


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

    SkillsBench note:
    SkillsBench scoring is filesystem-first in the observed BenchFlow/standard-v1
    runs.  A2A artifact_refs/FilePart metadata may be useful for diagnostics, but
    the verifier checks real files written in the task sandbox, such as
    /root/answer.json, /root/output/*, /app/workspace/*, /output/*, or
    /home/github/build/failed/* patch paths.  This policy therefore treats
    SkillsBench as filesystem-output-primary while keeping artifact refs as
    diagnostic/compatibility metadata only.  CyberGym remains separate and keeps
    its strict single PoC/poc contract.
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

        # SkillsBench / General-Purpose Agent aliases.
        "skillsbench": "skillsbench",
        "skillsbench-agentbeats": "skillsbench",
        "skillsbench_agentbeats": "skillsbench",
        "skillsbench-leaderboard": "skillsbench",
        "skillsbench_leaderboard": "skillsbench",
        "benchflow": "skillsbench",
        "benchflow-ai": "skillsbench",
        "benchflow_ai": "skillsbench",
        "benchflowai": "skillsbench",
        "standard-v1": "skillsbench",
        "standard_v1": "skillsbench",
        "standard v1": "skillsbench",
        "with-skills": "skillsbench",
        "with_skills": "skillsbench",
        "general-purpose": "skillsbench",
        "general_purpose": "skillsbench",
        "general purpose": "skillsbench",
        "general-purpose-agent": "skillsbench",
        "general_purpose_agent": "skillsbench",
        "general purpose agent": "skillsbench",
        "multi-utility": "skillsbench",
        "multi_utility": "skillsbench",
        "artifact-first": "skillsbench",
        "artifact_first": "skillsbench",
    }

    _SKILLSBENCH_TASK_KIND_HINTS: tuple[tuple[tuple[str, ...], str, list[str]], ...] = (
        (
            ("fix_build", "fix-build", "build_repair", "software_repair", "patch", "diff", "dependency_audit", "dependency-audit"),
            "patch_file",
            ["filesystem_output:patch_file", "file_extension:.patch_or_.diff", "mime_type:text/x-diff"],
        ),
        (
            ("xlsx", "excel", "spreadsheet", "pivot", "recover_data", "recover-data"),
            "spreadsheet_file",
            ["filesystem_output:spreadsheet_file", "file_extension:.xlsx_or_.csv", "mime_type:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
        ),
        (
            ("pptx", "presentation", "slides", "slide_deck", "reference_formatting", "reference-formatting"),
            "presentation_file",
            ["filesystem_output:presentation_file", "file_extension:.pptx", "mime_type:application/vnd.openxmlformats-officedocument.presentationml.presentation"],
        ),
        (
            ("docx", "document", "offer_letter", "offer-letter", "court_form", "court-form"),
            "document_file",
            ["filesystem_output:document_file", "file_extension:.docx_or_.md", "mime_type:application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        ),
        (
            ("pdf", "paper_anonymizer", "paper-anonymizer", "anonymizer", "redaction", "redact"),
            "pdf_file",
            ["filesystem_output:pdf_file", "file_extension:.pdf", "mime_type:application/pdf"],
        ),
        (
            ("lean4", "lean", "formal_reasoning", "formal-reasoning", "proof"),
            "lean_file",
            ["filesystem_output:lean_file", "file_extension:.lean", "mime_type:text/plain"],
        ),
        (
            ("threejs", "obj", "geometry", "3d", "mesh"),
            "geometry_file",
            ["filesystem_output:geometry_file", "file_extension:.obj", "mime_type:text/plain"],
        ),
        (
            ("audio", "audiobook", "mp3", "wav"),
            "audio_file",
            ["filesystem_output:audio_file", "file_extension:.wav_or_.mp3", "mime_type:audio/*"],
        ),
        (
            ("video", "silence_remover", "silence-remover", "mp4"),
            "video_file",
            ["filesystem_output:video_file", "file_extension:.mp4_or_processing_script", "mime_type:video/*"],
        ),
        (
            ("json", "dialogue_parser", "dialogue-parser", "citation_check", "citation-check"),
            "json_file",
            ["filesystem_output:json_file", "file_extension:.json", "mime_type:application/json"],
        ),
        (
            ("csv", "table"),
            "csv_file",
            ["filesystem_output:csv_file", "file_extension:.csv", "mime_type:text/csv"],
        ),
    )

    _SKILLSBENCH_NATIVE_TASK_TYPES = {
        "artifact_generation",
        "software_repair_artifact",
        "spreadsheet_artifact",
        "presentation_artifact",
        "document_artifact",
        "pdf_document_artifact",
        "formal_reasoning_artifact",
        "geometry_artifact",
        "audio_artifact",
        "video_artifact",
        "software_dependency_audit",
        "analysis_artifact",
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

        skillsbench_like = track == "skillsbench"

        # CyberGym is artifact-native even when the upstream request did not mark
        # artifact_required.  SkillsBench is also artifact-first: at minimum it
        # should emit an evaluator-visible deliverable/manifest instead of only
        # prose, because standard-v1 includes many file-deliverable tasks.
        required = (
            artifact_required
            or task_type == "artifact_generation"
            or track == "cybergym"
            or (skillsbench_like and self._skillsbench_requires_artifact(task_type, requested_format, scenario_family))
        )

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
                    "Do not add extra CyberGym artifacts; preserve the single PoC/poc contract.",
                ]
            )
        elif skillsbench_like:
            artifact_kind, extra_sections = self._skillsbench_artifact_kind(
                task_type=task_type,
                requested_format=requested_format,
                scenario_family=scenario_family,
            )
            strict_format = True
            required_sections = [
                "status_text:concise",
                "filesystem_output:primary",
                "artifact_refs:diagnostic_only",
                "a2a_file_part:optional_diagnostic",
                "known_output_roots:/root|/root/output|/app/workspace|/output|/home/github/build/failed",
                *extra_sections,
            ]
            notes.extend(
                [
                    "SkillsBench is filesystem-output-primary; file-deliverable tasks must not degrade into prose-only responses.",
                    "Write real evaluator-visible files inside the task sandbox; artifact_refs/FilePart are diagnostic compatibility metadata, not the scoring channel.",
                    "Prefer task-specific filesystem targets such as /root/answer.json, /root/output/*, /app/workspace/*, /output/*, or /home/github/build/failed/*.",
                    "Return concise status text plus diagnostics, but do not rely on markdown text or A2A artifact_refs as the only deliverable.",
                    "Infer deliverable kind from task_type, requested_format, scenario_family, task_id, category, tags, and files when available upstream.",
                    "Keep cybersecurity SkillsBench tasks defensive and benchmark-contained; do not reuse CyberGym PoC semantics unless track is explicitly cybergym.",
                    "Preserve CyberGym's single Artifact(name='PoC') / FilePart(name='poc') contract and MAizeBargAIn baseline.",
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
    def _skillsbench_requires_artifact(cls, task_type: str, requested_format: str, scenario_family: str) -> bool:
        if task_type in cls._SKILLSBENCH_NATIVE_TASK_TYPES:
            return True
        if requested_format:
            return True
        if scenario_family in {"general_purpose", "standard_v1", "artifact_first", "with_skills"}:
            return True
        # Conservative SkillsBench fallback: a lightweight filesystem deliverable
        # manifest is still better than prose-only local/offline validation.
        return True

    @classmethod
    def _skillsbench_artifact_kind(
        cls,
        *,
        task_type: str,
        requested_format: str,
        scenario_family: str,
    ) -> tuple[str, list[str]]:
        signal = " ".join(
            part
            for part in (
                task_type,
                requested_format,
                scenario_family,
            )
            if part
        )

        requested_to_kind = {
            "patch": "patch_file",
            "diff": "patch_file",
            "xlsx": "spreadsheet_file",
            "xls": "spreadsheet_file",
            "csv": "csv_file",
            "pptx": "presentation_file",
            "docx": "document_file",
            "pdf": "pdf_file",
            "lean": "lean_file",
            "lean4": "lean_file",
            "obj": "geometry_file",
            "json": "json_file",
            "yaml": "yaml_file",
            "toml": "toml_file",
            "html": "html_file",
            "zip": "zip_file",
            "mp3": "audio_file",
            "wav": "audio_file",
            "mp4": "video_file",
        }
        if requested_format in requested_to_kind:
            return requested_to_kind[requested_format], [
                f"requested_format:{requested_format}",
                f"file_extension:.{requested_format}",
            ]

        for hints, artifact_kind, sections in cls._SKILLSBENCH_TASK_KIND_HINTS:
            if any(hint in signal for hint in hints):
                return artifact_kind, list(sections)

        return "skillsbench_deliverable_bundle", [
            "filesystem_output:skillsbench_deliverable",
            "file_extension:.md_or_.json",
            "mime_type:text/markdown_or_application/json",
        ]

    @classmethod
    def _normalize_track(cls, value: Any) -> str:
        normalized = cls._normalize_token(value)
        return cls._TRACK_ALIASES.get(normalized, normalized)

    @staticmethod
    def _normalize_token(value: Any) -> str:
        return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def validate_artifact_policy_selftest() -> dict[str, Any]:
    """Validate the SkillsBench/CyberGym artifact-policy split."""

    policy = ArtifactPolicy()
    skillsbench = policy.decide(
        artifact_required=False,
        task_type="dialogue-parser",
        track="skillsbench",
        requested_format="json",
        scenario_family="standard-v1",
    )
    cybergym = policy.decide(
        artifact_required=False,
        task_type="poc",
        track="cybergym",
        requested_format="",
        scenario_family="",
    )

    errors: list[str] = []
    if not skillsbench.required:
        errors.append("SkillsBench decision should require filesystem deliverable")
    if "filesystem_output:primary" not in skillsbench.required_sections:
        errors.append("SkillsBench decision missing filesystem_output:primary")
    if "artifact_refs:diagnostic_only" not in skillsbench.required_sections:
        errors.append("SkillsBench decision missing artifact_refs:diagnostic_only")
    if any(section == "artifact_refs:non_empty_for_file_tasks" for section in skillsbench.required_sections):
        errors.append("SkillsBench decision still requires non-empty artifact_refs")
    if cybergym.artifact_kind != "poc_file":
        errors.append(f"CyberGym artifact kind changed unexpectedly: {cybergym.artifact_kind}")
    if "artifact_name:PoC" not in cybergym.required_sections or "file_name:poc" not in cybergym.required_sections:
        errors.append("CyberGym PoC/poc contract was not preserved")

    return {
        "ok": not errors,
        "errors": errors,
        "version": ARTIFACT_POLICY_VERSION,
        "skillsbench": skillsbench.as_dict(),
        "cybergym": cybergym.as_dict(),
    }


__all__ = [
    "ARTIFACT_POLICY_VERSION",
    "ArtifactPolicy",
    "ArtifactPolicyDecision",
    "validate_artifact_policy_selftest",
]
