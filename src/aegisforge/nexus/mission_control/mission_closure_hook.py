"""Supervised mission closure -> registry checkpoint -> archived display sources.

This is a *coordinator*, not a transaction manager or execution permission.
The host must authenticate actors, serialize all registry writers, own a private
checkpoint directory, and provide independent authorization for Service
transitions, checkpoint exports and archive capture. SHA-256 is consistency
metadata, never proof of authenticity. No network/tool/shell operations.

Boundary and failure rules:
- prepare is read-only and provides NO authorization;
- commit consumes its plan even on denial/failure;
- transition, checkpoint and source archive are separate irreversible stages;
- a failure returns a partial receipt; never rolls back a transition or uses a
  previous checkpoint silently;
- resume_archive is explicit, reauthorized, and only for an already-current
  checkpoint matching a closed live mission;
- archive captures historical Trust metadata but NEVER restores permissions.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import hmac
import json
import os
import re
from threading import RLock
from typing import Any, Mapping

from .mission_service import NexusMissionService
from .mission_state import NexusMissionState, verify_assessment
from .mission_persistence import NexusMissionPersistence, NexusMissionPersistenceError
from .mission_monitor_views import NexusMissionMonitorViews
from .mission_source_capture import NexusMissionSourceCapture
from .mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from .mission_source_archive import NexusMissionSourceArchive, _NAME, _frozen, _validate_presentation

MISSION_CLOSURE_HOOK_SCHEMA = "aegisforge.nexus.mission_control.closure_hook.v1"
_FINAL = frozenset({"reported_complete", "failed", "cancelled", "verified"})
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_OUTCOMES = frozenset({"completed", "transition_failed", "checkpoint_failed", "archive_failed"})
_FIELDS = frozenset({
    "schema", "mission_id", "target_status", "actor_sha256", "plan_sha256",
    "registry_before_sha256", "registry_after_sha256", "transition_recorded",
    "checkpoint_saved", "archive_saved", "checkpoint_generation",
    "checkpoint_envelope_sha256", "archive_sha256", "capture_report_sha256",
    "outcome", "requires_operator_reconciliation", "trust_permissions_restored",
    "authorization_granted_by_report", "effects_executed", "authenticity_verified",
    "receipt_sha256",
})
_MAX_PLANS = 64


class NexusMissionClosureError(ValueError):
    """Unsafe, stale, unauthorized, or invalid closure request."""


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise NexusMissionClosureError(message)


def _json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise NexusMissionClosureError("invalid closure metadata") from exc


def _digest(value: Any) -> str:
    return sha256(_json(value)).hexdigest()


def _actor(actor_id: str) -> str:
    _require(type(actor_id) is str and _ID.fullmatch(actor_id) is not None,
             "invalid authenticated actor identifier")
    return sha256(actor_id.encode("utf-8")).hexdigest()


def _id(mission_id: str) -> str:
    _require(type(mission_id) is str and _ID.fullmatch(mission_id) is not None,
             "invalid mission identifier")
    return mission_id


def _sha(value: Any) -> str:
    _require(type(value) is str and _SHA.fullmatch(value) is not None,
             "invalid digest")
    return value


class NexusMissionClosureHook:
    """Host-controlled, checkpoint-bound closure workflow.

    Host must inject a trusted NexusMissionService whose registry is exclusively
    mutated through that service, a matching NexusMissionPersistence, and live
    NexusMissionMonitorViews. This class cannot guarantee cross-process
    isolation; it holds the service's in-process lock over closure stages.
    """

    def __init__(self, service: NexusMissionService,
                 store: NexusMissionPersistence,
                 views: NexusMissionMonitorViews, *,
                 capture_authorizer: Any,
                 max_plans: int = _MAX_PLANS) -> None:
        _require(type(service) is NexusMissionService, "expected mission service")
        _require(type(store) is NexusMissionPersistence, "expected checkpoint store")
        _require(type(views) is NexusMissionMonitorViews, "expected monitor views")
        _require(callable(capture_authorizer), "independent capture authorizer required")
        _require(type(max_plans) is int and 1 <= max_plans <= 128,
                 "invalid plan budget")
        self._service = service
        self._store = store
        self._views = views
        self._capture_authorizer = capture_authorizer
        self._max_plans = max_plans
        self._plans: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def _snapshot(self, actor_id: str) -> dict[str, Any]:
        # Access-control check of the entire snapshot is delegated to service.
        try:
            return self._service.export_snapshot(actor_id=actor_id)
        except Exception as exc:
            raise NexusMissionClosureError("host denied or failed registry export") from exc

    def _persisted_head(self) -> tuple[int, str, str] | None:
        # Missing vs corrupt is not interchangeable: .prev alone is also unsafe.
        primary = self._store._primary
        previous = self._store._previous
        if not os.path.lexists(primary) and not os.path.lexists(previous):
            return None
        try:
            loaded = self._store.load()  # corrupt/missing primary must fail closed
        except Exception as exc:
            raise NexusMissionClosureError("current checkpoint not safely readable") from exc
        return (loaded.receipt.generation, loaded.receipt.envelope_sha256,
                loaded.receipt.registry_sha256)

    def _preflight_sources(self, mission_id: str) -> str:
        # Exact same display-shape verification as source_capture, without writes.
        found: dict[str, dict[str, Any]] = {}
        for kind, fn in (("graph", self._views.graph),
                         ("laboratory", self._views.laboratory),
                         ("trust", self._views.trust)):
            try:
                data = fn(mission_id)
                _require(type(data) is dict and type(data.get("available")) is bool,
                         "invalid live source response")
                if data["available"]:
                    frozen = _frozen(kind, data)
                    _validate_presentation(kind, mission_id, frozen)
                    found[kind] = frozen
            except NexusMissionClosureError:
                raise
            except Exception as exc:
                raise NexusMissionClosureError("live source could not be validated") from exc
        _require(bool(found), "closure requires at least one verified display source")
        return _digest(found)

    def _check_archive_absent(self) -> None:
        _require(not os.path.lexists(self._store._root / _NAME),
                 "source archive already exists; explicit migration required")

    def _validate_before(self, mission_id: str, target_status: str,
                         actor_id: str, state: NexusMissionState,
                         decision_sha256: str | None) -> tuple[dict[str, Any], str, str, tuple[int, str, str] | None]:
        _id(mission_id)
        _actor(actor_id)
        _require(type(target_status) is str and target_status in _FINAL,
                 "target is not a terminal closure status")
        _require(type(state) is NexusMissionState and
                 state.policy.mission_id == mission_id,
                 "host policy missing or bound to another mission")
        if target_status == "verified":
            _sha(decision_sha256)
        else:
            _require(decision_sha256 is None,
                     "verification decision only valid for verified transition")
        self._check_archive_absent()
        current = self._snapshot(actor_id)
        row = next((m for m in current["missions"] if m["mission_id"] == mission_id), None)
        _require(row is not None and row["product"] == state.policy.product,
                 "mission absent or policy product mismatch")
        try:
            assessment = state.assess(self._service._registry, target_status).to_dict()
        except Exception as exc:
            raise NexusMissionClosureError("mission readiness assessment unavailable") from exc
        _require(verify_assessment(assessment) and
                 assessment["outcome"] == "ready_for_host_review" and
                 assessment["registry_sha256"] == current["registry_sha256"],
                 "mission is not ready for supervised closure")
        existing = self._persisted_head()
        if existing is not None:
            _require(hmac.compare_digest(existing[2], current["registry_sha256"]),
                     "live registry differs from current checkpoint")
        sources = self._preflight_sources(mission_id)
        return current, sources, assessment["assessment_sha256"], existing

    def prepare(self, mission_id: str, target_status: str, *, actor_id: str,
                state: NexusMissionState, decision_sha256: str | None = None) -> dict[str, Any]:
        """Read-only closure plan. An approved plan is NOT an execution token."""
        with self._lock, self._service._lock:
            _require(len(self._plans) < self._max_plans,
                     "too many outstanding closure plans")
            current, src_digest, assessment_sha, prev = self._validate_before(
                mission_id, target_status, actor_id, state, decision_sha256)
            body = {
                "schema": MISSION_CLOSURE_HOOK_SCHEMA,
                "mission_id": mission_id,
                "target_status": target_status,
                "actor_sha256": _actor(actor_id),
                "registry_before_sha256": current["registry_sha256"],
                "assessment_sha256": assessment_sha,
                "state_policy_sha256": state.policy.sha256,
                "sources_sha256": src_digest,
                "prior_checkpoint_generation": prev[0] if prev else None,
                "prior_checkpoint_envelope_sha256": prev[1] if prev else None,
                "decision_sha256": decision_sha256,
                "host_authorization_required": True,
                "authorization_granted": False,
                "trust_permissions_restored": False,
                "effects_executed": False,
            }
            plan = {**body, "plan_sha256": _digest(body)}
            _require(plan["plan_sha256"] not in self._plans,
                     "closure plan already prepared")
            self._plans[plan["plan_sha256"]] = deepcopy(plan)
            return deepcopy(plan)

    def _receipt(self, plan: Mapping[str, Any], status: str, *,
                 after: str, transitioned: bool, checkpoint: bool,
                 archived: bool, generation: int | None = None,
                 envelope: str | None = None, archive_sha: str | None = None,
                 capture_sha: str | None = None) -> dict[str, Any]:
        data = {
            "schema": MISSION_CLOSURE_HOOK_SCHEMA,
            "mission_id": plan["mission_id"],
            "target_status": plan["target_status"],
            "actor_sha256": plan["actor_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "registry_before_sha256": plan["registry_before_sha256"],
            "registry_after_sha256": after,
            "transition_recorded": transitioned,
            "checkpoint_saved": checkpoint,
            "archive_saved": archived,
            "checkpoint_generation": generation,
            "checkpoint_envelope_sha256": envelope,
            "archive_sha256": archive_sha,
            "capture_report_sha256": capture_sha,
            "outcome": status,
            "requires_operator_reconciliation": status != "completed",
            "trust_permissions_restored": False,
            "authorization_granted_by_report": False,
            "effects_executed": False,
            "authenticity_verified": False,
        }
        return {**data, "receipt_sha256": _digest(data)}

    def commit(self, plan: Mapping[str, Any], *, actor_id: str,
               state: NexusMissionState) -> dict[str, Any]:
        """Consume plan and attempt ordered stages; return precise partial receipt.

        A denial before a transition leaves the registry unchanged. A failure
        after the transition must be inspected by the host (never rolled back).
        """
        actor_sha = _actor(actor_id)
        with self._lock, self._service._lock:
            _require(type(plan) is dict and type(plan.get("plan_sha256")) is str,
                     "invalid closure plan")
            key = plan["plan_sha256"]
            _require(key in self._plans and plan == self._plans[key],
                     "closure plan altered, unknown or already used")
            saved = self._plans.pop(key)  # single-use even on rejection
            _require(saved["actor_sha256"] == actor_sha, "closure actor mismatch")
            _require(type(state) is NexusMissionState and
                     state.policy.sha256 == saved["state_policy_sha256"],
                     "closure state policy changed")
            current, src, ass, prev = self._validate_before(
                saved["mission_id"], saved["target_status"], actor_id,
                state, saved["decision_sha256"])
            _require(current["registry_sha256"] == saved["registry_before_sha256"] and
                     src == saved["sources_sha256"] and
                     ass == saved["assessment_sha256"] and
                     (prev[0] if prev else None) == saved["prior_checkpoint_generation"] and
                     (prev[1] if prev else None) == saved["prior_checkpoint_envelope_sha256"],
                     "closure plan stale")
            transition = False
            checkpoint = False
            generation: int | None = None
            envelope: str | None = None
            after = saved["registry_before_sha256"]
            try:
                self._service.transition_mission(
                    saved["mission_id"], saved["target_status"], actor_id=actor_id,
                    expected_registry_sha256=after, state=state,
                    decision_sha256=saved["decision_sha256"])
                transition = True
            except Exception:
                # Even a failed call could be interrupted by concurrent external writers.
                after = self._service._registry.snapshot()["registry_sha256"]
                return self._receipt(saved, "transition_failed", after=after,
                                     transitioned=False, checkpoint=False, archived=False)
            after = self._service._registry.snapshot()["registry_sha256"]
            try:
                saved_checkpoint = self._store.checkpoint_service(
                    self._service, actor_id=actor_id,
                    expected_head_sha256=prev[1] if prev else None)
                checkpoint = True
                generation = saved_checkpoint.generation
                envelope = saved_checkpoint.envelope_sha256
                _require(saved_checkpoint.registry_sha256 == after,
                         "checkpoint does not match transitioned mission")
            except Exception:
                return self._receipt(saved, "checkpoint_failed", after=after,
                                     transitioned=transition, checkpoint=False, archived=False)
            try:
                capture = NexusMissionSourceCapture(
                    self._store, self._views, authorizer=self._capture_authorizer)
                capture_plan = capture.prepare([saved["mission_id"]], actor_id=actor_id)
                result = capture.commit(capture_plan, actor_id=actor_id)
                return self._receipt(saved, "completed", after=after,
                                     transitioned=True, checkpoint=True, archived=True,
                                     generation=generation, envelope=envelope,
                                     archive_sha=result["archive_sha256"],
                                     capture_sha=result["capture_report_sha256"])
            except Exception:
                return self._receipt(saved, "archive_failed", after=after,
                                     transitioned=True, checkpoint=True, archived=False,
                                     generation=generation, envelope=envelope)

    def resume_archive(self, mission_id: str, *, actor_id: str) -> dict[str, Any]:
        """Explicitly retry ONLY archival after independently authorized closure.

        Requires checkpoint and live registry to match exactly; denies if the
        primary is missing, changed or corrupt. It never performs a transition,
        rollback, checkpoint write, or automatic authorization.
        """
        _id(mission_id)
        _actor(actor_id)
        with self._lock, self._service._lock:
            self._check_archive_absent()
            snapshot = self._snapshot(actor_id)
            row = next((m for m in snapshot["missions"] if m["mission_id"] == mission_id), None)
            _require(row is not None and row["status"] in _FINAL,
                     "mission is not durably closed")
            head = self._persisted_head()
            _require(head is not None and head[2] == snapshot["registry_sha256"],
                     "checkpoint and live registry do not match")
            source_sha = self._preflight_sources(mission_id)
            capture = NexusMissionSourceCapture(
                self._store, self._views, authorizer=self._capture_authorizer)
            plan = capture.prepare([mission_id], actor_id=actor_id)
            # The independent capture authorizer must authenticate exact binding.
            result = capture.commit(plan, actor_id=actor_id)
            body = {"schema": MISSION_CLOSURE_HOOK_SCHEMA,
                    "outcome": "archive_resumed", "mission_id": mission_id,
                    "actor_sha256": _actor(actor_id),
                    "sources_sha256": source_sha,
                    "checkpoint_generation": head[0],
                    "checkpoint_envelope_sha256": head[1],
                    "archive_sha256": result["archive_sha256"],
                    "capture_report_sha256": result["capture_report_sha256"],
                    "trust_permissions_restored": False,
                    "effects_executed": False, "authenticity_verified": False}
            return {**body, "resumption_receipt_sha256": _digest(body)}

    @staticmethod
    def verify_resumption_receipt(receipt: Mapping[str, Any]) -> bool:
        try:
            fields = {"schema", "outcome", "mission_id", "actor_sha256",
                      "sources_sha256", "checkpoint_generation",
                      "checkpoint_envelope_sha256", "archive_sha256",
                      "capture_report_sha256", "trust_permissions_restored",
                      "effects_executed", "authenticity_verified", "resumption_receipt_sha256"}
            if type(receipt) is not dict or set(receipt) != fields:
                return False
            if (receipt["schema"] != MISSION_CLOSURE_HOOK_SCHEMA or
                    receipt["outcome"] != "archive_resumed" or
                    not all(receipt[k] is False for k in
                            ("trust_permissions_restored", "effects_executed", "authenticity_verified"))):
                return False
            _id(receipt["mission_id"])
            if (type(receipt["checkpoint_generation"]) is not int or
                    receipt["checkpoint_generation"] < 1):
                return False
            for field in ("actor_sha256", "sources_sha256", "checkpoint_envelope_sha256",
                          "archive_sha256", "capture_report_sha256", "resumption_receipt_sha256"):
                _sha(receipt[field])
            body = {k:v for k,v in receipt.items() if k != "resumption_receipt_sha256"}
            return hmac.compare_digest(_digest(body), receipt["resumption_receipt_sha256"])
        except (NexusMissionClosureError, KeyError, TypeError, ValueError,
                RecursionError, OverflowError):
            return False

    @staticmethod
    def verify_receipt(receipt: Mapping[str, Any]) -> bool:
        """Structural integrity only. No authenticity or host authorization proof."""
        try:
            if type(receipt) is not dict or set(receipt) != _FIELDS or len(_json(receipt)) > 8192:
                return False
            if receipt["schema"] != MISSION_CLOSURE_HOOK_SCHEMA or receipt["outcome"] not in _OUTCOMES:
                return False
            _id(receipt["mission_id"])
            for key in ("registry_before_sha256", "registry_after_sha256",
                        "plan_sha256", "actor_sha256", "receipt_sha256"):
                _sha(receipt[key])
            if receipt["target_status"] not in _FINAL or not all(
                receipt[k] is False for k in ("trust_permissions_restored", "authorization_granted_by_report",
                                              "effects_executed", "authenticity_verified")):
                return False
            if any(type(receipt[k]) is not bool for k in
                   ("transition_recorded", "checkpoint_saved", "archive_saved",
                    "requires_operator_reconciliation")):
                return False
            if receipt["requires_operator_reconciliation"] != (receipt["outcome"] != "completed"):
                return False
            if receipt["outcome"] == "completed":
                if not all(receipt[k] is True for k in
                           ("transition_recorded", "checkpoint_saved", "archive_saved")):
                    return False
            if receipt["outcome"] == "archive_failed" and (
                    receipt["transition_recorded"] is not True or
                    receipt["checkpoint_saved"] is not True or receipt["archive_saved"] is not False):
                return False
            if receipt["outcome"] == "checkpoint_failed" and (
                    receipt["transition_recorded"] is not True or receipt["checkpoint_saved"] is not False):
                return False
            if receipt["outcome"] == "transition_failed" and (
                    receipt["transition_recorded"] is not False or receipt["checkpoint_saved"] is not False):
                return False
            if receipt["checkpoint_saved"]:
                if type(receipt["checkpoint_generation"]) is not int or not (1 <= receipt["checkpoint_generation"] < 2**63):
                    return False
                _sha(receipt["checkpoint_envelope_sha256"])
            elif receipt["checkpoint_generation"] is not None or receipt["checkpoint_envelope_sha256"] is not None:
                return False
            if receipt["archive_saved"]:
                _sha(receipt["archive_sha256"])
                _sha(receipt["capture_report_sha256"])
            elif receipt["archive_sha256"] is not None or receipt["capture_report_sha256"] is not None:
                return False
            body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
            return hmac.compare_digest(_digest(body), receipt["receipt_sha256"])
        except (NexusMissionClosureError, TypeError, ValueError, KeyError,
                RecursionError, OverflowError):
            return False


__all__ = ["MISSION_CLOSURE_HOOK_SCHEMA", "NexusMissionClosureError",
           "NexusMissionClosureHook"]
