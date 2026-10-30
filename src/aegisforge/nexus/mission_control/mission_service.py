"""Authenticated, in-process Mission Control service for AegisForge Nexus.

Instalar: src/aegisforge/nexus/mission_control/mission_service.py

A host-owned authorization callback is MANDATORY for all operations. This file
exposes no HTTP routes, network listeners, persistence, arbitrary dispatch, or
model/tool execution. Caller-provided actor_id is NOT authentication; the host
callback must authenticate that actor, scope and permission independently.
State transitions are assessed against host policy and audited by MissionRegistry.
All mutations require an optimistic registry SHA anchor; for reliable CAS, the
host must make this service the exclusive writer of its registry instance.

SHA-256 confirms internal consistency only: NOT trusted identity, evidence,
secure persistence or an execution grant. Do not publish this service directly
as an unauthenticated web API.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from threading import RLock
from typing import Any, Callable

from .mission_registry import (
    NexusMissionRegistry, NexusMissionRegistryError, PRODUCTS, STATUSES,
)
from .mission_state import (
    NexusMissionState, NexusMissionStateError, verify_assessment,
)
from .mission_coordinator import NexusMissionCoordinator
from .mission_read_adapters import NexusMissionReadAdapters
from .mission_runtime import (
    NexusMissionRuntime, NexusMissionRuntimeError, MissionRuntimePreview,
    MissionRuntimeReport,
)

MISSION_SERVICE_SCHEMA = "aegisforge.nexus.mission_control.service.v1"
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_ACTIONS = frozenset({
    "create", "read", "list", "history", "export_snapshot",
    "assign_component", "link_reference", "transition", "configure",
    "preview", "inspect",
})


class NexusMissionServiceError(ValueError):
    """Denied authorization, state mismatch, invalid source or stale mutation."""


# Host callback: (authenticated actor ID, exact action, mission ID or '*',
#                 current registry SHA-256, exact sanitized request metadata)
#                 -> literal True only on approval.
# Host must validate identities itself; hashes are not bearer credentials.
HostMissionAuthorizer = Callable[[str, str, str, str, dict[str, Any]], bool]


def _require(ok: bool, why: str) -> None:
    if not ok:
        raise NexusMissionServiceError(why)


def _opaque(value: Any, name: str) -> str:
    _require(type(value) is str and _ID.fullmatch(value) is not None,
             f"invalid {name} identifier")
    return value


def _digest(value: Any, name: str) -> str:
    _require(type(value) is str and _SHA.fullmatch(value) is not None,
             f"invalid {name} hash")
    return value


def _encode(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionServiceError("invalid service metadata") from exc


def _sha(data: Any) -> str:
    return sha256(_encode(data)).hexdigest()


@dataclass(frozen=True, slots=True)
class MissionServiceStatus:
    """A source-backed read view; status is not an authorization token."""

    _json: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)


class NexusMissionService:
    """Host-authorized lifecycle facade and read-only runtime integration.

    The registry itself has no built-in actor authentication; this facade
    ensures ALL accessible public operations demand host authorization.
    An injected registry may still be mutated by its other holders. The host
    must keep it private or synchronize every writer on the same lock.
    """

    def __init__(self, registry: NexusMissionRegistry, *,
                 authorizer: HostMissionAuthorizer) -> None:
        _require(type(registry) is NexusMissionRegistry, "expected a mission registry")
        _require(callable(authorizer), "trusted host authorizer is required")
        self._registry = registry
        self._authorizer = authorizer
        self._runtimes: dict[str, NexusMissionRuntime] = {}
        self._lock = RLock()

    def _authorize(self, actor_id: str, action: str, mission_id: str,
                   anchor: str, request: dict[str, Any]) -> None:
        _opaque(actor_id, "actor")
        _require(action in _ACTIONS, "unsupported service action")
        _require(mission_id == "*" or _ID.fullmatch(mission_id) is not None,
                 "invalid mission identifier")
        _digest(anchor, "registry anchor")
        _require(type(request) is dict, "invalid authorization request")
        safe_request = _encode(request)
        _require(len(safe_request) <= 4096, "authorization request exceeds byte budget")
        try:
            approved = self._authorizer(actor_id, action, mission_id, anchor,
                                        json.loads(safe_request))
        except Exception as exc:
            raise NexusMissionServiceError("host authorization unavailable") from exc
        _require(approved is True, "host authorization denied")
        _require(hmac.compare_digest(self._registry.snapshot()["registry_sha256"], anchor),
                 "registry changed during authorization")

    def _begin(self, actor_id: str, action: str, mission_id: str,
               expected_registry_sha256: str | None = None,
               *, request: dict[str, Any] | None = None) -> str:
        anchor = self._registry.snapshot()["registry_sha256"]
        if expected_registry_sha256 is not None:
            _require(hmac.compare_digest(
                _digest(expected_registry_sha256, "expected registry"), anchor),
                "stale registry version")
        self._authorize(actor_id, action, mission_id, anchor,
                        {} if request is None else request)
        return anchor

    def registry_version(self, *, actor_id: str) -> str:
        """Get current SHA anchor after host authorization (no raw data)."""
        with self._lock:
            return self._begin(actor_id, "list", "*")

    def create_mission(self, mission_id: str, *, product: str, owner_id: str,
                       objective_sha256: str, actor_id: str,
                       expected_registry_sha256: str,
                       at_utc: str | None = None) -> dict[str, Any]:
        with self._lock:
            _opaque(mission_id, "mission")
            _opaque(owner_id, "owner")
            _digest(objective_sha256, "objective")
            _require(product in PRODUCTS, "unsupported product")
            self._begin(actor_id, "create", mission_id,
                        _digest(expected_registry_sha256, "expected registry"),
                        request={"product": product, "objective_sha256": objective_sha256,
                                 "owner_sha256": sha256(owner_id.encode("utf-8")).hexdigest()})
            return self._registry.register(mission_id, product=product,
                                           owner_id=owner_id,
                                           objective_sha256=objective_sha256,
                                           actor_id=actor_id, at_utc=at_utc)

    def get_mission(self, mission_id: str, *, actor_id: str) -> dict[str, Any]:
        with self._lock:
            _opaque(mission_id, "mission")
            self._begin(actor_id, "read", mission_id)
            return self._registry.get(mission_id)

    def list_missions(self, *, actor_id: str, product: str | None = None,
                      status: str | None = None, limit: int = 128) -> tuple[dict[str, Any], ...]:
        with self._lock:
            _require(type(limit) is int and 1 <= limit <= 128, "invalid list limit")
            if product is not None:
                _require(type(product) is str and product in PRODUCTS, "invalid product filter")
            if status is not None:
                _require(type(status) is str and status in STATUSES, "invalid status filter")
            anchor = self._begin(actor_id, "list", "*",
                                 request={"product": product, "status": status, "limit": limit})
            # Do not reveal missions for which the caller lacks host read access.
            result: list[dict[str, Any]] = []
            for row in self._registry.select(product=product, status=status):
                try:
                    self._authorize(actor_id, "read", row["mission_id"], anchor,
                                    {"scope": "mission_summary"})
                except NexusMissionServiceError as exc:
                    if str(exc) == "host authorization denied":
                        continue
                    raise
                result.append(row)
                if len(result) >= limit:
                    break
            return tuple(result)

    def mission_history(self, mission_id: str, *, actor_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            _opaque(mission_id, "mission")
            self._begin(actor_id, "history", mission_id)
            return self._registry.history(mission_id)

    def export_snapshot(self, *, actor_id: str) -> dict[str, Any]:
        with self._lock:
            self._begin(actor_id, "export_snapshot", "*",
                        request={"scope": "all_missions"})
            # Caller must be host-authorized for ALL missions, not just one.
            return self._registry.snapshot()

    def assign_component(self, mission_id: str, *, component: str,
                         actor_id: str, expected_registry_sha256: str,
                         at_utc: str | None = None) -> dict[str, Any]:
        with self._lock:
            _opaque(mission_id, "mission")
            self._begin(actor_id, "assign_component", mission_id,
                        _digest(expected_registry_sha256, "expected registry"),
                        request={"component": component})
            return self._registry.assign_component(mission_id, component=component,
                                                    actor_id=actor_id, at_utc=at_utc)

    def link_reference(self, mission_id: str, *, kind: str,
                       reference_sha256: str, actor_id: str,
                       expected_registry_sha256: str,
                       at_utc: str | None = None) -> dict[str, Any]:
        with self._lock:
            _opaque(mission_id, "mission")
            _digest(reference_sha256, "reference")
            self._begin(actor_id, "link_reference", mission_id,
                        _digest(expected_registry_sha256, "expected registry"),
                        request={"kind": kind, "reference_sha256": reference_sha256})
            return self._registry.link_reference(mission_id, kind=kind,
                                                  reference_sha256=reference_sha256,
                                                  actor_id=actor_id, at_utc=at_utc)

    def configure_workflow(self, mission_id: str, *,
                           actor_id: str, coordinator: NexusMissionCoordinator,
                           adapters: NexusMissionReadAdapters,
                           expected_registry_sha256: str) -> str:
        """Bind already-constructed, host-owned policy/blueprint/read adapters."""
        with self._lock:
            _opaque(mission_id, "mission")
            _require(type(coordinator) is NexusMissionCoordinator,
                     "expected trusted coordinator")
            _require(isinstance(adapters, NexusMissionReadAdapters),
                     "expected trusted read adapters")
            _require(coordinator.blueprint.mission_id == mission_id,
                     "workflow mission mismatch")
            self._begin(actor_id, "configure", mission_id,
                        _digest(expected_registry_sha256, "expected registry"),
                        request={"blueprint_sha256": coordinator.blueprint.sha256,
                                 "policy_sha256": coordinator.mission_state.policy.sha256,
                                 "adapter_catalog_sha256": adapters.catalog.sha256})
            _require(coordinator.blueprint.product == self._registry.get(mission_id)["product"],
                     "workflow product mismatch")
            try:
                runtime = NexusMissionRuntime(self._registry, coordinator, adapters)
            except NexusMissionRuntimeError as exc:
                raise NexusMissionServiceError("workflow adapter binding rejected") from exc
            # Only replace a binding after the full type and source checks pass.
            self._runtimes[mission_id] = runtime
            return coordinator.blueprint.sha256

    def preview_workflow(self, mission_id: str, *, actor_id: str) -> MissionRuntimePreview:
        with self._lock:
            _opaque(mission_id, "mission")
            self._begin(actor_id, "preview", mission_id,
                        request={"scope": "workflow_preview"})
            _require(mission_id in self._runtimes, "mission workflow not configured")
            return self._runtimes[mission_id].preview()

    def inspect_workflow(self, mission_id: str, *, actor_id: str) -> MissionRuntimeReport:
        with self._lock:
            _opaque(mission_id, "mission")
            self._begin(actor_id, "inspect", mission_id,
                        request={"scope": "read_only_workflow"})
            _require(mission_id in self._runtimes, "mission workflow not configured")
            return self._runtimes[mission_id].inspect()

    def transition_mission(self, mission_id: str, target_status: str, *,
                           actor_id: str, expected_registry_sha256: str,
                           state: NexusMissionState | None = None,
                           decision_sha256: str | None = None,
                           at_utc: str | None = None) -> dict[str, Any]:
        """Record an assessed, host-authorized transition; NEVER run a workflow.

        A state policy with matching mission/product/target MUST be supplied
        or already bound. 'running'/'verified' also need its host checker.
        External decision authentication and durable persistence remain host
        responsibilities. A transition does not imply effect permission.
        """
        with self._lock:
            _opaque(mission_id, "mission")
            _require(type(target_status) is str and target_status in STATUSES,
                     "invalid transition target")
            anchor = self._begin(actor_id, "transition", mission_id,
                                 _digest(expected_registry_sha256, "expected registry"),
                                 request={"target_status": target_status,
                                          "decision_sha256": decision_sha256})
            if state is None:
                rt = self._runtimes.get(mission_id)
                _require(rt is not None, "host state policy required")
                state = rt.coordinator.mission_state
            _require(type(state) is NexusMissionState and
                     state.policy.mission_id == mission_id and
                     state.policy.product == self._registry.get(mission_id)["product"],
                     "state policy does not match mission")
            if target_status == "verified":
                _digest(decision_sha256, "verification decision")
            else:
                _require(decision_sha256 is None, "decision only valid for verification")
            try:
                assessment = state.assess(self._registry, target_status).to_dict()
            except NexusMissionStateError as exc:
                raise NexusMissionServiceError("state assessment unavailable") from exc
            _require(verify_assessment(assessment) and
                     assessment["outcome"] == "ready_for_host_review",
                     "mission is not ready for requested transition")
            _require(assessment["registry_sha256"] == anchor and
                     self._registry.snapshot()["registry_sha256"] == anchor,
                     "registry changed before transition")
            return self._registry.transition(mission_id, target_status,
                                             actor_id=actor_id,
                                             decision_sha256=decision_sha256,
                                             at_utc=at_utc)

    def status(self, *, actor_id: str) -> MissionServiceStatus:
        """Minimal status of this in-process service, not a health guarantee."""
        with self._lock:
            anchor = self._begin(actor_id, "list", "*")
            body = {"schema": MISSION_SERVICE_SCHEMA,
                    "registry_sha256": anchor,
                    "mission_count": self._registry.snapshot()["mission_count"],
                    "workflow_binding_count": len(self._runtimes),
                    "read_only_runtime": True,
                    "execution_permission_granted": False,
                    "network_server_started": False}
            return MissionServiceStatus(_encode({**body, "status_sha256": _sha(body)}))


__all__ = ["MISSION_SERVICE_SCHEMA", "NexusMissionServiceError",
           "HostMissionAuthorizer", "MissionServiceStatus", "NexusMissionService"]
