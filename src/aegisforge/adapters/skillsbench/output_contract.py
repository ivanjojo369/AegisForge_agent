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


OUTPUT_CONTRACT_VERSION = "skillsbench_output_contract_v0_3_strict_output_filter_2026_06_03"


ABS_PATH_RE = re.compile(
    r"(?P<quote>[`\"']?)"
    r"(?P<path>/(?:root|app|data|output|workspace|home/github/build|home/github|logs)"
    r"(?:/[A-Za-z0-9_.{}<>:+@%=\-]+){0,48}"
    r"(?:\.json|\.csv|\.txt|\.md|\.py|\.xlsx|\.xls|\.pptx|\.docx|\.pdf|\.dxf|\.zip|\.diff|\.lean|\.yaml|\.yml|\.sh|/))"
    r"(?P=quote)"
)

REL_PATH_RE = re.compile(
    r"(?P<quote>[`\"']?)"
    r"(?P<path>(?:output|workspace|results|patches|logs|data|artifacts)"
    r"(?:/[A-Za-z0-9_.{}<>:+@%=\-]+){0,48}"
    r"(?:\.json|\.csv|\.txt|\.md|\.py|\.xlsx|\.xls|\.pptx|\.docx|\.pdf|\.dxf|\.zip|\.diff|\.lean|\.yaml|\.yml|\.sh))"
    r"(?P=quote)"
)

OUTPUT_SUFFIX_RE = r"(?:json|csv|txt|md|py|xlsx|xls|pptx|docx|pdf|dxf|zip|diff|lean|yaml|yml|sh)"
BARE_OUTPUT_FILE_RE = re.compile(
    r"(?P<quote>[`\"']?)"
    r"(?P<path>(?:[A-Za-z0-9_.+@%=\-]+/){0,5}[A-Za-z0-9][A-Za-z0-9_.+@%=\-]{0,140}\." + OUTPUT_SUFFIX_RE + r")"
    r"(?P=quote)",
    re.IGNORECASE,
)
OUTPUT_SECTION_HEADER_RE = re.compile(
    r"^(?:[#*\-\d.) ]{0,12})"
    r"(?P<header>(?:required\s+)?(?:output\s+files?|outputs?|deliverables?|submission\s+files?|files\s+to\s+(?:create|submit|write)|expected\s+files?|final\s+files?|artifacts?))"
    r"\s*:?(?P<tail>.*)$",
    re.IGNORECASE,
)
OUTPUT_CONTEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:write|save|create|generate|produce|output|submit|fill)(?![A-Za-z0-9_./-])"
    r"[^\n.;]{0,220}?"
    r"(?P<file>(?:[A-Za-z0-9_.+@%=\-]+/){0,5}[A-Za-z0-9][A-Za-z0-9_.+@%=\-]{0,140}\." + OUTPUT_SUFFIX_RE + r")",
    re.IGNORECASE,
)
BARE_OUTPUT_BASE_KEYS: tuple[str, ...] = (
    "output_dir",
    "output_directory",
    "results_dir",
    "result_dir",
    "artifact_dir",
    "artifacts_dir",
    "workspace_dir",
    "SKILLSBENCH_OUTPUT_DIR",
    "AEGISFORGE_SKILLSBENCH_OUTPUT_DIR",
    "TASK_OUTPUT_DIR",
    "OUTPUT_DIR",
)
RELATIVE_OUTPUT_PREFIXES: tuple[str, ...] = (
    "output/",
    "outputs/",
    "workspace/",
    "results/",
    "patches/",
    "logs/",
    "data/",
    "artifacts/",
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
    "submit",
    "fill",
)

