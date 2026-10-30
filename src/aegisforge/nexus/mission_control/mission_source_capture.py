"""Host-authorized capture of verified display summaries at a mission checkpoint.

The host completes/records mission transitions and saves the registry checkpoint
BEFORE calling prepare(). This module *never* transitions a mission, authorizes
another action, consumes approvals, executes a tool, or restores live sources.

Two-phase contract:
  prepare(actor_id, mission_ids) -> bounded, SHA-256-bound, in-memory plan
  commit(plan, actor_id)       -> one-time explicit host-authorized archive write

A checkpoint and archive are two separate files, NOT a distributed transaction.
The archive is atomically published via a private temp file and os.replace,
under a cooperative exclusive lockfile. The host must own and protect the root;
these hashes do not establish origin authenticity or defend against a malicious
local writer with access to the directory. A leftover lock after a process crash
requires operator inspection; it is never automatically reclaimed.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import hmac
import os
from pathlib import Path
import re
import stat
import tempfile
from threading import RLock
from typing import Any, Callable, Mapping, Sequence

from .mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from .mission_monitor_views import NexusMissionMonitorViews
from .mission_persistence import NexusMissionPersistence
from .mission_source_archive import (
    NexusMissionSourceArchive, NexusMissionSourceArchiveError,
    SOURCE_ARCHIVE_SCHEMA, _MAX_BYTES, _NAME, _frozen, _hash, _json,
    _pinned_path, _validate_presentation,
)

MISSION_SOURCE_CAPTURE_SCHEMA = "aegisforge.nexus.mission_control.source_capture.v1"
_CAPTURE_ACTION = "mission_sources.capture_closed_missions"
_FINAL_STATES = frozenset({"reported_complete", "verified", "failed", "cancelled"})
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_LOCK_NAME = ".mission_sources.capture.lock"
_MAX_PLANS = 64


class NexusMissionSourceCaptureError(ValueError):
    """Denied, incomplete, changed, unsafe, or non-atomic source capture."""


def _require(ok: bool, why: str) -> None:
    if not ok:
        raise NexusMissionSourceCaptureError(why)


def _actor_hash(actor: str) -> str:
    _require(type(actor) is str and 3 <= len(actor) <= 96 and
             _ID.fullmatch(actor) is not None, "invalid capture actor")
    return sha256(actor.encode("utf-8")).hexdigest()


def _safe_absent(path: Path) -> bool:
    return not os.path.lexists(os.fspath(path))


def _make_archive_payload(monitor: NexusMissionCheckpointMonitor,
                          views: NexusMissionMonitorViews,
                          mission_ids: tuple[str, ...]) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Use the precise Source Archive v1 schema, without any raw source data."""
    status = monitor.status()
    rows = {m["mission_id"]: m for m in monitor._registry.snapshot()["missions"]}
    entries: dict[str, dict[str, Any]] = {}
    counts = {"graph": 0, "laboratory": 0, "trust": 0}
    for mid in mission_ids:
        _require(mid in rows, "capture mission is absent from checkpoint")
        _require(rows[mid]["status"] in _FINAL_STATES,
                 "mission must be closed before capture")
        collected = {}
        for kind, reader in (("graph", views.graph), ("laboratory", views.laboratory),
                             ("trust", views.trust)):
            try:
                current = reader(mid)  # live independent structural/source validation
                _require(type(current) is dict and type(current.get("available")) is bool,
                         "invalid live monitor source")
                if current["available"]:
                    frozen = _frozen(kind, current)
                    _validate_presentation(kind, mid, frozen)
                    collected[kind] = frozen
                    counts[kind] += 1
            except NexusMissionSourceCaptureError:
                raise
            except Exception as exc:
                raise NexusMissionSourceCaptureError(
                    "live monitor source unavailable or inconsistent") from exc
        _require(bool(collected), "closed mission has no verified display sources")
        entries[mid] = collected
    body = {
        "schema": SOURCE_ARCHIVE_SCHEMA,
        "checkpoint_generation": status["generation"],
        "checkpoint_envelope_sha256": status["envelope_sha256"],
        "checkpoint_registry_sha256": status["registry_sha256"],
        "entries": entries,
        "integrity_only": True,
        "authenticity_verified": False,
    }
    payload = {**body, "archive_sha256": _hash(body)}
    raw = _json(payload) + b"\n"
    _require(len(raw) <= _MAX_BYTES, "source archive exceeds byte limit")
    monitor.assert_current()
    return payload, raw, counts


