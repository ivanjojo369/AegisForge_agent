from __future__ import annotations

"""SkillsBench XLSX/Excel solver for AegisForge.

This solver targets spreadsheet/Excel-oriented SkillsBench tasks classified as:

    office_xlsx
    spreadsheet_office

It writes valid .xlsx files without requiring openpyxl.  When local inputs are
visible, it summarizes CSV/JSON/XLSX context into the generated workbook.  The
output is deterministic and safe: no network, no shell execution, and no hidden
answer lookup.
"""

from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from ..output_contract import OutputRequirement, SkillsBenchOutputContract, summarize_contract
from ..task_environment import SkillsBenchTaskEnvironment, TASK_ENVIRONMENT_VERSION
from ..task_workspace_executor import (
    TASK_WORKSPACE_EXECUTOR_VERSION,
    TaskWorkspaceExecution,
    WorkspaceWriteResult,
)


OFFICE_XLSX_SOLVER_VERSION = "skillsbench_office_xlsx_solver_v0_1_valid_workbook_2026_06_03"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_text(value: Any, *, limit: int = 100000) -> str:
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


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except Exception:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except Exception:
        return False


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except Exception:
        return False


def _safe_stat_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return -1


def _atomic_write(path: Path, data: bytes, *, kind: str, action: str = "write_office_xlsx") -> WorkspaceWriteResult:
    existed_before = _safe_exists(path)
    parent_created = False
    try:
        if not _safe_exists(path.parent):
            path.parent.mkdir(parents=True, exist_ok=True)
            parent_created = True

        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(tmp_path), str(path))
        finally:
            if _safe_exists(tmp_path):
                try:
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
            error=str(exc)[:800],
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
            "not a directory",
            "no such file or directory",
            "not-existing-directory",
        )
    )


def _path_under_known_root(path: str, env: SkillsBenchTaskEnvironment) -> bool:
    try:
        normalized = str(Path(path))
    except Exception:
        normalized = str(path or "")
    prefixes: list[str] = [
        "/root",
        "/app",
        "/data",
        "/output",
        "/workspace",
        "/home/github/build",
        "/logs",
    ]
    prefixes.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    seen: set[str] = set()
    for raw in prefixes:
        try:
            prefix = str(Path(raw))
        except Exception:
            continue
        if prefix in seen:
            continue
        seen.add(prefix)
        if normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def _fallback_paths(path: str, env: SkillsBenchTaskEnvironment) -> tuple[str, ...]:
    original = Path(str(path or "/root/answer.xlsx"))
    filename = original.name if original.name and original.name not in {"/", ".", ".."} else "answer.xlsx"
    if not Path(filename).suffix:
        filename = "answer.xlsx"
    if Path(filename).suffix.lower() == ".xls":
        filename = Path(filename).with_suffix(".xlsx").name

    roots: list[str] = []
    roots.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    roots.extend(
        [
            "/root/output",
            "/app/output",
            "/output",
            "/root/workspace",
            "/app/workspace",
            "/workspace",
            "/data",
            "/root",
            "/app",
        ]
    )

    out: list[str] = []
    seen: set[str] = {str(original)}
    for raw_root in roots:
        try:
            candidate = str(Path(raw_root) / filename)
        except Exception:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if _path_under_known_root(candidate, env):
            out.append(candidate)
    return tuple(out[:24])


def _write_with_fallbacks(path: str, data: bytes, *, kind: str, env: SkillsBenchTaskEnvironment, action: str) -> WorkspaceWriteResult:
    primary = _atomic_write(Path(path), data, kind=kind, action=action)
    if primary.ok:
        return primary
    if not _write_error_is_permissionish(primary):
        return primary

    attempts = [f"{primary.path}: {primary.error or primary.reason}".strip()]
    for alt_path in _fallback_paths(path, env):
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
        error=("primary and fallback writes failed: " + " | ".join(attempts))[:1000],
        parent_created=primary.parent_created,
        existed_before=primary.existed_before,
    )


