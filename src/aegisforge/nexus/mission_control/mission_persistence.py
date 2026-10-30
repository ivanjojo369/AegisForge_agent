"""Crash-conscious local snapshot persistence for Nexus Mission Registry.

Destination: src/aegisforge/nexus/mission_control/mission_persistence.py

This is a metadata snapshot store, NOT a database, authorization layer, durable
multi-object transaction, journal of tool effects, or source-authentication tool.
The host must choose a private existing directory, control OS permissions,
authenticate callers, serialize service changes with checkpointing and verify
external decisions/evidence. Hashes detect accidental changes and inconsistent
snapshots, not malicious replacement of all files or rollback by an attacker.

On updates, the old primary is moved to `.prev`, then a fully synced temporary
file replaces the primary. A crash between those renames leaves no primary and
one valid previous generation. Recovery is always explicit; this module never
silently loads old data as current. Lockfiles left by crashes require inspection
by an operator (never automatically reclaimed).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from threading import RLock
from typing import Any, Mapping

from .mission_registry import (
    NexusMissionRegistry, NexusMissionRegistryError, MissionRegistryLimits,
)
from .mission_service import NexusMissionService

MISSION_PERSISTENCE_SCHEMA = "aegisforge.nexus.mission_control.persistence.v1"
_ZERO = "0" * 64
_SHA = re.compile(r"^[a-f0-9]{64}$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,95}\.json$")
_FIELDS = frozenset({
    "schema", "generation", "previous_sha256", "created_at_utc",
    "registry_sha256", "registry", "integrity_only", "authenticity_verified",
    "envelope_sha256",
})


class NexusMissionPersistenceError(ValueError):
    """Invalid or stale checkpoint, unsafe target, corruption or file operation."""


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise NexusMissionPersistenceError(message)


def _encode(data: Any) -> bytes:
    try:
        return json.dumps(data, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionPersistenceError("invalid checkpoint JSON") from exc


def _sha(data: Any) -> str:
    return sha256(_encode(data)).hexdigest()


def _digest(value: Any, field: str) -> str:
    _require(type(value) is str and _SHA.fullmatch(value) is not None,
             f"invalid {field} digest")
    return value


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_link_or_reparse(path: Path) -> bool:
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _safe_file(path: Path) -> None:
    """Reject links, non-regular files and hardlinks (best-effort OS checks)."""
    info = os.lstat(path)
    _require(not _is_link_or_reparse(path), "linked/reparse checkpoint path forbidden")
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
             "checkpoint must be one regular, unlinked file")


def _extends(previous: dict[str, Any], current: dict[str, Any]) -> None:
    """Forbid loss or rewrite of committed event history across generations."""
    older = {r["mission_id"]: r for r in previous["missions"]}
    newer = {r["mission_id"]: r for r in current["missions"]}
    _require(older.keys() <= newer.keys(), "checkpoint may not remove missions")
    for mission_id, old in older.items():
        new = newer[mission_id]
        _require(new["events"][:len(old["events"])] == old["events"],
                 "checkpoint may not rewrite or truncate mission history")
        if len(new["events"]) == len(old["events"]):
            _require(new == old, "checkpoint changed metadata without new event")


@dataclass(frozen=True, slots=True)
class MissionCheckpointReceipt:
    generation: int
    envelope_sha256: str
    registry_sha256: str
    source: str = "primary"

    def __post_init__(self) -> None:
        _require(type(self.generation) is int and self.generation >= 1,
                 "invalid receipt generation")
        _digest(self.envelope_sha256, "envelope")
        _digest(self.registry_sha256, "registry")
        _require(self.source in {"primary", "previous"}, "invalid checkpoint source")


@dataclass(frozen=True, slots=True)
class LoadedMissionCheckpoint:
    registry: NexusMissionRegistry
    receipt: MissionCheckpointReceipt


class NexusMissionPersistence:
    """Explicit checkpoint store with optimistic concurrency and last-good backup.

    A store instance is safe for multiple threads. Separate processes coordinate
    through an exclusive lockfile for writes. No automatic stale-lock deletion.
    This class does not persist MissionService's in-memory workflow bindings;
    those must be reconstructed and re-authorized after a restart.
    """

    def __init__(self, root: str | os.PathLike[str], *,
                 filename: str = "mission_registry.json",
                 max_checkpoint_bytes: int = 6_000_000,
                 registry_limits: MissionRegistryLimits | None = None) -> None:
        _require(type(filename) is str and _NAME.fullmatch(filename) is not None and
                 filename not in {".", ".."}, "invalid checkpoint filename")
        _require(type(max_checkpoint_bytes) is int and
                 4096 <= max_checkpoint_bytes <= 16_000_000,
                 "invalid checkpoint byte budget")
        _require(registry_limits is None or type(registry_limits) is MissionRegistryLimits,
                 "invalid registry limits")
        try:
            supplied = Path(root).absolute()
            # Don't resolve a symlink and then trust the target as operator-approved.
            _require(supplied.is_dir() and not _is_link_or_reparse(supplied),
                     "checkpoint root must be an existing real directory")
            for ancestor in (supplied, *supplied.parents):
                if ancestor.exists() and _is_link_or_reparse(ancestor):
                    raise NexusMissionPersistenceError("checkpoint root contains a link")
            self._root = supplied.resolve(strict=True)
        except (OSError, RuntimeError, TypeError) as exc:
            raise NexusMissionPersistenceError("invalid checkpoint root") from exc
        self._primary = self._root / filename
        self._previous = self._root / (filename + ".prev")
        self._lockfile = self._root / (filename + ".lock")
        self.max_checkpoint_bytes = max_checkpoint_bytes
        self.registry_limits = registry_limits
        self._lock = RLock()

    @property
    def filename(self) -> str:
        return self._primary.name

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            _safe_file(path)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            try:
                info = os.fstat(fd)
                _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                         info.st_size <= self.max_checkpoint_bytes,
                         "checkpoint file type/size rejected")
                with os.fdopen(fd, "rb", closefd=False) as handle:
                    raw = handle.read(self.max_checkpoint_bytes + 1)
                _require(len(raw) <= self.max_checkpoint_bytes,
                         "checkpoint exceeds byte budget")
                end = os.fstat(fd)
                _require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) ==
                         (end.st_dev, end.st_ino, end.st_size, end.st_mtime_ns),
                         "checkpoint modified during read")
            finally:
                os.close(fd)
            data = json.loads(raw.decode("utf-8"))
        except NexusMissionPersistenceError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise NexusMissionPersistenceError("checkpoint unreadable or malformed") from exc
        _require(type(data) is dict and set(data) == _FIELDS,
                 "invalid checkpoint fields")
        _require(data["schema"] == MISSION_PERSISTENCE_SCHEMA,
                 "checkpoint schema mismatch")
        _require(type(data["generation"]) is int and 1 <= data["generation"] < 2**63,
                 "invalid checkpoint generation")
        _require(data["integrity_only"] is True and
                 data["authenticity_verified"] is False,
                 "checkpoint makes an unsupported trust claim")
        _digest(data["previous_sha256"], "parent")
        _digest(data["envelope_sha256"], "envelope")
        _digest(data["registry_sha256"], "registry")
        _require(type(data["created_at_utc"]) is str and
                 len(data["created_at_utc"]) <= 32 and
                 data["created_at_utc"].endswith("Z"),
                 "invalid creation timestamp")
        try:
            dt = datetime.fromisoformat(data["created_at_utc"][:-1] + "+00:00")
            _require(dt.utcoffset().total_seconds() == 0, "creation timestamp not UTC")
        except (TypeError, ValueError, OverflowError) as exc:
            raise NexusMissionPersistenceError("invalid creation timestamp") from exc
        body = {key: value for key, value in data.items() if key != "envelope_sha256"}
        _require(hmac.compare_digest(_sha(body), data["envelope_sha256"]),
                 "checkpoint envelope SHA mismatch")
        _require(type(data["registry"]) is dict and
                 data["registry"].get("registry_sha256") == data["registry_sha256"],
                 "checkpoint registry digest mismatch")
        try:
            NexusMissionRegistry.from_snapshot(data["registry"], limits=self.registry_limits)
        except (NexusMissionRegistryError, ValueError, TypeError, KeyError) as exc:
            raise NexusMissionPersistenceError("checkpoint registry is invalid") from exc
        _require((data["generation"] == 1 and data["previous_sha256"] == _ZERO) or
                 (data["generation"] > 1 and data["previous_sha256"] != _ZERO),
                 "invalid checkpoint lineage")
        return data

    @staticmethod
    def _receipt(data: Mapping[str, Any], source: str = "primary") -> MissionCheckpointReceipt:
        return MissionCheckpointReceipt(data["generation"], data["envelope_sha256"],
                                        data["registry_sha256"], source)

    def _load(self, path: Path, source: str) -> LoadedMissionCheckpoint:
        data = self._read(path)
        registry = NexusMissionRegistry.from_snapshot(data["registry"],
                                                     limits=self.registry_limits)
        return LoadedMissionCheckpoint(registry, self._receipt(data, source))

    def load(self) -> LoadedMissionCheckpoint:
        """Read ONLY primary. Missing/corrupt primary never silently falls back."""
        with self._lock:
            if not self._primary.exists():
                _require(not self._previous.exists(),
                         "primary missing; explicit previous recovery required")
                raise NexusMissionPersistenceError("no saved checkpoint")
            current = self._read(self._primary)
            if self._previous.exists():
                previous = self._read(self._previous)
                self._adjacent(previous, current)
            return LoadedMissionCheckpoint(
                NexusMissionRegistry.from_snapshot(current["registry"],
                                                   limits=self.registry_limits),
                self._receipt(current))

    def inspect_previous(self) -> LoadedMissionCheckpoint:
        """For incident diagnostics. NEVER changes the primary or trusts rollback."""
        with self._lock:
            return self._load(self._previous, "previous")

    @staticmethod
    def _adjacent(previous: dict[str, Any], current: dict[str, Any]) -> None:
        _require(current["generation"] == previous["generation"] + 1 and
                 hmac.compare_digest(current["previous_sha256"],
                                     previous["envelope_sha256"]),
                 "checkpoint backup lineage mismatch")
        _extends(previous["registry"], current["registry"])

    def _write_temp(self, data: bytes) -> Path:
        fd, path = tempfile.mkstemp(prefix="." + self.filename + ".", suffix=".tmp",
                                   dir=self._root)
        temp = Path(path)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            temp.unlink(missing_ok=True)
            raise
        return temp

    def _sync_dir(self) -> None:
        if os.name == "nt":
            return  # Windows directory fsync isn't portable; file fsync + replace used.
        fd = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _replace(self, src: Path, dst: Path) -> None:
        os.replace(src, dst)

    def _acquire_disk_lock(self) -> int:
        try:
            fd = os.open(self._lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                         getattr(os, "O_BINARY", 0), 0o600)
            os.write(fd, b"aegisforge mission persistence exclusive write\n")
            os.fsync(fd)
            return fd
        except FileExistsError as exc:
            raise NexusMissionPersistenceError(
                "checkpoint lock exists; inspect owner before manual removal") from exc
        except OSError as exc:
            if 'fd' in locals():
                try: os.close(fd)
                except OSError: pass
                try: self._lockfile.unlink(missing_ok=True)
                except OSError: pass
            raise NexusMissionPersistenceError("unable to acquire checkpoint lock") from exc

    def _release_disk_lock(self, fd: int) -> None:
        os.close(fd)
        self._lockfile.unlink(missing_ok=True)

    def save_snapshot(self, snapshot: dict[str, Any], *,
                      expected_head_sha256: str | None = None) -> MissionCheckpointReceipt:
        """Persist an already host-authorized snapshot; never performs auth itself.

        Existing checkpoints REQUIRE expected_head_sha256 (compare-and-swap).
        Committed histories must only grow, never rewrite or delete prior events.
        """
        _require(type(snapshot) is dict, "expected registry snapshot")
        try:
            candidate = NexusMissionRegistry.from_snapshot(snapshot,
                                                           limits=self.registry_limits)
            checked = candidate.snapshot()
        except (NexusMissionRegistryError, ValueError, TypeError, KeyError) as exc:
            raise NexusMissionPersistenceError("invalid snapshot to persist") from exc
        with self._lock:
            fd = self._acquire_disk_lock()
            try:
                _require(not self._primary.is_symlink() and
                         not self._previous.is_symlink(), "linked checkpoint path forbidden")
                if self._primary.exists():
                    existing = self._read(self._primary)
                    if self._previous.exists():
                        self._adjacent(self._read(self._previous), existing)
                    _require(expected_head_sha256 is not None and
                             hmac.compare_digest(_digest(expected_head_sha256, "expected head"),
                                                 existing["envelope_sha256"]),
                             "stale or missing expected checkpoint head")
                    _extends(existing["registry"], checked)
                    if checked == existing["registry"]:
                        return self._receipt(existing)
                    _require(existing["generation"] < 2**63 - 1,
                             "checkpoint generation limit reached")
                    generation = existing["generation"] + 1
                    previous_sha = existing["envelope_sha256"]
                else:
                    _require(not self._previous.exists(),
                             "interrupted commit; explicit recovery required")
                    _require(expected_head_sha256 is None,
                             "initial save must not provide expected head")
                    generation, previous_sha = 1, _ZERO
                body = {
                    "schema": MISSION_PERSISTENCE_SCHEMA,
                    "generation": generation,
                    "previous_sha256": previous_sha,
                    "created_at_utc": _utc(),
                    "registry_sha256": checked["registry_sha256"],
                    "registry": checked,
                    "integrity_only": True,
                    "authenticity_verified": False,
                }
                data = {**body, "envelope_sha256": _sha(body)}
                encoded = _encode(data) + b"\n"
                _require(len(encoded) <= self.max_checkpoint_bytes,
                         "checkpoint exceeds configured byte budget")
                temp = self._write_temp(encoded)
                try:
                    # A completed checkpoint keeps the previous generation.
                    if self._primary.exists():
                        self._replace(self._primary, self._previous)
                    self._replace(temp, self._primary)
                    self._sync_dir()
                finally:
                    temp.unlink(missing_ok=True)
                return self._receipt(data)
            except OSError as exc:
                raise NexusMissionPersistenceError("checkpoint atomic write failed") from exc
            finally:
                self._release_disk_lock(fd)

    def save(self, registry: NexusMissionRegistry, *,
             expected_head_sha256: str | None = None) -> MissionCheckpointReceipt:
        _require(type(registry) is NexusMissionRegistry, "expected trusted mission registry")
        return self.save_snapshot(registry.snapshot(),
                                  expected_head_sha256=expected_head_sha256)

    def checkpoint_service(self, service: NexusMissionService, *, actor_id: str,
                           expected_head_sha256: str | None = None) -> MissionCheckpointReceipt:
        """Request a host-authorized export. Service+disk are NOT one transaction.

        Serialize mutations and checkpoints in the host process. Restored service
        should use a NEW host-verified authorizer, not one loaded from disk.
        """
        _require(type(service) is NexusMissionService,
                 "expected host-owned mission service")
        try:
            snapshot = service.export_snapshot(actor_id=actor_id)
        except Exception as exc:
            raise NexusMissionPersistenceError("service export was denied or unavailable") from exc
        return self.save_snapshot(snapshot, expected_head_sha256=expected_head_sha256)

    def restore_interrupted_commit(self, *,
                                   expected_previous_sha256: str) -> MissionCheckpointReceipt:
        """Explicit restore ONLY when primary is absent after interrupted rename.

        If primary is corrupt but exists, operator must quarantine it manually
        before calling this method. This function never deletes a primary.
        """
        with self._lock:
            fd = self._acquire_disk_lock()
            try:
                _require(not self._primary.exists() and not self._primary.is_symlink(),
                         "refusing to overwrite an existing primary")
                previous = self._read(self._previous)
                _require(hmac.compare_digest(
                    _digest(expected_previous_sha256, "expected previous"),
                    previous["envelope_sha256"]), "previous checkpoint head mismatch")
                try:
                    self._replace(self._previous, self._primary)
                    self._sync_dir()
                except OSError as exc:
                    raise NexusMissionPersistenceError("checkpoint recovery failed") from exc
                return self._receipt(previous)
            finally:
                self._release_disk_lock(fd)


__all__ = [
    "MISSION_PERSISTENCE_SCHEMA", "NexusMissionPersistenceError",
    "MissionCheckpointReceipt", "LoadedMissionCheckpoint",
    "NexusMissionPersistence",
]
