#!/usr/bin/env python3
from __future__ import annotations

"""Build a SkillsBench forensic table v3 focused on crossed scoring channels.

This v3 parser is intentionally local/offline and stdlib-only.  It can consume:
- official AgentBeats/SkillsBench result JSON files, including nested-shard shape;
- per-shard forensic CSV/JSONL files produced by v2;
- GitHub Actions text logs that contain AegisForge JSON markers;
- provenance JSON files with image digests.

The main new goal versus v2 is to make "crossed channel / crossed identity"
visible instead of hiding it inside generic 0-score diagnoses.

Typical usage from repo root or an extracted artifact directory:

    python tools/skillsbench_build_forensic_table.py --logs-dir forensics/quick_submit_latest \
      --out forensics/skillsbench_forensic_table_v3.csv \
      --jsonl forensics/skillsbench_forensic_table_v3.jsonl \
      --summary forensics/skillsbench_forensic_summary_v3.json

This tool does not access the network, execute shell commands, or read secrets.
It only reads local files and writes local reports.
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Mapping
from collections import Counter, defaultdict
import argparse
import ast
import csv
import hashlib
import json
import re
import sys


VERSION = "skillsbench_build_forensic_table_v3_crossed_channel_2026_06_11"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
PUBLIC_TASK_RE = re.compile(r"^[a-z0-9][a-z0-9]+(?:-[a-z0-9]+){1,10}$", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
GITHUB_TS_RE = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z\s*")
CONTAINER_PIPE_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:\s+)?\|\s?(.*)$")
SECRETISH_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|Bearer\s+[A-Za-z0-9._~+/-]{20,})"
)

CSV_FIELDNAMES = [
    # official result identity/score
    "official_task_id",
    "official_trial_id",
    "official_shard_index",
    "official_row_index",
    "official_result_shape",
    "official_category",
    "official_difficulty",
    "official_tags",
    "official_passed",
    "official_reward",
    "official_max_score",
    "official_score_eligible",
    "official_artifact_refs_len",
    "official_infra_failure_type",
    "official_error_type",
    "official_time_used",

    # local identity/routing
    "local_task_id",
    "local_task_id_is_uuid",
    "official_task_id_is_public_slug",
    "task_identity_source",
    "task_identity_confidence",
    "task_identity_candidates",
    "candidate_public_task_id",
    "candidate_matches_official_task_id",
    "task_id_match_mode",
    "identity_crossed_suspected",

    # local output/routing evidence
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
    "write_container_roots",
    "primary_outputs",
    "expected_outputs",
    "output_candidates",
    "primary_output_exactly_written",
    "expected_output_exactly_written",
    "path_match_mode",

    # final response / artifact evidence
    "final_artifact_refs_len",
    "final_artifact_refs_candidate_len",
    "artifact_outputs_len",
    "diagnostic_ref_count",
    "scoring_channel",
    "output_channel",
    "artifact_refs_role",
    "filesystem_primary",

    # provenance
    "agent_image_digest",
    "green_image_digest",
    "gateway_image_digest",
    "run_url",
    "provenance_timestamp",

    # v3 diagnosis
    "scoring_channel_evidence",
    "suspected_crossed_channel",
    "crossed_channel_reason",
    "v2_diagnosis",
    "v3_diagnosis",
    "evidence_confidence",
    "join_strategy",
    "source_files",
]


def redact(value: Any) -> str:
    if value is None:
        return ""
    return SECRETISH_RE.sub("***", str(value))


def stable_json(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return redact(value)
    try:
        return redact(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    except Exception:
        return redact(value)


def maybe_parse(value: Any) -> Any:
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


def parse_listish(value: Any) -> list[Any]:
    parsed = maybe_parse(value)
    if parsed in (None, ""):
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, tuple):
        return list(parsed)
    if isinstance(parsed, set):
        return list(parsed)
    return [parsed]


def as_bool_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None or value == "":
        return ""
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "ok", "passed", "pass", "success"}:
        return "true"
    if text in {"false", "0", "no", "n", "failed", "fail", "error"}:
        return "false"
    return text


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        try:
            return int(float(str(value)))
        except Exception:
            return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_line(line: str) -> str:
    line = ANSI_RE.sub("", line).rstrip("\n")
    line = GITHUB_TS_RE.sub("", line)
    match = CONTAINER_PIPE_RE.match(line)
    if match:
        return match.group(1)
    return line


def normalize_log_text(text: str) -> str:
    return "\n".join(normalize_line(line) for line in text.splitlines())


def iter_json_objects(text: str) -> Iterable[dict[str, Any]]:
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
            if isinstance(obj, dict):
                yield obj
            idx = start + max(end, 1)
        except Exception:
            idx = start + 1


def extract_marker_objects(text: str, marker: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    clean = normalize_log_text(text)
    for line in clean.splitlines():
        if marker not in line:
            continue
        tail = line.split(marker, 1)[1].strip()
        # Markers are usually emitted as "MARKER {json}" or "MARKER: {json}".
        tail = tail.lstrip(":=- ")
        if not tail:
            continue
        for parser in (json.loads, ast.literal_eval):
            try:
                obj = parser(tail)
                if isinstance(obj, dict):
                    out.append(obj)
                    break
            except Exception:
                continue
    return out


def discover_files(logs_dir: Path) -> list[Path]:
    patterns = ("*.json", "*.jsonl", "*.csv", "*.txt", "*.log")
    files: list[Path] = []
    for pattern in patterns:
        files.extend(logs_dir.rglob(pattern))
    skip_exact = {
        "skillsbench_forensic_table_v3.csv",
        "skillsbench_forensic_table_v3.jsonl",
        "skillsbench_forensic_summary_v3.json",
    }
    return [p for p in sorted(set(files), key=lambda x: str(x)) if p.is_file() and p.name not in skip_exact]


def flatten_official_results(obj: Any) -> tuple[str, list[dict[str, Any]]]:
    """Return (shape, rows) for official AgentBeats result JSON."""
    if not isinstance(obj, Mapping):
        return "not_mapping", []
    results = obj.get("results")
    if not isinstance(results, list):
        return "invalid_missing_results", []
    if not results:
        return "invalid_empty_results", []

    def is_result_row(x: Any) -> bool:
        return isinstance(x, Mapping) and "task_id" in x and "passed" in x and "reward" in x

    if all(is_result_row(x) for x in results):
        rows = []
        for idx, row in enumerate(results):
            r = dict(row)
            r["_official_shard_index"] = ""
            r["_official_row_index"] = idx
            rows.append(r)
        return "legacy_flat", rows

    nested_rows: list[dict[str, Any]] = []
    nested_ok = True
    for shard_idx, wrapper in enumerate(results):
        if not isinstance(wrapper, Mapping) or not isinstance(wrapper.get("results"), list):
            nested_ok = False
            continue
        for row_idx, row in enumerate(wrapper.get("results") or []):
            if is_result_row(row):
                r = dict(row)
                r["_official_shard_index"] = shard_idx
                r["_official_row_index"] = row_idx
                nested_rows.append(r)
            else:
                nested_ok = False
    if nested_rows:
        return ("nested_shard" if nested_ok else "mixed"), nested_rows
    return "invalid_elements", []


def choose_official_result_set(files: list[Path]) -> tuple[str, list[dict[str, Any]], str]:
    candidates: list[tuple[int, str, list[dict[str, Any]], str, Path]] = []
    for path in files:
        if path.suffix.lower() != ".json":
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        shape, rows = flatten_official_results(obj)
        if rows:
            unique_tasks = len({str(r.get("task_id") or "") for r in rows})
            # Prefer full official result sets and nested shape.
            score = unique_tasks * 10 + len(rows)
            if shape == "nested_shard":
                score += 1000
            candidates.append((score, shape, rows, path.name, path))
    if not candidates:
        return "", [], ""
    candidates.sort(key=lambda x: (x[0], len(x[2])))
    _score, shape, rows, source_name, _path = candidates[-1]
    return shape, rows, source_name


def extract_provenance(files: list[Path]) -> dict[str, str]:
    best: dict[str, str] = {}
    for path in files:
        if path.suffix.lower() != ".json":
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(obj, Mapping):
            continue
        img = obj.get("image_digests")
        if isinstance(img, Mapping):
            best["agent_image_digest"] = str(img.get("/agent") or "")
            best["green_image_digest"] = str(img.get("/green") or "")
            best["gateway_image_digest"] = str(img.get("/gateway") or "")
        gh = obj.get("github_actions")
        if isinstance(gh, Mapping):
            best["run_url"] = str(gh.get("run_url") or "")
        if obj.get("timestamp"):
            best["provenance_timestamp"] = str(obj.get("timestamp") or "")
    return best


def load_preparsed_rows(files: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in files:
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            try:
                for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(row, Mapping) and ("official_task_id" in row or "local_task_id" in row):
                        r = dict(row)
                        r.setdefault("_source_files", set())
                        r["_source_files"] = {path.name}
                        r["_source_line"] = line_no
                        rows.append(r)
            except Exception:
                continue
        elif suffix == ".csv":
            try:
                with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                    reader = csv.DictReader(handle)
                    for line_no, row in enumerate(reader, start=2):
                        if "official_task_id" in (reader.fieldnames or []) or "local_task_id" in (reader.fieldnames or []):
                            r = dict(row)
                            r.setdefault("_source_files", set())
                            r["_source_files"] = {path.name}
                            r["_source_line"] = line_no
                            rows.append(r)
            except Exception:
                continue
    return rows


def dedupe_preparsed_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe CSV/JSONL duplicates. Prefer JSONL over CSV and rows with local evidence."""
    by_task: dict[str, dict[str, Any]] = {}
    for row in rows:
        task = str(row.get("official_task_id") or "")
        key = task or f"__local__{row.get('local_task_id') or id(row)}"
        prev = by_task.get(key)
        if prev is None:
            by_task[key] = row
            continue
        # Merge source files.
        prev_sources = set(prev.get("_source_files") or ())
        new_sources = set(row.get("_source_files") or ())
        source_files = prev_sources | new_sources
        def score(x: Mapping[str, Any]) -> int:
            s = 0
            if x.get("local_task_id"):
                s += 10
            if x.get("write_paths"):
                s += 5
            if x.get("final_artifact_refs_len"):
                s += 2
            if any(str(sf).endswith(".jsonl") for sf in x.get("_source_files", ())):
                s += 1
            return s
        chosen = row if score(row) > score(prev) else prev
        chosen["_source_files"] = source_files
        by_task[key] = chosen
    return list(by_task.values())


