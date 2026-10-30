"""Conservative, read-only change impact assessment for AegisForge Nexus.

Target: src/aegisforge/nexus/engineering/change_impact.py

Consumes a freshly validated NexusDiffBuilder PatchBundle and a mission-scoped
NexusSymbolGraph snapshot to estimate which definitions, modules, and indexed
tests *might* be affected. Never applies patches, executes code, calls a model,
or writes files. Every graph scan must be separately allowed by WorkspacePolicy.

A static import/call graph cannot prove absence of runtime effects. Partial
scans, dynamic dispatch, unmatched lines, and tests outside the scanned scope
are reported as uncertainty; do not treat a small impact list as approval.
"""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Any

from .code_proposer import CodeProposal, ProposalContext
from .diff_builder import NexusDiffBuilder, PatchBundle
from ..workspace.symbol_graph import NexusSymbolGraph, file_id, symbol_id
from ..workspace.workspace_policy import NexusWorkspacePolicy

IMPACT_SCHEMA = "aegisforge.nexus.engineering.change_impact.v1"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:\r?\n)$")
_PYTHON = frozenset({".py", ".pyi"})


class NexusChangeImpactError(ValueError):
    """A patch, scope, snapshot, or mission binding is not fit for assessment."""


@dataclass(frozen=True, slots=True)
class ChangeImpactLimits:
    max_depth: int = 4
    max_affected_files: int = 160
    max_affected_symbols: int = 250
    max_test_candidates: int = 60
    max_changed_lines_per_file: int = 3500

    def __post_init__(self) -> None:
        for key in self.__dataclass_fields__:
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise NexusChangeImpactError(f"{key} must be a positive integer")
        if self.max_depth > 8 or self.max_affected_files > 1000 or self.max_affected_symbols > 3000:
            raise NexusChangeImpactError("impact limits exceed supported maximums")


def _in_scope(path: str, scope: str) -> bool:
    if scope == ".":
        return True
    return path == scope or path.startswith(scope + "/")


def _strict_scope(scope: str) -> str:
    if not isinstance(scope, str) or not scope.strip():
        raise NexusChangeImpactError("scope must be a repository-relative directory")
    path = scope.replace("\\", "/")
    if path == ".":
        return path
    if (path.startswith("/") or path.endswith("/") or ":" in path
            or any(p in ("", ".", "..") for p in path.split("/"))):
        raise NexusChangeImpactError("invalid graph scope")
    return path


def _sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _changed_old_lines(diff: str, cap: int) -> set[int]:
    """Extract edited *old-source* line numbers without counting hunk context.

    Inserts are anchored to the nearest old line, not falsely attributed to
    every surrounding context line. Diff syntax was already verified by the
    upstream NexusDiffBuilder; this is a second defensive shape check.
    """
    lines = diff.splitlines(keepends=True)
    if len(lines) < 4 or not lines[0].startswith("--- a/") or not lines[1].startswith("+++ b/"):
        raise NexusChangeImpactError("unexpected unified diff format")
    old: set[int] = set()
    i = 2
    while i < len(lines):
        match = _HUNK.fullmatch(lines[i])
        if match is None:
            raise NexusChangeImpactError("malformed diff hunk")
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        old_line = old_start if old_count else old_start + 1
        consumed = produced = 0
        i += 1
        while i < len(lines) and not lines[i].startswith("@@ "):
            row = lines[i]
            if not row or row[0] not in " +-":
                raise NexusChangeImpactError("unexpected diff line")
            if row[0] == "-":
                old.add(max(1, old_line))
                old_line += 1
                consumed += 1
            elif row[0] == "+":
                old.add(max(1, old_line - 1))
                produced += 1
            else:
                old_line += 1
                consumed += 1
                produced += 1
            if len(old) > cap:
                raise NexusChangeImpactError("too many changed lines for impact analysis")
            i += 1
        if consumed != old_count or produced != new_count:
            raise NexusChangeImpactError("diff hunk accounting disagrees")
    if not old:
        raise NexusChangeImpactError("diff has no effective changes")
    return old


