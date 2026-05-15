from __future__ import annotations

"""Trace schema for AegisForge telemetry.

Sprint 4 needs to preserve three identity layers at the same time:

1. upstream AgentBeats / benchmark track or opponent-profile names
   such as officeqa, crmarena, fieldworkarena, maizebargain, osworld,
   pibench, cybergym, netarena, tau2_agentbeats, and mcu_minecraft;
2. local OpenEnv / OmniBench domain names such as healthcare, web, defi,
   legal_domain, finance, tau2, coding, and business_process;
3. final AegisForge scenario names such as DocuDoctor, SearchGlitch,
   CryptoCrash, LawFirmLeak, TaxWizTrap, and SaleForceOneSpy.

The legacy constructor remains valid:

    EpisodeTrace(task_id="x", track="openenv", status="running")

All Sprint 4 fields are optional so older smoke tests and call sites keep
working while episode summaries and scorecards can preserve richer identity.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
import hashlib
import mimetypes
import time
import uuid
from typing import Any, Mapping


JsonDict = dict[str, Any]


def _now() -> float:
    """Unix timestamp in seconds, used consistently across telemetry."""

    return time.time()


def _trace_id() -> str:
    """Short stable-enough trace identifier for local logs and scorecards."""

    return uuid.uuid4().hex[:16]


def _to_dict(value: Any) -> JsonDict:
    """Best-effort conversion of common config/model objects into dictionaries."""

    if value is None:
        return {}
    if is_dataclass(value):
        return dict(asdict(value))
    if isinstance(value, Mapping):
        return dict(value)

    for method_name in ("as_dict", "to_dict", "model_dump", "dict"):
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
    """Normalize an optional scalar to a stripped string."""

    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _first_text(*values: Any, default: str = "") -> str:
    """Return the first non-empty string among several candidate values."""

    for value in values:
        text = _text(value)
        if text:
            return text
    return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = True) -> bool:
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
    """Normalize strings, iterables, and scalars into a list of non-empty strings."""

    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _dedupe(items: list[str]) -> list[str]:
    """Preserve order while removing blank and duplicate strings."""

    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _nested(meta: Mapping[str, Any], *keys: str) -> JsonDict:
    """Return the first mapping found in metadata under any of the provided keys."""

    for key in keys:
        nested = _to_dict(meta.get(key))
        if nested:
            return nested
    return {}


@dataclass(slots=True)
class TraceStep:
    """Single observable step/event in an episode trace."""

    name: str
    phase: str
    message: str
    ok: bool = True
    payload: JsonDict = field(default_factory=dict)
    timestamp: float = field(default_factory=_now)
    level: str = "info"
    duration_ms: float | None = None
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_event(cls, event: Any = None, **overrides: Any) -> "TraceStep":
        """Build a trace step from a TelemetryEvent-like object, mapping, or kwargs.

        Accepted input shapes include:
        - TraceStep.from_event({"name": "route", "phase": "router", ...})
        - TraceStep.from_event(event_obj)
        - TraceStep.from_event(name="route", phase="router", message="selected")
        """

        data = _to_dict(event)
        if event is not None and not data and not isinstance(event, Mapping):
            data = {
                "name": getattr(event, "name", None),
                "phase": getattr(event, "phase", None),
                "message": getattr(event, "message", None),
                "payload": getattr(event, "payload", None),
                "timestamp": getattr(event, "timestamp", None),
                "level": getattr(event, "level", None),
                "ok": getattr(event, "ok", None),
                "duration_ms": getattr(event, "duration_ms", None),
                "metadata": getattr(event, "metadata", None),
            }

        if overrides:
            data.update(overrides)

        payload = _to_dict(data.get("payload"))
        metadata = _to_dict(data.get("metadata"))

        reserved = {
            "name",
            "phase",
            "message",
            "ok",
            "payload",
            "timestamp",
            "level",
            "duration_ms",
            "metadata",
        }
        for key, value in data.items():
            if key not in reserved:
                metadata.setdefault(key, value)

        level = _first_text(data.get("level"), default="info").lower()
        ok_value = data.get("ok")
        if ok_value is None:
            ok = not level.startswith(("error", "fatal", "critical"))
        else:
            ok = _as_bool(ok_value, default=True)

        return cls(
            name=_first_text(data.get("name"), default="event"),
            phase=_first_text(data.get("phase"), default="runtime"),
            message=_text(data.get("message")),
            ok=ok,
            payload=payload,
            timestamp=_as_float(data.get("timestamp"), default=_now()) or _now(),
            level=level,
            duration_ms=_as_float(data.get("duration_ms")),
            metadata=metadata,
        )

    def as_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "phase": self.phase,
            "message": self.message,
            "ok": self.ok,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "level": self.level,
            "duration_ms": self.duration_ms,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class TraceArtifact:
    """Artifact produced during an episode, optionally content-addressed."""

    name: str
    kind: str
    path: str | None = None
    metadata: JsonDict = field(default_factory=dict)
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        name: str | None = None,
        kind: str = "file",
        metadata: Mapping[str, Any] | None = None,
        content_type: str | None = None,
        compute_sha256: bool = True,
    ) -> "TraceArtifact":
        """Create artifact metadata from a local path when available.

        This helper is intentionally best-effort. Missing files still produce
        an artifact record so callers can trace intended outputs.
        """

        p = Path(path)
        guessed_content_type = content_type or mimetypes.guess_type(str(p))[0]
        size_bytes: int | None = None
        digest: str | None = None

        if p.exists() and p.is_file():
            try:
                size_bytes = p.stat().st_size
            except OSError:
                size_bytes = None

            if compute_sha256:
                hasher = hashlib.sha256()
                try:
                    with p.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            hasher.update(chunk)
                    digest = hasher.hexdigest()
                except OSError:
                    digest = None

        return cls(
            name=name or p.name,
            kind=kind,
            path=str(p),
            metadata=dict(metadata or {}),
            content_type=guessed_content_type,
            size_bytes=size_bytes,
            sha256=digest,
        )

    def as_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "metadata": dict(self.metadata),
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(slots=True)
class EpisodeTrace:
    """Episode-level trace object for AegisForge telemetry."""

    # Legacy required fields. Keep this exact constructor shape working:
    # EpisodeTrace(task_id="x", track="openenv", status="running")
    task_id: str
    track: str
    status: str

    # Sprint 4 / AgentBeats identity fields. Optional by design.
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
    trace_id: str = field(default_factory=_trace_id)

    # Timing.
    started_at: float = field(default_factory=_now)
    ended_at: float | None = None

    # Payload and collections.
    metadata: JsonDict = field(default_factory=dict)
    steps: list[TraceStep] = field(default_factory=list)
    artifacts: list[TraceArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_metadata(
        cls,
        *,
        task_id: str,
        track: str = "openenv",
        status: str = "running",
        metadata: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> "EpisodeTrace":
        """Create a trace while extracting common Sprint 4 metadata keys.

        Supported metadata may be flat or nested under keys such as:
        scenario, route, adapter, source, identity, benchmark_config.
        """

        meta = dict(metadata or {})
        identity = _nested(meta, "identity", "trace_identity")
        scenario = _nested(meta, "scenario", "scenario_meta", "scenario_metadata")
        route = _nested(meta, "route", "routing", "router")
        adapter_meta = _nested(meta, "adapter", "adapter_meta")
        source = _nested(meta, "source", "benchmark_source", "origin")
        benchmark_meta = _nested(meta, "benchmark_config", "benchmark_meta")

        resolved_track = _first_text(
            meta.get("track"),
            identity.get("track"),
            meta.get("track_hint"),
            route.get("track"),
            track,
            default="openenv",
        )

        domain = _first_text(
            meta.get("domain"),
            identity.get("domain"),
            meta.get("scenario_domain"),
            scenario.get("domain"),
            route.get("domain"),
        )

        scenario_id = _first_text(
            meta.get("scenario_id"),
            identity.get("scenario_id"),
            scenario.get("scenario_id"),
            scenario.get("id"),
            meta.get("scenario_name"),
            scenario.get("name"),
        )

        scenario_name = _first_text(
            meta.get("scenario_name"),
            identity.get("scenario_name"),
            scenario.get("scenario_name"),
            scenario.get("name"),
            scenario_id,
        )

        upstream_track = _first_text(
            meta.get("upstream_track"),
            identity.get("upstream_track"),
            meta.get("benchmark_track"),
            meta.get("opponent_profile"),
            route.get("upstream_track"),
            route.get("opponent_profile"),
            source.get("upstream_track"),
        )

        category = _first_text(
            meta.get("category"),
            identity.get("category"),
            meta.get("attack_category"),
            scenario.get("category"),
            scenario.get("attack_category"),
            route.get("category"),
        )

        adapter_name = _first_text(
            meta.get("adapter"),
            meta.get("adapter_name"),
            identity.get("adapter"),
            route.get("adapter"),
            route.get("adapter_name"),
            adapter_meta.get("adapter"),
            adapter_meta.get("name"),
        )

        assessment_mode = _first_text(
            meta.get("assessment_mode"),
            identity.get("assessment_mode"),
            scenario.get("assessment_mode"),
            route.get("assessment_mode"),
            benchmark_meta.get("assessment_mode"),
            default="purple_benchmark" if scenario_id or scenario_name else "",
        )

        scenario_family = _first_text(
            meta.get("scenario_family"),
            identity.get("scenario_family"),
            scenario.get("scenario_family"),
            route.get("scenario_family"),
            benchmark_meta.get("scenario_family"),
            default="agentbeats_sprint4" if scenario_id or scenario_name else "",
        )

        benchmark = _first_text(
            meta.get("benchmark"),
            identity.get("benchmark"),
            source.get("benchmark"),
            benchmark_meta.get("benchmark"),
            benchmark_meta.get("name"),
        )

        selected_opponent = _first_text(
            meta.get("selected_opponent"),
            identity.get("selected_opponent"),
            source.get("selected_opponent"),
            route.get("selected_opponent"),
            route.get("opponent"),
        )

        source_url = _first_text(
            meta.get("source_url"),
            identity.get("source_url"),
            source.get("source_url"),
            source.get("url"),
            source.get("repo"),
            benchmark_meta.get("source_url"),
        )

        run_id = _first_text(
            meta.get("run_id"),
            identity.get("run_id"),
            meta.get("episode_id"),
            route.get("run_id"),
        )

        trace_id = _first_text(
            meta.get("trace_id"),
            identity.get("trace_id"),
            default=_trace_id(),
        )

        started_at = _as_float(
            _first_text(meta.get("started_at"), identity.get("started_at")),
            default=_now(),
        )
        ended_at = _as_float(_first_text(meta.get("ended_at"), identity.get("ended_at")))
        existing_duration = _as_float(meta.get("duration_ms"))

        trace = cls(
            task_id=task_id,
            track=resolved_track,
            status=status,
            domain=domain,
            scenario_id=scenario_id,
            scenario_name=scenario_name,
            upstream_track=upstream_track,
            category=category,
            adapter=adapter_name,
            assessment_mode=assessment_mode,
            scenario_family=scenario_family,
            benchmark=benchmark,
            selected_opponent=selected_opponent,
            source_url=source_url,
            run_id=run_id,
            trace_id=trace_id,
            started_at=started_at or _now(),
            ended_at=ended_at,
            metadata=meta,
            tags=_dedupe(list(tags or []) + _as_list(meta.get("tags"))),
        )

        # Preserve externally provided duration even when ended_at is unavailable.
        if existing_duration is not None and trace.ended_at is None:
            trace.metadata.setdefault("duration_ms", existing_duration)

        return trace

    @property
    def duration_ms(self) -> float | None:
        """Episode duration in milliseconds once finished."""

        if self.ended_at is not None:
            return round((self.ended_at - self.started_at) * 1000, 3)

        stored = _as_float(self.metadata.get("duration_ms"))
        return round(stored, 3) if stored is not None else None

    @property
    def identity(self) -> JsonDict:
        """Stable identity block used by summary and scorecard code."""

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

    def add_step(self, step: TraceStep | Mapping[str, Any]) -> TraceStep:
        """Append a TraceStep, accepting either a TraceStep or mapping."""

        normalized = step if isinstance(step, TraceStep) else TraceStep.from_event(step)
        self.steps.append(normalized)
        return normalized

    def add_event(self, event: Any = None, **kwargs: Any) -> TraceStep:
        """Append an event converted into TraceStep and return the step."""

        step = TraceStep.from_event(event, **kwargs)
        self.steps.append(step)
        return step

    def add_artifact(self, artifact: TraceArtifact | Mapping[str, Any]) -> TraceArtifact:
        """Append an artifact, accepting either TraceArtifact or mapping."""

        if isinstance(artifact, TraceArtifact):
            normalized = artifact
        else:
            data = dict(artifact)
            normalized = TraceArtifact(
                name=_first_text(data.get("name"), default="artifact"),
                kind=_first_text(data.get("kind"), default="file"),
                path=_text(data.get("path")) or None,
                metadata=_to_dict(data.get("metadata")),
                content_type=_text(data.get("content_type")) or None,
                size_bytes=_as_int(data.get("size_bytes")),
                sha256=_text(data.get("sha256")) or None,
            )

        self.artifacts.append(normalized)
        return normalized

    def add_warning(self, warning: str) -> str | None:
        """Append a deduplicated warning and return the stored warning."""

        text = str(warning).strip()
        if not text:
            return None
        self.warnings = _dedupe([*self.warnings, text])
        return text

    def add_tag(self, tag: str) -> str | None:
        """Append a deduplicated tag and return the stored tag."""

        text = str(tag).strip()
        if not text:
            return None
        self.tags = _dedupe([*self.tags, text])
        return text

    def finish(self, status: str | None = None, *, ended_at: float | None = None) -> "EpisodeTrace":
        """Mark the episode as finished and return self for optional chaining."""

        if status:
            self.status = status
        self.ended_at = ended_at if ended_at is not None else _now()
        return self

    def as_dict(self) -> JsonDict:
        """Full trace representation for JSON serialization."""

        return {
            **self.identity,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "steps": [step.as_dict() for step in self.steps],
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
            "warnings": list(self.warnings),
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    def compact_dict(self) -> JsonDict:
        """Compact summary suitable for logs, episode_summary, and scorecard."""

        return {
            **self.identity,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "step_count": len(self.steps),
            "artifact_count": len(self.artifacts),
            "warning_count": len(self.warnings),
            "tags": list(self.tags),
        }
