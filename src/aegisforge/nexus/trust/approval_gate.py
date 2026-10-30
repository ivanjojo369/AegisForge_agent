"""Host-owned, one-use approval gate for AegisForge Nexus / Colossus.

Target: src/aegisforge/nexus/trust/approval_gate.py

The gate authorizes NOTHING by itself. A trusted host creates a request, an
independently authenticated reviewer decides, and the existing WorkspacePolicy
must consume the approval immediately before an effect. All operations fail
closed; a denied, expired, rejected or consumed request cannot be reused.

CRITICAL INTEGRATION CONTRACT
-----------------------------
* ``reviewer`` and ``binding_provider`` must come from trusted host code, not an
  LLM, chat message, or untrusted plugin. A reviewer callback must authenticate
  the real operator OUTSIDE this process and explicitly confirm the exact
  request after displaying the actual proposed effect (for example the entire
  patch or test plan). Do NOT auto-approve because hashes match.
* ``binding_provider`` must independently calculate the digests of the ACTUAL
  pending operation and its plan at the execution boundary (not echo the
  request's stored hashes). If the host cannot do this, DENY the operation.
* Use ``gate.verify_workspace_action`` as ``NexusWorkspacePolicy``'s
  ``approval_verifier``. WorkspacePolicy separately enforces mission rules,
  real filesystem scope and single-use action IDs. No mutation is performed by
  this module. The host must still recheck target state and sandbox execution.
* This is an IN-MEMORY authority. A restarted process loses ALL approvals.
  For cross-process authorization, use a separately secured, atomic approval
  service. Hashes are bindings, not signatures or proof of user consent.
* All Python objects are accessible to code running in this process: never
  run untrusted Python in the approving process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import secrets
from threading import Lock
from time import monotonic
from typing import Any, Callable, Mapping

from ..workspace.workspace_policy import (
    IMPACTFUL_OPERATIONS,
    WorkspaceAction,
    _relative_scope,
    _valid_mission,
)

APPROVAL_SCHEMA = "aegisforge.nexus.trust.approval.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVIEWER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_EVIDENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_ID = re.compile(r"^[0-9a-f]{32}$")
_STATES = frozenset({"pending", "reviewing", "approved", "verifying", "consumed", "denied", "expired"})


class NexusApprovalError(ValueError):
    """Invalid request, identity, limit, or lifecycle transition."""


def canonical_sha256(value: Any) -> str:
    """SHA-256 of small canonical JSON *values* (never include secrets).

    For large/binary bodies, the trusted host should hash exact bytes itself.
    This is a digest, not an authorization credential or a digital signature.
    """
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusApprovalError("payload must be canonical JSON compatible") from exc
    if len(raw) > 262_144:
        raise NexusApprovalError("binding payload exceeds 256 KiB")
    return sha256(raw).hexdigest()


def _digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise NexusApprovalError("binding must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ActionBinding:
    """Digests of exact effect parameters and immutable plan/runtime context.

    Example: payload_sha256 hashes the normalized command/test parameters;
    context_sha256 binds the test plan ID and checkout revision, or the patch
    identity and expected source hash. Both must be independently recomputed.
    """

    payload_sha256: str
    context_sha256: str

    def __post_init__(self) -> None:
        _digest(self.payload_sha256)
        _digest(self.context_sha256)

    def to_dict(self) -> dict[str, str]:
        return {"payload_sha256": self.payload_sha256,
                "context_sha256": self.context_sha256}


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """A pending review subject; identifiers alone confer no privilege."""

    request_id: str
    root: str
    mission_id: str
    operation: str
    relative_path: str
    binding: ActionBinding
    created_at: float
    expires_at: float
    request_sha256: str

    def to_dict(self) -> dict[str, Any]:
        # No raw commands, text, tool outputs, authorization tokens or prompts.
        return {
            "schema": APPROVAL_SCHEMA,
            "request_id": self.request_id,
            "root": self.root,
            "mission_id": self.mission_id,
            "operation": self.operation,
            "relative_path": self.relative_path,
            "binding": self.binding.to_dict(),
            "request_sha256": self.request_sha256,
            "status": "review_required",
        }


@dataclass(frozen=True, slots=True)
class ApprovalAttestation:
    """Result returned by a HOST-OWNED reviewer, not by the model/operator text.

    The reviewer must explicitly inspect the displayed request fingerprint and
    authenticate the person granting permission. ``evidence_ref`` references
    the external review record and must not be a URL with a bearer token.
    """

    request_sha256: str
    reviewer_id: str
    evidence_ref: str

    def __post_init__(self) -> None:
        _digest(self.request_sha256)
        if not isinstance(self.reviewer_id, str) or not _REVIEWER.fullmatch(self.reviewer_id):
            raise NexusApprovalError("invalid authenticated reviewer ID")
        if not isinstance(self.evidence_ref, str) or not _EVIDENCE.fullmatch(self.evidence_ref):
            raise NexusApprovalError("invalid nonsecret review evidence reference")


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    request_id: str
    status: str
    code: str
    request_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"schema": APPROVAL_SCHEMA, "request_id": self.request_id,
                "status": self.status, "code": self.code,
                "request_sha256": self.request_sha256}


@dataclass(slots=True)
class _Record:
    request: ApprovalRequest
    status: str = "pending"
    attestation: ApprovalAttestation | None = None


TrustedReviewer = Callable[[ApprovalRequest], ApprovalAttestation | None]
LiveBindingProvider = Callable[[WorkspaceAction], ActionBinding]


class NexusApprovalGate:
    """In-memory, fail-closed authorization state machine.

    ``request()`` only creates a REVIEW REQUEST. ``review()`` invokes the
    trusted callback and cannot self-approve. ``verify_workspace_action()``
    validates the action, independently recalculates its binding, then consumes
    the request atomically; successful verification is NOT execution.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        reviewer: TrustedReviewer,
        binding_provider: LiveBindingProvider,
        clock: Callable[[], float] = monotonic,
        max_requests: int = 1024,
    ) -> None:
        root = Path(workspace_root).expanduser()
        if not root.is_absolute() or not root.is_dir() or root.is_symlink():
            raise NexusApprovalError("workspace_root must be an existing absolute directory")
        if not callable(reviewer) or not callable(binding_provider) or not callable(clock):
            raise NexusApprovalError("trusted reviewer, binding provider and clock required")
        if type(max_requests) is not int or not 1 <= max_requests <= 100_000:
            raise NexusApprovalError("max_requests outside supported range")
        self.root = str(root.resolve(strict=True))
        self._reviewer = reviewer
        self._binding_provider = binding_provider
        self._clock = clock
        self._max_requests = max_requests
        self._records: dict[str, _Record] = {}
        self._lock = Lock()

    def _now(self) -> float:
        value = self._clock()
        if (isinstance(value, bool) or not isinstance(value, (float, int))
                or not math.isfinite(value)):
            raise NexusApprovalError("invalid monotonic clock")
        return float(value)

    def _expire(self, record: _Record, now: float) -> None:
        if record.status not in {"consumed", "denied", "expired"} and now >= record.request.expires_at:
            record.status = "expired"

    def request(
        self,
        mission_id: str,
        operation: str,
        relative_path: str,
        *,
        binding: ActionBinding,
        ttl_seconds: int = 600,
    ) -> ApprovalRequest:
        """Register an inert review request; never grants access or performs I/O."""
        if (not _valid_mission(mission_id) or not isinstance(operation, str)
                or operation not in IMPACTFUL_OPERATIONS):
            raise NexusApprovalError("invalid mission or operation")
        try:
            path = _relative_scope(relative_path)
        except (ValueError, TypeError) as exc:
            raise NexusApprovalError("unsafe relative action path") from exc
        if path == "." and operation not in {"run_tests", "execute_tool"}:
            raise NexusApprovalError("operation requires a concrete target path")
        if not isinstance(binding, ActionBinding):
            raise NexusApprovalError("an exact action binding is required")
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 3600:
            raise NexusApprovalError("ttl_seconds must be 30..3600")
        now = self._now()
        request_id = secrets.token_hex(16)
        identity = {
            "schema": APPROVAL_SCHEMA,
            "request_id": request_id, "root": self.root,
            "mission_id": mission_id, "operation": operation,
            "relative_path": path, "binding": binding.to_dict(),
        }
        req = ApprovalRequest(
            request_id=request_id, root=self.root, mission_id=mission_id,
            operation=operation, relative_path=path, binding=binding,
            created_at=now, expires_at=now + ttl_seconds,
            request_sha256=canonical_sha256(identity),
        )
        with self._lock:
            if len(self._records) >= self._max_requests:
                raise NexusApprovalError("approval request capacity reached; fail closed")
            self._records[request_id] = _Record(req)
        return req

    def status(self, request_id: str) -> ApprovalDecision:
        """Return status metadata; never returns attestation credentials."""
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            return ApprovalDecision("", "denied", "invalid_request_id")
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return ApprovalDecision(request_id, "denied", "unknown_request")
            self._expire(record, self._now())
            return ApprovalDecision(request_id, record.status, "status_read", record.request.request_sha256)

    def deny(self, request_id: str) -> ApprovalDecision:
        """Host may cancel pending/reviewing/approved requests; irreversible."""
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            return ApprovalDecision("", "denied", "invalid_request_id")
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return ApprovalDecision(request_id, "denied", "unknown_request")
            self._expire(record, self._now())
            if record.status in {"pending", "reviewing", "approved"}:
                record.status = "denied"
                record.attestation = None
                return ApprovalDecision(request_id, "denied", "explicit_denial", record.request.request_sha256)
            return ApprovalDecision(request_id, record.status, "cannot_cancel_terminal_or_verifying", record.request.request_sha256)

    def review(self, request_id: str) -> ApprovalDecision:
        """Invoke ONLY a trusted, separately authenticated reviewer callback."""
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            return ApprovalDecision("", "denied", "invalid_request_id")
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return ApprovalDecision(request_id, "denied", "unknown_request")
            self._expire(record, self._now())
            if record.status != "pending":
                return ApprovalDecision(request_id, record.status, "not_pending", record.request.request_sha256)
            record.status = "reviewing"
            request = record.request
        try:
            attestation = self._reviewer(request)
        except Exception:
            attestation = None
        authorized = (
            isinstance(attestation, ApprovalAttestation)
            and attestation.request_sha256 == request.request_sha256
        )
        with self._lock:
            self._expire(record, self._now())
            if record.status != "reviewing":
                return ApprovalDecision(request_id, record.status, "review_invalidated", request.request_sha256)
            if authorized:
                record.attestation = attestation
                record.status = "approved"
                return ApprovalDecision(request_id, "approved", "reviewer_confirmed", request.request_sha256)
            record.status = "denied"
            return ApprovalDecision(request_id, "denied", "reviewer_declined_or_invalid", request.request_sha256)

    def verify_workspace_action(self, action: WorkspaceAction) -> bool:
        """One-shot verifier compatible with NexusWorkspacePolicy.

        The live binding provider MUST reconstruct the actual operation, not
        use request data. The approval is consumed only on exact equality.
        A failed binding check permanently invalidates this request.
        """
        if not isinstance(action, WorkspaceAction):
            return False
        request_id = action.action_id
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            return False
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return False
            self._expire(record, self._now())
            if record.status != "approved" or record.attestation is None:
                return False
            req = record.request
            if (action.root != req.root or action.mission_id != req.mission_id
                or action.operation != req.operation or action.relative_path != req.relative_path):
                return False
            record.status = "verifying"
        try:
            current_binding = self._binding_provider(action)
        except Exception:
            current_binding = None
        with self._lock:
            self._expire(record, self._now())
            if record.status != "verifying":
                return False
            if not isinstance(current_binding, ActionBinding) or current_binding != req.binding:
                record.status = "denied"
                record.attestation = None
                return False
            record.status = "consumed"
            return True


__all__ = [
    "APPROVAL_SCHEMA", "NexusApprovalError", "canonical_sha256", "ActionBinding",
    "ApprovalRequest", "ApprovalAttestation", "ApprovalDecision",
    "TrustedReviewer", "LiveBindingProvider", "NexusApprovalGate",
]
