from __future__ import annotations

"""Episode summaries for AegisForge telemetry.

The original summary object was intentionally small and preserved only:

    task_id, track, status, attempt_count, warning_count, fallback_used,
    failure_label, tags

Sprint 4 / AgentBeats Purple Benchmark reporting needs to keep the richer
identity created by ``trace_schema.EpisodeTrace`` while staying compatible with
older callers of ``EpisodeSummaryBuilder.build(...)``.

This module therefore keeps the legacy build shape working and adds:
- Sprint 4 identity fields;
- timing fields;
- step/artifact counts;
- builders from EpisodeTrace-like objects or dictionaries.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping

from .failure_taxonomy import FailureTaxonomy


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


def _to_dict(value: Any) -> JsonDict:
    """Best-effort conversion of telemetry/dataclass/model objects to dicts."""

    if value is None:
        return {}
    if is_dataclass(value):
        return dict(asdict(value))
    if isinstance(value, Mapping):
        return dict(value)

    for method_name in ("as_dict", "compact_dict", "to_dict", "model_dump", "dict"):
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


def _nested(mapping: Mapping[str, Any], *keys: str) -> JsonDict:
    """Return the first nested mapping found under one of the provided keys."""

    for key in keys:
        nested = _to_dict(mapping.get(key))
        if nested:
            return nested
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


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _extract_identity(data: Mapping[str, Any]) -> JsonDict:
    """Extract Sprint 4 identity from flat or nested telemetry dictionaries."""

    identity = _nested(data, "identity", "trace_identity")
    metadata = _nested(data, "metadata", "meta")
    scenario = _nested(data, "scenario", "scenario_meta", "scenario_metadata")
    route = _nested(data, "route", "routing", "router")
    source = _nested(data, "source", "benchmark_source", "origin")
    benchmark_meta = _nested(data, "benchmark_config", "benchmark_meta")

    extracted = {
        "task_id": _first_text(data.get("task_id"), identity.get("task_id"), metadata.get("task_id")),
        "track": _first_text(
            data.get("track"),
            identity.get("track"),
            metadata.get("track"),
            metadata.get("track_hint"),
            route.get("track"),
            default="openenv",
        ),
        "domain": _first_text(
            data.get("domain"),
            identity.get("domain"),
            metadata.get("domain"),
            metadata.get("scenario_domain"),
            scenario.get("domain"),
            route.get("domain"),
        ),
        "scenario_id": _first_text(
            data.get("scenario_id"),
            identity.get("scenario_id"),
            metadata.get("scenario_id"),
            scenario.get("scenario_id"),
            scenario.get("id"),
            data.get("scenario_name"),
            metadata.get("scenario_name"),
            scenario.get("name"),
        ),
        "scenario_name": _first_text(
            data.get("scenario_name"),
            identity.get("scenario_name"),
            metadata.get("scenario_name"),
            scenario.get("scenario_name"),
            scenario.get("name"),
            data.get("scenario_id"),
            metadata.get("scenario_id"),
        ),
        "upstream_track": _first_text(
            data.get("upstream_track"),
            identity.get("upstream_track"),
            metadata.get("upstream_track"),
            metadata.get("benchmark_track"),
            metadata.get("opponent_profile"),
            route.get("upstream_track"),
            route.get("opponent_profile"),
            source.get("upstream_track"),
        ),
        "category": _first_text(
            data.get("category"),
            identity.get("category"),
            metadata.get("category"),
            metadata.get("attack_category"),
            scenario.get("category"),
            scenario.get("attack_category"),
            route.get("category"),
        ),
        "adapter": _first_text(
            data.get("adapter"),
            identity.get("adapter"),
            metadata.get("adapter"),
            metadata.get("adapter_name"),
            route.get("adapter"),
            route.get("adapter_name"),
        ),
        "assessment_mode": _first_text(
            data.get("assessment_mode"),
            identity.get("assessment_mode"),
            metadata.get("assessment_mode"),
            scenario.get("assessment_mode"),
            route.get("assessment_mode"),
            benchmark_meta.get("assessment_mode"),
        ),
        "scenario_family": _first_text(
            data.get("scenario_family"),
            identity.get("scenario_family"),
            metadata.get("scenario_family"),
            scenario.get("scenario_family"),
            route.get("scenario_family"),
            benchmark_meta.get("scenario_family"),
        ),
        "benchmark": _first_text(
            data.get("benchmark"),
            identity.get("benchmark"),
            metadata.get("benchmark"),
            source.get("benchmark"),
            benchmark_meta.get("benchmark"),
            benchmark_meta.get("name"),
        ),
        "selected_opponent": _first_text(
            data.get("selected_opponent"),
            identity.get("selected_opponent"),
            metadata.get("selected_opponent"),
            source.get("selected_opponent"),
            route.get("selected_opponent"),
            route.get("opponent"),
        ),
        "source_url": _first_text(
            data.get("source_url"),
            identity.get("source_url"),
            metadata.get("source_url"),
            source.get("source_url"),
            source.get("url"),
            source.get("repo"),
            benchmark_meta.get("source_url"),
        ),
        "run_id": _first_text(data.get("run_id"), identity.get("run_id"), metadata.get("run_id"), metadata.get("episode_id")),
        "trace_id": _first_text(data.get("trace_id"), identity.get("trace_id"), metadata.get("trace_id")),
    }

    return extracted


def _extract_attempt_count(data: Mapping[str, Any], default: int = 0) -> int:
    metadata = _nested(data, "metadata", "meta")
    direct = _as_int(data.get("attempt_count"), default=-1)
    if direct >= 0:
        return direct

    meta_attempts = _as_int(metadata.get("attempt_count"), default=-1)
    if meta_attempts >= 0:
        return meta_attempts

    attempts = _as_int(metadata.get("attempts"), default=-1)
    if attempts >= 0:
        return attempts

    retry_count = _as_int(metadata.get("retry_count"), default=-1)
    if retry_count >= 0:
        return retry_count + 1

    return default


def _detect_fallback(warnings: list[str], tags: list[str], data: Mapping[str, Any]) -> bool:
    metadata = _nested(data, "metadata", "meta")
    if _as_bool(data.get("fallback_used"), default=False):
        return True
    if _as_bool(metadata.get("fallback_used"), default=False):
        return True

    haystack = " ".join([*warnings, *tags]).lower()
    return "fallback" in haystack or "fall back" in haystack


def _failure_value(taxonomy: FailureTaxonomy, *, error_code: str | None, error_message: str | None) -> str:
    failure = taxonomy.classify(error_code=error_code, message=error_message)
    return str(getattr(failure, "value", failure))


@dataclass(slots=True)
class EpisodeSummary:
    """Compact episode summary used by logs, reports, and scorecards."""

    # Legacy fields.
    task_id: str
    track: str
    status: str
    attempt_count: int
    warning_count: int
    fallback_used: bool = False
    failure_label: str = "none"
    tags: list[str] = field(default_factory=list)

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

    # Timing and trace shape.
    started_at: float | None = None
    ended_at: float | None = None
    duration_ms: float | None = None
    step_count: int = 0
    artifact_count: int = 0

    # Optional detail retained for diagnostics.
    warning_messages: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    @property
    def identity(self) -> JsonDict:
        """Identity block that mirrors EpisodeTrace.identity."""

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
    def ok(self) -> bool:
        """True when the summary does not represent a known failed/error state."""

        return self.status.lower() not in {"failed", "error", "timeout", "cancelled"} and self.failure_label == "none"

    @classmethod
    def from_trace(
        cls,
        trace: Any,
        *,
        taxonomy: FailureTaxonomy | None = None,
        attempt_count: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
    ) -> "EpisodeSummary":
        """Build an EpisodeSummary from an EpisodeTrace-like object or dict."""

        return EpisodeSummaryBuilder(taxonomy=taxonomy).build_from_trace(
            trace,
            attempt_count=attempt_count,
            error_code=error_code,
            error_message=error_message,
            tags=tags,
            status=status,
        )

    def as_dict(self) -> JsonDict:
        """Full summary dictionary.

        Includes the original fields plus Sprint 4 identity and timing. Existing
        consumers that read only the original keys remain compatible.
        """

        return {
            **self.identity,
            "status": self.status,
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
            "warning_messages": list(self.warning_messages),
            "metadata": dict(self.metadata),
            "ok": self.ok,
        }

    def compact_dict(self) -> JsonDict:
        """Smaller summary for scorecards and line-oriented logs."""

        return {
            **self.identity,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "warning_count": self.warning_count,
            "fallback_used": self.fallback_used,
            "failure_label": self.failure_label,
            "duration_ms": self.duration_ms,
            "step_count": self.step_count,
            "artifact_count": self.artifact_count,
            "tags": list(self.tags),
            "ok": self.ok,
        }


class EpisodeSummaryBuilder:
    """Build EpisodeSummary objects from legacy args or richer trace objects."""

    def __init__(self, taxonomy: FailureTaxonomy | None = None) -> None:
        self.taxonomy = taxonomy or FailureTaxonomy()

    def build(
        self,
        *,
        task_id: str,
        track: str,
        status: str,
        attempt_count: int,
        warnings: list[str] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        tags: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        domain: str = "",
        scenario_id: str = "",
        scenario_name: str = "",
        upstream_track: str = "",
        category: str = "",
        adapter: str = "",
        assessment_mode: str = "",
        scenario_family: str = "",
        benchmark: str = "",
        selected_opponent: str = "",
        source_url: str = "",
        run_id: str = "",
        trace_id: str = "",
        started_at: float | None = None,
        ended_at: float | None = None,
        duration_ms: float | None = None,
        step_count: int = 0,
        artifact_count: int = 0,
    ) -> EpisodeSummary:
        """Legacy-compatible builder with optional Sprint 4 identity fields."""

        warning_messages = _as_list(warnings)
        supplied_tags = _dedupe(_as_list(tags))
        meta = dict(metadata or {})
        identity = _extract_identity({**meta, "metadata": meta})

        fallback_used = _detect_fallback(warning_messages, supplied_tags, {"metadata": meta})
        failure_label = _failure_value(self.taxonomy, error_code=error_code, error_message=error_message)

        return EpisodeSummary(
            task_id=task_id,
            track=track,
            status=status,
            attempt_count=attempt_count,
            warning_count=len(warning_messages),
            fallback_used=fallback_used,
            failure_label=failure_label,
            tags=supplied_tags,
            domain=_first_text(domain, identity.get("domain")),
            scenario_id=_first_text(scenario_id, identity.get("scenario_id")),
            scenario_name=_first_text(scenario_name, identity.get("scenario_name"), scenario_id, identity.get("scenario_id")),
            upstream_track=_first_text(upstream_track, identity.get("upstream_track")),
            category=_first_text(category, identity.get("category")),
            adapter=_first_text(adapter, identity.get("adapter")),
            assessment_mode=_first_text(assessment_mode, identity.get("assessment_mode")),
            scenario_family=_first_text(scenario_family, identity.get("scenario_family")),
            benchmark=_first_text(benchmark, identity.get("benchmark")),
            selected_opponent=_first_text(selected_opponent, identity.get("selected_opponent")),
            source_url=_first_text(source_url, identity.get("source_url")),
            run_id=_first_text(run_id, identity.get("run_id")),
            trace_id=_first_text(trace_id, identity.get("trace_id")),
            started_at=started_at if started_at is not None else _as_float(meta.get("started_at")),
            ended_at=ended_at if ended_at is not None else _as_float(meta.get("ended_at")),
            duration_ms=duration_ms if duration_ms is not None else _as_float(meta.get("duration_ms")),
            step_count=step_count,
            artifact_count=artifact_count,
            warning_messages=warning_messages,
            metadata=meta,
        )

    def build_from_trace(
        self,
        trace: Any,
        *,
        attempt_count: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
    ) -> EpisodeSummary:
        """Build a summary from EpisodeTrace, compact trace dict, or full trace dict."""

        data = _to_dict(trace)
        metadata = _nested(data, "metadata", "meta")
        identity = _extract_identity(data)

        warning_messages = _as_list(data.get("warnings") or data.get("warning_messages"))
        warning_count = _as_int(data.get("warning_count"), default=len(warning_messages))
        if warning_count and not warning_messages:
            warning_messages = []

        existing_tags = _as_list(data.get("tags")) + _as_list(metadata.get("tags"))
        supplied_tags = _as_list(tags)
        merged_tags = _dedupe(existing_tags + supplied_tags)

        steps = data.get("steps")
        artifacts = data.get("artifacts")

        step_count = _as_int(
            data.get("step_count"),
            default=len(steps) if isinstance(steps, list) else 0,
        )
        artifact_count = _as_int(
            data.get("artifact_count"),
            default=len(artifacts) if isinstance(artifacts, list) else 0,
        )

        selected_status = _first_text(status, data.get("status"), metadata.get("status"), default="unknown")
        selected_attempt_count = attempt_count if attempt_count is not None else _extract_attempt_count(data)

        fallback_used = _detect_fallback(warning_messages, merged_tags, data)
        failure_label = _failure_value(self.taxonomy, error_code=error_code, error_message=error_message)

        return EpisodeSummary(
            task_id=_first_text(identity.get("task_id"), default="unknown"),
            track=_first_text(identity.get("track"), default="openenv"),
            status=selected_status,
            attempt_count=selected_attempt_count,
            warning_count=warning_count,
            fallback_used=fallback_used,
            failure_label=failure_label,
            tags=merged_tags,
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
            started_at=_as_float(data.get("started_at")),
            ended_at=_as_float(data.get("ended_at")),
            duration_ms=_as_float(data.get("duration_ms")),
            step_count=step_count,
            artifact_count=artifact_count,
            warning_messages=warning_messages,
            metadata=metadata,
        )
