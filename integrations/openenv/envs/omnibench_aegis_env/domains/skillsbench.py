from __future__ import annotations

"""SkillsBench domain profile for the OmniBench/OpenEnv integration layer.

This module is a lightweight domain descriptor.  It does not call the real
SkillsBench leaderboard and it does not change CyberGym or MAizeBargAIn
contracts.  Its job is to let the OpenEnv/OmniBench layer recognize
SkillsBench as a first-class general-purpose domain instead of routing it
through unrelated legacy domains.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping


DOMAIN = "skillsbench"
CATEGORY = "general_purpose_agent"
TRACK = "skillsbench"
BENCHMARK = "SkillsBench"
SCENARIO_FAMILY = "general_purpose"
ADAPTER = "skillsbench"

ALIASES = (
    "skillsbench",
    "skillsbench-leaderboard",
    "skillsbench_leaderboard",
    "benchflow",
    "benchflow-ai",
    "benchflow_ai",
    "standard-v1",
    "with_skills",
    "general_purpose",
    "general-purpose",
    "general purpose",
    "general_purpose_agent",
    "general-purpose-agent",
    "multi_utility",
    "multi-utility",
)

TASK_CATEGORIES = (
    "software-engineering",
    "office-white-collar",
    "natural-science",
    "industrial-physical-systems",
    "media-content-production",
    "finance-economics",
    "mathematics-or-formal-reasoning",
    "cybersecurity",
)

ARTIFACT_FAMILIES = (
    "text",
    "markdown",
    "json",
    "python",
    "patch",
    "diff",
    "csv",
    "xlsx",
    "docx",
    "pptx",
    "pdf",
    "lean",
    "obj",
    "html",
    "zip",
    "audio",
    "video",
)

ARTIFACT_NATIVE_HINTS = (
    "fix-build",
    "dependency-audit",
    "software-dependency-audit",
    "court-form",
    "paper-anonymizer",
    "pptx",
    "presentation",
    "slide",
    "xlsx",
    "excel",
    "spreadsheet",
    "threejs",
    "obj",
    "video",
    "silence-remover",
    "audio",
    "audiobook",
    "lean4",
    "proof",
    "pdf",
    "docx",
    "patch",
    "diff",
)


@dataclass(frozen=True, slots=True)
class SkillsBenchDomainSpec:
    domain: str = DOMAIN
    category: str = CATEGORY
    track: str = TRACK
    benchmark: str = BENCHMARK
    scenario_family: str = SCENARIO_FAMILY
    adapter: str = ADAPTER
    aliases: tuple[str, ...] = ALIASES
    task_categories: tuple[str, ...] = TASK_CATEGORIES
    artifact_families: tuple[str, ...] = ARTIFACT_FAMILIES
    artifact_native_hints: tuple[str, ...] = ARTIFACT_NATIVE_HINTS
    output_contract: tuple[str, ...] = field(
        default_factory=lambda: (
            "Classify by task_id, category, tags, files, and requested output format.",
            "For file-oriented tasks, produce evaluator-visible artifacts instead of prose-only answers.",
            "Prefer minimal valid deliverables over verbose reports.",
            "Keep cybersecurity tasks defensive and benchmark-contained.",
            "Do not alter CyberGym's single PoC/poc artifact contract.",
            "Do not alter the stable MAizeBargAIn baseline unless explicitly requested.",
        )
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "category": self.category,
            "track": self.track,
            "benchmark": self.benchmark,
            "scenario_family": self.scenario_family,
            "adapter": self.adapter,
            "aliases": list(self.aliases),
            "task_categories": list(self.task_categories),
            "artifact_families": list(self.artifact_families),
            "artifact_native_hints": list(self.artifact_native_hints),
            "output_contract": list(self.output_contract),
        }


SPEC = SkillsBenchDomainSpec()


def get_domain_spec() -> dict[str, Any]:
    return SPEC.as_dict()


def normalize_alias(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw.replace("_", "-").replace(" ", "-")


def matches(payload: Mapping[str, Any] | str | None) -> bool:
    """Return True when payload looks like SkillsBench/general-purpose traffic."""

    if payload is None:
        return False

    if isinstance(payload, str):
        blob = payload.lower()
    else:
        pieces: list[str] = []
        for key in (
            "track",
            "track_hint",
            "benchmark",
            "task_set",
            "category",
            "task_id",
            "scenario_family",
            "adapter",
            "instructions",
            "output_format",
        ):
            value = payload.get(key)
            if value is not None:
                pieces.append(str(value))
        tags = payload.get("tags")
        if isinstance(tags, list):
            pieces.extend(str(tag) for tag in tags)
        elif tags:
            pieces.append(str(tags))
        blob = " ".join(pieces).lower()

    normalized = blob.replace("_", "-")
    return any(alias.replace("_", "-") in normalized for alias in ALIASES)


def requires_artifact(payload: Mapping[str, Any] | str | None) -> bool:
    """Infer whether a SkillsBench-like task needs file artifacts."""

    if payload is None:
        return False

    if isinstance(payload, str):
        blob = payload.lower()
    else:
        pieces = []
        for key in (
            "task_id",
            "category",
            "instructions",
            "instruction",
            "prompt",
            "output_format",
            "expected_output",
            "deliverable",
        ):
            value = payload.get(key)
            if value is not None:
                pieces.append(str(value))
        for key in ("tags", "files", "attachments", "input_files"):
            value = payload.get(key)
            if isinstance(value, list):
                pieces.extend(str(item) for item in value)
            elif value:
                pieces.append(str(value))
        blob = " ".join(pieces).lower()

    return any(hint in blob for hint in ARTIFACT_NATIVE_HINTS)


def expected_artifact_family(payload: Mapping[str, Any] | str | None) -> str:
    """Return a coarse artifact family for routing/planning."""

    if payload is None:
        return "text"

    if isinstance(payload, str):
        blob = payload.lower()
    else:
        blob = " ".join(str(v) for v in payload.values() if v is not None).lower()

    if "xlsx" in blob or "excel" in blob or "spreadsheet" in blob:
        return "xlsx"
    if "pptx" in blob or "presentation" in blob or "slide" in blob:
        return "pptx"
    if "docx" in blob or "offer-letter" in blob:
        return "docx"
    if "pdf" in blob or "anonymizer" in blob:
        return "pdf"
    if "lean4" in blob or "lean" in blob or "proof" in blob:
        return "lean"
    if "threejs" in blob or " obj" in blob:
        return "obj"
    if "audio" in blob or "audiobook" in blob or "mp3" in blob or "wav" in blob:
        return "audio"
    if "video" in blob:
        return "video"
    if "patch" in blob or "diff" in blob or "fix-build" in blob:
        return "patch"
    if "json" in blob:
        return "json"
    if "csv" in blob:
        return "csv"
    return "text"


__all__ = [
    "DOMAIN",
    "CATEGORY",
    "TRACK",
    "BENCHMARK",
    "SCENARIO_FAMILY",
    "ADAPTER",
    "ALIASES",
    "TASK_CATEGORIES",
    "ARTIFACT_FAMILIES",
    "ARTIFACT_NATIVE_HINTS",
    "SPEC",
    "SkillsBenchDomainSpec",
    "get_domain_spec",
    "matches",
    "requires_artifact",
    "expected_artifact_family",
]
