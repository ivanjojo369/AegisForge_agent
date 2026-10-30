"""Fail-closed, read-only mission runtime for AegisForge Nexus / Colossus.

Instalar: src/aegisforge/nexus/mission_control/mission_runtime.py

Coordinates the *existing* MissionState -> Coordinator -> Bridge -> explicitly
allowlisted ReadAdapters chain. This is not an effects runner: it neither
executes arbitrary callables nor accepts action approvals as capabilities.
Even a Trust read receipt is not an execution permission. A real effects
runtime would require separately audited, action-bound host dispatch.

Snapshots/hashes mean internal consistency, NOT source authenticity. Source
objects may mutate concurrently; repeated checks reduce mixed observations but
cannot make a multi-component read atomic. Host must manage trust boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from threading import RLock
from typing import Any, Mapping

from .mission_registry import NexusMissionRegistry, NexusMissionRegistryError
from .mission_coordinator import (
    NexusMissionCoordinator, NexusMissionCoordinatorError,
    verify_coordination_preview,
)
from .mission_bridge import (
    NexusMissionBridge, NexusMissionBridgeError, verify_bridge_plan,
)
from .mission_read_adapters import (
    READ_ADAPTER_SCHEMA, NexusMissionReadAdapters, NexusMissionReadAdapterError,
)

MISSION_RUNTIME_SCHEMA = "aegisforge.nexus.mission_control.runtime.v1"
MISSION_RUNTIME_PREVIEW_SCHEMA = "aegisforge.nexus.mission_control.runtime_preview.v1"
_MAX_STEPS = 64
_MAX_RECEIPT_BYTES = 16_640
_MAX_REPORT_BYTES = 1_048_576
_MAX_ERRORS = 8
_SHA = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_RECEIPT_KEYS = frozenset({
    "schema", "mission_id", "product", "step_id", "component", "operation",
    "adapter_id", "contract_sha256", "bridge_sha256", "registry_sha256",
    "mission_head_sha256", "source_sha256", "summary", "integrity_only",
    "authenticity_verified", "authorization_granted", "effects_executed",
    "receipt_sha256",
})
_OUTCOMES = frozenset({"blocked", "completed_read_only", "failed", "stale"})
_PREVIEW_KEYS = frozenset({
    "schema", "mission_id", "product", "target_status", "outcome", "reasons",
    "registry_sha256", "mission_head_sha256", "coordination_sha256",
    "bridge_sha256", "adapter_catalog_sha256", "planned_layers", "step_count",
    "integrity_only", "authorization_granted", "effects_executed", "preview_sha256",
})
_REPORT_KEYS = frozenset({
    "schema", "mission_id", "product", "target_status", "outcome", "reasons",
    "preview", "step_count", "completed_count", "receipt_count", "receipts",
    "registry_sha256", "bridge_sha256", "integrity_only",
    "authenticity_verified", "authorization_granted", "effects_executed",
    "report_sha256",
})


class NexusMissionRuntimeError(ValueError):
    """A malformed, stale, unsupported or unsafe runtime request."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise NexusMissionRuntimeError(reason)


