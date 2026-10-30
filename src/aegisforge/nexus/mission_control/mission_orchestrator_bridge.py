"""Safe bridge between original NexusOrchestrator and Colossus Mission Control.

Location: src/aegisforge/nexus/mission_control/mission_orchestrator_bridge.py

* bind_prepared(): binds an already-host-prepared Nexus plan to an authorized
  MissionService read and a precise registry/version anchor.
* prepare(): optionally calls the original planner (which MAY use Nebius), but
  only after explicit opt-in AND an independently injected host policy check.
* preview(): uses the original orchestrator's non-executing preview.
* run_offline(): consumes the original plan exactly once with execute_tools=False.
* proof_snapshot(): builds a NEW in-memory graph from existing evidence.
* link_evidence(): explicitly calls the host-authorized MissionService to link
  the evidence terminal digest; never changes mission state.

NO effectful tool execution. NO automatic authorization. NO persistence, network
service, authentication, signatures or source authenticity. The host owns actor
identity and policy and must not expose the raw orchestrator or registry to
untrusted callers. An original planning client may use a network API in prepare.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from secrets import token_hex
from threading import RLock
from typing import Any, Callable, Mapping, Sequence
import hmac
import json
import re

from .mission_service import NexusMissionService, NexusMissionServiceError
from .mission_registry import NexusMissionRegistryError
from ..orchestrator import (NexusOrchestrator, PreparedNexusPlan,
                            NexusOrchestrationError, NexusOutcome)
from ..proof.proof_graph import NexusProofGraph, NexusProofGraphError

BRIDGE_SCHEMA = "aegisforge.nexus.mission_control.orchestrator_bridge.v1"
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_STATES = frozenset({"draft", "ready", "paused", "awaiting_review"})
_MAX_SESSIONS = 64
_MAX_REPORT = 16384


class NexusMissionOrchestratorBridgeError(ValueError):
    """Unsafe, stale, unauthorized or malformed bridge operation."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise NexusMissionOrchestratorBridgeError(reason)


def _json(value: Any) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise NexusMissionOrchestratorBridgeError("invalid bridge metadata") from exc
    _require(len(raw) <= _MAX_REPORT, "bridge metadata exceeds limit")
    return raw


def _hash(value: Any) -> str:
    return sha256(_json(value)).hexdigest()


def _report(body: dict[str, Any]) -> "BridgeReceipt":
    payload = {**body, "receipt_sha256": _hash(body)}
    return BridgeReceipt(_json(payload))


@dataclass(frozen=True, slots=True)
class BridgeReceipt:
    """Sanitized, immutable JSON-backed receipt: not an authority token."""
    _raw: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._raw)


def verify_bridge_receipt(value: Any) -> bool:
    """Verify structural consistency only; not actor identity or authenticity."""
    try:
        if type(value) is not dict or set(value) != {
            "schema", "phase", "session_id", "mission_id", "product", "actor_sha256",
            "registry_sha256", "mission_head_sha256", "plan_sha256",
            "plan_fingerprint_sha256", "step_count", "tool_step_count",
            "run_id_sha256", "audit_sha256", "audit_event_count",
            "execution_status", "verification_status", "tools_invoked",
            "integrity_only", "authorization_granted", "effects_executed",
            "receipt_sha256",
        }:
            return False
        if value["schema"] != BRIDGE_SCHEMA or value["phase"] not in {
            "bound", "previewed", "completed_read_only",
        }:
            return False
        for field in ("registry_sha256", "mission_head_sha256", "plan_sha256",
                      "plan_fingerprint_sha256", "actor_sha256", "receipt_sha256"):
            if type(value[field]) is not str or _SHA.fullmatch(value[field]) is None:
                return False
        for field in ("run_id_sha256", "audit_sha256"):
            if value[field] is not None and (
                type(value[field]) is not str or _SHA.fullmatch(value[field]) is None
            ):
                return False
        if (type(value["session_id"]) is not str or
            re.fullmatch(r"[a-f0-9]{32}", value["session_id"]) is None or
            type(value["mission_id"]) is not str or _ID.fullmatch(value["mission_id"]) is None or
            value["product"] not in {"nexus", "aegisgrid", "live_forge", "colossus"}):
            return False
        for field in ("step_count", "tool_step_count", "audit_event_count", "tools_invoked"):
            if type(value[field]) is not int or not 0 <= value[field] <= 12:
                return False
        if not (2 <= value["step_count"] <= 12 and
                value["tool_step_count"] <= value["step_count"]):
            return False
        if (value["integrity_only"] is not True or
            value["authorization_granted"] is not False or
            value["effects_executed"] is not False or
            value["tools_invoked"] != 0):
            return False
        valid_execution = {"reported_complete", "failed", "approval_required", "preview", "awaiting_evidence"}
        if value["phase"] == "bound" and value["execution_status"] is not None:
            return False
        if value["phase"] == "previewed" and value["execution_status"] not in valid_execution:
            return False
        if value["phase"] == "completed_read_only":
            if value["execution_status"] not in valid_execution:
                return False
            if (value["audit_event_count"] != 3 or value["audit_sha256"] is None or
                value["run_id_sha256"] is None or value["execution_status"] is None or
                value["verification_status"] not in ("inconclusive", "failed")):
                return False
        elif (value["audit_sha256"] is not None or value["run_id_sha256"] is not None or
              value["audit_event_count"] != 1 or value["verification_status"] is not None):
            return False
        expected = _hash({k: v for k, v in value.items() if k != "receipt_sha256"})
        return hmac.compare_digest(expected, value["receipt_sha256"])
    except (TypeError, KeyError, ValueError, NexusMissionOrchestratorBridgeError):
        return False


