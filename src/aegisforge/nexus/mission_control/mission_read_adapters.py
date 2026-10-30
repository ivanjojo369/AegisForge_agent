"""Explicit read-only adapters for Nexus Mission Control (Colossus).

Destination: src/aegisforge/nexus/mission_control/mission_read_adapters.py

This module is the FIRST *real* but narrowly bounded integration following
mission_bridge.py. It invokes only three specific in-memory, read-only methods
of pre-existing Nexus components. There is deliberately no generic dispatcher,
import-by-name, reflection on user input, filesystem, model, network or shell
access; request_effect and all review/authorization operations are rejected.

Only a trusted host may inject the three source objects and a bridge plan.
Bridge/receipt hashes protect metadata consistency, NOT truth, authenticity,
permissions, or an authority to invoke any other operation. A mutable host can
change between reads: the source and registry are rechecked after each read,
but this is not an atomic multi-component transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Mapping

from .mission_registry import NexusMissionRegistry
from .mission_bridge import (
    MissionBridgeCatalog, MissionBridgePlan, MissionBridgePort,
    verify_bridge_plan,
)
from ..lab.scenario_catalog import NexusScenarioCatalog
from ..proof.proof_graph import NexusProofGraph

READ_ADAPTER_SCHEMA = "aegisforge.nexus.mission_control.read_adapter.v1"
_RECEIPT_FIELDS = frozenset({
    "schema", "mission_id", "product", "step_id", "component", "operation",
    "adapter_id", "contract_sha256", "bridge_sha256", "registry_sha256",
    "mission_head_sha256", "source_sha256", "summary", "integrity_only",
    "authenticity_verified", "authorization_granted", "effects_executed",
    "receipt_sha256",
})
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_MAX_BYTES = 16_384

# Pure contract descriptors: stable names, version, approved read-only fields.
# These are for host-owned configuration, NOT cryptographic authenticity.
_DESCRIPTORS = (
    ("lab", "summarize", "local.lab_catalog.v1",
     ("catalog_sha256", "count", "by_product", "by_mode")),
    ("mission_control", "summarize_mission", "local.mission_summary.v1",
     ("head_sha256", "status", "product", "components", "reference_count", "event_count")),
    ("proof", "verify_integrity", "local.proof_graph.v1",
     ("graph_sha256", "node_count", "edge_count")),
)


class NexusMissionReadAdapterError(ValueError):
    """Invalid or stale bridge, missing source, or inconsistent read."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NexusMissionReadAdapterError(message)


