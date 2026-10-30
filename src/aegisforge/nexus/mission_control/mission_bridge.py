"""Read-only, metadata-only adapter bridge for Nexus Mission Control.

Destination: src/aegisforge/nexus/mission_control/mission_bridge.py

This bridge connects a verified MissionCoordinationPreview with a host-defined
catalog of *declared* component adapter interfaces. It neither imports nor
invokes an adapter; it never accesses files, models, networks or tools; it
cannot consume approvals or authorize an action. A metadata binding is NOT
proof that a provider exists, is safe, or may perform an effect.

SHA-256 gives internal consistency of JSON metadata, NOT authenticity or
capabilities. At the actual operation boundary a trusted host MUST authenticate
registry/blueprint/policy/provider, recheck the live mission head, enforce
NexusActionPolicy and NexusApprovalGate on the exact action, and check paths,
permissions and relevant state afresh. Never dispatch based on this report.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Mapping, Sequence

from .mission_registry import (
    COMPONENTS, MissionRegistryLimits, NexusMissionRegistry,
    NexusMissionRegistryError,
)
from .mission_coordinator import (
    MissionCoordinationPreview, MissionWorkflowStep,
    NexusMissionCoordinatorError, verify_coordination_preview,
)

MISSION_BRIDGE_SCHEMA = "aegisforge.nexus.mission_control.bridge.v1"
BRIDGE_CATALOG_SCHEMA = "aegisforge.nexus.mission_control.bridge_catalog.v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ADAPTER = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
_MAX_PORTS = 128
_MAX_REPORT_BYTES = 350_000
_MAX_PREVIEW_BYTES = 131_072
_MAX_PORT_BYTES = 18_000
_MAX_HANDOFFS = 64

_BRIDGE_KEYS = frozenset({
    "schema", "mission_id", "product", "target_status", "outcome",
    "reasons", "registry_sha256", "mission_head_sha256", "policy_sha256",
    "coordination_sha256", "coordination", "adapter_catalog",
    "planned_layers", "handoffs", "missing_bindings",
    "required_action_reviews", "integrity_only", "authenticity_verified",
    "authorization_granted", "effects_executed", "bridge_sha256",
})
_PORT_FIELDS = frozenset({
    "component", "operation", "adapter_id", "contract_sha256",
    "metadata_only",
})


class NexusMissionBridgeError(ValueError):
    """Invalid adapter catalog, coordination preview, or registry anchor."""


def _require(ok: bool, why: str) -> None:
    if not ok:
        raise NexusMissionBridgeError(why)


def _encode(item: Any) -> bytes:
    try:
        return json.dumps(item, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionBridgeError("invalid JSON metadata") from exc


def _digest(item: Any) -> str:
    return sha256(_encode(item)).hexdigest()


def _hex(value: Any, name: str) -> str:
    _require(type(value) is str and _SHA.fullmatch(value) is not None,
             f"invalid {name} SHA-256")
    return value


def _port_operation(component: Any, operation: Any) -> None:
    _require(type(component) is str and component in COMPONENTS,
             "invalid component")
    _require(type(operation) is str, "invalid operation")
    # Reuse the exact coordinator contract rather than another drifting copy
    # of its component/operation allowlist. The step is never executed.
    dummy = "0" * 64 if operation == "request_effect" else None
    try:
        MissionWorkflowStep("step.port", component, operation, (), dummy)
    except NexusMissionCoordinatorError as exc:
        raise NexusMissionBridgeError("unsupported component/operation binding") from exc


@dataclass(frozen=True, slots=True)
class MissionBridgePort:
    """Host-declared, non-executable adapter metadata; not an adapter handle."""

    component: str
    operation: str
    adapter_id: str
    contract_sha256: str

    def __post_init__(self) -> None:
        _port_operation(self.component, self.operation)
        _require(type(self.adapter_id) is str and
                 _ADAPTER.fullmatch(self.adapter_id) is not None,
                 "invalid opaque adapter identifier")
        _hex(self.contract_sha256, "adapter contract")

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component, "operation": self.operation,
            "adapter_id": self.adapter_id,
            "contract_sha256": self.contract_sha256,
            "metadata_only": True,
        }


@dataclass(frozen=True, slots=True)
class MissionBridgeCatalog:
    """Immutable, bounded manifest of externally configured adapter contracts."""

    ports: tuple[MissionBridgePort, ...]

    def __post_init__(self) -> None:
        _require(type(self.ports) is tuple and len(self.ports) <= _MAX_PORTS,
                 "invalid adapter count")
        _require(all(type(port) is MissionBridgePort for port in self.ports),
                 "invalid adapter port type")
        keys = [(p.component, p.operation) for p in self.ports]
        _require(keys == sorted(set(keys)),
                 "ports must be unique and sorted by component/operation")
        _require(len(_encode(self.to_dict())) <= _MAX_PORT_BYTES,
                 "adapter catalog exceeds size limit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": BRIDGE_CATALOG_SCHEMA,
            "ports": [p.to_dict() for p in self.ports],
        }

    @property
    def sha256(self) -> str:
        return _digest(self.to_dict())


def _catalog_from_report(raw: Any) -> MissionBridgeCatalog:
    _require(type(raw) is dict and set(raw) == {"schema", "ports", "catalog_sha256"},
             "invalid catalog snapshot")
    _require(raw["schema"] == BRIDGE_CATALOG_SCHEMA, "invalid catalog schema")
    ports = raw["ports"]
    _require(type(ports) is list and len(ports) <= _MAX_PORTS,
             "invalid catalog ports")
    parsed = []
    for row in ports:
        _require(type(row) is dict and set(row) == _PORT_FIELDS and
                 row["metadata_only"] is True,
                 "invalid adapter port snapshot")
        parsed.append(MissionBridgePort(
            row["component"], row["operation"], row["adapter_id"],
            row["contract_sha256"],
        ))
    catalog = MissionBridgeCatalog(tuple(parsed))
    _hex(raw["catalog_sha256"], "catalog")
    _require(hmac.compare_digest(catalog.sha256, raw["catalog_sha256"]),
             "adapter catalog digest mismatch")
    return catalog


def _preview_object(value: Any) -> dict[str, Any]:
    if type(value) is MissionCoordinationPreview:
        value = value.to_dict()
    _require(type(value) is dict and len(_encode(value)) <= _MAX_PREVIEW_BYTES,
             "invalid coordination preview")
    _require(verify_coordination_preview(value),
             "coordination preview failed integrity checks")
    return value


def _derive(preview: dict[str, Any], catalog: MissionBridgeCatalog) -> dict[str, Any]:
    """Recompute ALL user-visible derived fields from validated metadata."""
    steps = preview["blueprint"]["steps"]
    _require(len(steps) <= _MAX_HANDOFFS, "workflow exceeds bridge step limit")
    ports = {(p.component, p.operation): p for p in catalog.ports}
    handoffs = []
    missing: set[str] = set()
    for step in steps:
        port = ports.get((step["component"], step["operation"]))
        if port is None:
            missing.add(step["component"] + "." + step["operation"])
        body = {
            "step_id": step["step_id"],
            "component": step["component"],
            "operation": step["operation"],
            "depends_on": list(step["depends_on"]),
            "intent_sha256": step["intent_sha256"],
            "binding_status": "metadata_binding_present" if port else "missing",
            "adapter_id": port.adapter_id if port else None,
            "contract_sha256": port.contract_sha256 if port else None,
            "metadata_only": True,
            "coordination_sha256": preview["coordination_sha256"],
            "catalog_sha256": catalog.sha256,
        }
        handoffs.append({**body, "handoff_sha256": _digest(body)})
    reasons: set[str] = set()
    if preview["outcome"] == "blocked":
        reasons.add("coordination_blocked")
    reasons.update("binding_missing:" + ident for ident in missing)
    if not reasons:
        reasons.add("host_review_required")
    body = {
        "schema": MISSION_BRIDGE_SCHEMA,
        "mission_id": preview["mission_id"],
        "product": preview["product"],
        "target_status": preview["target_status"],
        "outcome": "blocked" if missing or preview["outcome"] == "blocked"
                  else "host_review_required",
        "reasons": sorted(reasons),
        "registry_sha256": preview["registry_sha256"],
        "mission_head_sha256": preview["mission_head_sha256"],
        "policy_sha256": preview["policy_sha256"],
        "coordination_sha256": preview["coordination_sha256"],
        "coordination": preview,
        "adapter_catalog": {
            **catalog.to_dict(), "catalog_sha256": catalog.sha256,
        },
        "planned_layers": preview["planned_layers"],
        "handoffs": handoffs,
        "missing_bindings": sorted(missing),
        "required_action_reviews": preview["required_action_reviews"],
        "integrity_only": True,
        "authenticity_verified": False,
        "authorization_granted": False,
        "effects_executed": False,
    }
    _require(len(_encode(body)) <= _MAX_REPORT_BYTES, "bridge report too large")
    return {**body, "bridge_sha256": _digest(body)}


def _registry_anchor(snapshot: Any, preview: Mapping[str, Any],
                     limits: MissionRegistryLimits | None = None) -> None:
    try:
        _require(type(snapshot) is dict, "invalid registry snapshot")
        verified = NexusMissionRegistry.from_snapshot(snapshot, limits=limits).snapshot()
    except (NexusMissionRegistryError, TypeError, ValueError, KeyError,
            RecursionError, OverflowError) as exc:
        raise NexusMissionBridgeError("registry failed validation") from exc
    _require(verified["registry_sha256"] == preview["registry_sha256"],
             "registry changed since coordination preview")
    row = next((r for r in verified["missions"]
                if r["mission_id"] == preview["mission_id"]), None)
    _require(row is not None and
             row["head_sha256"] == preview["mission_head_sha256"] and
             row["product"] == preview["product"],
             "mission anchor changed since coordination preview")


@dataclass(frozen=True, slots=True)
class MissionBridgePlan:
    """Immutable preview of declared bindings, never an executable plan."""

    _payload: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._payload)

    @property
    def outcome(self) -> str:
        return self.to_dict()["outcome"]

    @property
    def missing_bindings(self) -> tuple[str, ...]:
        return tuple(self.to_dict()["missing_bindings"])


def verify_bridge_plan(report: Any, *,
                       registry: NexusMissionRegistry | Mapping[str, Any] | None = None,
                       coordination: MissionCoordinationPreview | Mapping[str, Any] | None = None,
                       catalog: MissionBridgeCatalog | None = None,
                       limits: MissionRegistryLimits | None = None) -> bool:
    """Validate entire bridge structure, optionally re-anchor against live data.

    Without optional external anchors, this checks *internal consistency only*;
    a self-consistent forged report is NOT authenticated.
    """
    try:
        if type(report) is not dict or set(report) != _BRIDGE_KEYS:
            return False
        if len(_encode(report)) > _MAX_REPORT_BYTES + 100:
            return False
        if report["schema"] != MISSION_BRIDGE_SCHEMA:
            return False
        if (report["integrity_only"] is not True or
                report["authenticity_verified"] is not False or
                report["authorization_granted"] is not False or
                report["effects_executed"] is not False):
            return False
        preview = _preview_object(report["coordination"])
        ports = _catalog_from_report(report["adapter_catalog"])
        expected = _derive(preview, ports)
        if report != expected:
            return False
        if not hmac.compare_digest(report["bridge_sha256"], expected["bridge_sha256"]):
            return False
        if coordination is not None and _preview_object(coordination) != preview:
            return False
        if catalog is not None:
            if type(catalog) is not MissionBridgeCatalog or catalog != ports:
                return False
        if registry is not None:
            if isinstance(registry, NexusMissionRegistry):
                snapshot = registry.snapshot()
                provided_limits = registry.limits
            else:
                snapshot = registry
                provided_limits = limits
            _registry_anchor(snapshot, preview, provided_limits)
        return True
    except (NexusMissionBridgeError, NexusMissionCoordinatorError,
            NexusMissionRegistryError, KeyError, AttributeError, TypeError,
            ValueError, RecursionError, OverflowError, MemoryError):
        return False


class NexusMissionBridge:
    """Connect validated workflow metadata to a host-declared adapter catalog.

    The bridge does NOT import, inspect, call or execute adapter code. Its
    bindings are metadata and are suitable for a host review/inspection UI,
    never as authorization tokens or capability handles.
    """

    def __init__(self, catalog: MissionBridgeCatalog, *,
                 snapshot_limits: MissionRegistryLimits | None = None) -> None:
        _require(type(catalog) is MissionBridgeCatalog, "invalid adapter catalog")
        _require(snapshot_limits is None or type(snapshot_limits) is MissionRegistryLimits,
                 "invalid registry limit configuration")
        self.catalog = catalog
        self.snapshot_limits = snapshot_limits

    def plan(self, coordination: MissionCoordinationPreview | Mapping[str, Any],
             registry: NexusMissionRegistry | Mapping[str, Any]) -> MissionBridgePlan:
        preview = _preview_object(coordination)
        live = registry if isinstance(registry, NexusMissionRegistry) else None
        if live is not None:
            snapshot = live.snapshot()
            limits = live.limits
        else:
            snapshot = registry
            limits = self.snapshot_limits
        _registry_anchor(snapshot, preview, limits)
        report = _derive(preview, self.catalog)
        # Fail closed if live mission metadata changed during computation.
        if live is not None:
            _require(live.snapshot()["registry_sha256"] == preview["registry_sha256"],
                     "registry changed during bridge planning")
        _require(verify_bridge_plan(report, coordination=preview,
                                    catalog=self.catalog),
                 "bridge invariant failure")
        return MissionBridgePlan(_encode(report))


__all__ = [
    "MISSION_BRIDGE_SCHEMA", "BRIDGE_CATALOG_SCHEMA", "NexusMissionBridgeError",
    "MissionBridgePort", "MissionBridgeCatalog", "MissionBridgePlan",
    "NexusMissionBridge", "verify_bridge_plan",
]
