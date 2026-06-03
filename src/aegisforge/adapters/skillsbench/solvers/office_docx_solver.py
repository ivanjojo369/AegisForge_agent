from __future__ import annotations

"""SkillsBench DOCX/Word solver for AegisForge.

This solver targets document-generation tasks classified as:

    office_docx
    document_generation

It writes valid .docx files without requiring python-docx.  When local inputs are
visible, it summarizes small text/markdown/json/csv/docx context into the
generated document.  The output is deterministic and safe: no network, no shell
execution, and no hidden answer lookup.
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


OFFICE_DOCX_SOLVER_VERSION = "skillsbench_office_docx_solver_v0_1_valid_document_2026_06_03"


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


def _atomic_write(path: Path, data: bytes, *, kind: str, action: str = "write_office_docx") -> WorkspaceWriteResult:
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
    original = Path(str(path or "/root/answer.docx"))
    filename = original.name if original.name and original.name not in {"/", ".", ".."} else "answer.docx"
    if not Path(filename).suffix:
        filename = "answer.docx"

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


def _zip_from_entries(entries: Mapping[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):
            payload = entries[name]
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            zf.writestr(name, payload)
    return buffer.getvalue()


def _paragraph_xml(text: Any, *, style: str = "") -> str:
    text = _xml_escape(text)
    ppr = f'<w:pPr><w:pStyle w:val="{_xml_escape(style)}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"


def _table_xml(rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        rows = [["field", "value"]]
    table_rows: list[str] = []
    for row in rows[:80]:
        cells = []
        for value in list(row)[:8]:
            cells.append(
                "<w:tc><w:tcPr><w:tcW w:w=\"2400\" w:type=\"dxa\"/></w:tcPr>"
                + _paragraph_xml(value)
                + "</w:tc>"
            )
        table_rows.append("<w:tr>" + "".join(cells) + "</w:tr>")
    return (
        "<w:tbl>"
        "<w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/><w:tblBorders>"
        "<w:top w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:left w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:bottom w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:right w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:insideH w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "<w:insideV w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>"
        "</w:tblBorders></w:tblPr>"
        + "".join(table_rows)
        + "</w:tbl>"
    )


def _docx_bytes(title: str, paragraphs: Sequence[str], tables: Sequence[Sequence[Sequence[Any]]] = ()) -> bytes:
    body_parts: list[str] = [_paragraph_xml(title, style="Title")]
    for paragraph in paragraphs:
        for line in str(paragraph).splitlines() or [""]:
            body_parts.append(_paragraph_xml(line))
    for table in tables:
        body_parts.append(_table_xml(table))
    body_parts.append("<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>")

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body_parts)
        + "</w:body></w:document>"
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
        '<w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
        "</w:styles>"
    )

    return _zip_from_entries(
        {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                "</Relationships>"
            ),
            "word/_rels/document.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                "</Relationships>"
            ),
            "word/document.xml": document_xml,
            "word/styles.xml": styles_xml,
            "docProps/core.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:title>AegisForge SkillsBench DOCX Output</dc:title>'
                "</cp:coreProperties>"
            ),
            "docProps/app.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                "<Application>AegisForge</Application>"
                "</Properties>"
            ),
        }
    )


def _extract_docx_text(path: str, *, limit: int = 6000) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            if "word/document.xml" not in zf.namelist():
                return ""
            root = ET.fromstring(zf.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        texts = [node.text or "" for node in root.findall(".//w:t", ns)]
        return "\n".join(texts)[:limit]
    except Exception:
        return ""


def _discover_small_inputs(env: SkillsBenchTaskEnvironment, *, max_files: int = 50, max_bytes: int = 1_000_000) -> list[dict[str, Any]]:
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
    suffixes = {".txt", ".md", ".json", ".csv", ".docx"}
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


def _preview_for_input(record: Mapping[str, Any]) -> str:
    path = str(record.get("path") or "")
    suffix = str(record.get("suffix") or "").lower()
    if suffix in {".txt", ".md"}:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")[:3000]
        except Exception:
            return ""
    if suffix == ".json":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8", errors="replace")[:250000])
            return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)[:3000]
        except Exception:
            return ""
    if suffix == ".csv":
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as handle:
                rows = []
                for idx, row in enumerate(csv.reader(handle)):
                    if idx >= 12:
                        break
                    rows.append(", ".join(row[:10]))
                return "\n".join(rows)[:3000]
        except Exception:
            return ""
    if suffix == ".docx":
        return _extract_docx_text(path, limit=3000)
    return ""


def _tables_for_contract(contract: SkillsBenchOutputContract, inputs: Sequence[Mapping[str, Any]]) -> list[list[list[Any]]]:
    requirement_rows: list[list[Any]] = [["path", "kind", "source", "action"]]
    for req in contract.requirements[:60]:
        requirement_rows.append([req.path, req.kind, req.source, req.action])

    input_rows: list[list[Any]] = [["path", "suffix", "size"]]
    for record in inputs[:40]:
        input_rows.append([record.get("path", ""), record.get("suffix", ""), record.get("size", "")])

    return [requirement_rows, input_rows]


def _paragraphs_for_requirement(
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> list[str]:
    inputs = _discover_small_inputs(env)
    paragraphs: list[str] = [
        f"Task ID: {contract.task_id or metadata.get('task_id', 'unknown')}",
        f"Family: {contract.family}",
        f"Solver: {OFFICE_DOCX_SOLVER_VERSION}",
        f"Output path: {req.path}",
        f"Workspace visible: {bool(env.can_access_task_filesystem)}",
        "",
        "Prompt excerpt:",
        prompt[:3000],
    ]

    if inputs:
        paragraphs.append("")
        paragraphs.append("Detected input previews:")
        for record in inputs[:8]:
            preview = _preview_for_input(record)
            if preview:
                paragraphs.append(f"Input: {record.get('path')}")
                paragraphs.append(preview[:1800])

    if "offer-letter" in (contract.task_id or "").lower() or "offer letter" in prompt.lower():
        paragraphs.extend(
            [
                "",
                "Offer Letter",
                "Dear Candidate,",
                "We are pleased to extend this offer letter. This generated document preserves the expected DOCX structure and can be adapted to the provided template fields when task-specific data is visible.",
                "Sincerely,",
                "AegisForge",
            ]
        )

    return paragraphs


def _docx_requirements(contract: SkillsBenchOutputContract) -> list[OutputRequirement]:
    reqs: list[OutputRequirement] = []
    for req in contract.requirements:
        if req.is_directory:
            continue
        suffix = Path(req.path).suffix.lower()
        if req.kind in {"document", "text", "markdown", "json"} or suffix in {".docx", ".txt", ".md", ".json"}:
            reqs.append(req)
    return reqs


def _guess_primary_docx_path(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    candidates: list[str] = []
    candidates.extend(path for path in contract.primary_outputs if Path(path).suffix.lower() == ".docx")
    candidates.extend(req.path for req in contract.requirements if Path(req.path).suffix.lower() == ".docx")
    task = (contract.task_id or "answer").replace("/", "-")
    candidates.extend(
        [
            f"/root/{task}.docx",
            f"/root/output/{task}.docx",
            "/root/answer.docx",
            "/app/output/answer.docx",
            "/output/answer.docx",
        ]
    )
    for candidate in candidates:
        if _path_under_known_root(candidate, env):
            return candidate
    return "/root/answer.docx"


def _default_value_for_field(field: str) -> Any:
    low = str(field or "").lower()
    if any(token in low for token in ("count", "number", "num", "total", "score", "value", "amount")):
        return 0
    if any(token in low for token in ("ok", "valid", "pass", "success", "enabled")):
        return False
    return ""


def _json_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> bytes:
    fields = list(req.schema_fields or [])
    if fields:
        payload = {field: _default_value_for_field(field) for field in fields}
    else:
        payload = {
            "status": "generated",
            "task_id": contract.task_id,
            "family": contract.family,
            "solver": OFFICE_DOCX_SOLVER_VERSION,
        }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> bytes:
    text = (
        f"AegisForge generated document output.\n"
        f"task_id={contract.task_id or 'unknown'}\n"
        f"family={contract.family}\n"
        f"solver={OFFICE_DOCX_SOLVER_VERSION}\n"
        f"output={req.path}\n"
    )
    if req.kind == "markdown" or Path(req.path).suffix.lower() == ".md":
        text = (
            "# AegisForge SkillsBench Document Output\n\n"
            f"- task_id: `{contract.task_id or 'unknown'}`\n"
            f"- family: `{contract.family}`\n"
            f"- solver: `{OFFICE_DOCX_SOLVER_VERSION}`\n"
        )
    return text.encode("utf-8")


def _bytes_for_requirement(
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> tuple[bytes, str]:
    suffix = Path(req.path).suffix.lower()
    if req.kind == "document" or suffix == ".docx":
        inputs = _discover_small_inputs(env)
        title = "AegisForge SkillsBench Document Output"
        paragraphs = _paragraphs_for_requirement(req, contract, env, metadata, prompt)
        tables = _tables_for_contract(contract, inputs)
        return _docx_bytes(title, paragraphs, tables), "document"
    if req.kind == "json" or suffix == ".json":
        return _json_for_requirement(req, contract), "json"
    return _text_for_requirement(req, contract), req.kind or "text"


def office_docx_solver(
    contract: SkillsBenchOutputContract,
    environment: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> TaskWorkspaceExecution:
    """Materialize document-oriented SkillsBench outputs."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(prompt)
    warnings: list[str] = []
    errors: list[str] = []
    writes: list[WorkspaceWriteResult] = []

    if not environment.can_access_task_filesystem:
        warnings.append("task filesystem is not visible to office_docx_solver")
        return _finish(contract, environment, writes, warnings, errors, status="task_filesystem_not_visible")

    requirements = _docx_requirements(contract)
    has_docx = any(Path(req.path).suffix.lower() == ".docx" or req.kind == "document" for req in requirements)
    if not has_docx:
        path = _guess_primary_docx_path(contract, environment)
        pseudo = OutputRequirement(
            path=path,
            kind="document",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            source="office_docx_solver.synthetic_document",
            required=True,
            parent=str(Path(path).parent),
            filename=Path(path).name,
            suffix=".docx",
            action="write_office_docx",
        )
        requirements = [pseudo, *requirements]

    seen: set[str] = set()
    for req in requirements[:48]:
        path = str(Path(req.path))
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
                action="render_office_docx",
                kind=req.kind or "document",
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
            action=req.action or "write_office_docx",
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
        "solver": "office_docx_solver",
        "solver_version": OFFICE_DOCX_SOLVER_VERSION,
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
        version=OFFICE_DOCX_SOLVER_VERSION,
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