def _canonical(data: Any) -> bytes:
    try:
        return json.dumps(data, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionReadAdapterError("invalid JSON metadata") from exc


def _sha256(data: Any) -> str:
    return sha256(_canonical(data)).hexdigest()


def _contracts() -> MissionBridgeCatalog:
    ports = []
    for component, operation, adapter_id, fields in _DESCRIPTORS:
        contract = {
            "schema": READ_ADAPTER_SCHEMA,
            "component": component,
            "operation": operation,
            "adapter_id": adapter_id,
            "read_only": True,
            "summary_fields": list(fields),
        }
        ports.append(MissionBridgePort(
            component, operation, adapter_id, _sha256(contract)))
    return MissionBridgeCatalog(tuple(ports))


READ_ONLY_CATALOG = _contracts()


@dataclass(frozen=True, slots=True)
class MissionReadReceipt:
    """Immutable JSON-backed metadata receipt; not a permission token."""

    _json: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    @property
    def sha256(self) -> str:
        return self.to_dict()["receipt_sha256"]


class NexusMissionReadAdapters:
    """Call ONLY explicitly coded read methods of trusted in-memory objects.

    No arbitrary operation/adapter identifier is executed. The supplied bridge
    must be fully bound to READ_ONLY_CATALOG and to the current mission registry;
    even then, it is a request for a read preview, not an authorization.
    """

    def __init__(self, registry: NexusMissionRegistry, *,
                 scenarios: NexusScenarioCatalog | None = None,
                 proof_graph: NexusProofGraph | None = None) -> None:
        _require(type(registry) is NexusMissionRegistry, "expected a NexusMissionRegistry")
        _require(scenarios is None or type(scenarios) is NexusScenarioCatalog,
                 "invalid laboratory catalog source")
        _require(proof_graph is None or type(proof_graph) is NexusProofGraph,
                 "invalid ProofGraph source")
        self._registry = registry
        self._scenarios = scenarios
        self._proof_graph = proof_graph

    @property
    def catalog(self) -> MissionBridgeCatalog:
        return READ_ONLY_CATALOG

    def _validated(self, plan: MissionBridgePlan | Mapping[str, Any]) -> dict[str, Any]:
        if type(plan) is MissionBridgePlan:
            data = plan.to_dict()
        else:
            _require(type(plan) is dict, "expected a bridge plan")
            data = plan
        _require(verify_bridge_plan(data, registry=self._registry,
                                    catalog=self.catalog),
                 "invalid or stale bridge plan")
        _require(data["outcome"] == "host_review_required" and
                 data["coordination"]["outcome"] == "host_review_required",
                 "bridge is blocked")
        _require(not data["required_action_reviews"],
                 "action review/effect workflows not supported")
        pairs = {(p.component, p.operation) for p in self.catalog.ports}
        _require(all((h["component"], h["operation"]) in pairs and
                     h["binding_status"] == "metadata_binding_present" and
                     h["metadata_only"] is True and h["intent_sha256"] is None
                     for h in data["handoffs"]),
                 "workflow contains an unsupported or effectful step")
        return data

    def _source_summary(self, component: str, operation: str,
                        mission_id: str, product: str) -> tuple[str, dict[str, Any]]:
        if (component, operation) == ("mission_control", "summarize_mission"):
            # Registry validation has already been done by verify_bridge_plan.
            snapshot = self._registry.snapshot()
            mission = next((m for m in snapshot["missions"]
                            if m["mission_id"] == mission_id), None)
            _require(mission is not None, "mission no longer exists")
            return snapshot["registry_sha256"], {
                "status": mission["status"], "product": mission["product"],
                "head_sha256": mission["head_sha256"],
                "components": list(mission["components"]),
                "reference_count": mission["reference_count"],
                "event_count": mission["event_count"],
            }
        if (component, operation) == ("lab", "summarize"):
            _require(self._scenarios is not None, "laboratory source missing")
            snapshot = self._scenarios.snapshot()
            _require(NexusScenarioCatalog.verify_snapshot(snapshot,
                                                            limits=self._scenarios.limits),
                     "laboratory snapshot failed validation")
            by_product = {name: 0 for name in sorted(("nexus", "aegisgrid", "live_forge", "colossus"))}
            by_mode = {name: 0 for name in ("offline", "simulated", "external")}
            for case in snapshot["cases"]:
                by_product[case["product"]] += 1
                by_mode[case["mode"]] += 1
            return snapshot["catalog_sha256"], {
                "catalog_sha256": snapshot["catalog_sha256"],
                "count": snapshot["count"],
                "by_product": by_product,
                "by_mode": by_mode,
            }
        if (component, operation) == ("proof", "verify_integrity"):
            _require(self._proof_graph is not None, "ProofGraph source missing")
            snapshot = self._proof_graph.snapshot()
            _require(snapshot["mission_id"] == mission_id,
                     "ProofGraph is associated with another mission")
            _require(NexusProofGraph.verify_snapshot(snapshot),
                     "ProofGraph snapshot failed validation")
            return snapshot["graph_sha256"], {
                "graph_sha256": snapshot["graph_sha256"],
                "node_count": snapshot["node_count"],
                "edge_count": snapshot["edge_count"],
            }
        raise NexusMissionReadAdapterError("unsupported read-only operation")

    def _current_source_sha(self, component: str, operation: str) -> str:
        """Recheck source freshness after creating a summary (best effort)."""
        if (component, operation) == ("mission_control", "summarize_mission"):
            return self._registry.snapshot()["registry_sha256"]
        if (component, operation) == ("lab", "summarize"):
            _require(self._scenarios is not None, "laboratory source missing")
            return self._scenarios.snapshot()["catalog_sha256"]
        if (component, operation) == ("proof", "verify_integrity"):
            _require(self._proof_graph is not None, "ProofGraph source missing")
            return self._proof_graph.snapshot()["graph_sha256"]
        raise NexusMissionReadAdapterError("unsupported read-only operation")

    def inspect(self, plan: MissionBridgePlan | Mapping[str, Any], *,
                step_id: str) -> MissionReadReceipt:
        """Run one allowlisted metadata read; never execute a workflow step.

        This does not mark a step completed and does not claim authorization.
        Does not access any actual files, network endpoints, or credentials.
        """
        _require(type(step_id) is str and _ID.fullmatch(step_id) is not None,
                 "invalid step identifier")
        data = self._validated(plan)
        entry = next((h for h in data["handoffs"] if h["step_id"] == step_id), None)
        _require(entry is not None, "step not present in bridge")
        _require(data["product"] in ("nexus", "aegisgrid", "live_forge", "colossus"),
                 "invalid mission product")
        registry_anchor = self._registry.snapshot()
        _require(registry_anchor["registry_sha256"] == data["registry_sha256"],
                 "registry changed since bridge preparation")
        source_sha, summary = self._source_summary(
            entry["component"], entry["operation"], data["mission_id"], data["product"])
        _require(self._registry.snapshot()["registry_sha256"] ==
                 registry_anchor["registry_sha256"],
                 "registry changed during inspection")
        _require(self._current_source_sha(entry["component"], entry["operation"]) == source_sha,
                 "source changed during inspection")
        # Post-read rechecks reduce accidental mixed snapshots; they are NOT
        # an atomic transaction across multiple independent components.
        body = {
            "schema": READ_ADAPTER_SCHEMA,
            "mission_id": data["mission_id"],
            "product": data["product"],
            "step_id": step_id,
            "component": entry["component"],
            "operation": entry["operation"],
            "adapter_id": entry["adapter_id"],
            "contract_sha256": entry["contract_sha256"],
            "bridge_sha256": data["bridge_sha256"],
            "registry_sha256": data["registry_sha256"],
            "mission_head_sha256": data["mission_head_sha256"],
            "source_sha256": source_sha,
            "summary": summary,
            "integrity_only": True,
            "authenticity_verified": False,
            "authorization_granted": False,
            "effects_executed": False,
        }
        _require(len(_canonical(body)) <= _MAX_BYTES,
                 "read receipt exceeds byte budget")
        payload = {**body, "receipt_sha256": _sha256(body)}
        return MissionReadReceipt(_canonical(payload))

    def verify_receipt(self, receipt: Mapping[str, Any],
                       plan: MissionBridgePlan | Mapping[str, Any]) -> bool:
        """Re-read the current source and compare an earlier read-only receipt.

        Revalidation establishes current consistency only; not authenticity.
        """
        try:
            if type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS:
                return False
            if len(_canonical(receipt)) > _MAX_BYTES + 256:
                return False
            if (receipt["schema"] != READ_ADAPTER_SCHEMA or
                    receipt["integrity_only"] is not True or
                    receipt["authenticity_verified"] is not False or
                    receipt["authorization_granted"] is not False or
                    receipt["effects_executed"] is not False):
                return False
            unsigned = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
            if not (type(receipt["receipt_sha256"]) is str and
                    hmac.compare_digest(receipt["receipt_sha256"], _sha256(unsigned))):
                return False
            current = self.inspect(plan, step_id=receipt["step_id"]).to_dict()
            return current == receipt
        except (NexusMissionReadAdapterError, TypeError, ValueError, KeyError,
                AttributeError, RecursionError, OverflowError):
            return False


__all__ = [
    "READ_ADAPTER_SCHEMA", "READ_ONLY_CATALOG", "MissionReadReceipt",
    "NexusMissionReadAdapterError", "NexusMissionReadAdapters",
]
