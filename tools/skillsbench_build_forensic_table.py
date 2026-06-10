from __future__ import annotations

"""Build an optimized SkillsBench forensic table from Quick Submit logs/artifacts.

This version is designed for local/offline forensic auditing of BenchFlow /
SkillsBench `standard-v1` with `with_skills` runs. It treats filesystem writes
as the primary evidence channel and treats A2A `artifact_refs` as diagnostic
compatibility evidence.

Usage from the repo root or from a downloaded GitHub Actions log folder:

    python tools/skillsbench_build_forensic_table.py --logs-dir .

Outputs:
- CSV:   one analyst-friendly row per official task/result where possible.
- JSONL: one machine-readable row with the same fields.
- JSON:  run-level summary with counts, join strategy, diagnoses, and sources.

The parser is intentionally tolerant. It can consume raw GitHub Actions text
logs, ANSI-colored Amber logs, pretty-printed JSON bodies with line prefixes,
extracted `results.json`, extracted `provenance.json`, and AegisForge markers
such as `AEGISFORGE_ROUTE_PROBE` and `SKILLSBENCH_FINAL_RESPONSE_CONTRACT`.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
import argparse
import ast
import csv
import hashlib
import json
import re
import sys


VERSION = "skillsbench_build_forensic_table_v2_2026_06_10"

# GitHub Actions / Amber log cleanup.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
GITHUB_TS_RE = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z\s*")
CONTAINER_PIPE_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:\s+)?\|\s?(.*)$")
SHARD_RE = re.compile(r"(?:shard[-_ ](?P<shard>\d+)|eval \((?P<eval>\d+)\))", re.IGNORECASE)
RUN_URL_RE = re.compile(r"https://github\.com/[^\s\"']+/actions/runs/(?P<run_id>\d+)")
SUBMISSION_RE = re.compile(r"quick-submit-(?P<submission>[0-9a-fA-F-]{20,})")

SAFE_OUTPUT_ROOTS = (
    "/root",
    "/root/output",
    "/app/workspace",
    "/app/output",
    "/output",
    "/workspace",
    "/home/github/build/failed",
)
WRITE_PATH_RE = re.compile(
    r"(?P<path>/(?:root|app/workspace|app/output|output|workspace|home/github/build/failed)[^\s\"'<>),;]+)"
)

SECRETISH_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|Bearer\s+[A-Za-z0-9._~+/-]{20,})"
)

FAMILY_ALIASES = {
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
    "json": "json_output",
    "csv": "csv_output",
    "markdown": "markdown_output",
    "md": "markdown_output",
    "obj": "general_file_output",
}

CSV_FIELDNAMES = [
    "shard",
    "sequence_index",
    "join_strategy",
    "official_task_id",
    "official_trial_id",
    "official_category",
    "official_difficulty",
    "official_tags",
    "official_passed",
    "official_reward",
    "official_max_score",
    "official_score_eligible",
    "official_artifact_refs_len",
    "official_time_used",
    "official_infra_failure_type",
    "official_error_type",
    "local_task_id",
    "task_identity_source",
    "task_identity_confidence",
    "task_identity_candidates",
    "top_family",
    "contract_family",
    "output_family",
    "selected_solver_key",
    "selected_solver_name",
    "selected_source",
    "workspace_visible",
    "wrote_any_file",
    "ok_writes",
    "write_count",
    "write_paths",
    "primary_outputs",
    "expected_outputs",
    "output_candidates",
    "final_artifact_refs_len",
    "final_artifact_refs_candidate_len",
    "artifact_outputs_len",
    "diagnostic_ref_count",
    "scoring_channel",
    "output_channel",
    "artifact_refs_role",
    "filesystem_primary",
    "route_events",
    "warnings",
    "errors",
    "diagnosis",
    "evidence_confidence",
    "source_files",
]


def redact(value: Any) -> str:
    """Convert to a string and mask obvious secret/token shapes."""

    if value is None:
        return ""
    text = str(value)
    return SECRETISH_RE.sub("***", text)


def compact_json(value: Any) -> str:
    """Stable compact string for lists/dicts; blanks stay blank."""

    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return redact(value)
    try:
        return redact(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    except Exception:
        return redact(value)


def bool_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None or value == "":
        return ""
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "ok", "pass", "passed", "success"}:
        return "true"
    if text in {"0", "false", "no", "n", "fail", "failed", "error"}:
        return "false"
    return text


def as_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return str(float(value))
    except (TypeError, ValueError):
        return redact(value)


def normalize_family(value: Any) -> str:
    text = redact(value).strip().lower().replace("-", "_").replace(" ", "_")
    return FAMILY_ALIASES.get(text, text)


def dedupe(items: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        text = redact(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def maybe_parse(value: Any) -> Any:
    """Parse JSON/Python-literal strings used inside the current diagnostics."""

    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        for parser in (json.loads, ast.literal_eval):
            try:
                return parser(text)
            except Exception:
                pass
    return value


def to_mapping(value: Any) -> dict[str, Any]:
    parsed = maybe_parse(value)
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def to_list(value: Any) -> list[Any]:
    parsed = maybe_parse(value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, tuple):
        return list(parsed)
    if isinstance(parsed, set):
        return list(parsed)
    return [parsed]


def normalize_line(line: str) -> str:
    line = ANSI_RE.sub("", line).rstrip("\n")
    line = GITHUB_TS_RE.sub("", line)
    match = CONTAINER_PIPE_RE.match(line)
    if match:
        return match.group(1)
    return line


def normalize_log_text(text: str) -> str:
    return "\n".join(normalize_line(line) for line in text.splitlines())


def shard_from_path(path: Path, text: str = "") -> str:
    for candidate in (path.name, text[:20000]):
        match = SHARD_RE.search(candidate)
        if match:
            return match.group("shard") or match.group("eval") or ""
    return ""


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_json_objects(text: str) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield JSON objects from noisy text using JSONDecoder.raw_decode.

    The input should already have log prefixes stripped. This intentionally
    ignores parse failures and advances one opening brace at a time.
    """

    decoder = json.JSONDecoder()
    pos = 0
    while True:
        idx = text.find("{", pos)
        if idx < 0:
            return
        try:
            obj, end = decoder.raw_decode(text[idx:])
        except Exception:
            pos = idx + 1
            continue
        if isinstance(obj, dict):
            yield idx, obj
        pos = idx + max(end, 1)


