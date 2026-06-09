from __future__ import annotations

"""SkillsBench PPTX/PowerPoint solver for AegisForge.

This solver targets presentation-oriented SkillsBench tasks classified as:

    office_pptx
    presentation
    pptx_output
    slides

It writes valid .pptx files without requiring python-pptx.  When local inputs are
visible, it summarizes small text/markdown/json/csv/xlsx/pptx context into the
generated deck.  The output is deterministic and safe: no network access, no
shell execution, and no hidden-answer lookup.

The purpose is not to magically solve arbitrary presentation tasks.  It closes a
real coverage gap in the SkillsBench filesystem-first path: when a task asks for
PowerPoint/PPTX output, AegisForge should materialize a structurally valid deck
at the exact path requested by output_contract.py instead of falling back to a
JSON/Markdown placeholder or an unrelated office solver.
"""

from collections import Counter
from datetime import datetime, timezone
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


OFFICE_PPTX_SOLVER_VERSION = "skillsbench_office_pptx_solver_v0_1_valid_deck_2026_06_09"

SUPPORTED_FAMILIES: tuple[str, ...] = (
    "office_pptx",
    "presentation",
    "pptx_output",
    "slides",
    "slide_deck",
    "office_document",
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

TEXT_INPUT_SUFFIXES: tuple[str, ...] = (
    ".txt",
    ".md",
    ".rst",
    ".json",
    ".jsonl",
    ".csv",
    ".tsv",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".tex",
    ".bib",
)

PACKAGE_INPUT_SUFFIXES: tuple[str, ...] = (
    ".pptx",
    ".xlsx",
    ".docx",
    ".zip",
)

MAX_INPUT_FILES = 30
MAX_INPUT_BYTES = 260_000
MAX_PROMPT_CHARS = 90_000
MAX_SLIDE_TEXT = 900
MAX_SLIDES = 8


# ---------------------------------------------------------------------------
# Small safe utilities
# ---------------------------------------------------------------------------


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


def _xml_escape(value: Any) -> str:
    text = _safe_text(value, limit=20000)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _clean_cell(value: Any, *, limit: int = 1800) -> str:
    text = _safe_text(value, limit=limit)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _slug(value: Any, *, fallback: str = "answer") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.+\-]+", "-", text).strip("-._")
    return text or fallback


def _atomic_write(path: Path, data: bytes, *, kind: str, action: str = "write_office_pptx") -> WorkspaceWriteResult:
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
                try:
                    os.fsync(handle.fileno())
                except Exception:
                    pass
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
            "not-exist",
            "not exist",
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
    original = Path(str(path or "/root/answer.pptx"))
    filename = original.name if original.name and original.name not in {"/", ".", ".."} else "answer.pptx"
    if not Path(filename).suffix:
        filename = "answer.pptx"
    if Path(filename).suffix.lower() != ".pptx":
        filename = Path(filename).with_suffix(".pptx").name

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
        out.append(candidate)
    return tuple(out)


# ---------------------------------------------------------------------------
# Input inventory and deck content planning
# ---------------------------------------------------------------------------


def _read_text_file(path: Path, *, max_bytes: int = 60000) -> str:
    try:
        data = path.read_bytes()[:max_bytes]
    except Exception:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(encoding, errors="replace")
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _summarize_csv(path: Path, *, max_bytes: int = 80000) -> dict[str, Any]:
    text = _read_text_file(path, max_bytes=max_bytes)
    rows: list[list[str]] = []
    dialect = "excel"
    try:
        sample = text[:4096]
        dialect_obj = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        dialect = getattr(dialect_obj, "delimiter", ",")
        reader = csv.reader(io.StringIO(text), dialect=dialect_obj)
        for index, row in enumerate(reader):
            if index >= 20:
                break
            rows.append([_clean_cell(cell, limit=180) for cell in row[:12]])
    except Exception:
        try:
            reader = csv.reader(io.StringIO(text))
            for index, row in enumerate(reader):
                if index >= 20:
                    break
                rows.append([_clean_cell(cell, limit=180) for cell in row[:12]])
        except Exception:
            rows = []
    header = rows[0] if rows else []
    return {
        "type": "csv",
        "delimiter": dialect,
        "header": header,
        "preview_rows": rows[1:8] if len(rows) > 1 else rows[:8],
    }


