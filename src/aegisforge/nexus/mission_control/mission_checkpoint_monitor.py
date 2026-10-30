"""Read-only checkpoint provenance for a local Mission Control API.

A monitor pins one loaded *primary* checkpoint to its validated on-disk
receipt and in-memory registry. It checks both before serving requests; any
change requires an explicit server restart. It neither recovers old snapshots
nor writes checkpoints, archives, approvals, or workflow bindings.

The host must separately authenticate and authorize each request through
MissionService. SHA-256 checks internal consistency, NOT external authenticity
or protection from an attacker able to replace checkpoint files.

The host must serialize registry changes with requests. Checks cannot prevent
changes made by another process *after* a successful check (TOCTOU).
"""
from __future__ import annotations

import hmac
import re
from typing import Any

from .mission_persistence import (
    LoadedMissionCheckpoint,
    MissionCheckpointReceipt,
    NexusMissionPersistence,
)
from .mission_registry import NexusMissionRegistry

MISSION_CHECKPOINT_MONITOR_SCHEMA = "aegisforge.nexus.mission_control.checkpoint_monitor.v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class NexusMissionCheckpointMonitorError(ValueError):
    """Checkpoint source is invalid, unavailable, or changed."""


class NexusMissionCheckpointMonitor:
    """Pin a session to one validated, currently available primary generation.

    API-compatible with the v1 monitor. Constructor verification is intentional:
    a stale LoadedMissionCheckpoint is rejected *before* API initialization.
    """

    def __init__(self, store: NexusMissionPersistence,
                 loaded: LoadedMissionCheckpoint) -> None:
        if (type(store) is not NexusMissionPersistence
                or type(loaded) is not LoadedMissionCheckpoint
                or type(loaded.receipt) is not MissionCheckpointReceipt
                or type(loaded.registry) is not NexusMissionRegistry):
            raise NexusMissionCheckpointMonitorError("invalid checkpoint source")
        if loaded.receipt.source != "primary":
            raise NexusMissionCheckpointMonitorError("previous checkpoint is not a live source")

        registry_digest = self._snapshot_digest(loaded.registry)
        if not hmac.compare_digest(registry_digest, loaded.receipt.registry_sha256):
            raise NexusMissionCheckpointMonitorError("registry/receipt mismatch")

        self._store = store
        self._registry = loaded.registry
        self._generation = loaded.receipt.generation
        self._envelope_sha256 = loaded.receipt.envelope_sha256
        self._registry_sha256 = loaded.receipt.registry_sha256

        # The supplied receipt may be stale, or may belong to a different
        # persistence store. Verify it against the current primary immediately.
        self.assert_current()

    @staticmethod
    def _snapshot_digest(registry: NexusMissionRegistry) -> str:
        """Normalize registry snapshot failures into the API's fail-closed error."""
        try:
            snapshot = registry.snapshot()
            if type(snapshot) is not dict:
                raise ValueError("malformed registry snapshot")
            digest = snapshot.get("registry_sha256")
            if type(digest) is not str or _DIGEST.fullmatch(digest) is None:
                raise ValueError("invalid registry digest")
            return digest
        except Exception as exc:
            raise NexusMissionCheckpointMonitorError(
                "in-memory registry no longer verifiable"
            ) from exc

    def _current_receipt(self) -> MissionCheckpointReceipt:
        """Revalidate the primary and its previous-generation lineage on disk."""
        try:
            latest = self._store.load()
            if type(latest) is not LoadedMissionCheckpoint:
                raise ValueError("invalid loaded checkpoint")
            receipt = latest.receipt
            if type(receipt) is not MissionCheckpointReceipt:
                raise ValueError("invalid receipt")
            return receipt
        except Exception as exc:
            raise NexusMissionCheckpointMonitorError(
                "checkpoint no longer verifiable"
            ) from exc

    def matches_registry(self, registry: object) -> bool:
        """Only the exact registry loaded for this session may be served."""
        return registry is self._registry

    def assert_current(self) -> None:
        """Verify the pinned memory snapshot and disk primary; never reload."""
        if not hmac.compare_digest(self._snapshot_digest(self._registry),
                                   self._registry_sha256):
            raise NexusMissionCheckpointMonitorError(
                "in-memory registry changed since load"
            )

        latest = self._current_receipt()
        if (latest.source != "primary"
                or latest.generation != self._generation
                or not hmac.compare_digest(latest.envelope_sha256,
                                           self._envelope_sha256)
                or not hmac.compare_digest(latest.registry_sha256,
                                           self._registry_sha256)):
            raise NexusMissionCheckpointMonitorError(
                "checkpoint changed since server startup"
            )

        # Detect in-memory mutation occurring during the disk read. This is
        # not a substitute for host-level synchronization of writes and reads.
        if not hmac.compare_digest(self._snapshot_digest(self._registry),
                                   self._registry_sha256):
            raise NexusMissionCheckpointMonitorError(
                "in-memory registry changed during verification"
            )

    def status(self) -> dict[str, Any]:
        self.assert_current()
        return {
            "schema": MISSION_CHECKPOINT_MONITOR_SCHEMA,
            "available": True,
            "source": "persisted_primary",
            "generation": self._generation,
            "envelope_sha256": self._envelope_sha256,
            "registry_sha256": self._registry_sha256,
            "live_reload": False,
            "workflow_bindings_restored": False,
            "integrity_only": True,
            "authenticity_verified": False,
        }

    @staticmethod
    def absent() -> dict[str, Any]:
        return {
            "schema": MISSION_CHECKPOINT_MONITOR_SCHEMA,
            "available": False,
            "reason": "checkpoint_not_configured",
            "source": "in_memory",
            "live_reload": False,
            "workflow_bindings_restored": False,
            "integrity_only": True,
            "authenticity_verified": False,
        }


__all__ = [
    "MISSION_CHECKPOINT_MONITOR_SCHEMA",
    "NexusMissionCheckpointMonitor",
    "NexusMissionCheckpointMonitorError",
]
