from __future__ import annotations

"""SkillsBench output contract extraction for AegisForge.

SkillsBench/Harbor tasks are usually graded by files written to exact paths in
the task sandbox, for example:

    /root/answer.json
    /root/output/report.md
    /app/workspace/solution.py
    /home/github/build/failed/<repo>/<id>/patch_0.diff

This module extracts those required outputs from task text/metadata and turns
them into a normalized contract that task_workspace_executor.py can satisfy.

Design constraints:
- no network access;
- no shell execution;
- no hidden answer lookup;
- no writes to the task filesystem;
- JSON-serializable diagnostics;
- tolerant of partial metadata from A2A, BenchFlow, or local tests.
"""

from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import csv
import io
import json
import os
import re


OUTPUT_CONTRACT_VERSION = "skillsbench_output_contract_v0_1_path_schema_extractor_2026_06_02"


ABS_PATH_RE = re.compile(
    r"(?P<quote>[`\"']?)"
    r"(?P<path>/(?:root|app|data|output|workspace|home/github/build|home/github|logs)"
    r"[A-Za-z0-9_./{}<>:+@%=\- ]{0,260}?"
    r"(?:\.json|\.csv|\.txt|\.md|\.py|\.xlsx|\.xls|\.pptx|\.docx|\.pdf|\.dxf|\.zip|\.diff|\.lean|\.yaml|\.yml|\.sh|/))"
    r"(?P=quote)"
)

REL_PATH_RE = re.compile(
    r"(?P<quote>[`\"']?)"
    r"(?P<path>(?:output|workspace|results|patches|logs|data)/"
    r"[A-Za-z0-9_./{}<>:+@%=\- ]{0,220}?"
    r"(?:\.json|\.csv|\.txt|\.md|\.py|\.xlsx|\.xls|\.pptx|\.docx|\.pdf|\.dxf|\.zip|\.diff|\.lean|\.yaml|\.yml|\.sh))"
    r"(?P=quote)"
)

PLACEHOLDER_RE = re.compile(r"<[^>/\s]+>|\{[^}/\s]+\}")
CODE_FENCE_RE = re.compile(r"```(?P<lang>[a-zA-Z0-9_+-]*)\s*\n(?P<body>.*?)```", re.DOTALL)
JSON_FIELD_RE = re.compile(r'"(?P<field>[A-Za-z_][A-Za-z0-9_\- ]{0,80})"\s*:')
CSV_COLUMNS_RE = re.compile(
    r"(?:columns?|header|fields?)\s*(?:called|are|is|with|:)?\s*(?P<cols>(?:`[^`]+`|\"[^\"]+\"|'[^']+'|[A-Za-z_][A-Za-z0-9_\- ]+)(?:\s*,\s*(?:`[^`]+`|\"[^\"]+\"|'[^']+'|[A-Za-z_][A-Za-z0-9_\- ]+)){1,12})",
    re.IGNORECASE,
)

OUTPUT_VERBS = (
    "write",
    "save",
    "create",
    "generate",
    "produce",
    "output",
    "store",
    "fill",
    "modify",
    "update",
    "apply",
    "implement",
)

PATH_KIND_BY_SUFFIX = {
    ".json": "json",
    ".csv": "csv",
    ".txt": "text",
    ".md": "markdown",
    ".py": "python",
    ".xlsx": "excel",
    ".xls": "excel",
    ".pptx": "presentation",
    ".docx": "document",
    ".pdf": "pdf",
    ".dxf": "cad",
    ".zip": "archive",
    ".diff": "patch",
    ".lean": "lean",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "shell",
}

MIME_BY_KIND = {
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
    "directory": "inode/directory",
    "unknown": "application/octet-stream",
}

ROOT_PRIORITY = (
    "/home/github/build/failed",
    "/home/github/build",
    "/app/workspace",
    "/root/workspace",
    "/root/output",
    "/app/output",
    "/root",
    "/app",
    "/output",
    "/workspace",
    "/logs/verifier",
)


