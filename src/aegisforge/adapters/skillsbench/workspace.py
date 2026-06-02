from __future__ import annotations

"""Safe workspace utilities for the AegisForge SkillsBench adapter.

This module is intentionally low-level.  It does not call LLMs, does not open
network connections, and does not execute shell commands.  Its job is to find a
bounded task workspace, inspect input files safely, and write deterministic
outputs/manifests for the higher-level SkillsBench harness.

The expected call chain is:

    contract.py       -> normalize task request metadata
    workspace.py      -> discover/list/read/write sandbox files safely
    harness.py        -> choose a task-family strategy
    result_emitter.py -> persist final outputs and result manifests
    adapter.py        -> bridge the whole flow back to agent.py / executor.py
"""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import mimetypes
import os
import re


WORKSPACE_VERSION = "skillsbench_workspace_v0_1_safe_discovery_2026_06_02"

DEFAULT_MAX_FILES = int(os.getenv("AEGISFORGE_SKILLSBENCH_WORKSPACE_MAX_FILES", "240"))
DEFAULT_MAX_FILE_BYTES = int(os.getenv("AEGISFORGE_SKILLSBENCH_WORKSPACE_MAX_FILE_BYTES", "262144"))
DEFAULT_MAX_TEXT_CHARS = int(os.getenv("AEGISFORGE_SKILLSBENCH_WORKSPACE_MAX_TEXT_CHARS", "120000"))
DEFAULT_MAX_TOTAL_BYTES = int(os.getenv("AEGISFORGE_SKILLSBENCH_WORKSPACE_MAX_TOTAL_BYTES", "4000000"))

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".rst",
    ".json",
    ".json5",
    ".toml",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".scala",
    ".go",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".html",
    ".css",
    ".xml",
    ".svg",
    ".sql",
    ".lean",
    ".patch",
    ".diff",
    ".bib",
    ".tex",
    ".log",
    ".ini",
    ".cfg",
    ".conf",
}

BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".xls",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".7z",
    ".mp3",
    ".wav",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".stl",
    ".dxf",
    ".obj",
    ".pcap",
    ".parquet",
    ".sqlite",
    ".db",
}

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    ".next",
    "dist",
    "build",
    ".tox",
    ".nox",
    ".cache",
    ".cargo",
    ".gradle",
}

WORKSPACE_ENV_KEYS = (
    "SKILLSBENCH_WORKSPACE",
    "SKILLSBENCH_TASK_DIR",
    "SKILLSBENCH_TASK_ROOT",
    "SKILLSBENCH_REPO_DIR",
    "WORKSPACE",
    "WORKSPACE_DIR",
    "TASK_WORKSPACE",
    "TASK_DIR",
    "REPO_DIR",
    "PROJECT_DIR",
)

OUTPUT_ENV_KEYS = (
    "SKILLSBENCH_OUTPUT_DIR",
    "AEGISFORGE_SKILLSBENCH_OUTPUT_DIR",
    "AMBER_OUTPUT_DIR",
    "ARTIFACTS_DIR",
    "OUTPUT_DIR",
    "RESULTS_DIR",
)


