"""Read-only mission readiness assessment for AegisForge Nexus Mission Control.

Install: src/aegisforge/nexus/mission_control/mission_state.py

This module DOES NOT transition missions, grant authority, consume approvals,
run tools, inspect workspace files, contact services, or persist state. It
advises a trusted host about recorded prerequisites before a possible mission
state transition. Only the host can authenticate actors and sources, authorize
or perform a transition, and recheck the live registry immediately before it.

SHA-256 proves internal consistency of exported metadata, NOT authenticity.
A recorded 'verified' dependency is still a host-reported claim; a host gate
must independently check whichever external facts are security-sensitive.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Callable, Mapping

from .mission_registry import (
    COMPONENTS, PRODUCTS, REFERENCE_KINDS, STATUSES, TRANSITIONS,
    MissionRegistryLimits, NexusMissionRegistry, NexusMissionRegistryError,
)

MISSION_STATE_SCHEMA = "aegisforge.nexus.mission_control.state.v1"
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,150}$")
_OUTCOMES = frozenset({"ready_for_host_review", "blocked"})
_RESULT_KINDS = frozenset({"evidence", "evaluation", "scorecard"})
_HOST_BOUND_STATES = frozenset({"running", "verified"})
_MAX_RULES = len(STATUSES)
_MAX_DEPENDENCIES = 32
_MAX_REPORT = 131_072
_FIELDS = frozenset({
    "schema", "mission_id", "product", "current_status", "target_status",
    "outcome", "reasons", "missing_components", "missing_references",
    "dependencies", "registry_sha256", "mission_head_sha256", "policy_sha256",
    "integrity_only", "authenticity_verified", "authorization_granted",
    "effects_executed", "assessment_sha256",
})


class NexusMissionStateError(ValueError):
    """Invalid host policy, registry input, or assessment metadata."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NexusMissionStateError(message)


