"""Bounded, read-only artifact integrity manifest for Nexus — Colossus.

Destination: src/aegisforge/nexus/proof/artifact_manifest.py

A trusted host explicitly selects one regular file under a fixed workspace root.
This module measures its size and SHA-256, versions the observation, and builds
privacy-minimizing, digest-linked metadata snapshots. No file names, paths, raw
content or operator identities are exported. File data is NEVER written here.

ProofGraph and Provenance bridges create NEW in-memory representations. Hashes
establish equality with captured bytes, not authenticity, trust, or correctness.
The module is not a hostile-filesystem sandbox: a privileged process changing
paths concurrently may defeat user-space symlink checks (particularly Windows).
Never use this as the only security boundary for untrusted file access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
from typing import Any, Mapping

MANIFEST_SCHEMA = "aegisforge.nexus.proof.artifact_manifest.v1"
_ZERO = "0" * 64
_SHA = re.compile(r"^[a-f0-9]{64}$")
_MISSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$")
_DRIVE = re.compile(r"^[A-Za-z]:")

ARTIFACT_KINDS = frozenset({
    "source", "patch", "test_report", "execution_report", "evidence",
    "dataset", "model_output", "configuration", "binary", "other",
})
ORIGIN_TYPES = frozenset({"host", "operator", "tool", "model", "external", "nexus_evidence"})
_FORBIDDEN_COMPONENTS = frozenset({
    ".git", ".ssh", ".aws", ".venv", "venv", "node_modules", ".env",
    "secrets", "credentials", "__pycache__", ".pytest_cache",
})
_FORBIDDEN_FILENAMES = frozenset({
    ".env", ".env.local", ".env.production", ".env.development",
    "credentials.json", "secrets.json", "token.json", "id_rsa", "id_ed25519",
    "known_hosts", "authorized_keys", ".npmrc", ".pypirc",
})
_FORBIDDEN_SUFFIXES = frozenset({".pem", ".p12", ".pfx", ".key", ".kdbx"})
_RECORD_KEYS = frozenset({
    "schema", "mission_id", "sequence", "artifact_id_sha256", "kind", "version",
    "content_sha256", "size_bytes", "relative_path_sha256", "origin_type",
    "origin_id_sha256", "captured_at_utc", "previous_version_sha256",
    "previous_sha256", "record_sha256",
})
_SNAPSHOT_KEYS = frozenset({
    "schema", "mission_id", "record_count", "records", "terminal_sha256",
    "integrity_only", "authenticity_verified", "snapshot_sha256",
})


class NexusArtifactManifestError(ValueError):
    """Invalid scope, file, digest, artifact identity or manifest."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusArtifactManifestError("invalid canonical manifest data") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _hash_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise NexusArtifactManifestError(f"{label} must be lowercase SHA-256")
    return value


def _mission(value: Any) -> str:
    if not isinstance(value, str) or _MISSION.fullmatch(value) is None:
        raise NexusArtifactManifestError("invalid mission identifier")
    return value


def _artifact_id(value: Any) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise NexusArtifactManifestError("artifact_id must be opaque, not a file path")
    return value