STRICT_OUTPUT_VERBS = OUTPUT_VERBS
STRICT_OUTPUT_VERB_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])(?:write|writes|written|save|saves|saved|create|creates|created|generate|generates|generated|produce|produces|produced|output|outputs|submit|submits|submitted|fill|fills|filled)(?![A-Za-z0-9_./-])",
    re.IGNORECASE,
)
GENERIC_OUTPUT_DIR_DENYLIST = {
    "/root",
    "/app",
    "/workspace",
    "/root/output",
    "/root/output_schema",
}
INPUT_PATH_PREFIX_DENYLIST = (
    "/root/data/",
    "/data/",
)
STRUCTURED_OUTPUT_SOURCE_MARKERS = (
    "required_output_section",
    "output_context",
    "inline_required_output_files",
    "metadata.outputs",
    "metadata.output",
    "metadata.expected_outputs",
    "metadata.artifacts",
    "metadata.files",
    "metadata.required_outputs",
    "metadata.output_files",
    "metadata.deliverables",
)
SOURCE_INPUT_CONTEXT_RE = re.compile(
    r"\b(?:input|inputs|source|provided|given|read|load|parse|analy[sz]e|from|dataset|data file|reference|template|starter|existing)\b",
    re.IGNORECASE,
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


def _default_output_base(metadata: Mapping[str, Any], text: str) -> str:
    """Choose a conservative base for bare required filenames.

    SkillsBench prompts often say only `answer.json` or list a section named
    `Required Output Files` without an absolute path.  In the official Harbor
    tasks those bare outputs are normally looked for from `/root`; code-centric
    `/app` tasks are the main exception.
    """

    for key in BARE_OUTPUT_BASE_KEYS:
        raw = metadata.get(key) or os.getenv(key)
        if raw:
            value = normalize_path(str(raw))
            if value.startswith("/"):
                return value.rstrip("/") or "/"

    blob = " ".join([_safe_text(metadata, limit=20000), text[:40000]]).lower()
    if "/app/workspace" in blob:
        return "/app/workspace"
    if re.search(r"(?:^|\s)/app(?:\s|/|$)", blob) and "/root" not in blob:
        return "/app"
    if "/output" in blob and "/root" not in blob:
        return "/output"
    return "/root"


def _strip_output_token(raw: str) -> str:
    token = str(raw or "").strip()
    token = re.sub(r"^[\s`\"'•*\-–—]+", "", token)
    token = re.sub(r"^[\d]+[.)]\s*", "", token)
    token = token.strip().strip("`\"'[](){}<>")
    token = token.rstrip(".,;:)]") if token else token
    return token.strip()


def _is_probably_output_filename(token: str) -> bool:
    token = _strip_output_token(token)
    if not token or token.startswith("/"):
        return False
    if "://" in token or "@" in token and "/" not in token:
        return False
    name = Path(token).name
    if not name or name in {".", ".."}:
        return False
    suffix = Path(name).suffix.lower()
    return suffix in PATH_KIND_BY_SUFFIX


def _materialize_output_path(raw_path: str, base_for_relative: str) -> str:
    raw = _strip_output_token(raw_path)
    if not raw:
        return ""
    if raw.startswith("/"):
        return normalize_path(raw)
    lowered = raw.lower()
    base = normalize_path(base_for_relative or "/root").rstrip("/") or "/"
    if any(lowered.startswith(prefix) for prefix in RELATIVE_OUTPUT_PREFIXES):
        if base in {"/root", "/app", "/workspace", "/output"}:
            return normalize_path(str(Path(base) / raw))
        # If the configured base is already an output-like subdir, avoid nesting
        # `/root/output/output/foo.csv`.
        if Path(base).name.lower() in {"output", "outputs", "results", "workspace", "data", "logs", "patches"}:
            first, _, rest = raw.partition("/")
            return normalize_path(str(Path(base) / (rest or first)))
    return normalize_path(str(Path(base) / raw))




def _is_generic_denied_directory(path: str) -> bool:
    normalized = normalize_path(path).rstrip("/") or "/"
    return normalized in GENERIC_OUTPUT_DIR_DENYLIST


def _is_denied_input_path(path: str) -> bool:
    normalized = normalize_path(path)
    return any(normalized.startswith(prefix) for prefix in INPUT_PATH_PREFIX_DENYLIST)


def _has_strict_output_signal(evidence: str, source: str) -> bool:
    blob = f"{source or ''}\n{evidence or ''}"
    if any(marker in blob for marker in STRUCTURED_OUTPUT_SOURCE_MARKERS):
        return True
    return bool(STRICT_OUTPUT_VERB_RE.search(blob))


def _is_source_like_without_output_signal(path: str, evidence: str, source: str) -> bool:
    if _has_strict_output_signal(evidence, source):
        return False
    suffix = Path(path).suffix.lower()
    if suffix in {".py", ".sh", ".yaml", ".yml", ".json", ".csv", ".txt", ".md", ".lean"}:
        return True
    return False


def _should_accept_output_path(path: str, evidence: str, source: str, *, kind: str = "") -> tuple[bool, str]:
    normalized = normalize_path(path)
    inferred_kind, _suffix, _mime = path_kind(normalized)
    kind = kind or inferred_kind

    if not normalized.startswith("/"):
        return False, "not_absolute"
    if _is_generic_denied_directory(normalized):
        return False, "generic_directory_denied"
    if kind == "directory" and normalize_path(normalized).rstrip("/") in GENERIC_OUTPUT_DIR_DENYLIST:
        return False, "generic_directory_denied"
    if _is_denied_input_path(normalized):
        return False, "input_path_denied"
    if not _has_strict_output_signal(evidence, source):
        return False, "missing_strict_output_verb_or_output_section"
    if _is_source_like_without_output_signal(normalized, evidence, source):
        return False, "source_like_without_output_signal"
    return True, "accepted"

def _make_output_requirement(
    path: str,
    *,
    source: str,
    evidence: str,
    full_text: str,
    required: bool = True,
    kind_override: str = "",
    mime_override: str = "",
    action: str = "",
    schema_fields: Sequence[str] | None = None,
    csv_columns: Sequence[str] | None = None,
    constraints: Sequence[str] | None = None,
) -> OutputRequirement | None:
    path = normalize_path(path)
    if not path.startswith("/"):
        return None
    kind, suffix, mime = path_kind(path)
    accepted, _reject_reason = _should_accept_output_path(path, evidence, source, kind=kind)
    if not accepted:
        return None
    if kind_override:
        kind = str(kind_override)
        mime = mime_override or MIME_BY_KIND.get(kind, mime)
    parent = str(Path(path).parent)
    filename = Path(path).name
    placeholders = tuple(PLACEHOLDER_RE.findall(path))
    around = evidence or _sentence_window(full_text, 0, min(len(full_text), 200))
    if schema_fields is None:
        schema_fields = _json_fields_from_text(full_text, around) if kind == "json" else tuple()
    if csv_columns is None:
        csv_columns = _csv_columns_from_text(full_text, around) if kind == "csv" else tuple()
    if constraints is None:
        constraints = _constraints_from_evidence(around, path)
    return OutputRequirement(
        path=path,
        kind=kind,
        mime_type=mime_override or mime,
        source=source,
        required=required,
        parent=parent,
        filename=filename,
        suffix=suffix,
        has_placeholder=bool(placeholders),
        placeholder_tokens=placeholders,
        is_directory=kind == "directory",
        action=action or _action_from_evidence(around),
        schema_fields=tuple(str(x) for x in (schema_fields or ())),
        csv_columns=tuple(str(x) for x in (csv_columns or ())),
        constraints=tuple(str(x) for x in (constraints or ())),
        evidence=around[:900],
    )


def _iter_output_sections(text: str, *, max_lines: int = 18) -> list[tuple[str, str]]:
    """Return output/deliverable sections likely to contain bare filenames."""

    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        match = OUTPUT_SECTION_HEADER_RE.match(line.strip())
        if not match:
            continue
        body_lines: list[str] = []
        tail = (match.group("tail") or "").strip()
        if tail:
            body_lines.append(tail)
        blanks = 0
        for next_line in lines[index + 1 : index + 1 + max_lines]:
            stripped = next_line.strip()
            if not stripped:
                blanks += 1
                if blanks >= 2 and body_lines:
                    break
                continue
            if body_lines and OUTPUT_SECTION_HEADER_RE.match(stripped):
                break
            if re.match(r"^#{1,6}\s+", stripped) and body_lines:
                break
            body_lines.append(stripped)
        if body_lines:
            sections.append((match.group("header"), "\n".join(body_lines)))
    return sections


def _requirements_from_bare_output_files(text: str, source: str, *, base_for_relative: str = "/root") -> list[OutputRequirement]:
    """Extract required filenames that are not written as absolute paths.

    This closes the gap observed in Quick Submit logs where many tasks exposed
    `Required Output Files: answer.json, control_log.csv, ...` and v0.1 emitted
    `no_requirements` because only `/root/...` or `output/...` paths were parsed.
    """

    requirements: list[OutputRequirement] = []
    seen: set[str] = set()

    candidates: list[tuple[str, str, str]] = []
    for header, body in _iter_output_sections(text):
        for match in BARE_OUTPUT_FILE_RE.finditer(body):
            if match.start() > 0 and body[match.start() - 1] == "/":
                continue
            raw = match.group("path")
            if raw.startswith("/") or not _is_probably_output_filename(raw):
                continue
            evidence = f"{header}: {_sentence_window(body, match.start(), match.end(), radius=180)}"
            candidates.append((raw, f"{source}.required_output_section", evidence))

    for match in OUTPUT_CONTEXT_RE.finditer(text):
        raw_start = match.start("file")
        if raw_start > 0 and text[raw_start - 1] == "/":
            continue
        raw = match.group("file")
        if raw.startswith("/") or not _is_probably_output_filename(raw):
            continue
        evidence = _sentence_window(text, match.start(), match.end(), radius=220)
        candidates.append((raw, f"{source}.output_context", evidence))

    # Markdown bullets or numbered lines such as `- answer.json` immediately
    # after an output-ish phrase are common; scanning sections above catches most
    # of them, but this fallback helps compact prompts where the header and list
    # are on one line.
    for match in re.finditer(r"(?:required output files?|outputs?|deliverables?|submission files?)\s*[:\-]\s*(?P<tail>[^\n]{0,800})", text, re.IGNORECASE):
        tail = match.group("tail")
        for file_match in BARE_OUTPUT_FILE_RE.finditer(tail):
            if file_match.start() > 0 and tail[file_match.start() - 1] == "/":
                continue
            raw = file_match.group("path")
            if raw.startswith("/") or not _is_probably_output_filename(raw):
                continue
            evidence = _sentence_window(text, match.start(), match.end(), radius=220)
            candidates.append((raw, f"{source}.inline_required_output_files", evidence))

    for raw, item_source, evidence in candidates:
        path = _materialize_output_path(raw, base_for_relative)
        if not path or path in seen:
            continue
        seen.add(path)
        requirement = _make_output_requirement(path, source=item_source, evidence=evidence, full_text=text)
        if requirement is not None:
            requirements.append(requirement)

    return requirements


def _requirements_from_paths(text: str, source: str, *, base_for_relative: str = "/root") -> list[OutputRequirement]:
    requirements: list[OutputRequirement] = []
    seen: set[str] = set()

    matches: list[tuple[str, re.Match[str]]] = []
    matches.extend(("absolute", m) for m in ABS_PATH_RE.finditer(text))
    matches.extend(("relative", m) for m in REL_PATH_RE.finditer(text))

    for path_type, match in matches:
        raw_path = match.group("path")
        if path_type == "relative":
            # Avoid matching the `output/foo.csv` substring inside an already
            # absolute path such as `/root/output/foo.csv`.
            if match.start() > 0 and text[match.start() - 1] == "/":
                continue
            raw_path = str(Path(base_for_relative) / raw_path)
        path = normalize_path(raw_path)
        if not path.startswith("/"):
            continue
        if path in seen:
            continue
        seen.add(path)

        evidence = _sentence_window(text, match.start(), match.end())
        kind, suffix, mime = path_kind(path)
        accepted, _reject_reason = _should_accept_output_path(path, evidence, source, kind=kind)
        if not accepted:
            continue
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


def _requirements_from_metadata(metadata: Mapping[str, Any], *, base_for_relative: str = "/root") -> list[OutputRequirement]:
    requirements: list[OutputRequirement] = []

    for key in ("outputs", "output", "expected_outputs", "artifacts", "files", "required_outputs", "output_files", "deliverables"):
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
                raw_path = item.get("path") or item.get("file") or item.get("filename") or item.get("file_name") or item.get("name")
                if not raw_path:
                    continue
                path = _materialize_output_path(str(raw_path), base_for_relative)
                if not path.startswith("/"):
                    continue
                requirement = _make_output_requirement(
                    path,
                    source=f"metadata.{key}[{index}]",
                    evidence=f"metadata key {key}",
                    full_text=_safe_text(metadata, limit=60000),
                    required=bool(item.get("required", True)),
                    kind_override=str(item.get("kind") or ""),
                    mime_override=str(item.get("mime_type") or item.get("mime") or ""),
                    action=str(item.get("action") or "write"),
                    schema_fields=tuple(str(x) for x in item.get("schema_fields", []) or []),
                    csv_columns=tuple(str(x) for x in item.get("csv_columns", []) or []),
                    constraints=tuple(str(x) for x in item.get("constraints", []) or []),
                )
                if requirement is not None:
                    requirements.append(requirement)
            elif isinstance(item, (str, bytes, bytearray)):
                raw_path = _safe_text(item, limit=500).strip()
                if not raw_path:
                    continue
                path = _materialize_output_path(raw_path, base_for_relative)
                if not path.startswith("/"):
                    continue
                requirement = _make_output_requirement(
                    path,
                    source=f"metadata.{key}[{index}]",
                    evidence=f"metadata key {key}",
                    full_text=_safe_text(metadata, limit=60000),
                )
                if requirement is not None:
                    requirements.append(requirement)

    for key in ("instruction", "description", "prompt", "task_text", "task", "system_prompt", "user_prompt"):
        value = metadata.get(key)
        if value:
            blob = _safe_text(value)
            requirements.extend(_requirements_from_paths(blob, f"metadata.{key}", base_for_relative=base_for_relative))
            requirements.extend(_requirements_from_bare_output_files(blob, f"metadata.{key}", base_for_relative=base_for_relative))

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
            " ".join(req.filename for req in requirements),
            " ".join(req.kind for req in requirements),
        ]
    ).lower()

    filenames = " ".join(req.filename.lower() for req in requirements)
    kinds = {req.kind for req in requirements}

    security_tokens = (
        "security", "vulnerability", "cve", "pcap", "firewall", "iptables",
        "allowlist", "denylist", "rule", "rules", "policy", "policies", "rbac",
        "iam", "permissions", "auth", "config", "configuration", "semgrep", "yara",
        "suricata", "snort", "detection", "audit", "sandbox", "secret", "secrets",
    )
    security_filenames = (
        "config", "policy", "policies", "rules", "rule", "allowlist", "denylist",
        "firewall", "permissions", "rbac", "iam", "detection", "audit", "secrets",
    )
    if any(token in blob for token in security_tokens) and (
        kinds & {"json", "yaml", "text", "csv", "shell"}
        or any(token in filenames for token in security_filenames)
    ):
        return "security_config"

    if "lean" in kinds or "lean4" in blob:
        return "lean_solution"
    if "excel" in kinds:
        return "office_xlsx"
    if "document" in kinds:
        return "office_docx"
    if kinds & {"python", "patch", "shell"} or any(name in filenames for name in ("solution.py", "main.py", "answer.py", "patch_0.diff")):
        return "code_solution"
    if "csv" in kinds:
        return "csv_output"
    if "json" in kinds:
        return "json_output"
    if "presentation" in kinds:
        return "office_pptx"
    if "pdf" in kinds:
        return "pdf_output"
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

    base_for_relative = _default_output_base(metadata, prompt)

    requirements = []
    requirements.extend(_requirements_from_paths(prompt, "prompt", base_for_relative=base_for_relative))
    requirements.extend(_requirements_from_bare_output_files(prompt, "prompt", base_for_relative=base_for_relative))
    requirements.extend(_requirements_from_metadata(metadata, base_for_relative=base_for_relative))
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
    Required Output Files:
    - controller_params.json
    - control_log.csv with columns `time`, `value`
    - plots/pareto_frontier.csv
    Submit artifacts: summary.md, final_answer.txt
    For build repair write `/home/github/build/failed/<repo>/<id>/patch_0.diff` and `/home/github/build/failed/failed_reasons.txt`.
    """
    contract = build_output_contract(
        {
            "task_id": "selftest",
            "category": "software-engineering",
            "expected_outputs": [
                {"name": "metadata_output.json", "schema_fields": ["ok", "score"]},
                "metadata_metrics.csv",
            ],
        },
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
        "/root/controller_params.json",
        "/root/control_log.csv",
        "/root/plots/pareto_frontier.csv",
        "/root/summary.md",
        "/root/final_answer.txt",
        "/root/metadata_output.json",
        "/root/metadata_metrics.csv",
    ):
        if expected not in paths:
            errors.append(f"missing path: {expected}")

    answer_req = contract.requirement_for_path("/root/answer.json")
    if not answer_req or "fake_citations" not in answer_req.schema_fields:
        errors.append("json schema field not detected")

    csv_req = contract.requirement_for_path("/root/output/results.csv")
    if not csv_req or not {"station_id", "flood_days"}.issubset(set(csv_req.csv_columns)):
        errors.append("csv columns not detected")

    bare_csv_req = contract.requirement_for_path("/root/control_log.csv")
    if not bare_csv_req or not {"time", "value"}.issubset(set(bare_csv_req.csv_columns)):
        errors.append("bare filename csv columns not detected")

    metadata_req = contract.requirement_for_path("/root/metadata_output.json")
    if not metadata_req or not {"ok", "score"}.issubset(set(metadata_req.schema_fields)):
        errors.append("relative metadata output schema fields not detected")


    denied_contract = build_output_contract(
        {"task_id": "denied"},
        "Use /root/data/input.csv as the input dataset. The directory /root/output contains expected schemas. Read /app/main.py but do not change it.",
    )
    if denied_contract.requirements:
        errors.append(f"denied/input paths should not be requirements: {[req.path for req in denied_contract.requirements]}")

    security_contract = build_output_contract(
        {"task_id": "security"},
        "Generate security_config.yaml with firewall allowlist rules and submit audit_policy.json.",
    )
    if security_contract.family != "security_config":
        errors.append(f"security config family not detected: {security_contract.family}")

    if contract.family != "code_solution":
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
