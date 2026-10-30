"""Fail-closed, host-owned action preflight for AegisForge Nexus / Colossus.

Install as: src/aegisforge/nexus/trust/action_policy.py

This module answers which registered tool, workspace operation, path, fixed
sandbox recipe, and JSON payload a mission MAY REQUEST. It never runs a tool,
writes a file, starts a sandbox, contacts Nebius, or grants an impactful action.

Integration contract (IMPORTANT):
* The trusted host constructs ActionRule instances and registers tool handlers;
  model output may only REQUEST an action. Untrusted data must not define rules.
* NexusWorkspacePolicy remains the authoritative *workspace* allowlist and
  single-use approval consumer; this policy is an additional preflight layer.
* For impactful actions, evaluate() returns 'review_required' with allowed=False.
  To request ApprovalGate authorization for a sandbox plan, use the existing
  NexusSandboxRunner.binding_for(plan) rather than policy-preflight hashes.
  The host MUST independently rebuild that live binding from the real operation,
  verify source/plan hashes, and pass an authenticated single-use approval to
  the ultimate execution boundary (e.g. NexusSandboxRunner.run()).
* NEVER call NexusWorkspacePolicy.check(..., action_id=...) while previewing:
  that would consume a single-use approval without executing the operation.
* The secret scanner is best-effort defense in depth. Payload fields may be
  sensitive even if scanner finds no pattern; do not transmit unreviewed repo
  contents, raw tool output, credentials, or other private data to providers.
* All path validation here is preflight; the effect boundary must re-check
  paths, file state, permissions and staged checkout against TOCTOU changes.
* A hash is only an identity binding, not proof of authenticity or consent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping

from ..workspace.workspace_policy import (
    ALL_OPERATIONS,
    IMPACTFUL_OPERATIONS,
    READ_OPERATIONS,
    NexusWorkspacePolicy,
    _is_within,
    _relative_scope,
    _valid_mission,
)
from .approval_gate import canonical_sha256
from .secret_guard import NexusSecretGuard, NexusSecretGuardError

ACTION_POLICY_SCHEMA = "aegisforge.nexus.trust.action_policy.v1"
_TOOL_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECIPE_OPERATION = {
    "python_ast": "execute_tool",
    "node_check": "execute_tool",
    "pytest_file": "run_tests",
    "node_test_file": "run_tests",
}
_MAX_RULES = 256
_MAX_REQUEST_BYTES = 65_536


class NexusActionPolicyError(ValueError):
    """Invalid trusted policy configuration or non-readable action."""


class NexusActionDenied(NexusActionPolicyError):
    """The requested action is not permitted by the host policy."""


@dataclass(frozen=True, slots=True)
class ActionRule:
    """Host-created, allow-by-exact-tool+operation+mission, path-scope rule.

    For execute_tool / run_tests, recipes must explicitly enumerate allowed
    sandbox recipes. For other operations recipes MUST be empty. A narrower
    overlapping scope takes precedence, including its lower payload limit.
    """

    mission_id: str
    tool_id: str
    operation: str
    scope: str
    recipes: frozenset[str] = frozenset()
    allowed_payload_keys: frozenset[str] = frozenset()
    max_payload_bytes: int = 16_384

    def __post_init__(self) -> None:
        if not _valid_mission(self.mission_id):
            raise NexusActionPolicyError("invalid mission identifier")
        if not isinstance(self.tool_id, str) or not _TOOL_ID.fullmatch(self.tool_id):
            raise NexusActionPolicyError("invalid registered tool identifier")
        if self.operation not in ALL_OPERATIONS:
            raise NexusActionPolicyError("unsupported workspace operation")
        try:
            normalized = _relative_scope(self.scope)
        except (ValueError, TypeError) as exc:
            raise NexusActionPolicyError("invalid rule scope") from exc
        if type(self.recipes) is not frozenset:
            raise NexusActionPolicyError("recipes must be a frozenset")
        valid_recipes = {r for r, operation in _RECIPE_OPERATION.items()
                         if operation == self.operation}
        if self.operation in {"execute_tool", "run_tests"}:
            if not self.recipes or not self.recipes <= valid_recipes:
                raise NexusActionPolicyError("execution needs exact allowed recipes")
        elif self.recipes:
            raise NexusActionPolicyError("non-execution operation cannot allow recipes")
        if (type(self.allowed_payload_keys) is not frozenset
                or len(self.allowed_payload_keys) > 32
                or any(not isinstance(k, str) or not _TOOL_ID.fullmatch(k)
                       for k in self.allowed_payload_keys)):
            raise NexusActionPolicyError("invalid allowed payload keys")
        if (type(self.max_payload_bytes) is not int
                or not 64 <= self.max_payload_bytes <= _MAX_REQUEST_BYTES):
            raise NexusActionPolicyError("invalid JSON payload size limit")
        object.__setattr__(self, "scope", normalized)


@dataclass(frozen=True, slots=True)
class ActionIntent:
    """Untrusted proposal for a host-defined tool, NOT an executable command.

    plan_sha256 MUST represent a plan/revision from a trusted host registry.
    Passing a plausible digest in model output never grants any authority.
    The payload is opaque JSON metadata, not argv, code, shell text or secrets.
    """

    mission_id: str
    tool_id: str
    operation: str
    relative_path: str
    plan_sha256: str
    payload: Mapping[str, Any] = field(default_factory=dict, repr=False)
    recipe: str | None = None


@dataclass(frozen=True, slots=True)
class ActionDecision:
    """Metadata-only verdict. Do NOT treat as a capability to perform effects."""

    status: str               # allow_read_only | review_required | denied
    code: str
    allowed: bool             # always False for impactful operations
    approval_required: bool
    mission_id: str
    tool_id: str
    operation: str
    relative_path: str
    payload_sha256: str | None = None
    context_sha256: str | None = None
    schema: str = ACTION_POLICY_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "status": self.status, "code": self.code,
            "allowed": self.allowed, "approval_required": self.approval_required,
            "mission_id": self.mission_id, "tool_id": self.tool_id,
            "operation": self.operation, "relative_path": self.relative_path,
            "payload_sha256": self.payload_sha256,
            "context_sha256": self.context_sha256,
        }


class NexusActionPolicy:
    """A non-executing, no-I/O policy gate layered over WorkspacePolicy.

    The supplied WorkspacePolicy and SecretGuard MUST be host-owned. The
    ruleset is validated once and never learned from LLM responses.

    ``evaluate`` calls WorkspacePolicy.check with NO action ID. For impactful
    operations it only determines whether asking for approval is permissible.
    A later host-owned execution path MUST consume the ApprovalGate approval
    through WorkspacePolicy.require(..., action_id=...) and independently
    verify its live action binding before executing the effect.
    """

    def __init__(
        self,
        workspace_policy: NexusWorkspacePolicy,
        *,
        rules: Iterable[ActionRule] = (),
        secret_guard: NexusSecretGuard | None = None,
    ) -> None:
        if not isinstance(workspace_policy, NexusWorkspacePolicy):
            raise NexusActionPolicyError("workspace_policy must be NexusWorkspacePolicy")
        if secret_guard is not None and not isinstance(secret_guard, NexusSecretGuard):
            raise NexusActionPolicyError("secret_guard must be NexusSecretGuard")
        captured = tuple(rules)
        if len(captured) > _MAX_RULES or any(not isinstance(r, ActionRule) for r in captured):
            raise NexusActionPolicyError("invalid rule collection")
        keys = [(r.mission_id, r.tool_id, r.operation, r.scope) for r in captured]
        if len(keys) != len(set(keys)):
            raise NexusActionPolicyError("duplicate host policy rules")
        self.workspace_policy = workspace_policy
        self.rules = captured
        self.secret_guard = secret_guard or NexusSecretGuard()

    @staticmethod
    def _deny(intent: Any, code: str) -> ActionDecision:
        return ActionDecision(
            status="denied", code=code, allowed=False,
            approval_required=isinstance(intent, ActionIntent)
                and intent.operation in IMPACTFUL_OPERATIONS,
            mission_id=intent.mission_id if isinstance(intent, ActionIntent) and isinstance(intent.mission_id, str) else "",
            tool_id=intent.tool_id if isinstance(intent, ActionIntent) and isinstance(intent.tool_id, str) else "",
            operation=intent.operation if isinstance(intent, ActionIntent) and isinstance(intent.operation, str) else "",
            relative_path="",  # never echo unsafe or secret-bearing paths
        )

    def _matching_rule(self, intent: ActionIntent, normalized_path: str) -> ActionRule | None:
        candidates = [
            rule for rule in self.rules
            if rule.mission_id == intent.mission_id
            and rule.tool_id == intent.tool_id
            and rule.operation == intent.operation
            and _is_within(normalized_path, rule.scope)
        ]
        if not candidates:
            return None
        # Most specific matching rule wins; no broad-scope fallback if a narrow
        # rule refuses a recipe or applies a smaller payload limit.
        return max(candidates, key=lambda r: len(PurePosixPath(r.scope).parts) if r.scope != "." else 0)

    @staticmethod
    def _payload_bytes(payload: Mapping[str, Any]) -> int:
        if not isinstance(payload, Mapping):
            raise NexusActionPolicyError("payload must be a JSON object")
        try:
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
            encoded = raw.encode("utf-8")
        except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
            raise NexusActionPolicyError("payload must be valid JSON") from exc
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise NexusActionPolicyError("payload exceeds the hard policy limit")
        return len(encoded)

    def evaluate(self, intent: ActionIntent) -> ActionDecision:
        """Non-consuming preflight, with no tool execution or file writes.

        Review-required is NOT an approved action. No action_id is accepted.
        Payload is checked against SecretGuard; only nonsecret hashes escape.
        """
        if not isinstance(intent, ActionIntent):
            return self._deny(intent, "invalid_intent")
        if (not _valid_mission(intent.mission_id)
                or not isinstance(intent.tool_id, str) or not _TOOL_ID.fullmatch(intent.tool_id)
                or not isinstance(intent.operation, str) or intent.operation not in ALL_OPERATIONS
                or not isinstance(intent.plan_sha256, str)
                or not _SHA256.fullmatch(intent.plan_sha256)):
            return self._deny(intent, "invalid_intent")
        try:
            path = _relative_scope(intent.relative_path)
        except (ValueError, TypeError):
            return self._deny(intent, "invalid_path")
        rule = self._matching_rule(intent, path)
        if rule is None:
            return self._deny(intent, "no_matching_host_rule")
        if intent.operation in ("run_tests", "execute_tool"):
            if not isinstance(intent.recipe, str) or intent.recipe not in rule.recipes:
                return self._deny(intent, "recipe_not_authorized")
        elif intent.recipe is not None:
            return self._deny(intent, "recipe_not_applicable")
        try:
            size = self._payload_bytes(intent.payload)
            if not set(intent.payload).issubset(rule.allowed_payload_keys):
                return self._deny(intent, "payload_fields_not_allowed")
            if size > rule.max_payload_bytes:
                return self._deny(intent, "payload_too_large")
            # BLOCK rather than silently redact: a rewritten payload would no
            # longer match the independently reviewed operation.
            self.secret_guard.require_clean_payload(intent.payload)
            payload_sha = canonical_sha256({
                "tool_id": intent.tool_id, "operation": intent.operation,
                "path": path, "recipe": intent.recipe, "payload": intent.payload,
            })
            context_sha = canonical_sha256({
                "schema": ACTION_POLICY_SCHEMA,
                "workspace_root": str(self.workspace_policy.workspace.root),
                "mission_id": intent.mission_id,
                "plan_sha256": intent.plan_sha256,
                "payload_sha256": payload_sha,
            })
        except (NexusActionPolicyError, NexusSecretGuardError, ValueError, TypeError, OverflowError):
            return self._deny(intent, "payload_rejected")
        # Action ID deliberately omitted: approvals must be consumed by the
        # real effect boundary, not by preflight.
        workspace = self.workspace_policy.check(intent.mission_id, intent.operation, path)
        impactful = intent.operation in IMPACTFUL_OPERATIONS
        if impactful:
            if workspace.code != "missing_or_invalid_action_id":
                return self._deny(intent, "workspace_policy_denied")
            return ActionDecision(
                "review_required", "approval_required", False, True,
                intent.mission_id, intent.tool_id, intent.operation, path,
                payload_sha, context_sha,
            )
        if not workspace.allowed:
            return self._deny(intent, "workspace_policy_denied")
        return ActionDecision(
            "allow_read_only", "allowed_read_only_preflight", True, False,
            intent.mission_id, intent.tool_id, intent.operation, path,
            payload_sha, context_sha,
        )

    def require_review(self, intent: ActionIntent) -> ActionDecision:
        """Require a review-eligible impactful preflight WITHOUT consuming approval.

        The returned fingerprints are audit metadata, NOT ApprovalGate request
        bindings. For a sandbox operation, use runner.binding_for(actual_plan)
        in the trusted approval workflow; the runner consumes that approval
        through WorkspacePolicy at its real execution boundary.
        """
        decision = self.evaluate(intent)
        if decision.status != "review_required":
            raise NexusActionDenied("action is not eligible for an approval request")
        return decision

    def require_read(self, intent: ActionIntent) -> ActionDecision:
        """Check read preflight; the caller must still enforce access on read."""
        decision = self.evaluate(intent)
        if decision.status != "allow_read_only":
            raise NexusActionDenied("action is not a permitted read-only operation")
        return decision


__all__ = [
    "ACTION_POLICY_SCHEMA", "NexusActionPolicyError", "NexusActionDenied",
    "ActionRule", "ActionIntent", "ActionDecision", "NexusActionPolicy",
]