@dataclass(frozen=True)
class OutputRequirement:
    """One required output file/path from the task instruction."""

    path: str
    kind: str
    mime_type: str
    source: str
    required: bool = True
    parent: str = ""
    filename: str = ""
    suffix: str = ""
    has_placeholder: bool = False
    placeholder_tokens: tuple[str, ...] = field(default_factory=tuple)
    is_directory: bool = False
    action: str = "write"
    schema_fields: tuple[str, ...] = field(default_factory=tuple)
    csv_columns: tuple[str, ...] = field(default_factory=tuple)
    constraints: tuple[str, ...] = field(default_factory=tuple)
    evidence: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["placeholder_tokens"] = list(self.placeholder_tokens)
        data["schema_fields"] = list(self.schema_fields)
        data["csv_columns"] = list(self.csv_columns)
        data["constraints"] = list(self.constraints)
        return data


@dataclass(frozen=True)
class OutputDirectoryRequirement:
    """Directory that should exist for one or more outputs."""

    path: str
    source: str
    required: bool = True
    expected_files: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["expected_files"] = list(self.expected_files)
        return data


@dataclass(frozen=True)
class SkillsBenchOutputContract:
    """Normalized output contract for one SkillsBench task."""

    version: str
    task_id: str
    category: str
    difficulty: str
    family: str
    roots: tuple[str, ...]
    output_dirs: tuple[OutputDirectoryRequirement, ...]
    requirements: tuple[OutputRequirement, ...]
    primary_outputs: tuple[str, ...]
    verifier_style: str
    workspace_mode: str
    needs_filesystem_write: bool
    needs_repo_patch: bool
    needs_code_execution: bool
    confidence: float
    warnings: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["roots"] = list(self.roots)
        data["output_dirs"] = [item.as_dict() for item in self.output_dirs]
        data["requirements"] = [item.as_dict() for item in self.requirements]
        data["primary_outputs"] = list(self.primary_outputs)
        data["warnings"] = list(self.warnings)
        data["evidence"] = list(self.evidence)
        return data

    def as_context(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_id": self.task_id,
            "category": self.category,
            "difficulty": self.difficulty,
            "family": self.family,
            "roots": list(self.roots),
            "primary_outputs": list(self.primary_outputs),
            "requirements": [req.as_dict() for req in self.requirements[:60]],
            "output_dirs": [directory.as_dict() for directory in self.output_dirs[:30]],
            "verifier_style": self.verifier_style,
            "workspace_mode": self.workspace_mode,
            "needs_filesystem_write": self.needs_filesystem_write,
            "needs_repo_patch": self.needs_repo_patch,
            "needs_code_execution": self.needs_code_execution,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
        }

    def requirement_for_path(self, path: str) -> OutputRequirement | None:
        normalized = normalize_path(path)
        for req in self.requirements:
            if req.path == normalized or req.path == path:
                return req
        return None

    def requirements_by_kind(self, kind: str) -> list[OutputRequirement]:
        return [req for req in self.requirements if req.kind == kind]


def _safe_text(value: Any, *, limit: int = 120000) -> str:
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


def normalize_path(path: str) -> str:
    path = str(path or "").strip().strip("`\"'")
    path = path.rstrip(".,);]")
    path = re.sub(r"\s+", " ", path)
    # Preserve placeholders; Path() may normalize angle-bracket text safely.
    try:
        return str(Path(path))
    except Exception:
        return path


def path_kind(path: str) -> tuple[str, str, str]:
    path = normalize_path(path)
    if path.endswith("/"):
        return "directory", "", MIME_BY_KIND["directory"]
    suffix = Path(path).suffix.lower()
    kind = PATH_KIND_BY_SUFFIX.get(suffix, "unknown")
    return kind, suffix, MIME_BY_KIND.get(kind, MIME_BY_KIND["unknown"])