@dataclass(frozen=True)
class WorkspaceFile:
    """A bounded, serializable description of a file in the task workspace."""

    path: str
    relative_path: str
    name: str
    suffix: str
    mime_type: str
    size_bytes: int
    sha256: str
    is_text: bool
    is_binary: bool
    role: str = "input"
    read_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Summary handed to the harness before it chooses a task strategy."""

    version: str
    root: str
    input_dir: str
    output_dir: str
    task_id: str
    discovered_at: str
    files: list[WorkspaceFile] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["files"] = [item.as_dict() for item in self.files]
        return data


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: Any, *, fallback: str = "skillsbench_task", limit: int = 96) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.+-]+", "-", text).strip("-._")
    return (text or fallback)[:limit]


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _coerce_text(value: Any, *, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


def _iter_mapping_values(value: Any, *, depth: int = 0, limit: int = 200) -> Iterable[Any]:
    if value is None or depth > 4 or limit <= 0:
        return
    if isinstance(value, Mapping):
        count = 0
        for key, child in value.items():
            yield key
            yield child
            count += 2
            if count >= limit:
                return
            if isinstance(child, (Mapping, list, tuple)):
                yield from _iter_mapping_values(child, depth=depth + 1, limit=max(1, limit - count))
    elif isinstance(value, (list, tuple)):
        for child in list(value)[:limit]:
            yield child
            if isinstance(child, (Mapping, list, tuple)):
                yield from _iter_mapping_values(child, depth=depth + 1, limit=max(1, limit - 1))


def _looks_like_path(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 600:
        return False
    if text.startswith(("http://", "https://", "s3://", "gs://")):
        return False
    if "\n" in text or "\r" in text:
        return False
    if "/" in text or "\\" in text:
        return True
    return bool(re.search(r"(?i)\.(py|js|ts|json|csv|xlsx|pptx|docx|pdf|md|txt|toml|yaml|yml|lean|obj|stl|dxf|zip)$", text))


def _resolve_existing_dir(path_value: Any, *, base: Path | None = None) -> Path | None:
    text = str(path_value or "").strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        if not path.is_absolute() and base is not None:
            path = base / path
        path = path.resolve()
        if path.exists() and path.is_dir():
            return path
    except Exception:
        return None
    return None


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.name


def _mime_for_path(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() in TEXT_SUFFIXES:
        return "text/plain"
    if path.suffix.lower() == ".patch" or path.suffix.lower() == ".diff":
        return "text/x-diff"
    return "application/octet-stream"


def _safe_decode(raw: bytes, *, max_chars: int = DEFAULT_MAX_TEXT_CHARS) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding, errors="replace")[:max_chars]
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")[:max_chars]


def _bytes_are_text(raw: bytes, suffix: str = "") -> bool:
    if suffix.lower() in TEXT_SUFFIXES:
        return True
    if suffix.lower() in BINARY_SUFFIXES:
        return False
    if not raw:
        return True
    sample = raw[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except Exception:
        # Treat mostly printable Latin-1 as text, but be conservative.
        printable = sum(1 for b in sample if b in (9, 10, 13) or 32 <= b <= 126)
        return printable / max(len(sample), 1) > 0.88


def infer_task_id(metadata: Mapping[str, Any] | None = None, text: str = "") -> str:
    metadata = _as_mapping(metadata)
    for key in ("task_id", "id", "name", "task", "challenge_id", "scenario_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _slug(value)
    blob = " ".join(_coerce_text(value, limit=200) for value in _iter_mapping_values(metadata, limit=80))
    blob += "\n" + str(text or "")
    match = re.search(r"(?i)\b(?:task_id|task|id)\s*[:=]\s*['\"]?([a-z0-9][a-z0-9_.+-]{2,120})", blob)
    if match:
        return _slug(match.group(1))
    match = re.search(r"(?i)\b([a-z0-9]+(?:-[a-z0-9]+){1,10})\b", blob)
    if match:
        return _slug(match.group(1))
    return "skillsbench_task"


def discover_workspace_root(metadata: Mapping[str, Any] | None = None, text: str = "") -> Path:
    """Best-effort workspace discovery.

    Preference order:
    1. explicit workspace/task directory fields in metadata;
    2. SkillsBench/runner environment variables;
    3. path-looking values from metadata;
    4. current working directory.

    The returned path always exists and is a directory.
    """

    metadata = _as_mapping(metadata)

    explicit_keys = (
        "workspace",
        "workspace_dir",
        "workdir",
        "cwd",
        "task_dir",
        "task_root",
        "repo_dir",
        "repository",
        "project_dir",
        "input_dir",
        "files_dir",
    )
    for key in explicit_keys:
        candidate = _resolve_existing_dir(metadata.get(key))
        if candidate is not None:
            return candidate

    for env_key in WORKSPACE_ENV_KEYS:
        candidate = _resolve_existing_dir(os.getenv(env_key))
        if candidate is not None:
            return candidate

    cwd = Path.cwd().resolve()

    for value in _iter_mapping_values(metadata, limit=160):
        if not _looks_like_path(value):
            continue
        candidate = _resolve_existing_dir(value, base=cwd)
        if candidate is not None:
            return candidate
        try:
            maybe_file = Path(str(value)).expanduser()
            if not maybe_file.is_absolute():
                maybe_file = cwd / maybe_file
            maybe_file = maybe_file.resolve()
            if maybe_file.exists() and maybe_file.is_file():
                return maybe_file.parent
        except Exception:
            continue

    # Last-resort SkillsBench-ish common locations inside task containers.
    common = (
        "/workspace",
        "/workdir",
        "/task",
        "/app",
        "/repo",
        "/mnt/data",
        "/tmp/workspace",
    )
    for item in common:
        candidate = _resolve_existing_dir(item)
        if candidate is not None:
            return candidate

    return cwd


def discover_output_root(root: Path, metadata: Mapping[str, Any] | None = None, task_id: str = "") -> Path:
    """Find or create the safest output root available."""

    metadata = _as_mapping(metadata)

    explicit_keys = (
        "output_dir",
        "outputs_dir",
        "results_dir",
        "artifact_dir",
        "artifacts_dir",
        "submission_dir",
    )
    for key in explicit_keys:
        value = metadata.get(key)
        if value:
            try:
                path = Path(str(value)).expanduser()
                if not path.is_absolute():
                    path = root / path
                path = path.resolve()
                path.mkdir(parents=True, exist_ok=True)
                return path
            except Exception:
                continue

    for env_key in OUTPUT_ENV_KEYS:
        value = os.getenv(env_key)
        if value:
            try:
                path = Path(value).expanduser().resolve()
                path.mkdir(parents=True, exist_ok=True)
                return path
            except Exception:
                continue

    for relative in ("outputs", "output", "artifacts", "results"):
        try:
            path = (root / relative).resolve()
            if _is_relative_to(path, root):
                path.mkdir(parents=True, exist_ok=True)
                return path
        except Exception:
            continue

    # Fallback stays under /tmp but names the task so later diagnostics can find it.
    path = Path("/tmp") / "aegisforge_skillsbench_outputs" / _slug(task_id)
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


class SkillsBenchWorkspace:
    """A safe filesystem view for a single SkillsBench task."""

    def __init__(
        self,
        *,
        root: Path,
        output_dir: Path,
        task_id: str,
        input_dir: Path | None = None,
        max_files: int = DEFAULT_MAX_FILES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        self.root = root.resolve()
        self.input_dir = (input_dir or root).resolve()
        self.output_dir = output_dir.resolve()
        self.task_id = _slug(task_id)
        self.max_files = max(1, int(max_files))
        self.max_file_bytes = max(1024, int(max_file_bytes))
        self.max_total_bytes = max(1024, int(max_total_bytes))
        self.warnings: list[str] = []

    @classmethod
    def discover(
        cls,
        metadata: Mapping[str, Any] | None = None,
        text: str = "",
        *,
        task_id: str | None = None,
        max_files: int = DEFAULT_MAX_FILES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ) -> "SkillsBenchWorkspace":
        metadata = _as_mapping(metadata)
        resolved_task_id = task_id or infer_task_id(metadata, text)
        root = discover_workspace_root(metadata, text)
        output = discover_output_root(root, metadata, task_id=resolved_task_id)
        input_dir = root
        for key in ("input_dir", "inputs_dir", "files_dir"):
            candidate = _resolve_existing_dir(metadata.get(key), base=root)
            if candidate is not None and _is_relative_to(candidate, root):
                input_dir = candidate
                break
        return cls(
            root=root,
            input_dir=input_dir,
            output_dir=output,
            task_id=resolved_task_id,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
        )

    def safe_join(self, *parts: str | os.PathLike[str], base: Path | None = None, create_parent: bool = False) -> Path:
        """Join paths without allowing traversal outside the selected base.

        For output paths, pass base=self.output_dir.  For input paths, the
        default base is self.root.
        """

        selected_base = (base or self.root).resolve()
        raw = Path()
        for part in parts:
            if part is None:
                continue
            text = os.fspath(part)
            # Reject absolute fragments by taking their relative-looking tail.
            text = text.replace("\\", "/")
            text = re.sub(r"^[A-Za-z]:", "", text)
            text = text.lstrip("/")
            raw = raw / text
        path = (selected_base / raw).resolve()
        if not _is_relative_to(path, selected_base):
            raise ValueError(f"unsafe path outside workspace base: {path}")
        if create_parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _iter_files(self) -> Iterable[Path]:
        if not self.input_dir.exists():
            return
        count = 0
        for path in sorted(self.input_dir.rglob("*"), key=lambda p: p.as_posix()):
            if count >= self.max_files:
                self.warnings.append(f"file listing truncated at {self.max_files} files")
                break
            try:
                if not path.is_file():
                    continue
                rel_parts = set(path.relative_to(self.input_dir).parts)
                if rel_parts & SKIP_DIR_NAMES:
                    continue
                if not _is_relative_to(path, self.root):
                    continue
                count += 1
                yield path
            except Exception:
                continue

    def describe_file(self, path: Path, *, role: str = "input") -> WorkspaceFile:
        resolved = path.resolve()
        if not _is_relative_to(resolved, self.root) and not _is_relative_to(resolved, self.output_dir):
            raise ValueError(f"refusing to describe path outside workspace/output: {path}")
        try:
            size = resolved.stat().st_size
            with resolved.open("rb") as handle:
                raw = handle.read(min(size, self.max_file_bytes))
            digest = hashlib.sha256(raw).hexdigest()
            suffix = resolved.suffix.lower()
            is_text = _bytes_are_text(raw, suffix)
            return WorkspaceFile(
                path=str(resolved),
                relative_path=_safe_relative(resolved, self.root if _is_relative_to(resolved, self.root) else self.output_dir),
                name=resolved.name,
                suffix=suffix,
                mime_type=_mime_for_path(resolved),
                size_bytes=int(size),
                sha256=digest,
                is_text=is_text,
                is_binary=not is_text,
                role=role,
            )
        except Exception as exc:
            return WorkspaceFile(
                path=str(resolved),
                relative_path=_safe_relative(resolved, self.root),
                name=resolved.name,
                suffix=resolved.suffix.lower(),
                mime_type=_mime_for_path(resolved),
                size_bytes=0,
                sha256="",
                is_text=False,
                is_binary=True,
                role=role,
                read_error=str(exc)[:300],
            )

    def list_inputs(self) -> list[WorkspaceFile]:
        files: list[WorkspaceFile] = []
        total = 0
        for path in self._iter_files():
            info = self.describe_file(path, role="input")
            files.append(info)
            total += max(0, int(info.size_bytes))
            if total >= self.max_total_bytes:
                self.warnings.append(f"file listing byte budget reached at {self.max_total_bytes} bytes")
                break
        return files

    def read_bytes(self, relative_or_path: str | os.PathLike[str], *, max_bytes: int | None = None) -> bytes:
        max_bytes = max_bytes or self.max_file_bytes
        path = Path(relative_or_path)
        if path.is_absolute():
            resolved = path.resolve()
            if not _is_relative_to(resolved, self.root):
                raise ValueError(f"refusing to read outside workspace: {resolved}")
        else:
            resolved = self.safe_join(path)
        with resolved.open("rb") as handle:
            return handle.read(max(1, int(max_bytes)))

    def read_text(self, relative_or_path: str | os.PathLike[str], *, max_bytes: int | None = None, max_chars: int = DEFAULT_MAX_TEXT_CHARS) -> str:
        raw = self.read_bytes(relative_or_path, max_bytes=max_bytes)
        return _safe_decode(raw, max_chars=max_chars)

    def summarize_inputs(self, *, max_files: int = 40, max_text_chars_per_file: int = 4000) -> dict[str, Any]:
        files = self.list_inputs()
        summaries: list[dict[str, Any]] = []
        for info in files[:max_files]:
            item = info.as_dict()
            if info.is_text and info.size_bytes <= self.max_file_bytes and not info.read_error:
                try:
                    item["text_excerpt"] = self.read_text(info.path, max_chars=max_text_chars_per_file)
                except Exception as exc:
                    item["text_excerpt_error"] = str(exc)[:240]
            summaries.append(item)
        return {
            "version": WORKSPACE_VERSION,
            "task_id": self.task_id,
            "root": str(self.root),
            "input_dir": str(self.input_dir),
            "output_dir": str(self.output_dir),
            "file_count": len(files),
            "files": summaries,
            "warnings": list(self.warnings),
        }

    def write_output(
        self,
        name: str,
        content: str | bytes | bytearray | Mapping[str, Any] | list[Any],
        *,
        mime_type: str | None = None,
        subdir: str = "",
        ensure_newline: bool = True,
    ) -> WorkspaceFile:
        safe_name = _sanitize_output_name(name)
        base = self.output_dir
        if subdir:
            base = self.safe_join(_slug(subdir), base=self.output_dir, create_parent=True)
        path = self.safe_join(safe_name, base=base, create_parent=True)

        if isinstance(content, (dict, list)):
            raw = (json.dumps(content, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8")
        elif isinstance(content, str):
            text = content
            if ensure_newline and text and not text.endswith("\n"):
                text += "\n"
            raw = text.encode("utf-8", errors="replace")
        else:
            raw = bytes(content)

        with path.open("wb") as handle:
            handle.write(raw)

        described = self.describe_file(path, role="output")
        if mime_type:
            return WorkspaceFile(
                **{
                    **described.as_dict(),
                    "mime_type": mime_type,
                }
            )
        return described

    def write_json(self, name: str, payload: Mapping[str, Any] | list[Any], *, subdir: str = "") -> WorkspaceFile:
        return self.write_output(name, payload, mime_type="application/json", subdir=subdir)

    def snapshot(self) -> WorkspaceSnapshot:
        files = self.list_inputs()
        total = sum(max(0, int(item.size_bytes)) for item in files)
        return WorkspaceSnapshot(
            version=WORKSPACE_VERSION,
            root=str(self.root),
            input_dir=str(self.input_dir),
            output_dir=str(self.output_dir),
            task_id=self.task_id,
            discovered_at=_now_iso(),
            files=files,
            file_count=len(files),
            total_bytes=total,
            truncated=any("truncated" in warning for warning in self.warnings),
            warnings=list(self.warnings),
        )

    def write_manifest(self, *, extra: Mapping[str, Any] | None = None, name: str = "workspace_manifest.json") -> WorkspaceFile:
        snapshot = self.snapshot().as_dict()
        payload: dict[str, Any] = {
            "schema": "aegisforge.skillsbench.workspace_manifest.v0_1",
            "version": WORKSPACE_VERSION,
            "task_id": self.task_id,
            "created_at": _now_iso(),
            "workspace": snapshot,
            "extra": dict(extra or {}),
            "safe_scope": {
                "no_network": True,
                "no_shell_execution": True,
                "bounded_file_reads": True,
                "path_traversal_guard": True,
            },
        }
        return self.write_json(name, payload)

    def as_context(self, *, include_text: bool = False) -> dict[str, Any]:
        context = self.summarize_inputs(max_files=60 if include_text else 80, max_text_chars_per_file=4000 if include_text else 0)
        if not include_text:
            for item in context.get("files", []):
                if isinstance(item, dict):
                    item.pop("text_excerpt", None)
        return context


def _sanitize_output_name(name: str, *, fallback: str = "skillsbench_output.md") -> str:
    raw = str(name or "").replace("\\", "/").split("/")[-1].strip()
    raw = re.sub(r"[^A-Za-z0-9_.+\-]+", "_", raw).strip("._")
    if not raw:
        raw = fallback
    if "." not in raw:
        raw += ".md"
    return raw[:160]


def build_workspace_context(metadata: Mapping[str, Any] | None = None, text: str = "", *, include_text: bool = False) -> dict[str, Any]:
    workspace = SkillsBenchWorkspace.discover(metadata, text)
    return workspace.as_context(include_text=include_text)


__all__ = [
    "WORKSPACE_VERSION",
    "WorkspaceFile",
    "WorkspaceSnapshot",
    "SkillsBenchWorkspace",
    "build_workspace_context",
    "discover_workspace_root",
    "discover_output_root",
    "infer_task_id",
]
