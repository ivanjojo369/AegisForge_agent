"""Trust status inspection for Colossus Mission Control (read-only handoff).

This adapter consults NexusApprovalGate.status() for ONE host-selected request.
It never calls request/review/deny/verify_workspace_action, does not consume
an approval, and does not grant execution. Gate.status may mark a TTL-expired
request expired, a fail-closed security housekeeping transition.

The report is *not* an approval token: even status='approved' may not be used
at an effect boundary. A trusted host must independently verify exact action,
identity, live binding, scope and single-use approval at that boundary.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from .mission_bridge import MissionBridgeCatalog, MissionBridgePort
from .mission_read_adapters import READ_ADAPTER_SCHEMA, NexusMissionReadAdapterError
from .mission_workspace_engineering_adapters import (
    WORKSPACE_ENGINEERING_READ_CATALOG,
    NexusMissionWorkspaceEngineeringReadAdapters,
)
from .mission_registry import NexusMissionRegistry
from ..trust.approval_gate import NexusApprovalGate, NexusApprovalError, ApprovalRequest

TRUST_INSPECT_SCHEMA = 'aegisforge.nexus.mission_control.trust_inspect.v1'


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise NexusMissionReadAdapterError(message)


def _sha(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionReadAdapterError('invalid Trust metadata') from exc
    return sha256(encoded).hexdigest()


_TRUST_FIELDS = ('gate_status', 'gate_code', 'request_sha256',
                 'approved_for_execution_by_adapter', 'gate_status_only')
_TRUST_PORT = MissionBridgePort(
    'trust', 'review_action', 'local.trust_status_inspection.v1',
    _sha({'schema': READ_ADAPTER_SCHEMA, 'component': 'trust',
          'operation': 'review_action', 'adapter_id': 'local.trust_status_inspection.v1',
          'read_only': True, 'summary_fields': list(_TRUST_FIELDS)}),
)
TRUST_READ_CATALOG = MissionBridgeCatalog(tuple(sorted(
    (*WORKSPACE_ENGINEERING_READ_CATALOG.ports, _TRUST_PORT),
    key=lambda x: (x.component, x.operation))))


class NexusMissionTrustReadAdapters(NexusMissionWorkspaceEngineeringReadAdapters):
    """Inspect pre-existing approval status; never initiate Trust workflow."""

    def __init__(self, registry: NexusMissionRegistry, *,
                 approval_gate: NexusApprovalGate | None = None,
                 approval_request: ApprovalRequest | None = None,
                 **source_kwargs: Any) -> None:
        super().__init__(registry, **source_kwargs)
        _require(approval_gate is None or type(approval_gate) is NexusApprovalGate,
                 'invalid host-owned approval gate')
        _require(approval_request is None or type(approval_request) is ApprovalRequest,
                 'invalid host-supplied approval request')
        _require(approval_gate is None or approval_request is not None,
                 'host-supplied exact approval request is required')
        self._approval_gate = approval_gate
        self._approval_request = approval_request

    @property
    def catalog(self) -> MissionBridgeCatalog:
        return TRUST_READ_CATALOG

    def _trust_data(self, mission_id: str) -> tuple[str, dict[str, Any]]:
        _require(self._approval_gate is not None and self._approval_request is not None,
                 'Trust source missing')
        _require(self._approval_request.mission_id == mission_id,
                 'Trust request belongs to another mission')
        _require(self._approval_request.root == self._approval_gate.root,
                 'Trust gate root differs from request')
        try:
            decision = self._approval_gate.status(self._approval_request.request_id)
            record = decision.to_dict()
        except (NexusApprovalError, OSError, ValueError) as exc:
            raise NexusMissionReadAdapterError('Trust source unavailable') from exc
        _require(type(record) is dict and
                 record.get('status') in {'pending', 'reviewing', 'approved',
                                          'verifying', 'consumed', 'denied', 'expired'} and
                 type(record.get('code')) is str and len(record['code']) <= 64 and
                 (record.get('request_sha256') is None or
                  (type(record['request_sha256']) is str and
                   len(record['request_sha256']) == 64 and
                   all(c in '0123456789abcdef' for c in record['request_sha256']))),
                 'unexpected Trust decision')
        _require(record['request_sha256'] == self._approval_request.request_sha256,
                 'approval request binding mismatch')
        # These fields exclude the root, request ID, paths, reviewer and binding.
        summary = {'gate_status': record['status'],
                   'gate_code': record['code'],
                   'request_sha256': record['request_sha256'],
                   'approved_for_execution_by_adapter': False,
                   'gate_status_only': True}
        return _sha({'schema': TRUST_INSPECT_SCHEMA, 'summary': summary}), summary

    def _source_summary(self, component: str, operation: str, mission_id: str,
                        product: str) -> tuple[str, dict[str, Any]]:
        if (component, operation) == ('trust', 'review_action'):
            return self._trust_data(mission_id)
        return super()._source_summary(component, operation, mission_id, product)

    def _current_source_sha(self, component: str, operation: str) -> str:
        if (component, operation) == ('trust', 'review_action'):
            _require(self._approval_request is not None, 'Trust source missing')
            return self._trust_data(self._approval_request.mission_id)[0]
        return super()._current_source_sha(component, operation)


__all__ = ['TRUST_INSPECT_SCHEMA', 'TRUST_READ_CATALOG',
           'NexusMissionTrustReadAdapters']
