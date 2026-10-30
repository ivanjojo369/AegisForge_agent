"""Read-only workflow preview for AegisForge Nexus Mission Control.

Destination: src/aegisforge/nexus/mission_control/mission_coordinator.py

This is a *declarative* coordinator. It does not execute tasks, change mission
state, call models/tools, make network calls, check workspace contents, or
approve/consume permissions. A Trust review node models a REQUIRED dependency;
its mere presence is NEVER evidence that an action was approved.

A host MUST authenticate blueprints, policies and observations, check that the
actual registry and per-action approval remain current, apply NexusActionPolicy
and NexusApprovalGate for each exact effect, and enforce permissions at the
execution boundary. SHA-256 detects accidental/inconsistent changes to metadata;
it is NOT authentication, an attestation, or a capability token.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Mapping

from .mission_registry import (
    COMPONENTS, PRODUCTS, STATUSES, MissionRegistryLimits,
    NexusMissionRegistry, NexusMissionRegistryError,
)
from .mission_state import (
    NexusMissionState, NexusMissionStateError, verify_assessment,
)

MISSION_COORDINATION_SCHEMA = "aegisforge.nexus.mission_control.coordination.v1"
_STEP_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
_MISSION_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_MAX_STEPS = 64
_MAX_DEPENDENCIES_PER_STEP = 12
_MAX_EDGES = 256
_MAX_LAYERS = 64
_MAX_REPORT_BYTES = 131_072

# Closed, metadata-only operation vocabulary. "request_effect" is a PROPOSAL,
# not a dispatch instruction. It always needs a Trust review as an ancestor,
# plus a separate host-bound per-action approval before execution.
_OPERATIONS_BY_COMPONENT: dict[str, frozenset[str]] = {
    "workspace": frozenset({"inventory", "index"}),
    "intelligence": frozenset({"plan", "reason"}),
    "engineering": frozenset({"inspect_impact", "propose_change", "request_effect"}),
    "trust": frozenset({"review_action", "review_transition"}),
    "proof": frozenset({"collect_evidence", "verify_integrity"}),
    "lab": frozenset({"evaluate", "summarize"}),
    "mission_control": frozenset({"coordinate", "summarize_mission"}),
    "aegisgrid": frozenset({"plan_energy", "simulate_energy", "request_effect"}),
    "live_forge": frozenset({"draft_overlay", "plan_stream", "request_effect"}),
}
_EFFECT_OP = "request_effect"
_REVIEW_OP = "review_action"
_REPORT_FIELDS = frozenset({
    "schema", "mission_id", "product", "target_status", "outcome", "reasons",
    "missing_components", "blueprint", "blueprint_sha256", "state_assessment",
    "state_assessment_sha256", "registry_sha256", "mission_head_sha256",
    "policy_sha256", "planned_layers", "required_action_reviews",
    "integrity_only", "authenticity_verified", "authorization_granted",
    "effects_executed", "coordination_sha256",
})


class NexusMissionCoordinatorError(ValueError):
    """Invalid workflow definition, registry source, or preview."""


def _require(predicate: bool, reason: str) -> None:
    if not predicate:
        raise NexusMissionCoordinatorError(reason)


def _encode(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionCoordinatorError("invalid JSON metadata") from exc


def _digest(value: Any) -> str:
    return sha256(_encode(value)).hexdigest()


def _check_step_id(value: Any, label: str = "step") -> str:
    _require(type(value) is str and _STEP_ID.fullmatch(value) is not None,
             f"invalid {label} ID")
    return value


def _check_digest(value: Any, label: str) -> str:
    _require(type(value) is str and _SHA.fullmatch(value) is not None,
             f"invalid {label} SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class MissionWorkflowStep:
    """One *non-executable*, explicitly typed planned work item.

    A request_effect step MUST have an intent digest (bound by the trusted host
    to an exact action) and an upstream trust:review_action step. This still
    neither approves the action nor asserts that the reviewer agreed.
    """

    step_id: str
    component: str
    operation: str
    depends_on: tuple[str, ...] = ()
    intent_sha256: str | None = None

    def __post_init__(self) -> None:
        _check_step_id(self.step_id)
        _require(type(self.component) is str and self.component in COMPONENTS,
                 "invalid workflow component")
        _require(type(self.operation) is str and self.operation in
                 _OPERATIONS_BY_COMPONENT[self.component],
                 "invalid operation for component")
        _require(type(self.depends_on) is tuple and
                 len(self.depends_on) <= _MAX_DEPENDENCIES_PER_STEP,
                 "invalid step dependencies")
        for dependency in self.depends_on:
            _check_step_id(dependency, "dependency")
        _require(tuple(sorted(set(self.depends_on))) == self.depends_on,
                 "dependencies must be sorted and unique")
        _require(self.step_id not in self.depends_on, "self-dependency forbidden")
        if self.operation == _EFFECT_OP:
            _check_digest(self.intent_sha256, "action intent")
        else:
            _require(self.intent_sha256 is None,
                     "only effect proposals carry action intent digests")

    def to_dict(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "component": self.component,
                "operation": self.operation, "depends_on": list(self.depends_on),
                "intent_sha256": self.intent_sha256}


@dataclass(frozen=True, slots=True)
class MissionCoordinationBlueprint:
    """Immutable, host-supplied, bounded workflow DAG for one target transition."""

    mission_id: str
    product: str
    target_status: str
    steps: tuple[MissionWorkflowStep, ...]

    def __post_init__(self) -> None:
        # Mission IDs have the same public syntax as MissionStatePolicy.
        _require(type(self.mission_id) is str and
                 _MISSION_ID.fullmatch(self.mission_id) is not None,
                 "invalid mission ID")
        _require(type(self.product) is str and self.product in PRODUCTS,
                 "invalid workflow product")
        _require(type(self.target_status) is str and self.target_status in STATUSES
                 and self.target_status != "draft", "invalid workflow target")
        _require(type(self.steps) is tuple and 1 <= len(self.steps) <= _MAX_STEPS,
                 "workflow needs a bounded set of steps")
        _require(all(type(s) is MissionWorkflowStep for s in self.steps),
                 "invalid workflow step type")
        ids = [s.step_id for s in self.steps]
        _require(len(set(ids)) == len(ids), "duplicate workflow step ID")
        _require(sum(len(s.depends_on) for s in self.steps) <= _MAX_EDGES,
                 "workflow dependency budget exceeded")
        by_id = {s.step_id: s for s in self.steps}
        _require(all(all(dep in by_id for dep in s.depends_on) for s in self.steps),
                 "workflow has an unknown dependency")
        layers = _layers(by_id)
        _require(len(layers) <= _MAX_LAYERS, "workflow exceeds layer budget")
        for step in self.steps:
            if step.operation == _EFFECT_OP:
                ancestors = _ancestors(step.step_id, by_id)
                _require(any(by_id[ancestor].component == "trust" and
                             by_id[ancestor].operation == _REVIEW_OP
                             for ancestor in ancestors),
                         "effect proposal requires an upstream Trust review step")

    def to_dict(self) -> dict[str, Any]:
        return {"mission_id": self.mission_id, "product": self.product,
                "target_status": self.target_status,
                "steps": [s.to_dict() for s in sorted(self.steps, key=lambda s: s.step_id)]}

    @property
    def sha256(self) -> str:
        return _digest(self.to_dict())

    @property
    def required_components(self) -> tuple[str, ...]:
        return tuple(sorted({step.component for step in self.steps}))

    @property
    def planned_layers(self) -> tuple[tuple[str, ...], ...]:
        return _layers({step.step_id: step for step in self.steps})

    @property
    def required_action_reviews(self) -> tuple[dict[str, str], ...]:
        return tuple({"step_id": s.step_id, "intent_sha256": s.intent_sha256}
                     for s in sorted(self.steps, key=lambda s: s.step_id)
                     if s.operation == _EFFECT_OP)


def _ancestors(step_id: str, by_id: Mapping[str, MissionWorkflowStep]) -> set[str]:
    seen: set[str] = set()
    pending = list(by_id[step_id].depends_on)
    while pending:
        ident = pending.pop()
        if ident in seen:
            continue
        seen.add(ident)
        pending.extend(by_id[ident].depends_on)
    return seen


def _layers(by_id: Mapping[str, MissionWorkflowStep]) -> tuple[tuple[str, ...], ...]:
    """Deterministic, stable topological parallel waves (never executes waves)."""
    remaining = set(by_id)
    done: set[str] = set()
    layers: list[tuple[str, ...]] = []
    while remaining:
        wave = tuple(sorted(ident for ident in remaining
                            if set(by_id[ident].depends_on) <= done))
        _require(bool(wave), "workflow dependency cycle")
        layers.append(wave)
        done.update(wave)
        remaining.difference_update(wave)
    return tuple(layers)


@dataclass(frozen=True, slots=True)
class MissionCoordinationPreview:
    """Immutable evidence of a preview; absolutely NOT a workflow capability."""

    _payload: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._payload)

    @property
    def outcome(self) -> str:
        return self.to_dict()["outcome"]

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(self.to_dict()["reasons"])


def _blueprint_from_dict(data: Any) -> MissionCoordinationBlueprint:
    _require(type(data) is dict and set(data) ==
             {"mission_id", "product", "target_status", "steps"},
             "invalid blueprint body")
    rows = data["steps"]
    _require(type(rows) is list and 1 <= len(rows) <= _MAX_STEPS,
             "invalid blueprint steps")
    steps = []
    for row in rows:
        _require(type(row) is dict and set(row) ==
                 {"step_id", "component", "operation", "depends_on", "intent_sha256"},
                 "invalid workflow step body")
        _require(type(row["depends_on"]) is list, "invalid dependencies")
        steps.append(MissionWorkflowStep(
            row["step_id"], row["component"], row["operation"],
            tuple(row["depends_on"]), row["intent_sha256"]))
    blueprint = MissionCoordinationBlueprint(data["mission_id"], data["product"],
                                             data["target_status"], tuple(steps))
    _require(blueprint.to_dict() == data, "noncanonical blueprint")
    return blueprint


def verify_coordination_preview(report: Any) -> bool:
    """Revalidate shape, DAG, state assessment and hashes, NOT authenticity."""
    try:
        if type(report) is not dict or set(report) != _REPORT_FIELDS:
            return False
        if len(_encode(report)) > _MAX_REPORT_BYTES:
            return False
        if report["schema"] != MISSION_COORDINATION_SCHEMA:
            return False
        if not (report["integrity_only"] is True and
                report["authenticity_verified"] is False and
                report["authorization_granted"] is False and
                report["effects_executed"] is False):
            return False
        if report["outcome"] not in {"blocked", "host_review_required"}:
            return False
        bp = _blueprint_from_dict(report["blueprint"])
        if (report["mission_id"] != bp.mission_id or
                report["product"] != bp.product or
                report["target_status"] != bp.target_status):
            return False
        for field in ("blueprint_sha256", "state_assessment_sha256",
                      "registry_sha256", "mission_head_sha256", "policy_sha256",
                      "coordination_sha256"):
            _check_digest(report[field], field)
        if report["blueprint_sha256"] != bp.sha256:
            return False
        assessment = report["state_assessment"]
        if not verify_assessment(assessment):
            return False
        if (report["state_assessment_sha256"] != assessment["assessment_sha256"] or
                report["registry_sha256"] != assessment["registry_sha256"] or
                report["mission_head_sha256"] != assessment["mission_head_sha256"] or
                report["policy_sha256"] != assessment["policy_sha256"] or
                report["mission_id"] != assessment["mission_id"] or
                report["target_status"] != assessment["target_status"]):
            return False
        # A policy/registry product mismatch is deliberately an assessed block.
        if report["product"] != assessment["product"] and not (
            assessment["outcome"] == "blocked" and
            "policy_product_mismatch" in assessment["reasons"]
        ):
            return False
        if report["planned_layers"] != [list(w) for w in bp.planned_layers]:
            return False
        if report["required_action_reviews"] != list(bp.required_action_reviews):
            return False
        missing = report["missing_components"]
        reasons = report["reasons"]
        if not (type(missing) is list and
                missing == sorted(set(missing)) and
                all(type(c) is str and c in COMPONENTS for c in missing)):
            return False
        if not (type(reasons) is list and 1 <= len(reasons) <= 96 and
                reasons == sorted(set(reasons)) and
                all(type(reason) is str and 0 < len(reason) < 180 for reason in reasons)):
            return False
        # The full causal state is bound to an independently verified assessment.
        expected_reasons: set[str] = set()
        if assessment["outcome"] == "blocked":
            expected_reasons = {"state_blocked"}
            expected_reasons.update("state:" + r for r in assessment["reasons"])
        expected_reasons.update("component_missing:" + c for c in missing)
        # A live registry can change AFTER the snapshot was checked. This only
        # adds a reason to block; it never makes a preview more permissive.
        may_change = "registry_changed_during_preview" in reasons
        if may_change:
            expected_reasons.add("registry_changed_during_preview")
        if not (missing or assessment["outcome"] == "blocked" or may_change):
            expected_reasons.add("host_review_required")
        if reasons != sorted(expected_reasons):
            return False
        if report["outcome"] != ("blocked" if (missing or
                 assessment["outcome"] == "blocked" or may_change) else
                 "host_review_required"):
            return False
        unsigned = {key: val for key, val in report.items()
                    if key != "coordination_sha256"}
        return hmac.compare_digest(_digest(unsigned), report["coordination_sha256"])
    except (NexusMissionCoordinatorError, NexusMissionStateError,
            TypeError, ValueError, KeyError, AttributeError, RecursionError,
            OverflowError):
        return False


class NexusMissionCoordinator:
    """Prepare an inspectable workflow preview, never dispatch or authorize.

    Requires NexusMissionState to assess the *same* mission/product/target.
    Host may provide a read-only, non-consuming Trust checker through the
    NexusMissionState object. Its answer is still not an execution grant.
    """

    def __init__(self, blueprint: MissionCoordinationBlueprint,
                 mission_state: NexusMissionState, *,
                 snapshot_limits: MissionRegistryLimits | None = None) -> None:
        _require(type(blueprint) is MissionCoordinationBlueprint,
                 "invalid workflow blueprint")
        _require(type(mission_state) is NexusMissionState,
                 "invalid mission state checker")
        policy = mission_state.policy
        _require((policy.mission_id, policy.product) ==
                 (blueprint.mission_id, blueprint.product),
                 "mission policy and blueprint disagree")
        _require(any(rule.target_status == blueprint.target_status
                     for rule in policy.rules),
                 "policy does not define requested target")
        _require(snapshot_limits is None or type(snapshot_limits) is MissionRegistryLimits,
                 "invalid registry snapshot limits")
        self.blueprint = blueprint
        self.mission_state = mission_state
        self.snapshot_limits = snapshot_limits

    def preview(self, registry: NexusMissionRegistry | Mapping[str, Any]) -> MissionCoordinationPreview:
        live = registry if isinstance(registry, NexusMissionRegistry) else None
        if live is not None:
            snapshot = live.snapshot()
            limits = live.limits
        else:
            _require(type(registry) is dict, "provide a registry or JSON snapshot")
            snapshot = registry
            limits = self.snapshot_limits
        try:
            validated = NexusMissionRegistry.from_snapshot(snapshot, limits=limits).snapshot()
        except (NexusMissionRegistryError, ValueError, KeyError, TypeError,
                RecursionError, OverflowError) as exc:
            raise NexusMissionCoordinatorError("invalid registry snapshot") from exc
        own = next((m for m in validated["missions"]
                    if m["mission_id"] == self.blueprint.mission_id), None)
        _require(own is not None, "mission missing from registry")
        assessment = self.mission_state.assess(validated, self.blueprint.target_status).to_dict()
        _require(verify_assessment(assessment), "invalid state assessment")
        # Reference validated source, rather than raw provided snapshot.
        _require(assessment["registry_sha256"] == validated["registry_sha256"],
                 "registry disagreement during preview")
        missing = sorted(set(self.blueprint.required_components) - set(own["components"]))
        reasons: set[str] = set()
        if assessment["outcome"] == "blocked":
            reasons.add("state_blocked")
            reasons.update("state:" + r for r in assessment["reasons"])
        reasons.update("component_missing:" + component for component in missing)
        if live is not None and live.snapshot()["registry_sha256"] != validated["registry_sha256"]:
            reasons.add("registry_changed_during_preview")
        blocked = (assessment["outcome"] == "blocked" or bool(missing) or
                   "registry_changed_during_preview" in reasons)
        if not blocked:
            reasons.add("host_review_required")
        body = {
            "schema": MISSION_COORDINATION_SCHEMA,
            "mission_id": self.blueprint.mission_id,
            "product": self.blueprint.product,
            "target_status": self.blueprint.target_status,
            "outcome": "blocked" if blocked else "host_review_required",
            "reasons": sorted(reasons), "missing_components": missing,
            "blueprint": self.blueprint.to_dict(),
            "blueprint_sha256": self.blueprint.sha256,
            "state_assessment": assessment,
            "state_assessment_sha256": assessment["assessment_sha256"],
            "registry_sha256": validated["registry_sha256"],
            "mission_head_sha256": assessment["mission_head_sha256"],
            "policy_sha256": assessment["policy_sha256"],
            "planned_layers": [list(w) for w in self.blueprint.planned_layers],
            "required_action_reviews": list(self.blueprint.required_action_reviews),
            "integrity_only": True, "authenticity_verified": False,
            "authorization_granted": False, "effects_executed": False,
        }
        _require(len(_encode(body)) <= _MAX_REPORT_BYTES, "workflow preview exceeds byte budget")
        report = {**body, "coordination_sha256": _digest(body)}
        _require(verify_coordination_preview(report), "workflow preview invariant failed")
        return MissionCoordinationPreview(_encode(report))


__all__ = [
    "MISSION_COORDINATION_SCHEMA", "NexusMissionCoordinatorError",
    "MissionWorkflowStep", "MissionCoordinationBlueprint",
    "MissionCoordinationPreview", "NexusMissionCoordinator",
    "verify_coordination_preview",
]
