"""Read-only, bounded static symbol graph for AegisForge Nexus — Colossus.

Target: src/aegisforge/nexus/workspace/symbol_graph.py

Builds on NexusRepositoryIndex and NexusWorkspaceManager, without executing
inspected source or modifying the repository. Recognizes indexed definitions,
file-level local imports, and *conservative* Python calls/inheritance.

Graph edges describe syntactic possibilities, NOT observed runtime behaviour.
Dynamic dispatch, monkey-patching, conditional imports, nested definitions and
JavaScript/TypeScript call resolution are outside this version's guarantees.
Results are in memory; all query results are returned as detached dictionaries.
"""

from __future__ import annotations

import ast
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any, Iterable

from .manager import NexusWorkspaceError
from .repo_index import NexusRepositoryIndex, NexusRepositoryIndexError


GRAPH_SCHEMA = "aegisforge.nexus.workspace.symbol_graph.v1"
_SYMBOL_KIND = frozenset({"function", "async_function", "class", "method", "async_method", "binding"})
_EDGE_KINDS = frozenset({"declares", "imports", "calls", "inherits"})


class NexusSymbolGraphError(ValueError):
    """Invalid graph configuration, request, or missing build."""


@dataclass(frozen=True, slots=True)
class SymbolGraphLimits:
    """Hard caps per graph build/query; all settings must be positive integers."""

    max_nodes: int = 12_000
    max_edges: int = 20_000
    max_calls_per_file: int = 2_000
    max_query_results: int = 100
    max_impact_depth: int = 5

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise NexusSymbolGraphError(f"{field_name} must be a positive integer")


def file_id(relative_path: str) -> str:
    """Stable ID for a repository-relative file; never a filesystem path handle."""
    return f"file:{relative_path}"


def symbol_id(relative_path: str, qualified_name: str) -> str:
    """Stable ID for a known definition within a repository-relative file."""
    return f"symbol:{relative_path}::{qualified_name}"


def _relative_module(module: str, is_package: bool, level: int, name: str) -> str:
    if not level:
        return name
    pieces = module.split(".") if is_package else module.split(".")[:-1]
    if level > len(pieces):
        return ""
    parent = pieces[:len(pieces) - level + 1]
    return ".".join(parent + (name.split(".") if name else []))


def _attribute_parts(expr: ast.expr) -> tuple[str, ...] | None:
    if isinstance(expr, ast.Name):
        return (expr.id,)
    if isinstance(expr, ast.Attribute):
        prefix = _attribute_parts(expr.value)
        return (*prefix, expr.attr) if prefix else None
    return None


def _shadowed_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Conservatively avoid resolving names locally bound inside this function."""
    args = node.args
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)

    class LocalBindings(ast.NodeVisitor):
        def visit_Name(self, child: ast.Name) -> None:
            if isinstance(child.ctx, (ast.Store, ast.Del)):
                names.add(child.id)

        def visit_Import(self, child: ast.Import) -> None:
            for alias in child.names:
                names.add(alias.asname or alias.name.split(".")[0])

        def visit_ImportFrom(self, child: ast.ImportFrom) -> None:
            for alias in child.names:
                names.add(alias.asname or alias.name)

        def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
            names.add(child.name)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, child: ast.ClassDef) -> None:
            names.add(child.name)

    bindings = LocalBindings()
    for statement in node.body:
        bindings.visit(statement)
    return names


class _Calls(ast.NodeVisitor):
    """Collect calls in one function's body, excluding nested definitions."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.calls: list[ast.Call] = []
        self.truncated = False

    def visit_Call(self, node: ast.Call) -> None:
        if len(self.calls) >= self.cap:
            self.truncated = True
            return
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass


def _collect_calls(node: ast.FunctionDef | ast.AsyncFunctionDef, cap: int) -> _Calls:
    collector = _Calls(cap)
    for item in node.body:
        collector.visit(item)
    return collector