def _encode(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionStateError("invalid JSON metadata") from exc


def _digest(value: Any) -> str:
    return sha256(_encode(value)).hexdigest()


def _check_id(value: Any, label: str = "mission") -> str:
    _require(type(value) is str and _ID.fullmatch(value) is not None,
             f"invalid {label} identifier")
    return value


def _check_sha(value: Any, label: str) -> str:
    _require(type(value) is str and _SHA.fullmatch(value) is not None,
             f"invalid {label} SHA-256")
    return value


def _string_tuple(value: Any, allowed: frozenset[str] | None, limit: int,
                  label: str) -> tuple[str, ...]:
    _require(type(value) is tuple and len(value) <= limit, f"invalid {label} collection")
    for v in value:
        _require(type(v) is str and (v in allowed if allowed is not None else
                 _ID.fullmatch(v) is not None), f"invalid {label} entry")
    _require(tuple(sorted(set(value))) == value, f"{label} must be unique and sorted")
    return value


@dataclass(frozen=True, slots=True)
class MissionTransitionRule:
    """Host-defined prerequisites for ONE exact target status.

    ``required_reference_kinds`` demands at least one reference per kind.
    ``dependencies`` requires those missions to be in recorded status verified.
    A recorded status or reference is not trusted proof of authenticity.
    """

    target_status: str
    required_components: tuple[str, ...] = ()
    required_reference_kinds: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    requires_host_review: bool = False

    def __post_init__(self) -> None:
        _require(type(self.target_status) is str and self.target_status in STATUSES and
                 self.target_status != "draft", "invalid transition target")
        _string_tuple(self.required_components, COMPONENTS, len(COMPONENTS), "components")
        _string_tuple(self.required_reference_kinds, REFERENCE_KINDS,
                      len(REFERENCE_KINDS), "references")
        _string_tuple(self.dependencies, None, _MAX_DEPENDENCIES, "dependencies")
        _require(type(self.requires_host_review) is bool, "invalid host review requirement")

    def to_dict(self) -> dict[str, Any]:
        return {"target_status": self.target_status,
                "required_components": list(self.required_components),
                "required_reference_kinds": list(self.required_reference_kinds),
                "dependencies": list(self.dependencies),
                "requires_host_review": self.requires_host_review}


@dataclass(frozen=True, slots=True)
class MissionStatePolicy:
    """Host-owned, mission- and product-bound policy. Never model-generated."""

    mission_id: str
    product: str
    rules: tuple[MissionTransitionRule, ...]

    def __post_init__(self) -> None:
        _check_id(self.mission_id)
        _require(type(self.product) is str and self.product in PRODUCTS,
                 "invalid policy product")
        _require(type(self.rules) is tuple and 1 <= len(self.rules) <= _MAX_RULES,
                 "policy requires bounded transition rules")
        _require(all(type(r) is MissionTransitionRule for r in self.rules),
                 "invalid policy rule")
        names = tuple(r.target_status for r in self.rules)
        _require(tuple(sorted(set(names))) == names,
                 "policy targets must be unique and sorted")
        _require(all(self.mission_id not in r.dependencies for r in self.rules),
                 "self-dependency is forbidden")

    def to_dict(self) -> dict[str, Any]:
        return {"mission_id": self.mission_id, "product": self.product,
                "rules": [r.to_dict() for r in self.rules]}

    @property
    def sha256(self) -> str:
        return _digest(self.to_dict())


# Only the trusted host may inject this checker. It must verify bound state,
# actual reviewer/permission, expiry, purpose, and external evidence. It must
# NOT consume single-use approvals during a readiness preview.
# Args: mission, target, mission-head, registry-snapshot, policy, approval-ref.
HostReadinessChecker = Callable[[str, str, str, str, str, str | None], bool]


@dataclass(frozen=True, slots=True)
class MissionStateAssessment:
    """Immutable metadata-only result; never an authorization token."""

    _payload: bytes

    def to_dict(self) -> dict[str, Any]:
        # JSON bytes make the record immutable; callers receive independent copies.
        return json.loads(self._payload)

    @property
    def outcome(self) -> str:
        return self.to_dict()["outcome"]

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(self.to_dict()["reasons"])


def verify_assessment(assessment: Mapping[str, Any]) -> bool:
    """Check report shape and SHA-256, never authenticate a decision."""
    try:
        if type(assessment) is not dict or set(assessment) != _FIELDS:
            return False
        if len(_encode(assessment)) > _MAX_REPORT:
            return False
        if assessment["schema"] != MISSION_STATE_SCHEMA:
            return False
        if not (assessment["integrity_only"] is True and
                assessment["authenticity_verified"] is False and
                assessment["authorization_granted"] is False and
                assessment["effects_executed"] is False):
            return False
        _check_id(assessment["mission_id"])
        if type(assessment["product"]) is not str or assessment["product"] not in PRODUCTS:
            return False
        if (type(assessment["current_status"]) is not str or
                assessment["current_status"] not in STATUSES or
                type(assessment["target_status"]) is not str or
                assessment["target_status"] not in STATUSES):
            return False
        if assessment["outcome"] not in _OUTCOMES:
            return False
        for name in ("reasons", "missing_components", "missing_references"):
            entries = assessment[name]
            if type(entries) is not list or len(entries) > 64 or any(
                type(v) is not str or _CODE.fullmatch(v) is None for v in entries
            ) or entries != sorted(set(entries)):
                return False
        for name in ("registry_sha256", "mission_head_sha256", "policy_sha256", "assessment_sha256"):
            _check_sha(assessment[name], name)
        deps = assessment["dependencies"]
        if type(deps) is not list or len(deps) > _MAX_DEPENDENCIES:
            return False
        prior = ""
        for dep in deps:
            if type(dep) is not dict or set(dep) != {"mission_id", "status", "head_sha256"}:
                return False
            ident = _check_id(dep["mission_id"])
            if ident <= prior or ident == assessment["mission_id"]:
                return False
            prior = ident
            if dep["status"] is not None and dep["status"] not in STATUSES:
                return False
            if dep["head_sha256"] is not None:
                _check_sha(dep["head_sha256"], "dependency head")
            if (dep["status"] is None) != (dep["head_sha256"] is None):
                return False
        if assessment["outcome"] == "blocked" and not assessment["reasons"]:
            return False
        if assessment["outcome"] == "ready_for_host_review" and assessment["reasons"]:
            return False
        unsigned = {k: v for k, v in assessment.items() if k != "assessment_sha256"}
        return hmac.compare_digest(_digest(unsigned), assessment["assessment_sha256"])
    except (NexusMissionStateError, TypeError, ValueError, KeyError,
            AttributeError, OverflowError, RecursionError):
        return False


class NexusMissionState:
    """Read-only, fail-closed readiness assessment bound to registry snapshots.

    The checker is an explicit host-supplied adapter, NOT the ApprovalGate
    itself. Trust's ApprovalGate deals with particular workspace actions and
    must never be consumed by a mission readiness preview. Even an assessment
    of 'ready_for_host_review' is NOT permission to transition or execute.
    """

    def __init__(self, policy: MissionStatePolicy, *,
                 host_checker: HostReadinessChecker | None = None,
                 snapshot_limits: MissionRegistryLimits | None = None) -> None:
        _require(type(policy) is MissionStatePolicy, "invalid mission policy")
        _require(host_checker is None or callable(host_checker), "invalid host checker")
        _require(snapshot_limits is None or type(snapshot_limits) is MissionRegistryLimits,
                 "invalid snapshot limits")
        self.policy = policy
        self._host_checker = host_checker
        self._limits = snapshot_limits

    def assess(self, registry: NexusMissionRegistry | Mapping[str, Any],
               target_status: str) -> MissionStateAssessment:
        _require(type(target_status) is str and target_status in STATUSES,
                 "invalid requested target")
        if isinstance(registry, NexusMissionRegistry):
            snapshot = registry.snapshot()
            limits = registry.limits
            live = registry
        else:
            _require(type(registry) is dict, "provide a registry or JSON snapshot")
            snapshot = registry
            limits = self._limits
            live = None
        try:
            restored = NexusMissionRegistry.from_snapshot(snapshot, limits=limits)
        except (NexusMissionRegistryError, TypeError, ValueError, KeyError,
                AttributeError, RecursionError, OverflowError) as exc:
            raise NexusMissionStateError("invalid registry snapshot") from exc
        # From-snapshot replay ensures registry SHA and event state machine agree.
        canonical = restored.snapshot()
        rows = {row["mission_id"]: row for row in canonical["missions"]}
        _require(self.policy.mission_id in rows, "mission not present in registry")
        mission = rows[self.policy.mission_id]
        reasons: set[str] = set()
        missing_components: set[str] = set()
        missing_references: set[str] = set()
        dependencies: list[dict[str, Any]] = []
        rule = next((r for r in self.policy.rules if r.target_status == target_status), None)
        if self.policy.product != mission["product"]:
            reasons.add("policy_product_mismatch")
        if target_status not in TRANSITIONS[mission["status"]]:
            reasons.add("transition_not_allowed")
        if rule is None:
            reasons.add("policy_missing")
        else:
            have_components = set(mission["components"])
            have_kinds = {r["kind"] for r in mission["references"]}
            for component in rule.required_components:
                if component not in have_components:
                    missing_components.add(component)
                    reasons.add("missing_component:" + component)
            for kind in rule.required_reference_kinds:
                if kind not in have_kinds:
                    missing_references.add(kind)
                    reasons.add("missing_reference:" + kind)
            for ident in rule.dependencies:
                dep = rows.get(ident)
                dependencies.append({"mission_id": ident,
                                     "status": dep["status"] if dep else None,
                                     "head_sha256": dep["head_sha256"] if dep else None})
                if dep is None:
                    reasons.add("dependency_missing:" + ident)
                elif dep["status"] != "verified":
                    reasons.add("dependency_not_verified:" + ident)
            # Registry itself enforces result evidence at completion. Assess
            # it proactively before suggesting an impossible transition.
            if target_status in {"reported_complete", "verified"} and not have_kinds.intersection(_RESULT_KINDS):
                reasons.add("result_reference_missing")
                missing_references.add("result")  # any evidence, evaluation or scorecard
            check_host = (rule.requires_host_review or
                          target_status in _HOST_BOUND_STATES)
            if check_host:
                approval_refs = sorted({r["sha256"] for r in mission["references"]
                                        if r["kind"] == "approval"})
                if target_status == "verified" and not approval_refs:
                    reasons.add("approval_reference_missing")
                    missing_references.add("approval")
                if self._host_checker is None:
                    reasons.add("host_checker_missing")
                elif not reasons:
                    # A host callback is only queried AFTER all declarative
                    # prerequisites are met. It must never consume approvals.
                    choices: list[str | None] = approval_refs if target_status == "verified" else [None]
                    verified = False
                    for approval_sha in choices:
                        try:
                            result = self._host_checker(
                                self.policy.mission_id, target_status,
                                mission["head_sha256"], canonical["registry_sha256"],
                                self.policy.sha256, approval_sha,
                            )
                        except Exception:
                            reasons.add("host_checker_unavailable")
                            break
                        if result is True:
                            verified = True
                            break
                    if not verified and "host_checker_unavailable" not in reasons:
                        reasons.add("host_gate_not_satisfied")
        # Reject a stale reading if a live registry was edited while checking.
        if live is not None and live.snapshot()["registry_sha256"] != canonical["registry_sha256"]:
            reasons.add("registry_changed_during_assessment")
        outcome = "blocked" if reasons else "ready_for_host_review"
        body = {
            "schema": MISSION_STATE_SCHEMA,
            "mission_id": self.policy.mission_id,
            "product": mission["product"], "current_status": mission["status"],
            "target_status": target_status, "outcome": outcome,
            "reasons": sorted(reasons),
            "missing_components": sorted(missing_components),
            "missing_references": sorted(missing_references),
            "dependencies": dependencies,
            "registry_sha256": canonical["registry_sha256"],
            "mission_head_sha256": mission["head_sha256"],
            "policy_sha256": self.policy.sha256,
            "integrity_only": True, "authenticity_verified": False,
            "authorization_granted": False, "effects_executed": False,
        }
        _require(len(_encode(body)) <= _MAX_REPORT, "assessment report exceeds limit")
        report = {**body, "assessment_sha256": _digest(body)}
        _require(verify_assessment(report), "assessment invariant failed")
        return MissionStateAssessment(_encode(report))


__all__ = [
    "MISSION_STATE_SCHEMA", "NexusMissionStateError", "MissionTransitionRule",
    "MissionStatePolicy", "HostReadinessChecker", "MissionStateAssessment",
    "NexusMissionState", "verify_assessment",
]
