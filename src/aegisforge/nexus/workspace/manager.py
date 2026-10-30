"""Read-only workspace adapter for AegisForge Nexus — Colossus.

Target: src/aegisforge/nexus/workspace/manager.py

Reuses SkillsBenchWorkspace's safe path resolution and bounded file reading.
Intentionally does NOT call SkillsBenchWorkspace.discover(), which may create
output directories. This module neither runs commands nor writes files.

Security boundary: This is a host-controlled inspection adapter, NOT an OS
sandbox. The trusted host must select the root, enforce caller authorization,
and isolate untrusted processes. In particular, filesystem content can change
between validation and reading (TOCTOU); do not use this as a security boundary
against an attacker with concurrent filesystem write access.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
import re

from aegisforge.adapters.skillsbench.workspace import SkillsBenchWorkspace


_INVENTORY_SCHEMA = "aegisforge.nexus.workspace.inventory.v1"
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    "node_modules", ".next", "dist", "build", ".cache", ".ssh",
    ".aws", ".kube", ".azure", ".gnupg", "secrets",
})
_SENSITIVE_NAMES = frozenset({
    "id_rsa", "id_ed25519", "authorized_keys", "credentials.json",
    "credentials.yaml", "credentials.yml", "secrets.json", "secrets.yaml",
    "secrets.yml", "token.json", "tokens.json", ".npmrc", ".pypirc",
    "terraform.tfstate", "terraform.tfstate.backup",
})
_SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".kdbx"})
_TEXT_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".json", ".jsonl", ".toml", ".yaml", ".yml", ".xml", ".ini",
    ".cfg", ".conf", ".md", ".mdx", ".rst", ".txt", ".csv",
    ".tsv", ".sql", ".html", ".css", ".sh", ".ps1", ".bat",
    ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp",
    ".dockerignore", ".gitignore",
})
_DRIVE_PREFIX = re.compile(r"^[a-zA-Z]:")


class NexusWorkspaceError(ValueError):
    """A workspace path, inspection request or read was rejected."""


@dataclass(frozen=True, slots=True)
class WorkspaceLimits:
    """Resource caps for one listing or read operation."""

    max_files: int = 240
    max_file_bytes: int = 262_144
    max_total_bytes: int = 4_000_000
    max_text_chars: int = 12_000
    max_visited_entries: int = 12_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise NexusWorkspaceError(f"{name} must be a positive integer")
        if self.max_text_chars > self.max_file_bytes:
            raise NexusWorkspaceError("max_text_chars cannot exceed max_file_bytes")


class NexusWorkspaceManager:
    """Bounded, read-only repository inspection for a TRUSTED host-selected root.

    No implicit workspace discovery, directory creation, file writes, network
    calls, shell commands, or user-supplied tool execution. All relative paths
    remain beneath the approved root; known sensitive paths are excluded.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        limits: WorkspaceLimits | None = None,
        task_id: str = "colossus_workspace",
    ) -> None:
        if not isinstance(root, (str, Path)) or not str(root).strip():
            raise NexusWorkspaceError("root must be an explicit directory")
        proposed = Path(root).expanduser()
        if not proposed.is_absolute():
            raise NexusWorkspaceError("root must be an absolute path chosen by the trusted host")
        if self._is_link(proposed):
            raise NexusWorkspaceError("workspace root cannot be a symbolic link or junction")
        try:
            canonical = proposed.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise NexusWorkspaceError("workspace root does not exist") from exc
        if not canonical.is_dir():
            raise NexusWorkspaceError("workspace root is not a directory")
        self.root = canonical
        self.limits = limits if limits is not None else WorkspaceLimits()
        if not isinstance(self.limits, WorkspaceLimits):
            raise NexusWorkspaceError("limits must be WorkspaceLimits")

        # Direct construction is side-effect free. Never call .discover(),
        # .write_output(), .write_json() or .write_manifest() from this adapter.
        self._backend = SkillsBenchWorkspace(
            root=self.root,
            input_dir=self.root,
            output_dir=self.root,
            task_id=task_id,
            max_files=self.limits.max_files,
            max_file_bytes=self.limits.max_file_bytes,
            max_total_bytes=self.limits.max_total_bytes,
        )

    @staticmethod
    def _is_link(path: Path) -> bool:
        try:
            return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
        except (OSError, RuntimeError):
            return True

    @staticmethod
    def _blocked_file(name: str) -> bool:
        lower = name.casefold()
        return (
            lower == ".env" or lower.startswith(".env.") or lower.endswith(".env")
            or lower in _SENSITIVE_NAMES
            or Path(lower).suffix in _SENSITIVE_SUFFIXES
        )

    def _relative_parts(self, relative: str | Path) -> tuple[str, ...]:
        if not isinstance(relative, (str, Path)):
            raise NexusWorkspaceError("path must be a relative string or Path")
        raw = str(relative).replace("\\", "/")
        if not raw or raw.startswith("/") or _DRIVE_PREFIX.match(raw) or "\x00" in raw:
            raise NexusWorkspaceError("absolute, empty or invalid paths are prohibited")
        parts = PurePosixPath(raw).parts
        if not parts or any(part in ("..", "") or ":" in part for part in parts):
            raise NexusWorkspaceError("unsafe workspace path")
        if any(part.casefold() in _SKIP_DIRS for part in parts[:-1]):
            raise NexusWorkspaceError("restricted directory")
        if parts[-1].casefold() in _SKIP_DIRS or self._blocked_file(parts[-1]):
            raise NexusWorkspaceError("restricted workspace path")
        return parts

    def _resolve(self, relative: str | Path, *, require_file: bool = False) -> Path:
        parts = self._relative_parts(relative)
        current = self.root
        for part in parts:
            current = current / part
            if self._is_link(current):
                raise NexusWorkspaceError("symbolic links and junctions are not accepted")
        try:
            safe = self._backend.safe_join(*parts)
        except (ValueError, OSError, RuntimeError) as exc:
            raise NexusWorkspaceError("path escapes workspace root") from exc
        if require_file and (not safe.is_file() or self._is_link(safe)):
            raise NexusWorkspaceError("requested path is not a regular file")
        return safe

    def read_text(self, relative_path: str | Path, *, max_chars: int | None = None) -> str:
        """Read a small, allowed text file. Never truncate a larger file silently."""
        path = self._resolve(relative_path, require_file=True)
        if path.suffix.casefold() not in _TEXT_SUFFIXES and path.name.casefold() not in {
            "dockerfile", "makefile", "license", ".gitignore", ".dockerignore"
        }:
            raise NexusWorkspaceError("file type is not approved for text inspection")
        limit = self.limits.max_text_chars if max_chars is None else max_chars
        if type(limit) is not int or not 1 <= limit <= self.limits.max_text_chars:
            raise NexusWorkspaceError("max_chars exceeds configured text limit")
        try:
            size = path.stat().st_size
            if size > self.limits.max_file_bytes:
                raise NexusWorkspaceError("file exceeds configured read limit")
            # Delegate bounded filesystem access to the existing SkillsBench code.
            data = self._backend.read_bytes(path, max_bytes=size + 1)
        except OSError as exc:
            raise NexusWorkspaceError("file could not be read") from exc
        if len(data) != size or b"\x00" in data:
            raise NexusWorkspaceError("file changed during reading or is not text")
        return data.decode("utf-8-sig", errors="replace")[:limit]

    def inventory(self, relative_dir: str = ".", *, with_hashes: bool = True) -> dict[str, Any]:
        """List approved files, optionally digesting only fully read small files.

        SHA-256 is omitted if a file is too large, if the scan's read budget is
        exhausted, or if the file cannot be read completely. Listings have no
        side effects. A truncated listing is not a complete repository index.
        """
        if type(with_hashes) is not bool:
            raise NexusWorkspaceError("with_hashes must be boolean")
        if relative_dir == ".":
            start = self.root
            rel_display = "."
        else:
            start = self._resolve(relative_dir)
            rel_display = str(PurePosixPath(*self._relative_parts(relative_dir)))
            if not start.is_dir():
                raise NexusWorkspaceError("inventory path is not a directory")

        queue = deque([start])
        files: list[dict[str, Any]] = []
        warnings: list[str] = []
        visited = 0
        hashed_bytes = 0
        declared_bytes = 0
        truncated = False
        while queue and not truncated:
            folder = queue.popleft()
            try:
                children = sorted(folder.iterdir(), key=lambda p: p.name.casefold())
            except OSError:
                warnings.append("some directories could not be listed")
                continue
            for child in children:
                visited += 1
                if visited > self.limits.max_visited_entries:
                    truncated = True
                    warnings.append("directory entry scan limit reached")
                    break
                if self._is_link(child):
                    continue
                try:
                    # Also reject Windows reparse-point/junction escapes on
                    # Python versions without Path.is_junction().
                    child.resolve(strict=True).relative_to(self.root)
                except (OSError, ValueError, RuntimeError):
                    warnings.append("a filesystem entry could not be safely resolved")
                    continue
                try:
                    if child.is_dir():
                        if child.name.casefold() not in _SKIP_DIRS:
                            queue.append(child)
                        continue
                    if not child.is_file() or self._blocked_file(child.name):
                        continue
                    if len(files) >= self.limits.max_files:
                        truncated = True
                        warnings.append("file count limit reached")
                        break
                    stat = child.stat()
                    size = stat.st_size
                    rel = child.relative_to(self.root).as_posix()
                except (OSError, ValueError):
                    warnings.append("some file metadata could not be read")
                    continue

                digest: str | None = None
                if with_hashes and size <= self.limits.max_file_bytes and size + hashed_bytes <= self.limits.max_total_bytes:
                    try:
                        # Use SkillsBench for the bounded read, with an extra byte
                        # to detect files that grew between stat() and read().
                        data = self._backend.read_bytes(child, max_bytes=size + 1)
                        if len(data) == size and child.stat().st_size == size:
                            digest = sha256(data).hexdigest()
                            hashed_bytes += len(data)
                        else:
                            warnings.append("a file changed while hashing")
                    except (OSError, ValueError):
                        warnings.append("a file could not be hashed")
                declared_bytes += size
                files.append({
                    "relative_path": rel,
                    "size_bytes": size,
                    "sha256": digest,
                    "is_text_candidate": child.suffix.casefold() in _TEXT_SUFFIXES,
                })

        return {
            "schema": _INVENTORY_SCHEMA,
            "root": str(self.root),
            "relative_dir": rel_display,
            "file_count": len(files),
            "declared_bytes": declared_bytes,
            "hashed_bytes": hashed_bytes,
            "truncated": truncated,
            "warnings": warnings,
            "files": files,
        }


__all__ = ["NexusWorkspaceError", "WorkspaceLimits", "NexusWorkspaceManager"]