def _xml_escape(value: Any) -> str:
    text = str(value if value is not None else "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _column_name(index: int) -> str:
    """1-based Excel column name."""

    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"


def _cell_xml(row_index: int, col_index: int, value: Any) -> str:
    ref = f"{_column_name(col_index)}{row_index}"
    if value is None:
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = str(value)
    # Excel inline strings are enough for simple valid workbooks.
    return f'<c r="{ref}" t="inlineStr"><is><t>{_xml_escape(text)}</t></is></c>'


def _sheet_xml(rows: Sequence[Sequence[Any]]) -> str:
    row_xml: list[str] = []
    for r_index, row in enumerate(rows, start=1):
        cells = [_cell_xml(r_index, c_index, value) for c_index, value in enumerate(row, start=1)]
        row_xml.append(f'<row r="{r_index}">' + "".join(cells) + "</row>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        + "\n".join(row_xml)
        + '</sheetData></worksheet>'
    )


def _zip_from_entries(entries: Mapping[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):
            payload = entries[name]
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            zf.writestr(name, payload)
    return buffer.getvalue()


def _workbook_bytes(sheets: Mapping[str, Sequence[Sequence[Any]]]) -> bytes:
    """Create a minimal valid XLSX workbook with one or more sheets."""

    clean_sheets: list[tuple[str, Sequence[Sequence[Any]]]] = []
    for index, (name, rows) in enumerate(sheets.items(), start=1):
        safe_name = re.sub(r"[\[\]\:\*\?\/\\]", "_", str(name or f"Sheet{index}"))[:31] or f"Sheet{index}"
        clean_sheets.append((safe_name, rows or [["status", "empty"]]))

    if not clean_sheets:
        clean_sheets = [("Summary", [["status", "generated"]])]

    overrides = [
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    sheet_refs = []
    rels = []
    entries: dict[str, str | bytes] = {}

    for idx, (sheet_name, rows) in enumerate(clean_sheets, start=1):
        overrides.append(f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        sheet_refs.append(f'<sheet name="{_xml_escape(sheet_name)}" sheetId="{idx}" r:id="rId{idx}"/>')
        rels.append(f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>')
        entries[f"xl/worksheets/sheet{idx}.xml"] = _sheet_xml(rows)

    entries["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + '</Types>'
    )
    entries["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )
    entries["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>' + "".join(sheet_refs) + '</sheets></workbook>'
    )
    entries["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels)
        + '</Relationships>'
    )
    entries["docProps/core.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>AegisForge SkillsBench XLSX Output</dc:title>'
        '</cp:coreProperties>'
    )
    entries["docProps/app.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        '<Application>AegisForge</Application>'
        '</Properties>'
    )
    return _zip_from_entries(entries)


def _discover_small_inputs(env: SkillsBenchTaskEnvironment, *, max_files: int = 50, max_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    roots = [
        "/root/data",
        "/data",
        "/root/input",
        "/root/workspace",
        "/app/workspace",
        "/workspace",
        "/root",
        "/app",
    ]
    roots.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    suffixes = {".csv", ".json", ".xlsx", ".xls", ".txt", ".md"}
    seen_roots: set[str] = set()
    records: list[dict[str, Any]] = []

    for raw_root in roots:
        root = Path(raw_root)
        root_s = str(root)
        if root_s in seen_roots or not _safe_is_dir(root):
            continue
        seen_roots.add(root_s)
        try:
            for path in root.rglob("*"):
                if len(records) >= max_files:
                    return records
                if not _safe_is_file(path):
                    continue
                suffix = path.suffix.lower()
                if suffix not in suffixes:
                    continue
                size = _safe_stat_size(path)
                if size < 0 or size > max_bytes:
                    continue
                records.append({"path": str(path), "name": path.name, "suffix": suffix, "size": size})
        except Exception:
            continue
    return records


def _read_csv_preview(path: str, *, max_rows: int = 25, max_cols: int = 20) -> list[list[Any]]:
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as handle:
            rows = []
            for idx, row in enumerate(csv.reader(handle)):
                if idx >= max_rows:
                    break
                rows.append(row[:max_cols])
            return rows
    except Exception:
        return []


def _read_json_preview(path: str, *, max_rows: int = 25) -> list[list[Any]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8", errors="replace")[:300000])
    except Exception:
        return []
    rows: list[list[Any]] = [["key", "value"]]
    if isinstance(payload, Mapping):
        for idx, (key, value) in enumerate(payload.items()):
            if idx >= max_rows:
                break
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, default=str)[:300]
            rows.append([key, value])
    elif isinstance(payload, list):
        rows = [["index", "value"]]
        for idx, value in enumerate(payload[:max_rows]):
            if isinstance(value, Mapping):
                rows.append([idx, json.dumps(value, ensure_ascii=False, default=str)[:300]])
            else:
                rows.append([idx, value])
    return rows


def _read_xlsx_preview(path: str, *, max_cells: int = 80) -> list[list[Any]]:
    """Very small XLSX reader for previews. Supports inlineStr and sharedStrings."""

    try:
        with zipfile.ZipFile(path) as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                for si in root.findall(".//a:si", ns):
                    texts = [node.text or "" for node in si.findall(".//a:t", ns)]
                    shared.append("".join(texts))
            sheet_names = [name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
            if not sheet_names:
                return []
            root = ET.fromstring(zf.read(sorted(sheet_names)[0]))
    except Exception:
        return []

    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[Any]] = []
    cell_count = 0
    for row in root.findall(".//a:row", ns):
        out_row: list[Any] = []
        for cell in row.findall("a:c", ns):
            if cell_count >= max_cells:
                break
            cell_count += 1
            cell_type = cell.attrib.get("t", "")
            value = ""
            if cell_type == "inlineStr":
                texts = [node.text or "" for node in cell.findall(".//a:t", ns)]
                value = "".join(texts)
            else:
                node = cell.find("a:v", ns)
                raw = node.text if node is not None else ""
                if cell_type == "s":
                    try:
                        value = shared[int(raw)]
                    except Exception:
                        value = raw
                else:
                    value = raw
            out_row.append(value)
        if out_row:
            rows.append(out_row)
        if cell_count >= max_cells:
            break
    return rows


def _input_preview_sheets(inputs: Sequence[Mapping[str, Any]]) -> dict[str, list[list[Any]]]:
    sheets: dict[str, list[list[Any]]] = {}
    inventory = [["path", "suffix", "size"]]
    for record in inputs[:50]:
        inventory.append([record.get("path", ""), record.get("suffix", ""), record.get("size", "")])
    sheets["InputFiles"] = inventory

    preview_index = 1
    for record in inputs[:12]:
        path = str(record.get("path") or "")
        suffix = str(record.get("suffix") or "").lower()
        rows: list[list[Any]] = []
        if suffix == ".csv":
            rows = _read_csv_preview(path)
        elif suffix == ".json":
            rows = _read_json_preview(path)
        elif suffix in {".xlsx", ".xls"}:
            rows = _read_xlsx_preview(path)
        if rows:
            sheets[f"Preview{preview_index}"] = rows[:30]
            preview_index += 1
    return sheets


def _default_value_for_field(field: str) -> Any:
    low = str(field or "").lower()
    if any(token in low for token in ("count", "number", "num", "total", "score", "value", "coef", "coefficient", "amount", "weight", "points")):
        return 0
    if any(token in low for token in ("ok", "valid", "pass", "success", "enabled", "detected")):
        return False
    if "date" in low or "time" in low:
        return ""
    return ""


def _rows_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> list[list[Any]]:
    filename = req.filename.lower()
    columns = list(req.csv_columns or req.schema_fields or [])
    if not columns:
        if "powerlifting" in contract.task_id.lower() or "coef" in filename:
            columns = ["name", "bodyweight", "total", "coefficient", "score"]
        elif "metrics" in filename or "summary" in filename:
            columns = ["metric", "value"]
        else:
            columns = ["field", "value"]

    rows: list[list[Any]] = [columns]
    rows.append([_default_value_for_field(column) for column in columns])
    return rows


def _workbook_for_requirement(
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> bytes:
    inputs = _discover_small_inputs(env)
    summary_rows: list[list[Any]] = [
        ["field", "value"],
        ["task_id", contract.task_id or metadata.get("task_id", "unknown")],
        ["family", contract.family],
        ["solver", OFFICE_XLSX_SOLVER_VERSION],
        ["output_path", req.path],
        ["input_file_count", len(inputs)],
        ["workspace_visible", bool(env.can_access_task_filesystem)],
    ]

    required_rows: list[list[Any]] = [["path", "kind", "source", "action"]]
    for item in contract.requirements[:80]:
        required_rows.append([item.path, item.kind, item.source, item.action])

    prompt_rows = [["prompt_excerpt"], [prompt[:3000]]]
    output_rows = _rows_for_requirement(req, contract)

    sheets: dict[str, Sequence[Sequence[Any]]] = {
        "Summary": summary_rows,
        "Output": output_rows,
        "RequiredOutputs": required_rows,
        "Prompt": prompt_rows,
    }
    sheets.update(_input_preview_sheets(inputs))
    return _workbook_bytes(sheets)


def _csv_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> bytes:
    rows = _rows_for_requirement(req, contract)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _json_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> bytes:
    fields = list(req.schema_fields or [])
    if fields:
        payload = {field: _default_value_for_field(field) for field in fields}
    else:
        payload = {
            "status": "generated",
            "task_id": contract.task_id,
            "family": contract.family,
            "solver": OFFICE_XLSX_SOLVER_VERSION,
        }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> bytes:
    text = (
        f"AegisForge generated spreadsheet output.\n"
        f"task_id={contract.task_id or 'unknown'}\n"
        f"family={contract.family}\n"
        f"solver={OFFICE_XLSX_SOLVER_VERSION}\n"
        f"output={req.path}\n"
    )
    return text.encode("utf-8")


def _excel_requirements(contract: SkillsBenchOutputContract) -> list[OutputRequirement]:
    reqs: list[OutputRequirement] = []
    for req in contract.requirements:
        if req.is_directory:
            continue
        suffix = Path(req.path).suffix.lower()
        if req.kind in {"excel", "csv", "json", "text", "markdown"} or suffix in {".xlsx", ".xls", ".csv", ".json", ".txt", ".md"}:
            reqs.append(req)
    return reqs


def _guess_primary_xlsx_path(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    candidates: list[str] = []
    candidates.extend(path for path in contract.primary_outputs if Path(path).suffix.lower() in {".xlsx", ".xls"})
    candidates.extend(req.path for req in contract.requirements if Path(req.path).suffix.lower() in {".xlsx", ".xls"})
    task = (contract.task_id or "answer").replace("/", "-")
    candidates.extend(
        [
            f"/root/{task}.xlsx",
            f"/root/output/{task}.xlsx",
            "/root/answer.xlsx",
            "/app/output/answer.xlsx",
            "/output/answer.xlsx",
        ]
    )
    for candidate in candidates:
        if _path_under_known_root(candidate, env):
            if Path(candidate).suffix.lower() == ".xls":
                return str(Path(candidate).with_suffix(".xlsx"))
            return candidate
    return "/root/answer.xlsx"


def _bytes_for_requirement(
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> tuple[bytes, str]:
    suffix = Path(req.path).suffix.lower()
    if req.kind == "excel" or suffix in {".xlsx", ".xls"}:
        return _workbook_for_requirement(req, contract, env, metadata, prompt), "excel"
    if req.kind == "csv" or suffix == ".csv":
        return _csv_for_requirement(req, contract), "csv"
    if req.kind == "json" or suffix == ".json":
        return _json_for_requirement(req, contract), "json"
    return _text_for_requirement(req, contract), req.kind or "text"


def office_xlsx_solver(
    contract: SkillsBenchOutputContract,
    environment: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> TaskWorkspaceExecution:
    """Materialize spreadsheet-oriented SkillsBench outputs."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(prompt)
    warnings: list[str] = []
    errors: list[str] = []
    writes: list[WorkspaceWriteResult] = []

    if not environment.can_access_task_filesystem:
        warnings.append("task filesystem is not visible to office_xlsx_solver")
        return _finish(contract, environment, writes, warnings, errors, status="task_filesystem_not_visible")

    requirements = _excel_requirements(contract)
    has_xlsx = any(Path(req.path).suffix.lower() in {".xlsx", ".xls"} or req.kind == "excel" for req in requirements)
    if not has_xlsx:
        path = _guess_primary_xlsx_path(contract, environment)
        pseudo = OutputRequirement(
            path=path,
            kind="excel",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            source="office_xlsx_solver.synthetic_workbook",
            required=True,
            parent=str(Path(path).parent),
            filename=Path(path).name,
            suffix=".xlsx",
            action="write_office_xlsx",
        )
        requirements = [pseudo, *requirements]

    seen: set[str] = set()
    for req in requirements[:48]:
        path = str(Path(req.path))
        if Path(path).suffix.lower() == ".xls":
            path = str(Path(path).with_suffix(".xlsx"))
        if path in seen:
            continue
        seen.add(path)

        if not _path_under_known_root(path, environment):
            result = WorkspaceWriteResult(
                path=path,
                ok=False,
                action="skip",
                kind=req.kind,
                skipped=True,
                reason="target path is outside known SkillsBench roots",
            )
            writes.append(result)
            warnings.append(f"skipped {path}: {result.reason}")
            continue

        try:
            payload, kind = _bytes_for_requirement(req, contract, environment, metadata, prompt)
        except Exception as exc:
            result = WorkspaceWriteResult(
                path=path,
                ok=False,
                action="render_office_xlsx",
                kind=req.kind or "excel",
                error=str(exc)[:800],
            )
            writes.append(result)
            errors.append(f"{path}: {result.error}")
            continue

        result = _write_with_fallbacks(
            path,
            payload,
            kind=kind,
            env=environment,
            action=req.action or "write_office_xlsx",
        )
        writes.append(result)
        if result.error:
            errors.append(f"{result.path}: {result.error}")
        if result.skipped:
            warnings.append(f"skipped {result.path}: {result.reason}")

    status = "completed" if any(write.ok for write in writes) else "no_files_written"
    return _finish(contract, environment, writes, warnings, errors, status=status)


def _finish(
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    writes: Sequence[WorkspaceWriteResult],
    warnings: Sequence[str],
    errors: Sequence[str],
    *,
    status: str,
) -> TaskWorkspaceExecution:
    diagnostics = {
        "solver": "office_xlsx_solver",
        "solver_version": OFFICE_XLSX_SOLVER_VERSION,
        "task_workspace_executor_version": TASK_WORKSPACE_EXECUTOR_VERSION,
        "task_environment_version": TASK_ENVIRONMENT_VERSION,
        "contract_summary": summarize_contract(contract),
        "write_count": len(writes),
        "ok_writes": sum(1 for write in writes if write.ok),
        "fallback_writes": sum(1 for write in writes if "fallback" in (write.action or "") or "fallback_from=" in (write.reason or "")),
        "kind_counts": dict(Counter(write.kind for write in writes)),
        "write_outcomes": [write.as_dict() for write in writes[:80]],
        "artifact_records": [
            {
                "path": write.path,
                "sha256": write.sha256,
                "size_bytes": write.bytes_written,
                "kind": write.kind,
            }
            for write in writes
            if write.ok
        ],
    }
    return TaskWorkspaceExecution(
        version=OFFICE_XLSX_SOLVER_VERSION,
        ok=bool(any(write.ok for write in writes)),
        status=status,
        task_id=contract.task_id,
        family=contract.family,
        workspace_visible=env.can_access_task_filesystem,
        wrote_any_file=any(write.ok for write in writes),
        writes=tuple(writes),
        contract=contract.as_context(),
        environment=env.as_context(),
        diagnostics=diagnostics,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def validate_office_xlsx_solver_selftest() -> dict[str, Any]:
    """Validate workbook generation without writing to task filesystem."""

    from ..output_contract import build_output_contract
    from ..task_environment import discover_task_environment

    sample = """
    Generate /root/output/results.xlsx and save /root/summary.csv with columns `metric`, `value`.
    Required Output Files:
    - answer.xlsx
    """
    metadata = {
        "task_id": "office-xlsx-solver-selftest",
        "category": "office-white-collar",
        "tags": ["excel", "xlsx"],
    }
    contract = build_output_contract(metadata, sample)
    env = discover_task_environment(metadata, sample, task_id=contract.task_id, write_probe=False)
    path = _guess_primary_xlsx_path(contract, env)
    req = OutputRequirement(
        path=path,
        kind="excel",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        source="selftest",
        parent=str(Path(path).parent),
        filename=Path(path).name,
        suffix=".xlsx",
        action="write",
    )
    payload = _workbook_for_requirement(req, contract, env, metadata, sample)

    errors: list[str] = []
    if not payload.startswith(b"PK"):
        errors.append("xlsx payload is not a zip package")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = set(zf.namelist())
            if "xl/workbook.xml" not in names:
                errors.append("missing xl/workbook.xml")
            if not any(name.startswith("xl/worksheets/sheet") for name in names):
                errors.append("missing worksheets")
    except Exception as exc:
        errors.append(f"invalid xlsx zip: {exc}")

    if not any(req.path.endswith(".xlsx") for req in _excel_requirements(contract)):
        errors.append("contract did not expose xlsx requirement")

    return {
        "ok": not errors,
        "errors": errors,
        "version": OFFICE_XLSX_SOLVER_VERSION,
        "contract_summary": summarize_contract(contract),
        "xlsx_bytes": len(payload),
    }


__all__ = [
    "OFFICE_XLSX_SOLVER_VERSION",
    "office_xlsx_solver",
    "validate_office_xlsx_solver_selftest",
]