@dataclass(slots=True)
class _Session:
    session_id: str
    actor_id: str
    mission_id: str
    product: str
    registry_sha256: str
    mission_head_sha256: str
    plan_sha256: str
    plan_fingerprint_sha256: str
    prepared: PreparedNexusPlan
    outcome: NexusOutcome | None = None
    spent: bool = False
    linked: bool = False


# Host policy callback (actor, mission, task SHA-256, registry SHA-256,
#                       allowed tool IDs SHA-256) -> literal True only.
PlanningAuthorizer = Callable[[str, str, str, str, str], bool]


class NexusMissionOrchestratorBridge:
    """In-memory orchestrator/mission adapter with fail-closed host boundaries.

    Planning is opt-in and potentially billable. Tool callbacks are NEVER
    invoked by this class. Actor IDs are not authenticators: the host-owned
    MissionService and planning callback MUST verify them independently.
    """

    def __init__(self, service: NexusMissionService,
                 orchestrator: NexusOrchestrator, *,
                 planning_authorizer: PlanningAuthorizer | None = None) -> None:
        _require(type(service) is NexusMissionService, "expected MissionService")
        _require(type(orchestrator) is NexusOrchestrator, "expected NexusOrchestrator")
        _require(planning_authorizer is None or callable(planning_authorizer),
                 "invalid planning authorizer")
        self.service = service
        self.orchestrator = orchestrator
        self._planning_authorizer = planning_authorizer
        self._sessions: dict[str, _Session] = {}
        self._lock = RLock()

    def _current(self, mission_id: str, actor_id: str) -> tuple[dict[str, Any], str]:
        _require(type(mission_id) is str and _ID.fullmatch(mission_id) is not None,
                 "invalid mission id")
        _require(type(actor_id) is str and _ID.fullmatch(actor_id) is not None,
                 "invalid actor id")
        try:
            anchor = self.service.registry_version(actor_id=actor_id)
            mission = self.service.get_mission(mission_id, actor_id=actor_id)
            _require(self.service.registry_version(actor_id=actor_id) == anchor,
                     "mission registry changed during read")
        except (NexusMissionServiceError, NexusMissionRegistryError) as exc:
            raise NexusMissionOrchestratorBridgeError("mission access unavailable") from exc
        _require(mission["status"] in _ALLOWED_STATES,
                 "mission status does not permit offline planning")
        _require(type(mission.get("head_sha256")) is str and
                 _SHA.fullmatch(mission["head_sha256"]) is not None,
                 "invalid mission head")
        return mission, anchor

    def _bind(self, mission_id: str, actor_id: str, prepared: PreparedNexusPlan,
              mission: dict[str, Any], anchor: str) -> BridgeReceipt:
        _require(type(prepared) is PreparedNexusPlan, "expected original prepared plan")
        _require(not prepared.consumed, "plan already consumed")
        try:
            # Original API validates owner and plan without invoking registered tools.
            preview = self.orchestrator.preview(prepared)
            _require(preview.tools_invoked == 0, "preview invoked a tool")
            events = prepared.evidence.events
            _require(len(events) == 1 and events[0].stage == "plan", "invalid plan evidence")
            plan_digest = events[0].plan_sha256
            fingerprint = _hash(prepared.plan.to_dict())
            steps = prepared.plan.steps
            _require(2 <= len(steps) <= 12, "invalid step count")
        except (NexusOrchestrationError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise NexusMissionOrchestratorBridgeError("original plan validation failed") from exc
        _require(len(self._sessions) < _MAX_SESSIONS, "session capacity reached")
        _require(self._current(mission_id, actor_id)[1] == anchor,
                 "mission registry changed during plan binding")
        _require(mission["head_sha256"] == self._current(mission_id, actor_id)[0]["head_sha256"],
                 "mission changed during plan binding")
        sid = token_hex(16)
        session = _Session(sid, actor_id, mission_id, mission["product"], anchor,
                           mission["head_sha256"], plan_digest, fingerprint, prepared)
        self._sessions[sid] = session
        return self._receipt(session, "bound")

    def bind_prepared(self, mission_id: str, *, actor_id: str,
                      prepared: PreparedNexusPlan,
                      expected_registry_sha256: str) -> BridgeReceipt:
        """Attach a host-prepared original plan. No model calls, no tools."""
        with self._lock:
            _require(type(expected_registry_sha256) is str and
                     _SHA.fullmatch(expected_registry_sha256) is not None,
                     "invalid expected registry version")
            mission, anchor = self._current(mission_id, actor_id)
            _require(hmac.compare_digest(anchor, expected_registry_sha256),
                     "stale mission registry")
            return self._bind(mission_id, actor_id, prepared, mission, anchor)

    def prepare(self, mission_id: str, *, actor_id: str, task: str,
                expected_registry_sha256: str, allow_model_call: bool = False,
                constraints: Sequence[str] = ()) -> BridgeReceipt:
        """Make ONE original planner request only with explicit host approval.

        The injected planner MAY call Nebius and incur cost. Do not pass this
        method to an unauthenticated client or treat host callback as a stub.
        """
        with self._lock:
            _require(allow_model_call is True, "model planning requires explicit opt-in")
            _require(type(task) is str and 0 < len(task.strip()) <= 10000,
                     "invalid task")
            _require(self._planning_authorizer is not None,
                     "host planning authorizer not configured")
            mission, anchor = self._current(mission_id, actor_id)
            _require(type(expected_registry_sha256) is str and
                     hmac.compare_digest(anchor, expected_registry_sha256),
                     "stale mission registry")
            _require(len(self._sessions) < _MAX_SESSIONS, "session capacity reached")
            task_hash = sha256(task.encode("utf-8")).hexdigest()
            tool_hash = _hash(sorted(self.orchestrator.executor.allowed_tools()))
            try:
                granted = self._planning_authorizer(actor_id, mission_id, task_hash,
                                                     anchor, tool_hash)
            except Exception as exc:
                raise NexusMissionOrchestratorBridgeError("planning authorization unavailable") from exc
            _require(granted is True, "planning authorization denied")
            _require(self._current(mission_id, actor_id)[1] == anchor,
                     "registry changed during planning authorization")
            try:
                prepared = self.orchestrator.prepare(task, constraints=constraints)
            except Exception as exc:
                raise NexusMissionOrchestratorBridgeError("original planner rejected request") from exc
            return self._bind(mission_id, actor_id, prepared, mission, anchor)

    def _get(self, session_id: str, actor_id: str, *, fresh: bool = True) -> _Session:
        _require(type(session_id) is str and re.fullmatch(r"[a-f0-9]{32}", session_id) is not None,
                 "invalid session id")
        try:
            session = self._sessions[session_id]
        except KeyError as exc:
            raise NexusMissionOrchestratorBridgeError("unknown session") from exc
        _require(actor_id == session.actor_id, "session actor mismatch")
        current, anchor = self._current(session.mission_id, actor_id)
        if fresh:
            _require(hmac.compare_digest(session.registry_sha256, anchor) and
                     hmac.compare_digest(session.mission_head_sha256, current["head_sha256"]),
                     "session stale after registry change")
        _require(session.plan_fingerprint_sha256 == _hash(session.prepared.plan.to_dict()) and
                 session.plan_sha256 == session.prepared.evidence.events[0].plan_sha256,
                 "prepared plan mutated")
        return session

    def _receipt(self, session: _Session, phase: str,
                 *, execution_status: str | None = None,
                 verification_status: str | None = None) -> BridgeReceipt:
        evidence = session.prepared.evidence
        body = {
            "schema": BRIDGE_SCHEMA, "phase": phase,
            "session_id": session.session_id, "mission_id": session.mission_id,
            "product": session.product,
            "actor_sha256": sha256(session.actor_id.encode("utf-8")).hexdigest(),
            "registry_sha256": session.registry_sha256,
            "mission_head_sha256": session.mission_head_sha256,
            "plan_sha256": session.plan_sha256,
            "plan_fingerprint_sha256": session.plan_fingerprint_sha256,
            "step_count": len(session.prepared.plan.steps),
            "tool_step_count": sum(s.kind == "tool" for s in session.prepared.plan.steps),
            "run_id_sha256": sha256(evidence.run_id.encode("ascii")).hexdigest()
                if phase == "completed_read_only" else None,
            "audit_sha256": evidence.final_sha256 if phase == "completed_read_only" else None,
            "audit_event_count": len(evidence.events),
            "execution_status": execution_status,
            "verification_status": verification_status,
            "tools_invoked": 0, "integrity_only": True,
            "authorization_granted": False, "effects_executed": False,
        }
        result = _report(body)
        _require(verify_bridge_receipt(result.to_dict()), "receipt invariant failed")
        return result

    def preview(self, session_id: str, *, actor_id: str) -> BridgeReceipt:
        """Read-only preview; original host validators must be pure."""
        with self._lock:
            session = self._get(session_id, actor_id)
            _require(not session.spent and not session.prepared.consumed,
                     "session already spent")
            try:
                preview = self.orchestrator.preview(session.prepared)
                _require(preview.tools_invoked == 0, "preview invoked tools")
                status = preview.status
            except Exception as exc:
                raise NexusMissionOrchestratorBridgeError("original preview unavailable") from exc
            self._get(session_id, actor_id)
            return self._receipt(session, "previewed", execution_status=status)

    def run_offline(self, session_id: str, *, actor_id: str) -> BridgeReceipt:
        """Consume the original plan with NO tool execution or manual evidence."""
        with self._lock:
            session = self._get(session_id, actor_id)
            _require(not session.spent and not session.prepared.consumed,
                     "session already spent")
            session.spent = True  # never retry after callback or unexpected failure
            try:
                outcome = self.orchestrator.run(session.prepared, execute_tools=False,
                                                approved_mutations=frozenset(),
                                                tool_arguments=None, manual_evidence=None,
                                                independent_checks=(), expectations=())
                _require(outcome.execution.tools_invoked == 0, "unexpected tool execution")
                _require(outcome.status != "verified", "verification without independent checks")
                _require(len(outcome.evidence.events) == 3, "incomplete evidence trail")
                _require(outcome.evidence.events[0].plan_sha256 == session.plan_sha256,
                         "unexpected outcome plan")
                session.outcome = outcome
                self._get(session_id, actor_id)
                return self._receipt(session, "completed_read_only",
                                     execution_status=outcome.execution.status,
                                     verification_status=outcome.status)
            except Exception as exc:
                raise NexusMissionOrchestratorBridgeError("offline orchestration failed; plan consumed") from exc

    def proof_snapshot(self, session_id: str, *, actor_id: str) -> dict[str, Any]:
        """Build a fresh in-memory ProofGraph; no external graph is modified."""
        with self._lock:
            session = self._get(session_id, actor_id, fresh=False)
            _require(session.outcome is not None, "no completed outcome")
            try:
                graph = NexusProofGraph(session.mission_id)
                graph.ingest_evidence_trail(session.outcome.evidence)
                snapshot = graph.snapshot()
                _require(NexusProofGraph.verify_snapshot(snapshot),
                         "derived proof graph failed validation")
                return snapshot
            except NexusProofGraphError as exc:
                raise NexusMissionOrchestratorBridgeError("proof graph derivation unavailable") from exc

    def link_evidence(self, session_id: str, *, actor_id: str,
                      expected_registry_sha256: str) -> dict[str, Any]:
        """Explicitly link completed evidence through the host-authorized Service.

        This records a digest reference only. It does NOT attest to source
        authenticity, grant execution rights or change mission status.
        """
        with self._lock:
            session = self._get(session_id, actor_id)
            _require(session.outcome is not None and not session.linked,
                     "no unlinked completed outcome")
            _require(expected_registry_sha256 == session.registry_sha256,
                     "stale expected registry version")
            _require(session.outcome.evidence.final_sha256 ==
                     session.prepared.evidence.final_sha256,
                     "evidence changed")
            try:
                result = self.service.link_reference(
                    session.mission_id, kind="evidence",
                    reference_sha256=session.outcome.evidence.final_sha256,
                    actor_id=actor_id, expected_registry_sha256=expected_registry_sha256)
            except NexusMissionServiceError as exc:
                raise NexusMissionOrchestratorBridgeError("evidence link authorization rejected") from exc
            session.linked = True
            return result

    def session_count(self) -> int:
        """Number of transient sessions. No raw plan information is returned."""
        with self._lock:
            return len(self._sessions)


__all__ = ["BRIDGE_SCHEMA", "BridgeReceipt", "NexusMissionOrchestratorBridge",
           "NexusMissionOrchestratorBridgeError", "PlanningAuthorizer",
           "verify_bridge_receipt"]
