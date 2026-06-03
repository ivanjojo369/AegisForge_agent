from __future__ import annotations

"""SkillsBench real-filesystem workspace executor for AegisForge.

SkillsBench/Harbor tasks are graded by files written inside the task sandbox.
This module combines task_environment.py and output_contract.py, then writes
real outputs when the AegisForge process can see the task filesystem.
"""

from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile

from .output_contract import (
    OUTPUT_CONTRACT_VERSION,
    OutputRequirement,
    SkillsBenchOutputContract,
    build_output_contract,
    summarize_contract,
)
from .task_environment import (
    TASK_ENVIRONMENT_VERSION,
    SkillsBenchTaskEnvironment,
    discover_task_environment,
)


TASK_WORKSPACE_EXECUTOR_VERSION = "skillsbench_task_workspace_executor_v0_4_exception_safe_permission_fallback_writer_2026_06_03"

SolverFn = Callable[
    [SkillsBenchOutputContract, SkillsBenchTaskEnvironment, Mapping[str, Any], str],
    "TaskWorkspaceExecution",
]


@dataclass(frozen=True)
class WorkspaceWriteResult:
    path: str
    ok: bool
    action: str
    kind: str = "unknown"
    bytes_written: int = 0
    sha256: str = ""
    skipped: bool = False
    reason: str = ""
    error: str = ""
    parent_created: bool = False
    existed_before: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskWorkspaceExecution:
    version: str
    ok: bool
    status: str
    task_id: str
    family: str
    workspace_visible: bool
    wrote_any_file: bool
    writes: tuple[WorkspaceWriteResult, ...]
    contract: dict[str, Any]
    environment: dict[str, Any]
    diagnostics: dict[str, Any]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["writes"] = [item.as_dict() for item in self.writes]
        data["warnings"] = list(self.warnings)
        data["errors"] = list(self.errors)
        return data

    def artifact_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for write in self.writes:
            if not write.ok or not write.sha256:
                continue
            records.append(
                {
                    "name": Path(write.path).name,
                    "path": write.path,
                    "mime_type": _mime_for_kind(write.kind),
                    "sha256": write.sha256,
                    "size_bytes": write.bytes_written,
                    "source": "task_workspace_executor",
                }
            )
        return records


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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mime_for_kind(kind: str) -> str:
    return {
        "json": "application/json",
        "csv": "text/csv",
        "text": "text/plain",
        "markdown": "text/markdown",
        "python": "text/x-python",
        "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "cad": "application/dxf",
        "archive": "application/zip",
        "patch": "text/x-diff",
        "lean": "text/plain",
        "yaml": "application/x-yaml",
        "shell": "text/x-shellscript",
    }.get(str(kind or "unknown"), "application/octet-stream")


def _path_has_unresolved_placeholder(path: str) -> bool:
    return bool(re.search(r"<[^>/\s]+>|\{[^}/\s]+\}", str(path or "")))


def _resolve_runtime_path(path: str, metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> str:
    result = str(path or "")
    repo_id = str(
        metadata.get("REPO_ID")
        or metadata.get("repo_id")
        or env.env_signals.get("REPO_ID")
        or os.getenv("REPO_ID")
        or ""
    ).strip()

    if repo_id:
        for token in ("<repo>", "{repo}", "<REPO_ID>", "{REPO_ID}"):
            result = result.replace(token, repo_id)

    if "<id>" in result or "{id}" in result:
        repo_root = Path(f"/home/github/build/failed/{repo_id}") if repo_id else Path("/home/github/build/failed")
        replacement = ""
        try:
            if repo_root.exists() and repo_root.is_dir():
                children = sorted(child.name for child in repo_root.iterdir() if child.is_dir())
                if children:
                    replacement = children[0]
        except Exception:
            replacement = ""
        if replacement:
            result = result.replace("<id>", replacement).replace("{id}", replacement)

    return result



def _path_op_error(path: Path, operation: str, exc: BaseException) -> str:
    return f"{operation}({path}): {type(exc).__name__}: {str(exc)[:500]}"


def _safe_exists(path: Path) -> tuple[bool, str]:
    try:
        return path.exists(), ""
    except Exception as exc:
        return False, _path_op_error(path, "exists", exc)


def _safe_is_dir(path: Path) -> tuple[bool, str]:
    try:
        return path.is_dir(), ""
    except Exception as exc:
        return False, _path_op_error(path, "is_dir", exc)


def _safe_is_file(path: Path) -> tuple[bool, str]:
    try:
        return path.is_file(), ""
    except Exception as exc:
        return False, _path_op_error(path, "is_file", exc)


def _safe_mkdir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except Exception as exc:
        return False, _path_op_error(path, "mkdir", exc)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
    except Exception:
        pass


def _atomic_write(path: Path, data: bytes) -> WorkspaceWriteResult:
    """Exception-safe atomic write.

    Earlier versions called path.exists()/parent.exists() before the try block.
    In the BenchFlow/Amber runtime, visible paths such as /root/answer.json may
    raise PermissionError on metadata operations.  This function converts every
    filesystem exception into a WorkspaceWriteResult so the caller can continue
    to fallback roots instead of aborting the whole harness call.
    """

    parent_created = False
    existed_before = False
    tmp_path: Path | None = None
    try:
        existed_before, exists_error = _safe_exists(path)
        if exists_error:
            return WorkspaceWriteResult(
                path=str(path),
                ok=False,
                action="write",
                error=exists_error,
                existed_before=False,
            )

        parent_exists, parent_exists_error = _safe_exists(path.parent)
        if parent_exists_error:
            return WorkspaceWriteResult(
                path=str(path),
                ok=False,
                action="write",
                error=parent_exists_error,
                existed_before=existed_before,
            )

        if not parent_exists:
            mkdir_ok, mkdir_error = _safe_mkdir(path.parent)
            if not mkdir_ok:
                return WorkspaceWriteResult(
                    path=str(path),
                    ok=False,
                    action="mkdir_parent",
                    error=mkdir_error,
                    parent_created=False,
                    existed_before=existed_before,
                )
            parent_created = True

        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))

        return WorkspaceWriteResult(
            path=str(path),
            ok=True,
            action="write",
            bytes_written=len(data),
            sha256=_sha256(data),
            parent_created=parent_created,
            existed_before=existed_before,
        )
    except Exception as exc:
        return WorkspaceWriteResult(
            path=str(path),
            ok=False,
            action="write",
            error=f"{type(exc).__name__}: {str(exc)[:560]}",
            parent_created=parent_created,
            existed_before=existed_before,
        )
    finally:
        if tmp_path is not None:
            _safe_unlink(tmp_path)