def _dedupe_preserve(items: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def _sentence_window(text: str, start: int, end: int, *, radius: int = 260) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    window = text[left:right].strip()
    return re.sub(r"\s+", " ", window)


def _action_from_evidence(evidence: str) -> str:
    lowered = evidence.lower()
    for verb in OUTPUT_VERBS:
        if verb in lowered:
            return verb
    return "write"


def _constraints_from_evidence(evidence: str, path: str) -> tuple[str, ...]:
    lowered = evidence.lower()
    constraints: list[str] = []
    checks = {
        "exact_format": ("exact", "exactly", "only contain", "no extra", "single number", "output only"),
        "sort_required": ("sort", "sorted", "alphabetical"),
        "rounding_required": ("round", "rounded"),
        "preserve_formatting": ("unchanged", "keep formula", "preserve", "do not change"),
        "machine_checked": ("verifier", "test", "will compare", "independent verifier", "recompute"),
        "valid_json": ("valid json", ".json"),
        "valid_csv": ("csv", ".csv"),
        "apply_patch": ("git apply", "apply your changes", "patch_"),
        "create_directory": ("create /root/output", "create `/root/output", "mkdir", "create output"),
    }
    for name, tokens in checks.items():
        if any(token in lowered for token in tokens):
            constraints.append(name)
    kind, _, _ = path_kind(path)
    if kind in {"json", "csv", "python", "patch", "excel", "presentation", "lean"}:
        constraints.append(f"kind:{kind}")
    return _dedupe_preserve(constraints)


def _json_fields_from_text(text: str, around: str = "") -> tuple[str, ...]:
    blobs = [around, text]
    fields: list[str] = []
    for blob in blobs:
        for fence in CODE_FENCE_RE.finditer(blob):
            lang = (fence.group("lang") or "").lower()
            body = fence.group("body")
            if lang == "json" or "{" in body:
                fields.extend(match.group("field").strip() for match in JSON_FIELD_RE.finditer(body))
        fields.extend(match.group("field").strip() for match in JSON_FIELD_RE.finditer(blob[:4000]))
    return _dedupe_preserve(fields)[:80]


def _clean_column(raw: str) -> str:
    raw = raw.strip().strip("`\"' ")
    raw = re.sub(r"\s+", " ", raw)
    return raw


def _csv_columns_from_text(text: str, around: str = "") -> tuple[str, ...]:
    columns: list[str] = []

    for blob in (around, text):
        for fence in CODE_FENCE_RE.finditer(blob):
            body = fence.group("body").strip()
            first_line = body.splitlines()[0] if body.splitlines() else ""
            if "," in first_line and not first_line.lstrip().startswith("{"):
                try:
                    row = next(csv.reader(io.StringIO(first_line)))
                    if 1 < len(row) <= 40:
                        columns.extend(_clean_column(item) for item in row if _clean_column(item))
                except Exception:
                    pass

        for match in CSV_COLUMNS_RE.finditer(blob):
            raw_cols = match.group("cols")
            parts = re.split(r"\s*,\s*", raw_cols)
            cleaned = [_clean_column(part) for part in parts]
            if len(cleaned) >= 2:
                columns.extend(cleaned)

    return _dedupe_preserve(columns)[:60]


def _requirements_from_paths(text: str, source: str, *, base_for_relative: str = "/root") -> list[OutputRequirement]:
    requirements: list[OutputRequirement] = []
    seen: set[str] = set()

    matches: list[tuple[str, re.Match[str]]] = []
    matches.extend(("absolute", m) for m in ABS_PATH_RE.finditer(text))
    matches.extend(("relative", m) for m in REL_PATH_RE.finditer(text))

    for path_type, match in matches:
        raw_path = match.group("path")
        if path_type == "relative":
            raw_path = str(Path(base_for_relative) / raw_path)
        path = normalize_path(raw_path)
        if not path.startswith("/"):
            continue
        if path in seen:
            continue
        seen.add(path)

        evidence = _sentence_window(text, match.start(), match.end())
        kind, suffix, mime = path_kind(path)
        parent = str(Path(path).parent)
        filename = Path(path).name
        placeholders = tuple(PLACEHOLDER_RE.findall(path))
        has_placeholder = bool(placeholders)
        schema_fields = _json_fields_from_text(text, evidence) if kind == "json" else tuple()
        csv_columns = _csv_columns_from_text(text, evidence) if kind == "csv" else tuple()
        requirements.append(
            OutputRequirement(
                path=path,
                kind=kind,
                mime_type=mime,
                source=source,
                required=True,
                parent=parent,
                filename=filename,
                suffix=suffix,
                has_placeholder=has_placeholder,
                placeholder_tokens=placeholders,
                is_directory=kind == "directory",
                action=_action_from_evidence(evidence),
                schema_fields=schema_fields,
                csv_columns=csv_columns,
                constraints=_constraints_from_evidence(evidence, path),
                evidence=evidence[:900],
            )
        )

    return requirements


def _requirements_from_metadata(metadata: Mapping[str, Any]) -> list[OutputRequirement]:
    requirements: list[OutputRequirement] = []

    for key in ("outputs", "output", "expected_outputs", "artifacts", "files"):
        value = metadata.get(key)
        if value is None:
            continue
        items: Sequence[Any]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            items = value
        else:
            items = [value]
        for index, item in enumerate(items):
            if isinstance(item, Mapping):
                raw_path = item.get("path") or item.get("file") or item.get("filename") or item.get("name")
                if not raw_path:
                    continue
                path = normalize_path(str(raw_path))
                if not path.startswith("/"):
                    continue
                kind, suffix, mime = path_kind(path)
                parent = str(Path(path).parent)
                requirements.append(
                    OutputRequirement(
                        path=path,
                        kind=str(item.get("kind") or kind),
                        mime_type=str(item.get("mime_type") or item.get("mime") or mime),
                        source=f"metadata.{key}[{index}]",
                        required=bool(item.get("required", True)),
                        parent=parent,
                        filename=Path(path).name,
                        suffix=suffix,
                        has_placeholder=bool(PLACEHOLDER_RE.search(path)),
                        placeholder_tokens=tuple(PLACEHOLDER_RE.findall(path)),
                        is_directory=kind == "directory",
                        action=str(item.get("action") or "write"),
                        schema_fields=tuple(str(x) for x in item.get("schema_fields", []) or []),
                        csv_columns=tuple(str(x) for x in item.get("csv_columns", []) or []),
                        constraints=tuple(str(x) for x in item.get("constraints", []) or []),
                        evidence=f"metadata key {key}",
                    )
                )

    for key in ("instruction", "description", "prompt", "task_text"):
        value = metadata.get(key)
        if value:
            requirements.extend(_requirements_from_paths(_safe_text(value), f"metadata.{key}"))

    return requirements


def _merge_requirements(requirements: Iterable[OutputRequirement]) -> tuple[OutputRequirement, ...]:
    by_path: dict[str, OutputRequirement] = {}
    for req in requirements:
        existing = by_path.get(req.path)
        if existing is None:
            by_path[req.path] = req
            continue

        by_path[req.path] = OutputRequirement(
            path=req.path,
            kind=existing.kind if existing.kind != "unknown" else req.kind,
            mime_type=existing.mime_type if existing.mime_type != MIME_BY_KIND["unknown"] else req.mime_type,
            source=existing.source + "," + req.source,
            required=existing.required or req.required,
            parent=existing.parent or req.parent,
            filename=existing.filename or req.filename,
            suffix=existing.suffix or req.suffix,
            has_placeholder=existing.has_placeholder or req.has_placeholder,
            placeholder_tokens=_dedupe_preserve([*existing.placeholder_tokens, *req.placeholder_tokens]),
            is_directory=existing.is_directory or req.is_directory,
            action=existing.action if existing.action != "write" else req.action,
            schema_fields=_dedupe_preserve([*existing.schema_fields, *req.schema_fields]),
            csv_columns=_dedupe_preserve([*existing.csv_columns, *req.csv_columns]),
            constraints=_dedupe_preserve([*existing.constraints, *req.constraints]),
            evidence=(existing.evidence + " | " + req.evidence)[:1200],
        )

    def sort_key(req: OutputRequirement) -> tuple[int, str]:
        for i, root in enumerate(ROOT_PRIORITY):
            if req.path.startswith(root):
                return (i, req.path)
        return (len(ROOT_PRIORITY), req.path)

    return tuple(sorted(by_path.values(), key=sort_key))


def _output_dirs(requirements: Iterable[OutputRequirement]) -> tuple[OutputDirectoryRequirement, ...]:
    expected: dict[str, list[str]] = {}
    sources: dict[str, list[str]] = {}
    for req in requirements:
        directory = req.path if req.is_directory else req.parent
        if not directory:
            continue
        expected.setdefault(directory, [])
        sources.setdefault(directory, [])
        if not req.is_directory:
            expected[directory].append(req.filename)
        sources[directory].append(req.source)

    dirs: list[OutputDirectoryRequirement] = []
    for directory, files in expected.items():
        dirs.append(
            OutputDirectoryRequirement(
                path=directory,
                source=",".join(_dedupe_preserve(sources.get(directory, [])))[:300],
                required=True,
                expected_files=_dedupe_preserve(files),
            )
        )

    def sort_key(item: OutputDirectoryRequirement) -> tuple[int, str]:
        for i, root in enumerate(ROOT_PRIORITY):
            if item.path.startswith(root):
                return (i, item.path)
        return (len(ROOT_PRIORITY), item.path)

    return tuple(sorted(dirs, key=sort_key))


def _roots(requirements: Iterable[OutputRequirement]) -> tuple[str, ...]:
    roots: list[str] = []
    for req in requirements:
        path = req.path
        for root in ROOT_PRIORITY:
            if path.startswith(root):
                roots.append(root)
                break
        parts = Path(path).parts
        if len(parts) >= 2:
            roots.append("/".join(parts[:2]) or "/")
        if len(parts) >= 3:
            roots.append("/".join(parts[:3]))
    return _dedupe_preserve(roots)


def _infer_family(metadata: Mapping[str, Any], text: str, requirements: Sequence[OutputRequirement]) -> str:
    blob = " ".join(
        [
            _safe_text(metadata, limit=30000),
            text[:50000],
            " ".join(req.path for req in requirements),
            " ".join(req.kind for req in requirements),
        ]
    ).lower()

    if "/home/github/build" in blob or "patch_" in blob or "bugswarm" in blob or "failed_reasons.txt" in blob:
        return "bugswarm_build_repair"
    if any(req.kind == "patch" for req in requirements):
        return "software_patch"
    if any(req.kind == "lean" for req in requirements) or "lean4" in blob:
        return "formal_reasoning"
    if any(req.kind == "presentation" for req in requirements):
        return "presentation_editing"
    if any(req.kind == "excel" for req in requirements) or "excel" in blob:
        return "spreadsheet_office"
    if any(req.kind == "document" for req in requirements) or "docx" in blob:
        return "document_generation"
    if any(req.kind == "pdf" for req in requirements) or "form" in blob:
        return "pdf_document"
    if any(req.kind == "cad" for req in requirements) or "dxf" in blob:
        return "cad_geometry"
    if "pcap" in blob or "vulnerability" in blob or "cve" in blob:
        return "security_analysis"
    if "video" in blob or "ocr" in blob or "image" in blob or ".mp4" in blob:
        return "media_processing"
    if any(req.kind == "python" for req in requirements) or "solution.py" in blob:
        return "code_workspace"
    if any(req.kind == "csv" for req in requirements):
        return "data_csv"
    if any(req.kind == "json" for req in requirements):
        return "data_json"
    return "general_file_output"


def _infer_verifier_style(text: str, requirements: Sequence[OutputRequirement]) -> str:
    blob = " ".join([text[:50000], " ".join(req.evidence for req in requirements)]).lower()
    if "/logs/verifier/reward.txt" in blob or "pytest" in blob or "test_outputs.py" in blob:
        return "pytest_reward_file"
    if "independent verifier" in blob or "will recompute" in blob:
        return "independent_recompute"
    if "will compare" in blob or "line by line" in blob:
        return "golden_file_compare"
    if "f1" in blob or "score" in blob or "reward" in blob:
        return "metric_scoring"
    if requirements:
        return "file_existence_and_content"
    return "unknown"


def _infer_workspace_mode(requirements: Sequence[OutputRequirement]) -> str:
    paths = " ".join(req.path for req in requirements)
    if "/home/github/build" in paths:
        return "bugswarm_repo_workspace"
    if "/app/workspace" in paths:
        return "app_workspace"
    if "/root/workspace" in paths:
        return "root_workspace"
    if "/root/output" in paths:
        return "root_output"
    if "/app/output" in paths:
        return "app_output"
    if "/root" in paths:
        return "root_task"
    return "unknown"


def _primary_outputs(requirements: Sequence[OutputRequirement]) -> tuple[str, ...]:
    if not requirements:
        return tuple()

    preferred: list[OutputRequirement] = []
    for req in requirements:
        if req.is_directory:
            continue
        if any(token in req.filename.lower() for token in ("answer", "result", "report", "solution", "output", "schedule", "metrics", "patch", "filled", "processed")):
            preferred.append(req)

    selected = preferred or [req for req in requirements if not req.is_directory] or list(requirements)
    return tuple(req.path for req in selected[:24])


def _confidence(requirements: Sequence[OutputRequirement], text: str) -> float:
    score = 0.25
    if requirements:
        score += 0.35
    if any(req.evidence for req in requirements):
        score += 0.15
    if any(req.schema_fields or req.csv_columns for req in requirements):
        score += 0.15
    if any(token in text.lower() for token in ("write", "save", "create", "produce", "output")):
        score += 0.1
    return min(0.98, score)


def _warnings(requirements: Sequence[OutputRequirement], text: str) -> tuple[str, ...]:
    warnings: list[str] = []
    if not requirements:
        warnings.append("No explicit output file paths were detected.")
    if any(req.has_placeholder for req in requirements):
        warnings.append("Some output paths contain placeholders and require runtime resolution.")
    if len(requirements) > 30:
        warnings.append("Large number of output paths detected; downstream writer should prioritize primary_outputs.")
    if "do not change" in text.lower() or "unchanged" in text.lower():
        warnings.append("Task includes preservation constraints; writer must avoid destructive broad rewrites.")
    return tuple(warnings)


def build_output_contract(
    metadata: Mapping[str, Any] | None = None,
    text: Any = "",
    *,
    task_id: str = "",
    category: str = "",
    difficulty: str = "",
) -> SkillsBenchOutputContract:
    """Build a normalized filesystem-output contract from task metadata/text."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(text)

    requirements = []
    requirements.extend(_requirements_from_paths(prompt, "prompt"))
    requirements.extend(_requirements_from_metadata(metadata))
    merged = _merge_requirements(requirements)

    family = _infer_family(metadata, prompt, merged)
    verifier_style = _infer_verifier_style(prompt, merged)
    workspace_mode = _infer_workspace_mode(merged)
    needs_repo_patch = family in {"bugswarm_build_repair", "software_patch"} or any(req.kind == "patch" for req in merged)
    needs_code_execution = any(req.kind in {"python", "lean", "shell"} for req in merged) or needs_repo_patch
    needs_filesystem_write = bool(merged)

    evidence: list[str] = []
    for req in merged[:12]:
        if req.evidence:
            evidence.append(f"{req.path}: {req.evidence[:240]}")

    return SkillsBenchOutputContract(
        version=OUTPUT_CONTRACT_VERSION,
        task_id=str(metadata.get("task_id") or metadata.get("id") or task_id or ""),
        category=str(metadata.get("category") or metadata.get("task_category") or category or ""),
        difficulty=str(metadata.get("difficulty") or difficulty or ""),
        family=family,
        roots=_roots(merged),
        output_dirs=_output_dirs(merged),
        requirements=merged,
        primary_outputs=_primary_outputs(merged),
        verifier_style=verifier_style,
        workspace_mode=workspace_mode,
        needs_filesystem_write=needs_filesystem_write,
        needs_repo_patch=needs_repo_patch,
        needs_code_execution=needs_code_execution,
        confidence=_confidence(merged, prompt),
        warnings=_warnings(merged, prompt),
        evidence=tuple(evidence),
    )


def summarize_contract(contract: SkillsBenchOutputContract) -> dict[str, Any]:
    kinds = Counter(req.kind for req in contract.requirements)
    return {
        "version": contract.version,
        "task_id": contract.task_id,
        "family": contract.family,
        "workspace_mode": contract.workspace_mode,
        "verifier_style": contract.verifier_style,
        "requirement_count": len(contract.requirements),
        "directory_count": len(contract.output_dirs),
        "kind_counts": dict(sorted(kinds.items())),
        "primary_outputs": list(contract.primary_outputs),
        "needs_filesystem_write": contract.needs_filesystem_write,
        "needs_repo_patch": contract.needs_repo_patch,
        "needs_code_execution": contract.needs_code_execution,
        "confidence": contract.confidence,
        "warnings": list(contract.warnings),
    }


def contract_to_markdown(contract: SkillsBenchOutputContract) -> str:
    lines = [
        "# SkillsBench Output Contract",
        "",
        f"- version: `{contract.version}`",
        f"- task_id: `{contract.task_id or 'unknown'}`",
        f"- family: `{contract.family}`",
        f"- workspace_mode: `{contract.workspace_mode}`",
        f"- verifier_style: `{contract.verifier_style}`",
        f"- confidence: `{contract.confidence:.2f}`",
        "",
        "## Primary outputs",
        "",
    ]
    if contract.primary_outputs:
        lines.extend(f"- `{path}`" for path in contract.primary_outputs)
    else:
        lines.append("- No explicit output paths detected.")

    lines.extend(["", "## Requirements", ""])
    for req in contract.requirements:
        extras = []
        if req.schema_fields:
            extras.append("schema_fields=" + ",".join(req.schema_fields[:8]))
        if req.csv_columns:
            extras.append("csv_columns=" + ",".join(req.csv_columns[:8]))
        if req.constraints:
            extras.append("constraints=" + ",".join(req.constraints[:8]))
        extra_text = "; ".join(extras)
        lines.append(f"- `{req.path}` ({req.kind}, {req.action})" + (f" — {extra_text}" if extra_text else ""))

    if contract.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in contract.warnings)

    return "\n".join(lines) + "\n"


def validate_output_contract_selftest() -> dict[str, Any]:
    sample = """
    You need to write results to `/root/answer.json` in the following format:
    ```json
    {"fake_citations": ["Title"]}
    ```
    Also create /root/output/report.md and /root/output/results.csv with columns `station_id`, `flood_days`.
    For build repair write `/home/github/build/failed/<repo>/<id>/patch_0.diff` and `/home/github/build/failed/failed_reasons.txt`.
    """
    contract = build_output_contract(
        {"task_id": "selftest", "category": "software-engineering"},
        sample,
    )
    paths = set(contract.primary_outputs) | {req.path for req in contract.requirements}
    errors: list[str] = []
    for expected in (
        "/root/answer.json",
        "/root/output/report.md",
        "/root/output/results.csv",
        "/home/github/build/failed/<repo>/<id>/patch_0.diff",
        "/home/github/build/failed/failed_reasons.txt",
    ):
        if expected not in paths:
            errors.append(f"missing path: {expected}")

    answer_req = contract.requirement_for_path("/root/answer.json")
    if not answer_req or "fake_citations" not in answer_req.schema_fields:
        errors.append("json schema field not detected")

    csv_req = contract.requirement_for_path("/root/output/results.csv")
    if not csv_req or not {"station_id", "flood_days"}.issubset(set(csv_req.csv_columns)):
        errors.append("csv columns not detected")

    if contract.family != "bugswarm_build_repair":
        errors.append(f"unexpected family: {contract.family}")

    return {
        "ok": not errors,
        "errors": errors,
        "version": OUTPUT_CONTRACT_VERSION,
        "summary": summarize_contract(contract),
        "requirements": [req.as_dict() for req in contract.requirements],
    }


__all__ = [
    "OUTPUT_CONTRACT_VERSION",
    "OutputRequirement",
    "OutputDirectoryRequirement",
    "SkillsBenchOutputContract",
    "build_output_contract",
    "contract_to_markdown",
    "normalize_path",
    "path_kind",
    "summarize_contract",
    "validate_output_contract_selftest",
]