def _encode(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionRuntimeError("invalid runtime JSON metadata") from exc


def _sha(value: Any) -> str:
    return sha256(_encode(value)).hexdigest()


def _digest_valid(value: Any) -> bool:
    return type(value) is str and _SHA.fullmatch(value) is not None


def _sealed(body: dict[str, Any], field: str) -> bytes:
    payload = {**body, field: _sha(body)}
    raw = _encode(payload)
    _require(len(raw) <= _MAX_REPORT_BYTES, "runtime result exceeds byte budget")
    return raw


def _flags(payload: dict[str, Any]) -> bool:
    return (payload.get("integrity_only") is True and
            payload.get("authorization_granted") is False and
            payload.get("effects_executed") is False)


@dataclass(frozen=True, slots=True)
class MissionRuntimePreview:
    """Immutable, JSON-backed readiness preview, NOT an authorization token."""

    _json: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    @property
    def outcome(self) -> str:
        return self.to_dict()["outcome"]


@dataclass(frozen=True, slots=True)
class MissionRuntimeReport:
    """Immutable read-only observation report; no source code, paths or secrets."""

    _json: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    @property
    def outcome(self) -> str:
        return self.to_dict()["outcome"]


def verify_runtime_preview(data: Any) -> bool:
    """Verify internal structure and hash, never provenance or permissions."""
    try:
        if type(data) is not dict or set(data) != _PREVIEW_KEYS:
            return False
        if len(_encode(data)) > _MAX_REPORT_BYTES or not _flags(data):
            return False
        if data["schema"] != MISSION_RUNTIME_PREVIEW_SCHEMA:
            return False
        if data["outcome"] not in ("blocked", "host_review_required"):
            return False
        if any(not _digest_valid(data[k]) for k in (
            "registry_sha256", "mission_head_sha256", "coordination_sha256",
            "bridge_sha256", "adapter_catalog_sha256", "preview_sha256",
        )):
            return False
        if type(data["step_count"]) is not int or not 0 < data["step_count"] <= _MAX_STEPS:
            return False
        layers = data["planned_layers"]
        if type(layers) is not list or not 1 <= len(layers) <= _MAX_STEPS:
            return False
        ids = []
        for layer in layers:
            if type(layer) is not list or not layer or layer != sorted(set(layer)):
                return False
            if any(type(x) is not str or len(x) > 64 for x in layer):
                return False
            ids.extend(layer)
        if len(ids) != len(set(ids)) or len(ids) != data["step_count"]:
            return False
        reasons = data["reasons"]
        if type(reasons) is not list or reasons != sorted(set(reasons)) or len(reasons) > 96:
            return False
        if any(type(x) is not str or len(x) > 160 for x in reasons):
            return False
        if data["outcome"] == "blocked" and not reasons:
            return False
        if data["outcome"] != "blocked" and reasons:
            return False
        if not (_ID.fullmatch(data["mission_id"]) if type(data["mission_id"]) is str else False) or not all(type(data[k]) is str for k in ("product", "target_status")):
            return False
        unsigned = {k: v for k, v in data.items() if k != "preview_sha256"}
        return hmac.compare_digest(_sha(unsigned), data["preview_sha256"])
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError):
        return False


def verify_runtime_report(data: Any) -> bool:
    """Validate structure and receipt digests; does not authenticate observations."""
    try:
        if type(data) is not dict or set(data) != _REPORT_KEYS:
            return False
        if data["schema"] != MISSION_RUNTIME_SCHEMA or not _flags(data) or data["authenticity_verified"] is not False:
            return False
        if len(_encode(data)) > _MAX_REPORT_BYTES or data["outcome"] not in _OUTCOMES:
            return False
        preview = data["preview"]
        if not verify_runtime_preview(preview):
            return False
        if any(data[k] != preview[k] for k in ("mission_id", "product", "target_status", "registry_sha256", "bridge_sha256", "step_count")):
            return False
        receipts = data["receipts"]
        if type(receipts) is not list or len(receipts) > _MAX_STEPS:
            return False
        if type(data["completed_count"]) is not int or data["completed_count"] != len(receipts):
            return False
        if type(data["receipt_count"]) is not int or data["receipt_count"] != len(receipts):
            return False
        if data["outcome"] == "blocked" and (receipts or preview["outcome"] != "blocked"):
            return False
        if data["outcome"] != "blocked" and preview["outcome"] != "host_review_required":
            return False
        if data["outcome"] == "completed_read_only" and (len(receipts) != data["step_count"] or preview["outcome"] != "host_review_required"):
            return False
        if type(data["reasons"]) is not list or len(data["reasons"]) > _MAX_ERRORS or data["reasons"] != sorted(set(data["reasons"])):
            return False
        if any(type(r) is not str or len(r) > 160 for r in data["reasons"]):
            return False
        if data["outcome"] != "completed_read_only" and not data["reasons"]:
            return False
        if data["outcome"] == "completed_read_only" and data["reasons"]:
            return False
        allowed = [s for layer in preview["planned_layers"] for s in layer]
        ids = [r.get("step_id") for r in receipts if type(r) is dict]
        if len(ids) != len(receipts) or ids != allowed[:len(ids)]:
            return False
        for receipt in receipts:
            if set(receipt) != _RECEIPT_KEYS or len(_encode(receipt)) > _MAX_RECEIPT_BYTES:
                return False
            if (receipt.get("schema") != READ_ADAPTER_SCHEMA or
                    receipt.get("authenticity_verified") is not False or
                    receipt.get("product") != data["product"] or
                    receipt.get("mission_head_sha256") != preview["mission_head_sha256"] or
                    any(not _digest_valid(receipt.get(k)) for k in (
                        "contract_sha256", "source_sha256", "mission_head_sha256")) or
                    receipt.get("mission_id") != data["mission_id"] or
                    receipt.get("bridge_sha256") != data["bridge_sha256"] or
                    receipt.get("registry_sha256") != data["registry_sha256"] or
                    receipt.get("authorization_granted") is not False or
                    receipt.get("effects_executed") is not False or
                    receipt.get("integrity_only") is not True):
                return False
            h = receipt.get("receipt_sha256")
            if not _digest_valid(h) or _sha({k: v for k, v in receipt.items() if k != "receipt_sha256"}) != h:
                return False
        if not _digest_valid(data["report_sha256"]):
            return False
        body = {k: v for k, v in data.items() if k != "report_sha256"}
        return hmac.compare_digest(_sha(body), data["report_sha256"])
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, RecursionError):
        return False


