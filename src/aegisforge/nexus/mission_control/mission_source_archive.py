"""Checkpoint-bound, read-only archive of *display summaries*, not live sources.

A host explicitly captures these summaries from existing, independently checked
NexusMissionMonitorViews. The record is bound to an immutable Mission Registry
checkpoint. Reload never reconstitutes ApprovalGate, does not restore permissions,
does not execute tools, and does not claim evidence authenticity. An archive
contains selected presentation metadata only; raw evaluation reports, prompts,
source files, tokens, action bindings and approval credentials are excluded.

The SHA-256 envelope detects accidental or inconsistent edits, not malicious
replacement by someone able to write the archive and its checkpoint.
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
from typing import Any, Iterable, Mapping

from .mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from .mission_monitor_views import NexusMissionMonitorViews, MONITOR_VIEW_SCHEMA

SOURCE_ARCHIVE_SCHEMA = "aegisforge.nexus.mission_control.source_archive.v1"
_ALLOWED_KINDS = ("graph", "laboratory", "trust")
_NAME = "mission_sources.json"
_MID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_HEX = re.compile(r"^[a-f0-9]{64}$")
_MAX_BYTES = 2_000_000


class NexusMissionSourceArchiveError(ValueError):
    """Archive missing, changed, invalid, or bound to another checkpoint."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise NexusMissionSourceArchiveError(message)


def _json(obj: Any) -> bytes:
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusMissionSourceArchiveError("invalid archive JSON") from exc


def _hash(obj: Any) -> str:
    return sha256(_json(obj)).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        _check(key not in out, "duplicate archive JSON key")
        out[key] = value
    return out


def _regular_unlinked(path: Path) -> None:
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise NexusMissionSourceArchiveError("archive file unavailable") from exc
    _check(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and
           not (getattr(st, "st_file_attributes", 0) &
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)),
           "linked or non-regular archive forbidden")


