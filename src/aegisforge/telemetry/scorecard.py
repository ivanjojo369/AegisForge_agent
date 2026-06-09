from __future__ import annotations

"""Scorecards for AegisForge telemetry.

The original scorecard preserved task_id, track, status, three score hints, and
notes. Sprint 4 / AgentBeats reporting then added richer identity, timing, and
aggregation. v0.7 adds SkillsBench/BenchFlow filesystem-output-primary scoring
diagnostics while preserving the old builder call:

    ScorecardBuilder().build(summary, budget_stats=None)

Important SkillsBench rule:
- real files written into the task sandbox are the primary output/scoring signal;
- A2A artifacts/artifact_refs are diagnostic compatibility evidence, not the
  assumed scoring channel;
- zero official artifact_refs should not automatically punish a run when the
  workspace executor wrote evaluator-facing files.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Iterable, Mapping

from .budget_stats import BudgetStatsCollector
from .episode_summary import EpisodeSummary


JsonDict = dict[str, Any]

SCORECARD_VERSION = "scorecard_v0_7_skillsbench_filesystem_output_primary_2026_06_09"


IDENTITY_KEYS: tuple[str, ...] = (
    "task_id",
    "track",
    "domain",
    "scenario_id",
    "scenario_name",
    "upstream_track",
    "category",
    "adapter",
    "assessment_mode",
    "scenario_family",
    "benchmark",
    "selected_opponent",
    "source_url",
    "run_id",
    "trace_id",
)

SKILLSBENCH_KEYS: tuple[str, ...] = (
    "task_set",
    "condition",
    "output_family",
    "output_channel",
    "scoring_channel",
    "artifact_refs_role",
    "filesystem_primary",
    "workspace_visible",
    "wrote_any_file",
    "selected_solver_key",
    "score_eligible",
    "artifact_ref_count",
    "official_artifact_ref_count",
    "workspace_write_count",
    "workspace_ok_write_count",
)

SUCCESS_STATUSES: set[str] = {
    "completed",
    "complete",
    "passed",
    "pass",
    "success",
    "succeeded",
    "ok",
    "done",
}

PARTIAL_STATUSES: set[str] = {
    "running",
    "partial",
    "needs_review",
    "review",
    "unknown",
}

FAILURE_STATUSES: set[str] = {
    "failed",
    "fail",
    "error",
    "errored",
    "timeout",
    "timed_out",
    "cancelled",
    "canceled",
}


def _to_dict(value: Any) -> JsonDict:
    """Best-effort conversion of dataclasses/models/mappings to dictionaries."""

    if value is None:
        return {}
    if is_dataclass(value):
        return dict(asdict(value))
    if isinstance(value, Mapping):
        return dict(value)

    for method_name in ("as_dict", "compact_dict", "to_dict", "model_dump", "dict", "summary"):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            dumped = method()
        except Exception:
            continue
        if isinstance(dumped, Mapping):
            return dict(dumped)

    return {}


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _first_text(*values: Any, default: str = "") -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return default


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "ok", "pass", "passed", "success", "eligible"}:
        return True
    if text in {"0", "false", "no", "n", "fail", "failed", "error", "ineligible"}:
        return False
    return default


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, round(value, 3)))


def _mean(values: Iterable[float]) -> float:
    numbers = [float(value) for value in values]
    if not numbers:
        return 0.0
    return round(sum(numbers) / len(numbers), 3)


def _status_base_correctness(status: str) -> float:
    normalized = status.strip().lower()
    if normalized in SUCCESS_STATUSES:
        return 1.0
    if normalized in PARTIAL_STATUSES:
        return 0.6
    if normalized in FAILURE_STATUSES:
        return 0.25
    # Preserve the old behavior for unrecognized non-completed statuses.
    return 0.4


def _safe_budget_summary(budget_stats: BudgetStatsCollector | Mapping[str, Any] | None) -> JsonDict:
    if budget_stats is None:
        return {}
    if isinstance(budget_stats, Mapping):
        return dict(budget_stats)

    summary = getattr(budget_stats, "summary", None)
    if callable(summary):
        try:
            budget = summary()
        except Exception as exc:
            return {"budget_summary_error": str(exc)}
        return dict(budget) if isinstance(budget, Mapping) else {}

    return _to_dict(budget_stats)


def _identity_from_summary(summary: EpisodeSummary | Mapping[str, Any] | Any) -> JsonDict:
    data = _to_dict(summary)

    identity = _to_dict(data.get("identity"))
    output: JsonDict = {}
    for key in IDENTITY_KEYS:
        output[key] = _first_text(data.get(key), identity.get(key))

    output["track"] = _normalize_track(_first_text(output.get("track"), default="openenv"))
    output["task_id"] = _first_text(output.get("task_id"), default="unknown")
    return output


def _skillsbench_from_summary(summary: EpisodeSummary | Mapping[str, Any] | Any) -> JsonDict:
    data = _to_dict(summary)
    identity = _to_dict(data.get("identity"))
    metadata = _to_dict(data.get("metadata"))

    out: JsonDict = {}
    for key in SKILLSBENCH_KEYS:
        out[key] = data.get(key, identity.get(key, metadata.get(key)))

    workspace = _to_dict(metadata.get("workspace_execution"))
    diagnostics = _to_dict(workspace.get("diagnostics"))

    if not out.get("output_family"):
        out["output_family"] = _first_text(workspace.get("family"), diagnostics.get("family"), metadata.get("family"))
    if not out.get("scoring_channel"):
        out["scoring_channel"] = _first_text(
            metadata.get("scoring_channel"),
            metadata.get("output_channel"),
            workspace.get("scoring_channel"),
            default="filesystem_output_primary" if _looks_like_skillsbench(data, metadata) else "",
        )
    if not out.get("output_channel"):
        out["output_channel"] = out.get("scoring_channel") or metadata.get("output_channel") or ""
    if not out.get("artifact_refs_role"):
        out["artifact_refs_role"] = _first_text(
            metadata.get("artifact_refs_role"),
            workspace.get("artifact_refs_role"),
            default="diagnostic_and_compatibility_signal" if _looks_like_skillsbench(data, metadata) else "",
        )
    if out.get("workspace_visible") is None:
        out["workspace_visible"] = workspace.get("workspace_visible", diagnostics.get("workspace_visible"))
    if out.get("wrote_any_file") is None:
        out["wrote_any_file"] = workspace.get("wrote_any_file", diagnostics.get("wrote_any_file"))
    if out.get("selected_solver_key") is None:
        out["selected_solver_key"] = _first_text(
            workspace.get("selected_solver_key"),
            diagnostics.get("selected_solver_key"),
            metadata.get("selected_solver_key"),
        )
    if out.get("workspace_write_count") is None:
        out["workspace_write_count"] = _as_int(workspace.get("write_count", diagnostics.get("write_count")), default=0)
    if out.get("workspace_ok_write_count") is None:
        out["workspace_ok_write_count"] = _as_int(workspace.get("ok_writes", diagnostics.get("ok_writes")), default=0)
    if out.get("filesystem_primary") is None:
        out["filesystem_primary"] = bool(out.get("scoring_channel") == "filesystem_output_primary" or _looks_like_skillsbench(data, metadata))
    if out.get("score_eligible") is None:
        out["score_eligible"] = metadata.get("score_eligible")

    # Coerce common scalar fields.
    out["task_set"] = _first_text(out.get("task_set"), default="standard-v1" if _looks_like_skillsbench(data, metadata) else "")
    out["condition"] = _first_text(out.get("condition"), default="with_skills" if _looks_like_skillsbench(data, metadata) else "")
    out["output_family"] = _normalize_family(_first_text(out.get("output_family")))
    out["output_channel"] = _first_text(out.get("output_channel"))
    out["scoring_channel"] = _first_text(out.get("scoring_channel"))
    out["artifact_refs_role"] = _first_text(out.get("artifact_refs_role"))
    out["filesystem_primary"] = _as_bool(out.get("filesystem_primary"), default=False)
    out["workspace_visible"] = None if out.get("workspace_visible") is None else _as_bool(out.get("workspace_visible"), default=False)
    out["wrote_any_file"] = None if out.get("wrote_any_file") is None else _as_bool(out.get("wrote_any_file"), default=False)
    out["selected_solver_key"] = _first_text(out.get("selected_solver_key"))
    out["score_eligible"] = None if out.get("score_eligible") is None else _as_bool(out.get("score_eligible"), default=True)
    out["artifact_ref_count"] = _as_int(out.get("artifact_ref_count"), default=0)
    out["official_artifact_ref_count"] = _as_int(out.get("official_artifact_ref_count"), default=0)
    out["workspace_write_count"] = _as_int(out.get("workspace_write_count"), default=0)
    out["workspace_ok_write_count"] = _as_int(out.get("workspace_ok_write_count"), default=0)

    return out


def _looks_like_skillsbench(data: Mapping[str, Any], metadata: Mapping[str, Any] | None = None) -> bool:
    metadata = dict(metadata or {})
    blob = " ".join(
        str(value)
        for value in [
            data.get("track"),
            data.get("benchmark"),
            data.get("task_set"),
            data.get("condition"),
            data.get("scoring_channel"),
            data.get("output_channel"),
            metadata.get("track"),
            metadata.get("benchmark"),
            metadata.get("task_set"),
            metadata.get("condition"),
            metadata.get("skillsbench_artifact_bridge"),
            metadata.get("workspace_execution"),
        ]
        if value is not None
    ).lower().replace("_", "-")
    return any(
        marker in blob
        for marker in (
            "skillsbench",
            "benchflow",
            "standard-v1",
            "with-skills",
            "filesystem-output-primary",
            "artifact-refs-diagnostic",
        )
    )


def _normalize_track(value: Any) -> str:
    raw = _text(value, default="openenv").lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "skillsbench-agentbeats": "skillsbench",
        "skillsbench-leaderboard": "skillsbench",
        "benchflow": "skillsbench",
        "benchflow-ai": "skillsbench",
        "standard-v1": "skillsbench",
        "with-skills": "skillsbench",
        "general-purpose-agent": "skillsbench",
        "filesystem-output-primary": "skillsbench",
        "mcu-minecraft": "mcu",
        "pi-bench": "pibench",
        "agent-safety": "pibench",
        "net-arena": "netarena",
        "cybersecurity": "cybergym",
    }
    return aliases.get(raw, raw)


def _normalize_family(value: Any) -> str:
    raw = _text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "presentation": "office_pptx",
        "pptx": "office_pptx",
        "ppt": "office_pptx",
        "slides": "office_pptx",
        "slide_deck": "office_pptx",
        "spreadsheet": "office_xlsx",
        "excel": "office_xlsx",
        "xlsx": "office_xlsx",
        "document": "office_docx",
        "docx": "office_docx",
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


def _scorecard_tags(data: Mapping[str, Any], skillsbench: Mapping[str, Any]) -> list[str]:
    tags = _dedupe(_as_list(data.get("tags")) + _as_list(_to_dict(data.get("metadata")).get("tags")))
    if skillsbench.get("scoring_channel") == "filesystem_output_primary":
        tags.append("filesystem-output-primary")
    if skillsbench.get("artifact_refs_role"):
        tags.append("artifact-refs-diagnostic")
    if skillsbench.get("output_family"):
        tags.append(str(skillsbench.get("output_family")))
    if skillsbench.get("wrote_any_file"):
        tags.append("workspace-wrote-file")
    return _dedupe(tags)


@dataclass(slots=True)
class Scorecard:
    """Per-episode scorecard.

    The first seven fields match the original dataclass shape. All later fields
    are optional and preserve old constructor compatibility.
    """

    # Legacy fields.
    task_id: str
    track: str
    status: str
    correctness_hint: float
    efficiency_hint: float
    robustness_hint: float
    notes: list[str] = field(default_factory=list)

    # Sprint 4 identity fields.
    domain: str = ""
    scenario_id: str = ""
    scenario_name: str = ""
    upstream_track: str = ""
    category: str = ""
    adapter: str = ""
    assessment_mode: str = ""
    scenario_family: str = ""
    benchmark: str = ""
    selected_opponent: str = ""
    source_url: str = ""
    run_id: str = ""
    trace_id: str = ""

    # Summary metadata.
    attempt_count: int = 0
    warning_count: int = 0
    fallback_used: bool = False
    failure_label: str = "none"
    tags: list[str] = field(default_factory=list)

    # Timing and trace shape.
    started_at: float | None = None
    ended_at: float | None = None
    duration_ms: float | None = None
    step_count: int = 0
    artifact_count: int = 0

    # SkillsBench / filesystem-output-primary diagnostics.
    task_set: str = ""
    condition: str = ""
    output_family: str = ""
    output_channel: str = ""
    scoring_channel: str = ""
    artifact_refs_role: str = ""
    filesystem_primary: bool = False
    workspace_visible: bool | None = None
    wrote_any_file: bool | None = None
    selected_solver_key: str = ""
    score_eligible: bool | None = None
    artifact_ref_count: int = 0
    official_artifact_ref_count: int = 0
    workspace_write_count: int = 0
    workspace_ok_write_count: int = 0
    evaluator_diagnostic: JsonDict = field(default_factory=dict)

    # Budget diagnostics and extra room for future score inputs.
    budget_summary: JsonDict = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)

    @property
    def identity(self) -> JsonDict:
        return {
            "task_id": self.task_id,
            "track": self.track,
            "domain": self.domain,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "upstream_track": self.upstream_track,
            "category": self.category,
            "adapter": self.adapter,
            "assessment_mode": self.assessment_mode,
            "scenario_family": self.scenario_family,
            "benchmark": self.benchmark,
            "selected_opponent": self.selected_opponent,
            "source_url": self.source_url,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
        }

    @property
    def skillsbench_identity(self) -> JsonDict:
        return {
            "task_set": self.task_set,
            "condition": self.condition,
            "output_family": self.output_family,
            "output_channel": self.output_channel,
            "scoring_channel": self.scoring_channel,
            "artifact_refs_role": self.artifact_refs_role,
            "filesystem_primary": self.filesystem_primary,
            "workspace_visible": self.workspace_visible,
            "wrote_any_file": self.wrote_any_file,
            "selected_solver_key": self.selected_solver_key,
            "score_eligible": self.score_eligible,
            "artifact_ref_count": self.artifact_ref_count,
            "official_artifact_ref_count": self.official_artifact_ref_count,
            "workspace_write_count": self.workspace_write_count,
            "workspace_ok_write_count": self.workspace_ok_write_count,
        }

    @property
    def overall_hint(self) -> float:
        """Simple balanced average for UI/reporting."""

        return _mean([self.correctness_hint, self.efficiency_hint, self.robustness_hint])

    @property
    def ok(self) -> bool:
        if self.track == "skillsbench" and self.filesystem_primary:
            return (
                self.status.lower() in SUCCESS_STATUSES
                and self.failure_label in {"", "none"}
                and bool(self.wrote_any_file or self.workspace_ok_write_count > 0)
                and self.correctness_hint >= 0.65
            )
        return (
            self.status.lower() in SUCCESS_STATUSES
            and self.failure_label in {"", "none"}
            and self.correctness_hint >= 0.75
            and self.robustness_hint >= 0.65
        )

    def as_dict(self) -> JsonDict:
        return {
            "scorecard_version": SCORECARD_VERSION,
            **self.identity,
            **self.skillsbench_identity,
            "status": self.status,
            "correctness_hint": self.correctness_hint,
            "efficiency_hint": self.efficiency_hint,
            "robustness_hint": self.robustness_hint,
            "overall_hint": self.overall_hint,
            "notes": list(self.notes),
            "attempt_count": self.attempt_count,
            "warning_count": self.warning_count,
            "fallback_used": self.fallback_used,
            "failure_label": self.failure_label,
            "tags": list(self.tags),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "step_count": self.step_count,
            "artifact_count": self.artifact_count,
            "evaluator_diagnostic": dict(self.evaluator_diagnostic),
            "budget_summary": dict(self.budget_summary),
            "metadata": dict(self.metadata),
            "ok": self.ok,
        }

    def compact_dict(self) -> JsonDict:
        return {
            "scorecard_version": SCORECARD_VERSION,
            **self.identity,
            **self.skillsbench_identity,
            "status": self.status,
            "correctness_hint": self.correctness_hint,
            "efficiency_hint": self.efficiency_hint,
            "robustness_hint": self.robustness_hint,
            "overall_hint": self.overall_hint,
            "attempt_count": self.attempt_count,
            "warning_count": self.warning_count,
            "fallback_used": self.fallback_used,
            "failure_label": self.failure_label,
            "duration_ms": self.duration_ms,
            "tags": list(self.tags),
            "ok": self.ok,
        }


@dataclass(slots=True)
class ScorecardGroup:
    """Aggregated score view for one grouping key."""

    group_by: str
    value: str
    count: int
    correctness_hint: float
    efficiency_hint: float
    robustness_hint: float
    overall_hint: float
    passed_count: int
    failed_count: int
    warning_count: int
    fallback_count: int
    filesystem_primary_count: int = 0
    workspace_write_count: int = 0
    workspace_ok_write_count: int = 0
    artifact_ref_count: int = 0
    official_artifact_ref_count: int = 0
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> JsonDict:
        return {
            "group_by": self.group_by,
            "value": self.value,
            "count": self.count,
            "correctness_hint": self.correctness_hint,
            "efficiency_hint": self.efficiency_hint,
            "robustness_hint": self.robustness_hint,
            "overall_hint": self.overall_hint,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "warning_count": self.warning_count,
            "fallback_count": self.fallback_count,
            "filesystem_primary_count": self.filesystem_primary_count,
            "workspace_write_count": self.workspace_write_count,
            "workspace_ok_write_count": self.workspace_ok_write_count,
            "artifact_ref_count": self.artifact_ref_count,
            "official_artifact_ref_count": self.official_artifact_ref_count,
            "tags": list(self.tags),
        }


@dataclass(slots=True)
class ScorecardReport:
    """Collection-level scorecard report."""

    scorecards: list[Scorecard] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.scorecards)

    @property
    def overall_hint(self) -> float:
        return _mean(card.overall_hint for card in self.scorecards)

    @property
    def correctness_hint(self) -> float:
        return _mean(card.correctness_hint for card in self.scorecards)

    @property
    def efficiency_hint(self) -> float:
        return _mean(card.efficiency_hint for card in self.scorecards)

    @property
    def robustness_hint(self) -> float:
        return _mean(card.robustness_hint for card in self.scorecards)

    @property
    def passed_count(self) -> int:
        return sum(1 for card in self.scorecards if card.ok)

    @property
    def failed_count(self) -> int:
        return self.count - self.passed_count

    @property
    def filesystem_primary_count(self) -> int:
        return sum(1 for card in self.scorecards if card.filesystem_primary)

    @property
    def workspace_write_count(self) -> int:
        return sum(card.workspace_write_count for card in self.scorecards)

    @property
    def workspace_ok_write_count(self) -> int:
        return sum(card.workspace_ok_write_count for card in self.scorecards)

    @property
    def official_artifact_ref_count(self) -> int:
        return sum(card.official_artifact_ref_count for card in self.scorecards)

    def group_by(self, field_name: str) -> list[ScorecardGroup]:
        """Aggregate scorecards by any Scorecard attribute.

        Useful fields include Sprint 4 identity plus SkillsBench diagnostics:
        domain, scenario_name, category, upstream_track, output_family,
        scoring_channel, artifact_refs_role, selected_solver_key, etc.
        """

        buckets: dict[str, list[Scorecard]] = {}
        for card in self.scorecards:
            value = _text(getattr(card, field_name, ""), default="unknown")
            buckets.setdefault(value, []).append(card)

        groups: list[ScorecardGroup] = []
        for value, cards in sorted(buckets.items(), key=lambda item: item[0]):
            tags = _dedupe([tag for card in cards for tag in card.tags])
            groups.append(
                ScorecardGroup(
                    group_by=field_name,
                    value=value,
                    count=len(cards),
                    correctness_hint=_mean(card.correctness_hint for card in cards),
                    efficiency_hint=_mean(card.efficiency_hint for card in cards),
                    robustness_hint=_mean(card.robustness_hint for card in cards),
                    overall_hint=_mean(card.overall_hint for card in cards),
                    passed_count=sum(1 for card in cards if card.ok),
                    failed_count=sum(1 for card in cards if not card.ok),
                    warning_count=sum(card.warning_count for card in cards),
                    fallback_count=sum(1 for card in cards if card.fallback_used),
                    filesystem_primary_count=sum(1 for card in cards if card.filesystem_primary),
                    workspace_write_count=sum(card.workspace_write_count for card in cards),
                    workspace_ok_write_count=sum(card.workspace_ok_write_count for card in cards),
                    artifact_ref_count=sum(card.artifact_ref_count for card in cards),
                    official_artifact_ref_count=sum(card.official_artifact_ref_count for card in cards),
                    tags=tags,
                )
            )

        return groups

    def as_dict(self) -> JsonDict:
        return {
            "scorecard_version": SCORECARD_VERSION,
            "count": self.count,
            "correctness_hint": self.correctness_hint,
            "efficiency_hint": self.efficiency_hint,
            "robustness_hint": self.robustness_hint,
            "overall_hint": self.overall_hint,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "filesystem_primary_count": self.filesystem_primary_count,
            "workspace_write_count": self.workspace_write_count,
            "workspace_ok_write_count": self.workspace_ok_write_count,
            "official_artifact_ref_count": self.official_artifact_ref_count,
            "scorecards": [card.as_dict() for card in self.scorecards],
        }

    def compact_dict(self) -> JsonDict:
        return {
            "scorecard_version": SCORECARD_VERSION,
            "count": self.count,
            "correctness_hint": self.correctness_hint,
            "efficiency_hint": self.efficiency_hint,
            "robustness_hint": self.robustness_hint,
            "overall_hint": self.overall_hint,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "filesystem_primary_count": self.filesystem_primary_count,
            "workspace_write_count": self.workspace_write_count,
            "workspace_ok_write_count": self.workspace_ok_write_count,
            "official_artifact_ref_count": self.official_artifact_ref_count,
        }

    def grouped_dict(self, fields: Iterable[str] | None = None) -> JsonDict:
        selected_fields = list(
            fields
            or (
                "domain",
                "scenario_name",
                "scenario_id",
                "category",
                "upstream_track",
                "assessment_mode",
                "scenario_family",
                "benchmark",
                "selected_opponent",
                "adapter",
                "output_family",
                "scoring_channel",
                "artifact_refs_role",
                "selected_solver_key",
            )
        )

        return {
            field_name: [group.as_dict() for group in self.group_by(field_name)]
            for field_name in selected_fields
        }


class ScorecardBuilder:
    """Build scorecards from EpisodeSummary objects and optional budget stats."""

    def build(
        self,
        summary: EpisodeSummary | Mapping[str, Any],
        budget_stats: BudgetStatsCollector | Mapping[str, Any] | None = None,
    ) -> Scorecard:
        """Build a single scorecard.

        Keeps the old call shape while preserving Sprint 4 and SkillsBench
        filesystem-output-primary metadata when the summary object contains it.
        """

        data = _to_dict(summary)
        budget = _safe_budget_summary(budget_stats)
        identity = _identity_from_summary(summary)
        skillsbench = _skillsbench_from_summary(summary)

        status = _first_text(data.get("status"), default="unknown")
        correctness = _status_base_correctness(status)
        efficiency = 1.0
        robustness = 1.0
        notes: list[str] = []

        warning_count = _as_int(data.get("warning_count"), default=0)
        fallback_used = _as_bool(data.get("fallback_used"), default=False)
        failure_label = _first_text(data.get("failure_label"), default="none")
        attempt_count = _as_int(data.get("attempt_count"), default=0)
        artifact_count = _as_int(data.get("artifact_count"), default=0)

        is_skillsbench = identity["track"] == "skillsbench" or bool(skillsbench.get("task_set") or skillsbench.get("scoring_channel"))

        # Budget penalties. Keep old keys and accept common aliases.
        if budget.get("ever_near_limit") or budget.get("near_limit"):
            efficiency -= 0.15
            notes.append("Episode operated near budget limits.")

        if budget.get("ever_hard_limit_hit") or budget.get("hard_limit_hit"):
            efficiency -= 0.35
            robustness -= 0.2
            notes.append("Episode exceeded a hard budget boundary.")

        if budget.get("timeout") or budget.get("timed_out"):
            correctness -= 0.15
            efficiency -= 0.15
            robustness -= 0.1
            notes.append("Episode reported a timeout condition.")

        # Execution penalties.
        if fallback_used:
            if is_skillsbench and skillsbench.get("wrote_any_file"):
                correctness -= 0.05
                robustness -= 0.05
                notes.append("Fallback path was used, but workspace files were written.")
            else:
                correctness -= 0.25
                robustness -= 0.2
                notes.append("Fallback path was required.")

        if warning_count > 0:
            robustness -= min(0.3, warning_count * 0.05)
            notes.append(f"Warnings observed: {warning_count}.")

        if failure_label not in {"none", ""}:
            correctness -= 0.1
            notes.append(f"Failure label observed: {failure_label}.")

        if attempt_count > 1:
            efficiency -= min(0.2, (attempt_count - 1) * 0.05)
            notes.append(f"Multiple attempts observed: {attempt_count}.")

        # Trace quality hints.
        if _as_int(data.get("step_count"), default=0) == 0 and status.lower() in SUCCESS_STATUSES:
            robustness -= 0.05
            notes.append("Completed episode has no recorded trace steps.")

        if not identity.get("domain") and not identity.get("scenario_name") and not is_skillsbench:
            robustness -= 0.05
            notes.append("Sprint 4 scenario identity is incomplete.")

        # SkillsBench filesystem-output-primary rules.
        evaluator_diagnostic: JsonDict = {}
        if is_skillsbench:
            identity["track"] = "skillsbench"
            scoring_channel = _first_text(skillsbench.get("scoring_channel"), default="filesystem_output_primary")
            artifact_refs_role = _first_text(skillsbench.get("artifact_refs_role"), default="diagnostic_and_compatibility_signal")
            wrote_any_file = bool(skillsbench.get("wrote_any_file") or _as_int(skillsbench.get("workspace_ok_write_count"), default=0) > 0)
            workspace_visible = skillsbench.get("workspace_visible")
            official_ref_count = _as_int(skillsbench.get("official_artifact_ref_count"), default=0)
            artifact_ref_count = _as_int(skillsbench.get("artifact_ref_count"), default=0)
            score_eligible = skillsbench.get("score_eligible")

            notes.append("SkillsBench filesystem-output-primary scorecard path active.")

            if scoring_channel != "filesystem_output_primary":
                correctness -= 0.15
                robustness -= 0.15
                notes.append(f"SkillsBench scoring channel is not filesystem_output_primary: {scoring_channel or 'missing'}.")

            if artifact_refs_role and "diagnostic" not in artifact_refs_role:
                robustness -= 0.1
                notes.append("SkillsBench artifact_refs role is not clearly diagnostic/compatibility.")

            if workspace_visible is False:
                correctness -= 0.2
                robustness -= 0.2
                notes.append("SkillsBench task workspace was not visible to the executor.")
            elif workspace_visible is None:
                robustness -= 0.05
                notes.append("SkillsBench workspace visibility was not recorded.")

            if wrote_any_file:
                correctness += 0.1
                robustness += 0.1
                notes.append("Workspace executor wrote evaluator-facing files.")
            else:
                correctness -= 0.3
                robustness -= 0.2
                notes.append("No evaluator-facing workspace file write was recorded.")

            if not skillsbench.get("output_family"):
                robustness -= 0.08
                notes.append("SkillsBench output family is missing.")
            elif skillsbench.get("output_family") == "general_file_output":
                robustness -= 0.05
                notes.append("SkillsBench output family fell back to general_file_output.")

            if not skillsbench.get("selected_solver_key") and skillsbench.get("output_family") not in {"", "general_file_output"}:
                robustness -= 0.05
                notes.append("Concrete SkillsBench family has no selected solver key recorded.")

            if score_eligible is False:
                correctness -= 0.2
                robustness -= 0.15
                notes.append("SkillsBench score_eligible=false was recorded.")

            if official_ref_count == 0 and artifact_ref_count > 0:
                # Diagnostic only. Do not lower correctness when workspace files
                # exist; this is the exact evaluator-channel mismatch we want to
                # preserve for forensics.
                if wrote_any_file:
                    notes.append("Official artifact_refs stayed empty, but workspace files were written; treated as evaluator-channel diagnostic, not primary failure.")
                else:
                    robustness -= 0.08
                    notes.append("Official artifact_refs stayed empty and no workspace write evidence exists.")

            evaluator_diagnostic = {
                "skillsbench": True,
                "scoring_channel": scoring_channel,
                "artifact_refs_role": artifact_refs_role,
                "workspace_visible": workspace_visible,
                "wrote_any_file": wrote_any_file,
                "workspace_ok_write_count": _as_int(skillsbench.get("workspace_ok_write_count"), default=0),
                "artifact_ref_count": artifact_ref_count,
                "official_artifact_ref_count": official_ref_count,
                "channel_mismatch_suspected": bool(official_ref_count == 0 and artifact_ref_count > 0),
                "filesystem_primary": bool(skillsbench.get("filesystem_primary") or scoring_channel == "filesystem_output_primary"),
            }

        tags = _scorecard_tags(data, skillsbench)

        return Scorecard(
            task_id=identity["task_id"],
            track=identity["track"],
            status=status,
            correctness_hint=_clamp(correctness),
            efficiency_hint=_clamp(efficiency),
            robustness_hint=_clamp(robustness),
            notes=_dedupe(notes),
            domain=identity.get("domain", ""),
            scenario_id=identity.get("scenario_id", ""),
            scenario_name=identity.get("scenario_name", ""),
            upstream_track=identity.get("upstream_track", ""),
            category=identity.get("category", ""),
            adapter=identity.get("adapter", ""),
            assessment_mode=identity.get("assessment_mode", ""),
            scenario_family=identity.get("scenario_family", ""),
            benchmark=identity.get("benchmark", ""),
            selected_opponent=identity.get("selected_opponent", ""),
            source_url=identity.get("source_url", ""),
            run_id=identity.get("run_id", ""),
            trace_id=identity.get("trace_id", ""),
            attempt_count=attempt_count,
            warning_count=warning_count,
            fallback_used=fallback_used,
            failure_label=failure_label,
            tags=tags,
            started_at=_as_float(data.get("started_at")),
            ended_at=_as_float(data.get("ended_at")),
            duration_ms=_as_float(data.get("duration_ms")),
            step_count=_as_int(data.get("step_count"), default=0),
            artifact_count=artifact_count,
            task_set=_first_text(skillsbench.get("task_set")),
            condition=_first_text(skillsbench.get("condition")),
            output_family=_first_text(skillsbench.get("output_family")),
            output_channel=_first_text(skillsbench.get("output_channel")),
            scoring_channel=_first_text(skillsbench.get("scoring_channel")),
            artifact_refs_role=_first_text(skillsbench.get("artifact_refs_role")),
            filesystem_primary=_as_bool(skillsbench.get("filesystem_primary"), default=False),
            workspace_visible=skillsbench.get("workspace_visible"),
            wrote_any_file=skillsbench.get("wrote_any_file"),
            selected_solver_key=_first_text(skillsbench.get("selected_solver_key")),
            score_eligible=skillsbench.get("score_eligible"),
            artifact_ref_count=_as_int(skillsbench.get("artifact_ref_count"), default=0),
            official_artifact_ref_count=_as_int(skillsbench.get("official_artifact_ref_count"), default=0),
            workspace_write_count=_as_int(skillsbench.get("workspace_write_count"), default=0),
            workspace_ok_write_count=_as_int(skillsbench.get("workspace_ok_write_count"), default=0),
            evaluator_diagnostic=evaluator_diagnostic,
            budget_summary=budget,
            metadata=_to_dict(data.get("metadata")),
        )

    def build_many(
        self,
        summaries: Iterable[EpisodeSummary | Mapping[str, Any]],
        budget_stats_by_task_id: Mapping[str, BudgetStatsCollector | Mapping[str, Any]] | None = None,
    ) -> ScorecardReport:
        """Build a report from multiple summaries.

        ``budget_stats_by_task_id`` is optional and keyed by summary.task_id.
        """

        budget_map = dict(budget_stats_by_task_id or {})
        cards: list[Scorecard] = []

        for summary in summaries:
            data = _to_dict(summary)
            task_id = _first_text(data.get("task_id"), default="unknown")
            cards.append(self.build(summary, budget_stats=budget_map.get(task_id)))

        return ScorecardReport(scorecards=cards)


def validate_scorecard_selftest() -> JsonDict:
    """Validate legacy behavior and SkillsBench filesystem scoring diagnostics."""

    builder = ScorecardBuilder()
    errors: list[str] = []

    legacy_summary = {
        "task_id": "legacy",
        "track": "openenv",
        "status": "completed",
        "attempt_count": 1,
        "warning_count": 0,
        "fallback_used": False,
        "failure_label": "none",
        "tags": [],
        "step_count": 1,
    }
    legacy = builder.build(legacy_summary)
    if legacy.track != "openenv" or legacy.correctness_hint < 0.9:
        errors.append("legacy scorecard behavior changed unexpectedly")

    skills_summary = {
        "task_id": "exceltable-in-ppt",
        "track": "skillsbench",
        "status": "completed",
        "attempt_count": 1,
        "warning_count": 0,
        "fallback_used": True,
        "failure_label": "none",
        "tags": ["skillsbench"],
        "step_count": 4,
        "artifact_count": 2,
        "task_set": "standard-v1",
        "condition": "with_skills",
        "output_family": "office_pptx",
        "output_channel": "filesystem_output_primary",
        "scoring_channel": "filesystem_output_primary",
        "artifact_refs_role": "diagnostic_and_compatibility_signal",
        "filesystem_primary": True,
        "workspace_visible": True,
        "wrote_any_file": True,
        "selected_solver_key": "office_pptx",
        "artifact_ref_count": 2,
        "official_artifact_ref_count": 0,
        "workspace_write_count": 1,
        "workspace_ok_write_count": 1,
    }
    skills = builder.build(skills_summary)
    if skills.track != "skillsbench":
        errors.append("SkillsBench track was not preserved")
    if skills.scoring_channel != "filesystem_output_primary":
        errors.append(f"unexpected SkillsBench scoring channel: {skills.scoring_channel}")
    if not skills.filesystem_primary or not skills.wrote_any_file:
        errors.append("SkillsBench filesystem primary/write flags missing")
    if skills.correctness_hint < 0.75:
        errors.append(f"SkillsBench workspace-written run was over-penalized: {skills.correctness_hint}")
    if not skills.evaluator_diagnostic.get("channel_mismatch_suspected"):
        errors.append("artifact_refs channel mismatch diagnostic missing")

    report = builder.build_many([legacy_summary, skills_summary])
    grouped = report.grouped_dict(fields=["track", "output_family", "scoring_channel"])
    if "track" not in grouped or not grouped["track"]:
        errors.append("grouped report missing track aggregation")

    return {
        "ok": not errors,
        "errors": errors,
        "version": SCORECARD_VERSION,
        "legacy": legacy.compact_dict(),
        "skillsbench": skills.compact_dict(),
        "report": report.compact_dict(),
    }


__all__ = [
    "SCORECARD_VERSION",
    "Scorecard",
    "ScorecardGroup",
    "ScorecardReport",
    "ScorecardBuilder",
    "validate_scorecard_selftest",
]
