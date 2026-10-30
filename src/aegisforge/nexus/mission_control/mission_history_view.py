"""Pinned, read-only view over generations of historical Mission Source Archives.

A historical summary is inert presentation metadata; it is NOT a current grant
of Trust approval or fresh evaluation. The hosting API must authorize every
mission against the *current* Mission Service before revealing historical data.
This adapter never writes to the history directory or runs the host authorizer.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import hmac
from typing import Any

from .mission_archive_history import NexusMissionArchiveHistory, NexusMissionArchiveHistoryError
from .mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from .mission_persistence import NexusMissionPersistence
from .mission_source_archive import _read_file

HISTORY_VIEW_SCHEMA = "aegisforge.nexus.mission_control.history_view.v1"
_KINDS = frozenset({"graph", "laboratory", "trust"})


class NexusMissionHistoryViewError(ValueError):
    """Source missing, changed, invalid or incompatible with startup checkpoint."""


class NexusMissionHistoryView:
    """Read-only adapter pinned to the startup checkpoint AND history byte set."""

    def __init__(self, store: NexusMissionPersistence,
                 monitor: NexusMissionCheckpointMonitor) -> None:
        if type(store) is not NexusMissionPersistence or type(monitor) is not NexusMissionCheckpointMonitor:
            raise NexusMissionHistoryViewError("trusted checkpoint and monitor required")
        if monitor._store is not store:
            raise NexusMissionHistoryViewError("checkpoint/store mismatch")
        monitor.assert_current()
        self._monitor = monitor
        # No HTTP route can invoke mutation or obtain a working history authorizer.
        self._history = NexusMissionArchiveHistory(
            store, authorizer=lambda _actor, _action, _context: False)
        self._pinned = self._fingerprint()
        self._generation = monitor.status()["generation"]
        # Historical generations cannot be newer than the pinned registry.
        try:
            if any(row["generation"] > self._generation
                   for row in self._history.list_history()):
                raise NexusMissionHistoryViewError("future historical generation")
        except NexusMissionArchiveHistoryError as exc:
            raise NexusMissionHistoryViewError("invalid historical generations") from exc

    def _fingerprint(self) -> tuple[tuple[str, str], ...]:
        try:
            # Validates every historical record, uniqueness and internal digest.
            records = self._history.list_history()
            filenames = self._history._entry_files()
            fingerprints = tuple((p.name, sha256(_read_file(p)).hexdigest())
                                 for p in filenames)
            if len(records) != len(fingerprints):
                raise NexusMissionHistoryViewError("history directory changed during read")
            return fingerprints
        except NexusMissionHistoryViewError:
            raise
        except Exception as exc:
            raise NexusMissionHistoryViewError("history unavailable or corrupt") from exc

    def assert_current(self) -> None:
        self._monitor.assert_current()
        other = self._fingerprint()
        if not hmac.compare_digest(repr(self._pinned), repr(other)):
            raise NexusMissionHistoryViewError("historical records changed since startup")

    def generations(self, mission_id: str) -> dict[str, Any]:
        self.assert_current()
        if type(mission_id) is not str:
            raise NexusMissionHistoryViewError("invalid mission")
        rows: list[dict[str, Any]] = []
        series: list[dict[str, Any]] = []
        for row in self._history.list_history():
            if mission_id not in row["mission_ids"]:
                continue
            generation = row["generation"]
            # No global mission identifiers or counts are exposed.
            available = []
            for kind in ("graph", "laboratory", "trust"):
                value = self._history.read_summary(generation, mission_id, kind)
                if value is None:
                    continue
                available.append(kind)
                if kind == "laboratory":
                    aggregate = value.get("aggregate", {})
                    series.append({"generation": generation,
                                   "evaluations": aggregate.get("evaluations", 0),
                                   "passed": aggregate.get("passed", 0),
                                   "failed": aggregate.get("failed", 0),
                                   "inconclusive": aggregate.get("inconclusive", 0)})
            rows.append({"generation": generation,
                         "checkpoint_envelope_sha256": row["checkpoint_envelope_sha256"],
                         "archive_sha256": row["archive_sha256"],
                         "kinds": available,
                         "historical_only": True,
                         "authorization_restored": False})
        self.assert_current()
        return {"schema": HISTORY_VIEW_SCHEMA, "mission_id": mission_id,
                "available": True, "historical_only": True,
                "authenticity_verified": False,
                "authorization_restored": False,
                "integrity_only": True,
                "count": len(rows), "generations": rows, "lab_trend": series,
                "comparison_limit": "historical aggregates may use different evaluation inputs"}

    def summary(self, generation: int, mission_id: str, kind: str) -> dict[str, Any] | None:
        # A preserved copy of the active generation is valid. Future
        # generations are not part of the loaded checkpoint session.
        if type(generation) is not int or generation <= 0 or generation > self._generation:
            raise NexusMissionHistoryViewError("invalid generation")
        if kind not in _KINDS or type(mission_id) is not str:
            raise NexusMissionHistoryViewError("invalid historical request")
        self.assert_current()
        try:
            value = self._history.read_summary(generation, mission_id, kind)
        except NexusMissionArchiveHistoryError as exc:
            raise NexusMissionHistoryViewError("historical generation missing or invalid") from exc
        self.assert_current()
        if value is None:
            return None
        out = deepcopy(value)
        # Historically stored Trust states are never authorization decisions.
        out["historical_only"] = True
        out["archived_metadata_only"] = True
        out["authenticity_verified"] = False
        out["authorization_granted"] = False
        out["effects_executed"] = False
        out["checkpoint_generation"] = generation
        out["verified_against_sources"] = False
        return out


__all__ = ["HISTORY_VIEW_SCHEMA", "NexusMissionHistoryView", "NexusMissionHistoryViewError"]
