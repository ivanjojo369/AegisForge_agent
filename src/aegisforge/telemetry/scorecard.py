from __future__ import annotations

"""Scorecards for AegisForge telemetry.

The original scorecard preserved only task_id, track, status, three score hints,
and notes. Sprint 4 / AgentBeats Purple Benchmark reporting needs to preserve
the richer identity emitted by ``EpisodeSummary`` and ``EpisodeTrace`` while
keeping the old builder call working:

    ScorecardBuilder().build(summary, budget_stats=None)

This module keeps that shape and adds:
- Sprint 4 identity fields;
- timing and trace-shape fields;
- budget/failure/warning metadata;
- compact dictionaries for logs;
- lightweight aggregation by domain, scenario, category, upstream track, etc.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Iterable, Mapping

from .budget_stats import BudgetStatsCollector
from .episode_summary import EpisodeSummary


JsonDict = dict[str, Any]


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
    if text in {"1", "true", "yes", "y", "ok", "pass", "passed", "success"}:
        return True
    if text in {"0", "false", "no", "n", "fail", "failed", "error"}:
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

    output["track"] = _first_text(output.get("track"), default="openenv")
    output["task_id"] = _first_text(output.get("task_id"), default="unknown")
    return output


@dataclass(slots=True)
class Scorecard:
    """Per-episode scorecard.

    The first seven fields match the original dataclass shape. All Sprint 4
    fields are optional, preserving old constructor compatibility.
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
    def overall_hint(self) -> float:
        """Simple balanced average for UI/reporting.

        Keep the three individual hint scores as the authoritative components;
        this property is just a compact headline number.
        """

        return _mean([self.correctness_hint, self.efficiency_hint, self.robustness_hint])

    @property
    def ok(self) -> bool:
        return (
            self.status.lower() in SUCCESS_STATUSES
            and self.failure_label in {"", "none"}
            and self.correctness_hint >= 0.75
            and self.robustness_hint >= 0.65
        )

    def as_dict(self) -> JsonDict:
        return {
            **self.identity,
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
            "budget_summary": dict(self.budget_summary),
            "metadata": dict(self.metadata),
            "ok": self.ok,
        }

    def compact_dict(self) -> JsonDict:
        return {
            **self.identity,
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

    def group_by(self, field_name: str) -> list[ScorecardGroup]:
        """Aggregate scorecards by any Scorecard attribute.

        Useful Sprint 4 fields:
        - domain
        - scenario_name
        - scenario_id
        - category
        - upstream_track
        - assessment_mode
        - scenario_family
        - benchmark
        - selected_opponent
        - adapter
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
                    tags=tags,
                )
            )

        return groups

    def as_dict(self) -> JsonDict:
        return {
            "count": self.count,
            "correctness_hint": self.correctness_hint,
            "efficiency_hint": self.efficiency_hint,
            "robustness_hint": self.robustness_hint,
            "overall_hint": self.overall_hint,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "scorecards": [card.as_dict() for card in self.scorecards],
        }

    def compact_dict(self) -> JsonDict:
        return {
            "count": self.count,
            "correctness_hint": self.correctness_hint,
            "efficiency_hint": self.efficiency_hint,
            "robustness_hint": self.robustness_hint,
            "overall_hint": self.overall_hint,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
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

        Keeps the old call shape while preserving Sprint 4 metadata when the
        summary object contains it.
        """

        data = _to_dict(summary)
        budget = _safe_budget_summary(budget_stats)
        identity = _identity_from_summary(summary)

        status = _first_text(data.get("status"), default="unknown")
        correctness = _status_base_correctness(status)
        efficiency = 1.0
        robustness = 1.0
        notes: list[str] = []

        warning_count = _as_int(data.get("warning_count"), default=0)
        fallback_used = _as_bool(data.get("fallback_used"), default=False)
        failure_label = _first_text(data.get("failure_label"), default="none")
        attempt_count = _as_int(data.get("attempt_count"), default=0)

        # Budget penalties. Keep old keys and accept a few common aliases.
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

        if not identity.get("domain") and not identity.get("scenario_name"):
            robustness -= 0.05
            notes.append("Sprint 4 scenario identity is incomplete.")

        return Scorecard(
            task_id=identity["task_id"],
            track=identity["track"],
            status=status,
            correctness_hint=_clamp(correctness),
            efficiency_hint=_clamp(efficiency),
            robustness_hint=_clamp(robustness),
            notes=notes,
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
            tags=_dedupe(_as_list(data.get("tags"))),
            started_at=_as_float(data.get("started_at")),
            ended_at=_as_float(data.get("ended_at")),
            duration_ms=_as_float(data.get("duration_ms")),
            step_count=_as_int(data.get("step_count"), default=0),
            artifact_count=_as_int(data.get("artifact_count"), default=0),
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
