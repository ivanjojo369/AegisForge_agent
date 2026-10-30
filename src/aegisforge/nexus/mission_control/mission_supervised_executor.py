"""Fail-closed supervised ACTION PREFLIGHT for AegisForge Nexus / Colossus.

No effects. This is a staging boundary, NOT an effect executor or authorization
capability. In particular, a Trust status of 'approved', a hash, or a policy
'allow_read_only' result is NOT sufficient authority to perform an effect.

The trusted host owns NexusActionPolicy and MissionRegistry and must separately
implement an audited, action-bound, single-use effect boundary if ever needed.
This module NEVER consumes approvals, accesses a model, invokes tools, changes
files, or dispatches arbitrary callables. Plans are in-memory and single-use.

Install: src/aegisforge/nexus/mission_control/mission_supervised_executor.py
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
import secrets
from threading import RLock
from typing import Any, Iterable

from .mission_registry import NexusMissionRegistry, NexusMissionRegistryError
from ..trust.action_policy import NexusActionPolicy, ActionIntent, ActionDecision
from ..workspace.workspace_policy import IMPACTFUL_OPERATIONS

SUPERVISED_PLAN_SCHEMA = "aegisforge.nexus.mission_control.supervised_plan.v1"
SUPERVISED_REPORT_SCHEMA = "aegisforge.nexus.mission_control.supervised_report.v1"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_SESSION = re.compile(r"^[0-9a-f]{32}$")
_MISSION = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_MAX_ACTIONS = 32
_MAX_SESSIONS = 128
_MAX_BYTES = 65_536
_OUTCOMES = frozenset({"denied", "review_required", "read_only_prepared", "stale"})
_FLAGS = {"integrity_only": True, "authenticity_verified": False,
          "authorization_granted": False, "approval_consumed": False,
          "effects_executed": False}
_PLAN_FIELDS = frozenset({"schema", "session_id", "mission_id", "product", "mission_status",
                          "registry_sha256", "mission_head_sha256", "policy_sha256", "actions",
                          "action_count", "outcome", "integrity_only", "authenticity_verified",
                          "authorization_granted", "approval_consumed", "effects_executed", "plan_sha256"})
_REPORT_FIELDS = frozenset({"schema", "session_id", "mission_id", "product", "plan_sha256",
                            "registry_sha256", "policy_sha256", "action_count", "actions",
                            "outcome", "reasons", "integrity_only", "authenticity_verified",
                            "authorization_granted", "approval_consumed", "effects_executed",
                            "report_sha256"})
_ACTION_FIELDS = frozenset({"index", "intent_sha256", "path_sha256", "tool_id", "operation",
                            "policy_status", "policy_code", "approval_required", "allowed",
                            "payload_sha256", "context_sha256"})


class NexusSupervisedExecutorError(ValueError):
    """Unsafe, stale, invalid, exhausted or unsupported preflight."""


def _require(value: bool, message: str) -> None:
    if not value:
        raise NexusSupervisedExecutorError(message)


def _json(value: Any) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise NexusSupervisedExecutorError("invalid JSON metadata") from exc
    _require(len(raw) <= _MAX_BYTES, "supervised metadata byte budget exceeded")
    return raw


def _digest(value: Any) -> str:
    return sha256(_json(value)).hexdigest()


def _sha_valid(x: Any) -> bool:
    return type(x) is str and _HEX.fullmatch(x) is not None


def _seal(body: dict[str, Any], field: str) -> dict[str, Any]:
    result = {**body, field: _digest(body)}
    _json(result)
    return result


def _flags_ok(data: dict[str, Any]) -> bool:
    return all(data.get(k) is v for k, v in _FLAGS.items())


def _valid_actions(actions: Any, *, count: int) -> bool:
    if type(actions) is not list or len(actions) != count or not 1 <= count <= _MAX_ACTIONS:
        return False
    for i, a in enumerate(actions):
        if type(a) is not dict or set(a) != _ACTION_FIELDS or type(a["index"]) is not int or a["index"] != i:
            return False
        if not (_sha_valid(a["intent_sha256"]) and _sha_valid(a["path_sha256"])):
            return False
        if not all(type(a[k]) is str and len(a[k]) <= 64 for k in ("tool_id", "operation", "policy_status", "policy_code")):
            return False
        if a["policy_status"] not in {"denied", "review_required", "allow_read_only"}:
            return False
        if type(a["allowed"]) is not bool or type(a["approval_required"]) is not bool:
            return False
        if a["allowed"] is not (a["policy_status"] == "allow_read_only"):
            return False
        if a["policy_status"] == "review_required" and (not a["approval_required"] or a["allowed"]):
            return False
        for key in ("payload_sha256", "context_sha256"):
            if a[key] is not None and not _sha_valid(a[key]):
                return False
        if a["policy_status"] != "denied" and (a["payload_sha256"] is None or a["context_sha256"] is None):
            return False
    return True


def _outcome(actions: list[dict[str, Any]]) -> str:
    statuses = {a["policy_status"] for a in actions}
    if "denied" in statuses:
        return "denied"
    if "review_required" in statuses:
        return "review_required"
    return "read_only_prepared"


def verify_supervised_plan(value: Any) -> bool:
    """Structure/hash consistency only. NOT authentication or authorization."""
    try:
        if type(value) is not dict or set(value) != _PLAN_FIELDS or not _flags_ok(value):
            return False
        if value["schema"] != SUPERVISED_PLAN_SCHEMA:
            return False
        if type(value["session_id"]) is not str or not _SESSION.fullmatch(value["session_id"]):
            return False
        if type(value["mission_id"]) is not str or not _MISSION.fullmatch(value["mission_id"]):
            return False
        if value["product"] not in {"nexus", "aegisgrid", "live_forge", "colossus"}:
            return False
        if value["mission_status"] not in {"ready", "running"}:
            return False
        if not all(_sha_valid(value[k]) for k in ("registry_sha256", "mission_head_sha256", "policy_sha256", "plan_sha256")):
            return False
        if type(value["action_count"]) is not int or not _valid_actions(value["actions"], count=value["action_count"]):
            return False
        if value["outcome"] != _outcome(value["actions"]):
            return False
        return hmac.compare_digest(value["plan_sha256"], _digest({k: v for k, v in value.items() if k != "plan_sha256"}))
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError, AttributeError):
        return False


def verify_supervised_report(value: Any, *, plan: Any = None) -> bool:
    """Revalidate report structure and optional exact original plan comparison."""
    try:
        if type(value) is not dict or set(value) != _REPORT_FIELDS or not _flags_ok(value):
            return False
        if value["schema"] != SUPERVISED_REPORT_SCHEMA or value["outcome"] not in _OUTCOMES:
            return False
        if not all(_sha_valid(value[k]) for k in ("plan_sha256", "registry_sha256", "policy_sha256", "report_sha256")):
            return False
        if type(value["session_id"]) is not str or not _SESSION.fullmatch(value["session_id"]):
            return False
        if type(value["mission_id"]) is not str or not _MISSION.fullmatch(value["mission_id"]):
            return False
        if value["product"] not in {"nexus", "aegisgrid", "live_forge", "colossus"}:
            return False
        if type(value["action_count"]) is not int or not _valid_actions(value["actions"], count=value["action_count"]):
            return False
        if type(value["reasons"]) is not list or value["reasons"] != sorted(set(value["reasons"])):
            return False
        if len(value["reasons"]) > 8 or any(type(r) is not str or len(r) > 80 for r in value["reasons"]):
            return False
        if value["outcome"] == "stale":
            if not value["reasons"]:
                return False
        elif value["outcome"] != _outcome(value["actions"]) or value["reasons"]:
            return False
        if plan is not None:
            if not verify_supervised_plan(plan):
                return False
            if any(value[k] != plan[k] for k in ("session_id", "mission_id", "product", "plan_sha256", "action_count")):
                return False
            if value["actions"] != plan["actions"]:
                return False
        return hmac.compare_digest(value["report_sha256"], _digest({k: v for k, v in value.items() if k != "report_sha256"}))
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError, AttributeError):
        return False


@dataclass(frozen=True, slots=True)
class SupervisedActionPlan:
    _bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._bytes)

    @property
    def session_id(self) -> str:
        return self.to_dict()["session_id"]


@dataclass(frozen=True, slots=True)
class SupervisedActionReport:
    _bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._bytes)

    @property
    def outcome(self) -> str:
        return self.to_dict()["outcome"]


class NexusMissionSupervisedExecutor:
    """Safe staging only. No operations, approvals, or effect dispatch occur."""

    def __init__(self, registry: NexusMissionRegistry, policy: NexusActionPolicy,
                 *, max_sessions: int = _MAX_SESSIONS):
        _require(type(registry) is NexusMissionRegistry, "invalid registry")
        _require(type(policy) is NexusActionPolicy, "invalid host action policy")
        _require(type(max_sessions) is int and 1 <= max_sessions <= _MAX_SESSIONS,
                 "invalid session capacity")
        self.registry = registry
        self.policy = policy
        self.max_sessions = max_sessions
        self._lock = RLock()
        self._plans: dict[str, tuple[bytes, tuple[ActionIntent, ...], bool]] = {}

    def _policy_digest(self) -> str:
        # Snapshot policy configuration, never callbacks or secret-bearing roots.
        w = self.policy.workspace_policy
        summary = {
            "workspace_root_sha256": sha256(str(w.workspace.root).encode("utf-8")).hexdigest(),
            "allow_mutations": w.allow_mutations,
            "workspace_rules": sorted({(r.mission_id, r.scope, tuple(sorted(r.operations))) for r in w.rules}),
            "action_rules": sorted({(r.mission_id, r.tool_id, r.operation, r.scope,
                                     tuple(sorted(r.recipes)), tuple(sorted(r.allowed_payload_keys)),
                                     r.max_payload_bytes) for r in self.policy.rules}),
        }
        return _digest(summary)

    def _registry_view(self, mission_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            snap = self.registry.snapshot()
            _require(NexusMissionRegistry.verify_snapshot(snap), "invalid registry snapshot")
            rows = [m for m in snap["missions"] if m["mission_id"] == mission_id]
            _require(len(rows) == 1, "mission not in registry")
            return snap, rows[0]
        except (NexusMissionRegistryError, ValueError, KeyError, TypeError) as exc:
            raise NexusSupervisedExecutorError("mission registry unavailable") from exc

    @staticmethod
    def _intent_sha(intent: ActionIntent) -> str:
        return _digest({"mission_id": intent.mission_id, "tool_id": intent.tool_id,
                        "operation": intent.operation, "relative_path": intent.relative_path,
                        "plan_sha256": intent.plan_sha256, "recipe": intent.recipe,
                        "payload": intent.payload})

    def _decisions(self, intents: tuple[ActionIntent, ...]) -> list[dict[str, Any]]:
        actions = []
        for i, intent in enumerate(intents):
            try:
                decision: ActionDecision = self.policy.evaluate(intent)
            except Exception:
                # No exception detail escapes, especially if it references file paths.
                raise NexusSupervisedExecutorError("trusted policy preflight failed") from None
            _require(decision.status in {"denied", "review_required", "allow_read_only"},
                     "unknown action policy status")
            actions.append({
                "index": i, "intent_sha256": self._intent_sha(intent),
                "path_sha256": sha256(intent.relative_path.encode("utf-8")).hexdigest(),
                "tool_id": intent.tool_id, "operation": intent.operation,
                "policy_status": decision.status,
                "policy_code": decision.code,
                "approval_required": bool(decision.approval_required),
                "allowed": decision.status == "allow_read_only",
                "payload_sha256": decision.payload_sha256,
                "context_sha256": decision.context_sha256,
            })
        _require(_valid_actions(actions, count=len(actions)), "invalid action decisions")
        return actions

    def prepare(self, mission_id: str, intents: Iterable[ActionIntent], *,
                expected_registry_sha256: str) -> SupervisedActionPlan:
        """Build a nonexecuting plan; never consumes an approval or runs a tool."""
        _require(type(mission_id) is str and _MISSION.fullmatch(mission_id) is not None,
                 "invalid mission identifier")
        _require(_sha_valid(expected_registry_sha256), "expected registry anchor required")
        try:
            actions = tuple(intents)
        except (TypeError, OverflowError) as exc:
            raise NexusSupervisedExecutorError("invalid action sequence") from exc
        _require(1 <= len(actions) <= _MAX_ACTIONS, "action count outside safe budget")
        _require(all(type(a) is ActionIntent and a.mission_id == mission_id for a in actions),
                 "action mission mismatch or invalid intent")
        # Validate strict, bounded canonical JSON *before* touching the policies.
        _require(all(type(a.relative_path) is str and len(a.relative_path) <= 512 and
                     type(a.tool_id) is str and len(a.tool_id) <= 64 and
                     type(a.operation) is str and len(a.operation) <= 64
                     for a in actions), "malformed action intent")
        # Bound the *combined* private payload retained in memory per plan.
        _require(sum(len(_json({"path": a.relative_path, "payload": a.payload,
                                "recipe": a.recipe, "plan": a.plan_sha256}))
                     for a in actions) <= _MAX_BYTES, "action batch byte budget exceeded")
        fingerprints = [self._intent_sha(a) for a in actions]
        _require(len(fingerprints) == len(set(fingerprints)), "duplicate action intent")
        with self._lock:
            _require(len(self._plans) < self.max_sessions, "supervised session capacity reached")
            snap, row = self._registry_view(mission_id)
            _require(snap["registry_sha256"] == expected_registry_sha256, "registry anchor is stale")
            _require(row["status"] in {"ready", "running"}, "mission not ready for preflight")
            _require("mission_control" in row["components"], "mission_control component required")
            _require(not any(a.operation in IMPACTFUL_OPERATIONS for a in actions) or
                     "trust" in row["components"], "trust component required for impactful intents")
            policy_sha = self._policy_digest()
            decisions = self._decisions(actions)
            refreshed, latest = self._registry_view(mission_id)
            _require(refreshed["registry_sha256"] == expected_registry_sha256 and
                     latest["head_sha256"] == row["head_sha256"],
                     "registry changed during preparation")
            _require(self._policy_digest() == policy_sha, "policy changed during preparation")
            session_id = secrets.token_hex(16)
            plan = _seal({
                "schema": SUPERVISED_PLAN_SCHEMA, "session_id": session_id,
                "mission_id": mission_id, "product": row["product"],
                "mission_status": row["status"],
                "registry_sha256": expected_registry_sha256,
                "mission_head_sha256": row["head_sha256"], "policy_sha256": policy_sha,
                "actions": decisions, "action_count": len(actions),
                "outcome": _outcome(decisions), **_FLAGS,
            }, "plan_sha256")
            _require(verify_supervised_plan(plan), "internal plan verification failed")
            raw = _json(plan)
            self._plans[session_id] = (raw, actions, False)
            return SupervisedActionPlan(raw)

    def _get(self, session_id: str) -> tuple[bytes, tuple[ActionIntent, ...], bool]:
        _require(type(session_id) is str and _SESSION.fullmatch(session_id) is not None,
                 "invalid session identifier")
        _require(session_id in self._plans, "unknown supervised session")
        return self._plans[session_id]

    def inspect(self, session_id: str) -> SupervisedActionReport:
        """Refresh preflight without changing registry, approvals or session state."""
        with self._lock:
            raw, intents, consumed = self._get(session_id)
            _require(not consumed, "supervised session already finalized")
            plan = json.loads(raw)
            reasons: list[str] = []
            registry_sha = plan["registry_sha256"]
            policy_sha = plan["policy_sha256"]
            actions = plan["actions"]
            try:
                snap, row = self._registry_view(plan["mission_id"])
                if (snap["registry_sha256"] != registry_sha or
                    row["head_sha256"] != plan["mission_head_sha256"] or
                    row["status"] != plan["mission_status"]):
                    reasons.append("registry_changed")
                current_policy_sha = self._policy_digest()
                if current_policy_sha != policy_sha:
                    reasons.append("policy_changed")
                refreshed = self._decisions(intents)
                if refreshed != actions:
                    reasons.append("policy_decisions_changed")
                later, _ = self._registry_view(plan["mission_id"])
                if later["registry_sha256"] != snap["registry_sha256"]:
                    reasons.append("registry_changed_during_inspection")
                if self._policy_digest() != current_policy_sha:
                    reasons.append("policy_changed_during_inspection")
            except Exception:
                # The host source may fail, but its private exception text never
                # belongs in an exported receipt. Fail closed instead.
                reasons.append("source_unavailable")
            if reasons:
                outcome = "stale"
            else:
                outcome = plan["outcome"]
            report = _seal({
                "schema": SUPERVISED_REPORT_SCHEMA,
                "session_id": session_id, "mission_id": plan["mission_id"],
                "product": plan["product"], "plan_sha256": plan["plan_sha256"],
                "registry_sha256": registry_sha, "policy_sha256": policy_sha,
                "action_count": len(actions), "actions": actions,
                "outcome": outcome, "reasons": sorted(set(reasons)), **_FLAGS,
            }, "report_sha256")
            _require(verify_supervised_report(report, plan=plan),
                     "internal supervised receipt verification failed")
            return SupervisedActionReport(_json(report))

    def finalize(self, session_id: str) -> SupervisedActionReport:
        """One-shot close of a preflight session. Never dispatches anything."""
        with self._lock:
            report = self.inspect(session_id)
            raw, intents, _ = self._get(session_id)
            self._plans[session_id] = (raw, intents, True)
            return report

    def forget(self, session_id: str) -> None:
        """Discard internal data; no authority is returned or consumed."""
        with self._lock:
            self._get(session_id)
            del self._plans[session_id]


__all__ = ["SUPERVISED_PLAN_SCHEMA", "SUPERVISED_REPORT_SCHEMA",
           "NexusSupervisedExecutorError", "SupervisedActionPlan", "SupervisedActionReport",
           "NexusMissionSupervisedExecutor", "verify_supervised_plan", "verify_supervised_report"]
