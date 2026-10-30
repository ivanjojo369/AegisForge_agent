"""Fail-closed, mission-scoped workspace policy for AegisForge Nexus — Colossus.

Target: src/aegisforge/nexus/workspace/workspace_policy.py

This file completes the first read-only Workspace layer. It provides:
- explicit per-mission, per-operation, path-prefix allowlists;
- a policy-gated facade for manager inventory/read, repository indexing and graphs;
- a *decision only* gate for mutating operations, which are OFF by default;
- optional trusted-host approval checks with single-use IDs per policy instance.

IMPORTANT SECURITY CONTRACT
---------------------------
Construct rules and the approval_verifier ONLY in trusted host code. NEVER
accept policy rules, mission IDs, approval callbacks or action IDs as authority
merely because an LLM generated them. This policy does not execute mutations,
run processes, open network connections, or grant OS permissions. The caller
must enforce these decisions at the action boundary, revalidate immediately
before each effect, and independently sandbox any executed code. The underlying
NexusWorkspaceManager and index/graph objects can be called directly by Python
code that holds references to them: only expose this facade to untrusted callers.

Uses NexusWorkspaceManager's path resolution (including its sensitive-path
exclusions), so TOCTOU, Windows ACLs, content secrecy, and hostile concurrent
filesystem modification remain the trusted host's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
from threading import Lock
from typing import Any, Callable, Iterable, TYPE_CHECKING

from .manager import NexusWorkspaceError, NexusWorkspaceManager

if TYPE_CHECKING:
    from .repo_index import NexusRepositoryIndex
    from .symbol_graph import NexusSymbolGraph


POLICY_SCHEMA = "aegisforge.nexus.workspace.policy.v1"
READ_OPERATIONS = frozenset({
    "inventory", "hash_files", "read_text", "index_repo", "build_symbol_graph",
})
IMPACTFUL_OPERATIONS = frozenset({
    "write_file", "delete_file", "apply_patch", "run_tests", "execute_tool",
})
ALL_OPERATIONS = READ_OPERATIONS | IMPACTFUL_OPERATIONS
_DIRECTORY_OPERATIONS = frozenset({
    "inventory", "hash_files", "index_repo", "build_symbol_graph",
})
_FILE_OPERATIONS = frozenset({"read_text", "delete_file", "apply_patch"})
_MISSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ACTION_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class NexusWorkspacePolicyError(ValueError):
    """Invalid policy configuration or attempted use of a denied operation."""


class NexusWorkspaceAccessDenied(NexusWorkspacePolicyError):
    """A workspace action was not authorized by the trusted host policy."""


def _valid_mission(value: str) -> bool:
    return isinstance(value, str) and bool(_MISSION_RE.fullmatch(value))


def _relative_scope(value: str) -> str:
    """Validate one lexical, repository-relative path without normalizing '..'."""
    if not isinstance(value, str) or not value.strip():
        raise NexusWorkspacePolicyError("scope/path must be a nonempty relative string")
    raw = value.replace("\\", "/")
    if raw == ".":
        return "."
    if (raw.startswith("/") or _DRIVE_RE.match(raw) or "\x00" in raw
            or raw.startswith("//") or raw.endswith("/")):
        raise NexusWorkspacePolicyError("absolute or invalid workspace path")
    # Do not permit empty, dot or parent components; no path cleansing.
    segments = raw.split("/")
    if any(segment in ("", ".", "..") or ":" in segment for segment in segments):
        raise NexusWorkspacePolicyError("unsafe workspace path")
    return PurePosixPath(*segments).as_posix()


def _is_within(path: str, scope: str) -> bool:
    """Compare whole path segments, not string prefixes."""
    if scope == ".":
        return True
    if os.name == "nt":
        path, scope = path.casefold(), scope.casefold()
    return path == scope or path.startswith(scope + "/")


@dataclass(frozen=True, slots=True)
class WorkspaceRule:
    """Trusted-host allow rule applying to exactly one mission.

    ``scope='.'`` grants a whole-repository scope, which is discouraged for
    untrusted missions. An operation authorizes the requested directory and
    ALL descendants, including source content for index/graph operations.
    """

    mission_id: str
    scope: str
    operations: frozenset[str]

    def __post_init__(self) -> None:
        if not _valid_mission(self.mission_id):
            raise NexusWorkspacePolicyError("invalid mission ID")
        normalized = _relative_scope(self.scope)
        if type(self.operations) is not frozenset or not self.operations:
            raise NexusWorkspacePolicyError("operations must be a nonempty frozenset")
        if not self.operations <= ALL_OPERATIONS:
            raise NexusWorkspacePolicyError("rule contains unsupported operations")
        object.__setattr__(self, "scope", normalized)


@dataclass(frozen=True, slots=True)
class WorkspaceAction:
    """Fully scoped action identity for a HOST-OWNED approval verifier.

    ``binding_sha256`` is an identifier, NOT a digital signature or proof that
    approval happened. The verifier must consult an independent approval store,
    compare the full action, and consume a one-shot approval atomically.
    """

    root: str
    mission_id: str
    operation: str
    relative_path: str
    action_id: str

    @property
    def binding_sha256(self) -> str:
        fields = {
            "root": self.root,
            "mission_id": self.mission_id,
            "operation": self.operation,
            "relative_path": self.relative_path,
            "action_id": self.action_id,
        }
        serialized = json.dumps(fields, sort_keys=True, separators=(",", ":"))
        return sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkspaceDecision:
    """Metadata-only policy result; it never executes an operation."""

    allowed: bool
    code: str
    mission_id: str
    operation: str
    relative_path: str
    approval_required: bool
    action_binding_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": POLICY_SCHEMA,
            "allowed": self.allowed,
            "code": self.code,
            "mission_id": self.mission_id,
            "operation": self.operation,
            "relative_path": self.relative_path,
            "approval_required": self.approval_required,
            "action_binding_sha256": self.action_binding_sha256,
        }


ApprovalVerifier = Callable[[WorkspaceAction], bool]


class NexusWorkspacePolicy:
    """Restricted facade around the Colossus workspace analysis modules.

    No implicit allow rules; no mutating operation unless all of these hold:
    the operation is explicitly in a mission rule, ``allow_mutations=True``,
    a unique host-created action ID is provided, and the trusted verifier
    approves the exact action. Even if approved, this class does NOT execute it.
    """

    def __init__(
        self,
        workspace: NexusWorkspaceManager,
        *,
        rules: Iterable[WorkspaceRule] = (),
        allow_mutations: bool = False,
        approval_verifier: ApprovalVerifier | None = None,
    ) -> None:
        if not isinstance(workspace, NexusWorkspaceManager):
            raise NexusWorkspacePolicyError("workspace must be NexusWorkspaceManager")
        if type(allow_mutations) is not bool:
            raise NexusWorkspacePolicyError("allow_mutations must be a boolean")
        if approval_verifier is not None and not callable(approval_verifier):
            raise NexusWorkspacePolicyError("approval_verifier must be callable")
        if allow_mutations and approval_verifier is None:
            raise NexusWorkspacePolicyError("mutations require a trusted approval verifier")
        captured = tuple(rules)
        if len(captured) > 256 or not all(isinstance(r, WorkspaceRule) for r in captured):
            raise NexusWorkspacePolicyError("rules must contain at most 256 WorkspaceRule instances")
        self.workspace = workspace
        self.rules = captured
        self.allow_mutations = allow_mutations
        self._approval_verifier = approval_verifier
        self._consumed_action_ids: set[str] = set()
        self._approval_lock = Lock()

    def _target(self, operation: str, path: str) -> str:
        normalized = _relative_scope(path)
        if normalized == ".":
            if operation not in _DIRECTORY_OPERATIONS | frozenset({"run_tests", "execute_tool"}):
                raise NexusWorkspacePolicyError("operation requires a concrete file path")
            resolved = self.workspace.root
        else:
            # Reuse manager's existing traversal/link/junction/sensitive guards.
            # It is a private API; keep a regression test if manager is changed.
            resolved = self.workspace._resolve(normalized)
        if operation in _DIRECTORY_OPERATIONS and not resolved.is_dir():
            raise NexusWorkspacePolicyError("operation requires an existing directory")
        if operation in _FILE_OPERATIONS and not resolved.is_file():
            raise NexusWorkspacePolicyError("operation requires an existing file")
        if operation == "write_file" and (resolved.is_dir() or not resolved.parent.is_dir()):
            raise NexusWorkspacePolicyError("write target must have an existing parent directory")
        if operation == "execute_tool" and not resolved.exists():
            raise NexusWorkspacePolicyError("tool scope must exist")
        if operation == "run_tests" and not resolved.exists():
            raise NexusWorkspacePolicyError("test scope must exist")
        return normalized

    def check(
        self,
        mission_id: str,
        operation: str,
        path: str,
        *,
        action_id: str | None = None,
    ) -> WorkspaceDecision:
        """Return a metadata-only authorization decision; fail closed.

        The approval verifier MUST be trusted host code and MUST validate the
        full action identity against an independent, single-use approval record.
        Invoke this only immediately before an actual separately implemented
        effect: a successful approval consumes its action ID. For previews,
        omit ``action_id`` so the verifier is not invoked.
        """
        needs_approval = operation in IMPACTFUL_OPERATIONS

        def decision(allowed: bool, code: str, target: str = "", digest: str | None = None) -> WorkspaceDecision:
            return WorkspaceDecision(
                allowed=allowed, code=code, mission_id=mission_id if isinstance(mission_id, str) else "",
                operation=operation if isinstance(operation, str) else "",
                relative_path=target, approval_required=needs_approval,
                action_binding_sha256=digest,
            )

        if not _valid_mission(mission_id) or not isinstance(operation, str) or operation not in ALL_OPERATIONS:
            return decision(False, "invalid_request")
        try:
            target = self._target(operation, path)
        except (NexusWorkspacePolicyError, NexusWorkspaceError, OSError, ValueError, RuntimeError):
            return decision(False, "invalid_or_restricted_path")
        if not any(
            rule.mission_id == mission_id
            and operation in rule.operations
            and _is_within(target, rule.scope)
            for rule in self.rules
        ):
            return decision(False, "outside_mission_permissions", target)
        if not needs_approval:
            return decision(True, "allowed_read_only", target)
        if not self.allow_mutations:
            return decision(False, "impactful_operations_disabled", target)
        if not isinstance(action_id, str) or not _ACTION_ID_RE.fullmatch(action_id):
            return decision(False, "missing_or_invalid_action_id", target)
        action = WorkspaceAction(
            root=str(self.workspace.root), mission_id=mission_id,
            operation=operation, relative_path=target, action_id=action_id,
        )
        # One-shot protection within one process. Cross-process/durable replay
        # prevention is exclusively the trusted approval store's responsibility.
        with self._approval_lock:
            if action_id in self._consumed_action_ids:
                return decision(False, "approval_replay", target)
            try:
                verified = self._approval_verifier(action) is True if self._approval_verifier else False
            except Exception:
                verified = False
            if not verified:
                return decision(False, "approval_not_verified", target)
            self._consumed_action_ids.add(action_id)
        return decision(True, "approved_action_decision_only", target, action.binding_sha256)

    def require(
        self,
        mission_id: str,
        operation: str,
        path: str,
        *,
        action_id: str | None = None,
    ) -> WorkspaceDecision:
        """Raise a safe, non-content-bearing error on denial."""
        result = self.check(mission_id, operation, path, action_id=action_id)
        if not result.allowed:
            raise NexusWorkspaceAccessDenied(f"workspace policy denied request: {result.code}")
        return result

    # Host-facing facade: ONLY these four methods perform read-only inspection.
    def inventory(
        self,
        mission_id: str,
        scope: str = ".",
        *,
        with_hashes: bool = False,
    ) -> dict[str, Any]:
        if type(with_hashes) is not bool:
            raise NexusWorkspacePolicyError("with_hashes must be boolean")
        self.require(mission_id, "hash_files" if with_hashes else "inventory", scope)
        return self.workspace.inventory(scope, with_hashes=with_hashes)

    def read_text(self, mission_id: str, path: str, *, max_chars: int | None = None) -> str:
        self.require(mission_id, "read_text", path)
        return self.workspace.read_text(path, max_chars=max_chars)

    def index_repository(
        self,
        mission_id: str,
        repository_index: "NexusRepositoryIndex",
        scope: str = ".",
    ) -> dict[str, Any]:
        from .repo_index import NexusRepositoryIndex
        if not isinstance(repository_index, NexusRepositoryIndex) or repository_index.workspace is not self.workspace:
            raise NexusWorkspacePolicyError("index must use the policy's workspace instance")
        self.require(mission_id, "index_repo", scope)
        return repository_index.build(scope)

    def build_symbol_graph(
        self,
        mission_id: str,
        graph: "NexusSymbolGraph",
        scope: str = ".",
    ) -> dict[str, Any]:
        from .symbol_graph import NexusSymbolGraph
        if (not isinstance(graph, NexusSymbolGraph)
                or graph.repository_index.workspace is not self.workspace):
            raise NexusWorkspacePolicyError("graph must use the policy's workspace instance")
        self.require(mission_id, "build_symbol_graph", scope)
        return graph.build(scope)


__all__ = [
    "POLICY_SCHEMA", "READ_OPERATIONS", "IMPACTFUL_OPERATIONS", "ALL_OPERATIONS",
    "NexusWorkspacePolicyError", "NexusWorkspaceAccessDenied", "WorkspaceRule",
    "WorkspaceAction", "WorkspaceDecision", "ApprovalVerifier", "NexusWorkspacePolicy",
]