def _utc(value: Any) -> str:
    if not isinstance(value, str) or _UTC.fullmatch(value) is None:
        raise NexusArtifactManifestError("invalid UTC timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.utcoffset() != timedelta(0):
            raise ValueError("not UTC")
    except ValueError as exc:
        raise NexusArtifactManifestError("invalid UTC timestamp") from exc
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _path_parts(relative_path: str) -> tuple[str, ...]:
    if (not isinstance(relative_path, str) or not relative_path
            or len(relative_path) > 512 or "\x00" in relative_path
            or _DRIVE.match(relative_path) or relative_path.startswith(("/", "\\"))):
        raise NexusArtifactManifestError("expected a relative workspace file path")
    normalized = relative_path.replace("\\", "/")
    parts = normalized.split("/")
    if (any(not p or p in (".", "..") or ":" in p for p in parts)
            or any(p.lower() in _FORBIDDEN_COMPONENTS for p in parts)
            or parts[-1].lower() in _FORBIDDEN_FILENAMES
            or Path(parts[-1]).suffix.lower() in _FORBIDDEN_SUFFIXES
            or parts[-1].lower().startswith(".env.")):
        raise NexusArtifactManifestError("path is unsafe or excluded")
    return tuple(parts)


@dataclass(frozen=True, slots=True)
class ArtifactManifestLimits:
    max_records: int = 512
    max_file_bytes: int = 8_388_608
    max_versions_per_artifact: int = 32
    max_snapshot_bytes: int = 1_500_000

    def __post_init__(self) -> None:
        for field_name, ceiling in (
            ("max_records", 10_000),
            ("max_file_bytes", 64 * 1024 * 1024),
            ("max_versions_per_artifact", 1024),
            ("max_snapshot_bytes", 8_000_000),
        ):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise NexusArtifactManifestError(f"invalid {field_name}")


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    schema: str
    mission_id: str
    sequence: int
    artifact_id_sha256: str
    kind: str
    version: int
    content_sha256: str
    size_bytes: int
    relative_path_sha256: str
    origin_type: str
    origin_id_sha256: str
    captured_at_utc: str
    previous_version_sha256: str | None
    previous_sha256: str
    record_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}


def _measurement_is_stable(before: os.stat_result, opened: os.stat_result,
                           ended: os.stat_result, after: os.stat_result,
                           bytes_read: int, *, windows: bool) -> bool:
    """Compare file metadata without mixing incompatible Windows ctime meanings.

    On Windows, path-based stat may expose creation time in st_ctime_ns
    while descriptor-based fstat exposes metadata-change time. Compare
    each measurement source to itself, and compare compatible properties
    (size, mtime, file identity and, when available, birthtime) across
    sources. On POSIX, ctime retains consistent semantics across sources.

    This detects ordinary modifications/replacements, not adversarial
    filesystem races or a forged stat implementation.
    """
    observations = (before, opened, ended, after)
    if (bytes_read != after.st_size or
            any(not stat.S_ISREG(item.st_mode) for item in observations)):
        return False

    # The path and the open file descriptor must each remain stable.
    for old, new in ((before, after), (opened, ended)):
        if (old.st_size != new.st_size or
                old.st_mtime_ns != new.st_mtime_ns or
                old.st_ctime_ns != new.st_ctime_ns):
            return False
        if (old.st_dev, old.st_ino) != (new.st_dev, new.st_ino):
            return False

    # These values have compatible meanings for stat(path) and fstat(fd).
    if any((item.st_size, item.st_mtime_ns) !=
           (before.st_size, before.st_mtime_ns) for item in observations[1:]):
        return False

    # Compare file IDs when the filesystem provides them. Some Windows
    # filesystems expose st_ino=0 for one of the APIs; zero is not an ID.
    if all(item.st_ino != 0 for item in observations):
        if any((item.st_dev, item.st_ino) !=
               (before.st_dev, before.st_ino) for item in observations[1:]):
            return False
    elif all(item.st_dev != 0 for item in observations):
        if any(item.st_dev != before.st_dev for item in observations[1:]):
            return False

    if windows:
        # st_birthtime_ns is consistently the creation time on supported
        # Windows Python versions, unlike st_ctime_ns.
        births = [getattr(item, "st_birthtime_ns", None) for item in observations]
        if all(value is not None for value in births) and len(set(births)) != 1:
            return False
    else:
        if any(item.st_ctime_ns != before.st_ctime_ns for item in observations[1:]):
            return False
    return True


