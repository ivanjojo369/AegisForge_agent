"""Explicit, host-authorized append-only snapshots of Mission Source Archive v1.

Keep older source summaries before a newer Mission Registry checkpoint is created.
This only preserves historical metadata, never permissions or live evidence. The
host must own the private directory and serialize writers/close operations.
No automatic cleanup, rollback, or token restoration.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
from typing import Any, Callable

from .mission_persistence import NexusMissionPersistence
from .mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from .mission_source_archive import (
    NexusMissionSourceArchive, SOURCE_ARCHIVE_SCHEMA, _NAME,
    _read_file, _json, _hash, _unique_pairs, _validate_presentation,
)

HISTORY_SCHEMA = "aegisforge.nexus.mission_control.archive_history.v1"
_FOLDER = "mission_source_history"
_LOCK = ".mission_sources.capture.lock"  # same advisory lock used by Source Capture
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_FILENAME = re.compile(r"^g([0-9]{20})_([a-f0-9]{64})\.json$")
_MAX_ARCHIVES = 256


class NexusMissionArchiveHistoryError(ValueError):
    """Unavailable, denied, changed, corrupted, or conflicting history."""


def _check(value: bool, message: str) -> None:
    if not value:
        raise NexusMissionArchiveHistoryError(message)


def _validate_payload(data: Any) -> None:
    """Validate older display-only archive without pretending its checkpoint is live."""
    _check(type(data) is dict and set(data) == {
        "schema", "checkpoint_generation", "checkpoint_envelope_sha256",
        "checkpoint_registry_sha256", "entries", "integrity_only",
        "authenticity_verified", "archive_sha256",
    }, "historical archive shape invalid")
    _check(data["schema"] == SOURCE_ARCHIVE_SCHEMA and
           type(data["checkpoint_generation"]) is int and
           1 <= data["checkpoint_generation"] < 2**63 and
           data["integrity_only"] is True and
           data["authenticity_verified"] is False, "unsupported historical archive claims")
    for key in ("checkpoint_envelope_sha256", "checkpoint_registry_sha256", "archive_sha256"):
        _check(type(data[key]) is str and _SHA.fullmatch(data[key]) is not None,
               "invalid historical digest")
    body = {k: v for k, v in data.items() if k != "archive_sha256"}
    _check(hmac.compare_digest(_hash(body), data["archive_sha256"]),
           "historical archive internal digest mismatch")
    entries = data["entries"]
    _check(type(entries) is dict and 1 <= len(entries) <= 128,
           "invalid historical archive entries")
    for mid, sources in entries.items():
        _check(type(mid) is str and _ID.fullmatch(mid) is not None and
               type(sources) is dict and sources and
               set(sources) <= {"graph", "laboratory", "trust"},
               "invalid historical mission mapping")
        for kind, presentation in sources.items():
            _validate_presentation(kind, mid, presentation)


def _parse_raw(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        _validate_payload(data)
        return data
    except NexusMissionArchiveHistoryError:
        raise
    except Exception as exc:
        raise NexusMissionArchiveHistoryError("historical archive could not be verified") from exc


def _historical_name(data: dict[str, Any]) -> str:
    return f"g{data['checkpoint_generation']:020d}_{data['archive_sha256']}.json"


class NexusMissionArchiveHistory:
    """Protect one active archive and maintain separately indexed historical copies.

    authorizer(actor_id, action_name, context) must authenticate actor and
    explicitly authorize the *exact checkpoint/archive/next mission* request.
    It must return the literal boolean True. Each call is a fresh check.

    preserve_current: append checked copy, keep active archive.
    retire_current: append checked copy then unlink active archive so a new
                    checkpoint and source capture may proceed.
    list_history: read-only verification; historical Trust data never active.
    """

    def __init__(self, store: NexusMissionPersistence, *,
                 authorizer: Callable[[str, str, dict[str, Any]], bool],
                 max_archives: int = _MAX_ARCHIVES) -> None:
        _check(type(store) is NexusMissionPersistence, "checkpoint store required")
        _check(callable(authorizer), "independent history authorizer required")
        _check(type(max_archives) is int and 1 <= max_archives <= _MAX_ARCHIVES,
               "invalid history capacity")
        self._store = store
        self._root = store._root
        self._directory = self._root / _FOLDER
        self._authorize = authorizer
        self._max_archives = max_archives
        self._lock = RLock()

    def _directory_checked(self, *, create: bool = False) -> Path:
        if not os.path.lexists(self._directory):
            if create:
                try:
                    self._directory.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise NexusMissionArchiveHistoryError("history directory not creatable") from exc
            else:
                return self._directory
        try:
            st = os.lstat(self._directory)
            _check(stat.S_ISDIR(st.st_mode) and not (
                getattr(st, "st_file_attributes", 0) &
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)),
                "history directory is linked or unsafe")
        except OSError as exc:
            raise NexusMissionArchiveHistoryError("history directory unavailable") from exc
        return self._directory

    def _entry_files(self) -> list[Path]:
        folder = self._directory_checked()
        if not os.path.lexists(folder):
            return []
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            raise NexusMissionArchiveHistoryError("unable to list history") from exc
        _check(len(entries) <= self._max_archives, "history capacity exceeded")
        _check(all(_FILENAME.fullmatch(x.name) is not None for x in entries),
               "unexpected file in historical archive directory")
        return entries

    @staticmethod
    def _validated_file(path: Path) -> dict[str, Any]:
        try:
            raw = _read_file(path)
            data = _parse_raw(raw)
        except Exception as exc:
            raise NexusMissionArchiveHistoryError("historical archive unavailable or invalid") from exc
        _check(path.name == _historical_name(data), "historical filename/hash mismatch")
        return data

    def list_history(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = []
            generations: set[int] = set()
            for path in self._entry_files():
                data = self._validated_file(path)
                generation = data["checkpoint_generation"]
                _check(generation not in generations, "conflicting historical checkpoint generation")
                generations.add(generation)
                rows.append({
                    "generation": generation,
                    "archive_sha256": data["archive_sha256"],
                    "checkpoint_envelope_sha256": data["checkpoint_envelope_sha256"],
                    "checkpoint_registry_sha256": data["checkpoint_registry_sha256"],
                    "mission_ids": sorted(data["entries"]),
                    "historical_only": True,
                    "authorization_restored": False,
                    "authenticity_verified": False,
                })
            return tuple(sorted(rows, key=lambda row: row["generation"]))

    def read_summary(self, generation: int, mission_id: str, kind: str) -> dict[str, Any] | None:
        _check(type(generation) is int and generation >= 1, "invalid history generation")
        _check(type(mission_id) is str and _ID.fullmatch(mission_id) is not None,
               "invalid history mission")
        _check(kind in {"graph", "laboratory", "trust"}, "invalid history kind")
        with self._lock:
            matches = [path for path in self._entry_files()
                       if int(_FILENAME.fullmatch(path.name).group(1)) == generation]
            _check(len(matches) == 1, "historical generation missing or ambiguous")
            data = self._validated_file(matches[0])
            item = data["entries"].get(mission_id, {}).get(kind)
            return deepcopy(item) if item is not None else None

    def _current(self) -> tuple[dict[str, Any], bytes]:
        try:
            monitor = NexusMissionCheckpointMonitor(self._store, self._store.load())
            archive = NexusMissionSourceArchive(self._root / _NAME, monitor)
            archive.assert_current()
            raw = _read_file(self._root / _NAME)
            data = _parse_raw(raw)
            checkpoint = monitor.status()
            _check(data["checkpoint_generation"] == checkpoint["generation"] and
                   data["checkpoint_envelope_sha256"] == checkpoint["envelope_sha256"],
                   "archive and checkpoint differ")
            return data, raw
        except Exception as exc:
            raise NexusMissionArchiveHistoryError("active archive not safely verifiable") from exc

    def _operate(self, actor_id: str, action: str, *,
                 next_mission_id: str | None, next_status: str | None,
                 retire: bool) -> dict[str, Any]:
        _check(type(actor_id) is str and _ID.fullmatch(actor_id) is not None,
               "invalid history actor")
        if retire:
            _check(type(next_mission_id) is str and _ID.fullmatch(next_mission_id) is not None
                   and next_status in {"reported_complete", "verified", "failed", "cancelled"},
                   "retirement must target an explicit subsequent closure")
        else:
            _check(next_mission_id is None and next_status is None,
                   "preserve may not specify another closure")
        with self._lock, self._store._lock:
            initial, raw = self._current()
            ctx = {
                "generation": initial["checkpoint_generation"],
                "archive_sha256": initial["archive_sha256"],
                "archive_file_sha256": sha256(raw).hexdigest(),
                "checkpoint_envelope_sha256": initial["checkpoint_envelope_sha256"],
                "checkpoint_registry_sha256": initial["checkpoint_registry_sha256"],
                "mission_ids": sorted(initial["entries"]),
                "next_mission_id": next_mission_id,
                "next_status": next_status,
                "retire_active": retire,
            }
            try:
                approved = self._authorize(actor_id, action, deepcopy(ctx))
            except Exception as exc:
                raise NexusMissionArchiveHistoryError("history authorization unavailable") from exc
            _check(approved is True, "history operation denied by host")
            # Revalidate after callback and enforce serialize-with-capture lockfile.
            lock_fd: int | None = None
            lockfile = self._root / _LOCK
            try:
                lock_fd = os.open(lockfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                  getattr(os, "O_BINARY", 0), 0o600)
            except OSError as exc:
                raise NexusMissionArchiveHistoryError(
                    "archive lock unavailable; operator inspection required") from exc
            try:
                latest, raw2 = self._current()
                _check(latest == initial and raw2 == raw,
                       "archive changed during authorization")
                folder = self._directory_checked(create=True)
                existing = self._entry_files()
                generations = [self._validated_file(x)["checkpoint_generation"] for x in existing]
                _check(len(set(generations)) == len(generations), "conflicting history generation")
                name = _historical_name(initial)
                target = folder / name
                _check(all(g != initial["checkpoint_generation"] or x.name == name
                           for g, x in zip(generations, existing)),
                       "different archive already stored for this generation")
                if os.path.lexists(target):
                    _check(_read_file(target) == raw, "historical archive has changed")
                else:
                    _check(len(existing) < self._max_archives,
                           "archive history capacity reached")
                    try:
                        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                                     getattr(os, "O_BINARY", 0), 0o600)
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                    except OSError as exc:
                        # Fail closed; incomplete file must be quarantined manually.
                        raise NexusMissionArchiveHistoryError("history write incomplete; inspect file") from exc
                _check(_read_file(target) == raw and
                       self._validated_file(target) == initial,
                       "historical copy failed read-back verification")
                if retire:
                    again, current_raw = self._current()
                    _check(again == initial and current_raw == raw,
                           "active archive drifted before retirement")
                    try:
                        (self._root / _NAME).unlink()
                    except OSError as exc:
                        raise NexusMissionArchiveHistoryError("active archive not retired") from exc
                body = {
                    "schema": HISTORY_SCHEMA,
                    "generation": initial["checkpoint_generation"],
                    "archive_sha256": initial["archive_sha256"],
                    "checkpoint_envelope_sha256": initial["checkpoint_envelope_sha256"],
                    "retired_active": retire,
                    "preserved_historical": True,
                    "authorization_restored": False,
                    "authenticity_verified": False,
                    "effects_executed": False,
                }
                return {**body, "receipt_sha256": _hash(body)}
            finally:
                if lock_fd is not None:
                    os.close(lock_fd)
                    try:
                        lockfile.unlink()
                    except OSError:
                        # Lock left for explicit operator inspection.
                        pass

    def preserve_current(self, *, actor_id: str) -> dict[str, Any]:
        return self._operate(actor_id, "mission_history.preserve_current",
                             next_mission_id=None, next_status=None, retire=False)

    def retire_current(self, *, actor_id: str, next_mission_id: str,
                       next_status: str) -> dict[str, Any]:
        return self._operate(actor_id, "mission_history.retire_current",
                             next_mission_id=next_mission_id, next_status=next_status,
                             retire=True)


__all__ = ["HISTORY_SCHEMA", "NexusMissionArchiveHistory",
           "NexusMissionArchiveHistoryError"]