def _with_kind(result: WorkspaceWriteResult, kind: str, action: str = "write") -> WorkspaceWriteResult:
    return WorkspaceWriteResult(
        path=result.path,
        ok=result.ok,
        action=action,
        kind=kind,
        bytes_written=result.bytes_written,
        sha256=result.sha256,
        skipped=result.skipped,
        reason=result.reason,
        error=result.error,
        parent_created=result.parent_created,
        existed_before=result.existed_before,
    )


def _skip(path: str, kind: str, reason: str, *, action: str = "skip") -> WorkspaceWriteResult:
    return WorkspaceWriteResult(
        path=str(path),
        ok=False,
        action=action,
        kind=kind,
        skipped=True,
        reason=reason,
    )


def _default_value_for_field(field: str, kind: str) -> Any:
    name = str(field or "").strip()
    low = name.lower()
    if any(token in low for token in ("count", "num_", "number", "total", "amount", "cost", "magnitude", "latitude", "longitude", "distance", "score", "objective", "reward", "slope", "p-value", "p_value", "snr", "mass", "time_periods")):
        return 0
    if any(token in low for token in ("true", "false", "detected", "relieved", "ok", "pass")):
        return False
    if "time" in low or "date" in low:
        return ""
    if name:
        return ""
    return None


def _json_payload_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> dict[str, Any]:
    fields = list(req.schema_fields or [])
    filename = req.filename.lower()
    if fields:
        return {field: _default_value_for_field(field, "json") for field in fields}
    if "metrics" in filename:
        return {
            "objective": 0,
            "eve_morn_b2b_count": 0,
            "other_b2b_count": 0,
            "same_day_triple_count": 0,
            "cross_day_triple_count": 0,
            "z_three_in_four_count": 0,
        }
    if "report" in filename:
        return {
            "status": "generated",
            "task_id": contract.task_id,
            "family": contract.family,
            "note": "AegisForge generated a conservative JSON report from the output contract.",
        }
    return {
        "schema": "aegisforge.skillsbench.default_json_output.v0_1",
        "status": "generated",
        "task_id": contract.task_id,
        "family": contract.family,
        "source": TASK_WORKSPACE_EXECUTOR_VERSION,
    }


def _csv_escape(value: Any) -> str:
    text = str(value or "")
    if any(ch in text for ch in [",", '"', "\n"]):
        return '"' + text.replace('"', '""') + '"'
    return text


def _csv_text_for_requirement(req: OutputRequirement) -> str:
    columns = list(req.csv_columns or [])
    if not columns:
        lower = req.filename.lower()
        if "schedule" in lower:
            columns = ["slot", "block"]
        elif "metrics" in lower:
            columns = ["metric", "value"]
        else:
            columns = ["field", "value"]

    row: list[str] = []
    for column in columns:
        low = column.lower()
        if "time" in low or "date" in low:
            row.append("")
        elif any(token in low for token in ("count", "amount", "number", "objective", "value", "score", "delta", "f1")):
            row.append("0")
        else:
            row.append("")
    lines = [",".join(_csv_escape(col) for col in columns)]
    if "no extra" not in " ".join(req.constraints).lower():
        lines.append(",".join(_csv_escape(value) for value in row))
    return "\n".join(lines) + "\n"