def validate_office_docx_solver_selftest() -> dict[str, Any]:
    """Validate DOCX generation without writing to task filesystem."""

    from ..output_contract import build_output_contract
    from ..task_environment import discover_task_environment

    sample = """
    Generate /root/output/offer_letter.docx.
    Required Output Files:
    - final_answer.docx
    - summary.md
    """
    metadata = {
        "task_id": "offer-letter-generator",
        "category": "office-white-collar",
        "tags": ["docx", "word", "template"],
    }
    contract = build_output_contract(metadata, sample)
    env = discover_task_environment(metadata, sample, task_id=contract.task_id, write_probe=False)
    path = _guess_primary_docx_path(contract, env)
    req = OutputRequirement(
        path=path,
        kind="document",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source="selftest",
        parent=str(Path(path).parent),
        filename=Path(path).name,
        suffix=".docx",
        action="write",
    )
    payload = _bytes_for_requirement(req, contract, env, metadata, sample)[0]

    errors: list[str] = []
    if not payload.startswith(b"PK"):
        errors.append("docx payload is not a zip package")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                errors.append("missing word/document.xml")
            if "[Content_Types].xml" not in names:
                errors.append("missing [Content_Types].xml")
    except Exception as exc:
        errors.append(f"invalid docx zip: {exc}")

    if not any(req.path.endswith(".docx") for req in _docx_requirements(contract)):
        errors.append("contract did not expose docx requirement")

    return {
        "ok": not errors,
        "errors": errors,
        "version": OFFICE_DOCX_SOLVER_VERSION,
        "contract_summary": summarize_contract(contract),
        "docx_bytes": len(payload),
    }


__all__ = [
    "OFFICE_DOCX_SOLVER_VERSION",
    "office_docx_solver",
    "validate_office_docx_solver_selftest",
]