def root_for_path(path: str) -> str:
    p = str(path or "").strip()
    if not p:
        return ""
    if p.startswith("/home/github/build/failed"):
        return "/home/github/build/failed"
    if p.startswith("/home/github/build"):
        return "/home/github/build"
    if p.startswith("/app/workspace"):
        return "/app/workspace"
    if p.startswith("/app/output"):
        return "/app/output"
    if p.startswith("/app/data"):
        return "/app/data"
    if p.startswith("/root/output"):
        return "/root/output"
    if p.startswith("/root/data"):
        return "/root/data"
    if p.startswith("/root/input"):
        return "/root/input"
    if p.startswith("/root"):
        return "/root"
    if p.startswith("/output"):
        return "/output"
    if p.startswith("/workspace"):
        return "/workspace"
    if p.startswith("/data"):
        return "/data"
    return p.split("/", 2)[0] if not p.startswith("/") else "/" + p.split("/", 2)[1]


def normalize_path(path: str) -> str:
    path = str(path or "").strip().replace("\\", "/")
    # Trim common punctuation left in log fragments.
    path = path.strip("`'\" ,;)]}")
    while "//" in path:
        path = path.replace("//", "/")
    return path


def parse_paths(value: Any) -> list[str]:
    paths: list[str] = []
    parsed = maybe_parse(value)
    if isinstance(parsed, str):
        text = parsed
        for item in re.findall(r"/(?:root|app|data|output|workspace|home/github/build|logs)[^\"'\s,\]\)]+", text):
            paths.append(normalize_path(item))
    elif isinstance(parsed, list):
        for item in parsed:
            paths.extend(parse_paths(item))
    elif isinstance(parsed, Mapping):
        for key in ("path", "file", "output_path", "uri"):
            if parsed.get(key):
                paths.append(normalize_path(str(parsed[key])))
        for nested in parsed.values():
            paths.extend(parse_paths(nested))
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def candidate_public_task_id(row: Mapping[str, Any]) -> str:
    candidates = maybe_parse(row.get("task_identity_candidates"))
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, Mapping):
                value = str(item.get("value") or "").strip()
                if PUBLIC_TASK_RE.match(value):
                    return value
            elif isinstance(item, str) and PUBLIC_TASK_RE.match(item.strip()):
                return item.strip()
    return ""


