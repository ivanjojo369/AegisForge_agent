#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

"""Reconcile SkillsBench/AegisForge run logs against official results.

This tool is for local/offline post-mortem work. It compares what AegisForge
appears to have emitted during a SkillsBench run with what the official
AgentBeats/SkillsBench results JSON preserved.

Primary questions answered:
- Did AegisForge emit artifact_refs/artifact candidates, but official rows ended
  with artifact_refs == []?
- Did AegisForge write files through the task workspace executor, but official
  reward stayed 0?
- Are task identities unresolved because the request used UUID/session ids
  instead of public standard-v1 task ids?
- Is the results JSON flat, nested by shard, mixed, or malformed?

The script intentionally avoids network access, shell execution, secrets, and
platform mutation. It only reads local files and writes local reports.

Typical usage from the repo root:

    python tools/skillsbench_reconcile_run_logs.py ^
      --logs "artifacts/skillsbench/logs/*.txt" ^
      --results "results/019ead57-c76b-7ce1-93bc-2857c9652f35.json" ^
      --out "artifacts/skillsbench/reconcile_report.json" ^
      --markdown "artifacts/skillsbench/reconcile_report.md"

You can pass --log multiple times for explicit files, --logs multiple times for
globs/directories, or both.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import argparse
import ast
import glob
import json
import re
import time


TOOL_VERSION = "skillsbench_reconcile_run_logs_v0_1_2026_06_09"

MARKERS: tuple[str, ...] = (
    "SKILLSBENCH_FINAL_RESPONSE_CONTRACT",
    "AEGISFORGE_SKILLSBENCH_WORKSPACE_EXECUTOR",
    "direct_adapter_complete",
    "final_response_contract_emit",
    "skillsbench_direct_adapter_complete",
    "skillsbench_final_response_contract",
    "skillsbench_workspace_executor",
)

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

JSON_START_RE = re.compile(r"[\{\[]")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


JsonDict = dict[str, Any]


@dataclass(slots=True)
class OfficialResultRow:
    task_id: str
    task_digest: str = ""
    shard_index: int | None = None
    row_index: int | None = None
    passed: bool = False
    reward: float = 0.0
    score_eligible: bool = False
    artifact_refs_count: int = 0
    time_used: float | None = None
    infra_failure_type: str | None = None
    error_type: str | None = None
    condition: str = ""
    agent_transport: str = ""
    participant_role: str = ""
    raw: JsonDict = field(default_factory=dict)

    @property
    def zeroed_without_infra_failure(self) -> bool:
        return (
            self.score_eligible
            and not self.passed
            and float(self.reward or 0.0) == 0.0
            and not self.infra_failure_type
            and not self.error_type
        )

    def as_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(slots=True)
class EmittedEvidence:
    source_file: str
    line_number: int
    marker: str
    task_id: str = ""
    canonical_task_id: str = ""
    request_task_id: str = ""
    family: str = ""
    status: str = ""
    identity_source: str = ""
    identity_confidence: float | None = None
    artifact_refs_candidate_count: int = 0
    artifact_refs_count: int = 0
    artifact_outputs_count: int = 0
    artifact_count: int = 0
    files_count: int = 0
    deliverables_count: int = 0
    workspace_write_count: int = 0
    workspace_write_ok_count: int = 0
    workspace_visible: bool | None = None
    wrote_any_file: bool | None = None
    output_paths: list[str] = field(default_factory=list)
    raw_excerpt: JsonDict = field(default_factory=dict)

    @property
    def best_task_key(self) -> str:
        for value in (
            self.canonical_task_id,
            self.task_id,
            self.request_task_id,
            _find_first_string(self.raw_excerpt, ("task_id", "canonical_task_id", "request_task_id")),
        ):
            cleaned = _clean_task_key(value)
            if cleaned:
                return cleaned
        return ""

    @property
    def has_artifact_evidence(self) -> bool:
        return (
            self.artifact_refs_candidate_count > 0
            or self.artifact_refs_count > 0
            or self.artifact_outputs_count > 0
            or self.artifact_count > 0
            or self.files_count > 0
            or self.deliverables_count > 0
        )

    @property
    def has_filesystem_evidence(self) -> bool:
        return bool(self.wrote_any_file) or self.workspace_write_ok_count > 0

    def as_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(slots=True)
class ReconciledTask:
    task_key: str
    official: OfficialResultRow | None = None
    evidence: list[EmittedEvidence] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def emitted_artifact_refs_candidate_count(self) -> int:
        return sum(item.artifact_refs_candidate_count for item in self.evidence)

    @property
    def emitted_artifact_refs_count(self) -> int:
        return sum(item.artifact_refs_count for item in self.evidence)

    @property
    def emitted_artifact_outputs_count(self) -> int:
        return sum(item.artifact_outputs_count for item in self.evidence)

    @property
    def workspace_write_ok_count(self) -> int:
        return sum(item.workspace_write_ok_count for item in self.evidence)

    @property
    def evidence_count(self) -> int:
        return len(self.evidence)

    def as_dict(self) -> JsonDict:
        return {
            "task_key": self.task_key,
            "official": self.official.as_dict() if self.official else None,
            "evidence": [item.as_dict() for item in self.evidence],
            "metrics": {
                "evidence_count": self.evidence_count,
                "emitted_artifact_refs_candidate_count": self.emitted_artifact_refs_candidate_count,
                "emitted_artifact_refs_count": self.emitted_artifact_refs_count,
                "emitted_artifact_outputs_count": self.emitted_artifact_outputs_count,
                "workspace_write_ok_count": self.workspace_write_ok_count,
            },
            "anomalies": list(self.anomalies),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class ReconcileReport:
    version: str
    created_at_unix: float
    official_results_path: str
    log_files: list[str]
    result_shape: str
    official_row_count: int
    evidence_record_count: int
    matched_task_count: int
    unmatched_evidence_count: int
    summary: JsonDict
    tasks: list[ReconciledTask]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> JsonDict:
        return {
            "version": self.version,
            "created_at_unix": self.created_at_unix,
            "official_results_path": self.official_results_path,
            "log_files": list(self.log_files),
            "result_shape": self.result_shape,
            "official_row_count": self.official_row_count,
            "evidence_record_count": self.evidence_record_count,
            "matched_task_count": self.matched_task_count,
            "unmatched_evidence_count": self.unmatched_evidence_count,
            "summary": dict(self.summary),
            "warnings": list(self.warnings),
            "tasks": [item.as_dict() for item in self.tasks],
        }


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _safe_read_json(path: str | Path) -> Any:
    p = Path(path)
    with p.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _safe_write_json(path: str | Path, payload: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return p


def _clean_task_key(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = text.strip('"\'` ')
    return text.lower()


def _as_float(value: Any, default: float = 0.0) -> float:
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
    if text in {"1", "true", "yes", "y", "ok", "pass", "passed", "success", "completed"}:
        return True
    if text in {"0", "false", "no", "n", "fail", "failed", "error", "null", "none"}:
        return False
    return default


def _is_uuid(value: Any) -> bool:
    return bool(UUID_RE.match(str(value or "").strip()))


def _to_mapping(value: Any) -> JsonDict:
    return dict(value) if isinstance(value, Mapping) else {}


def _truncate_value(value: Any, *, max_chars: int = 4000) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _truncate_value(v, max_chars=max_chars) for k, v in list(value.items())[:80]}
    if isinstance(value, list):
        return [_truncate_value(item, max_chars=max_chars) for item in value[:80]]
    if isinstance(value, str):
        return value[:max_chars]
    return value


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _find_first_string(value: Any, keys: Sequence[str]) -> str:
    wanted = {str(key) for key in keys}
    for node in _walk(value):
        if isinstance(node, Mapping):
            for key in wanted:
                child = node.get(key)
                if isinstance(child, str) and child.strip():
                    return child.strip()
    return ""


def _find_first_number(value: Any, keys: Sequence[str]) -> float | None:
    wanted = {str(key) for key in keys}
    for node in _walk(value):
        if isinstance(node, Mapping):
            for key in wanted:
                child = node.get(key)
                if isinstance(child, (int, float)) and not isinstance(child, bool):
                    return float(child)
                if isinstance(child, str):
                    try:
                        return float(child)
                    except ValueError:
                        pass
    return None


def _find_first_bool(value: Any, keys: Sequence[str]) -> bool | None:
    wanted = {str(key) for key in keys}
    for node in _walk(value):
        if isinstance(node, Mapping):
            for key in wanted:
                child = node.get(key)
                if isinstance(child, bool):
                    return child
                if isinstance(child, str) and child.lower() in {"true", "false", "yes", "no", "1", "0"}:
                    return _as_bool(child)
    return None


def _count_list_fields(value: Any, keys: Sequence[str]) -> int:
    total = 0
    wanted = {str(key) for key in keys}
    for node in _walk(value):
        if isinstance(node, Mapping):
            for key in wanted:
                child = node.get(key)
                if isinstance(child, list):
                    total += len(child)
                elif child:
                    total += 1
    return total


def _collect_paths(value: Any, *, limit: int = 80) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    path_keys = {"path", "relative_path", "output_path", "file_path", "uri"}

    for node in _walk(value):
        if not isinstance(node, Mapping):
            continue
        for key in path_keys:
            candidate = node.get(key)
            if not isinstance(candidate, str):
                continue
            text = candidate.strip()
            if not text or text in seen:
                continue
            if "/" not in text and "artifact://" not in text:
                continue
            seen.add(text)
            output.append(text)
            if len(output) >= limit:
                return output
    return output


# ---------------------------------------------------------------------------
# Official result normalization
# ---------------------------------------------------------------------------


def detect_official_result_shape(payload: Any) -> str:
    """Detect flat, nested, mixed, empty, or invalid result shapes."""

    if not isinstance(payload, Mapping):
        return "invalid_non_object"
    results = payload.get("results")
    if not isinstance(results, list):
        return "invalid_missing_results"
    if not results:
        return "empty"

    flat = 0
    nested = 0
    invalid = 0

    for item in results:
        if isinstance(item, Mapping) and isinstance(item.get("results"), list):
            nested += 1
        elif isinstance(item, Mapping) and "task_id" in item:
            flat += 1
        else:
            invalid += 1

    if invalid and not flat and not nested:
        return "invalid_rows"
    if flat and nested:
        return "mixed"
    if nested:
        return "nested_shard"
    if flat:
        return "legacy_flat"
    return "unknown"


def normalize_official_result_rows(payload: Any) -> list[OfficialResultRow]:
    """Return official rows from flat, nested, or mixed results JSON."""

    if not isinstance(payload, Mapping):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []

    rows: list[OfficialResultRow] = []

    def add_row(raw: Any, *, shard_index: int | None, row_index: int | None) -> None:
        if not isinstance(raw, Mapping):
            return
        task_id = str(raw.get("task_id") or "").strip()
        if not task_id:
            return
        artifact_refs = raw.get("artifact_refs")
        rows.append(
            OfficialResultRow(
                task_id=task_id,
                task_digest=str(raw.get("task_digest") or raw.get("digest") or "").strip(),
                shard_index=shard_index,
                row_index=row_index,
                passed=_as_bool(raw.get("passed"), default=False),
                reward=_as_float(raw.get("reward"), default=0.0),
                score_eligible=_as_bool(raw.get("score_eligible"), default=False),
                artifact_refs_count=len(artifact_refs) if isinstance(artifact_refs, list) else (1 if artifact_refs else 0),
                time_used=_as_float(raw.get("time_used"), default=0.0) if raw.get("time_used") is not None else None,
                infra_failure_type=raw.get("infra_failure_type") or None,
                error_type=raw.get("error_type") or None,
                condition=str(raw.get("condition") or "").strip(),
                agent_transport=str(raw.get("agent_transport") or "").strip(),
                participant_role=str(raw.get("participant_role") or "").strip(),
                raw=dict(raw),
            )
        )

    for outer_index, item in enumerate(results):
        if isinstance(item, Mapping) and isinstance(item.get("results"), list):
            for row_index, row in enumerate(item.get("results") or []):
                add_row(row, shard_index=outer_index, row_index=row_index)
        elif isinstance(item, Mapping) and "task_id" in item:
            add_row(item, shard_index=None, row_index=outer_index)

    return rows


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------


def resolve_log_files(explicit_logs: Sequence[str], log_patterns: Sequence[str]) -> list[Path]:
    """Resolve explicit log files plus glob/directory patterns."""

    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        try:
            resolved = str(path.resolve())
        except Exception:
            resolved = str(path)
        if resolved in seen:
            return
        if path.exists() and path.is_file():
            seen.add(resolved)
            found.append(path)

    for item in explicit_logs:
        add(Path(item))

    for pattern in log_patterns:
        p = Path(pattern)
        if p.exists() and p.is_dir():
            for child in sorted(p.rglob("*.txt")):
                add(child)
            for child in sorted(p.rglob("*.log")):
                add(child)
            continue

        matches = glob.glob(pattern, recursive=True)
        for match in sorted(matches):
            add(Path(match))

    return found


def line_has_marker(line: str) -> str:
    lowered = line.lower()
    for marker in MARKERS:
        if marker.lower() in lowered:
            return marker
    return ""


def strip_github_timestamp(line: str) -> str:
    return re.sub(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s+", "", line).strip()


def _balanced_json_candidates(text: str) -> list[str]:
    """Extract balanced JSON-ish object/array candidates from a single line."""

    candidates: list[str] = []
    starts = [match.start() for match in JSON_START_RE.finditer(text)]
    for start in starts:
        opener = text[start]
        closer = "}" if opener == "{" else "]"
        stack: list[str] = []
        in_string = False
        escape = False
        quote = ""
        for index in range(start, len(text)):
            ch = text[index]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    in_string = False
                continue

            if ch in {'"', "'"}:
                in_string = True
                quote = ch
                continue
            if ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if not stack or ch != stack[-1]:
                    break
                stack.pop()
                if not stack:
                    candidate = text[start : index + 1]
                    if opener == "{" and candidate.endswith(closer):
                        candidates.append(candidate)
                    elif opener == "[" and candidate.endswith(closer):
                        candidates.append(candidate)
                    break
    unique: list[str] = []
    seen: set[str] = set()
    for item in sorted(candidates, key=len, reverse=True):
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def parse_jsonish(candidate: str) -> Any:
    """Parse strict JSON, then Python literal dict/list as fallback."""

    text = candidate.strip()
    if not text:
        raise ValueError("empty JSON candidate")
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return ast.literal_eval(text)
    except Exception:
        pass

    try:
        unescaped = json.loads(f'"{text}"')
        if isinstance(unescaped, str):
            return json.loads(unescaped)
    except Exception:
        pass

    raise ValueError("not JSON-ish")


def parse_marker_payloads_from_line(line: str) -> list[Any]:
    cleaned = strip_github_timestamp(line)
    if not line_has_marker(cleaned):
        return []

    # Prefer the longest balanced object/array.  Marker lines normally contain
    # one JSON payload, and shorter nested objects are not independent evidence.
    # This avoids double-counting nested writes/artifacts from the same log line.
    for candidate in _balanced_json_candidates(cleaned):
        try:
            return [parse_jsonish(candidate)]
        except Exception:
            continue
    return []


def evidence_from_payload(payload: Any, *, source_file: str, line_number: int, marker: str) -> EmittedEvidence:
    data = _to_mapping(payload)
    if not data:
        data = {"payload": _truncate_value(payload)}

    task_id = (
        _find_first_string(data, ("task_id",))
        or _find_first_string(data, ("canonical_task_id",))
        or _find_first_string(data, ("request_task_id",))
    )
    canonical_task_id = _find_first_string(data, ("canonical_task_id", "environment_canonical_task_id"))
    request_task_id = _find_first_string(data, ("request_task_id", "trial_id", "uuid"))
    family = _find_first_string(data, ("family", "contract_family", "environment_family_hint"))
    status = _find_first_string(data, ("status",))

    artifact_refs_candidate_count = _count_list_fields(data, ("artifact_refs_candidate", "artifact_refs_candidates"))
    artifact_refs_count = _count_list_fields(data, ("artifact_refs",))
    artifact_outputs_count = _count_list_fields(data, ("artifact_outputs",))
    artifact_count = _count_list_fields(data, ("artifacts", "artifact_records"))
    files_count = _count_list_fields(data, ("files",))
    deliverables_count = _count_list_fields(data, ("deliverables",))

    workspace_write_count = 0
    workspace_write_ok_count = 0
    for node in _walk(data):
        if isinstance(node, Mapping) and isinstance(node.get("writes"), list):
            writes = node.get("writes") or []
            workspace_write_count += len(writes)
            for write in writes:
                if isinstance(write, Mapping) and _as_bool(write.get("ok"), default=False):
                    workspace_write_ok_count += 1

    if not workspace_write_count and isinstance(data.get("writes"), list):
        writes = data.get("writes") or []
        workspace_write_count = len(writes)
        workspace_write_ok_count = sum(1 for write in writes if isinstance(write, Mapping) and _as_bool(write.get("ok"), default=False))

    identity_confidence = _find_first_number(data, ("identity_confidence", "confidence"))
    workspace_visible = _find_first_bool(data, ("workspace_visible", "can_see_workspace", "workspace_seen"))
    wrote_any_file = _find_first_bool(data, ("wrote_any_file", "wrote_any_output", "wrote_file"))

    return EmittedEvidence(
        source_file=source_file,
        line_number=line_number,
        marker=marker,
        task_id=str(task_id or "").strip(),
        canonical_task_id=str(canonical_task_id or "").strip(),
        request_task_id=str(request_task_id or "").strip(),
        family=str(family or "").strip(),
        status=str(status or "").strip(),
        identity_source=_find_first_string(data, ("identity_source",)),
        identity_confidence=identity_confidence,
        artifact_refs_candidate_count=artifact_refs_candidate_count,
        artifact_refs_count=artifact_refs_count,
        artifact_outputs_count=artifact_outputs_count,
        artifact_count=artifact_count,
        files_count=files_count,
        deliverables_count=deliverables_count,
        workspace_write_count=workspace_write_count,
        workspace_write_ok_count=workspace_write_ok_count,
        workspace_visible=workspace_visible,
        wrote_any_file=wrote_any_file,
        output_paths=_collect_paths(data),
        raw_excerpt=_truncate_value(data, max_chars=1600),
    )


def collect_evidence_from_logs(log_files: Sequence[Path]) -> list[EmittedEvidence]:
    evidence: list[EmittedEvidence] = []
    for path in log_files:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, start=1):
                    marker = line_has_marker(line)
                    if not marker:
                        continue
                    payloads = parse_marker_payloads_from_line(line)
                    if not payloads:
                        evidence.append(
                            EmittedEvidence(
                                source_file=str(path),
                                line_number=line_number,
                                marker=marker,
                                raw_excerpt={"unparsed_line_excerpt": strip_github_timestamp(line)[:1600]},
                            )
                        )
                        continue
                    for payload in payloads:
                        evidence.append(
                            evidence_from_payload(
                                payload,
                                source_file=str(path),
                                line_number=line_number,
                                marker=marker,
                            )
                        )
        except OSError as exc:
            evidence.append(
                EmittedEvidence(
                    source_file=str(path),
                    line_number=0,
                    marker="log_read_error",
                    raw_excerpt={"error": str(exc)},
                )
            )
    return evidence


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def _index_official(rows: Sequence[OfficialResultRow]) -> dict[str, OfficialResultRow]:
    index: dict[str, OfficialResultRow] = {}
    for row in rows:
        key = _clean_task_key(row.task_id)
        if key:
            index[key] = row
    return index


def _index_evidence(evidence: Sequence[EmittedEvidence]) -> dict[str, list[EmittedEvidence]]:
    grouped: dict[str, list[EmittedEvidence]] = defaultdict(list)
    for item in evidence:
        key = item.best_task_key
        if key:
            grouped[key].append(item)
        else:
            grouped["__unresolved__"].append(item)
    return grouped


def reconcile(official_rows: Sequence[OfficialResultRow], evidence: Sequence[EmittedEvidence]) -> tuple[list[ReconciledTask], JsonDict]:
    official_index = _index_official(official_rows)
    evidence_index = _index_evidence(evidence)

    task_keys: set[str] = set(official_index)
    task_keys.update(key for key in evidence_index if key != "__unresolved__")

    tasks: list[ReconciledTask] = []
    anomaly_counts: Counter[str] = Counter()

    for key in sorted(task_keys):
        row = official_index.get(key)
        ev = evidence_index.get(key, [])
        item = ReconciledTask(task_key=key, official=row, evidence=ev)

        emitted_refs = sum(x.artifact_refs_candidate_count + x.artifact_refs_count for x in ev)
        emitted_artifact_outputs = sum(x.artifact_outputs_count + x.artifact_count + x.files_count + x.deliverables_count for x in ev)
        write_ok = sum(x.workspace_write_ok_count for x in ev)
        identity_zero = any((x.identity_confidence == 0.0) or (x.identity_source == "unresolved") for x in ev)

        if row is None and ev:
            item.anomalies.append("official_row_missing_for_emitted_evidence")
        if row is not None and not ev:
            item.notes.append("no_aegisforge_marker_evidence_found_for_official_row")
        if row is not None and emitted_refs > 0 and row.artifact_refs_count == 0:
            item.anomalies.append("artifact_refs_dropped")
        if row is not None and emitted_artifact_outputs > 0 and row.artifact_refs_count == 0:
            item.anomalies.append("artifact_outputs_not_preserved_as_official_refs")
        if row is not None and write_ok > 0 and row.zeroed_without_infra_failure:
            item.anomalies.append("filesystem_outputs_not_scored")
        if row is not None and row.zeroed_without_infra_failure:
            item.anomalies.append("official_result_zeroed_without_infra_failure")
        if row is not None and not row.score_eligible:
            item.anomalies.append("score_ineligible")
        if row is not None and row.infra_failure_type:
            item.anomalies.append("infra_failure")
        if row is not None and row.error_type:
            item.anomalies.append("official_error_type_present")
        if identity_zero:
            item.anomalies.append("task_identity_unresolved")
        if _is_uuid(key):
            item.anomalies.append("task_key_is_uuid_not_public_task_id")

        for anomaly in item.anomalies:
            anomaly_counts[anomaly] += 1
        tasks.append(item)

    unresolved = evidence_index.get("__unresolved__", [])
    for ev in unresolved:
        item = ReconciledTask(
            task_key=f"__unresolved__:{Path(ev.source_file).name}:{ev.line_number}",
            official=None,
            evidence=[ev],
            anomalies=["evidence_task_identity_unresolved"],
        )
        anomaly_counts["evidence_task_identity_unresolved"] += 1
        tasks.append(item)

    matched_task_count = sum(1 for t in tasks if t.official is not None and t.evidence)
    unmatched_evidence_count = sum(1 for t in tasks if t.official is None and t.evidence)

    summary: JsonDict = {
        "official_rows": len(official_rows),
        "evidence_records": len(evidence),
        "matched_task_count": matched_task_count,
        "unmatched_evidence_count": unmatched_evidence_count,
        "official_passed": sum(1 for r in official_rows if r.passed),
        "official_failed": sum(1 for r in official_rows if not r.passed),
        "official_score_eligible": sum(1 for r in official_rows if r.score_eligible),
        "official_reward_sum": round(sum(float(r.reward or 0.0) for r in official_rows), 6),
        "official_artifact_refs_total": sum(r.artifact_refs_count for r in official_rows),
        "emitted_artifact_refs_candidate_total": sum(e.artifact_refs_candidate_count for e in evidence),
        "emitted_artifact_refs_total": sum(e.artifact_refs_count for e in evidence),
        "emitted_artifact_outputs_total": sum(e.artifact_outputs_count for e in evidence),
        "workspace_write_ok_total": sum(e.workspace_write_ok_count for e in evidence),
        "anomaly_counts": dict(sorted(anomaly_counts.items())),
        "marker_counts": dict(Counter(e.marker for e in evidence)),
    }
    return tasks, summary


def build_report(
    *,
    official_results_path: str | Path,
    log_files: Sequence[Path],
) -> ReconcileReport:
    official_payload = _safe_read_json(official_results_path)
    result_shape = detect_official_result_shape(official_payload)
    official_rows = normalize_official_result_rows(official_payload)
    evidence = collect_evidence_from_logs(log_files)
    tasks, summary = reconcile(official_rows, evidence)

    warnings: list[str] = []
    if not official_rows:
        warnings.append("No official result rows were found.")
    if not evidence:
        warnings.append("No AegisForge/SkillsBench marker evidence was found in logs.")
    if result_shape.startswith("invalid"):
        warnings.append(f"Official result shape looks invalid: {result_shape}")
    if result_shape == "mixed":
        warnings.append("Official result shape is mixed; leaderboard compatibility should be audited.")

    return ReconcileReport(
        version=TOOL_VERSION,
        created_at_unix=time.time(),
        official_results_path=str(official_results_path),
        log_files=[str(path) for path in log_files],
        result_shape=result_shape,
        official_row_count=len(official_rows),
        evidence_record_count=len(evidence),
        matched_task_count=int(summary.get("matched_task_count", 0)),
        unmatched_evidence_count=int(summary.get("unmatched_evidence_count", 0)),
        summary=summary,
        tasks=tasks,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def write_markdown_report(path: str | Path, report: ReconcileReport, *, max_task_rows: int = 80) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    summary = report.summary
    anomaly_counts = summary.get("anomaly_counts") if isinstance(summary.get("anomaly_counts"), Mapping) else {}

    lines: list[str] = []
    lines.append("# SkillsBench Reconcile Report")
    lines.append("")
    lines.append(f"- Tool version: `{report.version}`")
    lines.append(f"- Official results: `{report.official_results_path}`")
    lines.append(f"- Result shape: `{report.result_shape}`")
    lines.append(f"- Log files scanned: `{len(report.log_files)}`")
    lines.append(f"- Official rows: `{report.official_row_count}`")
    lines.append(f"- Evidence records: `{report.evidence_record_count}`")
    lines.append(f"- Matched tasks: `{report.matched_task_count}`")
    lines.append(f"- Unmatched evidence records/tasks: `{report.unmatched_evidence_count}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for key in (
        "official_passed",
        "official_failed",
        "official_score_eligible",
        "official_reward_sum",
        "official_artifact_refs_total",
        "emitted_artifact_refs_candidate_total",
        "emitted_artifact_refs_total",
        "emitted_artifact_outputs_total",
        "workspace_write_ok_total",
    ):
        lines.append(f"- {key}: `{summary.get(key)}`")
    lines.append("")

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Anomaly counts")
    lines.append("")
    if anomaly_counts:
        for key, value in sorted(anomaly_counts.items(), key=lambda kv: (-int(kv[1]), kv[0])):
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- No anomalies detected.")
    lines.append("")

    lines.append("## Task samples")
    lines.append("")
    lines.append("| task_key | passed | reward | official_refs | emitted_refs | emitted_outputs | fs_writes | anomalies |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")

    interesting = sorted(
        report.tasks,
        key=lambda t: (
            0 if t.anomalies else 1,
            t.task_key,
        ),
    )[:max_task_rows]

    for task in interesting:
        official = task.official
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(task.task_key),
                    str(official.passed if official else ""),
                    str(official.reward if official else ""),
                    str(official.artifact_refs_count if official else ""),
                    str(task.emitted_artifact_refs_candidate_count + task.emitted_artifact_refs_count),
                    str(task.emitted_artifact_outputs_count),
                    str(task.workspace_write_ok_count),
                    _md(", ".join(task.anomalies)),
                ]
            )
            + " |"
        )

    lines.append("")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _md(value: Any) -> str:
    text = str(value or "")
    return text.replace("|", "\\|").replace("\n", " ")[:240]


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------


def validate_selftest() -> JsonDict:
    sample_results = {
        "status": "completed",
        "participants": {"agent": "agent-id", "green": "green-id"},
        "results": [
            {
                "shard_index": 0,
                "results": [
                    {
                        "task_id": "sample-task",
                        "task_digest": "sha256:demo",
                        "score_eligible": True,
                        "passed": False,
                        "reward": 0.0,
                        "artifact_refs": [],
                        "infra_failure_type": None,
                        "error_type": None,
                    }
                ],
            }
        ],
    }

    sample_log_line = (
        "2026-06-09T00:00:00.0000000Z SKILLSBENCH_FINAL_RESPONSE_CONTRACT "
        + json.dumps(
            {
                "task_id": "sample-task",
                "status": "completed",
                "artifact_refs_candidate": [{"name": "answer.json", "uri": "artifact://answer.json"}],
                "artifact_outputs": [{"name": "answer.json", "path": "/root/answer.json"}],
                "workspace_execution": {
                    "wrote_any_file": True,
                    "writes": [{"path": "/root/answer.json", "ok": True, "sha256": "abc", "bytes_written": 12}],
                },
            },
            ensure_ascii=False,
        )
    )

    shape = detect_official_result_shape(sample_results)
    rows = normalize_official_result_rows(sample_results)
    marker = line_has_marker(sample_log_line)
    payloads = parse_marker_payloads_from_line(sample_log_line)
    evidence = [
        evidence_from_payload(payload, source_file="<selftest>", line_number=1, marker=marker)
        for payload in payloads
    ]
    tasks, summary = reconcile(rows, evidence)

    errors: list[str] = []
    if shape != "nested_shard":
        errors.append(f"expected nested_shard shape, got {shape}")
    if len(rows) != 1:
        errors.append(f"expected 1 official row, got {len(rows)}")
    if len(evidence) != 1:
        errors.append(f"expected 1 evidence row, got {len(evidence)}")
    if not tasks or "artifact_refs_dropped" not in tasks[0].anomalies:
        errors.append("expected artifact_refs_dropped anomaly")
    if not tasks or "filesystem_outputs_not_scored" not in tasks[0].anomalies:
        errors.append("expected filesystem_outputs_not_scored anomaly")

    return {
        "ok": not errors,
        "errors": errors,
        "version": TOOL_VERSION,
        "shape": shape,
        "summary": summary,
        "task": tasks[0].as_dict() if tasks else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile AegisForge SkillsBench logs against official results JSON.",
    )
    parser.add_argument("--results", help="Official results JSON path.", default="")
    parser.add_argument("--log", action="append", default=[], help="Explicit log file path. May be repeated.")
    parser.add_argument("--logs", action="append", default=[], help="Log glob or directory. May be repeated.")
    parser.add_argument("--out", default="", help="Output JSON report path.")
    parser.add_argument("--markdown", default="", help="Optional Markdown report path.")
    parser.add_argument("--max-task-rows", type=int, default=80, help="Max task rows in Markdown report.")
    parser.add_argument("--selftest", action="store_true", help="Run built-in selftest and exit.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.selftest:
        result = validate_selftest()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0 if result.get("ok") else 1

    if not args.results:
        parser.error("--results is required unless --selftest is used.")

    log_files = resolve_log_files(args.log, args.logs)
    report = build_report(official_results_path=args.results, log_files=log_files)

    if args.out:
        _safe_write_json(args.out, report.as_dict())
    else:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True, default=str))

    if args.markdown:
        write_markdown_report(args.markdown, report, max_task_rows=max(1, int(args.max_task_rows)))

    if report.result_shape.startswith("invalid"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