class NexusMissionRuntime:
    """Orchestrate existing read-only handlers in validated topological order.

    The adapter registry and coordinator are trusted host-injected objects,
    not constructed from strings or user-supplied import paths.
    Effects/reviews/unknown operations are blocked rather than dispatched.
    """

    def __init__(self, registry: NexusMissionRegistry,
                 coordinator: NexusMissionCoordinator,
                 adapters: NexusMissionReadAdapters) -> None:
        _require(type(registry) is NexusMissionRegistry, "expected mission registry")
        _require(type(coordinator) is NexusMissionCoordinator, "expected mission coordinator")
        _require(isinstance(adapters, NexusMissionReadAdapters), "expected a supported read adapter")
        _require(adapters._registry is registry, "adapter must use the same registry")
        self.registry = registry
        self.coordinator = coordinator
        self.adapters = adapters
        self._lock = RLock()

    def _prepare_internal(self) -> tuple[MissionRuntimePreview, dict[str, Any] | None]:
        coordination = self.coordinator.preview(self.registry).to_dict()
        _require(verify_coordination_preview(coordination), "invalid coordination preview")
        bridge = NexusMissionBridge(self.adapters.catalog).plan(coordination, self.registry).to_dict()
        _require(verify_bridge_plan(bridge, registry=self.registry,
                                    coordination=coordination, catalog=self.adapters.catalog),
                 "invalid or stale bridge plan")
        reasons = set()
        if coordination["outcome"] != "host_review_required":
            reasons.add("coordination_blocked")
        if bridge["outcome"] != "host_review_required":
            reasons.add("adapter_binding_incomplete")
        if coordination["required_action_reviews"]:
            reasons.add("effectful_workflow_not_supported")
        if any(step["operation"] == "request_effect" or
               (step["component"] == "trust" and step["operation"] in ("review_action", "review_transition"))
               for step in coordination["blueprint"]["steps"]):
            reasons.add("authorization_or_effect_step_not_supported")
        if any(row["binding_status"] != "metadata_binding_present" or
               row["metadata_only"] is not True or row["intent_sha256"] is not None
               for row in bridge["handoffs"]):
            reasons.add("unsupported_step_binding")
        body = {
            "schema": MISSION_RUNTIME_PREVIEW_SCHEMA,
            "mission_id": coordination["mission_id"], "product": coordination["product"],
            "target_status": coordination["target_status"],
            "outcome": "blocked" if reasons else "host_review_required",
            "reasons": sorted(reasons), "registry_sha256": coordination["registry_sha256"],
            "mission_head_sha256": coordination["mission_head_sha256"],
            "coordination_sha256": coordination["coordination_sha256"],
            "bridge_sha256": bridge["bridge_sha256"],
            "adapter_catalog_sha256": self.adapters.catalog.sha256,
            "planned_layers": coordination["planned_layers"],
            "step_count": len(coordination["blueprint"]["steps"]),
            "integrity_only": True, "authorization_granted": False,
            "effects_executed": False,
        }
        raw = _sealed(body, "preview_sha256")
        _require(verify_runtime_preview(json.loads(raw)), "runtime preview invariant failure")
        return MissionRuntimePreview(raw), bridge

    def preview(self) -> MissionRuntimePreview:
        with self._lock:
            return self._prepare_internal()[0]

    def inspect(self) -> MissionRuntimeReport:
        """Execute allowed read-only inspections only, fail closed on drift.

        Exceptions from adapters become opaque failure codes: sensitive
        exception messages (paths etc.) are never returned in the report.
        """
        with self._lock:
            preview, bridge = self._prepare_internal()
            p = preview.to_dict()
            receipts: list[dict[str, Any]] = []
            outcome = "blocked" if p["outcome"] == "blocked" else "completed_read_only"
            reasons = list(p["reasons"])
            if outcome != "blocked":
                for step_id in [s for layer in p["planned_layers"] for s in layer]:
                    if self.registry.snapshot()["registry_sha256"] != p["registry_sha256"]:
                        outcome, reasons = "stale", ["registry_changed_during_inspection"]
                        break
                    try:
                        receipt = self.adapters.inspect(bridge, step_id=step_id).to_dict()
                        _require(self.adapters.verify_receipt(receipt, bridge),
                                 "source did not remain stable")
                    except (NexusMissionReadAdapterError, NexusMissionRuntimeError):
                        outcome, reasons = "failed", ["read_adapter_rejected_step"]
                        break
                    except Exception:
                        outcome, reasons = "failed", ["read_adapter_unavailable"]
                        break
                    receipts.append(receipt)
                    if sum(len(_encode(r)) for r in receipts) > _MAX_REPORT_BYTES // 2:
                        outcome, reasons = "failed", ["receipt_budget_exceeded"]
                        break
                if outcome == "completed_read_only":
                    try:
                        if self.registry.snapshot()["registry_sha256"] != p["registry_sha256"]:
                            outcome, reasons = "stale", ["registry_changed_during_inspection"]
                        elif not all(self.adapters.verify_receipt(r, bridge) for r in receipts):
                            outcome, reasons = "stale", ["source_changed_during_inspection"]
                    except Exception:
                        outcome, reasons = "failed", ["source_recheck_unavailable"]
            body = {
                "schema": MISSION_RUNTIME_SCHEMA, "mission_id": p["mission_id"],
                "product": p["product"], "target_status": p["target_status"],
                "outcome": outcome, "reasons": sorted(set(reasons)), "preview": p,
                "step_count": p["step_count"], "completed_count": len(receipts),
                "receipt_count": len(receipts), "receipts": receipts,
                "registry_sha256": p["registry_sha256"],
                "bridge_sha256": p["bridge_sha256"],
                "integrity_only": True, "authenticity_verified": False,
                "authorization_granted": False, "effects_executed": False,
            }
            raw = _sealed(body, "report_sha256")
            _require(verify_runtime_report(json.loads(raw)), "runtime report invariant failure")
            return MissionRuntimeReport(raw)


__all__ = [
    "MISSION_RUNTIME_SCHEMA", "MISSION_RUNTIME_PREVIEW_SCHEMA",
    "NexusMissionRuntimeError", "MissionRuntimePreview", "MissionRuntimeReport",
    "verify_runtime_preview", "verify_runtime_report", "NexusMissionRuntime",
]