class NexusSymbolGraph:
    """Static, permission-bounded graph built on a NexusRepositoryIndex.

    ``build(scope)`` rebuilds the repository snapshot. Query functions only
    access the last in-memory graph. They do not inspect files or run tools.
    """

    def __init__(
        self,
        repository_index: NexusRepositoryIndex,
        *,
        limits: SymbolGraphLimits | None = None,
    ) -> None:
        if not isinstance(repository_index, NexusRepositoryIndex):
            raise NexusSymbolGraphError("repository_index must be a NexusRepositoryIndex")
        self.repository_index = repository_index
        self.limits = limits if limits is not None else SymbolGraphLimits()
        if not isinstance(self.limits, SymbolGraphLimits):
            raise NexusSymbolGraphError("limits must be SymbolGraphLimits")
        self._last: dict[str, Any] | None = None

    def build(self, scope: str = ".") -> dict[str, Any]:
        """Build a detached JSON-compatible graph; never return source contents."""
        index = self.repository_index.build(scope)
        indexed_files = index["files"]
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        node_map: dict[str, dict[str, Any]] = {}
        edge_keys: set[tuple[str, str, str, int | None]] = set()
        warnings: list[str] = []
        nodes_limited = False
        edges_limited = False
        changed_sources: list[str] = []
        unsupported_python: list[str] = []

        def add_node(node: dict[str, Any]) -> bool:
            nonlocal nodes_limited
            key = node["id"]
            if key in node_map:
                return True
            if len(nodes) >= self.limits.max_nodes:
                nodes_limited = True
                return False
            nodes.append(node)
            node_map[key] = node
            return True

        def add_edge(source: str, target: str, kind: str, line: int | None = None) -> None:
            nonlocal edges_limited
            if source not in node_map or target not in node_map:
                return
            key = source, target, kind, line
            if key in edge_keys:
                return
            if len(edges) >= self.limits.max_edges:
                edges_limited = True
                return
            edge_keys.add(key)
            row: dict[str, Any] = {"source": source, "target": target, "kind": kind}
            if line is not None:
                row["line"] = line
            edges.append(row)

        # Register file nodes first; repository index already confined paths.
        for entry in indexed_files:
            path = entry["relative_path"]
            add_node({"id": file_id(path), "type": "file", "relative_path": path,
                      "language": entry["language"]})

        symbol_nodes: dict[tuple[str, str], str] = {}
        module_to_file: dict[str, str] = {}
        for entry in indexed_files:
            path = entry["relative_path"]
            if entry["module"]:
                module_to_file.setdefault(entry["module"], path)
            for s in entry["symbols"]:
                name = s["name"]
                if s["kind"] not in _SYMBOL_KIND:
                    continue
                sid = symbol_id(path, name)
                item = {"id": sid, "type": "symbol", "relative_path": path,
                        "qualified_name": name, "kind": s["kind"], "line": s["line"],
                        "language": entry["language"]}
                if add_node(item):
                    symbol_nodes[(path, name)] = sid
                    if "." in name and (path, name.split(".", 1)[0]) in symbol_nodes:
                        add_edge(symbol_nodes[(path, name.split(".", 1)[0])], sid, "declares")
                    else:
                        add_edge(file_id(path), sid, "declares")

        for edge in index["edges"]:
            add_edge(file_id(edge["source"]), file_id(edge["target"]), "imports")

        # Python-specific, conservative symbol-level relationships. The source
        # is read again via the approved manager, and its hash must match the
        # repository index snapshot, preventing stale cross-version links.
        workspace = self.repository_index.workspace
        for entry in indexed_files:
            if entry["language"] != "python":
                continue
            path = entry["relative_path"]
            try:
                source = workspace.read_text(path)
                if sha256(source.encode("utf-8")).hexdigest() != entry["source_text_sha256"]:
                    changed_sources.append(path)
                    continue
                tree = ast.parse(source, filename=path)
            except (NexusWorkspaceError, SyntaxError, ValueError, OSError, RecursionError):
                unsupported_python.append(path)
                continue
            module_id = entry["module"] or ""
            is_package = PurePosixPath(path).stem == "__init__"
            imports: dict[str, tuple[str, str]] = {}
            for stmt in tree.body:
                if isinstance(stmt, ast.Import):
                    for alias in stmt.names:
                        bound = alias.asname or alias.name.split(".", 1)[0]
                        resolved = alias.name if alias.asname else bound
                        imports[bound] = ("module", resolved)
                elif isinstance(stmt, ast.ImportFrom):
                    base = _relative_module(module_id, is_package, stmt.level, stmt.module or "")
                    for alias in stmt.names:
                        if alias.name == "*":
                            continue  # Never invent symbol links from wildcard imports.
                        binding = alias.asname or alias.name
                        candidate = f"{base}.{alias.name}" if base else alias.name
                        if candidate in module_to_file:
                            imports[binding] = ("module", candidate)
                        elif (module_to_file.get(base), alias.name) in symbol_nodes:
                            imports[binding] = ("symbol", symbol_nodes[(module_to_file[base], alias.name)])

            def resolve(expr: ast.expr, class_name: str | None, shadowed: set[str]) -> str | None:
                parts = _attribute_parts(expr)
                if not parts:
                    return None
                first = parts[0]
                if first in {"self", "cls"} and class_name and len(parts) == 2:
                    return symbol_nodes.get((path, f"{class_name}.{parts[1]}"))
                if first in shadowed:
                    return None
                if len(parts) == 1:
                    local = symbol_nodes.get((path, first))
                    if local:
                        return local
                    imported = imports.get(first)
                    return imported[1] if imported and imported[0] == "symbol" else None
                # Class.method(), including a class imported explicitly.
                if len(parts) == 2:
                    local_class = symbol_nodes.get((path, first))
                    if local_class and node_map[local_class]["kind"] == "class":
                        return symbol_nodes.get((path, f"{first}.{parts[1]}"))
                    imported = imports.get(first)
                    if imported and imported[0] == "symbol":
                        target_node = node_map.get(imported[1])
                        if target_node and target_node.get("kind") == "class":
                            return symbol_nodes.get((target_node["relative_path"], f"{target_node['qualified_name']}.{parts[1]}"))
                # Explicitly imported modules, e.g. 'from . import planner as p'.
                imported = imports.get(first)
                if imported and imported[0] == "module":
                    module_name = imported[1]
                    if len(parts) > 2:
                        module_name = ".".join([module_name, *parts[1:-1]])
                    target_path = module_to_file.get(module_name)
                    if target_path:
                        return symbol_nodes.get((target_path, parts[-1]))
                return None

            used_calls = 0
            for item in tree.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    definitions: Iterable[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, str | None]] = ((item, item.name, None),)
                elif isinstance(item, ast.ClassDef):
                    class_symbol = symbol_nodes.get((path, item.name))
                    if class_symbol:
                        for base in item.bases:
                            target = resolve(base, None, set())
                            if target and target != class_symbol and node_map[target].get("kind") == "class":
                                add_edge(class_symbol, target, "inherits", getattr(base, "lineno", item.lineno))
                    definitions = ((member, f"{item.name}.{member.name}", item.name)
                                   for member in item.body if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)))
                else:
                    continue
                for definition, qualified_name, owning_class in definitions:
                    caller = symbol_nodes.get((path, qualified_name))
                    if not caller:
                        continue
                    remaining = self.limits.max_calls_per_file - used_calls
                    if remaining <= 0:
                        warnings.append(f"call scan capped: {path}")
                        break
                    calls = _collect_calls(definition, remaining)
                    used_calls += len(calls.calls)
                    shadows = _shadowed_names(definition)
                    for call in calls.calls:
                        callee = resolve(call.func, owning_class, shadows)
                        if callee:
                            add_edge(caller, callee, "calls", call.lineno)
                    if calls.truncated:
                        warnings.append(f"call scan capped: {path}")
                        break

        complete = bool(index["complete"] and not (nodes_limited or edges_limited or warnings or changed_sources or unsupported_python))
        if nodes_limited:
            warnings.append("graph node limit reached")
        if edges_limited:
            warnings.append("graph edge limit reached")
        if changed_sources:
            warnings.append("source changed since index snapshot; some symbol relationships omitted")
        if unsupported_python:
            warnings.append("some Python files could not be parsed during symbol analysis")
        result = {
            "schema": GRAPH_SCHEMA,
            "root": index["root"],
            "scope": index["scope"],
            "complete": complete,
            "index_complete": bool(index["complete"]),
            "nodes_count": len(nodes),
            "edges_count": len(edges),
            "symbol_count": len(symbol_nodes),
            "files_count": sum(n["type"] == "file" for n in nodes),
            "edge_counts": {kind: sum(edge["kind"] == kind for edge in edges) for kind in sorted(_EDGE_KINDS)},
            "warnings": warnings,
            "changed_sources": changed_sources,
            "unsupported_python": unsupported_python,
            "nodes": nodes,
            "edges": edges,
            "semantics": {
                "calls": "statically resolvable potential calls, not runtime execution",
                "inherits": "statically resolvable Python base classes only",
                "imports": "repository-index local file dependencies",
                "javascript_typescript": "indexed declarations and file imports only; no symbol-level call resolution",
            },
        }
        self._last = result
        return deepcopy(result)

    def _snapshot(self) -> dict[str, Any]:
        if self._last is None:
            raise NexusSymbolGraphError("build() must be called before querying")
        return self._last

    def find_symbols(self, query: str) -> list[dict[str, Any]]:
        """Find definitions by case-insensitive qualified name substring."""
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise NexusSymbolGraphError("query must contain 1..200 characters")
        needle = query.strip().casefold()
        matches = [node for node in self._snapshot()["nodes"]
                   if node["type"] == "symbol" and needle in node["qualified_name"].casefold()]
        return deepcopy(matches[:self.limits.max_query_results])

    def relations(self, node_id: str, *, direction: str = "out", kinds: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Return outgoing/incoming edges for an existing graph node."""
        snapshot = self._snapshot()
        if not isinstance(node_id, str) or not any(node["id"] == node_id for node in snapshot["nodes"]):
            raise NexusSymbolGraphError("node ID not present in current graph")
        if direction not in ("in", "out"):
            raise NexusSymbolGraphError("direction must be 'in' or 'out'")
        accepted = set(_EDGE_KINDS) if kinds is None else set(kinds)
        if not accepted.issubset(_EDGE_KINDS):
            raise NexusSymbolGraphError("unsupported edge kind")
        match_field = "source" if direction == "out" else "target"
        found = [row for row in snapshot["edges"] if row[match_field] == node_id and row["kind"] in accepted]
        return deepcopy(found[:self.limits.max_query_results])

    def callers(self, symbol: str) -> list[dict[str, Any]]:
        """Static call sites into a symbol ID; does not imply a runtime call."""
        return self.relations(symbol, direction="in", kinds=("calls",))

    def callees(self, symbol: str) -> list[dict[str, Any]]:
        """Statically resolved outbound call sites from a symbol ID."""
        return self.relations(symbol, kinds=("calls",))

    def affected_files(self, relative_path: str, *, max_depth: int = 3) -> dict[str, Any]:
        """Conservative reverse-import transitive impact for a file.

        Does NOT claim that all affected modules necessarily fail or change.
        Returns at most max_query_results affected files and marks truncation.
        """
        if type(max_depth) is not int or not 1 <= max_depth <= self.limits.max_impact_depth:
            raise NexusSymbolGraphError("invalid max_depth")
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise NexusSymbolGraphError("relative_path must be a nonempty string")
        fid = file_id(relative_path.replace("\\", "/"))
        snapshot = self._snapshot()
        if fid not in {n["id"] for n in snapshot["nodes"] if n["type"] == "file"}:
            raise NexusSymbolGraphError("file is not in current graph")
        reverse: dict[str, set[str]] = {}
        for edge in snapshot["edges"]:
            if edge["kind"] == "imports":
                reverse.setdefault(edge["target"], set()).add(edge["source"])
        visited = {fid}
        discovered: list[dict[str, Any]] = []
        queue = deque([(fid, 0)])
        truncated = False
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for dependent in sorted(reverse.get(current, ())):
                if dependent in visited:
                    continue
                visited.add(dependent)
                if len(discovered) >= self.limits.max_query_results:
                    truncated = True
                    queue.clear()
                    break
                discovered.append({"relative_path": dependent.removeprefix("file:"), "distance": depth + 1})
                queue.append((dependent, depth + 1))
        return {"changed_file": relative_path.replace("\\", "/"), "affected": discovered,
                "truncated": truncated, "graph_complete": snapshot["complete"]}


__all__ = ["GRAPH_SCHEMA", "NexusSymbolGraphError", "SymbolGraphLimits",
           "NexusSymbolGraph", "file_id", "symbol_id"]
