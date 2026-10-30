"""Read-only, mission-scoped static discovery of regression tests for Colossus.

Target: src/aegisforge/nexus/engineering/test_discovery.py

Consumes optional *hints* from NexusChangeImpactAnalyzer or explicit changed-file
paths and discovers existing Python/JavaScript/TypeScript test definitions via
NexusWorkspacePolicy. Test code is NEVER imported, executed, modified, or sent to
an LLM. Results are candidate tests, not proof of coverage or a run command.

Authorizations must originate from a trusted host, not model-generated rules.
The policy must allow `inventory` for each test scope and `read_text` for its
files. Inputs/impact digests are consistency checks, NOT authenticity proofs.
Test name and static import analysis cannot establish actual test collection:
pytest plugins, parameterization, aliases and dynamic discovery may differ.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence

from ..workspace.workspace_policy import NexusWorkspacePolicy
from .change_impact import IMPACT_SCHEMA

DISCOVERY_SCHEMA = "aegisforge.nexus.engineering.test_discovery.v1"
_PY_TEST = re.compile(r"(?:^test_[^/]+|[^/]+_test)\.py$")
_JS_TEST = re.compile(r"(?:(?:^test_[^/]+)|(?:[^/]+\.(?:test|spec)))\.(?:js|jsx|ts|tsx|mjs|cjs)$")
_JS_DECL = re.compile(r"(?<![\w$.])(?:test|it|describe)\s*\(\s*(['\"`])")
_JS_IMPORT = re.compile(r"\b(?:require\s*\(\s*|from\s+|import\s*\(\s*)['\"]([^'\"\r\n]{1,250})['\"]")
_HEX = re.compile(r"^[a-f0-9]{64}$")
_MISSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TEST_SUFFIXES = frozenset({".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})


class NexusTestDiscoveryError(ValueError):
    """Malformed discovery input, incompatible workspace or untrusted impact hint."""


@dataclass(frozen=True, slots=True)
class TestDiscoveryLimits:
    max_scopes: int = 8
    max_test_files: int = 160
    max_total_read_bytes: int = 2_000_000
    max_cases_per_file: int = 200
    max_selected_tests: int = 80
    max_imports_per_file: int = 120
    max_source_hints: int = 200

    def __post_init__(self) -> None:
        ceilings = {
            "max_scopes": 32, "max_test_files": 2000,
            "max_total_read_bytes": 25_000_000, "max_cases_per_file": 2000,
            "max_selected_tests": 1000, "max_imports_per_file": 2000,
            "max_source_hints": 1000,
        }
        for field, upper in ceilings.items():
            value = getattr(self, field)
            if type(value) is not int or not 1 <= value <= upper:
                raise NexusTestDiscoveryError(f"{field} must be an integer from 1 to {upper}")


def _relative(path: str) -> str:
    """Validate, do NOT sanitize away traversal or absolute paths."""
    if not isinstance(path, str) or not path.strip():
        raise NexusTestDiscoveryError("invalid relative path")
    raw = path.replace("\\", "/")
    if raw == ".":
        return raw
    if (raw.startswith("/") or raw.endswith("/") or ":" in raw or "\x00" in raw
            or any(part in ("", ".", "..") for part in raw.split("/"))):
        raise NexusTestDiscoveryError("unsafe repository-relative path")
    return PurePosixPath(raw).as_posix()


def _canonical_digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _targets_from_impact(impact: Mapping[str, Any], mission_id: str,
                         max_hints: int) -> tuple[set[str], set[str], dict[str, str], list[str]]:
    """Validate report consistency; its content is still untrusted ranking data."""
    if not isinstance(impact, Mapping) or impact.get("schema") != IMPACT_SCHEMA:
        raise NexusTestDiscoveryError("impact report schema not recognized")
    if impact.get("mission_id") != mission_id:
        raise NexusTestDiscoveryError("impact report belongs to a different mission")
    digest = impact.get("report_sha256")
    if not isinstance(digest, str) or not _HEX.fullmatch(digest):
        raise NexusTestDiscoveryError("impact report has no digest")
    document = dict(impact)
    del document["report_sha256"]
    if _canonical_digest(document) != digest:
        raise NexusTestDiscoveryError("impact report digest mismatch")
    patch_hash = impact.get("patchset_sha256")
    if not isinstance(patch_hash, str) or not _HEX.fullmatch(patch_hash):
        raise NexusTestDiscoveryError("impact report lacks patch identity")
    if impact.get("status") not in ("analyzed", "partial"):
        raise NexusTestDiscoveryError("impact report has unsupported status")
    files: set[str] = set()
    downstream: set[str] = set()
    candidates: dict[str, str] = {}
    for field, target in (("changed_files", files), ("downstream_files", downstream)):
        rows = impact.get(field)
        if not isinstance(rows, list) or len(rows) > max_hints:
            raise NexusTestDiscoveryError("impact report contains too many or invalid file hints")
        for row in rows:
            if not isinstance(row, dict):
                raise NexusTestDiscoveryError("malformed impact file hint")
            target.add(_relative(row.get("relative_path")))
    rows = impact.get("candidate_tests")
    if not isinstance(rows, list) or len(rows) > max_hints:
        raise NexusTestDiscoveryError("impact report contains invalid test hints")
    for row in rows:
        if not isinstance(row, dict):
            raise NexusTestDiscoveryError("malformed candidate test hint")
        path = _relative(row.get("relative_path"))
        candidates[path] = "impact_graph_candidate"
    warnings: list[str] = []
    if impact["status"] == "partial" or impact.get("graph_complete") is False:
        warnings.append("Impact graph was partial; its test hints may omit dependencies")
    return files, downstream, candidates, warnings


def _is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    if p.suffix.lower() not in _TEST_SUFFIXES:
        return False
    name = p.name.lower()
    return bool(_PY_TEST.fullmatch(name) if p.suffix.lower() == ".py" else _JS_TEST.fullmatch(name))


def _python_cases_and_imports(source: str, max_cases: int, max_imports: int) -> tuple[list[dict[str, Any]], list[str], bool, bool]:
    tree = ast.parse(source)
    cases: list[dict[str, Any]] = []
    imported: set[str] = set()
    truncated_cases = truncated_imports = False

    def register(name: str, node: ast.AST, *, style: str) -> None:
        nonlocal truncated_cases
        if len(cases) >= max_cases:
            truncated_cases = True
            return
        decorators = getattr(node, "decorator_list", ())
        param = any(
            (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and
             d.func.attr == "parametrize")
            or (isinstance(d, ast.Attribute) and d.attr == "parametrize")
            for d in decorators
        )
        cases.append({"qualified_name": name, "line": node.lineno,
                      "style": style, "parameterized": bool(param)})

    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name.startswith("test_"):
            register(statement.name, statement, style="pytest_function")
        elif isinstance(statement, ast.ClassDef):
            is_test_class = statement.name.startswith("Test") or any(
                (isinstance(base, ast.Name) and base.id == "TestCase") or
                (isinstance(base, ast.Attribute) and base.attr == "TestCase")
                for base in statement.bases
            )
            if not is_test_class:
                continue
            for method in statement.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test_"):
                    register(f"{statement.name}.{method.name}", method, style="class_method")

    for statement in ast.walk(tree):
        names: list[str] = []
        if isinstance(statement, ast.Import):
            names = [alias.name for alias in statement.names]
        elif isinstance(statement, ast.ImportFrom):
            # Relative imports have resolution context dependent on package.
            base = "." * statement.level + (statement.module or "")
            names = [base]
            if statement.module:
                names.extend(base + "." + alias.name for alias in statement.names)
        for name in names:
            if name and len(imported) < max_imports:
                imported.add(name)
            elif name:
                truncated_imports = True
    return cases, sorted(imported), truncated_cases, truncated_imports


def _javascript_cases_and_imports(source: str, max_cases: int, max_imports: int) -> tuple[list[dict[str, Any]], list[str], bool, bool]:
    """Conservative lexical hints; may match comments/strings and omit dynamic tests."""
    cases: list[dict[str, Any]] = []
    truncated_cases = truncated_imports = False
    for match in _JS_DECL.finditer(source):
        if len(cases) >= max_cases:
            truncated_cases = True
            break
        line = source.count("\n", 0, match.start()) + 1
        cases.append({"qualified_name": f"static_candidate_at_line_{line}",
                      "line": line, "style": "js_heuristic", "parameterized": False})
    imported: set[str] = set()
    for match in _JS_IMPORT.finditer(source):
        if len(imported) >= max_imports:
            truncated_imports = True
            break
        imported.add(match.group(1))
    return cases, sorted(imported), truncated_cases, truncated_imports


def _source_modules(paths: set[str]) -> set[str]:
    modules: set[str] = set()
    for path in paths:
        p = PurePosixPath(path)
        if p.suffix.lower() in (".py", ".pyi"):
            parts = list(p.with_suffix("").parts)
            if parts and parts[0] == "src":
                parts.pop(0)
            if parts and parts[-1] == "__init__":
                parts.pop()
            if parts:
                modules.add(".".join(parts))
    return modules


def _name_keys(paths: set[str]) -> set[str]:
    keys: set[str] = set()
    for path in paths:
        p = PurePosixPath(path)
        name = p.stem.lower()
        for token in (name, p.parent.name.lower()):
            if len(token) >= 4 and token not in {"nexus", "aegisforge", "source", "tests", "src", "test"}:
                keys.add(token)
    return keys


def _ranking(path: str, imports: Sequence[str], changed: set[str], downstream: set[str],
             candidates: Mapping[str, str]) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0
    if path in candidates:
        score = max(score, 100)
        reasons.append(candidates[path])
    if path in changed:
        score = max(score, 100)
        reasons.append("modified_test_file")
    p = PurePosixPath(path)
    normalized = p.stem.lower().removeprefix("test_").removesuffix("_test")
    all_source = changed | downstream
    names = _name_keys(changed)
    secondary_names = _name_keys(downstream)
    if normalized in names:
        score = max(score, 85)
        reasons.append("filename_matches_changed_module")
    elif normalized in secondary_names:
        score = max(score, 60)
        reasons.append("filename_matches_downstream_module")
    modules = _source_modules(changed)
    downstream_modules = _source_modules(downstream)
    for imported in imports:
        if imported in modules or any(imported.startswith(mod + ".") for mod in modules):
            score = max(score, 90)
            reasons.append("static_import_of_changed_module")
            break
        if imported in downstream_modules or any(imported.startswith(mod + ".") for mod in downstream_modules):
            score = max(score, 65)
            reasons.append("static_import_of_downstream_module")
            break
        # JS relative import paths are only a weak heuristic, never a proof.
        if imported.startswith(".") and any(
            imported.rsplit("/", 1)[-1].split(".")[0].lower() == PurePosixPath(source).stem.lower()
            for source in all_source
        ):
            score = max(score, 55)
            reasons.append("heuristic_js_relative_import_name")
            break
    return score, sorted(set(reasons))


class NexusTestDiscovery:
    """Discover test candidates through the policy facade; never execute tests.

    A trusted host supplies the policy and mission scopes. Optional impact data
    only influences ranking: even a well-formed digest is not an authorization.
    """

    def __init__(self, policy: NexusWorkspacePolicy, *, limits: TestDiscoveryLimits | None = None) -> None:
        if not isinstance(policy, NexusWorkspacePolicy):
            raise NexusTestDiscoveryError("policy must be NexusWorkspacePolicy")
        self.policy = policy
        self.limits = limits if limits is not None else TestDiscoveryLimits()
        if not isinstance(self.limits, TestDiscoveryLimits):
            raise NexusTestDiscoveryError("limits must be TestDiscoveryLimits")

    def discover(
        self,
        mission_id: str,
        *,
        test_scopes: Sequence[str] = ("tests",),
        changed_files: Sequence[str] = (),
        impact_report: Mapping[str, Any] | None = None,
        include_unlinked: bool = False,
    ) -> dict[str, Any]:
        """Return bounded candidate files and test names, without performing a test run.

        Must have `inventory` on each test scope and `read_text` on every test
        file discovered. Scanning broad scopes risks hitting inventory caps;
        prefer dedicated test directories (e.g. `tests/test_nexus`).
        """
        if not isinstance(mission_id, str) or not _MISSION.fullmatch(mission_id):
            raise NexusTestDiscoveryError("invalid mission ID")
        if type(include_unlinked) is not bool:
            raise NexusTestDiscoveryError("include_unlinked must be boolean")
        if (not isinstance(test_scopes, (tuple, list)) or isinstance(test_scopes, str)
                or not 1 <= len(test_scopes) <= self.limits.max_scopes):
            raise NexusTestDiscoveryError("invalid test scope list")
        scopes = [_relative(path) for path in test_scopes]
        if len(set(scopes)) != len(scopes):
            raise NexusTestDiscoveryError("duplicate test scopes")
        if (not isinstance(changed_files, (tuple, list)) or isinstance(changed_files, str)
                or len(changed_files) > self.limits.max_source_hints):
            raise NexusTestDiscoveryError("invalid changed files list")
        changed = {_relative(path) for path in changed_files}
        downstream: set[str] = set()
        candidates: dict[str, str] = {}
        warnings: list[str] = []
        impact_link: dict[str, str] | None = None
        if impact_report is not None:
            extra_changed, downstream, candidates, impact_warnings = _targets_from_impact(
                impact_report, mission_id, self.limits.max_source_hints)
            changed.update(extra_changed)
            warnings.extend(impact_warnings)
            impact_link = {"report_sha256": impact_report["report_sha256"],
                           "patchset_sha256": impact_report["patchset_sha256"]}
        if not changed and not candidates and not include_unlinked:
            warnings.append("No changed files/impact hints provided; no tests can be ranked")

        # Validate ALL inventory permissions before gathering any results.
        # File-level read permissions will be checked per file, fail-closed.
        for scope in scopes:
            self.policy.require(mission_id, "inventory", scope)

        file_rows: dict[str, dict[str, Any]] = {}
        partial = False
        for scope in scopes:
            scan = self.policy.inventory(mission_id, scope, with_hashes=False)
            warnings.extend(scan.get("warnings", ()))
            partial = partial or bool(scan.get("truncated"))
            for item in scan["files"]:
                path = _relative(item["relative_path"])
                if _is_test_file(path):
                    file_rows[path] = item
        paths = sorted(file_rows)
        if len(paths) > self.limits.max_test_files:
            partial = True
            warnings.append("Test file count exceeded discovery limit")
            paths = paths[:self.limits.max_test_files]
        processed_bytes = 0
        skipped: list[dict[str, str]] = []
        known: list[dict[str, Any]] = []
        for path in paths:
            info = file_rows[path]
            size = info.get("size_bytes")
            if type(size) is not int or size < 0:
                partial = True
                skipped.append({"relative_path": path, "reason": "invalid_file_size"})
                continue
            # NexusWorkspaceManager.read_text() truncates text silently at its
            # char limit; by requiring bytes <= chars we avoid partial parsing.
            if size > self.policy.workspace.limits.max_text_chars or size > self.policy.workspace.limits.max_file_bytes:
                partial = True
                skipped.append({"relative_path": path, "reason": "file_exceeds_read_limit"})
                continue
            if size + processed_bytes > self.limits.max_total_read_bytes:
                partial = True
                skipped.append({"relative_path": path, "reason": "discovery_read_budget"})
                continue
            # Every test read is gated separately; never bypass with workspace._backend.
            self.policy.require(mission_id, "read_text", path)
            try:
                content = self.policy.read_text(mission_id, path)
                # Report a changed file, not a misleading complete parse.
                if not size - 3 <= len(content.encode("utf-8")) <= size:
                    raise NexusTestDiscoveryError("test file changed during discovery")
                if path.endswith(".py"):
                    cases, imports, c_truncated, i_truncated = _python_cases_and_imports(
                        content, self.limits.max_cases_per_file, self.limits.max_imports_per_file)
                    framework = "python_static"
                else:
                    cases, imports, c_truncated, i_truncated = _javascript_cases_and_imports(
                        content, self.limits.max_cases_per_file, self.limits.max_imports_per_file)
                    framework = "js_ts_heuristic"
            except (SyntaxError, RecursionError, UnicodeError, ValueError, OSError) as exc:
                partial = True
                skipped.append({"relative_path": path, "reason": "unparseable_or_unstable_source"})
                continue
            processed_bytes += size
            if c_truncated or i_truncated:
                partial = True
                warnings.append("Some test definitions or imports reached per-file limits")
            score, reasons = _ranking(path, imports, changed, downstream, candidates)
            known.append({
                "relative_path": path,
                "framework": framework,
                "static_cases": cases,
                "static_case_count": len(cases),
                "imports_count": len(imports),
                "source_sha256": sha256(content.encode("utf-8")).hexdigest(),
                "score": score,
                "reasons": reasons,
                "heuristic": framework != "python_static",
            })
        ranked = [t for t in known if t["score"] > 0 or include_unlinked]
        ranked.sort(key=lambda t: (-t["score"], t["relative_path"]))
        if len(ranked) > self.limits.max_selected_tests:
            partial = True
            warnings.append("Selected test results exceeded output limit")
            ranked = ranked[:self.limits.max_selected_tests]
        if not ranked:
            warnings.append("No candidate test files identified in the authorized scopes")
        if not include_unlinked:
            warnings.append("Unlinked tests were not selected; they may still be required for regression")
        warnings.append("Static discovery does not run test collectors or prove coverage")
        result = {
            "schema": DISCOVERY_SCHEMA,
            "mission_id": mission_id,
            "status": "partial" if partial else "discovered",
            "test_scopes": scopes,
            "changed_files": sorted(changed),
            "impact_link": impact_link,
            "discovered_test_files": len(file_rows),
            "analyzed_test_files": len(known),
            "static_cases_found": sum(t["static_case_count"] for t in known),
            "candidate_test_files": len(ranked),
            "selected_tests": ranked,
            "unlinked_files_count": sum(t["score"] == 0 for t in known),
            "skipped_files": skipped,
            "bytes_read": processed_bytes,
            "complete_inventory": not partial,
            "executed": False,
            "applied": False,
            "warnings": list(dict.fromkeys(warnings)),
            "semantics": {
                "static_cases": "names visible to static analysis, not verified collector IDs",
                "selection": "heuristic candidates for human review, not guaranteed coverage",
                "impact": "impact hints affect ranking only; do not confer permissions",
                "execution": "no code imports, test processes, tool use, or writes",
            },
        }
        result["catalog_sha256"] = _canonical_digest(result)
        return result

__all__ = ["DISCOVERY_SCHEMA", "NexusTestDiscoveryError", "TestDiscoveryLimits", "NexusTestDiscovery"]
