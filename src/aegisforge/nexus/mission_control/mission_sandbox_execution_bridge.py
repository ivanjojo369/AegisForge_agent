"""One-use, host-approved bridge from Colossus Mission Control to isolated sandbox recipes.

Host-side integration only. Staging/preview/review request never run code. The
ONLY effect path delegates to the existing NexusSandboxRunner after independently
checking the mission anchor, supervised plan, host review, exact operation
binding, and trusted Docker configuration. Neither hash values nor a preflight
'review_required' status are authority. The gate's authenticated reviewer and
live binding provider MUST be wired by the trusted host.

There is NO generic command dispatch, patch writing, raw stdout, networking or
model-call API. Docker is disabled by default. Untrusted Python code must never
run inside this approval process. The host must protect its Docker daemon and
prepare an immutable, sanitized, separate checkout before enabling execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import secrets
from threading import RLock
from typing import Any

from .mission_registry import NexusMissionRegistry
from .mission_supervised_executor import (
    NexusMissionSupervisedExecutor, SupervisedActionPlan,
    verify_supervised_plan, verify_supervised_report,
)
from ..trust.action_policy import NexusActionPolicy, ActionIntent
from ..trust.approval_gate import NexusApprovalGate, ActionBinding, canonical_sha256
from ..trust.sandbox_runner import (
    NexusSandboxRunner, SandboxPlan, DockerSandboxBackend,
)

SANDBOX_BRIDGE_SCHEMA = "aegisforge.nexus.mission_control.sandbox_bridge.v1"
_RECIPES = frozenset({"python_ast", "node_check", "pytest_file", "node_test_file"})
_LIMIT_SESSIONS = 64


class NexusMissionSandboxBridgeError(ValueError):
    """Action was rejected, unavailable, stale or already consumed."""


def _must(condition: bool, explanation: str) -> None:
    if not condition:
        raise NexusMissionSandboxBridgeError(explanation)


def _fingerprint(value: Any) -> str:
    try:
        return canonical_sha256(value)
    except Exception:
        raise NexusMissionSandboxBridgeError("invalid bounded metadata") from None


def verify_sandbox_bridge_receipt(value: Any) -> bool:
    """Check formatting and internal SHA-256 consistency; NOT authenticity."""
    try:
        if type(value) is not dict or set(value) != {
            "schema", "session_id", "mission_id", "product", "sandbox_plan_sha256",
            "supervised_plan_sha256", "registry_sha256", "action_sha256", "status",
            "sandbox_outcome_sha256", "approval_consumed", "effects_attempted",
            "authenticity_verified", "integrity_only", "receipt_sha256",
        }:
            return False
        if value["schema"] != SANDBOX_BRIDGE_SCHEMA or value["status"] not in {
            "review_required", "approval_pending", "approval_denied", "stale",
            "sandbox_passed", "sandbox_failed", "sandbox_timed_out", "sandbox_infra_error",
        }:
            return False
        if value["authenticity_verified"] is not False or value["integrity_only"] is not True:
            return False
        if any(type(value[x]) is not str or len(value[x]) != 64 or
               any(ch not in "0123456789abcdef" for ch in value[x]) for x in (
                   "sandbox_plan_sha256", "supervised_plan_sha256", "registry_sha256",
                   "action_sha256", "receipt_sha256")):
            return False
        if type(value["session_id"]) is not str or len(value["session_id"]) != 32 or any(
                c not in "0123456789abcdef" for c in value["session_id"]):
            return False
        if type(value["mission_id"]) is not str or not 3 <= len(value["mission_id"]) <= 96:
            return False
        if value["product"] not in {"nexus", "aegisgrid", "live_forge", "colossus"}:
            return False
        if type(value["approval_consumed"]) is not bool or type(value["effects_attempted"]) is not bool:
            return False
        outcome = value["sandbox_outcome_sha256"]
        if outcome is not None and (type(outcome) is not str or len(outcome) != 64 or any(
                c not in "0123456789abcdef" for c in outcome)):
            return False
        if value["status"].startswith("sandbox_") != value["effects_attempted"]:
            return False
        if value["approval_consumed"] and not value["effects_attempted"]:
            return False
        if value["effects_attempted"] != (outcome is not None):
            return False
        check = _fingerprint({k: v for k, v in value.items() if k != "receipt_sha256"})
        return hmac.compare_digest(check, value["receipt_sha256"])
    except (TypeError, KeyError, ValueError, OverflowError, RecursionError):
        return False


@dataclass(slots=True)
class _Staged:
    mission_id: str
    registry_sha256: str
    sandbox: SandboxPlan
    supervised: SupervisedActionPlan
    action_sha256: str
    approval_id: str | None = None
    approval_binding: ActionBinding | None = None
    consumed: bool = False


class NexusMissionSandboxExecutionBridge:
    """Trusted host orchestrator for fixed-recipe, Docker-only sandbox runs.

    `effects_enabled=False` is the default and permits stage/preview/review only.
    A production host can set it True only after wiring an authenticated
    reviewer, an independently computed live binding provider, and an isolated,
    sanitized checkout. Only the existing DockerSandboxBackend is admitted at
    the final effect boundary. Approval IDs cannot be supplied by model output.
    """

    def __init__(self, registry: NexusMissionRegistry,
                 policy: NexusActionPolicy,
                 supervised: NexusMissionSupervisedExecutor,
                 runner: NexusSandboxRunner,
                 *, approval_gate: NexusApprovalGate | None = None,
                 effects_enabled: bool = False,
                 max_sessions: int = _LIMIT_SESSIONS) -> None:
        _must(type(registry) is NexusMissionRegistry, "invalid mission registry")
        _must(type(policy) is NexusActionPolicy, "invalid action policy")
        _must(type(supervised) is NexusMissionSupervisedExecutor, "invalid supervised executor")
        _must(type(runner) is NexusSandboxRunner, "invalid sandbox runner")
        _must(supervised.registry is registry and supervised.policy is policy,
              "supervised sources do not match")
        _must(runner.policy is policy.workspace_policy, "sandbox and action policies differ")
        _must(approval_gate is None or type(approval_gate) is NexusApprovalGate,
              "invalid approval gate")
        if approval_gate is not None:
            _must(approval_gate.root == str(runner.policy.workspace.root),
                  "approval gate workspace mismatch")
        _must(type(effects_enabled) is bool, "execution flag must be boolean")
        _must(type(max_sessions) is int and 1 <= max_sessions <= _LIMIT_SESSIONS,
              "invalid session limit")
        self.registry = registry
        self.policy = policy
        self.supervised = supervised
        self.runner = runner
        self.gate = approval_gate
        self.effects_enabled = effects_enabled
        self.max_sessions = max_sessions
        self._staged: dict[str, _Staged] = {}
        self._lock = RLock()

    def _view(self, mission_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            snap = self.registry.snapshot()
            row = next(r for r in snap["missions"] if r["mission_id"] == mission_id)
            return snap, row
        except (KeyError, ValueError, TypeError, StopIteration):
            raise NexusMissionSandboxBridgeError("mission unavailable") from None

    def _state(self, item: _Staged) -> str:
        snap, row = self._view(item.mission_id)
        _must(snap["registry_sha256"] == item.registry_sha256,
              "mission registry changed since staging")
        _must(row["status"] in {"ready", "running"}, "mission no longer ready")
        _must({"mission_control", "trust"}.issubset(set(row["components"])),
              "mission required components missing")
        report = self.supervised.inspect(item.supervised.session_id).to_dict()
        _must(verify_supervised_report(report, plan=item.supervised.to_dict()),
              "invalid supervised inspection")
        _must(report["outcome"] == "review_required", "supervised plan is not review-ready")
        # Supervisory action preflight checks metadata; sandbox must additionally
        # verify LIVE source bytes and backend identity before review.
        try:
            _must(self.runner._source_sha(item.mission_id, item.sandbox.relative_path)
                  == item.sandbox.source_sha256, "sandbox source changed")
            _must(self.runner._backend_sha() == item.sandbox.backend_sha256 and
                  self.runner.limits == item.sandbox.limits,
                  "sandbox backend or resource limits changed")
        except Exception:
            raise NexusMissionSandboxBridgeError("sandbox source or configuration unavailable") from None
        return row["product"]

    def stage(self, mission_id: str, recipe: str, relative_path: str, *,
              tool_id: str, expected_registry_sha256: str) -> dict[str, Any]:
        """Prepare an inert one-shot fixed recipe, without asking for approval."""
        _must(type(recipe) is str and recipe in _RECIPES, "unregistered sandbox recipe")
        _must(type(mission_id) is str and type(tool_id) is str, "invalid mission/tool")
        with self._lock:
            _must(len(self._staged) < self.max_sessions, "sandbox session capacity exceeded")
            snap, row = self._view(mission_id)
            _must(type(expected_registry_sha256) is str and
                  snap["registry_sha256"] == expected_registry_sha256,
                  "stale registry anchor")
            _must(row["status"] in {"ready", "running"} and
                  {"mission_control", "trust"}.issubset(row["components"]),
                  "mission not eligible for sandbox staging")
            try:
                sandbox = self.runner.prepare(mission_id, recipe, relative_path)
                intent = ActionIntent(mission_id, tool_id, sandbox.operation,
                                      sandbox.relative_path, sandbox.plan_sha256, {}, recipe)
                supervised = self.supervised.prepare(
                    mission_id, (intent,), expected_registry_sha256=expected_registry_sha256)
                plan = supervised.to_dict()
                _must(verify_supervised_plan(plan) and plan["outcome"] == "review_required",
                      "host policy did not permit sandbox review")
            except Exception:
                raise NexusMissionSandboxBridgeError("sandbox staging was rejected") from None
            action_sha = _fingerprint({"mission_id": mission_id, "recipe": recipe,
                                        "operation": sandbox.operation,
                                        "path_sha256": sha256(sandbox.relative_path.encode()).hexdigest(),
                                        "plan_sha256": sandbox.plan_sha256,
                                        "supervised_plan_sha256": plan["plan_sha256"]})
            stage_id = secrets.token_hex(16)
            self._staged[stage_id] = _Staged(mission_id, expected_registry_sha256,
                                             sandbox, supervised, action_sha)
            return self._receipt(stage_id, self._staged[stage_id], "review_required")

    def _get(self, session_id: str) -> _Staged:
        _must(type(session_id) is str and session_id in self._staged,
              "unknown sandbox session")
        return self._staged[session_id]

    def _receipt(self, session_id: str, item: _Staged, status: str, *,
                 outcome_sha: str | None = None, consumed: bool = False,
                 effects: bool = False) -> dict[str, Any]:
        product = next((r["product"] for r in self.registry.snapshot()["missions"]
                        if r["mission_id"] == item.mission_id), None)
        _must(product is not None, "mission unavailable")
        body = {
            "schema": SANDBOX_BRIDGE_SCHEMA, "session_id": session_id,
            "mission_id": item.mission_id, "product": product,
            "sandbox_plan_sha256": item.sandbox.plan_sha256,
            "supervised_plan_sha256": item.supervised.to_dict()["plan_sha256"],
            "registry_sha256": item.registry_sha256,
            "action_sha256": item.action_sha256, "status": status,
            "sandbox_outcome_sha256": outcome_sha,
            "approval_consumed": consumed, "effects_attempted": effects,
            "authenticity_verified": False, "integrity_only": True,
        }
        body["receipt_sha256"] = _fingerprint(body)
        _must(verify_sandbox_bridge_receipt(body), "invalid internal sandbox receipt")
        return body

    def inspect(self, session_id: str) -> dict[str, Any]:
        """Live preflight status. Does not consume approvals or execute effects."""
        with self._lock:
            item = self._get(session_id)
            _must(not item.consumed, "sandbox session already consumed")
            try:
                self._state(item)
            except NexusMissionSandboxBridgeError:
                return self._receipt(session_id, item, "stale")
            if item.approval_id is None:
                return self._receipt(session_id, item, "review_required")
            if self.gate is None:
                return self._receipt(session_id, item, "approval_denied")
            status = self.gate.status(item.approval_id).status
            if status in {"denied", "expired", "consumed"}:
                return self._receipt(session_id, item, "approval_denied")
            return self._receipt(session_id, item, "approval_pending")

    def request_review(self, session_id: str, *, ttl_seconds: int = 600) -> dict[str, Any]:
        """Create a host-owned exact review request; never call the reviewer."""
        with self._lock:
            item = self._get(session_id)
            _must(not item.consumed and item.approval_id is None,
                  "sandbox review is already requested or finalized")
            _must(self.gate is not None, "trusted approval gate unavailable")
            self._state(item)
            try:
                binding = self.runner.binding_for(item.sandbox)
                request = self.gate.request(item.mission_id, item.sandbox.operation,
                                            item.sandbox.relative_path,
                                            binding=binding, ttl_seconds=ttl_seconds)
            except Exception:
                raise NexusMissionSandboxBridgeError("live sandbox approval request failed") from None
            item.approval_id = request.request_id
            item.approval_binding = binding
            return request.to_dict()

    def _wired_for_effects(self) -> None:
        _must(self.effects_enabled, "sandbox effects disabled by host")
        _must(self.gate is not None, "approval gate missing")
        _must(type(self.runner.backend) is DockerSandboxBackend,
              "sandbox effect backend must be trusted Docker")
        verifier = getattr(self.runner.policy, "_approval_verifier", None)
        _must(getattr(verifier, "__self__", None) is self.gate and
              getattr(verifier, "__func__", None) is NexusApprovalGate.verify_workspace_action,
              "workspace policy not wired to exact approval gate")
        _must(self.runner.policy.allow_mutations is True,
              "workspace policy disables sandbox effects")

    def run_approved(self, session_id: str) -> dict[str, Any]:
        """Attempt ONE host-reviewed fixed recipe in isolated Docker.

        No auto-review. The host must call gate.review() independently.
        An unsuccessful attempt invalidates the staged session and cancels
        any unused review authorization to avoid a dangling capability.
        """
        with self._lock:
            item = self._get(session_id)
            _must(not item.consumed, "sandbox session already consumed")
            item.consumed = True  # claim before ALL effect-related checks
            try:
                self._wired_for_effects()
                self._state(item)
                _must(item.approval_id is not None and item.approval_binding is not None,
                      "sandbox review was not requested")
                _must(self.gate is not None and self.gate.status(item.approval_id).status == "approved",
                      "exact sandbox request is not approved")
                _must(self.runner.binding_for(item.sandbox) == item.approval_binding,
                      "sandbox binding changed after review")
                self.supervised.finalize(item.supervised.session_id)
                sandbox_report = self.runner.run(item.sandbox, action_id=item.approval_id)
                _must(type(sandbox_report) is dict and sandbox_report.get("executed") is True,
                      "sandbox returned an invalid execution report")
                _must(sandbox_report.get("plan_sha256") == item.sandbox.plan_sha256 and
                      sandbox_report.get("mission_id") == item.mission_id and
                      sandbox_report.get("recipe") == item.sandbox.recipe,
                      "sandbox execution plan mismatch")
                hash_received = sandbox_report.get("report_sha256")
                _must(type(hash_received) is str and hmac.compare_digest(
                    hash_received, _fingerprint({k:v for k,v in sandbox_report.items()
                                                  if k != "report_sha256"})),
                      "invalid sandbox report hash")
                outcome = sandbox_report["status"]
                _must(outcome in {"passed", "failed", "timed_out", "infra_error"},
                      "invalid sandbox outcome")
                consumed = self.gate.status(item.approval_id).status == "consumed"
                _must(consumed, "approval was not consumed at effect boundary")
                return self._receipt(session_id, item, "sandbox_" + outcome,
                                     outcome_sha=hash_received, consumed=True, effects=True)
            except NexusMissionSandboxBridgeError:
                if item.approval_id is not None and self.gate is not None:
                    self.gate.deny(item.approval_id)
                raise
            except Exception:
                if item.approval_id is not None and self.gate is not None:
                    self.gate.deny(item.approval_id)
                raise NexusMissionSandboxBridgeError(
                    "sandbox execution rejected or unavailable; session consumed") from None

    def cancel(self, session_id: str) -> None:
        """Discard an inert stage. Deny any live review request where possible."""
        with self._lock:
            item = self._get(session_id)
            _must(not item.consumed, "sandbox session already consumed")
            item.consumed = True
            if item.approval_id is not None and self.gate is not None:
                self.gate.deny(item.approval_id)
            self.supervised.forget(item.supervised.session_id)

    def release(self, session_id: str) -> None:
        """Forget a closed session to reclaim bounded memory; no auth effect."""
        with self._lock:
            item = self._get(session_id)
            _must(item.consumed, "cannot release an active sandbox session")
            self._staged.pop(session_id)
            # Finalized supervised sessions may remain in the supervisor and
            # should be retired by the trusted host's lifecycle manager.


__all__ = ["SANDBOX_BRIDGE_SCHEMA", "NexusMissionSandboxBridgeError",
           "NexusMissionSandboxExecutionBridge", "verify_sandbox_bridge_receipt"]