def _definitions(source: str, path: str) -> list[tuple[str, int, int]]:
    """Only names covered by the graph's top-level functions and class methods."""
    if PurePosixPath(path).suffix.lower() not in _PYTHON:
        return []
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise NexusChangeImpactError("captured Python source cannot be parsed") from exc
    result: list[tuple[str, int, int]] = []

    def span(node: ast.AST) -> tuple[int, int]:
        decorators = getattr(node, "decorator_list", ())
        first = min((d.lineno for d in decorators), default=node.lineno)
        return first, getattr(node, "end_lineno", node.lineno)

    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            a, b = span(item)
            result.append((item.name, a, b))
            if isinstance(item, ast.ClassDef):
                for member in item.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        x, y = span(member)
                        result.append((f"{item.name}.{member.name}", x, y))
    return result


def _test_path(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name.lower()
    return ("tests" in [segment.lower() for segment in p.parts]
            or name.startswith("test_") or name.endswith("_test.py")
            or name.endswith((".test.js", ".spec.js", ".test.ts", ".spec.ts",
                              ".test.tsx", ".spec.tsx", ".test.jsx", ".spec.jsx")))


class NexusChangeImpactAnalyzer:
    """Estimate downstream change impact, always preserving explicit uncertainty.

    Both patch verification and graph scanning use the trusted host's existing
    proposer/workspace policy. The symbol graph MUST share the same manager as
    the proposer's policy; a graph constructed from a different workspace is
    refused. Scans occur only through policy.build_symbol_graph().

    Analysis never grants approval, proves safety, or recommends executing
    arbitrary generated commands. All reported impacts are potential impacts.
    """

    def __init__(
        self,
        diff_builder: NexusDiffBuilder,
        symbol_graph: NexusSymbolGraph,
        *,
        limits: ChangeImpactLimits | None = None,
    ) -> None:
        if not isinstance(diff_builder, NexusDiffBuilder):
            raise NexusChangeImpactError("diff_builder must be a NexusDiffBuilder")
        if not isinstance(symbol_graph, NexusSymbolGraph):
            raise NexusChangeImpactError("symbol_graph must be a NexusSymbolGraph")
        policy = diff_builder.proposer.policy
        if not isinstance(policy, NexusWorkspacePolicy) or symbol_graph.repository_index.workspace is not policy.workspace:
            raise NexusChangeImpactError("symbol graph and proposer must share a workspace")
        self.diff_builder = diff_builder
        self.symbol_graph = symbol_graph
        self.policy = policy
        self.limits = limits if limits is not None else ChangeImpactLimits()
        if not isinstance(self.limits, ChangeImpactLimits):
            raise NexusChangeImpactError("limits must be ChangeImpactLimits")

    def analyze(
        self,
        context: ProposalContext,
        proposal: CodeProposal,
        bundle: PatchBundle,
        *,
        scope: str = ".",
        require_fresh: bool = True,
    ) -> dict[str, Any]:
        """Produce a bounded, evidence-linked JSON-compatible impact report.

        ``scope`` should include changed modules, their possible consumers,
        and relevant tests. Partial graphs are clearly flagged. No I/O beyond
        mission-authorized reads by the existing workspace adapters.
        """
        selected = _strict_scope(scope)
        if type(require_fresh) is not bool:
            raise NexusChangeImpactError("require_fresh must be boolean")
        # Recheck exact proposal/patch contents via the upstream verifier.
        self.diff_builder.verify(bundle, context, proposal, require_fresh=require_fresh)
        if not bundle.files:
            raise NexusChangeImpactError("empty bundle")
        for patched in bundle.files:
            if not _in_scope(patched.relative_path, selected):
                raise NexusChangeImpactError("patched file is outside graph scope")

        # The policy gate is *required*: direct symbol_graph.build() would
        # otherwise bypass the mission's graph-scan permission check.
        graph = self.policy.build_symbol_graph(context.mission_id, self.symbol_graph, selected)
        index = self.symbol_graph.repository_index._snapshot()
        indexed = {entry["relative_path"]: entry for entry in index["files"]}
        available_ids = {node["id"] for node in graph["nodes"]}
        symbol_meta = {node["id"]: node for node in graph["nodes"] if node["type"] == "symbol"}
        sources = {source.relative_path: source for source in context.sources}

        # Detect changes in the short interval between patch verification and
        # the graph snapshot. A missing source (e.g. size cap) is not acceptable
        # evidence that it has no dependencies.
        for patched in bundle.files:
            path = patched.relative_path
            entry = indexed.get(path)
            if entry is None or file_id(path) not in available_ids:
                raise NexusChangeImpactError("patched file absent from graph/index; expand scan or limits")
            if entry["source_text_sha256"] != patched.base_text_sha256:
                raise NexusChangeImpactError("indexed base does not match the patch")
            if _sha(self.policy.read_text(context.mission_id, path)) != patched.base_text_sha256:
                raise NexusChangeImpactError("source changed during impact assessment")

        direct_symbols: set[str] = set()
        module_scope: set[str] = set()
        file_changes: list[dict[str, Any]] = []
        for patched in bundle.files:
            path = patched.relative_path
            old_lines = _changed_old_lines(patched.unified_diff, self.limits.max_changed_lines_per_file)
            source = sources.get(path)
            if source is None or _sha(source._source) != patched.base_text_sha256:
                raise NexusChangeImpactError("captured source is unavailable or changed")
            definitions = _definitions(source._source, path)
            changed_defs: set[str] = set()
            unassigned = False
            for line in old_lines:
                containers = [(name, lo, hi) for name, lo, hi in definitions if lo <= line <= hi]
                if containers:
                    # Methods are more specific than their containing class.
                    name, _, _ = min(containers, key=lambda d: (d[2] - d[1], -d[1]))
                    sid = symbol_id(path, name)
                    if sid in symbol_meta:
                        direct_symbols.add(sid)
                        changed_defs.add(name)
                    else:
                        unassigned = True
                else:
                    unassigned = True
            if unassigned:
                module_scope.add(path)
            file_changes.append({
                "relative_path": path,
                "edited_old_line_count": len(old_lines),
                "changed_definitions": sorted(changed_defs),
                "module_scope_or_unresolved": unassigned,
                "additions": patched.additions,
                "deletions": patched.deletions,
            })

        # Reverse directed imports: A -> B means 'A imports B'. Reverse
        # calls/inherits likewise find potential callers/derived classes.
        importers: dict[str, set[str]] = {}
        callers: dict[str, set[str]] = {}
        for edge in graph["edges"]:
            if edge["kind"] == "imports":
                importers.setdefault(edge["target"], set()).add(edge["source"])
            elif edge["kind"] in ("calls", "inherits"):
                callers.setdefault(edge["target"], set()).add(edge["source"])

        affected: dict[str, tuple[int, str]] = {}
        queue = deque((file_id(row["relative_path"]), 0) for row in file_changes)
        visited = {file_id(row["relative_path"]) for row in file_changes}
        files_limited = False
        while queue:
            node, distance = queue.popleft()
            if distance >= self.limits.max_depth:
                continue
            for importer in sorted(importers.get(node, ())):
                if importer in visited or importer not in available_ids:
                    continue
                if len(affected) >= self.limits.max_affected_files:
                    files_limited = True
                    break
                visited.add(importer)
                path = importer.removeprefix("file:")
                affected[path] = (distance + 1, "reverse_import")
                queue.append((importer, distance + 1))
            if files_limited:
                break

        touched_symbols: dict[str, int] = {sid: 0 for sid in direct_symbols}
        symbols_limited = False
        queue_symbols = deque((sid, 0) for sid in sorted(direct_symbols))
        while queue_symbols:
            node, depth = queue_symbols.popleft()
            if depth >= self.limits.max_depth:
                continue
            for caller in sorted(callers.get(node, ())):
                if caller in touched_symbols or caller not in symbol_meta:
                    continue
                if len(touched_symbols) >= self.limits.max_affected_symbols:
                    symbols_limited = True
                    break
                touched_symbols[caller] = depth + 1
                queue_symbols.append((caller, depth + 1))
                owner_path = symbol_meta[caller]["relative_path"]
                if owner_path not in {item["relative_path"] for item in file_changes}:
                    previous = affected.get(owner_path)
                    if previous is None or depth + 1 < previous[0]:
                        if owner_path in affected or len(affected) < self.limits.max_affected_files:
                            affected[owner_path] = (depth + 1, "reverse_symbol_relation")
                        else:
                            files_limited = True
            if symbols_limited:
                break

        test_candidates: list[dict[str, Any]] = []
        tests_limited = False
        changed_paths = {row["relative_path"] for row in file_changes}
        for path in sorted(affected):
            if _test_path(path):
                if len(test_candidates) >= self.limits.max_test_candidates:
                    tests_limited = True
                    break
                depth, basis = affected[path]
                test_candidates.append({"relative_path": path, "distance": depth, "reason": basis})
        for path in sorted(changed_paths):
            if _test_path(path) and all(t["relative_path"] != path for t in test_candidates):
                if len(test_candidates) >= self.limits.max_test_candidates:
                    tests_limited = True
                    break
                test_candidates.append({"relative_path": path, "distance": 0, "reason": "modified_test"})
        test_candidates.sort(key=lambda row: (row["distance"], row["relative_path"]))

        warnings = list(graph.get("warnings", ()))
        if not graph["complete"]:
            warnings.append("Graph/index scan is incomplete; omitted dependencies may exist")
        if module_scope:
            warnings.append("Some changes are outside indexed definitions or cannot be mapped to a symbol")
        if files_limited or symbols_limited or tests_limited:
            warnings.append("Impact result hit an output limit; there may be additional dependents")
        if selected != ".":
            warnings.append("Dependencies and tests outside the selected graph scope were not inspected")
        if not test_candidates:
            warnings.append("No linked tests were identified within the scanned graph; test coverage is unknown")
        warnings.append("Static analysis does not resolve dynamic dispatch, runtime imports or all JS/TS calls")
        complete = bool(graph["complete"] and not (files_limited or symbols_limited or tests_limited))
        total_downstream = len(affected)
        breadth = "broad" if total_downstream >= 6 else "moderate" if total_downstream >= 2 else "localized"
        if not complete:
            breadth = "undetermined"
        report = {
            "schema": IMPACT_SCHEMA,
            "mission_id": bundle.mission_id,
            "proposal_id": bundle.proposal_id,
            "patchset_sha256": bundle.patchset_sha256,
            "context_sha256": bundle.context_sha256,
            "status": "analyzed" if complete else "partial",
            "requires_review": True,
            "applied": False,
            "graph_scope": selected,
            "graph_complete": bool(graph["complete"]),
            "scope_limited": selected != ".",
            "impact_breadth": breadth,
            "changed_files": file_changes,
            "direct_changed_symbols": [
                {"id": sid, "relative_path": symbol_meta[sid]["relative_path"],
                 "qualified_name": symbol_meta[sid]["qualified_name"]}
                for sid in sorted(direct_symbols)
            ],
            "module_scope_changes": sorted(module_scope),
            "downstream_files": [
                {"relative_path": path, "distance": depth, "reason": reason}
                for path, (depth, reason) in sorted(affected.items(), key=lambda pair: (pair[1][0], pair[0]))
                if path not in changed_paths
            ],
            "downstream_symbols": [
                {"id": sid, "relative_path": symbol_meta[sid]["relative_path"],
                 "qualified_name": symbol_meta[sid]["qualified_name"], "distance": depth}
                for sid, depth in sorted(touched_symbols.items(), key=lambda pair: (pair[1], pair[0]))
                if depth > 0
            ],
            "candidate_tests": test_candidates,
            "test_coverage": "candidates_only" if test_candidates else "unknown",
            "warnings": list(dict.fromkeys(warnings)),
            "limits_reached": files_limited or symbols_limited or tests_limited,
            "semantics": {
                "downstream": "potentially impacted according to the current static graph",
                "candidate_tests": "indexed tests inferred by graph linkage; not executed",
                "breadth": "heuristic breadth of visible dependencies, not severity or safety",
            },
        }
        # Deterministic metadata-only identity, not a signature or an approval.
        serialized = json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        report["report_sha256"] = _sha(serialized)
        return report


__all__ = [
    "IMPACT_SCHEMA", "NexusChangeImpactError", "ChangeImpactLimits",
    "NexusChangeImpactAnalyzer",
]