def extract_marker_objects(text: str, marker: str) -> list[tuple[int, dict[str, Any]]]:
    """Extract JSON object immediately following a marker."""

    decoder = json.JSONDecoder()
    results: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()
    pos = 0
    while True:
        marker_idx = text.find(marker, pos)
        if marker_idx < 0:
            break
        json_idx = text.find("{", marker_idx + len(marker))
        if json_idx < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[json_idx:])
        except Exception:
            pos = marker_idx + len(marker)
            continue
        if isinstance(obj, dict):
            identity = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
            digest = hashlib.sha1(identity.encode("utf-8", errors="replace")).hexdigest()
            if digest not in seen:
                seen.add(digest)
                results.append((marker_idx, obj))
        pos = json_idx + max(end, 1)
    return results


def official_rows_from_obj(obj: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return official result rows from a parsed object.

    Prefer top-level `results` arrays because they preserve official order.
    """

    results = obj.get("results")
    if isinstance(results, list) and any(isinstance(item, Mapping) and "task_id" in item for item in results):
        return [dict(item) for item in results if isinstance(item, Mapping)]

    rows: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            if (
                "task_id" in value
                and ("passed" in value or "reward" in value or "artifact_refs" in value)
                and ("trial_id" in value or "task_set" in value or "score_eligible" in value)
            ):
                rows.append(dict(value))
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(obj)
    return rows


def choose_official_results(parsed_objects: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, dict[str, Any]]]:
    """Pick the best official result set from parsed JSON objects."""

    parent_sets: list[tuple[int, list[dict[str, Any]]]] = []
    loose: list[tuple[int, dict[str, Any]]] = []

    for pos, obj in parsed_objects:
        rows = official_rows_from_obj(obj)
        if isinstance(obj.get("results"), list) and rows:
            parent_sets.append((pos, rows))
        else:
            for row in rows:
                loose.append((pos, row))

    if parent_sets:
        # Use the largest, latest complete results object; the final poll tends
        # to be the most authoritative when logs contain both running and final bodies.
        parent_sets.sort(key=lambda item: (len(item[1]), item[0]))
        base_pos, rows = parent_sets[-1]
        return [(base_pos + i, row) for i, row in enumerate(rows)]

    # Fallback: dedupe loose rows by trial_id/task_id while preserving first seen order.
    out: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()
    for pos, row in sorted(loose, key=lambda item: item[0]):
        key = str(row.get("trial_id") or row.get("task_id") or pos)
        if key in seen:
            continue
        seen.add(key)
        out.append((pos, row))
    return out


@dataclass
class LocalEvidence:
    sequence_index: int
    source_file: str
    position: int = 0
    shard: str = ""
    local_task_id: str = ""
    task_identity_source: str = ""
    task_identity_confidence: str = ""
    task_identity_candidates: str = ""
    top_family: str = ""
    contract_family: str = ""
    output_family: str = ""
    selected_solver_key: str = ""
    selected_solver_name: str = ""
    selected_source: str = ""
    workspace_visible: str = ""
    wrote_any_file: str = ""
    ok_writes: str = ""
    write_count: str = ""
    write_paths: str = ""
    primary_outputs: str = ""
    expected_outputs: str = ""
    output_candidates: str = ""
    final_artifact_refs_len: str = ""
    final_artifact_refs_candidate_len: str = ""
    artifact_outputs_len: str = ""
    diagnostic_ref_count: str = ""
    scoring_channel: str = ""
    output_channel: str = ""
    artifact_refs_role: str = ""
    filesystem_primary: str = ""
    route_events: str = ""
    warnings: str = ""
    errors: str = ""


@dataclass
class OfficialEvidence:
    sequence_index: int
    source_file: str
    position: int = 0
    shard: str = ""
    official_task_id: str = ""
    official_trial_id: str = ""
    official_category: str = ""
    official_difficulty: str = ""
    official_tags: str = ""
    official_passed: str = ""
    official_reward: str = ""
    official_max_score: str = ""
    official_score_eligible: str = ""
    official_artifact_refs_len: str = ""
    official_time_used: str = ""
    official_infra_failure_type: str = ""
    official_error_type: str = ""


@dataclass
class ForensicRow:
    shard: str = ""
    sequence_index: str = ""
    join_strategy: str = ""
    official_task_id: str = ""
    official_trial_id: str = ""
    official_category: str = ""
    official_difficulty: str = ""
    official_tags: str = ""
    official_passed: str = ""
    official_reward: str = ""
    official_max_score: str = ""
    official_score_eligible: str = ""
    official_artifact_refs_len: str = ""
    official_time_used: str = ""
    official_infra_failure_type: str = ""
    official_error_type: str = ""
    local_task_id: str = ""
    task_identity_source: str = ""
    task_identity_confidence: str = ""
    task_identity_candidates: str = ""
    top_family: str = ""
    contract_family: str = ""
    output_family: str = ""
    selected_solver_key: str = ""
    selected_solver_name: str = ""
    selected_source: str = ""
    workspace_visible: str = ""
    wrote_any_file: str = ""
    ok_writes: str = ""
    write_count: str = ""
    write_paths: str = ""
    primary_outputs: str = ""
    expected_outputs: str = ""
    output_candidates: str = ""
    final_artifact_refs_len: str = ""
    final_artifact_refs_candidate_len: str = ""
    artifact_outputs_len: str = ""
    diagnostic_ref_count: str = ""
    scoring_channel: str = ""
    output_channel: str = ""
    artifact_refs_role: str = ""
    filesystem_primary: str = ""
    route_events: str = ""
    warnings: str = ""
    errors: str = ""
    diagnosis: str = ""
    evidence_confidence: str = ""
    source_files: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        data["source_files"] = ";".join(sorted(self.source_files))
        return {key: redact(value) for key, value in data.items() if key in CSV_FIELDNAMES}


def extract_paths_from_requirements(value: Any) -> list[str]:
    parsed = maybe_parse(value)
    paths: list[str] = []
    if isinstance(parsed, Mapping):
        for key in ("path", "file", "output_path"):
            if parsed.get(key):
                paths.append(str(parsed[key]))
        for nested in parsed.values():
            paths.extend(extract_paths_from_requirements(nested))
    elif isinstance(parsed, list):
        for item in parsed:
            paths.extend(extract_paths_from_requirements(item))
    elif isinstance(parsed, str):
        paths.extend(match.group("path") for match in WRITE_PATH_RE.finditer(parsed))
    return dedupe(paths)


def parse_writes(raw_writes: Any) -> list[dict[str, Any]]:
    writes: list[dict[str, Any]] = []
    for item in to_list(raw_writes):
        parsed = maybe_parse(item)
        if isinstance(parsed, Mapping):
            writes.append(dict(parsed))
        elif isinstance(parsed, str):
            # Last fallback: preserve a path-only record if a string was truncated.
            paths = [m.group("path") for m in WRITE_PATH_RE.finditer(parsed)]
            for path in paths:
                writes.append({"path": path, "ok": True, "raw": parsed[:500]})
    return writes


def selected_solver_from_warnings(warnings: Any) -> str:
    for warning in to_list(warnings):
        text = str(warning)
        match = re.search(r"selected_solver_key=([A-Za-z0-9_.:/-]+)", text)
        if match:
            return match.group(1)
    return ""


def local_evidence_from_final(
    obj: Mapping[str, Any],
    *,
    sequence_index: int,
    position: int,
    source_file: str,
    shard: str,
    route_events: list[Mapping[str, Any]],
) -> LocalEvidence:
    workspace = to_mapping(obj.get("workspace_execution"))
    contract = to_mapping(workspace.get("contract"))
    diagnostics = to_mapping(workspace.get("diagnostics"))
    environment = to_mapping(workspace.get("environment"))
    diag_top = to_mapping(obj.get("aegisforge_diagnostics"))

    writes = parse_writes(workspace.get("writes"))
    ok_writes = [w for w in writes if bool_text(w.get("ok", True)) != "false"]
    write_paths = dedupe(w.get("path") for w in ok_writes if w.get("path"))
    if not write_paths:
        write_paths = dedupe(m.group("path") for m in WRITE_PATH_RE.finditer(json.dumps(obj, default=str)))

    primary_outputs = extract_paths_from_requirements(contract.get("primary_outputs"))
    if not primary_outputs:
        primary_outputs = extract_paths_from_requirements(diagnostics.get("primary_outputs"))
    expected_outputs = extract_paths_from_requirements(contract.get("requirements"))
    output_candidates = extract_paths_from_requirements(environment.get("output_candidates"))

    contract_family = normalize_family(contract.get("family"))
    workspace_family = normalize_family(workspace.get("family"))
    top_family = normalize_family(obj.get("family"))
    output_family = contract_family or workspace_family or top_family

    route_counts: dict[str, int] = {}
    for event in route_events:
        name = str(event.get("event") or event.get("phase") or "event")
        route_counts[name] = route_counts.get(name, 0) + 1

    selected_solver_key = (
        str(diagnostics.get("selected_solver_key") or "")
        or selected_solver_from_warnings(workspace.get("warnings"))
        or str(diagnostics.get("selected_source") or "")
    )

    scoring_channel = str(obj.get("scoring_channel") or diagnostics.get("scoring_channel") or "")
    if not scoring_channel and "filesystem" in json.dumps(obj, default=str).lower():
        scoring_channel = "filesystem_output_primary"

    filesystem_primary = bool_text(
        obj.get("filesystem_primary")
        or diagnostics.get("filesystem_primary")
        or (scoring_channel == "filesystem_output_primary")
    )

    final_refs = to_list(obj.get("artifact_refs"))
    candidate_refs = to_list(obj.get("artifact_refs_candidate"))
    artifact_outputs = to_list(obj.get("artifact_outputs"))

    return LocalEvidence(
        sequence_index=sequence_index,
        source_file=source_file,
        position=position,
        shard=shard,
        local_task_id=str(obj.get("task_id") or workspace.get("task_id") or environment.get("task_id") or ""),
        task_identity_source=str(environment.get("task_identity_source") or ""),
        task_identity_confidence=str(environment.get("task_identity_confidence") or ""),
        task_identity_candidates=compact_json(maybe_parse(environment.get("task_identity_candidates"))),
        top_family=top_family,
        contract_family=contract_family,
        output_family=output_family,
        selected_solver_key=selected_solver_key,
        selected_solver_name=str(diagnostics.get("selected_solver_name") or ""),
        selected_source=str(diagnostics.get("selected_source") or ""),
        workspace_visible=bool_text(workspace.get("workspace_visible")),
        wrote_any_file=bool_text(workspace.get("wrote_any_file") or bool(ok_writes)),
        ok_writes=str(len(ok_writes)) if ok_writes else str(as_int(diagnostics.get("ok_writes"), 0) or ""),
        write_count=str(len(writes)) if writes else str(as_int(diagnostics.get("write_count"), 0) or ""),
        write_paths=compact_json(write_paths),
        primary_outputs=compact_json(primary_outputs),
        expected_outputs=compact_json(expected_outputs),
        output_candidates=compact_json(output_candidates),
        final_artifact_refs_len=str(len(final_refs)) if final_refs else "0" if "artifact_refs" in obj else "",
        final_artifact_refs_candidate_len=str(len(candidate_refs)) if candidate_refs else "0" if "artifact_refs_candidate" in obj else "",
        artifact_outputs_len=str(len(artifact_outputs)) if artifact_outputs else "0" if "artifact_outputs" in obj else "",
        diagnostic_ref_count=str(diag_top.get("ref_count") or ""),
        scoring_channel=scoring_channel,
        output_channel=str(obj.get("output_channel") or diagnostics.get("output_channel") or scoring_channel),
        artifact_refs_role=str(obj.get("artifact_refs_role") or diagnostics.get("artifact_refs_role") or "diagnostic_and_compatibility_signal"),
        filesystem_primary=filesystem_primary,
        route_events=compact_json(route_counts),
        warnings=compact_json(dedupe(workspace.get("warnings") or [])),
        errors=compact_json(dedupe(workspace.get("errors") or [])),
    )


def official_evidence_from_result(
    row: Mapping[str, Any],
    *,
    sequence_index: int,
    position: int,
    source_file: str,
    shard: str,
) -> OfficialEvidence:
    artifact_refs = row.get("artifact_refs")
    tags = row.get("tags")
    return OfficialEvidence(
        sequence_index=sequence_index,
        source_file=source_file,
        position=position,
        shard=shard,
        official_task_id=str(row.get("task_id") or row.get("canonical_task_id") or ""),
        official_trial_id=str(row.get("trial_id") or row.get("id") or ""),
        official_category=str(row.get("category") or ""),
        official_difficulty=str(row.get("difficulty") or ""),
        official_tags=compact_json(tags if isinstance(tags, list) else to_list(tags)),
        official_passed=bool_text(row.get("passed")) if "passed" in row else "",
        official_reward=as_float_text(row.get("reward")) if "reward" in row else "",
        official_max_score=as_float_text(row.get("max_score")) if "max_score" in row else "",
        official_score_eligible=bool_text(row.get("score_eligible")) if "score_eligible" in row else "",
        official_artifact_refs_len=str(len(artifact_refs)) if isinstance(artifact_refs, list) else "",
        official_time_used=as_float_text(row.get("time_used")) if "time_used" in row else "",
        official_infra_failure_type=str(row.get("infra_failure_type") or ""),
        official_error_type=str(row.get("error_type") or ""),
    )


@dataclass
class ParsedFile:
    path: Path
    shard: str
    official: list[OfficialEvidence] = field(default_factory=list)
    local: list[LocalEvidence] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    route_event_count: int = 0
    json_object_count: int = 0


def parse_file(path: Path) -> ParsedFile:
    text = path.read_text(encoding="utf-8", errors="replace")
    normalized = normalize_log_text(text)
    shard = shard_from_path(path, normalized)
    parsed_objects = list(iter_json_objects(normalized))
    route_objects = extract_marker_objects(normalized, "AEGISFORGE_ROUTE_PROBE")
    final_objects = extract_marker_objects(normalized, "SKILLSBENCH_FINAL_RESPONSE_CONTRACT")

    parsed = ParsedFile(path=path, shard=shard, json_object_count=len(parsed_objects), route_event_count=len(route_objects))

    # Official results.
    official_results = choose_official_results(parsed_objects)
    seen_official: set[str] = set()
    for seq, (pos, result) in enumerate(official_results):
        key = str(result.get("trial_id") or result.get("task_id") or seq)
        if key in seen_official:
            continue
        seen_official.add(key)
        parsed.official.append(
            official_evidence_from_result(result, sequence_index=seq, position=pos, source_file=path.name, shard=shard)
        )

    # Local final response contracts. Associate route events by position window.
    route_sorted = sorted(route_objects, key=lambda item: item[0])
    previous_final_pos = -1
    for seq, (pos, obj) in enumerate(sorted(final_objects, key=lambda item: item[0])):
        route_window = [event for event_pos, event in route_sorted if previous_final_pos < event_pos <= pos]
        parsed.local.append(
            local_evidence_from_final(
                obj,
                sequence_index=seq,
                position=pos,
                source_file=path.name,
                shard=shard,
                route_events=route_window,
            )
        )
        previous_final_pos = pos

    # Provenance objects.
    for _, obj in parsed_objects:
        if "image_digests" in obj or "manifest_digests" in obj:
            parsed.provenance.append(obj)

    return parsed


def discover_files(logs_dir: Path, include_outputs: bool = False) -> list[Path]:
    patterns = ("*.txt", "*.log", "*.json", "*.jsonl")
    files: list[Path] = []
    for pattern in patterns:
        files.extend(logs_dir.rglob(pattern))
    skip_names = {
        "skillsbench_forensic_table.csv",
        "skillsbench_forensic_table.jsonl",
        "skillsbench_forensic_summary.json",
        "forensic_table.csv",
    }
    out: list[Path] = []
    for path in sorted(set(files), key=lambda p: str(p)):
        if path.is_dir():
            continue
        if not include_outputs and path.name in skip_names:
            continue
        out.append(path)
    return out


def choose_primary_official(parsed_files: list[ParsedFile]) -> list[OfficialEvidence]:
    candidates = [pf.official for pf in parsed_files if pf.official]
    if not candidates:
        return []
    candidates.sort(key=lambda rows: (len(rows), max((r.position for r in rows), default=0)))
    return candidates[-1]


def choose_primary_local(parsed_files: list[ParsedFile]) -> list[LocalEvidence]:
    candidates = [pf.local for pf in parsed_files if pf.local]
    if not candidates:
        return []
    candidates.sort(key=lambda rows: (len(rows), max((r.position for r in rows), default=0)))
    return candidates[-1]


def merge_row(official: OfficialEvidence | None, local: LocalEvidence | None, join_strategy: str) -> ForensicRow:
    row = ForensicRow(join_strategy=join_strategy)
    if official:
        for key, value in asdict(official).items():
            if key in {"source_file", "position"}:
                continue
            if key == "sequence_index":
                row.sequence_index = str(value)
            elif hasattr(row, key):
                setattr(row, key, redact(value))
        row.source_files.add(official.source_file)
        row.shard = row.shard or official.shard
    if local:
        for key, value in asdict(local).items():
            if key in {"source_file", "position"}:
                continue
            if key == "sequence_index" and not row.sequence_index:
                row.sequence_index = str(value)
            elif hasattr(row, key):
                current = getattr(row, key)
                if not current:
                    setattr(row, key, redact(value))
        row.source_files.add(local.source_file)
        row.shard = row.shard or local.shard
    row.diagnosis = diagnose(row)
    row.evidence_confidence = evidence_confidence(row)
    return row


def build_rows(official: list[OfficialEvidence], local: list[LocalEvidence]) -> tuple[list[ForensicRow], str]:
    rows: list[ForensicRow] = []

    # Exact identity join if local already resolved canonical task IDs.
    local_by_id: dict[str, LocalEvidence] = {item.local_task_id: item for item in local if item.local_task_id}
    used_local: set[int] = set()
    exact_matches = 0
    for off in official:
        loc = local_by_id.get(off.official_task_id)
        if loc:
            exact_matches += 1
            used_local.add(loc.sequence_index)
            rows.append(merge_row(off, loc, "exact_task_id"))

    if exact_matches:
        for loc in local:
            if loc.sequence_index not in used_local:
                rows.append(merge_row(None, loc, "local_unmatched"))
        return rows, "exact_task_id"

    # Sequence join: used when Quick Submit official rows have stable order but
    # the local agent only sees opaque UUID task IDs in A2A request metadata.
    if official and local and len(official) == len(local):
        for off, loc in zip(official, local):
            rows.append(merge_row(off, loc, "sequence_order"))
        return rows, "sequence_order"

    # Partial sequence fallback: preserve all rows, with best-effort pairing.
    max_len = max(len(official), len(local))
    for idx in range(max_len):
        off = official[idx] if idx < len(official) else None
        loc = local[idx] if idx < len(local) else None
        rows.append(merge_row(off, loc, "partial_sequence" if off and loc else "unmatched"))
    return rows, "partial_sequence"


def diagnose(row: ForensicRow) -> str:
    wrote = row.wrote_any_file == "true" or as_int(row.ok_writes) > 0 or bool(row.write_paths)
    reward_zero = row.official_reward in {"0", "0.0", "0.00"}
    passed_false = row.official_passed == "false"
    refs_zero = row.official_artifact_refs_len == "0"
    final_refs = as_int(row.final_artifact_refs_len)
    candidate_refs = as_int(row.final_artifact_refs_candidate_len)
    artifact_outputs = as_int(row.artifact_outputs_len)
    infra_clean = not row.official_infra_failure_type and not row.official_error_type
    generic_family = row.output_family == "general_file_output" or row.contract_family == "general_file_output"

    if row.official_passed == "true":
        return "official_passed"
    if row.official_error_type or row.official_infra_failure_type:
        return "infra_or_runtime_error"
    if row.official_task_id and not row.local_task_id:
        return "official_result_without_local_evidence"
    if row.local_task_id and not row.official_task_id:
        return "local_evidence_without_official_result"
    if row.workspace_visible == "false":
        return "workspace_not_visible"
    if wrote and reward_zero and passed_false and infra_clean and refs_zero:
        if final_refs > 0 or candidate_refs > 0 or artifact_outputs > 0:
            if generic_family:
                return "filesystem_outputs_written+final_refs_present+official_refs_empty+generic_family"
            return "filesystem_outputs_written+final_refs_present+official_refs_empty"
        return "filesystem_outputs_written+official_refs_empty"
    if wrote and reward_zero and passed_false and infra_clean:
        return "filesystem_outputs_not_scored"
    if not wrote and row.workspace_visible == "true":
        if final_refs > 0 or candidate_refs > 0:
            return "artifact_refs_only_no_files_written"
        return "workspace_visible_but_no_file_written"
    if refs_zero and not wrote:
        return "artifact_refs_dropped_no_files"
    if generic_family:
        return "generic_file_output_fallback"
    return "needs_manual_review"


def evidence_confidence(row: ForensicRow) -> str:
    score = 0
    if row.official_task_id:
        score += 2
    if row.local_task_id:
        score += 2
    if row.join_strategy in {"exact_task_id", "sequence_order"}:
        score += 2
    if row.write_paths:
        score += 1
    if row.official_passed or row.official_reward:
        score += 1
    if row.final_artifact_refs_len:
        score += 1
    if score >= 7:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def build_table(logs_dir: Path) -> tuple[list[ForensicRow], dict[str, Any]]:
    files = discover_files(logs_dir)
    parsed_files: list[ParsedFile] = []
    file_errors: list[dict[str, str]] = []

    for path in files:
        try:
            parsed_files.append(parse_file(path))
        except Exception as exc:
            file_errors.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})

    official = choose_primary_official(parsed_files)
    local = choose_primary_local(parsed_files)
    rows, join_strategy = build_rows(official, local)

    diagnosis_counts: dict[str, int] = {}
    for row in rows:
        diagnosis_counts[row.diagnosis] = diagnosis_counts.get(row.diagnosis, 0) + 1

    provenance = []
    for pf in parsed_files:
        for item in pf.provenance:
            provenance.append({"source_file": pf.path.name, "data": item})

    source_summary = []
    for pf in parsed_files:
        source_summary.append(
            {
                "file": pf.path.name,
                "sha256": file_sha256(pf.path),
                "shard": pf.shard,
                "official_rows": len(pf.official),
                "local_final_contracts": len(pf.local),
                "route_events": pf.route_event_count,
                "json_objects": pf.json_object_count,
            }
        )

    summary: dict[str, Any] = {
        "version": VERSION,
        "logs_dir": str(logs_dir),
        "files_scanned": len(files),
        "rows": len(rows),
        "join_strategy": join_strategy,
        "official_rows_selected": len(official),
        "local_final_contracts_selected": len(local),
        "diagnoses": dict(sorted(diagnosis_counts.items())),
        "all_official_artifact_refs_empty": bool(rows) and all(row.official_artifact_refs_len == "0" for row in rows if row.official_task_id),
        "filesystem_write_rows": sum(1 for row in rows if row.wrote_any_file == "true" or as_int(row.ok_writes) > 0 or row.write_paths),
        "final_ref_rows": sum(1 for row in rows if as_int(row.final_artifact_refs_len) > 0 or as_int(row.final_artifact_refs_candidate_len) > 0),
        "provenance": provenance[:3],
        "source_summary": source_summary,
        "file_errors": file_errors,
    }
    return rows, summary


def write_csv(rows: list[ForensicRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            data = row.to_dict()
            writer.writerow({name: data.get(name, "") for name in CSV_FIELDNAMES})


def write_jsonl(rows: list[ForensicRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def write_summary(summary: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a SkillsBench forensic table from Quick Submit logs/artifacts.")
    parser.add_argument("--logs-dir", default=".", help="Directory containing downloaded Quick Submit logs/artifacts")
    parser.add_argument("--out", default="skillsbench_forensic_table.csv", help="Output CSV path")
    parser.add_argument("--jsonl", default="skillsbench_forensic_table.jsonl", help="Output JSONL path")
    parser.add_argument("--summary", default="skillsbench_forensic_summary.json", help="Output summary JSON path")
    args = parser.parse_args(argv)

    rows, summary = build_table(Path(args.logs_dir))
    write_csv(rows, Path(args.out))
    write_jsonl(rows, Path(args.jsonl))
    write_summary(summary, Path(args.summary))

    print(json.dumps({
        "version": VERSION,
        "rows": len(rows),
        "csv": args.out,
        "jsonl": args.jsonl,
        "summary": args.summary,
        "join_strategy": summary.get("join_strategy"),
        "official_rows_selected": summary.get("official_rows_selected"),
        "local_final_contracts_selected": summary.get("local_final_contracts_selected"),
        "diagnoses": summary.get("diagnoses"),
        "all_official_artifact_refs_empty": summary.get("all_official_artifact_refs_empty"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
