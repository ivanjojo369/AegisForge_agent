from __future__ import annotations

"""SkillsBench/AgentBeats forensic evidence models for AegisForge.

This module is intentionally offline and deterministic.  It does not call the
network, execute shell commands, or touch secrets.  Its job is to model and
reconcile three evidence surfaces that were observed to diverge in SkillsBench
AgentBeats runs:

1. What AegisForge emitted, including artifact refs, FilePart-like records,
   artifact_outputs, final contracts, and deliverables.
2. What AegisForge wrote into the visible task filesystem, including workspace
   executor writes and their hashes.
3. What the official gateway/worker/leaderboard result JSON reported, including
   passed/reward/score_eligible/infra_failure_type/error_type/artifact_refs.

The goal is not to prove that a task answer is correct.  The goal is to make
channel mismatches reproducible: artifact_refs dropped, filesystem outputs not
seen by the scorer, task identity hidden behind UUIDs, or result-shape drift.

Recommended use:

    from aegisforge.telemetry.skillsbench_forensics import (
        build_forensic_run,
        load_json_file,
        write_forensic_report,
    )

    official = load_json_file("results.json")
    contracts = [load_json_file("aegisforge_final_contract.json")]
    run = build_forensic_run(official_results=official, final_contracts=contracts)
    write_forensic_report(run, "skillsbench_forensics_report.json")
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import hashlib
import json
import mimetypes
import re


SKILLSBENCH_FORENSICS_VERSION = "skillsbench_forensics_v0_1_2026_06_09"

JsonDict = dict[str, Any]

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

SKILLSBENCH_PUBLIC_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9]+(?:-[a-z0-9]+)+$")


# ---------------------------------------------------------------------------
# Safe normalization helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_dict(value: Any) -> JsonDict:
    if value is None:
        return {}
    if is_dataclass(value):
        try:
            return dict(asdict(value))
        except Exception:
            return {}
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}

    for method_name in ("as_dict", "to_dict", "model_dump", "dict", "compact_dict"):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            dumped = method()
        except Exception:
            continue
        if isinstance(dumped, Mapping):
            return {str(k): v for k, v in dumped.items()}
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _as_str_list(value: Any) -> list[str]:
    output: list[str] = []
    for item in _as_list(value):
        text = _safe_text(item, limit=2000).strip()
        if text:
            output.append(text)
    return _dedupe(output)


def _safe_text(value: Any, *, limit: int = 200_000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:limit]
    except Exception:
        try:
            return str(value)[:limit]
        except Exception:
            return repr(value)[:limit]


def _first_text(*values: Any, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "ok", "pass", "passed", "success", "succeeded"}:
        return True
    if text in {"0", "false", "no", "n", "fail", "failed", "error", "errored", "null", "none"}:
        return False
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_json_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return _sha256_text(text)


def _guess_mime(path_or_name: str, fallback: str = "application/octet-stream") -> str:
    guessed = mimetypes.guess_type(path_or_name or "")[0]
    return guessed or fallback


def _looks_like_uuid(value: Any) -> bool:
    return bool(UUID_RE.match(str(value or "").strip()))


def _looks_like_public_task_id(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text or _looks_like_uuid(text):
        return False
    return bool(SKILLSBENCH_PUBLIC_TASK_ID_RE.match(text))


def _nested(mapping: Mapping[str, Any], *keys: str) -> JsonDict:
    for key in keys:
        data = _to_dict(mapping.get(key))
        if data:
            return data
    return {}


def _maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


# ---------------------------------------------------------------------------
# Evidence models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SkillsBenchArtifactEvidence:
    """One artifact/ref/file evidence item emitted by AegisForge."""

    name: str = ""
    source: str = "unknown"
    role: str = "artifact"
    path: str = ""
    uri: str = ""
    artifact_ref: str = ""
    mime_type: str = ""
    sha256: str = ""
    size_bytes: int = 0
    status: str = "observed"
    metadata: JsonDict = field(default_factory=dict)

    @property
    def identity_key(self) -> str:
        return _first_text(self.sha256, self.artifact_ref, self.uri, self.path, self.name, default="unknown")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, source: str = "unknown", role: str = "artifact") -> "SkillsBenchArtifactEvidence":
        data = _to_dict(value)
        name = _first_text(data.get("name"), data.get("artifact_name"), data.get("filename"), data.get("file_name"))
        path = _first_text(data.get("path"), data.get("absolute_path"), data.get("local_path"), data.get("relative_path"))
        uri = _first_text(data.get("uri"), data.get("artifact_uri"), data.get("url"), data.get("href"))
        artifact_ref = _first_text(
            data.get("artifact_ref"),
            data.get("artifact_uri"),
            data.get("ref"),
            data.get("id"),
            uri,
        )
        mime_type = _first_text(data.get("mime_type"), data.get("content_type"), default=_guess_mime(name or path or uri))
        return cls(
            name=name or Path(path).name or Path(uri).name or "artifact",
            source=source,
            role=_first_text(data.get("role"), default=role),
            path=path,
            uri=uri,
            artifact_ref=artifact_ref,
            mime_type=mime_type,
            sha256=_first_text(data.get("sha256"), data.get("digest"), data.get("hash")),
            size_bytes=_as_int(data.get("size_bytes", data.get("bytes_written", data.get("size"))), default=0),
            status=_first_text(data.get("status"), default="observed"),
            metadata={k: v for k, v in data.items() if k not in _ARTIFACT_RESERVED_KEYS},
        )

    @classmethod
    def from_string(cls, value: str, *, source: str = "artifact_refs_candidate") -> "SkillsBenchArtifactEvidence":
        text = str(value or "").strip()
        return cls(
            name=Path(text).name or text or "artifact_ref",
            source=source,
            role="artifact_ref",
            path=text if text.startswith("/") else "",
            uri=text if "://" in text else "",
            artifact_ref=text,
            mime_type=_guess_mime(text),
            metadata={},
        )

    def as_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "source": self.source,
            "role": self.role,
            "path": self.path,
            "uri": self.uri,
            "artifact_ref": self.artifact_ref,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "metadata": dict(self.metadata),
        }


_ARTIFACT_RESERVED_KEYS = {
    "name",
    "artifact_name",
    "filename",
    "file_name",
    "path",
    "absolute_path",
    "local_path",
    "relative_path",
    "uri",
    "artifact_uri",
    "url",
    "href",
    "artifact_ref",
    "ref",
    "id",
    "mime_type",
    "content_type",
    "sha256",
    "digest",
    "hash",
    "size_bytes",
    "bytes_written",
    "size",
    "role",
    "status",
}


@dataclass(slots=True)
class SkillsBenchWorkspaceWriteEvidence:
    """One write reported by task_workspace_executor or a similar surface."""

    path: str
    ok: bool
    action: str = "write"
    kind: str = "unknown"
    bytes_written: int = 0
    sha256: str = ""
    skipped: bool = False
    reason: str = ""
    error: str = ""
    parent_created: bool = False
    existed_before: bool = False
    source: str = "task_workspace_executor"
    metadata: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, source: str = "task_workspace_executor") -> "SkillsBenchWorkspaceWriteEvidence":
        data = _to_dict(value)
        return cls(
            path=_first_text(data.get("path"), data.get("absolute_path"), data.get("local_path")),
            ok=_as_bool(data.get("ok"), default=False),
            action=_first_text(data.get("action"), default="write"),
            kind=_first_text(data.get("kind"), default="unknown"),
            bytes_written=_as_int(data.get("bytes_written"), default=0),
            sha256=_first_text(data.get("sha256"), data.get("digest"), data.get("hash")),
            skipped=_as_bool(data.get("skipped"), default=False),
            reason=_first_text(data.get("reason")),
            error=_first_text(data.get("error")),
            parent_created=_as_bool(data.get("parent_created"), default=False),
            existed_before=_as_bool(data.get("existed_before"), default=False),
            source=source,
            metadata={k: v for k, v in data.items() if k not in _WRITE_RESERVED_KEYS},
        )

    def to_artifact_evidence(self) -> SkillsBenchArtifactEvidence:
        return SkillsBenchArtifactEvidence(
            name=Path(self.path).name or "workspace_write",
            source=self.source,
            role="filesystem_write",
            path=self.path,
            mime_type=_guess_mime(self.path),
            sha256=self.sha256,
            size_bytes=self.bytes_written,
            status="ok" if self.ok else "failed",
            metadata={
                "action": self.action,
                "kind": self.kind,
                "skipped": self.skipped,
                "reason": self.reason,
                "error": self.error,
                **dict(self.metadata),
            },
        )

    def as_dict(self) -> JsonDict:
        return {
            "path": self.path,
            "ok": self.ok,
            "action": self.action,
            "kind": self.kind,
            "bytes_written": self.bytes_written,
            "sha256": self.sha256,
            "skipped": self.skipped,
            "reason": self.reason,
            "error": self.error,
            "parent_created": self.parent_created,
            "existed_before": self.existed_before,
            "source": self.source,
            "metadata": dict(self.metadata),
        }


_WRITE_RESERVED_KEYS = {
    "path",
    "absolute_path",
    "local_path",
    "ok",
    "action",
    "kind",
    "bytes_written",
    "sha256",
    "digest",
    "hash",
    "skipped",
    "reason",
    "error",
    "parent_created",
    "existed_before",
}


@dataclass(slots=True)
class SkillsBenchOfficialResultRow:
    """Normalized row from official SkillsBench/AgentBeats result JSON."""

    task_id: str = ""
    trial_id: str = ""
    task_set: str = ""
    task_set_digest: str = ""
    condition: str = ""
    score_eligible: bool = False
    passed: bool = False
    reward: float = 0.0
    max_score: float = 0.0
    time_used: float = 0.0
    task_digest: str = ""
    category: str = ""
    difficulty: str = ""
    tags: tuple[str, ...] = ()
    has_skills: bool = False
    agent_transport: str = ""
    participant_role: str = ""
    infra_failure_type: str = ""
    error_type: str = ""
    artifact_refs: tuple[str, ...] = ()
    shard_index: int = -1
    row_index: int = -1
    status: str = ""
    raw: JsonDict = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        row: Mapping[str, Any],
        *,
        shard_index: int = -1,
        row_index: int = -1,
        shard_status: str = "",
    ) -> "SkillsBenchOfficialResultRow":
        data = _to_dict(row)
        artifact_refs = _extract_artifact_ref_strings(data.get("artifact_refs"))
        return cls(
            task_id=_first_text(data.get("task_id"), data.get("id")),
            trial_id=_first_text(data.get("trial_id")),
            task_set=_first_text(data.get("task_set")),
            task_set_digest=_first_text(data.get("task_set_digest")),
            condition=_first_text(data.get("condition")),
            score_eligible=_as_bool(data.get("score_eligible"), default=False),
            passed=_as_bool(data.get("passed"), default=False),
            reward=_as_float(data.get("reward"), default=0.0),
            max_score=_as_float(data.get("max_score"), default=0.0),
            time_used=_as_float(data.get("time_used"), default=0.0),
            task_digest=_first_text(data.get("task_digest")),
            category=_first_text(data.get("category")),
            difficulty=_first_text(data.get("difficulty")),
            tags=tuple(_as_str_list(data.get("tags"))),
            has_skills=_as_bool(data.get("has_skills"), default=False),
            agent_transport=_first_text(data.get("agent_transport")),
            participant_role=_first_text(data.get("participant_role")),
            infra_failure_type=_first_text(data.get("infra_failure_type")),
            error_type=_first_text(data.get("error_type")),
            artifact_refs=tuple(artifact_refs),
            shard_index=shard_index,
            row_index=row_index,
            status=_first_text(data.get("status"), default=shard_status),
            raw=data,
        )

    @property
    def artifact_ref_count(self) -> int:
        return len(self.artifact_refs)

    @property
    def has_infra_failure(self) -> bool:
        return bool(self.infra_failure_type or self.error_type or not self.score_eligible)

    def as_dict(self) -> JsonDict:
        return {
            "task_id": self.task_id,
            "trial_id": self.trial_id,
            "task_set": self.task_set,
            "task_set_digest": self.task_set_digest,
            "condition": self.condition,
            "score_eligible": self.score_eligible,
            "passed": self.passed,
            "reward": self.reward,
            "max_score": self.max_score,
            "time_used": self.time_used,
            "task_digest": self.task_digest,
            "category": self.category,
            "difficulty": self.difficulty,
            "tags": list(self.tags),
            "has_skills": self.has_skills,
            "agent_transport": self.agent_transport,
            "participant_role": self.participant_role,
            "infra_failure_type": self.infra_failure_type,
            "error_type": self.error_type,
            "artifact_refs": list(self.artifact_refs),
            "artifact_ref_count": self.artifact_ref_count,
            "shard_index": self.shard_index,
            "row_index": self.row_index,
            "status": self.status,
        }


@dataclass(slots=True)
class SkillsBenchArtifactSurvivalReport:
    """Comparison of AegisForge-emitted refs vs official refs."""

    emitted_count: int
    official_count: int
    emitted_keys: tuple[str, ...] = ()
    official_keys: tuple[str, ...] = ()
    matched_keys: tuple[str, ...] = ()
    dropped_keys: tuple[str, ...] = ()
    official_extra_keys: tuple[str, ...] = ()
    status: str = "unknown"

    @classmethod
    def build(
        cls,
        emitted: Sequence[SkillsBenchArtifactEvidence],
        official_refs: Sequence[str],
    ) -> "SkillsBenchArtifactSurvivalReport":
        emitted_keys = _dedupe(item.identity_key for item in emitted if item.identity_key and item.identity_key != "unknown")
        official_keys = _dedupe(str(item).strip() for item in official_refs if str(item).strip())

        # Direct set matching is conservative.  It also checks substring matches
        # because official refs are sometimes URIs while local refs are paths or
        # artifact:// identifiers.
        matched: list[str] = []
        dropped: list[str] = []
        for key in emitted_keys:
            if _key_matches_any(key, official_keys):
                matched.append(key)
            else:
                dropped.append(key)

        official_extra = [key for key in official_keys if not _key_matches_any(key, emitted_keys)]

        if not emitted_keys and not official_keys:
            status = "no_refs_on_either_side"
        elif emitted_keys and not official_keys:
            status = "emitted_refs_dropped"
        elif official_keys and not emitted_keys:
            status = "official_refs_without_local_evidence"
        elif dropped:
            status = "partial_ref_survival"
        else:
            status = "refs_survived"

        return cls(
            emitted_count=len(emitted_keys),
            official_count=len(official_keys),
            emitted_keys=tuple(emitted_keys),
            official_keys=tuple(official_keys),
            matched_keys=tuple(_dedupe(matched)),
            dropped_keys=tuple(_dedupe(dropped)),
            official_extra_keys=tuple(_dedupe(official_extra)),
            status=status,
        )

    def as_dict(self) -> JsonDict:
        return {
            "status": self.status,
            "emitted_count": self.emitted_count,
            "official_count": self.official_count,
            "emitted_keys": list(self.emitted_keys),
            "official_keys": list(self.official_keys),
            "matched_keys": list(self.matched_keys),
            "dropped_keys": list(self.dropped_keys),
            "official_extra_keys": list(self.official_extra_keys),
        }


@dataclass(slots=True)
class SkillsBenchFilesystemVisibilityReport:
    """Summary of filesystem writes vs official scoring."""

    workspace_visible: bool = False
    wrote_any_file: bool = False
    write_count: int = 0
    ok_write_count: int = 0
    failed_write_count: int = 0
    primary_output_paths: tuple[str, ...] = ()
    scored_as_passed: bool = False
    official_reward: float = 0.0
    status: str = "unknown"

    @classmethod
    def build(
        cls,
        writes: Sequence[SkillsBenchWorkspaceWriteEvidence],
        *,
        workspace_visible: bool = False,
        official_row: SkillsBenchOfficialResultRow | None = None,
        primary_output_paths: Sequence[str] | None = None,
    ) -> "SkillsBenchFilesystemVisibilityReport":
        ok_writes = [item for item in writes if item.ok and not item.skipped]
        failed_writes = [item for item in writes if not item.ok and not item.skipped]
        wrote_any_file = bool(ok_writes)
        passed = bool(official_row.passed) if official_row is not None else False
        reward = float(official_row.reward) if official_row is not None else 0.0

        if wrote_any_file and official_row is not None and not passed and reward <= 0:
            status = "filesystem_outputs_not_scored"
        elif wrote_any_file and official_row is not None and passed:
            status = "filesystem_outputs_scored_pass"
        elif not workspace_visible:
            status = "workspace_not_visible"
        elif not wrote_any_file:
            status = "no_files_written"
        else:
            status = "filesystem_outputs_observed"

        return cls(
            workspace_visible=workspace_visible,
            wrote_any_file=wrote_any_file,
            write_count=len(writes),
            ok_write_count=len(ok_writes),
            failed_write_count=len(failed_writes),
            primary_output_paths=tuple(_dedupe(primary_output_paths or [item.path for item in ok_writes])),
            scored_as_passed=passed,
            official_reward=reward,
            status=status,
        )

    def as_dict(self) -> JsonDict:
        return {
            "status": self.status,
            "workspace_visible": self.workspace_visible,
            "wrote_any_file": self.wrote_any_file,
            "write_count": self.write_count,
            "ok_write_count": self.ok_write_count,
            "failed_write_count": self.failed_write_count,
            "primary_output_paths": list(self.primary_output_paths),
            "scored_as_passed": self.scored_as_passed,
            "official_reward": self.official_reward,
        }


@dataclass(slots=True)
class SkillsBenchForensicRecord:
    """Per-task reconciliation record."""

    version: str = SKILLSBENCH_FORENSICS_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    task_id: str = ""
    canonical_task_id: str = ""
    request_task_id: str = ""
    trial_id: str = ""
    task_digest: str = ""
    category: str = ""
    difficulty: str = ""
    condition: str = ""
    family: str = ""
    identity_confidence: float = 0.0
    identity_source: str = ""
    official: SkillsBenchOfficialResultRow | None = None
    emitted_artifacts: tuple[SkillsBenchArtifactEvidence, ...] = ()
    workspace_writes: tuple[SkillsBenchWorkspaceWriteEvidence, ...] = ()
    final_contract: JsonDict = field(default_factory=dict)
    workspace_execution: JsonDict = field(default_factory=dict)
    diagnostics: JsonDict = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def effective_task_id(self) -> str:
        return _first_text(self.canonical_task_id, self.task_id, self.request_task_id, self.trial_id, default="unknown")

    @property
    def emitted_artifact_count(self) -> int:
        return len(self.emitted_artifacts)

    @property
    def workspace_write_count(self) -> int:
        return len(self.workspace_writes)

    @property
    def ok_workspace_write_count(self) -> int:
        return sum(1 for item in self.workspace_writes if item.ok and not item.skipped)

    @property
    def official_artifact_ref_count(self) -> int:
        return self.official.artifact_ref_count if self.official else 0

    @property
    def official_passed(self) -> bool:
        return bool(self.official and self.official.passed)

    @property
    def official_reward(self) -> float:
        return float(self.official.reward) if self.official else 0.0

    @property
    def official_score_eligible(self) -> bool:
        return bool(self.official and self.official.score_eligible)

    def artifact_survival(self) -> SkillsBenchArtifactSurvivalReport:
        return SkillsBenchArtifactSurvivalReport.build(
            self.emitted_artifacts,
            list(self.official.artifact_refs) if self.official else [],
        )

    def filesystem_visibility(self) -> SkillsBenchFilesystemVisibilityReport:
        workspace_visible = _as_bool(self.workspace_execution.get("workspace_visible"), default=False)
        primary_outputs = _as_str_list(self.workspace_execution.get("primary_outputs"))
        if not primary_outputs:
            primary_outputs = _as_str_list(_nested(self.workspace_execution, "diagnostics").get("primary_outputs"))
        return SkillsBenchFilesystemVisibilityReport.build(
            self.workspace_writes,
            workspace_visible=workspace_visible,
            official_row=self.official,
            primary_output_paths=primary_outputs,
        )

    def anomaly_codes(self) -> list[str]:
        codes: list[str] = []
        official = self.official
        survival = self.artifact_survival()
        fs = self.filesystem_visibility()

        if self.request_task_id and _looks_like_uuid(self.request_task_id) and not _looks_like_public_task_id(self.canonical_task_id):
            codes.append("task_identity_unresolved")
        if self.identity_confidence <= 0 and not _looks_like_public_task_id(self.canonical_task_id):
            codes.append("identity_confidence_zero")
        if survival.status == "emitted_refs_dropped":
            codes.append("artifact_refs_dropped")
        elif survival.status == "partial_ref_survival":
            codes.append("artifact_refs_partially_dropped")
        if fs.status == "filesystem_outputs_not_scored":
            codes.append("filesystem_outputs_not_scored")
        if fs.status == "workspace_not_visible":
            codes.append("filesystem_not_visible")
        if official is not None:
            if official.score_eligible and not official.passed and official.reward <= 0:
                codes.append("official_result_zeroed")
            if not official.score_eligible:
                codes.append("score_ineligible")
            if official.infra_failure_type:
                codes.append("infra_failure")
            if official.error_type:
                codes.append("official_error_type_present")
            if "timeout" in f"{official.infra_failure_type} {official.error_type}".lower():
                codes.append("worker_timeout")
        else:
            codes.append("official_row_missing")

        if self.emitted_artifact_count == 0 and self.ok_workspace_write_count == 0:
            codes.append("no_local_output_evidence")

        return _dedupe(codes)

    def status(self) -> str:
        codes = set(self.anomaly_codes())
        if not codes:
            return "clean"
        if "artifact_refs_dropped" in codes or "filesystem_outputs_not_scored" in codes:
            return "channel_mismatch"
        if "official_result_zeroed" in codes:
            return "official_zero"
        if "official_row_missing" in codes:
            return "missing_official_result"
        if "task_identity_unresolved" in codes:
            return "identity_problem"
        return "anomalies_detected"

    def as_dict(self, *, include_raw: bool = False) -> JsonDict:
        data: JsonDict = {
            "version": self.version,
            "created_at": self.created_at,
            "status": self.status(),
            "task_id": self.task_id,
            "canonical_task_id": self.canonical_task_id,
            "request_task_id": self.request_task_id,
            "effective_task_id": self.effective_task_id,
            "trial_id": self.trial_id,
            "task_digest": self.task_digest,
            "category": self.category,
            "difficulty": self.difficulty,
            "condition": self.condition,
            "family": self.family,
            "identity_confidence": self.identity_confidence,
            "identity_source": self.identity_source,
            "official": self.official.as_dict() if self.official else None,
            "artifact_survival": self.artifact_survival().as_dict(),
            "filesystem_visibility": self.filesystem_visibility().as_dict(),
            "emitted_artifact_count": self.emitted_artifact_count,
            "workspace_write_count": self.workspace_write_count,
            "ok_workspace_write_count": self.ok_workspace_write_count,
            "official_artifact_ref_count": self.official_artifact_ref_count,
            "official_passed": self.official_passed,
            "official_reward": self.official_reward,
            "official_score_eligible": self.official_score_eligible,
            "anomaly_codes": self.anomaly_codes(),
            "emitted_artifacts": [item.as_dict() for item in self.emitted_artifacts],
            "workspace_writes": [item.as_dict() for item in self.workspace_writes],
            "diagnostics": dict(self.diagnostics),
            "warnings": list(self.warnings),
        }
        if include_raw:
            data["final_contract"] = dict(self.final_contract)
            data["workspace_execution"] = dict(self.workspace_execution)
            if self.official:
                data["official_raw"] = dict(self.official.raw)
        return data


@dataclass(slots=True)
class SkillsBenchRunForensics:
    """Whole-run forensic report."""

    version: str = SKILLSBENCH_FORENSICS_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    run_id: str = ""
    source: str = "local"
    official_shape: str = "unknown"
    records: tuple[SkillsBenchForensicRecord, ...] = ()
    metadata: JsonDict = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def task_count(self) -> int:
        return len(self.records)

    @property
    def passed_count(self) -> int:
        return sum(1 for item in self.records if item.official_passed)

    @property
    def zeroed_count(self) -> int:
        return sum(1 for item in self.records if "official_result_zeroed" in item.anomaly_codes())

    @property
    def artifact_refs_dropped_count(self) -> int:
        return sum(1 for item in self.records if "artifact_refs_dropped" in item.anomaly_codes())

    @property
    def filesystem_not_scored_count(self) -> int:
        return sum(1 for item in self.records if "filesystem_outputs_not_scored" in item.anomaly_codes())

    @property
    def identity_problem_count(self) -> int:
        return sum(
            1
            for item in self.records
            if "task_identity_unresolved" in item.anomaly_codes() or "identity_confidence_zero" in item.anomaly_codes()
        )

    def anomaly_counts(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for record in self.records:
            counter.update(record.anomaly_codes())
        return dict(sorted(counter.items()))

    def status_counts(self) -> dict[str, int]:
        counter: Counter[str] = Counter(record.status() for record in self.records)
        return dict(sorted(counter.items()))

    def summary(self) -> JsonDict:
        reward_values = [record.official_reward for record in self.records if record.official is not None]
        total_reward = round(sum(reward_values), 6)
        return {
            "version": self.version,
            "created_at": self.created_at,
            "run_id": self.run_id,
            "source": self.source,
            "official_shape": self.official_shape,
            "task_count": self.task_count,
            "passed_count": self.passed_count,
            "pass_rate": round(self.passed_count / self.task_count, 6) if self.task_count else 0.0,
            "total_reward": total_reward,
            "mean_reward": round(total_reward / self.task_count, 6) if self.task_count else 0.0,
            "zeroed_count": self.zeroed_count,
            "artifact_refs_dropped_count": self.artifact_refs_dropped_count,
            "filesystem_not_scored_count": self.filesystem_not_scored_count,
            "identity_problem_count": self.identity_problem_count,
            "status_counts": self.status_counts(),
            "anomaly_counts": self.anomaly_counts(),
            "warnings": list(self.warnings),
        }

    def as_dict(self, *, include_raw: bool = False) -> JsonDict:
        return {
            **self.summary(),
            "metadata": dict(self.metadata),
            "records": [item.as_dict(include_raw=include_raw) for item in self.records],
        }


# ---------------------------------------------------------------------------
# Official result JSON shape handling
# ---------------------------------------------------------------------------


def detect_official_result_shape(payload: Any) -> str:
    """Classify AgentBeats result JSON shape.

    Known shapes:
    - nested_shard: top-level results is a list of shard objects, each with
      its own results list.
    - legacy_flat: top-level results is a list of direct task rows.
    - mixed: results contains both shard wrappers and direct rows.
    - empty: no rows.
    - invalid: not a recognizable result payload.
    """

    data = _to_dict(payload)
    results = data.get("results")
    if not isinstance(results, list):
        return "invalid"
    if not results:
        return "empty"

    nested = 0
    flat = 0
    other = 0
    for item in results:
        row = _to_dict(item)
        if not row:
            other += 1
            continue
        if isinstance(row.get("results"), list):
            nested += 1
        elif "task_id" in row or "trial_id" in row or "reward" in row or "passed" in row:
            flat += 1
        else:
            other += 1

    if nested and not flat and not other:
        return "nested_shard"
    if flat and not nested and not other:
        return "legacy_flat"
    if nested or flat:
        return "mixed"
    return "invalid"


def normalize_official_result_rows(payload: Any) -> list[SkillsBenchOfficialResultRow]:
    data = _to_dict(payload)
    results = data.get("results")
    if not isinstance(results, list):
        return []

    rows: list[SkillsBenchOfficialResultRow] = []
    for outer_index, shard_or_row in enumerate(results):
        outer = _to_dict(shard_or_row)
        if not outer:
            continue

        nested = outer.get("results")
        if isinstance(nested, list):
            shard_status = _first_text(outer.get("status"))
            for row_index, row in enumerate(nested):
                row_data = _to_dict(row)
                if row_data:
                    rows.append(
                        SkillsBenchOfficialResultRow.from_mapping(
                            row_data,
                            shard_index=outer_index,
                            row_index=row_index,
                            shard_status=shard_status,
                        )
                    )
        elif "task_id" in outer or "trial_id" in outer or "reward" in outer or "passed" in outer:
            rows.append(
                SkillsBenchOfficialResultRow.from_mapping(
                    outer,
                    shard_index=-1,
                    row_index=outer_index,
                    shard_status=_first_text(data.get("status")),
                )
            )
    return rows


def official_rows_by_task(rows: Sequence[SkillsBenchOfficialResultRow]) -> dict[str, list[SkillsBenchOfficialResultRow]]:
    grouped: dict[str, list[SkillsBenchOfficialResultRow]] = defaultdict(list)
    for row in rows:
        for key in _official_row_keys(row):
            grouped[key].append(row)
    return dict(grouped)


def _official_row_keys(row: SkillsBenchOfficialResultRow) -> list[str]:
    keys = [row.task_id, row.trial_id, row.task_digest]
    if row.trial_id and "__" in row.trial_id:
        keys.append(row.trial_id.split("__", 1)[0])
    return _dedupe(keys)


# ---------------------------------------------------------------------------
# AegisForge evidence extraction
# ---------------------------------------------------------------------------


def collect_artifact_evidence_from_contract(contract: Any) -> list[SkillsBenchArtifactEvidence]:
    """Extract artifact evidence from an AegisForge final contract/response.

    This function accepts several shapes observed in AegisForge surfaces:
    - artifact_refs_candidate: list[str|dict]
    - artifact_refs: list[str|dict]
    - artifact_outputs: list[dict]
    - artifacts: list[dict]
    - files: list[dict]
    - deliverables: list[dict]
    - result_payload.artifact_refs: list[str|dict]
    """

    data = _to_dict(_maybe_json(contract))
    if not data:
        return []

    evidence: list[SkillsBenchArtifactEvidence] = []
    source_fields = (
        ("artifact_refs_candidate", "artifact_refs_candidate", "artifact_ref"),
        ("artifact_refs", "artifact_refs", "artifact_ref"),
        ("artifact_outputs", "artifact_outputs", "artifact_output"),
        ("artifacts", "artifacts", "artifact"),
        ("files", "files", "file"),
        ("deliverables", "deliverables", "deliverable"),
    )
    for field_name, source, role in source_fields:
        evidence.extend(_evidence_from_sequence(data.get(field_name), source=source, role=role))

    payload = _nested(data, "result_payload", "payload")
    if payload:
        evidence.extend(_evidence_from_sequence(payload.get("artifact_refs"), source="result_payload.artifact_refs", role="artifact_ref"))
        evidence.extend(_evidence_from_sequence(payload.get("files"), source="result_payload.files", role="file"))
        evidence.extend(_evidence_from_sequence(payload.get("artifacts"), source="result_payload.artifacts", role="artifact"))

    # Some final_text values are JSON strings containing the same surface.
    final_text = data.get("final_text")
    if isinstance(final_text, str):
        parsed = _maybe_json(final_text)
        if isinstance(parsed, Mapping):
            evidence.extend(collect_artifact_evidence_from_contract(parsed))

    return _dedupe_artifact_evidence(evidence)


def collect_workspace_writes_from_execution(execution: Any) -> list[SkillsBenchWorkspaceWriteEvidence]:
    data = _to_dict(_maybe_json(execution))
    if not data:
        return []

    writes: list[SkillsBenchWorkspaceWriteEvidence] = []
    for item in _as_list(data.get("writes")):
        row = _to_dict(item)
        if row:
            writes.append(SkillsBenchWorkspaceWriteEvidence.from_mapping(row))

    diagnostics = _nested(data, "diagnostics")
    for key in ("writes", "workspace_writes", "write_results"):
        for item in _as_list(diagnostics.get(key)):
            row = _to_dict(item)
            if row:
                writes.append(SkillsBenchWorkspaceWriteEvidence.from_mapping(row, source=f"diagnostics.{key}"))

    return _dedupe_workspace_writes(writes)


def collect_workspace_execution_from_contract(contract: Any) -> JsonDict:
    data = _to_dict(_maybe_json(contract))
    if not data:
        return {}

    for key in (
        "workspace_execution",
        "task_workspace_execution",
        "workspace_executor",
        "filesystem_execution",
    ):
        nested = _to_dict(data.get(key))
        if nested:
            return nested

    diagnostics = _nested(data, "diagnostics")
    for key in (
        "workspace_execution",
        "task_workspace_execution",
        "workspace_executor",
        "filesystem_execution",
    ):
        nested = _to_dict(diagnostics.get(key))
        if nested:
            return nested

    return {}


def extract_identity_from_contract(contract: Any) -> JsonDict:
    data = _to_dict(_maybe_json(contract))
    if not data:
        return {}

    request = _nested(data, "request")
    plan = _nested(data, "plan")
    diagnostics = _nested(data, "diagnostics")
    environment = _nested(data, "environment", "task_environment")
    identity = _nested(data, "identity", "task_identity")

    return {
        "task_id": _first_text(data.get("task_id"), request.get("task_id"), plan.get("task_id"), identity.get("task_id")),
        "canonical_task_id": _first_text(
            data.get("canonical_task_id"),
            request.get("canonical_task_id"),
            diagnostics.get("canonical_task_id"),
            identity.get("canonical_task_id"),
            environment.get("canonical_task_id"),
        ),
        "request_task_id": _first_text(
            data.get("request_task_id"),
            request.get("request_task_id"),
            request.get("task_id"),
            diagnostics.get("request_task_id"),
            environment.get("request_task_id"),
        ),
        "trial_id": _first_text(data.get("trial_id"), request.get("trial_id"), diagnostics.get("trial_id")),
        "task_digest": _first_text(data.get("task_digest"), request.get("task_digest"), diagnostics.get("task_digest")),
        "category": _first_text(data.get("category"), request.get("category"), plan.get("category")),
        "difficulty": _first_text(data.get("difficulty"), request.get("difficulty"), plan.get("difficulty")),
        "condition": _first_text(data.get("condition"), request.get("condition")),
        "family": _first_text(data.get("family"), plan.get("family"), request.get("family")),
        "identity_confidence": _as_float(
            data.get("identity_confidence", diagnostics.get("identity_confidence", environment.get("identity_confidence"))),
            default=0.0,
        ),
        "identity_source": _first_text(
            data.get("identity_source"),
            diagnostics.get("identity_source"),
            environment.get("identity_source"),
            identity.get("source"),
        ),
    }


def _evidence_from_sequence(value: Any, *, source: str, role: str) -> list[SkillsBenchArtifactEvidence]:
    output: list[SkillsBenchArtifactEvidence] = []
    for item in _as_list(value):
        item = _maybe_json(item)
        if isinstance(item, Mapping):
            output.append(SkillsBenchArtifactEvidence.from_mapping(item, source=source, role=role))
        elif isinstance(item, str) and item.strip():
            output.append(SkillsBenchArtifactEvidence.from_string(item, source=source))
    return output


def _extract_artifact_ref_strings(value: Any) -> list[str]:
    output: list[str] = []
    for item in _as_list(value):
        item = _maybe_json(item)
        if isinstance(item, Mapping):
            text = _first_text(
                item.get("artifact_ref"),
                item.get("artifact_uri"),
                item.get("uri"),
                item.get("path"),
                item.get("name"),
                item.get("id"),
            )
        else:
            text = str(item or "").strip()
        if text:
            output.append(text)
    return _dedupe(output)


def _dedupe_artifact_evidence(items: Sequence[SkillsBenchArtifactEvidence]) -> list[SkillsBenchArtifactEvidence]:
    seen: set[str] = set()
    output: list[SkillsBenchArtifactEvidence] = []
    for item in items:
        key = item.identity_key
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _dedupe_workspace_writes(items: Sequence[SkillsBenchWorkspaceWriteEvidence]) -> list[SkillsBenchWorkspaceWriteEvidence]:
    seen: set[tuple[str, str, str]] = set()
    output: list[SkillsBenchWorkspaceWriteEvidence] = []
    for item in items:
        key = (item.path, item.sha256, item.action)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _key_matches_any(key: str, candidates: Sequence[str]) -> bool:
    cleaned = str(key or "").strip()
    if not cleaned:
        return False
    for candidate in candidates:
        other = str(candidate or "").strip()
        if not other:
            continue
        if cleaned == other:
            return True
        if cleaned in other or other in cleaned:
            return True
    return False


# ---------------------------------------------------------------------------
# Reconciliation builders
# ---------------------------------------------------------------------------


def build_forensic_record(
    *,
    official_row: SkillsBenchOfficialResultRow | Mapping[str, Any] | None = None,
    final_contract: Any = None,
    workspace_execution: Any = None,
    emitted_artifacts: Sequence[SkillsBenchArtifactEvidence | Mapping[str, Any] | str] | None = None,
    workspace_writes: Sequence[SkillsBenchWorkspaceWriteEvidence | Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> SkillsBenchForensicRecord:
    metadata_dict = _to_dict(metadata)

    official: SkillsBenchOfficialResultRow | None
    if isinstance(official_row, SkillsBenchOfficialResultRow):
        official = official_row
    elif isinstance(official_row, Mapping):
        official = SkillsBenchOfficialResultRow.from_mapping(official_row)
    else:
        official = None

    contract_dict = _to_dict(_maybe_json(final_contract))
    identity = extract_identity_from_contract(contract_dict)

    execution_dict = _to_dict(_maybe_json(workspace_execution)) or collect_workspace_execution_from_contract(contract_dict)
    artifact_items = collect_artifact_evidence_from_contract(contract_dict)
    write_items = collect_workspace_writes_from_execution(execution_dict)

    for item in emitted_artifacts or []:
        item = _maybe_json(item)
        if isinstance(item, SkillsBenchArtifactEvidence):
            artifact_items.append(item)
        elif isinstance(item, Mapping):
            artifact_items.append(SkillsBenchArtifactEvidence.from_mapping(item, source="explicit"))
        elif isinstance(item, str):
            artifact_items.append(SkillsBenchArtifactEvidence.from_string(item, source="explicit"))

    for item in workspace_writes or []:
        if isinstance(item, SkillsBenchWorkspaceWriteEvidence):
            write_items.append(item)
        elif isinstance(item, Mapping):
            write_items.append(SkillsBenchWorkspaceWriteEvidence.from_mapping(item, source="explicit"))

    # Include filesystem writes as emitted artifact evidence.  This makes the
    # survival report represent both A2A refs and filesystem evidence.
    for write in write_items:
        if write.ok and not write.skipped:
            artifact_items.append(write.to_artifact_evidence())

    task_id = _first_text(
        metadata_dict.get("task_id"),
        identity.get("task_id"),
        official.task_id if official else "",
    )
    canonical_task_id = _first_text(
        metadata_dict.get("canonical_task_id"),
        identity.get("canonical_task_id"),
        official.task_id if official and _looks_like_public_task_id(official.task_id) else "",
    )
    request_task_id = _first_text(metadata_dict.get("request_task_id"), identity.get("request_task_id"), task_id)

    return SkillsBenchForensicRecord(
        task_id=task_id,
        canonical_task_id=canonical_task_id,
        request_task_id=request_task_id,
        trial_id=_first_text(metadata_dict.get("trial_id"), identity.get("trial_id"), official.trial_id if official else ""),
        task_digest=_first_text(metadata_dict.get("task_digest"), identity.get("task_digest"), official.task_digest if official else ""),
        category=_first_text(metadata_dict.get("category"), identity.get("category"), official.category if official else ""),
        difficulty=_first_text(metadata_dict.get("difficulty"), identity.get("difficulty"), official.difficulty if official else ""),
        condition=_first_text(metadata_dict.get("condition"), identity.get("condition"), official.condition if official else ""),
        family=_first_text(metadata_dict.get("family"), identity.get("family")),
        identity_confidence=_as_float(metadata_dict.get("identity_confidence", identity.get("identity_confidence")), default=0.0),
        identity_source=_first_text(metadata_dict.get("identity_source"), identity.get("identity_source")),
        official=official,
        emitted_artifacts=tuple(_dedupe_artifact_evidence(artifact_items)),
        workspace_writes=tuple(_dedupe_workspace_writes(write_items)),
        final_contract=contract_dict,
        workspace_execution=execution_dict,
        diagnostics=_to_dict(metadata_dict.get("diagnostics")),
        warnings=tuple(_as_str_list(metadata_dict.get("warnings"))),
    )


def build_forensic_run(
    *,
    official_results: Any,
    final_contracts: Sequence[Any] | None = None,
    workspace_executions: Sequence[Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    source: str = "local",
    run_id: str = "",
) -> SkillsBenchRunForensics:
    official_shape = detect_official_result_shape(official_results)
    official_rows = normalize_official_result_rows(official_results)
    rows_by_key = official_rows_by_task(official_rows)

    contract_list = list(final_contracts or [])
    execution_list = list(workspace_executions or [])
    used_official_rows: set[int] = set()
    records: list[SkillsBenchForensicRecord] = []

    for index, contract in enumerate(contract_list):
        identity = extract_identity_from_contract(contract)
        candidate_keys = _dedupe(
            [
                identity.get("canonical_task_id", ""),
                identity.get("task_id", ""),
                identity.get("request_task_id", ""),
                identity.get("trial_id", ""),
                identity.get("task_digest", ""),
            ]
        )
        official = _find_best_official_row(candidate_keys, rows_by_key)
        if official is not None:
            used_official_rows.add(id(official))
        execution = execution_list[index] if index < len(execution_list) else None
        records.append(
            build_forensic_record(
                official_row=official,
                final_contract=contract,
                workspace_execution=execution,
                metadata={"contract_index": index},
            )
        )

    # Preserve official rows that had no local contract match.  This matters for
    # whole-run post-mortems where official JSON has 94 rows but local evidence
    # only covered a subset of tasks.
    for row in official_rows:
        if id(row) in used_official_rows:
            continue
        records.append(build_forensic_record(official_row=row, metadata={"source": "official_only"}))

    meta = _to_dict(metadata)
    return SkillsBenchRunForensics(
        run_id=_first_text(run_id, meta.get("run_id"), _extract_run_id_from_results(official_results)),
        source=source,
        official_shape=official_shape,
        records=tuple(records),
        metadata=meta,
        warnings=tuple(_run_warnings(official_shape=official_shape, records=records)),
    )


def _find_best_official_row(
    candidate_keys: Sequence[str],
    rows_by_key: Mapping[str, Sequence[SkillsBenchOfficialResultRow]],
) -> SkillsBenchOfficialResultRow | None:
    for key in candidate_keys:
        if not key:
            continue
        exact = rows_by_key.get(key)
        if exact:
            return exact[0]

    # Trial IDs often include the task id as prefix.  Fall back to substring
    # matching, but prefer public-looking task IDs over UUID-like request ids.
    ordered = sorted(candidate_keys, key=lambda k: (0 if _looks_like_public_task_id(k) else 1, -len(k)))
    for key in ordered:
        if not key:
            continue
        for row_keys, rows in rows_by_key.items():
            if key in row_keys or row_keys in key:
                return rows[0]
    return None


def _extract_run_id_from_results(payload: Any) -> str:
    data = _to_dict(payload)
    for key in ("run_id", "submission_id", "id"):
        text = _first_text(data.get(key))
        if text:
            return text
    provenance = _nested(data, "provenance", "github_actions")
    return _first_text(provenance.get("run_id"), provenance.get("run_url"))


def _run_warnings(*, official_shape: str, records: Sequence[SkillsBenchForensicRecord]) -> list[str]:
    warnings: list[str] = []
    if official_shape in {"legacy_flat", "mixed", "invalid"}:
        warnings.append(f"official_result_shape_is_{official_shape}")
    if records and all(record.official is not None and record.official_reward <= 0 and not record.official_passed for record in records):
        warnings.append("all_official_rows_zero_reward")
    if any("artifact_refs_dropped" in record.anomaly_codes() for record in records):
        warnings.append("one_or_more_records_dropped_artifact_refs")
    if any("filesystem_outputs_not_scored" in record.anomaly_codes() for record in records):
        warnings.append("one_or_more_records_wrote_files_but_were_not_scored")
    return _dedupe(warnings)


# ---------------------------------------------------------------------------
# Tagged JSON extraction for log reconciliation tools
# ---------------------------------------------------------------------------


def extract_tagged_json_objects(text: str, marker: str) -> list[JsonDict]:
    """Extract JSON objects following a marker in line-oriented logs.

    The function is conservative: it attempts to parse balanced JSON objects on
    the same line after the marker.  It also accepts lines where the content after
    the marker is a JSON string that contains an object.
    """

    if not text or not marker:
        return []

    objects: list[JsonDict] = []
    for line in text.splitlines():
        if marker not in line:
            continue
        tail = line.split(marker, 1)[1].strip(" :-\t")
        if not tail:
            continue
        parsed = _parse_first_json_object(tail)
        if isinstance(parsed, Mapping):
            objects.append(_to_dict(parsed))
    return objects


def _parse_first_json_object(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    if text[0] not in "[{":
        first_obj = min([idx for idx in (text.find("{"), text.find("[")) if idx >= 0], default=-1)
        if first_obj < 0:
            return None
        text = text[first_obj:]
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(text)
        return value
    except Exception:
        return None


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def load_json_file(path: str | Path) -> Any:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def dump_json_file(payload: Any, path: str | Path, *, include_newline: bool = True) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if include_newline:
        text += "\n"
    p.write_text(text, encoding="utf-8")
    return p


def write_forensic_report(run: SkillsBenchRunForensics, path: str | Path, *, include_raw: bool = False) -> Path:
    return dump_json_file(run.as_dict(include_raw=include_raw), path)


def fingerprint_forensic_record(record: SkillsBenchForensicRecord) -> str:
    return _stable_json_hash(record.as_dict(include_raw=False))


def fingerprint_forensic_run(run: SkillsBenchRunForensics) -> str:
    return _stable_json_hash(run.as_dict(include_raw=False))


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------


def validate_skillsbench_forensics_selftest() -> JsonDict:
    official = {
        "status": "completed",
        "results": [
            {
                "status": "completed",
                "results": [
                    {
                        "task_id": "example-task",
                        "trial_id": "example-task__agentbeats__abc123",
                        "task_set": "standard-v1",
                        "condition": "with_skills",
                        "score_eligible": True,
                        "passed": False,
                        "reward": 0.0,
                        "max_score": 1.0,
                        "artifact_refs": [],
                    }
                ],
            }
        ],
    }
    contract = {
        "task_id": "example-task",
        "canonical_task_id": "example-task",
        "artifact_refs_candidate": ["artifact://example-task/answer.json"],
        "artifact_outputs": [
            {
                "name": "answer.json",
                "path": "/root/answer.json",
                "sha256": "abc",
                "size_bytes": 12,
                "mime_type": "application/json",
            }
        ],
        "workspace_execution": {
            "workspace_visible": True,
            "writes": [
                {
                    "path": "/root/answer.json",
                    "ok": True,
                    "kind": "json",
                    "bytes_written": 12,
                    "sha256": "abc",
                }
            ],
        },
    }
    run = build_forensic_run(official_results=official, final_contracts=[contract], source="selftest")
    errors: list[str] = []
    if run.official_shape != "nested_shard":
        errors.append(f"unexpected shape: {run.official_shape}")
    if run.task_count != 1:
        errors.append(f"unexpected task_count: {run.task_count}")
    if run.artifact_refs_dropped_count != 1:
        errors.append(f"expected artifact_refs_dropped_count=1, got {run.artifact_refs_dropped_count}")
    if run.filesystem_not_scored_count != 1:
        errors.append(f"expected filesystem_not_scored_count=1, got {run.filesystem_not_scored_count}")
    summary = run.summary()
    return {
        "ok": not errors,
        "version": SKILLSBENCH_FORENSICS_VERSION,
        "errors": errors,
        "summary": summary,
    }


__all__ = [
    "SKILLSBENCH_FORENSICS_VERSION",
    "SkillsBenchArtifactEvidence",
    "SkillsBenchWorkspaceWriteEvidence",
    "SkillsBenchOfficialResultRow",
    "SkillsBenchArtifactSurvivalReport",
    "SkillsBenchFilesystemVisibilityReport",
    "SkillsBenchForensicRecord",
    "SkillsBenchRunForensics",
    "build_forensic_record",
    "build_forensic_run",
    "collect_artifact_evidence_from_contract",
    "collect_workspace_execution_from_contract",
    "collect_workspace_writes_from_execution",
    "detect_official_result_shape",
    "dump_json_file",
    "extract_identity_from_contract",
    "extract_tagged_json_objects",
    "fingerprint_forensic_record",
    "fingerprint_forensic_run",
    "load_json_file",
    "normalize_official_result_rows",
    "official_rows_by_task",
    "utc_now_iso",
    "validate_skillsbench_forensics_selftest",
    "write_forensic_report",
]
