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

TRACE_SCHEMA_VERSION = "trace_schema_v0_7_skillsbench_filesystem_output_primary_2026_06_09"

SKILLSBENCH_TRACE_TAGS = (
    "skillsbench",
    "benchflow",
    "standard-v1",
    "with_skills",
    "filesystem-output-primary",
    "artifact-refs-diagnostic",
)

SKILLSBENCH_SAFE_OUTPUT_ROOTS = (
    "/root",
    "/root/output",
    "/app/workspace",
    "/app/output",
    "/output",
    "/workspace",
    "/home/github/build/failed",
)

SKILLSBENCH_FAMILY_ALIASES = {
    "presentation": "office_pptx",
    "pptx": "office_pptx",
    "ppt": "office_pptx",
    "slides": "office_pptx",
    "slide_deck": "office_pptx",
    "spreadsheet": "office_xlsx",
    "xlsx": "office_xlsx",
    "excel": "office_xlsx",
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


def _normalize_track(value: Any) -> str:
    raw = _text(value, default="openenv").lower()
    raw_dash = raw.replace("_", "-").replace(" ", "-")
    aliases = {
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
        "artifact-first": "skillsbench",
        "mcu-minecraft": "mcu",
        "minecraft": "mcu",
        "pi-bench": "pibench",
        "agent-safety": "pibench",
        "net-arena": "netarena",
        "cybersecurity": "cybergym",
    }
    return aliases.get(raw_dash, raw_dash)


def _normalize_family(value: Any) -> str:
    raw = _text(value).lower().replace("-", "_").replace(" ", "_")
    return SKILLSBENCH_FAMILY_ALIASES.get(raw, raw)


def _looks_like_skillsbench(meta: Mapping[str, Any]) -> bool:
    blob_parts = []
    for key in (
        "track",
        "track_hint",
        "benchmark",
        "task_set",
        "condition",
        "adapter",
        "scenario_family",
        "output_channel",
        "scoring_channel",
        "artifact_refs_role",
        "task_id",
        "canonical_task_id",
        "category",
    ):
        value = meta.get(key)
        if value is not None:
            blob_parts.append(str(value))
    blob_parts.append(str(_to_dict(meta.get("skillsbench_artifact_bridge"))))
    blob_parts.append(str(_to_dict(meta.get("workspace_execution"))))
    blob = " ".join(blob_parts).lower().replace("_", "-")
    return any(tag.replace("_", "-") in blob for tag in SKILLSBENCH_TRACE_TAGS)


def _skillsbench_scoring_channel(meta: Mapping[str, Any]) -> str:
    return _first_text(
        meta.get("scoring_channel"),
        meta.get("output_channel"),
        _to_dict(meta.get("skillsbench_artifact_bridge")).get("output_channel"),
        _to_dict(meta.get("workspace_execution")).get("scoring_channel"),
        default="filesystem_output_primary" if _looks_like_skillsbench(meta) else "",
    )


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

    # v0.7 fields for evaluator/scoring-channel forensics.
    output_channel: str = ""
    artifact_refs_role: str = ""
    filesystem_primary: bool = False
    scoring_channel: str = ""

    @classmethod
    def from_record(
        cls,
        record: Mapping[str, Any],
        *,
        default_kind: str = "file",
        metadata: Mapping[str, Any] | None = None,
    ) -> "TraceArtifact":
        """Create artifact metadata from a writer/executor artifact record.

        Supported records include task_workspace_executor WorkspaceWriteResult
        dicts, artifact_writer manifests, and A2A artifact refs. This method
        does not require the file to be readable; it preserves intended paths
        for evaluator forensic traces.
        """

        data = dict(record or {})
        merged_meta = dict(metadata or {})
        for key in ("source", "phase", "solver", "selected_solver_key", "family", "task_id"):
            if key in data and key not in merged_meta:
                merged_meta[key] = data.get(key)

        path = _first_text(data.get("path"), data.get("uri"), data.get("file_path"))
        name = _first_text(
            data.get("name"),
            data.get("file_name"),
            data.get("artifact_name"),
            Path(path).name if path else "",
            default="artifact",
        )
        kind = _first_text(data.get("kind"), data.get("artifact_kind"), default=default_kind)
        content_type = _first_text(
            data.get("content_type"),
            data.get("mime_type"),
            mimetypes.guess_type(name)[0],
        ) or None

        return cls(
            name=name,
            kind=kind,
            path=path or None,
            metadata=merged_meta,
            content_type=content_type,
            size_bytes=_as_int(data.get("size_bytes") if data.get("size_bytes") is not None else data.get("bytes_written")),
            sha256=_text(data.get("sha256")) or None,
            output_channel=_first_text(data.get("output_channel"), merged_meta.get("output_channel")),
            artifact_refs_role=_first_text(data.get("artifact_refs_role"), merged_meta.get("artifact_refs_role")),
            filesystem_primary=_as_bool(data.get("filesystem_primary", merged_meta.get("filesystem_primary")), default=False),
            scoring_channel=_first_text(data.get("scoring_channel"), merged_meta.get("scoring_channel")),
        )

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
            output_channel=_text((metadata or {}).get("output_channel")) if metadata else "",
            artifact_refs_role=_text((metadata or {}).get("artifact_refs_role")) if metadata else "",
            filesystem_primary=_as_bool((metadata or {}).get("filesystem_primary"), default=False) if metadata else False,
            scoring_channel=_text((metadata or {}).get("scoring_channel")) if metadata else "",
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
            "output_channel": self.output_channel,
            "artifact_refs_role": self.artifact_refs_role,
            "filesystem_primary": self.filesystem_primary,
            "scoring_channel": self.scoring_channel,
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

    # v0.7 SkillsBench / evaluator-forensics identity fields.
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
        identity = _to_dict(meta.get("identity")) or _to_dict(meta.get("trace_identity"))
        scenario = (
            _to_dict(meta.get("scenario"))
            or _to_dict(meta.get("scenario_meta"))
            or _to_dict(meta.get("scenario_metadata"))
        )
        route = _to_dict(meta.get("route")) or _to_dict(meta.get("routing")) or _to_dict(meta.get("router"))
        adapter_meta = _to_dict(meta.get("adapter")) or _to_dict(meta.get("adapter_meta"))
        source = (
            _to_dict(meta.get("source"))
            or _to_dict(meta.get("benchmark_source"))
            or _to_dict(meta.get("origin"))
        )
        benchmark_meta = _to_dict(meta.get("benchmark_config")) or _to_dict(meta.get("benchmark_meta"))

        resolved_track = _normalize_track(
            _first_text(
                meta.get("track"),
                identity.get("track"),
                meta.get("track_hint"),
                route.get("track"),
                meta.get("benchmark"),
                meta.get("task_set"),
                track,
                default="openenv",
            )
        )
        if resolved_track != "skillsbench" and _looks_like_skillsbench(meta):
            resolved_track = "skillsbench"

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


        workspace_execution = _to_dict(meta.get("workspace_execution"))
        workspace_diagnostics = _to_dict(workspace_execution.get("diagnostics"))
        skillsbridge = _to_dict(meta.get("skillsbench_artifact_bridge"))
        artifact_output = _to_dict(meta.get("skillsbench_artifact_output"))
        filesystem_output = _to_dict(meta.get("skillsbench_filesystem_output"))

        task_set = _first_text(
            meta.get("task_set"),
            identity.get("task_set"),
            workspace_execution.get("task_set"),
            artifact_output.get("task_set"),
            filesystem_output.get("task_set"),
            default="standard-v1" if resolved_track == "skillsbench" else "",
        )

        condition = _first_text(
            meta.get("condition"),
            identity.get("condition"),
            workspace_execution.get("condition"),
            artifact_output.get("condition"),
            filesystem_output.get("condition"),
            default="with_skills" if resolved_track == "skillsbench" else "",
        )

        output_family = _normalize_family(
            _first_text(
                meta.get("output_family"),
                meta.get("family"),
                meta.get("contract_family"),
                workspace_execution.get("family"),
                workspace_diagnostics.get("family"),
                workspace_diagnostics.get("contract_family"),
                default="",
            )
        )

        output_channel = _first_text(
            meta.get("output_channel"),
            meta.get("scoring_channel"),
            skillsbridge.get("output_channel"),
            workspace_execution.get("scoring_channel"),
            filesystem_output.get("scoring_channel"),
            default="filesystem_output_primary" if resolved_track == "skillsbench" else "",
        )

        scoring_channel = _skillsbench_scoring_channel(
            {
                **meta,
                "output_channel": output_channel,
                "workspace_execution": workspace_execution,
            }
        )

        artifact_refs_role = _first_text(
            meta.get("artifact_refs_role"),
            skillsbridge.get("a2a_artifacts"),
            workspace_execution.get("artifact_refs_role"),
            artifact_output.get("artifact_refs"),
            filesystem_output.get("artifact_refs"),
            default="diagnostic_and_compatibility_signal" if resolved_track == "skillsbench" else "",
        )

        filesystem_primary = _as_bool(
            meta.get("filesystem_primary"),
            default=(resolved_track == "skillsbench" and scoring_channel == "filesystem_output_primary"),
        )

        workspace_visible = workspace_execution.get("workspace_visible")
        if workspace_visible is None:
            workspace_visible = workspace_diagnostics.get("workspace_visible")

        wrote_any_file = workspace_execution.get("wrote_any_file")
        if wrote_any_file is None:
            wrote_any_file = workspace_diagnostics.get("wrote_any_file")
        if wrote_any_file is None and workspace_diagnostics.get("ok_writes") is not None:
            wrote_any_file = (_as_int(workspace_diagnostics.get("ok_writes"), default=0) or 0) > 0

        selected_solver_key = _first_text(
            meta.get("selected_solver_key"),
            workspace_execution.get("selected_solver_key"),
            workspace_diagnostics.get("selected_solver_key"),
        )

        score_eligible_raw = meta.get("score_eligible")
        if score_eligible_raw is None:
            score_eligible_raw = workspace_execution.get("score_eligible")

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
            task_set=task_set,
            condition=condition,
            output_family=output_family,
            output_channel=output_channel,
            scoring_channel=scoring_channel,
            artifact_refs_role=artifact_refs_role,
            filesystem_primary=filesystem_primary,
            workspace_visible=_as_bool(workspace_visible, default=False) if workspace_visible is not None else None,
            wrote_any_file=_as_bool(wrote_any_file, default=False) if wrote_any_file is not None else None,
            selected_solver_key=selected_solver_key,
            score_eligible=_as_bool(score_eligible_raw, default=True) if score_eligible_raw is not None else None,
            trace_id=trace_id,
            started_at=started_at or _now(),
            ended_at=ended_at,
            metadata=meta,
            tags=_dedupe(list(tags or []) + _as_list(meta.get("tags"))),
        )

        if trace.track == "skillsbench":
            trace.add_tag("skillsbench")
            trace.add_tag("standard-v1")
            if trace.condition:
                trace.add_tag(trace.condition)
            if trace.scoring_channel == "filesystem_output_primary":
                trace.add_tag("filesystem-output-primary")
            if trace.artifact_refs_role:
                trace.add_tag("artifact-refs-diagnostic")
            if trace.output_family:
                trace.add_tag(trace.output_family)

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
                content_type=_text(data.get("content_type") or data.get("mime_type")) or None,
                size_bytes=_as_int(data.get("size_bytes") if data.get("size_bytes") is not None else data.get("bytes_written")),
                sha256=_text(data.get("sha256")) or None,
                output_channel=_text(data.get("output_channel")),
                artifact_refs_role=_text(data.get("artifact_refs_role")),
                filesystem_primary=_as_bool(data.get("filesystem_primary"), default=False),
                scoring_channel=_text(data.get("scoring_channel")),
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


    def add_workspace_execution(self, execution: Any, *, phase: str = "workspace_executor") -> TraceStep:
        """Attach a SkillsBench task_workspace_executor result to this trace."""

        data = _to_dict(execution)
        diagnostics = _to_dict(data.get("diagnostics"))
        writes = data.get("writes") or diagnostics.get("write_outcomes") or []

        self.track = _normalize_track(self.track)
        if self.track == "skillsbench":
            self.scoring_channel = self.scoring_channel or "filesystem_output_primary"
            self.output_channel = self.output_channel or "filesystem_output_primary"
            self.artifact_refs_role = self.artifact_refs_role or "diagnostic_and_compatibility_signal"
            self.filesystem_primary = True

        self.workspace_visible = _as_bool(data.get("workspace_visible"), default=False)
        self.wrote_any_file = _as_bool(data.get("wrote_any_file"), default=False)
        self.output_family = self.output_family or _normalize_family(data.get("family"))
        self.selected_solver_key = self.selected_solver_key or _first_text(diagnostics.get("selected_solver_key"))

        for item in writes if isinstance(writes, list) else []:
            item_dict = _to_dict(item)
            if not item_dict:
                continue
            if not _as_bool(item_dict.get("ok"), default=False):
                continue
            artifact = TraceArtifact.from_record(
                item_dict,
                default_kind=_first_text(item_dict.get("kind"), default="file"),
                metadata={
                    "phase": phase,
                    "source": "task_workspace_executor",
                    "output_channel": self.output_channel,
                    "scoring_channel": self.scoring_channel,
                    "artifact_refs_role": self.artifact_refs_role,
                    "filesystem_primary": self.filesystem_primary,
                    "selected_solver_key": self.selected_solver_key,
                    "family": self.output_family,
                },
            )
            if not artifact.output_channel:
                artifact.output_channel = self.output_channel
            if not artifact.scoring_channel:
                artifact.scoring_channel = self.scoring_channel
            if not artifact.artifact_refs_role:
                artifact.artifact_refs_role = self.artifact_refs_role
            artifact.filesystem_primary = self.filesystem_primary
            self.add_artifact(artifact)

        self.metadata["workspace_execution"] = data
        return self.add_event(
            name="workspace_execution",
            phase=phase,
            message="SkillsBench workspace execution recorded",
            ok=_as_bool(data.get("ok"), default=bool(self.wrote_any_file)),
            payload={
                "status": data.get("status"),
                "workspace_visible": self.workspace_visible,
                "wrote_any_file": self.wrote_any_file,
                "family": self.output_family,
                "selected_solver_key": self.selected_solver_key,
                "write_count": len(writes) if isinstance(writes, list) else None,
                "scoring_channel": self.scoring_channel,
                "artifact_refs_role": self.artifact_refs_role,
            },
        )

    def add_artifact_ref_diagnostic(self, refs: Any, *, phase: str = "artifact_refs_probe") -> TraceStep:
        """Record artifact_refs as diagnostic evidence, not primary scoring."""

        ref_list = refs if isinstance(refs, list) else []
        self.metadata["artifact_refs_diagnostic"] = ref_list
        self.artifact_refs_role = self.artifact_refs_role or "diagnostic_and_compatibility_signal"
        return self.add_event(
            name="artifact_refs_diagnostic",
            phase=phase,
            message="A2A artifact_refs captured as diagnostic compatibility evidence",
            ok=True,
            payload={
                "ref_count": len(ref_list),
                "artifact_refs_role": self.artifact_refs_role,
                "scoring_channel": self.scoring_channel or "filesystem_output_primary",
            },
        )


    def finish(self, status: str | None = None, *, ended_at: float | None = None) -> "EpisodeTrace":
        """Mark the episode as finished and return self for optional chaining."""

        if status:
            self.status = status
        self.ended_at = ended_at if ended_at is not None else _now()
        return self

    def as_dict(self) -> JsonDict:
        """Full trace representation for JSON serialization."""

        return {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
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
            "trace_schema_version": TRACE_SCHEMA_VERSION,
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



def validate_trace_schema_selftest() -> JsonDict:
    """Validate legacy compatibility plus SkillsBench filesystem trace fields."""

    errors: list[str] = []

    legacy = EpisodeTrace(task_id="legacy", track="openenv", status="running")
    if legacy.task_id != "legacy" or legacy.track != "openenv":
        errors.append("legacy EpisodeTrace constructor compatibility failed")

    trace = EpisodeTrace.from_metadata(
        task_id="exceltable-in-ppt",
        track="benchflow-ai",
        status="running",
        metadata={
            "task_set": "standard-v1",
            "condition": "with_skills",
            "family": "office_pptx",
            "workspace_execution": {
                "version": "workspace-v",
                "ok": True,
                "status": "completed",
                "task_id": "exceltable-in-ppt",
                "family": "office_pptx",
                "workspace_visible": True,
                "wrote_any_file": True,
                "writes": [
                    {
                        "path": "/root/output/final_deck.pptx",
                        "ok": True,
                        "kind": "presentation",
                        "bytes_written": 128,
                        "sha256": "0" * 64,
                    }
                ],
                "diagnostics": {"selected_solver_key": "office_pptx"},
            },
        },
    )
    trace.add_workspace_execution(trace.metadata["workspace_execution"])
    trace.add_artifact_ref_diagnostic([{"name": "final_deck.pptx"}])
    trace.finish("completed")

    if trace.track != "skillsbench":
        errors.append(f"SkillsBench track did not normalize: {trace.track}")
    if trace.scoring_channel != "filesystem_output_primary":
        errors.append(f"unexpected scoring channel: {trace.scoring_channel}")
    if trace.artifact_refs_role != "diagnostic_and_compatibility_signal":
        errors.append(f"unexpected artifact refs role: {trace.artifact_refs_role}")
    if not trace.filesystem_primary:
        errors.append("filesystem_primary should be true for SkillsBench")
    if trace.output_family != "office_pptx":
        errors.append(f"unexpected output family: {trace.output_family}")
    if not trace.artifacts:
        errors.append("workspace artifacts were not recorded")
    if "filesystem-output-primary" not in trace.tags:
        errors.append("filesystem-output-primary tag missing")

    return {
        "ok": not errors,
        "errors": errors,
        "version": TRACE_SCHEMA_VERSION,
        "legacy": legacy.compact_dict(),
        "skillsbench": trace.compact_dict(),
    }


__all__ = [
    "TRACE_SCHEMA_VERSION",
    "TraceStep",
    "TraceArtifact",
    "EpisodeTrace",
    "validate_trace_schema_selftest",
]