class NexusMissionSourceCapture:
    """Checkpoint-bound capture. No implicit or inherited authorization.

    authorizer(actor_id, action_name, sorted_mission_ids, checkpoint_metadata)
      must return the literal bool True. It runs once at commit, not at prepare.
    It MUST authenticate the actor and inspect the exact mission IDs/checkpoint;
    this class does not authenticate the caller itself.
    """

    def __init__(self, store: NexusMissionPersistence,
                 views: NexusMissionMonitorViews, *,
                 authorizer: Callable[[str, str, tuple[str, ...], dict[str, Any]], bool] | None = None,
                 max_plans: int = _MAX_PLANS) -> None:
        _require(type(store) is NexusMissionPersistence, "checkpoint store required")
        _require(type(views) is NexusMissionMonitorViews, "live monitor views required")
        _require(authorizer is None or callable(authorizer), "invalid host authorizer")
        _require(type(max_plans) is int and 1 <= max_plans <= 128, "invalid plan limit")
        self._store = store
        self._views = views
        self._authorizer = authorizer
        self._max_plans = max_plans
        self._plans: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def _monitor(self) -> NexusMissionCheckpointMonitor:
        try:
            monitor = NexusMissionCheckpointMonitor(self._store, self._store.load())
            monitor.assert_current()
            return monitor
        except Exception as exc:
            raise NexusMissionSourceCaptureError("current checkpoint unavailable") from exc

    def _preview(self, ids: tuple[str, ...], actor_id: str) -> tuple[dict[str, Any], bytes, dict[str, Any], NexusMissionCheckpointMonitor]:
        monitor = self._monitor()
        payload, raw, counts = _make_archive_payload(monitor, self._views, ids)
        return payload, raw, counts, monitor

    def prepare(self, mission_ids: Sequence[str], *, actor_id: str) -> dict[str, Any]:
        """Plan and verify sources without requesting approval or writing files."""
        actor_sha = _actor_hash(actor_id)
        _require(type(mission_ids) in (tuple, list) and 1 <= len(mission_ids) <= 128,
                 "invalid capture mission selection")
        _require(all(type(mid) is str and _ID.fullmatch(mid) is not None
                     for mid in mission_ids), "invalid mission identifier")
        ids = tuple(sorted(mission_ids))
        _require(len(set(ids)) == len(ids), "duplicate capture mission")
        with self._lock:
            _require(len(self._plans) < self._max_plans, "capture plan budget exhausted")
            _require(_safe_absent(self._store._root / _NAME),
                     "source archive already exists: new checkpoint required")
            payload, raw, counts, _ = self._preview(ids, actor_id)
            body = {
                "schema": MISSION_SOURCE_CAPTURE_SCHEMA,
                "actor_sha256": actor_sha,
                "mission_ids": list(ids),
                "mission_count": len(ids),
                "checkpoint_generation": payload["checkpoint_generation"],
                "checkpoint_envelope_sha256": payload["checkpoint_envelope_sha256"],
                "checkpoint_registry_sha256": payload["checkpoint_registry_sha256"],
                "archive_sha256": payload["archive_sha256"],
                "archive_file_sha256": sha256(raw).hexdigest(),
                "source_counts": counts,
                "host_authorization_required": True,
                "authorization_granted": False,
                "trust_permissions_restored": False,
                "effects_executed": False,
            }
            plan = {**body, "plan_sha256": _hash(body)}
            _require(plan["plan_sha256"] not in self._plans,
                     "capture already prepared for this checkpoint and actor")
            self._plans[plan["plan_sha256"]] = deepcopy(plan)
            return deepcopy(plan)

    def commit(self, plan: Mapping[str, Any], *, actor_id: str) -> dict[str, Any]:
        """One-time host-authorized publish. Deny on any change or write failure."""
        actor_sha = _actor_hash(actor_id)
        with self._lock:
            _require(type(plan) is dict and type(plan.get("plan_sha256")) is str,
                     "invalid capture plan")
            plan_id = plan["plan_sha256"]
            _require(plan_id in self._plans and plan == self._plans[plan_id],
                     "capture plan missing, altered or already used")
            # Consume on *attempt*, even when host authorization is denied.
            saved = self._plans.pop(plan_id)
            _require(saved["actor_sha256"] == actor_sha,
                     "different capture operator")
            ids = tuple(saved["mission_ids"])
            path = self._store._root / _NAME
            _require(_safe_absent(path), "source archive already exists")
            payload, raw, counts, monitor = self._preview(ids, actor_id)
            _require(payload["archive_sha256"] == saved["archive_sha256"] and
                     sha256(raw).hexdigest() == saved["archive_file_sha256"] and
                     payload["checkpoint_generation"] == saved["checkpoint_generation"] and
                     payload["checkpoint_envelope_sha256"] == saved["checkpoint_envelope_sha256"] and
                     payload["checkpoint_registry_sha256"] == saved["checkpoint_registry_sha256"] and
                     counts == saved["source_counts"],
                     "mission, checkpoint or sources changed since preparation")
            _require(self._authorizer is not None, "host authorizer not configured")
            auth_context = {
                "generation": saved["checkpoint_generation"],
                "envelope_sha256": saved["checkpoint_envelope_sha256"],
                "registry_sha256": saved["checkpoint_registry_sha256"],
                "archive_sha256": saved["archive_sha256"],
                "archive_file_sha256": saved["archive_file_sha256"],
                "source_counts": deepcopy(saved["source_counts"]),
            }
            try:
                granted = self._authorizer(actor_id, _CAPTURE_ACTION, ids,
                                            deepcopy(auth_context))
            except Exception as exc:
                raise NexusMissionSourceCaptureError("host capture authorization failed") from exc
            _require(granted is True, "host denied source capture")
            payload2, raw2, counts2, monitor2 = self._preview(ids, actor_id)
            _require(raw2 == raw and counts2 == counts and
                     monitor2.status()["envelope_sha256"] == saved["checkpoint_envelope_sha256"],
                     "capture changed during host authorization")
            self._publish(raw2, monitor2)
            try:
                archive = NexusMissionSourceArchive(path, monitor2)
                archive.assert_current()
            except Exception as exc:
                raise NexusMissionSourceCaptureError(
                    "published archive failed verification; inspect files explicitly") from exc
            report_body = {
                "schema": MISSION_SOURCE_CAPTURE_SCHEMA,
                "plan_sha256": saved["plan_sha256"],
                "actor_sha256": actor_sha,
                "mission_ids": list(ids),
                "checkpoint_generation": saved["checkpoint_generation"],
                "checkpoint_envelope_sha256": saved["checkpoint_envelope_sha256"],
                "checkpoint_registry_sha256": saved["checkpoint_registry_sha256"],
                "archive_sha256": payload2["archive_sha256"],
                "archive_file_sha256": sha256(raw2).hexdigest(),
                "source_counts": counts2,
                "host_authorization_checked": True,
                "trust_permissions_restored": False,
                "authenticity_verified": False,
                "effects_executed": False,
            }
            return {**report_body, "capture_report_sha256": _hash(report_body)}

    def _publish(self, raw: bytes, monitor: NexusMissionCheckpointMonitor) -> None:
        root = self._store._root
        path = _pinned_path(root, monitor)
        lock_path = root / _LOCK_NAME
        lock_fd: int | None = None
        temp_path: Path | None = None
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                              getattr(os, "O_BINARY", 0), 0o600)
            _require(_safe_absent(path), "archive already exists")
            fd, tmp = tempfile.mkstemp(prefix=".mission_sources.", suffix=".tmp", dir=root)
            temp_path = Path(tmp)
            with os.fdopen(fd, "wb") as stream:
                try:
                    os.fchmod(stream.fileno(), stat.S_IRUSR | stat.S_IWUSR)
                except (AttributeError, OSError):
                    pass  # ACL control is the host's responsibility on Windows
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            monitor.assert_current()
            _require(_safe_absent(path), "archive appeared during capture")
            os.replace(temp_path, path)
            temp_path = None
            # Best-effort directory sync where supported (Windows differs).
            if os.name == "posix":
                try:
                    dfd = os.open(root, os.O_RDONLY)
                    try:
                        os.fsync(dfd)
                    finally:
                        os.close(dfd)
                except OSError:
                    pass
        except NexusMissionSourceCaptureError:
            raise
        except OSError as exc:
            raise NexusMissionSourceCaptureError("atomic archive publish failed") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            if lock_fd is not None:
                os.close(lock_fd)
                lock_path.unlink(missing_ok=True)

    @staticmethod
    def verify_capture_report(report: Mapping[str, Any],
                              monitor: NexusMissionCheckpointMonitor,
                              archive: NexusMissionSourceArchive) -> bool:
        try:
            if (type(report) is not dict or type(monitor) is not NexusMissionCheckpointMonitor
                    or type(archive) is not NexusMissionSourceArchive):
                return False
            fields = {"schema", "plan_sha256", "actor_sha256", "mission_ids",
                      "checkpoint_generation", "checkpoint_envelope_sha256",
                      "checkpoint_registry_sha256", "archive_sha256",
                      "archive_file_sha256", "source_counts", "host_authorization_checked",
                      "trust_permissions_restored", "authenticity_verified",
                      "effects_executed", "capture_report_sha256"}
            if set(report) != fields or report["schema"] != MISSION_SOURCE_CAPTURE_SCHEMA:
                return False
            if (report["host_authorization_checked"] is not True or
                    report["trust_permissions_restored"] is not False or
                    report["authenticity_verified"] is not False or
                    report["effects_executed"] is not False or
                    type(report["mission_ids"]) is not list or
                    not report["mission_ids"] or
                    not all(type(x) is str for x in report["mission_ids"])
                    or type(report["source_counts"]) is not dict or
                    set(report["source_counts"]) != {"graph", "laboratory", "trust"} or
                    not all(type(x) is int and 0 <= x <= 128
                            for x in report["source_counts"].values())):
                return False
            for k in ("plan_sha256", "actor_sha256", "archive_sha256",
                      "archive_file_sha256", "checkpoint_envelope_sha256",
                      "checkpoint_registry_sha256", "capture_report_sha256"):
                if type(report[k]) is not str or _SHA.fullmatch(report[k]) is None:
                    return False
            if type(report["checkpoint_generation"]) is not int:
                return False
            monitor.assert_current()
            archive.assert_current()
            status = monitor.status()
            if (status["generation"] != report["checkpoint_generation"] or
                    status["envelope_sha256"] != report["checkpoint_envelope_sha256"] or
                    status["registry_sha256"] != report["checkpoint_registry_sha256"]):
                return False
            if archive._payload["archive_sha256"] != report["archive_sha256"]:
                return False
            if not hmac.compare_digest(archive._file_sha256, report["archive_file_sha256"]):
                return False
            entries = archive._payload["entries"]
            if report["mission_ids"] != sorted(entries):
                return False
            recorded = {k: sum(k in sources for sources in entries.values())
                        for k in ("graph", "laboratory", "trust")}
            if recorded != report["source_counts"]:
                return False
            body = {k: v for k, v in report.items() if k != "capture_report_sha256"}
            return hmac.compare_digest(report["capture_report_sha256"], _hash(body))
        except (KeyError, TypeError, ValueError, OSError, RecursionError):
            return False


__all__ = ["MISSION_SOURCE_CAPTURE_SCHEMA", "NexusMissionSourceCaptureError",
           "NexusMissionSourceCapture"]