def source_files_text(row: Mapping[str, Any]) -> str:
    sources = row.get("_source_files")
    if isinstance(sources, set):
        return ";".join(sorted(sources))
    if isinstance(sources, (list, tuple)):
        return ";".join(sorted(str(x) for x in sources))
    return str(row.get("source_files") or "")


def infer_match_mode(official_task_id: str, local_task_id: str, candidate_task_id: str) -> str:
    if not local_task_id:
        return "no_local_evidence"
    if local_task_id == official_task_id:
        return "exact_public_task_id"
    if candidate_task_id and candidate_task_id == official_task_id:
        return "candidate_public_task_id_matches_official"
    if candidate_task_id and candidate_task_id != official_task_id:
        return "candidate_public_task_id_differs_from_official"
    if UUID_RE.match(local_task_id) and PUBLIC_TASK_RE.match(official_task_id):
        return "local_uuid_vs_official_public_task_id"
    return "unmatched_identity"


def boolish(row: Mapping[str, Any], key: str) -> bool:
    return as_bool_text(row.get(key)) == "true"


def build_evidence_and_diagnosis(out: dict[str, str]) -> tuple[str, str, str, str]:
    evidence: list[str] = []
    reasons: list[str] = []

    official_public = out["official_task_id_is_public_slug"] == "true"
    local_uuid = out["local_task_id_is_uuid"] == "true"
    wrote = out["wrote_any_file"] == "true" or as_int(out["ok_writes"]) > 0 or bool(out["write_paths"])
    workspace = out["workspace_visible"] == "true"
    final_refs = as_int(out["final_artifact_refs_len"]) > 0 or as_int(out["final_artifact_refs_candidate_len"]) > 0 or as_int(out["artifact_outputs_len"]) > 0
    official_refs_empty = out["official_artifact_refs_len"] == "0"
    reward_zero = as_float(out["official_reward"]) == 0.0
    passed_false = out["official_passed"] == "false"
    infra_clean = not out["official_infra_failure_type"] and not out["official_error_type"]
    primary_exact = out["primary_output_exactly_written"] == "true"
    expected_exact = out["expected_output_exactly_written"] == "true"
    candidate_matches = out["candidate_matches_official_task_id"] == "true"

    if out["official_result_shape"]:
        evidence.append(f"official_shape:{out['official_result_shape']}")
    if official_refs_empty:
        evidence.append("official_artifact_refs_empty")
    if final_refs:
        evidence.append("a2a_final_refs_or_candidates_present")
    if wrote:
        evidence.append("filesystem_writes_present")
    if workspace:
        evidence.append("workspace_visible")
    if local_uuid:
        evidence.append("local_task_id_uuid")
    if official_public:
        evidence.append("official_task_id_public_slug")
    if candidate_matches:
        evidence.append("candidate_identity_matches_official")
    if primary_exact:
        evidence.append("primary_output_exactly_written")
    if expected_exact:
        evidence.append("expected_output_exactly_written")
    if infra_clean:
        evidence.append("official_infra_clean")
    if out["agent_image_digest"]:
        evidence.append("agent_image_digest_present")

    suspected = False
    if wrote and reward_zero and passed_false and infra_clean and official_refs_empty and local_uuid and official_public:
        suspected = True
        reasons.append("filesystem outputs exist, but official result is keyed by public task_id while local agent task_id is UUID")
    if final_refs and official_refs_empty:
        suspected = True
        reasons.append("A2A/final artifact candidates exist locally but official artifact_refs are empty")
    if primary_exact and reward_zero and passed_false and infra_clean:
        suspected = True
        reasons.append("exact requested output path appears written but official score remains zero")
    if not out["local_task_id"] and out["official_task_id"]:
        reasons.append("official result has no matched local final/workspace evidence")
    if out["task_id_match_mode"] == "candidate_public_task_id_differs_from_official":
        suspected = True
        reasons.append("local identity candidate points to a different public task_id than the official row")

    if out["official_passed"] == "true":
        diagnosis = "official_passed"
    elif out["official_error_type"] or out["official_infra_failure_type"]:
        diagnosis = "official_infra_or_runtime_error"
    elif not out["local_task_id"]:
        diagnosis = "official_result_without_local_evidence"
    elif suspected and local_uuid:
        diagnosis = "suspected_crossed_identity_or_channel"
    elif wrote and reward_zero and passed_false and official_refs_empty:
        diagnosis = "filesystem_written_but_not_scored"
    elif not wrote and workspace:
        diagnosis = "workspace_visible_but_no_file_written"
    else:
        diagnosis = "needs_manual_review"

    return stable_json(evidence), "true" if suspected else "false", "; ".join(reasons), diagnosis