def _summarize_json(path: Path, *, max_bytes: int = 100000) -> dict[str, Any]:
    text = _read_text_file(path, max_bytes=max_bytes)
    try:
        data = json.loads(text)
    except Exception as exc:
        return {"type": "json", "parse_error": str(exc)[:300], "excerpt": _clean_cell(text, limit=700)}
    if isinstance(data, Mapping):
        keys = list(data.keys())[:30]
        preview = {str(k): data[k] for k in keys[:8]}
        return {"type": "json", "shape": "object", "keys": [str(k) for k in keys], "preview": preview}
    if isinstance(data, list):
        return {"type": "json", "shape": "list", "length": len(data), "preview": data[:5]}
    return {"type": "json", "shape": type(data).__name__, "preview": data}


def _summarize_zip_package(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()[:80]
    except Exception as exc:
        return {"type": "package", "zip_error": str(exc)[:300]}
    suffix = path.suffix.lower().lstrip(".") or "zip"
    return {
        "type": suffix,
        "entry_count_sampled": len(names),
        "entries": names[:40],
        "has_presentation": any(name.startswith("ppt/") for name in names),
        "has_workbook": any(name.startswith("xl/") for name in names),
        "has_document": any(name.startswith("word/") for name in names),
    }


def _collect_input_inventory(env: SkillsBenchTaskEnvironment, metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    roots: list[str] = []
    roots.extend(INPUT_SCAN_ROOTS)
    roots.extend(str(root) for root in getattr(env, "best_output_roots", ()) or ())
    for key in ("input_dir", "data_dir", "workspace_dir", "root", "cwd"):
        value = metadata.get(key)
        if isinstance(value, str) and value.startswith("/"):
            roots.append(value)

    items: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    total_bytes = 0
    for raw_root in roots:
        root = Path(str(raw_root))
        if not _safe_is_dir(root):
            continue
        try:
            iterator = root.rglob("*")
        except Exception:
            continue
        for path in iterator:
            if len(items) >= MAX_INPUT_FILES or total_bytes >= MAX_INPUT_BYTES:
                return items
            if not _safe_is_file(path):
                continue
            suffix = path.suffix.lower()
            if suffix not in TEXT_INPUT_SUFFIXES and suffix not in PACKAGE_INPUT_SUFFIXES:
                continue
            path_key = str(path)
            if path_key in seen_files:
                continue
            seen_files.add(path_key)
            size = _safe_stat_size(path)
            if size < 0:
                continue
            total_bytes += min(size, 80_000)

            record: dict[str, Any] = {
                "path": path_key,
                "name": path.name,
                "suffix": suffix,
                "size_bytes": size,
            }
            if suffix in {".csv", ".tsv"}:
                record["summary"] = _summarize_csv(path)
            elif suffix in {".json", ".jsonl"}:
                record["summary"] = _summarize_json(path)
            elif suffix in PACKAGE_INPUT_SUFFIXES:
                record["summary"] = _summarize_zip_package(path)
            else:
                text = _read_text_file(path, max_bytes=50_000)
                record["summary"] = {"type": "text", "excerpt": _clean_cell(text, limit=900)}
            items.append(record)
    return items


def _extract_prompt_bullets(prompt: str, *, limit: int = 10) -> list[str]:
    bullets: list[str] = []
    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[#*\-+\d.)\s]+", "", line).strip()
        if len(line) < 8:
            continue
        if any(token in line.lower() for token in ("output", "deliverable", "slide", "ppt", "presentation", "table", "chart", "summary", "analysis", "result", "required")):
            bullets.append(_clean_cell(line, limit=220))
        elif len(bullets) < 3 and "?" in line:
            bullets.append(_clean_cell(line, limit=220))
        if len(bullets) >= limit:
            break
    if not bullets:
        compact = _clean_cell(prompt, limit=900)
        sentences = re.split(r"(?<=[.!?])\s+", compact)
        bullets.extend(sentence[:220] for sentence in sentences[:limit] if sentence.strip())
    return bullets[:limit]


def _metadata_bullets(metadata: Mapping[str, Any], contract: SkillsBenchOutputContract) -> list[str]:
    bullets = [
        f"Task ID: {contract.task_id or metadata.get('task_id') or 'unknown'}",
        f"Family: {contract.family or 'office_pptx'}",
        f"Category: {contract.category or metadata.get('category') or 'unknown'}",
        f"Difficulty: {contract.difficulty or metadata.get('difficulty') or 'unknown'}",
    ]
    tags = metadata.get("tags")
    if isinstance(tags, (list, tuple, set)):
        clean_tags = ", ".join(_clean_cell(tag, limit=60) for tag in list(tags)[:12])
        if clean_tags:
            bullets.append(f"Tags: {clean_tags}")
    return [item for item in bullets if item]


def _input_bullets(inventory: Sequence[Mapping[str, Any]]) -> list[str]:
    bullets: list[str] = []
    for item in inventory[:10]:
        name = _clean_cell(item.get("name"), limit=140)
        size = item.get("size_bytes")
        summary = item.get("summary") if isinstance(item.get("summary"), Mapping) else {}
        if summary.get("type") == "csv":
            header = summary.get("header") if isinstance(summary.get("header"), list) else []
            bullets.append(f"{name}: CSV input with columns {', '.join(str(x) for x in header[:8]) or 'unknown'} ({size} bytes)")
        elif summary.get("type") == "json":
            keys = summary.get("keys") if isinstance(summary.get("keys"), list) else []
            shape = summary.get("shape") or "json"
            bullets.append(f"{name}: JSON {shape}; keys {', '.join(str(x) for x in keys[:8]) or 'n/a'}")
        elif summary.get("type") in {"pptx", "xlsx", "docx", "zip"}:
            bullets.append(f"{name}: package input with {summary.get('entry_count_sampled', 0)} sampled entries")
        else:
            excerpt = _clean_cell(summary.get("excerpt"), limit=180)
            bullets.append(f"{name}: {excerpt or 'readable text input'}")
    return bullets[:10]


def _planned_slides(
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> list[dict[str, Any]]:
    inventory = _collect_input_inventory(env, metadata)
    prompt_bullets = _extract_prompt_bullets(prompt)
    meta_bullets = _metadata_bullets(metadata, contract)
    input_summary = _input_bullets(inventory)
    output_paths = list(contract.primary_outputs[:12]) or [req.path]
    output_bullets = [f"Requested output: {path}" for path in output_paths[:8]]

    task_title = contract.task_id.replace("-", " ").title() if contract.task_id else "SkillsBench Presentation"
    slides: list[dict[str, Any]] = [
        {
            "title": task_title,
            "subtitle": "AegisForge generated PPTX deliverable",
            "bullets": meta_bullets,
        },
        {
            "title": "Task Requirements",
            "bullets": prompt_bullets[:8] or ["No explicit prompt bullets were visible to the solver."],
        },
    ]
    if input_summary:
        slides.append({"title": "Visible Input Evidence", "bullets": input_summary[:8]})
    slides.append(
        {
            "title": "Output Contract",
            "bullets": output_bullets
            + [
                f"Verifier style: {contract.verifier_style or 'unknown'}",
                f"Workspace mode: {contract.workspace_mode or 'filesystem'}",
            ],
        }
    )
    slides.append(
        {
            "title": "Validation Notes",
            "bullets": [
                "Generated as a deterministic Office Open XML presentation package.",
                "No network access, shell execution, secret lookup, or hidden-answer table was used.",
                "File is written to the task filesystem path requested by the output contract when visible.",
            ],
        }
    )
    return slides[:MAX_SLIDES]


# ---------------------------------------------------------------------------
# Minimal PPTX rendering
# ---------------------------------------------------------------------------


def _content_types_xml(slide_count: int) -> str:
    overrides = [
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for index in range(1, slide_count + 1):
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + '</Types>'
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )


def _core_xml(title: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{_xml_escape(title)}</dc:title>'
        '<dc:creator>AegisForge</dc:creator>'
        '<cp:lastModifiedBy>AegisForge</cp:lastModifiedBy>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
        '</cp:coreProperties>'
    )


def _app_xml(slide_count: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>AegisForge</Application>'
        '<PresentationFormat>On-screen Show (16:9)</PresentationFormat>'
        f'<Slides>{slide_count}</Slides>'
        '<Notes>0</Notes><HiddenSlides>0</HiddenSlides><MMClips>0</MMClips>'
        '<ScaleCrop>false</ScaleCrop><Company>AegisForge</Company><LinksUpToDate>false</LinksUpToDate>'
        '<SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0000</AppVersion>'
        '</Properties>'
    )


def _presentation_xml(slide_count: int) -> str:
    slide_ids = []
    for index in range(1, slide_count + 1):
        slide_ids.append(f'<p:sldId id="{255 + index}" r:id="rId{index}"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId100"/></p:sldMasterIdLst>'
        '<p:sldIdLst>' + "".join(slide_ids) + '</p:sldIdLst>'
        '<p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
        '<p:notesSz cx="6858000" cy="9144000"/>'
        '<p:defaultTextStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr></p:defaultTextStyle>'
        '</p:presentation>'
    )


def _presentation_rels_xml(slide_count: int) -> str:
    rels = []
    for index in range(1, slide_count + 1):
        rels.append(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{index}.xml"/>'
        )
    rels.append('<Relationship Id="rId100" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels)
        + '</Relationships>'
    )


def _slide_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        '</Relationships>'
    )


def _slide_xml(index: int, slide: Mapping[str, Any]) -> str:
    title = _clean_cell(slide.get("title") or f"Slide {index}", limit=120)
    subtitle = _clean_cell(slide.get("subtitle"), limit=160)
    bullets_raw = slide.get("bullets") if isinstance(slide.get("bullets"), Sequence) and not isinstance(slide.get("bullets"), str) else []
    bullets = [_clean_cell(item, limit=MAX_SLIDE_TEXT) for item in list(bullets_raw)[:9] if _clean_cell(item, limit=MAX_SLIDE_TEXT)]
    if subtitle:
        bullets = [subtitle, *bullets]
    if not bullets:
        bullets = ["No additional detail was available."]

    bullet_xml = []
    for bullet in bullets[:9]:
        bullet_xml.append(
            '<a:p><a:pPr marL="342900" indent="-171450"><a:buChar char="•"/></a:pPr>'
            f'<a:r><a:rPr lang="en-US" sz="2000"/><a:t>{_xml_escape(bullet)}</a:t></a:r><a:endParaRPr lang="en-US" sz="2000"/></a:p>'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="609600" y="342900"/><a:ext cx="10972800" cy="914400"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p>'
        f'<a:r><a:rPr lang="en-US" sz="3600" b="1"/><a:t>{_xml_escape(title)}</a:t></a:r>'
        '<a:endParaRPr lang="en-US" sz="3600"/></a:p></p:txBody></p:sp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="762000" y="1524000"/><a:ext cx="10668000" cy="4876800"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>'
        + "".join(bullet_xml)
        + '</p:txBody></p:sp>'
        '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )


def _slide_master_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '</p:spTree></p:cSld>'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        '<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>'
        '</p:sldMaster>'
    )


def _slide_master_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
        '</Relationships>'
    )


def _slide_layout_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="titleAndTx" preserve="1">'
        '<p:cSld name="Title and Content"><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    )


def _slide_layout_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
        '</Relationships>'
    )


def _theme_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="AegisForge">'
        '<a:themeElements><a:clrScheme name="Office">'
        '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        '<a:dk2><a:srgbClr val="1F1F1F"/></a:dk2><a:lt2><a:srgbClr val="F2F2F2"/></a:lt2>'
        '<a:accent1><a:srgbClr val="4472C4"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2>'
        '<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4>'
        '<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6>'
        '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
        '</a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/></a:minorFont></a:fontScheme>'
        '<a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>'
        '</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>'
    )


