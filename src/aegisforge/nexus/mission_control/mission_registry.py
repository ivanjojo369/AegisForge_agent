"""Bounded, audit-linked mission registry for AegisForge Nexus Mission Control.

Destination: src/aegisforge/nexus/mission_control/mission_registry.py

In-memory metadata registry only: no tool execution, network requests, file
access, owner authentication, or changes to the existing Nexus modules.
The host MUST authenticate actors, authorize transitions, authenticate evidence
and persist snapshots securely. SHA-256 links show *internal consistency*, not
that an actor's claim, approval, timestamp, or outcome is truthful.

Mission objectives, owners and actors are digests in exported records. Hashes
are not encryption: predictable owner names may still be guessed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import re
from threading import RLock
from typing import Any, Mapping

MISSION_REGISTRY_SCHEMA = "aegisforge.nexus.mission_control.registry.v1"
PRODUCTS = frozenset({"nexus", "aegisgrid", "live_forge", "colossus"})
STATUSES = frozenset({
    "draft", "ready", "running", "paused", "awaiting_review",
    "reported_complete", "verified", "failed", "cancelled",
})
REFERENCE_KINDS = frozenset({
    "run", "evidence", "artifact", "evaluation", "scorecard", "provenance", "approval",
})
COMPONENTS = frozenset({
    "intelligence", "engineering", "trust", "proof", "lab", "mission_control",
    "workspace", "aegisgrid", "live_forge",
})
TRANSITIONS = {
    "draft": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"running", "cancelled"}),
    "running": frozenset({"paused", "awaiting_review", "reported_complete", "failed", "cancelled"}),
    "paused": frozenset({"running", "awaiting_review", "failed", "cancelled"}),
    "awaiting_review": frozenset({"running", "reported_complete", "failed", "cancelled"}),
    "reported_complete": frozenset({"verified", "awaiting_review", "failed"}),
    "verified": frozenset(), "failed": frozenset(), "cancelled": frozenset(),
}
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_ZERO = "0" * 64
_EVENT_FIELDS = frozenset({
    "sequence", "mission_id", "operation", "at_utc", "actor_sha256", "status",
    "registration", "reference", "component", "decision_sha256", "previous_sha256", "event_sha256",
})
_RECORD_FIELDS = frozenset({
    "mission_id", "product", "owner_sha256", "objective_sha256", "status",
    "created_at_utc", "updated_at_utc", "reference_count", "references",
    "component_count", "components", "event_count", "events", "head_sha256",
})
_SNAPSHOT_FIELDS = frozenset({
    "schema", "mission_count", "event_count", "missions", "integrity_only",
    "authenticity_verified", "effects_executed", "registry_sha256",
})


class NexusMissionRegistryError(ValueError):
    """An invalid mission, transition, reference, limit or snapshot."""


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise NexusMissionRegistryError(message)


def _encode(data: Any) -> bytes:
    try:
        return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise NexusMissionRegistryError("expected bounded JSON-compatible metadata") from exc


def _digest(data: Any) -> str:
    return sha256(_encode(data)).hexdigest()


def _check_id(value: Any, name: str) -> str:
    _require(type(value) is str and _ID.fullmatch(value) is not None,
             f"invalid opaque {name} identifier")
    return value


def _check_digest(value: Any, name: str) -> str:
    _require(type(value) is str and _HEX.fullmatch(value) is not None,
             f"invalid {name} SHA-256")
    return value


def _actor_hash(actor_id: str) -> str:
    return sha256(_check_id(actor_id, "actor").encode("utf-8")).hexdigest()


def _timestamp(at_utc: str | None) -> str:
    if at_utc is None:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    _require(type(at_utc) is str and len(at_utc) <= 32 and _TIME.fullmatch(at_utc) is not None,
             "timestamp must be an ISO-8601 UTC string ending in Z")
    try:
        candidate = datetime.fromisoformat(at_utc[:-1] + "+00:00")
    except (TypeError, ValueError, OverflowError) as exc:
        raise NexusMissionRegistryError("invalid UTC timestamp") from exc
    _require(candidate.utcoffset().total_seconds() == 0, "timestamp must be UTC")
    return at_utc


def _time_obj(value: str) -> datetime:
    return datetime.fromisoformat(_timestamp(value)[:-1] + "+00:00")


@dataclass(frozen=True, slots=True)
class MissionRegistryLimits:
    max_missions: int = 128
    max_events_per_mission: int = 128
    max_references_per_mission: int = 64
    max_components_per_mission: int = 16
    max_total_events: int = 4096
    max_snapshot_bytes: int = 3_000_000

    def __post_init__(self) -> None:
        for name, lo, hi in (
            ("max_missions", 1, 1024),
            ("max_events_per_mission", 1, 2048),
            ("max_references_per_mission", 1, 512),
            ("max_components_per_mission", 1, 32),
            ("max_total_events", 1, 30000),
            ("max_snapshot_bytes", 4096, 8_000_000),
        ):
            v = getattr(self, name)
            _require(type(v) is int and lo <= v <= hi, f"invalid {name}")


@dataclass(slots=True)
class _Mission:
    mission_id: str
    product: str
    owner_sha256: str
    objective_sha256: str
    status: str
    created_at_utc: str
    updated_at_utc: str
    events: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    components: list[str] = field(default_factory=list)

    def export(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id, "product": self.product,
            "owner_sha256": self.owner_sha256, "objective_sha256": self.objective_sha256,
            "status": self.status, "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "reference_count": len(self.references),
            "references": sorted(deepcopy(self.references), key=lambda r: (r["kind"], r["sha256"])),
            "component_count": len(self.components), "components": sorted(self.components),
            "event_count": len(self.events), "events": deepcopy(self.events),
            "head_sha256": self.events[-1]["event_sha256"] if self.events else _ZERO,
        }

    def summary(self) -> dict[str, Any]:
        row = self.export()
        del row["events"]
        return row


def _apply_event(m: _Mission, event: Mapping[str, Any], limits: MissionRegistryLimits) -> None:
    """Validate and apply an event, called only on an isolated candidate."""
    _require(type(event) is dict and set(event) == _EVENT_FIELDS, "invalid event keys")
    _require(type(event["sequence"]) is int and event["sequence"] == len(m.events) + 1,
             "out-of-order event sequence")
    _require(event["mission_id"] == m.mission_id, "event belongs to another mission")
    _check_digest(event["actor_sha256"], "actor")
    _check_digest(event["previous_sha256"], "previous event")
    _check_digest(event["event_sha256"], "event")
    _require(event["previous_sha256"] == (
        m.events[-1]["event_sha256"] if m.events else _ZERO), "broken event chain")
    _timestamp(event["at_utc"])
    if m.events:
        _require(_time_obj(event["at_utc"]) >= _time_obj(m.updated_at_utc),
                 "event timestamp decreases")
    _require(type(event["status"]) is str and event["status"] in STATUSES,
             "invalid event status")
    operation, status = event["operation"], event["status"]
    _require(type(operation) is str and operation in {
        "registered", "status_changed", "reference_linked", "component_assigned"}, "invalid operation")
    reg, ref, component, decision = (event["registration"], event["reference"],
                                     event["component"], event["decision_sha256"])
    if operation == "registered":
        _require(not m.events and status == "draft" and ref is None and component is None and decision is None,
                 "invalid initial event")
        _require(type(reg) is dict and set(reg) == {"product", "owner_sha256", "objective_sha256"},
                 "invalid initial registration")
        _require(type(reg["product"]) is str and reg["product"] in PRODUCTS,
                 "invalid registration product")
        _check_digest(reg["owner_sha256"], "owner")
        _check_digest(reg["objective_sha256"], "objective")
        _require(reg == {"product": m.product, "owner_sha256": m.owner_sha256,
                         "objective_sha256": m.objective_sha256}, "registration metadata mismatch")
        _require(event["at_utc"] == m.created_at_utc, "invalid creation timestamp")
    elif operation == "status_changed":
        _require(bool(m.events) and reg is None and ref is None and component is None and
                 status in TRANSITIONS[m.status], "invalid mission status transition")
        if status == "reported_complete":
            _require(any(r["kind"] in {"evidence", "evaluation", "scorecard"}
                         for r in m.references),
                     "reported completion requires a linked result or evidence")
        if status == "verified":
            _check_digest(decision, "verification decision")
            _require(any(r["kind"] in {"evidence", "evaluation", "scorecard"}
                         for r in m.references), "verified requires linked results")
        else:
            _require(decision is None, "decision digest only permitted for verification")
        m.status = status
    elif operation == "reference_linked":
        _require(bool(m.events) and reg is None and component is None and decision is None and
                 status == m.status, "invalid reference event")
        _require(type(ref) is dict and set(ref) == {"kind", "sha256"},
                 "invalid reference structure")
        _require(type(ref["kind"]) is str and ref["kind"] in REFERENCE_KINDS,
                 "invalid reference kind")
        _check_digest(ref["sha256"], "reference")
        _require(ref not in m.references, "duplicate reference")
        _require(len(m.references) < limits.max_references_per_mission,
                 "reference budget reached")
        _require(m.status not in {"verified", "failed", "cancelled"},
                 "terminal missions are immutable")
        m.references.append(dict(ref))
    else:
        _require(bool(m.events) and reg is None and ref is None and decision is None
                 and status == m.status, "invalid component assignment event")
        _require(type(component) is str and component in COMPONENTS,
                 "invalid component identifier")
        _require(component not in m.components, "component already assigned")
        _require(len(m.components) < limits.max_components_per_mission,
                 "component budget reached")
        _require(m.status in {"draft", "ready", "running", "paused", "awaiting_review"},
                 "components cannot be added after completion")
        m.components.append(component)
    unsigned = {k: event[k] for k in _EVENT_FIELDS if k != "event_sha256"}
    _require(hmac.compare_digest(event["event_sha256"], _digest(unsigned)),
             "event SHA-256 mismatch")
    _require(len(m.events) < limits.max_events_per_mission,
             "mission event budget reached")
    m.events.append(deepcopy(dict(event)))
    m.updated_at_utc = event["at_utc"]


class NexusMissionRegistry:
    """Transactional, in-memory mission metadata registry.

    The host owns authorization and source authentication. This registry does
    not grant authority to an actor merely because its identifier is supplied.
    Its status 'verified' is a host-reported decision, NOT verified truth.
    """

    def __init__(self, *, limits: MissionRegistryLimits | None = None) -> None:
        _require(limits is None or isinstance(limits, MissionRegistryLimits),
                 "invalid mission registry limits")
        self.limits = limits if limits is not None else MissionRegistryLimits()
        self._missions: dict[str, _Mission] = {}
        self._lock = RLock()

    def _body_locked(self, replacement: _Mission | None = None) -> dict[str, Any]:
        records = dict(self._missions)
        if replacement is not None:
            records[replacement.mission_id] = replacement
        entries = [records[k].export() for k in sorted(records)]
        return {"schema": MISSION_REGISTRY_SCHEMA, "mission_count": len(entries),
                "event_count": sum(row["event_count"] for row in entries),
                "missions": entries, "integrity_only": True,
                "authenticity_verified": False, "effects_executed": False}

    def _commit_locked(self, m: _Mission, event: dict[str, Any]) -> dict[str, Any]:
        candidate = deepcopy(m)
        _apply_event(candidate, event, self.limits)
        body = self._body_locked(candidate)
        _require(body["mission_count"] <= self.limits.max_missions,
                 "mission budget reached")
        _require(body["event_count"] <= self.limits.max_total_events,
                 "registry event budget reached")
        _require(len(_encode(body)) <= self.limits.max_snapshot_bytes,
                 "snapshot byte budget reached")
        self._missions[candidate.mission_id] = candidate
        return candidate.summary()

    @staticmethod
    def _build_event(m: _Mission, operation: str, at_utc: str, actor_id: str,
                     status: str, *, registration: dict[str, str] | None = None,
                     reference: dict[str, str] | None = None,
                     component: str | None = None,
                     decision_sha256: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "sequence": len(m.events) + 1, "mission_id": m.mission_id,
            "operation": operation, "at_utc": at_utc, "actor_sha256": _actor_hash(actor_id),
            "status": status, "registration": registration, "reference": reference,
            "component": component, "decision_sha256": decision_sha256,
            "previous_sha256": m.events[-1]["event_sha256"] if m.events else _ZERO,
        }
        return {**body, "event_sha256": _digest(body)}

    def register(self, mission_id: str, *, product: str, owner_id: str,
                 objective_sha256: str, actor_id: str, at_utc: str | None = None) -> dict[str, Any]:
        """Register an opaque ID and objective digest; raw objective is forbidden."""
        mission_id = _check_id(mission_id, "mission")
        _require(type(product) is str and product in PRODUCTS, "invalid product")
        owner = _actor_hash(_check_id(owner_id, "owner"))
        _check_digest(objective_sha256, "objective")
        at = _timestamp(at_utc)
        with self._lock:
            _require(mission_id not in self._missions, "mission already registered")
            _require(len(self._missions) < self.limits.max_missions, "mission budget reached")
            m = _Mission(mission_id, product, owner, objective_sha256, "draft", at, at)
            reg = {"product": product, "owner_sha256": owner,
                   "objective_sha256": objective_sha256}
            event = self._build_event(m, "registered", at, actor_id, "draft", registration=reg)
            return self._commit_locked(m, event)

    def transition(self, mission_id: str, status: str, *, actor_id: str,
                   at_utc: str | None = None,
                   decision_sha256: str | None = None) -> dict[str, Any]:
        """Record a host-requested transition, never authorize or execute work."""
        mission_id = _check_id(mission_id, "mission")
        _require(type(status) is str and status in STATUSES, "invalid status")
        if decision_sha256 is not None:
            _check_digest(decision_sha256, "decision")
        at = _timestamp(at_utc)
        with self._lock:
            m = self._get_locked(mission_id)
            event = self._build_event(m, "status_changed", at, actor_id, status,
                                      decision_sha256=decision_sha256)
            return self._commit_locked(m, event)

    def link_reference(self, mission_id: str, *, kind: str, reference_sha256: str,
                       actor_id: str, at_utc: str | None = None) -> dict[str, Any]:
        """Link an opaque source digest; external authenticity is NOT checked."""
        mission_id = _check_id(mission_id, "mission")
        _require(type(kind) is str and kind in REFERENCE_KINDS, "invalid reference kind")
        _check_digest(reference_sha256, "reference")
        at = _timestamp(at_utc)
        with self._lock:
            m = self._get_locked(mission_id)
            event = self._build_event(m, "reference_linked", at, actor_id, m.status,
                                      reference={"kind": kind, "sha256": reference_sha256})
            return self._commit_locked(m, event)

    def assign_component(self, mission_id: str, *, component: str, actor_id: str,
                         at_utc: str | None = None) -> dict[str, Any]:
        """Record a participating subsystem, without granting it capabilities."""
        mission_id = _check_id(mission_id, "mission")
        _require(type(component) is str and component in COMPONENTS,
                 "invalid component")
        at = _timestamp(at_utc)
        with self._lock:
            m = self._get_locked(mission_id)
            event = self._build_event(m, "component_assigned", at, actor_id, m.status,
                                      component=component)
            return self._commit_locked(m, event)

    def _get_locked(self, mission_id: str) -> _Mission:
        try:
            return self._missions[mission_id]
        except KeyError as exc:
            raise NexusMissionRegistryError("unknown mission") from exc

    def get(self, mission_id: str) -> dict[str, Any]:
        mission_id = _check_id(mission_id, "mission")
        with self._lock:
            return self._get_locked(mission_id).summary()

    def history(self, mission_id: str) -> tuple[dict[str, Any], ...]:
        mission_id = _check_id(mission_id, "mission")
        with self._lock:
            return tuple(deepcopy(self._get_locked(mission_id).events))

    def select(self, *, product: str | None = None, status: str | None = None,
               owner_id: str | None = None) -> tuple[dict[str, Any], ...]:
        if product is not None:
            _require(type(product) is str and product in PRODUCTS, "invalid product filter")
        if status is not None:
            _require(type(status) is str and status in STATUSES, "invalid status filter")
        owner_hash = _actor_hash(owner_id) if owner_id is not None else None
        with self._lock:
            return tuple(self._missions[k].summary() for k in sorted(self._missions)
                         if (product is None or self._missions[k].product == product)
                         and (status is None or self._missions[k].status == status)
                         and (owner_hash is None or self._missions[k].owner_sha256 == owner_hash))

    def snapshot(self) -> dict[str, Any]:
        """Export a deterministic, hash-linked JSON snapshot without raw text."""
        with self._lock:
            body = self._body_locked()
            _require(len(_encode(body)) <= self.limits.max_snapshot_bytes,
                     "snapshot byte budget exceeded")
            return {**body, "registry_sha256": _digest(body)}

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any], *,
                      limits: MissionRegistryLimits | None = None) -> "NexusMissionRegistry":
        """Revalidate the WHOLE state machine and every event, not just the hash."""
        registry = cls(limits=limits)
        _require(type(snapshot) is dict and set(snapshot) == _SNAPSHOT_FIELDS,
                 "invalid registry snapshot keys")
        _require(snapshot["schema"] == MISSION_REGISTRY_SCHEMA,
                 "invalid registry schema")
        _require(snapshot["integrity_only"] is True and
                 snapshot["authenticity_verified"] is False and
                 snapshot["effects_executed"] is False, "invalid trust or execution flags")
        body = {k: v for k, v in snapshot.items() if k != "registry_sha256"}
        _check_digest(snapshot["registry_sha256"], "registry")
        _require(len(_encode(body)) <= registry.limits.max_snapshot_bytes,
                 "snapshot byte budget exceeded")
        _require(hmac.compare_digest(snapshot["registry_sha256"], _digest(body)),
                 "snapshot SHA-256 mismatch")
        rows = snapshot["missions"]
        _require(type(rows) is list and type(snapshot["mission_count"]) is int and
                 type(snapshot["event_count"]) is int and
                 0 <= len(rows) == snapshot["mission_count"] <= registry.limits.max_missions,
                 "invalid mission count")
        _require(0 <= snapshot["event_count"] <= registry.limits.max_total_events,
                 "invalid total event count")
        previous_id = ""
        for row in rows:
            _require(type(row) is dict and set(row) == _RECORD_FIELDS,
                     "invalid mission record keys")
            mission_id = _check_id(row["mission_id"], "mission")
            _require(mission_id > previous_id, "missions must have unique, sorted IDs")
            previous_id = mission_id
            events = row["events"]
            _require(type(events) is list and 1 <= len(events) <= registry.limits.max_events_per_mission,
                     "invalid mission history length")
            first = events[0]
            _require(type(first) is dict and first.get("operation") == "registered",
                     "mission registration event is missing")
            registration = first.get("registration")
            _require(type(registration) is dict and
                     set(registration) == {"product", "owner_sha256", "objective_sha256"},
                     "invalid registration metadata")
            _require(type(registration["product"]) is str and registration["product"] in PRODUCTS,
                     "invalid product")
            _check_digest(registration["owner_sha256"], "owner")
            _check_digest(registration["objective_sha256"], "objective")
            at = _timestamp(first.get("at_utc")) if first.get("at_utc") is not None else None
            _require(at is not None, "initial timestamp required")
            m = _Mission(mission_id, registration["product"], registration["owner_sha256"],
                         registration["objective_sha256"], "draft", at, at)
            for event in events:
                _apply_event(m, event, registry.limits)
            _require(row == m.export(), "mission summary does not match history")
            registry._missions[mission_id] = m
        _require(sum(len(m.events) for m in registry._missions.values()) == snapshot["event_count"],
                 "event count mismatch")
        _require(registry.snapshot() == snapshot,
                 "registry snapshot differs from replayed history")
        return registry

    @staticmethod
    def verify_snapshot(snapshot: Mapping[str, Any], *,
                        max_bytes: int = 8_000_000) -> bool:
        """False on invalid input; authenticity requires independent validation."""
        if type(max_bytes) is not int or not 4096 <= max_bytes <= 8_000_000:
            return False
        try:
            NexusMissionRegistry.from_snapshot(
                snapshot, limits=MissionRegistryLimits(
                    max_missions=1024, max_events_per_mission=2048,
                    max_references_per_mission=512, max_components_per_mission=32,
                    max_total_events=30000,
                    max_snapshot_bytes=max_bytes))
            return True
        except (NexusMissionRegistryError, TypeError, ValueError, RecursionError,
                OverflowError, KeyError, AttributeError):
            return False

    def to_proof_graph(self, mission_id: str) -> dict[str, Any]:
        """Build a separate ProofGraph *record*, not a verification claim.

        A registry-attestation run is synthetic and labeled internal; linked
        evidence remains 'observed' rather than 'verified'. Unsupported link
        types remain available in registry snapshots but are not reinterpreted.
        """
        from ..proof.proof_graph import NexusProofGraph
        mission_id = _check_id(mission_id, "mission")
        with self._lock:
            rec = self._get_locked(mission_id)
            row = rec.summary()
        graph = NexusProofGraph(mission_id)
        mission_node = graph.add_node("mission", mission_id, status="observed")
        audit_run = graph.add_node("run", f"registry.{mission_id}", status="observed",
                                   source="internal", digest_sha256=row["head_sha256"])
        graph.add_edge("contains", mission_node, audit_run)
        for ref in row["references"]:
            kind, digest = ref["kind"], ref["sha256"]
            if kind == "run":
                node = graph.add_node("run", digest, status="observed",
                                      source="external", digest_sha256=digest)
                graph.add_edge("contains", mission_node, node)
            elif kind == "evidence":
                node = graph.add_node("evidence", digest, status="observed",
                                      source="external", digest_sha256=digest)
                graph.add_edge("records", audit_run, node)
            elif kind == "artifact":
                graph.add_node("artifact", digest, status="observed", source="external",
                               digest_sha256=digest)
        return graph.snapshot()


__all__ = [
    "MISSION_REGISTRY_SCHEMA", "PRODUCTS", "STATUSES", "REFERENCE_KINDS", "COMPONENTS", "TRANSITIONS",
    "NexusMissionRegistryError", "MissionRegistryLimits", "NexusMissionRegistry",
]