def build_rows(logs_dir: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    files = discover_files(logs_dir)
    official_shape, official_rows, official_source = choose_official_result_set(files)
    provenance = extract_provenance(files)
    preparsed = dedupe_preparsed_rows(load_preparsed_rows(files))
    local_by_official_task = {str(r.get("official_task_id") or ""): r for r in preparsed if r.get("official_task_id")}

    out_rows: list[dict[str, str]] = []
    for off in official_rows:
        official_task_id = str(off.get("task_id") or "")
        loc = local_by_official_task.get(official_task_id, {})

        write_paths = parse_paths(loc.get("write_paths"))
        primary_outputs = parse_paths(loc.get("primary_outputs"))
        expected_outputs = parse_paths(loc.get("expected_outputs"))
        output_candidates = parse_paths(loc.get("output_candidates"))

        write_set = set(write_paths)
        primary_set = set(primary_outputs)
        expected_set = set(expected_outputs)

        candidate = candidate_public_task_id(loc)
        local_task_id = str(loc.get("local_task_id") or "")
        local_uuid = bool(UUID_RE.match(local_task_id))
        official_public = bool(PUBLIC_TASK_RE.match(official_task_id))
        candidate_matches = bool(candidate and candidate == official_task_id)

        path_match_mode = "none"
        if primary_set and write_set & primary_set:
            path_match_mode = "primary_output_exact"
        elif expected_set and write_set & expected_set:
            path_match_mode = "expected_output_exact"
        elif write_set:
            path_match_mode = "fallback_or_unverified_write_path"

        row: dict[str, str] = {
            "official_task_id": official_task_id,
            "official_trial_id": str(off.get("trial_id") or ""),
            "official_shard_index": str(off.get("_official_shard_index") if off.get("_official_shard_index") is not None else ""),
            "official_row_index": str(off.get("_official_row_index") if off.get("_official_row_index") is not None else ""),
            "official_result_shape": official_shape,
            "official_category": stable_json(off.get("category")),
            "official_difficulty": stable_json(off.get("difficulty")),
            "official_tags": stable_json(off.get("tags")),
            "official_passed": as_bool_text(off.get("passed")),
            "official_reward": str(off.get("reward") if off.get("reward") is not None else ""),
            "official_max_score": str(off.get("max_score") if off.get("max_score") is not None else ""),
            "official_score_eligible": as_bool_text(off.get("score_eligible")),
            "official_artifact_refs_len": str(len(off.get("artifact_refs") or [])),
            "official_infra_failure_type": stable_json(off.get("infra_failure_type")),
            "official_error_type": stable_json(off.get("error_type")),
            "official_time_used": str(off.get("time_used") if off.get("time_used") is not None else ""),

            "local_task_id": stable_json(local_task_id),
            "local_task_id_is_uuid": "true" if local_uuid else "false",
            "official_task_id_is_public_slug": "true" if official_public else "false",
            "task_identity_source": stable_json(loc.get("task_identity_source")),
            "task_identity_confidence": stable_json(loc.get("task_identity_confidence")),
            "task_identity_candidates": stable_json(loc.get("task_identity_candidates")),
            "candidate_public_task_id": stable_json(candidate),
            "candidate_matches_official_task_id": "true" if candidate_matches else "false",
            "task_id_match_mode": infer_match_mode(official_task_id, local_task_id, candidate),
            "identity_crossed_suspected": "true" if local_uuid and official_public and not candidate_matches else "false",

            "contract_family": stable_json(loc.get("contract_family")),
            "output_family": stable_json(loc.get("output_family")),
            "selected_solver_key": stable_json(loc.get("selected_solver_key")),
            "selected_solver_name": stable_json(loc.get("selected_solver_name")),
            "selected_source": stable_json(loc.get("selected_source")),
            "workspace_visible": as_bool_text(loc.get("workspace_visible")),
            "wrote_any_file": as_bool_text(loc.get("wrote_any_file")),
            "ok_writes": stable_json(loc.get("ok_writes")),
            "write_count": stable_json(loc.get("write_count")),
            "write_paths": stable_json(write_paths),
            "write_container_roots": stable_json(sorted(set(root_for_path(p) for p in write_paths if p))),
            "primary_outputs": stable_json(primary_outputs),
            "expected_outputs": stable_json(expected_outputs),
            "output_candidates": stable_json(output_candidates),
            "primary_output_exactly_written": "true" if bool(primary_set and write_set & primary_set) else "false",
            "expected_output_exactly_written": "true" if bool(expected_set and write_set & expected_set) else "false",
            "path_match_mode": path_match_mode,

            "final_artifact_refs_len": stable_json(loc.get("final_artifact_refs_len")),
            "final_artifact_refs_candidate_len": stable_json(loc.get("final_artifact_refs_candidate_len")),
            "artifact_outputs_len": stable_json(loc.get("artifact_outputs_len")),
            "diagnostic_ref_count": stable_json(loc.get("diagnostic_ref_count")),
            "scoring_channel": stable_json(loc.get("scoring_channel")),
            "output_channel": stable_json(loc.get("output_channel")),
            "artifact_refs_role": stable_json(loc.get("artifact_refs_role")),
            "filesystem_primary": stable_json(loc.get("filesystem_primary")),

            "agent_image_digest": stable_json(provenance.get("agent_image_digest")),
            "green_image_digest": stable_json(provenance.get("green_image_digest")),
            "gateway_image_digest": stable_json(provenance.get("gateway_image_digest")),
            "run_url": stable_json(provenance.get("run_url")),
            "provenance_timestamp": stable_json(provenance.get("provenance_timestamp")),

            "v2_diagnosis": stable_json(loc.get("diagnosis")),
            "join_strategy": stable_json(loc.get("join_strategy") or ("official_task_id_from_preparsed" if loc else "official_only")),
            "evidence_confidence": stable_json(loc.get("evidence_confidence")),
            "source_files": source_files_text(loc) or official_source,
        }
        ev, suspected, reason, diagnosis = build_evidence_and_diagnosis(row)
        row["scoring_channel_evidence"] = ev
        row["suspected_crossed_channel"] = suspected
        row["crossed_channel_reason"] = reason
        row["v3_diagnosis"] = diagnosis

        out_rows.append({key: redact(row.get(key, "")) for key in CSV_FIELDNAMES})

    # Include any local-only evidence not represented in official rows.
    official_tasks = {str(r.get("task_id") or "") for r in official_rows}
    for loc in preparsed:
        if loc.get("official_task_id") in official_tasks:
            continue
        # Local-only row.
        row = {key: "" for key in CSV_FIELDNAMES}
        row.update({
            "official_task_id": stable_json(loc.get("official_task_id")),
            "local_task_id": stable_json(loc.get("local_task_id")),
            "local_task_id_is_uuid": "true" if UUID_RE.match(str(loc.get("local_task_id") or "")) else "false",
            "contract_family": stable_json(loc.get("contract_family")),
            "output_family": stable_json(loc.get("output_family")),
            "selected_solver_key": stable_json(loc.get("selected_solver_key")),
            "workspace_visible": as_bool_text(loc.get("workspace_visible")),
            "wrote_any_file": as_bool_text(loc.get("wrote_any_file")),
            "write_paths": stable_json(parse_paths(loc.get("write_paths"))),
            "source_files": source_files_text(loc),
            "join_strategy": "local_only",
            "v3_diagnosis": "local_evidence_without_official_result",
        })
        out_rows.append(row)

    summary = summarize(files, official_shape, official_source, official_rows, preparsed, out_rows, provenance)
    return out_rows, summary


def summarize(
    files: list[Path],
    official_shape: str,
    official_source: str,
    official_rows: list[dict[str, Any]],
    preparsed: list[dict[str, Any]],
    rows: list[dict[str, str]],
    provenance: Mapping[str, str],
) -> dict[str, Any]:
    def count(field: str) -> dict[str, int]:
        return dict(sorted(Counter(row.get(field, "") for row in rows).items()))
    return {
        "version": VERSION,
        "files_scanned": len(files),
        "official_source": official_source,
        "official_result_shape": official_shape,
        "official_rows": len(official_rows),
        "official_unique_tasks": len({str(r.get("task_id") or "") for r in official_rows}),
        "preparsed_rows_loaded": len(preparsed),
        "rows": len(rows),
        "all_official_artifact_refs_empty": bool(rows) and all(row.get("official_artifact_refs_len") == "0" for row in rows if row.get("official_task_id")),
        "all_official_score_eligible": bool(rows) and all(row.get("official_score_eligible") == "true" for row in rows if row.get("official_task_id")),
        "all_official_failed_zero_reward": bool(rows) and all(row.get("official_passed") == "false" and as_float(row.get("official_reward")) == 0.0 for row in rows if row.get("official_task_id")),
        "counts": {
            "v3_diagnosis": count("v3_diagnosis"),
            "suspected_crossed_channel": count("suspected_crossed_channel"),
            "task_id_match_mode": count("task_id_match_mode"),
            "selected_solver_key": count("selected_solver_key"),
            "contract_family": count("contract_family"),
            "path_match_mode": count("path_match_mode"),
            "write_container_roots": count("write_container_roots"),
            "identity_crossed_suspected": count("identity_crossed_suspected"),
        },
        "provenance": dict(provenance),
        "source_sha256": {path.name: file_sha256(path) for path in files[:300]},
    }


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_jsonl(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_summary(summary: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SkillsBench forensic table v3 with crossed-channel evidence.")
    parser.add_argument("--logs-dir", default=".", help="Directory containing downloaded Quick Submit logs/artifacts")
    parser.add_argument("--out", default="skillsbench_forensic_table_v3.csv")
    parser.add_argument("--jsonl", default="skillsbench_forensic_table_v3.jsonl")
    parser.add_argument("--summary", default="skillsbench_forensic_summary_v3.json")
    args = parser.parse_args(argv)

    rows, summary = build_rows(Path(args.logs_dir))
    write_csv(rows, Path(args.out))
    write_jsonl(rows, Path(args.jsonl))
    write_summary(summary, Path(args.summary))

    print(json.dumps({
        "version": VERSION,
        "rows": len(rows),
        "official_result_shape": summary.get("official_result_shape"),
        "official_rows": summary.get("official_rows"),
        "all_official_artifact_refs_empty": summary.get("all_official_artifact_refs_empty"),
        "all_official_failed_zero_reward": summary.get("all_official_failed_zero_reward"),
        "counts": summary.get("counts", {}),
        "csv": args.out,
        "jsonl": args.jsonl,
        "summary": args.summary,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
