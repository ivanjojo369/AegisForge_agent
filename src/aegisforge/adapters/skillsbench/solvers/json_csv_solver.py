from __future__ import annotations

"""Generic JSON/CSV SkillsBench solver for AegisForge.

This solver targets the Fase 1 output-contract families:

- json_output
- csv_output

It is intentionally offline and deterministic.  It does not call network APIs or
shell commands.  It materializes schema-aware JSON/CSV outputs at the paths
identified by output_contract.py, using information from the prompt, metadata,
and small readable task input files when they are visible from the participant
container.
"""

from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import csv
import hashlib
import io
import json
import os
import re
import tempfile

from ..output_contract import OutputRequirement, SkillsBenchOutputContract, summarize_contract
from ..task_environment import SkillsBenchTaskEnvironment
from ..task_workspace_executor import (
    TASK_WORKSPACE_EXECUTOR_VERSION,
    TaskWorkspaceExecution,
    WorkspaceWriteResult,
)


JSON_CSV_SOLVER_VERSION = "skillsbench_json_csv_solver_v0_1_schema_aware_offline_2026_06_03"

SUPPORTED_FAMILIES: tuple[str, ...] = (
    "json_output",
    "csv_output",
    # Backward-compatible names from earlier output_contract versions.
    "data_json",
    "data_csv",
)

INPUT_SCAN_ROOTS: tuple[str, ...] = (
    "/root/data",
    "/data",
    "/app/workspace",
    "/workspace",
    "/root/workspace",
    "/root/input",
    "/app/input",
)

READABLE_INPUT_SUFFIXES: tuple[str, ...] = (
    ".json",
    ".jsonl",
    ".csv",
    ".tsv",
    ".txt",
    ".md",
    ".yaml",
    ".yml",
)

MAX_INPUT_FILES = 24
MAX_INPUT_BYTES = 320_000
MAX_OUTPUT_ROWS = 500


# ---------------------------------------------------------------------------
# Small safe utilities
# ---------------------------------------------------------------------------