class NexusArtifactManifest:
    """Mission-scoped, in-memory manifest; reads only explicitly selected files.

    Path-to-artifact mappings are private and not restored from snapshots. After
    from_snapshot(), callers MUST provide an explicit path to verify a file.
    """

    def __init__(self, mission_id: str, workspace_root: str | os.PathLike[str],
                 *, limits: ArtifactManifestLimits | None = None) -> None:
        self.mission_id = _mission(mission_id)
        self.limits = limits or ArtifactManifestLimits()
        if not isinstance(self.limits, ArtifactManifestLimits):
            raise NexusArtifactManifestError("invalid limits")
        root = Path(workspace_root).expanduser()
        if root.is_symlink() or not root.is_dir():
            raise NexusArtifactManifestError("workspace root must be an existing real directory")
        self.workspace_root = root.resolve(strict=True)
        self._records: list[ArtifactRecord] = []
        self._by_artifact: dict[str, list[ArtifactRecord]] = {}
        self._local_paths: dict[str, str] = {}
        self._lock = RLock()

    def _locate(self, relative_path: str) -> tuple[Path, str]:
        parts = _path_parts(relative_path)
        relative = "/".join(parts)
        candidate = self.workspace_root
        try:
            for part in parts:
                candidate = candidate / part
                if candidate.is_symlink():
                    raise NexusArtifactManifestError("symbolic links are not permitted")
            actual = candidate.resolve(strict=True)
            if not actual.is_relative_to(self.workspace_root) or not actual.is_file():
                raise NexusArtifactManifestError("file escapes workspace or is not regular")
            return actual, relative
        except (OSError, RuntimeError) as exc:
            raise NexusArtifactManifestError("cannot resolve requested file") from exc

    def _measure(self, relative_path: str) -> tuple[str, int, str]:
        path, relative = self._locate(relative_path)
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode) or before.st_size > self.limits.max_file_bytes:
                raise NexusArtifactManifestError("unsupported file or file-size limit exceeded")
            digest = sha256()
            count = 0
            # On POSIX, O_NOFOLLOW prevents a final-component symlink race.
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if (not stat.S_ISREG(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                    raise NexusArtifactManifestError("file changed before read")
                while True:
                    chunk = handle.read(min(65536, self.limits.max_file_bytes + 1 - count))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > self.limits.max_file_bytes:
                        raise NexusArtifactManifestError("file-size limit exceeded while reading")
                    digest.update(chunk)
                ended = os.fstat(handle.fileno())
            after = path.stat()
            if not _measurement_is_stable(
                before, opened, ended, after, count, windows=os.name == "nt"
            ):
                raise NexusArtifactManifestError("file changed during measurement")
        except NexusArtifactManifestError:
            raise
        except (OSError, ValueError) as exc:
            raise NexusArtifactManifestError("cannot measure requested file") from exc
        return digest.hexdigest(), count, relative

    def register_file(self, artifact_id: str, relative_path: str, *, kind: str,
                      origin_type: str = "host", origin_id_sha256: str | None = None) -> ArtifactRecord:
        """Record actual file bytes. Adds a version only if bytes or path changed."""
        opaque = _artifact_id(artifact_id)
        if kind not in ARTIFACT_KINDS or origin_type not in ORIGIN_TYPES:
            raise NexusArtifactManifestError("invalid artifact kind or source type")
        if origin_id_sha256 is None:
            origin_id_sha256 = sha256(b"colossus:local-host").hexdigest()
        _hash_value(origin_id_sha256, "origin")
        content_sha, size, relative = self._measure(relative_path)
        key = _digest({"mission_id": self.mission_id, "artifact_id": opaque})
        path_sha = sha256(relative.encode("utf-8")).hexdigest()
        with self._lock:
            history = self._by_artifact.get(key, [])
            if len(self._records) >= self.limits.max_records or len(history) >= self.limits.max_versions_per_artifact:
                raise NexusArtifactManifestError("manifest record/version limit reached")
            previous = history[-1] if history else None
            if previous and previous.content_sha256 == content_sha and previous.relative_path_sha256 == path_sha:
                raise NexusArtifactManifestError("unchanged artifact cannot produce a new version")
            body = {
                "schema": MANIFEST_SCHEMA, "mission_id": self.mission_id,
                "sequence": len(self._records) + 1,
                "artifact_id_sha256": key, "kind": kind,
                "version": len(history) + 1, "content_sha256": content_sha,
                "size_bytes": size, "relative_path_sha256": path_sha,
                "origin_type": origin_type, "origin_id_sha256": origin_id_sha256,
                "captured_at_utc": _now(),
                "previous_version_sha256": previous.record_sha256 if previous else None,
                "previous_sha256": self._records[-1].record_sha256 if self._records else _ZERO,
            }
            record = ArtifactRecord(**body, record_sha256=_digest(body))
            self._records.append(record)
            self._by_artifact.setdefault(key, []).append(record)
            self._local_paths[key] = relative
            return record

    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(row.to_dict() for row in self._records)

    def history(self, artifact_id: str) -> tuple[dict[str, Any], ...]:
        key = _digest({"mission_id": self.mission_id, "artifact_id": _artifact_id(artifact_id)})
        with self._lock:
            return tuple(row.to_dict() for row in self._by_artifact.get(key, ()))

    def verify_file(self, artifact_id: str, relative_path: str | None = None,
                    *, version: int | None = None) -> dict[str, Any]:
        """Rehash actual file; 'matched' is byte equality, NOT content veracity."""
        key = _digest({"mission_id": self.mission_id, "artifact_id": _artifact_id(artifact_id)})
        with self._lock:
            history = self._by_artifact.get(key)
            if not history:
                raise NexusArtifactManifestError("unknown artifact")
            if version is None:
                expected = history[-1]
            elif type(version) is int and 1 <= version <= len(history):
                expected = history[version - 1]
            else:
                raise NexusArtifactManifestError("invalid artifact version")
            selected = relative_path if relative_path is not None else self._local_paths.get(key)
        if selected is None:
            raise NexusArtifactManifestError("provide path when verifying a restored manifest")
        content, size, relative = self._measure(selected)
        matched = (content == expected.content_sha256 and size == expected.size_bytes
                   and sha256(relative.encode("utf-8")).hexdigest() == expected.relative_path_sha256)
        return {"matched": matched, "record_sha256": expected.record_sha256,
                "version": expected.version, "actual_content_sha256": content,
                "actual_size_bytes": size, "integrity_only": True,
                "authenticity_verified": False}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            body = {
                "schema": MANIFEST_SCHEMA, "mission_id": self.mission_id,
                "record_count": len(self._records),
                "records": [record.to_dict() for record in self._records],
                "terminal_sha256": self._records[-1].record_sha256 if self._records else _ZERO,
                "integrity_only": True, "authenticity_verified": False,
            }
            encoded = _canonical(body)
            if len(encoded) > self.limits.max_snapshot_bytes:
                raise NexusArtifactManifestError("snapshot byte budget exceeded")
            return {**body, "snapshot_sha256": sha256(encoded).hexdigest()}

    @staticmethod
    def verify_snapshot(snapshot: Mapping[str, Any], *, max_bytes: int = 8_000_000) -> bool:
        if (not isinstance(snapshot, Mapping) or type(max_bytes) is not int
                or not 1 <= max_bytes <= 8_000_000 or set(snapshot) != _SNAPSHOT_KEYS
                or snapshot.get("schema") != MANIFEST_SCHEMA):
            raise NexusArtifactManifestError("invalid manifest snapshot")
        mission = _mission(snapshot["mission_id"])
        if snapshot["integrity_only"] is not True or snapshot["authenticity_verified"] is not False:
            raise NexusArtifactManifestError("misleading trust flags")
        _hash_value(snapshot["snapshot_sha256"], "snapshot")
        body = {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
        encoded = _canonical(body)
        if (len(encoded) > max_bytes or not hmac.compare_digest(snapshot["snapshot_sha256"], sha256(encoded).hexdigest())):
            raise NexusArtifactManifestError("manifest hash mismatch or excessive size")
        rows, count = snapshot["records"], snapshot["record_count"]
        if (not isinstance(rows, list) or type(count) is not int or count != len(rows)
                or count > 10_000):
            raise NexusArtifactManifestError("invalid manifest record count")
        last = _ZERO
        histories: dict[str, list[str]] = {}
        for i, item in enumerate(rows, 1):
            if not isinstance(item, dict) or set(item) != _RECORD_KEYS:
                raise NexusArtifactManifestError("invalid record fields")
            if (item["schema"] != MANIFEST_SCHEMA or item["mission_id"] != mission
                    or type(item["sequence"]) is not int or item["sequence"] != i
                    or item["kind"] not in ARTIFACT_KINDS
                    or item["origin_type"] not in ORIGIN_TYPES
                    or type(item["size_bytes"]) is not int
                    or not 0 <= item["size_bytes"] <= 64 * 1024 * 1024):
                raise NexusArtifactManifestError("invalid manifest record labels")
            for field_name in ("artifact_id_sha256", "content_sha256", "relative_path_sha256",
                               "origin_id_sha256", "previous_sha256", "record_sha256"):
                _hash_value(item[field_name], field_name)
            _utc(item["captured_at_utc"])
            key = item["artifact_id_sha256"]
            entries = histories.setdefault(key, [])
            if (type(item["version"]) is not int or item["version"] != len(entries) + 1
                    or item["previous_version_sha256"] != (entries[-1] if entries else None)
                    or item["previous_sha256"] != last):
                raise NexusArtifactManifestError("broken manifest version or sequence chain")
            calculated = _digest({k: v for k, v in item.items() if k != "record_sha256"})
            if not hmac.compare_digest(item["record_sha256"], calculated):
                raise NexusArtifactManifestError("altered manifest record")
            if item["record_sha256"] in entries:
                raise NexusArtifactManifestError("duplicate manifest record")
            entries.append(item["record_sha256"])
            last = item["record_sha256"]
        if snapshot["terminal_sha256"] != last:
            raise NexusArtifactManifestError("manifest terminal digest mismatch")
        return True

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any], workspace_root: str | os.PathLike[str],
                      *, limits: ArtifactManifestLimits | None = None) -> "NexusArtifactManifest":
        cls.verify_snapshot(snapshot)
        restored = cls(snapshot["mission_id"], workspace_root, limits=limits)
        if len(snapshot["records"]) > restored.limits.max_records:
            raise NexusArtifactManifestError("snapshot exceeds configured record limit")
        with restored._lock:
            for item in snapshot["records"]:
                key = item["artifact_id_sha256"]
                history = restored._by_artifact.setdefault(key, [])
                if len(history) >= restored.limits.max_versions_per_artifact or item["size_bytes"] > restored.limits.max_file_bytes:
                    raise NexusArtifactManifestError("snapshot exceeds configured limits")
                row = ArtifactRecord(**item)
                restored._records.append(row)
                history.append(row)
            restored.snapshot()  # recheck bound under the instance's configured limit
        return restored

    def to_proof_graph(self) -> Any:
        """Create a NEW digest-only graph; never assert evidence truth."""
        from .proof_graph import NexusProofGraph, ProofGraphLimits
        with self._lock:
            rows = tuple(self._records)
        graph = NexusProofGraph(self.mission_id, limits=ProofGraphLimits(
            max_nodes=max(1024, min(10000, len(rows) + 1)),
            max_edges=max(4096, min(40000, len(rows))),
        ))
        nodes: dict[str, str] = {}
        for row in rows:
            node = graph.add_node(
                "artifact", f"manifest:{row.record_sha256}", status="observed",
                source="internal", stage="artifact", digest_sha256=row.content_sha256,
                metrics={"sequence": row.sequence},
            )
            nodes[row.record_sha256] = node
            if row.previous_version_sha256:
                graph.add_edge("derives_from", node, nodes[row.previous_version_sha256])
        return graph

    def to_provenance_ledger(self) -> Any:
        """Create a NEW record of observed file digests, not source authentication."""
        from .provenance import NexusProvenanceLedger, ProvenanceLimits
        with self._lock:
            rows = tuple(self._records)
        ledger = NexusProvenanceLedger(self.mission_id, limits=ProvenanceLimits(
            max_records=max(1024, min(10000, len(rows))),
        ))
        provenance_ids: dict[str, str] = {}
        for row in rows:
            parents = (provenance_ids[row.previous_version_sha256],) if row.previous_version_sha256 else ()
            entry = ledger.record(
                "artifact", row.content_sha256, origin_type=row.origin_type,
                origin_id_sha256=row.origin_id_sha256, capture_method="direct_observation",
                operation="derived" if parents else "registered",
                parent_record_ids=parents, captured_at_utc=row.captured_at_utc,
            )
            provenance_ids[row.record_sha256] = entry.record_sha256
        return ledger


__all__ = [
    "MANIFEST_SCHEMA", "ARTIFACT_KINDS", "ORIGIN_TYPES", "NexusArtifactManifestError",
    "ArtifactManifestLimits", "ArtifactRecord", "NexusArtifactManifest",
]