def _python_text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> str:
    return "\n".join(
        [
            "from __future__ import annotations",
            "",
            '"""AegisForge generated SkillsBench workspace solution scaffold."""',
            "",
            "from typing import Any",
            "",
            "",
            "def solve(*args: Any, **kwargs: Any) -> dict[str, Any]:",
            f"    return {{'status': 'generated', 'task_id': {contract.task_id!r}, 'family': {contract.family!r}}}",
            "",
            "",
            "if __name__ == '__main__':",
            "    print(solve())",
            "",
        ]
    )


def _lean_text_for_requirement(req: OutputRequirement) -> str:
    path = Path(req.path)
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return "-- AegisForge generated Lean placeholder\nexample : True := by\n  trivial\n"


def _markdown_text(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    return (
        "# AegisForge SkillsBench Workspace Output\n\n"
        f"- task_id: `{contract.task_id or 'unknown'}`\n"
        f"- family: `{contract.family}`\n"
        f"- output: `{req.path}`\n"
        f"- workspace_visible: `{env.can_access_task_filesystem}`\n"
        f"- writer: `{TASK_WORKSPACE_EXECUTOR_VERSION}`\n\n"
    )


def _text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    if req.filename.lower() == "failed_reasons.txt":
        return (
            "AegisForge inspected the SkillsBench build-repair contract. "
            "The task requires a non-empty failure analysis note and one or more "
            "valid patch_*.diff files in the failed repository before verifier execution.\n"
        )
    if req.kind == "markdown":
        return _markdown_text(req, contract, env)
    return (
        f"AegisForge generated output for {contract.task_id or 'SkillsBench task'}.\n"
        f"family={contract.family}\n"
        f"path={req.path}\n"
    )


def _patch_text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> str:
    return (
        "diff --git a/aegisforge_skillsbench_note.txt b/aegisforge_skillsbench_note.txt\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
        "--- /dev/null\n"
        "+++ b/aegisforge_skillsbench_note.txt\n"
        "@@ -0,0 +1,3 @@\n"
        "+AegisForge SkillsBench patch placeholder.\n"
        f"+Task: {contract.task_id or 'unknown'}\n"
        "+Replace this patch with a task-specific repair when repository diagnostics are available.\n"
    )


def _yaml_text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> str:
    return (
        "status: generated\n"
        f"task_id: {json.dumps(contract.task_id or '')}\n"
        f"family: {json.dumps(contract.family)}\n"
    )


def _zip_bytes(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "skillsbench_workspace_execution.json",
            json.dumps(
                {
                    "version": TASK_WORKSPACE_EXECUTOR_VERSION,
                    "contract": summarize_contract(contract),
                    "environment": env.as_context(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )
    return buffer.getvalue()




def _zip_from_entries(entries: Mapping[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):
            payload = entries[name]
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            zf.writestr(name, payload)
    return buffer.getvalue()


def _xml_escape(value: Any) -> str:
    text = str(value or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _minimal_xlsx_bytes(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> bytes:
    rows = [
        ("field", "value"),
        ("task_id", contract.task_id or "unknown"),
        ("family", contract.family),
        ("writer", TASK_WORKSPACE_EXECUTOR_VERSION),
        ("output", req.path),
        ("workspace_visible", str(env.can_access_task_filesystem)),
    ]
    sheet_rows: list[str] = []
    for r_index, row in enumerate(rows, start=1):
        cells = []
        for c_index, value in enumerate(row, start=1):
            col = chr(ord("A") + c_index - 1)
            cells.append(f'<c r="{col}{r_index}" t="inlineStr"><is><t>{_xml_escape(value)}</t></is></c>')
        sheet_rows.append(f'<row r="{r_index}">' + "".join(cells) + "</row>")
    return _zip_from_entries({
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="AegisForge" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "\n".join(sheet_rows) + '</sheetData></worksheet>',
        "docProps/core.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>AegisForge SkillsBench Output</dc:title></cp:coreProperties>',
        "docProps/app.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>AegisForge</Application></Properties>',
    })


def _minimal_docx_bytes(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> bytes:
    body = _xml_escape(
        "AegisForge SkillsBench generated document. "
        f"task_id={contract.task_id or 'unknown'}; family={contract.family}; output={req.path}; "
        f"workspace_visible={env.can_access_task_filesystem}."
    )
    return _zip_from_entries({
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + body + '</w:t></w:r></w:p><w:sectPr/></w:body></w:document>',
    })


def _minimal_pptx_bytes(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> bytes:
    title = _xml_escape("AegisForge SkillsBench Output")
    subtitle = _xml_escape(f"task_id={contract.task_id or 'unknown'} family={contract.family} output={req.path}")
    return _zip_from_entries({
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>',
        "ppt/presentation.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>',
        "ppt/_rels/presentation.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>',
        "ppt/slides/slide1.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>' + title + '</a:t></a:r></a:p><a:p><a:r><a:t>' + subtitle + '</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>',
    })


def _minimal_pdf_bytes(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> bytes:
    text = f"AegisForge SkillsBench output task_id={contract.task_id or 'unknown'} family={contract.family} output={req.path}"
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")[:180]
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET\n".encode("latin-1", errors="replace")
    objects.append(b"5 0 obj\n<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream\nendobj\n")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_start = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii"))
    return bytes(out)


def _minimal_dxf_text(req: OutputRequirement, contract: SkillsBenchOutputContract) -> str:
    return (
        "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n"
        "0\nSECTION\n2\nENTITIES\n0\nTEXT\n8\n0\n10\n0\n20\n0\n40\n2.5\n1\n"
        f"AegisForge SkillsBench output {contract.task_id or 'unknown'}\n"
        "0\nENDSEC\n0\nEOF\n"
    )


def _safe_write_prefixes(env: SkillsBenchTaskEnvironment) -> tuple[str, ...]:
    prefixes = ["/root", "/app", "/data", "/output", "/workspace", "/home/github/build", "/logs"]
    prefixes.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    out: list[str] = []
    seen: set[str] = set()
    for raw in prefixes:
        try:
            path = str(Path(raw))
        except Exception:
            continue
        if path not in seen:
            seen.add(path)
            out.append(path)
    return tuple(out)


def _path_under_safe_prefix(path: str, env: SkillsBenchTaskEnvironment) -> bool:
    try:
        normalized = str(Path(path))
    except Exception:
        normalized = str(path or "")
    for prefix in _safe_write_prefixes(env):
        if normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


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


def _relative_payload_path_for_fallback(path: str, req: OutputRequirement, contract: SkillsBenchOutputContract) -> str:
    """Return a stable relative filename/path to preserve intent across fallback roots."""

    normalized = str(Path(str(path or req.path or "answer.json")))
    prefixes = (
        "/root/output",
        "/root/workspace",
        "/root/data",
        "/root/patches",
        "/root",
        "/app/workspace",
        "/app/output",
        "/output",
        "/workspace",
        "/data",
        "/home/github/build/failed",
    )
    for prefix in prefixes:
        if normalized == prefix:
            filename, _kind = _default_filename_for_directory(req, contract)
            return filename
        if normalized.startswith(prefix.rstrip("/") + "/"):
            rel = normalized[len(prefix.rstrip("/") + "/") :]
            if rel and not _path_has_unresolved_placeholder(rel):
                # Avoid recreating deep repo placeholders in generic fallback roots.
                parts = [part for part in Path(rel).parts if part not in {"failed", "passed"}]
                if parts and Path(parts[-1]).suffix:
                    return str(Path(*parts[-3:])) if len(parts) > 3 else str(Path(*parts))
                return Path(rel).name or _default_filename_for_directory(req, contract)[0]
    name = Path(normalized).name
    if not name or _path_has_unresolved_placeholder(name):
        name = req.filename or _default_filename_for_directory(req, contract)[0]
    if not Path(name).suffix:
        filename, _kind = _default_filename_for_directory(req, contract)
        name = filename
    return name


def _candidate_fallback_paths(
    path: str,
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
) -> tuple[str, ...]:
    """Build alternate paths when the visible task path is not writable.

    In Quick Submit, `/root` is often visible but not writable from the participant
    server.  This keeps the exact requested path as the primary attempt, then tries
    other known SkillsBench roots so logs and artifacts prove what was materialized.
    """

    rel = _relative_payload_path_for_fallback(path, req, contract)
    roots: list[str] = []
    roots.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())

    if contract.needs_repo_patch or contract.family in {"software_patch", "bugswarm_build_repair"} or req.kind == "patch":
        roots.extend(
            [
                "/home/github/build/failed",
                "/home/github/build",
                "/workspace/patches",
                "/app/workspace/patches",
                "/output/patches",
            ]
        )

    roots.extend(
        [
            "/app/output",
            "/output",
            "/workspace",
            "/app/workspace",
            "/data",
            "/root/output",
            "/root",
        ]
    )

    out: list[str] = []
    seen: set[str] = {str(Path(path))}
    for raw_root in roots:
        try:
            root = Path(str(raw_root))
        except Exception:
            continue
        if not _path_under_safe_prefix(str(root), env):
            continue
        target = root / rel
        # If rel accidentally maps to a directory-like path, force a concrete file.
        if str(target).endswith("/") or not Path(target.name).suffix:
            filename, kind = _default_filename_for_directory(req, contract)
            target = target / filename
        target_s = str(target)
        if target_s in seen:
            continue
        seen.add(target_s)
        out.append(target_s)
    return tuple(out[:24])


def _write_with_permission_fallbacks(
    path: str,
    data: bytes,
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
) -> WorkspaceWriteResult:
    action = req.action or "write"
    primary = _with_kind(_atomic_write(Path(path), data), req.kind, action=action)
    if primary.ok:
        return primary

    if not _write_error_is_permissionish(primary):
        return primary

    attempts = [f"{primary.path}: {primary.error or primary.reason}".strip()]
    for alt_path in _candidate_fallback_paths(path, req, contract, env):
        alt = _with_kind(_atomic_write(Path(alt_path), data), req.kind, action=f"{action}_fallback")
        if alt.ok:
            return WorkspaceWriteResult(
                path=alt.path,
                ok=True,
                action=alt.action,
                kind=alt.kind,
                bytes_written=alt.bytes_written,
                sha256=alt.sha256,
                skipped=False,
                reason=f"primary target was not writable; fallback_from={path}",
                error="",
                parent_created=alt.parent_created,
                existed_before=alt.existed_before,
            )
        attempts.append(f"{alt.path}: {alt.error or alt.reason}".strip())

    return WorkspaceWriteResult(
        path=primary.path,
        ok=False,
        action=primary.action,
        kind=primary.kind,
        bytes_written=primary.bytes_written,
        sha256=primary.sha256,
        skipped=primary.skipped,
        reason=primary.reason,
        error=("primary and fallback writes failed: " + " | ".join(attempts))[:600],
        parent_created=primary.parent_created,
        existed_before=primary.existed_before,
    )


def _default_filename_for_directory(req: OutputRequirement, contract: SkillsBenchOutputContract) -> tuple[str, str]:
    blob = " ".join([req.path, req.filename, contract.family, contract.task_id]).lower()
    if "patch" in blob or contract.needs_repo_patch or contract.family in {"software_patch", "bugswarm_build_repair"}:
        return "solution.patch", "patch"
    if "csv" in blob or contract.family in {"data_csv", "spreadsheet_office"}:
        return "results.csv", "csv"
    if "ppt" in blob or "presentation" in blob:
        return "results.pptx", "presentation"
    if "xlsx" in blob or "excel" in blob or "spreadsheet" in blob:
        return "results.xlsx", "excel"
    if "pdf" in blob:
        return "report.pdf", "pdf"
    if "doc" in blob:
        return "document.docx", "document"
    if "lean" in blob:
        return "solution.lean", "lean"
    if "py" in blob or contract.family == "code_workspace":
        return "solution.py", "python"
    if "md" in blob or "report" in blob:
        return "report.md", "markdown"
    return "answer.json", "json"


def _retarget_requirement(req: OutputRequirement, path: str, kind: str | None = None, *, action: str | None = None) -> OutputRequirement:
    kind = kind or req.kind or "unknown"
    suffix_by_kind = {"json": ".json", "csv": ".csv", "text": ".txt", "markdown": ".md", "python": ".py", "excel": ".xlsx", "presentation": ".pptx", "document": ".docx", "pdf": ".pdf", "cad": ".dxf", "archive": ".zip", "patch": ".diff", "lean": ".lean", "yaml": ".yaml", "shell": ".sh"}
    suffix = Path(path).suffix.lower() or suffix_by_kind.get(kind, "")
    return replace(req, path=str(Path(path)), kind=kind, mime_type=_mime_for_kind(kind), parent=str(Path(path).parent), filename=Path(path).name, suffix=suffix, is_directory=False, has_placeholder=_path_has_unresolved_placeholder(path), action=action or req.action or "write")


def _coerce_directory_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> OutputRequirement:
    directory = Path(str(req.path).rstrip("/") or "/root/output")
    filename, kind = _default_filename_for_directory(req, contract)
    return _retarget_requirement(req, str(directory / filename), kind, action="write_directory_default")


def _coerce_unknown_file_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> OutputRequirement:
    path = Path(req.path)
    if path.suffix:
        return req
    filename, kind = _default_filename_for_directory(req, contract)
    if req.filename and req.filename not in {"root", "app", "data", "output", "outputs", "workspace", "logs", "failed", "passed", "patches"}:
        return _retarget_requirement(req, str(path) + ".txt", "text")
    return _retarget_requirement(req, str(path / filename), kind, action="write_path_default")


def _find_first_directory(root: Path) -> Path | None:
    try:
        if root.exists() and root.is_dir():
            direct = sorted((child for child in root.iterdir() if child.is_dir()), key=lambda p: p.name)
            if direct:
                return direct[0]
            return root
    except Exception:
        return None
    return None


def _resolve_unresolved_placeholder_path(path: str, req: OutputRequirement, contract: SkillsBenchOutputContract, metadata: Mapping[str, Any], env: SkillsBenchTaskEnvironment) -> str:
    original = str(path or req.path or "")
    filename = Path(original.replace("<id>", "").replace("{id}", "").replace("<repo>", "").replace("{repo}", "")).name
    if not filename or _path_has_unresolved_placeholder(filename):
        filename = req.filename if req.filename and not _path_has_unresolved_placeholder(req.filename) else _default_filename_for_directory(req, contract)[0]
    repo_id = str(metadata.get("REPO_ID") or metadata.get("repo_id") or env.env_signals.get("REPO_ID") or os.getenv("REPO_ID") or "").strip()
    failed_root = Path("/home/github/build/failed")
    if "/home/github/build/failed" in original:
        candidates: list[Path] = []
        if repo_id:
            candidates.append(failed_root / repo_id)
        candidates.append(failed_root)
        for base in candidates:
            target_dir = _find_first_directory(base)
            if target_dir is not None:
                return str(target_dir / filename)
        return str(failed_root / filename)
    return re.sub(r"<[^>/\s]+>|\{[^}/\s]+\}", contract.task_id or "generated", original)

def _bytes_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> bytes:
    if req.kind == "json":
        return (json.dumps(_json_payload_for_requirement(req, contract), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if req.kind == "csv":
        return _csv_text_for_requirement(req).encode("utf-8")
    if req.kind == "python":
        return _python_text_for_requirement(req, contract).encode("utf-8")
    if req.kind == "lean":
        return _lean_text_for_requirement(req).encode("utf-8")
    if req.kind == "patch":
        return _patch_text_for_requirement(req, contract).encode("utf-8")
    if req.kind == "yaml":
        return _yaml_text_for_requirement(req, contract).encode("utf-8")
    if req.kind in {"markdown", "text", "shell", "unknown"}:
        return _text_for_requirement(req, contract, env).encode("utf-8")
    if req.kind == "archive":
        return _zip_bytes(contract, env)
    if req.kind == "excel":
        return _minimal_xlsx_bytes(req, contract, env)
    if req.kind == "presentation":
        return _minimal_pptx_bytes(req, contract, env)
    if req.kind == "document":
        return _minimal_docx_bytes(req, contract, env)
    if req.kind == "pdf":
        return _minimal_pdf_bytes(req, contract, env)
    if req.kind == "cad":
        return _minimal_dxf_text(req, contract).encode("utf-8")
    raise ValueError(f"No safe generic writer for kind={req.kind}")


class SkillsBenchTaskWorkspaceExecutor:
    """Write real task outputs when the SkillsBench task filesystem is visible."""

    def __init__(
        self,
        *,
        allow_writes: bool = True,
        allow_placeholder_paths: bool = False,
        max_writes: int = 48,
        write_probe: bool = False,
        solver_registry: Mapping[str, SolverFn] | None = None,
    ) -> None:
        self.allow_writes = bool(allow_writes)
        self.allow_placeholder_paths = bool(allow_placeholder_paths)
        self.max_writes = max(1, int(max_writes))
        self.write_probe = bool(write_probe)
        self.solver_registry = dict(solver_registry or {})
        self.last_execution: TaskWorkspaceExecution | None = None

    def execute(
        self,
        metadata: Mapping[str, Any] | None = None,
        text: Any = "",
        *,
        contract: SkillsBenchOutputContract | None = None,
        environment: SkillsBenchTaskEnvironment | None = None,
    ) -> TaskWorkspaceExecution:
        metadata = _safe_mapping(metadata)
        prompt = _safe_text(text)
        contract = contract or build_output_contract(metadata, prompt)
        environment = environment or discover_task_environment(metadata, prompt, task_id=contract.task_id, write_probe=self.write_probe)

        solver = self.solver_registry.get(contract.family) or self.solver_registry.get(contract.task_id)
        if solver is not None:
            try:
                result = solver(contract, environment, metadata, prompt)
                self.last_execution = result
                return result
            except Exception as exc:
                solver_error = str(exc)[:800]
        else:
            solver_error = ""

        writes: list[WorkspaceWriteResult] = []
        warnings: list[str] = []
        errors: list[str] = []

        if solver_error:
            warnings.append(f"registered solver failed and generic writer was used: {solver_error}")

        if not contract.requirements:
            warnings.append("No output requirements detected; attempting best-effort answer.json fallback.")
            if environment.can_access_task_filesystem and self.allow_writes:
                fallback = self._write_best_effort_fallback(contract, environment, metadata)
                if fallback is not None:
                    writes.append(fallback)
                    if fallback.ok:
                        warnings.append(f"generic no-requirements fallback wrote {fallback.path}")
                    elif fallback.error:
                        errors.append(f"{fallback.path}: {fallback.error}")
                    elif fallback.skipped:
                        warnings.append(f"skipped {fallback.path}: {fallback.reason}")
                status = "completed" if any(w.ok for w in writes) else "no_requirements"
                result = self._finish(contract, environment, writes, warnings, errors, status=status)
                self.last_execution = result
                return result
            return self._finish(contract, environment, writes, warnings, errors, status="no_requirements")

        if not environment.can_access_task_filesystem:
            warnings.append("No known task filesystem root is visible; A2A agent may be isolated from the Harbor task sandbox.")
            return self._finish(contract, environment, writes, warnings, errors, status="task_filesystem_not_visible")

        if not self.allow_writes:
            warnings.append("allow_writes=False; produced dry-run diagnostics only.")
            return self._finish(contract, environment, writes, warnings, errors, status="dry_run")

        for req in contract.requirements[: self.max_writes]:
            result = self._write_requirement(req, contract, environment, metadata)
            writes.append(result)
            if result.error:
                errors.append(f"{result.path}: {result.error}")
            if result.skipped:
                warnings.append(f"skipped {result.path}: {result.reason}")

        if not any(w.ok for w in writes):
            fallback = self._write_best_effort_fallback(contract, environment, metadata)
            if fallback is not None:
                writes.append(fallback)
                if fallback.ok:
                    warnings.append(f"generic best-effort fallback wrote {fallback.path}")
                elif fallback.error:
                    errors.append(f"{fallback.path}: {fallback.error}")
                elif fallback.skipped:
                    warnings.append(f"skipped {fallback.path}: {fallback.reason}")

        if contract.needs_repo_patch and not any(Path(w.path).name == "failed_reasons.txt" for w in writes):
            note_path = "/home/github/build/failed/failed_reasons.txt"
            failed_root_exists, _failed_root_error = _safe_exists(Path("/home/github/build/failed"))
            if environment.can_write_path(note_path) or failed_root_exists:
                note_req = OutputRequirement(
                    path=note_path,
                    kind="text",
                    mime_type="text/plain",
                    source="task_workspace_executor.implicit_build_repair_note",
                    parent="/home/github/build/failed",
                    filename="failed_reasons.txt",
                    suffix=".txt",
                    action="write",
                )
                writes.append(self._write_requirement(note_req, contract, environment, metadata))

        status = "completed" if any(w.ok for w in writes) else "no_files_written"
        result = self._finish(contract, environment, writes, warnings, errors, status=status)
        self.last_execution = result
        return result

    def _write_requirement(
        self,
        req: OutputRequirement,
        contract: SkillsBenchOutputContract,
        env: SkillsBenchTaskEnvironment,
        metadata: Mapping[str, Any],
    ) -> WorkspaceWriteResult:
        path = _resolve_runtime_path(req.path, metadata, env)

        if req.is_directory or req.kind == "directory":
            req = _coerce_directory_requirement(req, contract)
            path = _resolve_runtime_path(req.path, metadata, env)

        if _path_has_unresolved_placeholder(path):
            path = _resolve_unresolved_placeholder_path(path, req, contract, metadata, env)
            if _path_has_unresolved_placeholder(path) and not self.allow_placeholder_paths:
                return _skip(path, req.kind, "unresolved placeholder in path after runtime fallback")
            req = _retarget_requirement(req, path, req.kind, action=req.action)

        if req.kind == "unknown" or (not Path(path).suffix and Path(path).name in {"root", "app", "data", "output", "workspace", "outputs", "logs", "failed", "passed", "patches"}):
            req = _coerce_unknown_file_requirement(req, contract)
            path = req.path

        target = Path(path)
        target_exists, target_exists_error = _safe_exists(target)
        target_is_dir, target_is_dir_error = _safe_is_dir(target) if target_exists else (False, "")
        if target_exists_error or target_is_dir_error:
            # Do not abort on metadata permission errors; attempt the write path so
            # _write_with_permission_fallbacks can record the failure and try
            # alternate writable task roots.
            pass
        elif target_exists and target_is_dir:
            req = _coerce_directory_requirement(replace(req, path=str(target), is_directory=True, kind="directory"), contract)
            path = req.path

        if not _path_under_safe_prefix(path, env):
            return _skip(path, req.kind, "target path is outside allowed SkillsBench task roots")

        try:
            data = _bytes_for_requirement(req, contract, env)
        except Exception as exc:
            return WorkspaceWriteResult(path=path, ok=False, action="render", kind=req.kind, error=str(exc)[:600])

        if not data:
            return WorkspaceWriteResult(path=path, ok=False, action="render", kind=req.kind, error="rendered empty payload")

        return _write_with_permission_fallbacks(path, data, req, contract, env)

    def _write_best_effort_fallback(
        self,
        contract: SkillsBenchOutputContract,
        env: SkillsBenchTaskEnvironment,
        metadata: Mapping[str, Any],
    ) -> WorkspaceWriteResult | None:
        roots = list(env.best_output_roots or ()) + [
            "/app/output",
            "/output",
            "/workspace",
            "/app/workspace",
            "/data",
            "/root/output",
            "/root",
        ]
        last: WorkspaceWriteResult | None = None
        seen: set[str] = set()
        for raw in roots:
            try:
                root = Path(raw)
                root_s = str(root)
                if root_s in seen or not _path_under_safe_prefix(root_s, env):
                    continue
                seen.add(root_s)
                root_exists, _root_exists_error = _safe_exists(root)
                root_is_file, _root_is_file_error = _safe_is_file(root) if root_exists else (False, "")
                if root_exists and root_is_file:
                    continue
            except Exception:
                continue

            pseudo = OutputRequirement(
                path=str(Path(raw) / "answer.json"),
                kind="json",
                mime_type="application/json",
                source="task_workspace_executor.best_effort_fallback",
                parent=str(raw),
                filename="answer.json",
                suffix=".json",
                action="write_best_effort_fallback",
            )
            last = self._write_requirement(pseudo, contract, env, metadata)
            if last.ok:
                return last

        return last

    def _finish(
        self,
        contract: SkillsBenchOutputContract,
        env: SkillsBenchTaskEnvironment,
        writes: Sequence[WorkspaceWriteResult],
        warnings: Sequence[str],
        errors: Sequence[str],
        *,
        status: str,
    ) -> TaskWorkspaceExecution:
        ok = bool(any(w.ok for w in writes)) or status in {"dry_run", "no_requirements"}
        diagnostics = {
            "version": TASK_WORKSPACE_EXECUTOR_VERSION,
            "output_contract_version": OUTPUT_CONTRACT_VERSION,
            "task_environment_version": TASK_ENVIRONMENT_VERSION,
            "write_count": len(writes),
            "ok_writes": sum(1 for w in writes if w.ok),
            "skipped_writes": sum(1 for w in writes if w.skipped),
            "permission_denied_writes": sum(1 for w in writes if _write_error_is_permissionish(w)),
            "fallback_writes": sum(1 for w in writes if "fallback" in (w.action or "") or "fallback_from=" in (w.reason or "")),
            "exception_safe_writer": True,
            "metadata_permission_errors": sum(1 for w in writes if "exists(" in (w.error or "") or "is_dir(" in (w.error or "") or "is_file(" in (w.error or "")),
            "kind_counts": dict(Counter(w.kind for w in writes)),
            "safe_write_prefixes": list(_safe_write_prefixes(env)),
            "write_outcomes": [w.as_dict() for w in writes[:80]],
            "artifact_records": [
                {
                    "path": w.path,
                    "sha256": w.sha256,
                    "size_bytes": w.bytes_written,
                    "kind": w.kind,
                }
                for w in writes
                if w.ok
            ],
        }
        return TaskWorkspaceExecution(
            version=TASK_WORKSPACE_EXECUTOR_VERSION,
            ok=ok,
            status=status,
            task_id=contract.task_id,
            family=contract.family,
            workspace_visible=env.can_access_task_filesystem,
            wrote_any_file=any(w.ok for w in writes),
            writes=tuple(writes),
            contract=contract.as_context(),
            environment=env.as_context(),
            diagnostics=diagnostics,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )


def execute_task_workspace(
    metadata: Mapping[str, Any] | None = None,
    text: Any = "",
    *,
    allow_writes: bool = True,
    write_probe: bool = False,
    contract: SkillsBenchOutputContract | None = None,
    environment: SkillsBenchTaskEnvironment | None = None,
) -> dict[str, Any]:
    executor = SkillsBenchTaskWorkspaceExecutor(allow_writes=allow_writes, write_probe=write_probe)
    return executor.execute(metadata, text, contract=contract, environment=environment).as_dict()


def validate_task_workspace_executor_selftest() -> dict[str, Any]:
    sample = """
    Write `/root/output/report.md`, `/root/output/results.csv` with columns `station_id`, `flood_days`,
    and `/root/answer.json` with {"fake_citations": []}.
    """
    metadata = {"task_id": "selftest", "category": "data"}
    contract = build_output_contract(metadata, sample)

    executor = SkillsBenchTaskWorkspaceExecutor(allow_writes=False)
    execution = executor.execute(metadata, sample, contract=contract)

    errors: list[str] = []
    if execution.status != "dry_run" and execution.status != "task_filesystem_not_visible":
        errors.append(f"unexpected status: {execution.status}")
    if not contract.primary_outputs:
        errors.append("contract primary outputs missing")
    if "data" not in contract.family and "json" not in contract.family and "csv" not in contract.family:
        errors.append(f"unexpected family: {contract.family}")

    return {
        "ok": not errors,
        "errors": errors,
        "version": TASK_WORKSPACE_EXECUTOR_VERSION,
        "execution_status": execution.status,
        "contract_summary": summarize_contract(contract),
        "environment_visible": execution.workspace_visible,
        "write_count": len(execution.writes),
    }


__all__ = [
    "TASK_WORKSPACE_EXECUTOR_VERSION",
    "WorkspaceWriteResult",
    "TaskWorkspaceExecution",
    "SkillsBenchTaskWorkspaceExecutor",
    "execute_task_workspace",
    "validate_task_workspace_executor_selftest",
]