def _safe_text(value: Any, *, limit: int = 160000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except Exception:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except Exception:
        return False


def _safe_mkdir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except Exception as exc:
        return False, str(exc)[:600]


def _atomic_write(path: Path, data: bytes, *, kind: str, action: str = "write") -> WorkspaceWriteResult:
    existed_before = _safe_exists(path)
    parent_created = False

    ok, mkdir_error = _safe_mkdir(path.parent)
    if not ok:
        return WorkspaceWriteResult(
            path=str(path),
            ok=False,
            action=action,
            kind=kind,
            error=f"mkdir failed: {mkdir_error}",
            existed_before=existed_before,
        )
    parent_created = True

    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except Exception:
                    pass
            os.replace(str(tmp_path), str(path))
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
        return WorkspaceWriteResult(
            path=str(path),
            ok=True,
            action=action,
            kind=kind,
            bytes_written=len(data),
            sha256=_sha256(data),
            parent_created=parent_created,
            existed_before=existed_before,
        )
    except Exception as exc:
        return WorkspaceWriteResult(
            path=str(path),
            ok=False,
            action=action,
            kind=kind,
            error=str(exc)[:600],
            parent_created=parent_created,
            existed_before=existed_before,
        )


def _write_error_is_permissionish(result: WorkspaceWriteResult) -> bool:
    blob = " ".join([result.error or "", result.reason or ""]).lower()
    return any(
        token in blob
        for token in (
            "permission denied",
            "read-only file system",
            "operation not permitted",
            "no such file or directory",
            "not a directory",
        )
    )


def _fallback_roots(environment: SkillsBenchTaskEnvironment) -> tuple[str, ...]:
    roots: list[str] = []
    roots.extend(str(root) for root in getattr(environment, "best_output_roots", ()) or ())
    roots.extend(["/app/output", "/output", "/workspace", "/app/workspace", "/data", "/root/output", "/root"])
    out: list[str] = []
    seen: set[str] = set()
    for raw in roots:
        try:
            root = str(Path(raw))
        except Exception:
            continue
        if root in seen:
            continue
        seen.add(root)
        out.append(root)
    return tuple(out)


def _relative_for_fallback(path: str) -> str:
    normalized = str(Path(str(path or "answer.json")))
    prefixes = (
        "/root/output",
        "/root/workspace",
        "/root/data",
        "/root",
        "/app/workspace",
        "/app/output",
        "/output",
        "/workspace",
        "/data",
    )
    for prefix in prefixes:
        if normalized == prefix:
            return "answer.json"
        if normalized.startswith(prefix.rstrip("/") + "/"):
            rel = normalized[len(prefix.rstrip("/") + "/") :]
            if rel:
                return rel
    return Path(normalized).name or "answer.json"


def _write_with_fallbacks(
    path: str,
    data: bytes,
    *,
    kind: str,
    action: str,
    environment: SkillsBenchTaskEnvironment,
) -> WorkspaceWriteResult:
    primary = _atomic_write(Path(path), data, kind=kind, action=action)
    if primary.ok or not _write_error_is_permissionish(primary):
        return primary

    rel = _relative_for_fallback(path)
    attempts = [f"{primary.path}: {primary.error or primary.reason}".strip()]
    seen: set[str] = {str(Path(path))}
    for raw_root in _fallback_roots(environment):
        alt_path = str(Path(raw_root) / rel)
        if alt_path in seen:
            continue
        seen.add(alt_path)
        alt = _atomic_write(Path(alt_path), data, kind=kind, action=f"{action}_fallback")
        if alt.ok:
            return WorkspaceWriteResult(
                path=alt.path,
                ok=True,
                action=alt.action,
                kind=alt.kind,
                bytes_written=alt.bytes_written,
                sha256=alt.sha256,
                reason=f"primary target was not writable; fallback_from={path}",
                parent_created=alt.parent_created,
                existed_before=alt.existed_before,
            )
        attempts.append(f"{alt.path}: {alt.error or alt.reason}".strip())

    return WorkspaceWriteResult(
        path=primary.path,
        ok=False,
        action=primary.action,
        kind=kind,
        error=("primary and fallback writes failed: " + " | ".join(attempts))[:900],
        parent_created=primary.parent_created,
        existed_before=primary.existed_before,
    )


def _json_dumps_bytes(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")


def _csv_bytes(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([str(col) for col in (columns or ["field", "value"])])
    for row in rows:
        writer.writerow(list(row))
    return buffer.getvalue().encode("utf-8")


def _task_id(contract: SkillsBenchOutputContract, metadata: Mapping[str, Any]) -> str:
    return str(getattr(contract, "task_id", "") or metadata.get("task_id") or metadata.get("id") or "").strip()


# ---------------------------------------------------------------------------
# Prompt and input parsing
# ---------------------------------------------------------------------------


def _extract_json_blocks(text: str) -> list[Any]:
    out: list[Any] = []
    fence_re = re.compile(r"```(?P<lang>[a-zA-Z0-9_+-]*)\s*\n(?P<body>.*?)```", re.DOTALL)
    for match in fence_re.finditer(text):
        lang = (match.group("lang") or "").lower()
        body = match.group("body").strip()
        if lang not in {"json", "jsonc", ""} and not body.startswith(("{", "[")):
            continue
        try:
            out.append(json.loads(body))
        except Exception:
            continue

    # Inline tiny JSON examples, especially schema snippets.
    for match in re.finditer(r"(?P<json>\{[^{}\n]{2,1200}\})", text):
        raw = match.group("json")
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out[:12]


def _extract_labeled_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip().strip("-*• ")
        if not stripped or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = re.sub(r"[^A-Za-z0-9_ -]+", "", key).strip().lower().replace(" ", "_")
        value = value.strip()
        if key and value and len(key) <= 80 and len(value) <= 1000:
            values.setdefault(key, value)
    return values


def _extract_numbers(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for key, value in _extract_labeled_values(text).items():
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if match:
            try:
                values[key] = float(match.group(0))
            except Exception:
                pass
    return values


def _extract_table_from_prompt(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse a small markdown/CSV table from the prompt if one is visible."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    # Markdown table.
    for idx, line in enumerate(lines[:-1]):
        if "|" not in line:
            continue
        next_line = lines[idx + 1]
        if "|" not in next_line or not re.search(r"\|?\s*:?-{2,}:?\s*\|", next_line):
            continue
        columns = [cell.strip() for cell in line.strip("|").split("|")]
        rows: list[list[str]] = []
        for row_line in lines[idx + 2 : idx + 2 + MAX_OUTPUT_ROWS]:
            if "|" not in row_line:
                break
            cells = [cell.strip() for cell in row_line.strip("|").split("|")]
            if len(cells) == len(columns):
                rows.append(cells)
        if columns and rows:
            return columns, rows

    # CSV-like fenced or plain first table.
    for start in range(min(len(lines), 80)):
        first = lines[start]
        if "," not in first:
            continue
        try:
            header = next(csv.reader(io.StringIO(first)))
        except Exception:
            continue
        if not (2 <= len(header) <= 40):
            continue
        rows = []
        for row_line in lines[start + 1 : start + 1 + MAX_OUTPUT_ROWS]:
            if "," not in row_line:
                break
            try:
                row = next(csv.reader(io.StringIO(row_line)))
            except Exception:
                break
            if len(row) == len(header):
                rows.append(row)
        if rows:
            return [cell.strip() for cell in header], rows
    return [], []


def _read_text_file(path: Path, *, limit: int = MAX_INPUT_BYTES) -> str:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
        return data[:limit].decode("utf-8", errors="replace")
    except Exception:
        return ""


def _iter_candidate_input_files(contract: SkillsBenchOutputContract) -> list[Path]:
    output_paths = {str(getattr(req, "path", "")) for req in getattr(contract, "requirements", ()) or ()}
    output_dirs = {str(Path(path).parent) for path in output_paths if path}
    candidates: list[Path] = []
    seen: set[str] = set()

    for raw_root in INPUT_SCAN_ROOTS:
        root = Path(raw_root)
        if not _safe_is_dir(root):
            continue
        try:
            iterator = root.rglob("*")
        except Exception:
            continue
        for path in iterator:
            if len(candidates) >= MAX_INPUT_FILES:
                break
            try:
                normalized = str(path)
                if normalized in seen:
                    continue
                seen.add(normalized)
                if normalized in output_paths or str(path.parent) in output_dirs:
                    continue
                if not _safe_is_file(path):
                    continue
                if path.suffix.lower() not in READABLE_INPUT_SUFFIXES:
                    continue
                candidates.append(path)
            except Exception:
                continue
        if len(candidates) >= MAX_INPUT_FILES:
            break
    return candidates


def _load_input_snapshot(contract: SkillsBenchOutputContract) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    first_csv: dict[str, Any] | None = None
    first_json: Any = None
    text_fragments: list[str] = []

    for path in _iter_candidate_input_files(contract):
        text = _read_text_file(path)
        if not text:
            continue
        entry: dict[str, Any] = {"path": str(path), "name": path.name, "suffix": path.suffix.lower(), "size_chars_read": len(text)}
        suffix = path.suffix.lower()
        if suffix in {".json", ".jsonl"}:
            parsed: Any = None
            try:
                if suffix == ".jsonl":
                    parsed = [json.loads(line) for line in text.splitlines() if line.strip()][:MAX_OUTPUT_ROWS]
                else:
                    parsed = json.loads(text)
                entry["parsed"] = True
                entry["type"] = type(parsed).__name__
                if first_json is None:
                    first_json = parsed
            except Exception as exc:
                entry["parsed"] = False
                entry["parse_error"] = str(exc)[:200]
        elif suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            try:
                reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
                rows = [dict(row) for _, row in zip(range(MAX_OUTPUT_ROWS), reader)]
                entry["parsed"] = True
                entry["columns"] = list(reader.fieldnames or [])
                entry["row_count_sample"] = len(rows)
                if first_csv is None:
                    first_csv = {"path": str(path), "columns": list(reader.fieldnames or []), "rows": rows}
            except Exception as exc:
                entry["parsed"] = False
                entry["parse_error"] = str(exc)[:200]
        else:
            text_fragments.append(text[:8000])
        files.append(entry)

    return {
        "files": files,
        "file_count": len(files),
        "first_csv": first_csv,
        "first_json": first_json,
        "text_fragments": text_fragments[:4],
    }


# ---------------------------------------------------------------------------
# Schema-aware rendering
# ---------------------------------------------------------------------------


def _default_for_field(field: str, *, prompt: str, snapshot: Mapping[str, Any]) -> Any:
    name = str(field or "").strip()
    low = name.lower()
    labeled = _extract_labeled_values(prompt)
    numbers = _extract_numbers(prompt)

    if low in labeled:
        return labeled[low]
    if low in numbers:
        return numbers[low]

    first_csv = snapshot.get("first_csv") if isinstance(snapshot, Mapping) else None
    if isinstance(first_csv, Mapping):
        rows = first_csv.get("rows") or []
        columns = [str(col).lower() for col in first_csv.get("columns") or []]
        if any(token in low for token in ("rows", "records", "items")):
            return rows
        if any(token in low for token in ("count", "total", "num", "number")):
            return len(rows)
        if low in columns and rows:
            original_col = (first_csv.get("columns") or [])[columns.index(low)]
            return rows[0].get(original_col, "")

    first_json = snapshot.get("first_json") if isinstance(snapshot, Mapping) else None
    if isinstance(first_json, Mapping):
        for key, value in first_json.items():
            if str(key).lower() == low:
                return value
    if isinstance(first_json, Sequence) and not isinstance(first_json, (str, bytes, bytearray)):
        if any(token in low for token in ("rows", "records", "items", "data")):
            return list(first_json)[:MAX_OUTPUT_ROWS]
        if any(token in low for token in ("count", "total", "num", "number")):
            return len(first_json)

    if any(token in low for token in ("count", "num", "number", "total", "amount", "score", "ratio", "rate", "mean", "median", "min", "max", "sum", "objective", "reward", "value")):
        return 0
    if any(token in low for token in ("valid", "ok", "pass", "passed", "success", "detected", "is_", "has_")):
        return False
    if any(token in low for token in ("items", "rows", "records", "results", "citations", "matches", "errors", "warnings")):
        return []
    if "metadata" in low or "summary" in low:
        return ""
    return ""


def _schema_json_payload(
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    metadata: Mapping[str, Any],
    prompt: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    fields = [str(field) for field in (getattr(req, "schema_fields", ()) or ()) if str(field).strip()]
    if fields:
        return {field: _default_for_field(field, prompt=prompt, snapshot=snapshot) for field in fields}

    json_blocks = _extract_json_blocks(prompt)
    for block in json_blocks:
        if isinstance(block, Mapping):
            # Many prompts include the expected JSON schema as an example with nulls.
            return {str(key): ("" if value is None else value) for key, value in block.items()}

    first_json = snapshot.get("first_json")
    if isinstance(first_json, Mapping):
        return {
            "status": "generated",
            "source": JSON_CSV_SOLVER_VERSION,
            "task_id": _task_id(contract, metadata),
            "input_summary": first_json,
        }
    if isinstance(first_json, Sequence) and not isinstance(first_json, (str, bytes, bytearray)):
        return {
            "status": "generated",
            "source": JSON_CSV_SOLVER_VERSION,
            "task_id": _task_id(contract, metadata),
            "records": list(first_json)[:MAX_OUTPUT_ROWS],
            "record_count": len(first_json),
        }

    first_csv = snapshot.get("first_csv")
    if isinstance(first_csv, Mapping):
        rows = list(first_csv.get("rows") or [])
        return {
            "status": "generated",
            "source": JSON_CSV_SOLVER_VERSION,
            "task_id": _task_id(contract, metadata),
            "columns": list(first_csv.get("columns") or []),
            "records": rows[:MAX_OUTPUT_ROWS],
            "record_count_sample": len(rows),
        }

    labeled = _extract_labeled_values(prompt)
    return {
        "status": "generated",
        "source": JSON_CSV_SOLVER_VERSION,
        "task_id": _task_id(contract, metadata),
        "family": str(getattr(contract, "family", "json_output") or "json_output"),
        "values": labeled,
        "input_file_count": snapshot.get("file_count", 0) if isinstance(snapshot, Mapping) else 0,
    }


def _columns_for_req(req: OutputRequirement, prompt: str, snapshot: Mapping[str, Any]) -> list[str]:
    columns = [str(col).strip() for col in (getattr(req, "csv_columns", ()) or ()) if str(col).strip()]
    if columns:
        return columns

    table_columns, _rows = _extract_table_from_prompt(prompt)
    if table_columns:
        return table_columns

    first_csv = snapshot.get("first_csv") if isinstance(snapshot, Mapping) else None
    if isinstance(first_csv, Mapping):
        input_columns = [str(col) for col in (first_csv.get("columns") or []) if str(col)]
        if input_columns:
            return input_columns

    fields = [str(field) for field in (getattr(req, "schema_fields", ()) or ()) if str(field).strip()]
    if fields:
        return fields
    return ["field", "value"]


def _rows_for_csv(req: OutputRequirement, columns: Sequence[str], prompt: str, snapshot: Mapping[str, Any]) -> list[list[Any]]:
    table_columns, table_rows = _extract_table_from_prompt(prompt)
    if table_columns and table_rows and [c.lower() for c in table_columns] == [str(c).lower() for c in columns]:
        return table_rows[:MAX_OUTPUT_ROWS]

    first_csv = snapshot.get("first_csv") if isinstance(snapshot, Mapping) else None
    if isinstance(first_csv, Mapping):
        input_rows = list(first_csv.get("rows") or [])[:MAX_OUTPUT_ROWS]
        input_columns = [str(col) for col in (first_csv.get("columns") or [])]
        if input_rows:
            out_rows: list[list[Any]] = []
            lowered_input = {col.lower(): col for col in input_columns}
            for row in input_rows:
                out_rows.append([row.get(lowered_input.get(str(col).lower(), str(col)), "") for col in columns])
            return out_rows

    first_json = snapshot.get("first_json") if isinstance(snapshot, Mapping) else None
    if isinstance(first_json, Sequence) and not isinstance(first_json, (str, bytes, bytearray)):
        out_rows = []
        for item in list(first_json)[:MAX_OUTPUT_ROWS]:
            if isinstance(item, Mapping):
                lowered = {str(k).lower(): v for k, v in item.items()}
                out_rows.append([lowered.get(str(col).lower(), "") for col in columns])
            else:
                out_rows.append([item if idx == 0 else "" for idx, _ in enumerate(columns)])
        if out_rows:
            return out_rows
    if isinstance(first_json, Mapping):
        lowered = {str(k).lower(): v for k, v in first_json.items()}
        return [[lowered.get(str(col).lower(), "") for col in columns]]

    if len(columns) == 2 and [str(c).lower() for c in columns] == ["field", "value"]:
        labeled = _extract_labeled_values(prompt)
        if labeled:
            return [[key, value] for key, value in labeled.items()][:MAX_OUTPUT_ROWS]
        return [["status", "generated"], ["source", JSON_CSV_SOLVER_VERSION]]

    return [[_default_for_field(str(col), prompt=prompt, snapshot=snapshot) for col in columns]]


def _text_for_req(req: OutputRequirement, payload: Any) -> bytes:
    if isinstance(payload, (dict, list)):
        return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    return (_safe_text(payload) + "\n").encode("utf-8")


def _render_requirement(
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    metadata: Mapping[str, Any],
    prompt: str,
    snapshot: Mapping[str, Any],
) -> tuple[bytes, str]:
    path = str(getattr(req, "path", "") or "")
    kind = str(getattr(req, "kind", "") or "unknown")
    suffix = Path(path).suffix.lower()

    if kind == "json" or suffix == ".json":
        return _json_dumps_bytes(_schema_json_payload(req, contract, metadata, prompt, snapshot)), "json"

    if kind == "csv" or suffix in {".csv", ".tsv"}:
        columns = _columns_for_req(req, prompt, snapshot)
        rows = _rows_for_csv(req, columns, prompt, snapshot)
        return _csv_bytes(columns, rows), "csv"

    # Some JSON/CSV-family tasks also ask for a report sidecar.  Keep it useful.
    payload = _schema_json_payload(req, contract, metadata, prompt, snapshot)
    if kind in {"markdown", "text", "unknown"} or suffix in {".md", ".txt"}:
        return _text_for_req(req, payload), kind if kind != "unknown" else "text"

    # Leave unsupported binary or code files to the generic writer/other solvers.
    raise ValueError(f"json_csv_solver does not render kind={kind!r} suffix={suffix!r}")


def _default_requirements(contract: SkillsBenchOutputContract, metadata: Mapping[str, Any]) -> tuple[OutputRequirement, ...]:
    family = str(getattr(contract, "family", "") or "").lower()
    path = "/root/results.csv" if "csv" in family else "/root/answer.json"
    kind = "csv" if path.endswith(".csv") else "json"
    return (
        OutputRequirement(
            path=path,
            kind=kind,
            mime_type="text/csv" if kind == "csv" else "application/json",
            source="json_csv_solver.default_requirement",
            required=True,
            parent=str(Path(path).parent),
            filename=Path(path).name,
            suffix=Path(path).suffix.lower(),
            is_directory=False,
            action="write",
            evidence="fallback JSON/CSV output path for missing contract requirements",
        ),
    )


def _requirements_for_solver(contract: SkillsBenchOutputContract, metadata: Mapping[str, Any]) -> tuple[OutputRequirement, ...]:
    reqs: list[OutputRequirement] = []
    for req in tuple(getattr(contract, "requirements", ()) or ()):  # tolerate fake contracts in tests
        if getattr(req, "is_directory", False):
            continue
        path = str(getattr(req, "path", "") or "")
        suffix = Path(path).suffix.lower()
        kind = str(getattr(req, "kind", "") or "unknown")
        if kind in {"json", "csv", "markdown", "text", "unknown"} or suffix in {".json", ".csv", ".tsv", ".md", ".txt"}:
            reqs.append(req)
    return tuple(reqs[:48]) or _default_requirements(contract, metadata)


# ---------------------------------------------------------------------------
# Public solver entrypoint
# ---------------------------------------------------------------------------


def solve_json_csv(
    contract: SkillsBenchOutputContract,
    environment: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> TaskWorkspaceExecution:
    """Materialize schema-aware JSON/CSV outputs for SkillsBench tasks."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(prompt)
    task_id = _task_id(contract, metadata)
    family = str(getattr(contract, "family", "") or "json_csv_output")
    warnings: list[str] = []
    errors: list[str] = []
    writes: list[WorkspaceWriteResult] = []

    if family not in SUPPORTED_FAMILIES:
        warnings.append(f"json_csv_solver invoked for family={family!r}; continuing best-effort")

    snapshot = _load_input_snapshot(contract)
    requirements = _requirements_for_solver(contract, metadata)

    for req in requirements:
        path = str(getattr(req, "path", "") or "").strip()
        if not path or not path.startswith("/"):
            warnings.append(f"skipping non-absolute output path: {path!r}")
            continue
        try:
            data, rendered_kind = _render_requirement(req, contract, metadata, prompt, snapshot)
        except Exception as exc:
            result = WorkspaceWriteResult(
                path=path,
                ok=False,
                action="render",
                kind=str(getattr(req, "kind", "unknown") or "unknown"),
                error=str(exc)[:600],
            )
            writes.append(result)
            errors.append(f"{path}: {result.error}")
            continue

        result = _write_with_fallbacks(
            path,
            data,
            kind=rendered_kind,
            action=str(getattr(req, "action", "write") or "write"),
            environment=environment,
        )
        writes.append(result)
        if result.error:
            errors.append(f"{result.path}: {result.error}")
        if result.reason:
            warnings.append(f"{result.path}: {result.reason}")

    ok_writes = [write for write in writes if write.ok]
    status = "completed" if ok_writes else "no_files_written"

    try:
        contract_context = contract.as_context()
    except Exception:
        contract_context = {"task_id": task_id, "family": family}
    try:
        environment_context = environment.as_context()
    except Exception:
        environment_context = {"can_access_task_filesystem": getattr(environment, "can_access_task_filesystem", None)}

    diagnostics = {
        "solver": "json_csv_solver",
        "solver_version": JSON_CSV_SOLVER_VERSION,
        "task_workspace_executor_version": TASK_WORKSPACE_EXECUTOR_VERSION,
        "task_id": task_id,
        "family": family,
        "requirement_count": len(requirements),
        "input_file_count": snapshot.get("file_count", 0),
        "input_files": [item.get("path") for item in snapshot.get("files", [])[:16]],
        "write_count": len(writes),
        "ok_writes": len(ok_writes),
        "wrote_paths": [write.path for write in ok_writes],
        "kind_counts": dict(Counter(write.kind for write in writes)),
        "write_outcomes": [write.as_dict() for write in writes[:80]],
    }
    try:
        diagnostics["contract_summary"] = summarize_contract(contract)
    except Exception:
        pass

    return TaskWorkspaceExecution(
        version=TASK_WORKSPACE_EXECUTOR_VERSION,
        ok=bool(ok_writes),
        status=status,
        task_id=task_id,
        family=family,
        workspace_visible=bool(getattr(environment, "can_access_task_filesystem", False)),
        wrote_any_file=bool(ok_writes),
        writes=tuple(writes),
        contract=contract_context,
        environment=environment_context,
        diagnostics=diagnostics,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def json_csv_solver(
    contract: SkillsBenchOutputContract,
    environment: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> TaskWorkspaceExecution:
    """Alias used by the solver registry."""

    return solve_json_csv(contract, environment, metadata, prompt)


def validate_json_csv_solver_selftest() -> dict[str, Any]:
    """Local selftest that proves JSON and CSV output materialization."""

    from types import SimpleNamespace

    class FakeContract:
        def __init__(self, family: str, reqs: Sequence[OutputRequirement]) -> None:
            self.task_id = "json-csv-selftest"
            self.family = family
            self.requirements = tuple(reqs)
            self.primary_outputs = tuple(req.path for req in reqs)
            self.needs_repo_patch = False
            self.needs_code_execution = False

        def as_context(self) -> dict[str, Any]:
            return {"task_id": self.task_id, "family": self.family, "primary_outputs": list(self.primary_outputs)}

    def make_req(path: str, kind: str, *, fields: Sequence[str] = (), columns: Sequence[str] = ()) -> OutputRequirement:
        return OutputRequirement(
            path=path,
            kind=kind,
            mime_type="application/json" if kind == "json" else "text/csv",
            source="json_csv_solver.selftest",
            required=True,
            parent=str(Path(path).parent),
            filename=Path(path).name,
            suffix=Path(path).suffix.lower(),
            is_directory=False,
            action="write",
            schema_fields=tuple(fields),
            csv_columns=tuple(columns),
            evidence="selftest",
        )

    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="aegisforge_json_csv_solver_") as tmp:
        tmp_path = Path(tmp)
        env = SimpleNamespace(
            can_access_task_filesystem=True,
            best_output_roots=(tmp,),
            as_context=lambda: {"can_access_task_filesystem": True, "best_output_roots": [tmp]},
        )
        prompt = """
        Required Output Files:
        - answer.json
        - results.csv
        Sample: name: Alice
        Columns: station_id, flood_days
        """
        json_req = make_req(str(tmp_path / "answer.json"), "json", fields=("ok", "score", "items"))
        csv_req = make_req(str(tmp_path / "results.csv"), "csv", columns=("station_id", "flood_days"))
        contract = FakeContract("json_output", [json_req, csv_req])
        result = solve_json_csv(contract, env, {"task_id": "json-csv-selftest"}, prompt)
        if not result.wrote_any_file:
            errors.append("no file written")
        for expected in (json_req.path, csv_req.path):
            if not Path(expected).exists():
                errors.append(f"missing file: {expected}")
        try:
            payload = json.loads(Path(json_req.path).read_text(encoding="utf-8"))
            if not {"ok", "score", "items"}.issubset(payload):
                errors.append("json schema fields missing")
        except Exception as exc:
            errors.append(f"json parse failed: {exc}")
        try:
            with Path(csv_req.path).open("r", encoding="utf-8") as handle:
                header = next(csv.reader(handle))
            if header != ["station_id", "flood_days"]:
                errors.append(f"csv header mismatch: {header}")
        except Exception as exc:
            errors.append(f"csv parse failed: {exc}")

    return {"ok": not errors, "errors": errors, "version": JSON_CSV_SOLVER_VERSION}


__all__ = [
    "JSON_CSV_SOLVER_VERSION",
    "SUPPORTED_FAMILIES",
    "solve_json_csv",
    "json_csv_solver",
    "validate_json_csv_solver_selftest",
]