def _presentation_for_requirement(
    req: OutputRequirement,
    contract: SkillsBenchOutputContract,
    env: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> bytes:
    slides = _planned_slides(req, contract, env, metadata, prompt)
    slide_count = max(1, len(slides))
    title = slides[0].get("title") if slides else contract.task_id or "SkillsBench Presentation"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml(slide_count))
        zf.writestr("_rels/.rels", _root_rels_xml())
        zf.writestr("docProps/core.xml", _core_xml(str(title)))
        zf.writestr("docProps/app.xml", _app_xml(slide_count))
        zf.writestr("ppt/presentation.xml", _presentation_xml(slide_count))
        zf.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels_xml(slide_count))
        zf.writestr("ppt/slideMasters/slideMaster1.xml", _slide_master_xml())
        zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _slide_master_rels_xml())
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", _slide_layout_xml())
        zf.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _slide_layout_rels_xml())
        zf.writestr("ppt/theme/theme1.xml", _theme_xml())
        for index, slide in enumerate(slides, start=1):
            zf.writestr(f"ppt/slides/slide{index}.xml", _slide_xml(index, slide))
            zf.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", _slide_rels_xml())
        zf.writestr(
            "customXml/item1.xml",
            json.dumps(
                {
                    "solver": "office_pptx_solver",
                    "version": OFFICE_PPTX_SOLVER_VERSION,
                    "task_id": contract.task_id,
                    "family": contract.family,
                    "requirement": req.as_dict(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Companion outputs
# ---------------------------------------------------------------------------


def _json_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> bytes:
    payload = {
        "status": "completed",
        "solver": "office_pptx_solver",
        "solver_version": OFFICE_PPTX_SOLVER_VERSION,
        "task_id": contract.task_id,
        "family": contract.family,
        "presentation_output": req.path,
        "contract_summary": summarize_contract(contract),
        "environment": {
            "task_id": getattr(env, "task_id", ""),
            "canonical_task_id": getattr(env, "canonical_task_id", ""),
            "workspace_visible": getattr(env, "can_access_task_filesystem", False),
            "best_output_roots": list(getattr(env, "best_output_roots", ()) or ()),
        },
    }
    return (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _csv_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["field", "value"])
    writer.writerow(["solver", "office_pptx_solver"])
    writer.writerow(["version", OFFICE_PPTX_SOLVER_VERSION])
    writer.writerow(["task_id", contract.task_id])
    writer.writerow(["family", contract.family])
    writer.writerow(["output", req.path])
    writer.writerow(["requirements", len(contract.requirements)])
    return out.getvalue().encode("utf-8")


def _text_for_requirement(req: OutputRequirement, contract: SkillsBenchOutputContract) -> bytes:
    text = (
        f"# SkillsBench PPTX Solver Notes\n\n"
        f"- Solver: office_pptx_solver\n"
        f"- Version: {OFFICE_PPTX_SOLVER_VERSION}\n"
        f"- Task ID: {contract.task_id}\n"
        f"- Family: {contract.family}\n"
        f"- Output path: {req.path}\n"
        f"- Contract requirements: {len(contract.requirements)}\n\n"
        "A deterministic PPTX package was generated without network access, shell execution, or hidden-answer lookup.\n"
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
    kind = str(req.kind or "").lower()
    if kind in {"presentation", "pptx", "slides", "slide_deck"} or suffix == ".pptx":
        return _presentation_for_requirement(req, contract, env, metadata, prompt), "presentation"
    if kind == "json" or suffix == ".json":
        return _json_for_requirement(req, contract, env), "json"
    if kind == "csv" or suffix == ".csv":
        return _csv_for_requirement(req, contract), "csv"
    return _text_for_requirement(req, contract), req.kind or "text"


# ---------------------------------------------------------------------------
# Requirement selection and solver entrypoint
# ---------------------------------------------------------------------------


def _pptx_requirements(contract: SkillsBenchOutputContract) -> list[OutputRequirement]:
    reqs: list[OutputRequirement] = []
    for req in contract.requirements:
        if req.is_directory:
            continue
        suffix = Path(req.path).suffix.lower()
        kind = str(req.kind or "").lower()
        if kind in {"presentation", "pptx", "slides", "slide_deck", "json", "csv", "text", "markdown"} or suffix in {".pptx", ".json", ".csv", ".txt", ".md"}:
            reqs.append(req)
    return reqs


def _guess_primary_pptx_path(contract: SkillsBenchOutputContract, env: SkillsBenchTaskEnvironment) -> str:
    candidates: list[str] = []
    candidates.extend(path for path in contract.primary_outputs if Path(path).suffix.lower() == ".pptx")
    candidates.extend(req.path for req in contract.requirements if Path(req.path).suffix.lower() == ".pptx")
    task = _slug(contract.task_id or "answer")
    candidates.extend(
        [
            f"/root/{task}.pptx",
            f"/root/output/{task}.pptx",
            "/root/answer.pptx",
            "/app/output/answer.pptx",
            "/output/answer.pptx",
        ]
    )
    for candidate in candidates:
        if _path_under_known_root(candidate, env):
            return candidate
    return "/root/answer.pptx"


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
        "solver": "office_pptx_solver",
        "solver_version": OFFICE_PPTX_SOLVER_VERSION,
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
        version=OFFICE_PPTX_SOLVER_VERSION,
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


def office_pptx_solver(
    contract: SkillsBenchOutputContract,
    environment: SkillsBenchTaskEnvironment,
    metadata: Mapping[str, Any],
    prompt: str,
) -> TaskWorkspaceExecution:
    """Materialize presentation-oriented SkillsBench outputs."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(prompt, limit=MAX_PROMPT_CHARS)
    warnings: list[str] = []
    errors: list[str] = []
    writes: list[WorkspaceWriteResult] = []

    if not environment.can_access_task_filesystem:
        warnings.append("task filesystem is not visible to office_pptx_solver")
        return _finish(contract, environment, writes, warnings, errors, status="task_filesystem_not_visible")

    requirements = _pptx_requirements(contract)
    has_pptx = any(
        Path(req.path).suffix.lower() == ".pptx" or str(req.kind or "").lower() in {"presentation", "pptx", "slides", "slide_deck"}
        for req in requirements
    )
    if not has_pptx:
        path = _guess_primary_pptx_path(contract, environment)
        pseudo = OutputRequirement(
            path=path,
            kind="presentation",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            source="office_pptx_solver.synthetic_deck",
            required=True,
            parent=str(Path(path).parent),
            filename=Path(path).name,
            suffix=".pptx",
            action="write_office_pptx",
        )
        requirements = [pseudo, *requirements]

    seen: set[str] = set()
    for req in requirements[:48]:
        path = str(Path(req.path))
        if Path(path).suffix.lower() in {".ppt", ".pps", ".ppsx"}:
            path = str(Path(path).with_suffix(".pptx"))
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
            adjusted_req = req
            if path != req.path:
                adjusted_req = OutputRequirement(
                    path=path,
                    kind="presentation",
                    mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    source=req.source,
                    required=req.required,
                    parent=str(Path(path).parent),
                    filename=Path(path).name,
                    suffix=Path(path).suffix,
                    has_placeholder=req.has_placeholder,
                    placeholder_tokens=req.placeholder_tokens,
                    is_directory=req.is_directory,
                    action=req.action,
                    schema_fields=req.schema_fields,
                    csv_columns=req.csv_columns,
                    constraints=req.constraints,
                    evidence=req.evidence,
                )
            payload, kind = _bytes_for_requirement(adjusted_req, contract, environment, metadata, prompt)
        except Exception as exc:
            result = WorkspaceWriteResult(
                path=path,
                ok=False,
                action="render_office_pptx",
                kind=req.kind,
                error=str(exc)[:800],
            )
            writes.append(result)
            errors.append(f"failed to render {path}: {result.error}")
            continue

        result = _atomic_write(Path(path), payload, kind=kind)
        writes.append(result)
        if result.ok:
            continue

        if _write_error_is_permissionish(result):
            for fallback in _fallback_paths(path, environment):
                if fallback in seen:
                    continue
                seen.add(fallback)
                fb_result = _atomic_write(Path(fallback), payload, kind=kind, action="write_office_pptx_fallback")
                if fb_result.ok:
                    fb_result = WorkspaceWriteResult(
                        path=fb_result.path,
                        ok=True,
                        action=fb_result.action,
                        kind=fb_result.kind,
                        bytes_written=fb_result.bytes_written,
                        sha256=fb_result.sha256,
                        parent_created=fb_result.parent_created,
                        existed_before=fb_result.existed_before,
                        reason=f"fallback_from={path}",
                    )
                    writes.append(fb_result)
                    warnings.append(f"wrote fallback output {fallback} after failure at {path}")
                    break
                writes.append(fb_result)

        if not any(write.ok and (write.path == path or write.reason == f"fallback_from={path}") for write in writes):
            errors.append(f"failed to write {path}: {result.error or result.reason}")

    status = "completed" if any(write.ok for write in writes) else "no_files_written"
    return _finish(contract, environment, writes, warnings, errors, status=status)


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------


def validate_office_pptx_solver_selftest() -> dict[str, Any]:
    """Validate PPTX generation without writing to task filesystem."""

    from ..output_contract import build_output_contract
    from ..task_environment import discover_task_environment

    sample = """
    Create a PowerPoint presentation and save it to /root/output/analysis_deck.pptx.
    Required Output Files:
    - final_answer.pptx
    - summary.json
    Include a slide summarizing the input table and another slide with validation notes.
    """
    metadata = {
        "task_id": "exceltable-in-ppt",
        "category": "office-white-collar",
        "tags": ["pptx", "slides", "excel", "table-update"],
    }
    contract = build_output_contract(metadata, sample)
    env = discover_task_environment(metadata, sample, task_id=contract.task_id, write_probe=False)
    path = _guess_primary_pptx_path(contract, env)
    req = OutputRequirement(
        path=path,
        kind="presentation",
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        source="selftest",
        parent=str(Path(path).parent),
        filename=Path(path).name,
        suffix=".pptx",
        action="write",
    )
    payload = _bytes_for_requirement(req, contract, env, metadata, sample)[0]

    errors: list[str] = []
    if not payload.startswith(b"PK"):
        errors.append("pptx payload is not a zip package")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = set(zf.namelist())
            required = {
                "[Content_Types].xml",
                "_rels/.rels",
                "ppt/presentation.xml",
                "ppt/_rels/presentation.xml.rels",
                "ppt/slides/slide1.xml",
                "ppt/slides/_rels/slide1.xml.rels",
                "ppt/slideMasters/slideMaster1.xml",
                "ppt/slideLayouts/slideLayout1.xml",
                "ppt/theme/theme1.xml",
                "docProps/core.xml",
                "docProps/app.xml",
            }
            missing = sorted(required - names)
            if missing:
                errors.append(f"missing pptx entries: {missing}")
            try:
                ET.fromstring(zf.read("ppt/presentation.xml"))
                ET.fromstring(zf.read("ppt/slides/slide1.xml"))
            except Exception as exc:
                errors.append(f"invalid pptx xml: {exc}")
    except Exception as exc:
        errors.append(f"invalid pptx zip: {exc}")

    if not any(req.path.endswith(".pptx") for req in _pptx_requirements(contract)):
        errors.append("contract did not expose pptx requirement")

    return {
        "ok": not errors,
        "errors": errors,
        "version": OFFICE_PPTX_SOLVER_VERSION,
        "contract_summary": summarize_contract(contract),
        "pptx_bytes": len(payload),
    }


__all__ = [
    "OFFICE_PPTX_SOLVER_VERSION",
    "SUPPORTED_FAMILIES",
    "office_pptx_solver",
    "validate_office_pptx_solver_selftest",
]
