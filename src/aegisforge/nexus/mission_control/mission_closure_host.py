"""Explicit host entrypoint for supervised mission closure and archive retention.

This facade coordinates existing, independently authorized contracts. It is not
an API route, authentication system, distributed transaction or effects runner.
It is deliberately not auto-registered in Mission Service: the trusted host
must opt in, serialize other registry writers, and approve each disk operation.
A checkpoint or history failure NEVER silently undoes a mission transition.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from threading import RLock
from typing import Any

from .mission_archive_history import NexusMissionArchiveHistory
from .mission_closure_hook import NexusMissionClosureHook
from .mission_state import NexusMissionState

HOST_CLOSURE_SCHEMA = "aegisforge.nexus.mission_control.closure_host.v1"
_FINAL = frozenset({"reported_complete", "verified", "failed", "cancelled"})


class NexusMissionClosureHostError(ValueError):
    """Invalid host bindings, stale registry, or unauthorized orchestration."""


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class NexusMissionClosureHost:
    """One host-owned coordinator for a closure and its immutable archive history.

    Service's authorizer, Source Capture authorizer and History authorizer are
    independently injected into their existing components. This wrapper never
    creates approvals or restores old Trust decisions. Use with a host that
    serializes all registry/archival writes across threads and processes.

    close() optionally retires a *verified* old source archive BEFORE the next
    transition. If the subsequent closure fails, the old copy stays in history,
    and the returned report explicitly requires reconciliation. A new archive
    is captured by ClosureHook after checkpointing, then preserved in history.
    """

    def __init__(self, hook: NexusMissionClosureHook,
                 history: NexusMissionArchiveHistory) -> None:
        if type(hook) is not NexusMissionClosureHook or type(history) is not NexusMissionArchiveHistory:
            raise NexusMissionClosureHostError("expected closure hook and history")
        if history._store is not hook._store:
            raise NexusMissionClosureHostError("history and hook must use same checkpoint store")
        self._hook = hook
        self._history = history
        self._lock = RLock()

    @staticmethod
    def _report(mission_id: str, *, closure: dict[str, Any] | None,
                retired_prior: bool, preserved_current: bool,
                history_receipt: dict[str, Any] | None = None,
                stage: str = "finished") -> dict[str, Any]:
        closure_outcome = closure.get("outcome") if closure else None
        success = closure_outcome == "completed" and preserved_current
        body = {
            "schema": HOST_CLOSURE_SCHEMA,
            "mission_id": mission_id,
            "stage": stage,
            "outcome": "completed" if success else "operator_reconciliation_required",
            "closure_outcome": closure_outcome,
            "closure_receipt_sha256": closure.get("receipt_sha256") if closure else None,
            "prior_archive_retired": retired_prior,
            "current_archive_preserved": preserved_current,
            "history_receipt_sha256": (history_receipt or {}).get("receipt_sha256"),
            "operator_reconciliation_required": not success,
            "trust_authorizations_restored": False,
            "effects_executed": False,
            "authenticity_verified": False,
        }
        return {**body, "report_sha256": _hash(body)}

    def close(self, mission_id: str, target_status: str, *, actor_id: str,
              state: NexusMissionState,
              expected_registry_sha256: str,
              decision_sha256: str | None = None,
              rotate_prior_archive: bool = False) -> dict[str, Any]:
        """Explicit multi-step close. No automatic approvals or rollback.

        rotate_prior_archive requires separate host authorization linked to
        this *exact next mission and target*. Previously archived source must
        still match the currently loaded checkpoint when rotation starts.
        """
        if type(expected_registry_sha256) is not str or len(expected_registry_sha256) != 64:
            raise NexusMissionClosureHostError("expected registry anchor required")
        if type(rotate_prior_archive) is not bool:
            raise NexusMissionClosureHostError("invalid rotate flag")
        if type(target_status) is not str or target_status not in _FINAL:
            raise NexusMissionClosureHostError("invalid closure target")
        with self._lock, self._hook._lock, self._hook._service._lock:
            current = self._hook._service.registry_version(actor_id=actor_id)
            if current != expected_registry_sha256:
                raise NexusMissionClosureHostError("registry changed since host reviewed closure")
            retired = False
            active = self._history._root / "mission_sources.json"
            if os.path.lexists(active):
                if not rotate_prior_archive:
                    raise NexusMissionClosureHostError(
                        "current archive requires explicit authorized retirement")
                self._history.retire_current(actor_id=actor_id,
                                             next_mission_id=mission_id,
                                             next_status=target_status)
                retired = True
            try:
                plan = self._hook.prepare(mission_id, target_status,
                                          actor_id=actor_id, state=state,
                                          decision_sha256=decision_sha256)
                closure = self._hook.commit(plan, actor_id=actor_id, state=state)
            except Exception:
                if not retired:
                    raise
                # Old archive has been preserved and retired, but no new
                # transition has been confirmed. Must not silently retry.
                return self._report(mission_id, closure=None, retired_prior=True,
                                    preserved_current=False, stage="preparation_or_commit_failed")
            if closure["outcome"] != "completed":
                return self._report(mission_id, closure=closure, retired_prior=retired,
                                    preserved_current=False,
                                    stage="closure_partial")
            try:
                saved = self._history.preserve_current(actor_id=actor_id)
            except Exception:
                return self._report(mission_id, closure=closure, retired_prior=retired,
                                    preserved_current=False,
                                    stage="history_preservation_failed")
            return self._report(mission_id, closure=closure, retired_prior=retired,
                                preserved_current=True, history_receipt=saved)

    def resume_archive(self, mission_id: str, *, actor_id: str) -> dict[str, Any]:
        """Explicit resume of archive stage, then separate historical retention."""
        with self._lock, self._hook._lock, self._hook._service._lock:
            receipt = self._hook.resume_archive(mission_id, actor_id=actor_id)
            # Resumption receipt format belongs to ClosureHook, not close().
            try:
                saved = self._history.preserve_current(actor_id=actor_id)
            except Exception:
                return self._report(mission_id, closure=None, retired_prior=False,
                                    preserved_current=False,
                                    stage="resumed_archive_history_failed")
            body = {
                "schema": HOST_CLOSURE_SCHEMA,
                "mission_id": mission_id,
                "outcome": "archive_resumed_and_preserved",
                "resumption_receipt_sha256": receipt["resumption_receipt_sha256"],
                "history_receipt_sha256": saved["receipt_sha256"],
                "operator_reconciliation_required": False,
                "trust_authorizations_restored": False,
                "effects_executed": False,
                "authenticity_verified": False,
            }
            return {**body, "report_sha256": _hash(body)}

    def preserve_current_after_failure(self, *, actor_id: str) -> dict[str, Any]:
        """Reauthorize history only, never repeat a completed closure."""
        with self._lock, self._hook._service._lock:
            return deepcopy(self._history.preserve_current(actor_id=actor_id))


__all__ = ["HOST_CLOSURE_SCHEMA", "NexusMissionClosureHostError",
           "NexusMissionClosureHost"]