def _read_file(path: Path) -> bytes:
    _regular_unlinked(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            start = os.fstat(fd)
            _check(stat.S_ISREG(start.st_mode) and start.st_nlink == 1
                   and start.st_size <= _MAX_BYTES, "archive file too large or unsafe")
            with os.fdopen(fd, "rb", closefd=False) as f:
                raw = f.read(_MAX_BYTES + 1)
            end = os.fstat(fd)
            _check(len(raw) <= _MAX_BYTES and
                   (start.st_dev, start.st_ino, start.st_size, start.st_mtime_ns) ==
                   (end.st_dev, end.st_ino, end.st_size, end.st_mtime_ns),
                   "archive changed during read")
            return raw
        finally:
            os.close(fd)
    except NexusMissionSourceArchiveError:
        raise
    except OSError as exc:
        raise NexusMissionSourceArchiveError("archive could not be read") from exc


def _pinned_path(folder: Path, monitor: NexusMissionCheckpointMonitor) -> Path:
    _check(type(monitor) is NexusMissionCheckpointMonitor, "checkpoint monitor required")
    _check(isinstance(folder, Path), "archive root must be a Path")
    # The store already validated this private root; never accept a second root.
    _check(folder.absolute() == monitor._store._root and folder.is_dir(),
           "archive must reside in the checkpoint directory")
    return folder / _NAME


def _validate_presentation(kind: str, mid: str, data: Any) -> None:
    """Enforce a strict, display-only shape from the existing view contract."""
    _check(type(data) is dict and data.get("available") is True
           and data.get("kind") == {"graph":"proof_graph", "laboratory":"laboratory", "trust":"trust"}[kind]
           and data.get("mission_id") == mid,
           "invalid archived view or cross-mission source")
    _check(data.get("schema") == MONITOR_VIEW_SCHEMA,
           "invalid monitor presentation version")
    _check(data.get("integrity_only") is True and data.get("authenticity_verified") is False
           and data.get("archived_metadata_only") is True,
           "archive must not claim live authenticity")
    fields = {
        "graph": {"schema", "kind", "available", "mission_id", "graph_sha256",
                  "node_count", "edge_count", "nodes", "edges", "integrity_only",
                  "authenticity_verified", "archived_metadata_only"},
        "laboratory": {"schema", "kind", "available", "mission_id", "scorecard_sha256",
                       "status", "aggregate", "by_product", "by_category",
                       "evaluation_reports", "regression_reports", "ablation_studies",
                       "review_flags", "verified_against_sources", "verified_at_capture",
                       "integrity_only", "authenticity_verified", "archived_metadata_only"},
        "trust": {"schema", "kind", "available", "mission_id", "count", "requests",
                  "counts", "status_only", "authorization_granted", "effects_executed",
                  "integrity_only", "authenticity_verified", "archived_metadata_only",
                  "historical_only"},
    }
    _check(set(data) == fields[kind], "unexpected archived presentation fields")
    if kind == "graph":
        _check(type(data.get("node_count")) is int and type(data.get("edge_count")) is int,
               "invalid graph counts")
        nodes, edges = data.get("nodes"), data.get("edges")
        _check(type(nodes) is list and type(edges) is list and
               len(nodes) == data["node_count"] <= 64 and
               len(edges) == data["edge_count"] <= 128,
               "invalid archived graph size")
        ids: set[str] = set()
        for node in nodes:
            _check(type(node) is dict and set(node) ==
                   {"node_id", "kind", "status", "stage", "source"} and
                   type(node["node_id"]) is str and len(node["node_id"]) <= 180 and
                   all(type(node[k]) is str and len(node[k]) <= 40
                       for k in ("kind", "status", "stage", "source")) and
                   node["node_id"] not in ids, "invalid graph node")
            ids.add(node["node_id"])
        for edge in edges:
            _check(type(edge) is dict and set(edge) ==
                   {"source_node_id", "target_node_id", "relation"} and
                   edge["source_node_id"] in ids and edge["target_node_id"] in ids and
                   type(edge["relation"]) is str and len(edge["relation"]) <= 40,
                   "invalid graph edge")
        _check(type(data.get("graph_sha256")) is str and
               _HEX.fullmatch(data["graph_sha256"]) is not None,
               "missing graph digest")
    elif kind == "laboratory":
        a = data.get("aggregate")
        _check(type(a) is dict and set(a) == {"evaluations", "passed", "failed", "inconclusive"} and all(type(a.get(k)) is int and 0 <= a[k] <= 100000
               for k in ("evaluations", "passed", "failed", "inconclusive")) and
               a["passed"]+a["failed"]+a["inconclusive"] == a["evaluations"],
               "invalid laboratory totals")
        for key in ("by_product", "by_category"):
            groups = data.get(key)
            _check(type(groups) is dict and len(groups) <= 32, "invalid laboratory groups")
            for group, counts in groups.items():
                _check(type(group) is str and len(group) <= 60 and type(counts) is dict and
                       set(counts) == {"evaluations", "passed", "failed", "inconclusive"} and
                       all(type(counts.get(k)) is int and 0 <= counts[k] <= 100000
                           for k in ("evaluations", "passed", "failed", "inconclusive")),
                       "invalid laboratory group metrics")
        for key in ("evaluation_reports", "regression_reports", "ablation_studies"):
            _check(type(data.get(key)) is int and 0 <= data[key] <= 100000,
                   "invalid laboratory reports")
        for groups in (data["by_product"], data["by_category"]):
            for metric in ("evaluations", "passed", "failed", "inconclusive"):
                _check(sum(group[metric] for group in groups.values()) == a[metric],
                       "laboratory subtotals inconsistent")
        _check(type(data.get("status")) is str and len(data["status"]) <= 80,
               "invalid laboratory state")
        _check(type(data.get("review_flags")) is list and len(data["review_flags"]) <= 20
               and all(type(x) is str and len(x) <= 100 for x in data["review_flags"]),
               "invalid laboratory flags")
        _check(data.get("verified_against_sources") is False and
               data.get("verified_at_capture") is True and
               type(data.get("scorecard_sha256")) is str and
               _HEX.fullmatch(data["scorecard_sha256"]) is not None,
               "archived scorecard cannot claim live source verification")
    elif kind == "trust":
        rows = data.get("requests")
        _check(type(rows) is list and len(rows) <= 24 and
               type(data.get("count")) is int and data["count"] == len(rows) and
               data.get("status_only") is True and data.get("historical_only") is True and
               data.get("authorization_granted") is False and
               data.get("effects_executed") is False,
               "invalid historic Trust summary")
        for row in rows:
            _check(type(row) is dict and set(row) == {"request_sha256", "operation", "status"}
                   and type(row["request_sha256"]) is str and
                   _HEX.fullmatch(row["request_sha256"]) is not None and
                   type(row["operation"]) is str and len(row["operation"]) <= 80 and
                   row["status"] in ("pending", "reviewing", "approved", "denied",
                                     "expired", "consumed", "verifying"),
                   "invalid historic Trust request")
        counts = data.get("counts")
        _check(type(counts) is dict and set(counts) ==
               {"pending", "reviewing", "approved", "denied", "expired", "consumed", "verifying"}
               and all(type(v) is int and v >= 0 for v in counts.values()) and
               sum(counts.values()) == len(rows) and
               all(sum(row["status"] == state for row in rows) == n
                   for state, n in counts.items()), "invalid historic Trust counts")


def _frozen(kind: str, value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    result["archived_metadata_only"] = True
    if kind == "laboratory":
        result["verified_at_capture"] = result.pop("verified_against_sources")
        result["verified_against_sources"] = False
    if kind == "trust":
        result["historical_only"] = True
        result["authorization_granted"] = False
        result["effects_executed"] = False
    return result


class NexusMissionSourceArchive:
    """Read a pinned archive; never restores live sources or permissions."""

    def __init__(self, path: Path, monitor: NexusMissionCheckpointMonitor):
        expected = _pinned_path(path.parent, monitor)
        _check(path == expected, "unapproved source archive filename")
        monitor.assert_current()
        self._monitor = monitor
        self._path = path
        raw = _read_file(path)
        self._file_sha256 = sha256(raw).hexdigest()
        try:
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
            raise NexusMissionSourceArchiveError("archive JSON malformed") from exc
        self._verify(payload)
        self._payload = payload

    def _verify(self, payload: Any) -> None:
        _check(type(payload) is dict and set(payload) == {
            "schema", "checkpoint_generation", "checkpoint_envelope_sha256",
            "checkpoint_registry_sha256", "entries", "integrity_only",
            "authenticity_verified", "archive_sha256"}, "invalid archive schema")
        receipt = self._monitor.status()
        _check(payload["schema"] == SOURCE_ARCHIVE_SCHEMA and
               type(payload["checkpoint_generation"]) is int and
               payload["checkpoint_generation"] == receipt["generation"] and
               payload["checkpoint_envelope_sha256"] == receipt["envelope_sha256"] and
               payload["checkpoint_registry_sha256"] == receipt["registry_sha256"] and
               payload["integrity_only"] is True and
               payload["authenticity_verified"] is False,
               "archive not bound to the loaded checkpoint")
        _check(type(payload["archive_sha256"]) is str and
               _HEX.fullmatch(payload["archive_sha256"]) is not None,
               "invalid archive hash")
        body = {k: v for k,v in payload.items() if k != "archive_sha256"}
        _check(hmac.compare_digest(payload["archive_sha256"], _hash(body)),
               "archive hash mismatch")
        entries = payload["entries"]
        _check(type(entries) is dict and len(entries) <= 128,
               "invalid archive entries")
        known_missions = {x["mission_id"] for x in self._monitor._registry.snapshot()["missions"]}
        for mid, sources in entries.items():
            _check(type(mid) is str and _MID.fullmatch(mid) is not None
                   and mid in known_missions and type(sources) is dict
                   and set(sources) <= set(_ALLOWED_KINDS) and bool(sources),
                   "invalid archive mission mapping")
            for kind, data in sources.items():
                _validate_presentation(kind, mid, data)

    @classmethod
    def write_new(cls, checkpoint_directory: Path, monitor: NexusMissionCheckpointMonitor,
                  views: NexusMissionMonitorViews,
                  mission_ids: Iterable[str]) -> Path:
        """Explicit one-time host export; never overwrite or auto-save."""
        path = _pinned_path(checkpoint_directory, monitor)
        _check(type(views) is NexusMissionMonitorViews, "verified views required")
        monitor.assert_current()
        missions = {r["mission_id"] for r in monitor._registry.snapshot()["missions"]}
        ids = tuple(mission_ids)
        _check(len(ids) <= 128 and len(ids) == len(set(ids)) and
               all(type(mid) is str and mid in missions for mid in ids),
               "invalid mission selection")
        entries: dict[str, dict[str, Any]] = {}
        for mid in sorted(ids):
            groups: dict[str, Any] = {}
            for kind, method in (("graph", views.graph), ("laboratory", views.laboratory),
                                 ("trust", views.trust)):
                value = method(mid)  # verifies live source contracts
                if value["available"]:
                    frozen = _frozen(kind, value)
                    _validate_presentation(kind, mid, frozen)
                    groups[kind] = frozen
            if groups:
                entries[mid] = groups
        checkpoint = monitor.status()
        body = {"schema": SOURCE_ARCHIVE_SCHEMA,
                "checkpoint_generation": checkpoint["generation"],
                "checkpoint_envelope_sha256": checkpoint["envelope_sha256"],
                "checkpoint_registry_sha256": checkpoint["registry_sha256"],
                "entries": entries, "integrity_only": True, "authenticity_verified": False}
        payload = {**body, "archive_sha256": _hash(body)}
        data = _json(payload) + b"\n"
        _check(len(data) <= _MAX_BYTES, "archive too large")
        monitor.assert_current()
        # Exclusive create, restrictive mode where available, no overwrite.
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            fd = os.open(path, flags, 0o600)
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(fd)
        except OSError as exc:
            raise NexusMissionSourceArchiveError("archive save denied or already exists") from exc
        return path

    def assert_current(self) -> None:
        self._monitor.assert_current()
        raw = _read_file(self._path)
        _check(hmac.compare_digest(self._file_sha256, sha256(raw).hexdigest()),
               "archive changed since startup; explicit restart required")

    def summary(self, kind: str, mission_id: str) -> dict[str, Any] | None:
        _check(kind in _ALLOWED_KINDS and type(mission_id) is str,
               "invalid requested archive source")
        self.assert_current()
        data = self._payload["entries"].get(mission_id, {}).get(kind)
        return deepcopy(data) if data is not None else None

    def visible_status(self, visible_mission_ids: Iterable[str]) -> dict[str, Any]:
        self.assert_current()
        selected = set(visible_mission_ids)
        return {"configured": True, "archived_metadata_only": True,
                "authenticity_verified": False, "trust_historical_only": True,
                "visible_missions_with_sources": sum(mid in selected for mid in self._payload["entries"]),
                "archive_sha256": self._payload["archive_sha256"]}


__all__ = ["SOURCE_ARCHIVE_SCHEMA", "NexusMissionSourceArchiveError",
           "NexusMissionSourceArchive"]
