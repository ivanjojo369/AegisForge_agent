"""Bounded, read-only repository symbol and dependency index for Colossus.

Target: src/aegisforge/nexus/workspace/repo_index.py

Uses NexusWorkspaceManager, never accesses unapproved paths directly, never
executes inspected source code, and never creates output files. Python parsing
uses stdlib ``ast``; JavaScript/TypeScript extraction is explicitly heuristic.
The index is for navigation/planning, NOT proof of runtime dependencies or a
security boundary. A trusted host must authorize the selected workspace.

The in-memory cache avoids reparsing unchanged text within one index instance;
files are reread to validate the cache digest. No persistent database yet.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
import re
from typing import Any

from .manager import NexusWorkspaceError, NexusWorkspaceManager


INDEX_SCHEMA = "aegisforge.nexus.workspace.repo_index.v1"
_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
_JS_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"})
_CODE_SUFFIXES = _PYTHON_SUFFIXES | _JS_SUFFIXES
_RESTRICTED_BASENAMES = frozenset({
    "credentials.py", "credentials.js", "secrets.py", "secrets.js",
    "secrets.ts", "token.py", "token.js", "token.ts",
})
_IMPORT_RE = re.compile(
    r"(?m)^\s*(?:import\s+(?:[^\n;]*?\s+from\s+)?|export\s+[^\n;]*?\s+from\s+)"
    r"['\"]([^'\"\r\n]+)['\"]"
    r"|(?m:^\s*(?:(?:const|let|var)\s+[^=\n]+?=\s*)?require\s*\(\s*['\"]([^'\"\r\n]+)['\"]\s*\))"
)
_JS_SYMBOL_RE = re.compile(
    r"(?m)^\s*(?:export\s+(?:default\s+)?)?"
    r"(?:(async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("
    r"|class\s+([A-Za-z_$][\w$]*)\b"
    r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=)"
)


class NexusRepositoryIndexError(ValueError):
    """An index request is invalid or no successfully built index exists."""


@dataclass(frozen=True, slots=True)
class RepositoryIndexLimits:
    """Caps on parsed source, symbols, imports, and graph edges."""

    max_indexed_files: int = 240
    max_symbols_per_file: int = 250
    max_imports_per_file: int = 250
    max_edges: int = 4_000
    max_query_results: int = 100

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise NexusRepositoryIndexError(f"{name} must be a positive integer")


def _python_module(path: str) -> str:
    """Conventional Python module ID from repository-relative filename."""
    parts = list(PurePosixPath(path).parts)
    if "src" in parts[:-1]:
        parts = parts[parts.index("src") + 1:]
    if not parts or not parts[-1].endswith((".py", ".pyi")):
        return ""
    if parts[-1].startswith("__init__."):
        parts.pop()
    else:
        parts[-1] = parts[-1].rsplit(".", 1)[0]
    return ".".join(parts)


def _python_ast(source: str, path: str, limits: RepositoryIndexLimits) -> dict[str, Any]:
    """Collect definitions and lexical imports; never evaluate source."""
    try:
        tree = ast.parse(source, filename=path, type_comments=True)
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return {"status": "parse_error", "symbols": [], "imports": [],
                "symbols_truncated": False, "imports_truncated": False}

    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    sym_truncated = False
    imp_truncated = False

    def add_symbol(name: str, kind: str, line: int) -> None:
        nonlocal sym_truncated
        if len(symbols) >= limits.max_symbols_per_file:
            sym_truncated = True
        else:
            symbols.append({"name": name, "kind": kind, "line": line})

    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_symbol(item.name, "async_function" if isinstance(item, ast.AsyncFunctionDef) else "function", item.lineno)
        elif isinstance(item, ast.ClassDef):
            add_symbol(item.name, "class", item.lineno)
            for sub in item.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add_symbol(f"{item.name}.{sub.name}", "async_method" if isinstance(sub, ast.AsyncFunctionDef) else "method", sub.lineno)

    for item in ast.walk(tree):
        if isinstance(item, ast.Import):
            for alias in item.names:
                if len(imports) >= limits.max_imports_per_file:
                    imp_truncated = True
                    break
                imports.append({"kind": "import", "module": alias.name, "level": 0,
                                "names": [], "line": item.lineno})
        elif isinstance(item, ast.ImportFrom):
            if len(imports) >= limits.max_imports_per_file:
                imp_truncated = True
                continue
            if len(item.names) > 50:
                imp_truncated = True
            imports.append({"kind": "from", "module": item.module or "",
                            "level": item.level, "names": [a.name for a in item.names[:50]],
                            "line": item.lineno})
    imports.sort(key=lambda row: (row["line"], row["module"], row["kind"]))
    return {"status": "indexed", "symbols": symbols, "imports": imports,
            "symbols_truncated": sym_truncated, "imports_truncated": imp_truncated}


def _javascript_heuristic(source: str, limits: RepositoryIndexLimits) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    sym_truncated = False
    imp_truncated = False
    for match in _JS_SYMBOL_RE.finditer(source):
        if len(symbols) >= limits.max_symbols_per_file:
            sym_truncated = True
            break
        function_name, class_name, binding_name = match.group(2, 3, 4)
        name = function_name or class_name or binding_name
        kind = "function" if function_name else "class" if class_name else "binding"
        symbols.append({"name": name, "kind": kind,
                        "line": source.count("\n", 0, match.start()) + 1})
    for match in _IMPORT_RE.finditer(source):
        if len(imports) >= limits.max_imports_per_file:
            imp_truncated = True
            break
        spec = match.group(1) or match.group(2)
        imports.append({"kind": "static_specifier", "module": spec, "level": 0,
                        "names": [], "line": source.count("\n", 0, match.start()) + 1})
    return {"status": "heuristic", "symbols": symbols, "imports": imports,
            "symbols_truncated": sym_truncated, "imports_truncated": imp_truncated}


def _relative_import_base(module_id: str, is_package: bool, level: int, imported_module: str) -> str:
    package = module_id.split(".") if is_package else module_id.split(".")[:-1]
    if level > len(package):
        return ""
    prefix = package[:len(package) - level + 1]
    suffix = imported_module.split(".") if imported_module else []
    return ".".join(prefix + suffix)


def _local_python_targets(entry: dict[str, Any], known: dict[str, str]) -> set[str]:
    targets: set[str] = set()
    current_id = entry.get("module") or ""
    is_package = PurePosixPath(entry["relative_path"]).stem == "__init__"
    for imp in entry["imports"]:
        if imp["kind"] == "import":
            base = imp["module"]
        elif imp["level"]:
            base = _relative_import_base(current_id, is_package, imp["level"], imp["module"])
        else:
            base = imp["module"]
        if base in known:
            targets.add(known[base])
        # Handles ``from . import config`` or ``from package import submodule``.
        if imp["kind"] == "from" and base:
            for name in imp["names"]:
                submodule = base + "." + name
                if submodule in known:
                    targets.add(known[submodule])
    targets.discard(entry["relative_path"])
    return targets


def _local_js_targets(entry: dict[str, Any], available: set[str]) -> set[str]:
    source = PurePosixPath(entry["relative_path"])
    targets: set[str] = set()
    for imp in entry["imports"]:
        spec = imp["module"]
        if not spec.startswith("./") and not spec.startswith("../"):
            continue
        # Use lexical normalization, no disk lookup or traversal beyond root.
        parts = list(source.parent.parts)
        for segment in spec.replace("\\", "/").split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                if not parts:
                    break
                parts.pop()
            else:
                parts.append(segment)
        else:
            normalized = "/".join(parts)
            candidates = [normalized]
            candidates.extend(normalized + suffix for suffix in sorted(_JS_SUFFIXES))
            candidates.extend(normalized + "/index" + suffix for suffix in sorted(_JS_SUFFIXES))
            for candidate in candidates:
                if candidate in available:
                    targets.add(candidate)
                    break
    targets.discard(entry["relative_path"])
    return targets


class NexusRepositoryIndex:
    """Analyze only files visible through an injected NexusWorkspaceManager.

    ``build`` indexes supported code files and resolves local dependencies.
    Results and cache exist in memory only. ``find_symbols``, ``dependencies``
    and ``dependents`` query the last built snapshot; rebuild after changes.
    """

    def __init__(
        self,
        workspace: NexusWorkspaceManager,
        *,
        limits: RepositoryIndexLimits | None = None,
        include_text_targets: bool = False,
    ) -> None:
        if not isinstance(workspace, NexusWorkspaceManager):
            raise NexusRepositoryIndexError("workspace must be a NexusWorkspaceManager")
        if type(include_text_targets) is not bool:
            raise NexusRepositoryIndexError("include_text_targets must be bool")
        self.include_text_targets = include_text_targets
        self.workspace = workspace
        self.limits = limits if limits is not None else RepositoryIndexLimits()
        if not isinstance(self.limits, RepositoryIndexLimits):
            raise NexusRepositoryIndexError("limits must be RepositoryIndexLimits")
        self._cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self._last: dict[str, Any] | None = None

    def build(self, scope: str = ".") -> dict[str, Any]:
        """Return JSON-compatible metadata; never include source code excerpts."""
        if not isinstance(scope, str) or not scope.strip():
            raise NexusRepositoryIndexError("scope must be a relative directory")
        inventory = self.workspace.inventory(scope, with_hashes=False)
        from .manager import _TEXT_SUFFIXES
        extensions = _TEXT_SUFFIXES if self.include_text_targets else _CODE_SUFFIXES
        candidates = sorted(
            (row for row in inventory["files"]
             if PurePosixPath(row["relative_path"]).suffix.lower() in extensions),
            key=lambda row: row["relative_path"],
        )
        entries: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        cache_hits = 0
        read_bytes = 0
        cap_reached = len(candidates) > self.limits.max_indexed_files
        for info in candidates[:self.limits.max_indexed_files]:
            path = info["relative_path"]
            filename = PurePosixPath(path).name.lower()
            if filename in _RESTRICTED_BASENAMES:
                skipped.append({"relative_path": path, "reason": "restricted_filename"})
                continue
            if info["size_bytes"] > min(self.workspace.limits.max_file_bytes, self.workspace.limits.max_text_chars):
                skipped.append({"relative_path": path, "reason": "source_exceeds_read_limit"})
                continue
            if read_bytes + info["size_bytes"] > self.workspace.limits.max_total_bytes:
                skipped.append({"relative_path": path, "reason": "total_read_budget_exceeded"})
                continue
            try:
                source = self.workspace.read_text(path)
            except (NexusWorkspaceError, OSError, UnicodeError, ValueError):
                skipped.append({"relative_path": path, "reason": "source_unavailable"})
                continue
            read_bytes += info["size_bytes"]
            digest = sha256(source.encode("utf-8")).hexdigest()
            cached = self._cache.get(path)
            if cached is not None and cached[0] == digest:
                parsed = cached[1]
                cache_hits += 1
            else:
                suffix = PurePosixPath(path).suffix.lower()
                parsed = (_python_ast(source, path, self.limits) if suffix in _PYTHON_SUFFIXES
                          else _javascript_heuristic(source, self.limits) if suffix in _JS_SUFFIXES
                          else {"status": "text_unparsed", "symbols": [], "imports": [],
                                "symbols_truncated": False, "imports_truncated": False})
                self._cache[path] = (digest, parsed)
            if parsed["status"] == "parse_error":
                skipped.append({"relative_path": path, "reason": "python_syntax_error"})
                continue
            suffix = PurePosixPath(path).suffix.lower()
            entry = {
                "relative_path": path,
                "language": ("python" if suffix in _PYTHON_SUFFIXES else
                             "javascript_typescript" if suffix in _JS_SUFFIXES else "text"),
                "analysis": parsed["status"],
                "module": _python_module(path) if suffix in _PYTHON_SUFFIXES else None,
                "source_text_sha256": digest,
                "symbols": deepcopy(parsed["symbols"]),
                "imports": deepcopy(parsed["imports"]),
                "symbols_truncated": parsed["symbols_truncated"],
                "imports_truncated": parsed["imports_truncated"],
                "local_dependencies": [],
            }
            entries.append(entry)

        # Prefer a Python implementation over its stub when both are present.
        modules: dict[str, str] = {}
        for item in sorted(entries, key=lambda row: row["relative_path"].endswith(".pyi")):
            if item["module"]:
                modules.setdefault(item["module"], item["relative_path"])
        available = {item["relative_path"] for item in entries}
        edges: list[dict[str, str]] = []
        edges_truncated = False
        for item in entries:
            targets = (_local_python_targets(item, modules) if item["language"] == "python"
                       else _local_js_targets(item, available)
                       if item["language"] == "javascript_typescript" else [])
            item["local_dependencies"] = sorted(targets)
            for target in sorted(targets):
                if len(edges) >= self.limits.max_edges:
                    edges_truncated = True
                    break
                edges.append({"source": item["relative_path"], "target": target})

        # Retain only files touched by this build to bound cache growth.
        active_paths = {row["relative_path"] for row in candidates[:self.limits.max_indexed_files]}
        self._cache = {path: cached for path, cached in self._cache.items() if path in active_paths}

        # An incomplete scan must never appear as an authoritative repository map.
        partial = (bool(inventory["truncated"]) or bool(inventory["warnings"])
                   or cap_reached or bool(skipped) or edges_truncated
                   or any(e["symbols_truncated"] or e["imports_truncated"] for e in entries))
        output = {
            "schema": INDEX_SCHEMA,
            "root": str(self.workspace.root),
            "scope": scope,
            "complete": not partial,
            "inventory_truncated": bool(inventory["truncated"]),
            "limits_reached": cap_reached or edges_truncated,
            "indexed_file_count": len(entries),
            "source_bytes_read": read_bytes,
            "symbol_count": sum(len(e["symbols"]) for e in entries),
            "local_dependency_count": len(edges),
            "cache_hits": cache_hits,
            "skipped_files": skipped,
            "warnings": list(inventory["warnings"]),
            "files": entries,
            "edges": edges,
        }
        self._last = output
        return deepcopy(output)

    def _snapshot(self) -> dict[str, Any]:
        if self._last is None:
            raise NexusRepositoryIndexError("build() must be called first")
        return self._last

    def find_symbols(self, query: str) -> list[dict[str, Any]]:
        """Case-insensitive substring search, bounded by max_query_results."""
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise NexusRepositoryIndexError("query must contain 1..200 characters")
        needle = query.casefold().strip()
        found: list[dict[str, Any]] = []
        for item in self._snapshot()["files"]:
            for symbol in item["symbols"]:
                if needle in symbol["name"].casefold():
                    found.append({"relative_path": item["relative_path"], **symbol})
                    if len(found) >= self.limits.max_query_results:
                        return found
        return found

    def dependencies(self, relative_path: str) -> list[str]:
        if not isinstance(relative_path, str):
            raise NexusRepositoryIndexError("relative_path must be a string")
        for item in self._snapshot()["files"]:
            if item["relative_path"] == relative_path.replace("\\", "/"):
                return list(item["local_dependencies"])
        raise NexusRepositoryIndexError("path not found in current index")

    def dependents(self, relative_path: str) -> list[str]:
        if not isinstance(relative_path, str):
            raise NexusRepositoryIndexError("relative_path must be a string")
        path = relative_path.replace("\\", "/")
        snapshot = self._snapshot()
        if not any(item["relative_path"] == path for item in snapshot["files"]):
            raise NexusRepositoryIndexError("path not found in current index")
        return sorted(item["relative_path"] for item in snapshot["files"]
                      if path in item["local_dependencies"])


__all__ = ["INDEX_SCHEMA", "NexusRepositoryIndexError", "RepositoryIndexLimits", "NexusRepositoryIndex"]
