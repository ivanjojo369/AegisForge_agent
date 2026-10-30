"""Bounded, append-only provenance ledger for AegisForge Nexus — Colossus.

Destination: src/aegisforge/nexus/proof/provenance.py

Records where a *digest-identified* subject reportedly came from, what kind of
observation was registered, and the earlier provenance records it depends on.
Never accepts or exports raw file contents, secrets, model responses, identities,
absolute paths, or arbitrary textual descriptions.

Integrity verification checks structure, ordering, parent links and SHA-256
hashes, NOT authenticity or correctness of the observations. There is no
cryptographic signature, trusted timestamp, durable log, or OS sandbox here.
All operations are in memory; no filesystem/network/tool/model operations.

Optional bridges:
  ingest_evidence_trail(trail): validates the original NexusEvidenceTrail via
      a temporary NexusProofGraph, then imports its events transactionally.
  to_proof_graph(): returns a NEW NexusProofGraph projection, without altering
      an existing graph or claiming that an evidence edge proves its subject.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import re
from threading import RLock
from typing import Any, Mapping

PROVENANCE_SCHEMA = "aegisforge.nexus.proof.provenance.v1"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_MISSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_ZERO = "0" * 64

SUBJECT_KINDS = frozenset({
    "artifact", "evidence", "plan", "action", "check", "decision", "test_run"
})
ORIGIN_TYPES = frozenset({"nexus_evidence", "host", "operator", "model", "tool", "external"})
CAPTURE_METHODS = frozenset({"imported", "reported", "direct_observation", "attestation"})
OPERATIONS = frozenset({"created", "observed", "derived", "registered"})

_RECORD_KEYS = frozenset({
    "schema", "mission_id", "sequence", "subject_kind", "subject_sha256",
    "origin_type", "origin_id_sha256", "capture_method", "operation",
    "parent_record_ids", "source_event_sha256", "captured_at_utc",
    "previous_sha256", "record_sha256",
})
_SNAPSHOT_KEYS = frozenset({
    "schema", "mission_id", "record_count", "records", "terminal_sha256",
    "integrity_only", "authenticity_verified", "snapshot_sha256",
})


class NexusProvenanceError(ValueError):
    """Malformed provenance metadata, invalid chain, scope or limits."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise NexusProvenanceError("provenance data must be canonical JSON") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise NexusProvenanceError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _mission(value: Any) -> str:
    if not isinstance(value, str) or not _MISSION.fullmatch(value):
        raise NexusProvenanceError("mission_id must be an opaque, safe identifier")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        raise NexusProvenanceError("timestamp must be ISO-8601 UTC ending with Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() != timedelta(0):
            raise ValueError("not UTC")
    except ValueError as exc:
        raise NexusProvenanceError("invalid UTC timestamp") from exc
    return value


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ProvenanceLimits:
    max_records: int = 1024
    max_parents: int = 16
    max_snapshot_bytes: int = 1_500_000

    def __post_init__(self) -> None:
        for label, bound in (("max_records", 10_000), ("max_parents", 32),
                             ("max_snapshot_bytes", 8_000_000)):
            value = getattr(self, label)
            if type(value) is not int or not 1 <= value <= bound:
                raise NexusProvenanceError(f"invalid {label}")


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    schema: str
    mission_id: str
    sequence: int
    subject_kind: str
    subject_sha256: str
    origin_type: str
    origin_id_sha256: str
    capture_method: str
    operation: str
    parent_record_ids: tuple[str, ...]
    source_event_sha256: str | None
    captured_at_utc: str
    previous_sha256: str
    record_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "mission_id": self.mission_id,
            "sequence": self.sequence, "subject_kind": self.subject_kind,
            "subject_sha256": self.subject_sha256, "origin_type": self.origin_type,
            "origin_id_sha256": self.origin_id_sha256,
            "capture_method": self.capture_method, "operation": self.operation,
            "parent_record_ids": list(self.parent_record_ids),
            "source_event_sha256": self.source_event_sha256,
            "captured_at_utc": self.captured_at_utc,
            "previous_sha256": self.previous_sha256,
            "record_sha256": self.record_sha256,
        }


class NexusProvenanceLedger:
    """A mission-scoped, in-memory ledger of provenance metadata.

    Entries are appended only after all inputs have passed validation. The
    source type/method are caller DECLARATIONS, not authenticated identities.
    """

    def __init__(self, mission_id: str, *, limits: ProvenanceLimits | None = None) -> None:
        self.mission_id = _mission(mission_id)
        self.limits = limits or ProvenanceLimits()
        if not isinstance(self.limits, ProvenanceLimits):
            raise NexusProvenanceError("invalid provenance limits")
        self._records: list[ProvenanceRecord] = []
        self._by_id: dict[str, ProvenanceRecord] = {}
        self._imported_runs: set[str] = set()
        self._lock = RLock()

    def record(
        self, subject_kind: str, subject_sha256: str, *,
        origin_type: str, origin_id_sha256: str, capture_method: str,
        operation: str = "registered", parent_record_ids: tuple[str, ...] = (),
        source_event_sha256: str | None = None, captured_at_utc: str | None = None,
    ) -> ProvenanceRecord:
        """Append one digest-only record; does not read or verify the subject."""
        if not isinstance(subject_kind, str) or subject_kind not in SUBJECT_KINDS:
            raise NexusProvenanceError("unsupported subject kind")
        _sha(subject_sha256, "subject")
        if (not isinstance(origin_type, str) or origin_type not in ORIGIN_TYPES
                or not isinstance(capture_method, str) or capture_method not in CAPTURE_METHODS):
            raise NexusProvenanceError("unsupported origin or capture method")
        _sha(origin_id_sha256, "origin identifier")
        if not isinstance(operation, str) or operation not in OPERATIONS:
            raise NexusProvenanceError("unsupported provenance operation")
        if not isinstance(parent_record_ids, tuple) or len(parent_record_ids) > self.limits.max_parents:
            raise NexusProvenanceError("invalid parent record collection")
        for parent in parent_record_ids:
            _sha(parent, "parent record")
        if len(set(parent_record_ids)) != len(parent_record_ids):
            raise NexusProvenanceError("duplicate parent record")
        if operation == "derived" and not parent_record_ids:
            raise NexusProvenanceError("derived records require an earlier parent")
        if source_event_sha256 is not None:
            _sha(source_event_sha256, "source event")
            if subject_kind != "evidence":
                raise NexusProvenanceError("source event digest requires evidence subject")
        at = _timestamp(captured_at_utc if captured_at_utc is not None else _now_utc())
        with self._lock:
            if len(self._records) >= self.limits.max_records:
                raise NexusProvenanceError("provenance record limit reached")
            for parent in parent_record_ids:
                if parent not in self._by_id:
                    raise NexusProvenanceError("parent must exist in same ledger and precede child")
            body: dict[str, Any] = {
                "schema": PROVENANCE_SCHEMA, "mission_id": self.mission_id,
                "sequence": len(self._records) + 1, "subject_kind": subject_kind,
                "subject_sha256": subject_sha256, "origin_type": origin_type,
                "origin_id_sha256": origin_id_sha256,
                "capture_method": capture_method, "operation": operation,
                "parent_record_ids": list(parent_record_ids),
                "source_event_sha256": source_event_sha256,
                "captured_at_utc": at,
                "previous_sha256": self._records[-1].record_sha256 if self._records else _ZERO,
            }
            record = ProvenanceRecord(**{**body, "parent_record_ids": parent_record_ids},
                                      record_sha256=_digest(body))
            self._records.append(record)
            self._by_id[record.record_sha256] = record
            return record

    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(record.to_dict() for record in self._records)

    def get(self, record_sha256: str) -> dict[str, Any] | None:
        _sha(record_sha256, "record")
        with self._lock:
            record = self._by_id.get(record_sha256)
            return record.to_dict() if record else None

    def find_subject(self, subject_kind: str, subject_sha256: str, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if not isinstance(subject_kind, str) or subject_kind not in SUBJECT_KINDS or type(limit) is not int or not 1 <= limit <= 500:
            raise NexusProvenanceError("invalid subject lookup")
        _sha(subject_sha256, "subject")
        with self._lock:
            return tuple(row.to_dict() for row in self._records
                         if row.subject_kind == subject_kind and row.subject_sha256 == subject_sha256)[:limit]

    def ancestors(self, record_sha256: str, *, max_depth: int = 8, max_results: int = 250) -> tuple[str, ...]:
        _sha(record_sha256, "record")
        if type(max_depth) is not int or not 1 <= max_depth <= 32:
            raise NexusProvenanceError("invalid ancestry depth")
        if type(max_results) is not int or not 1 <= max_results <= 1000:
            raise NexusProvenanceError("invalid ancestry result limit")
        with self._lock:
            if record_sha256 not in self._by_id:
                raise NexusProvenanceError("unknown provenance record")
            found: list[str] = []
            seen: set[str] = set()
            frontier = [(record_sha256, 0)]
            while frontier:
                current, depth = frontier.pop(0)
                if depth >= max_depth:
                    continue
                for parent in self._by_id[current].parent_record_ids:
                    if parent not in seen:
                        seen.add(parent)
                        found.append(parent)
                        if len(found) >= max_results:
                            return tuple(found)
                        frontier.append((parent, depth + 1))
            return tuple(found)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            body: dict[str, Any] = {
                "schema": PROVENANCE_SCHEMA, "mission_id": self.mission_id,
                "record_count": len(self._records),
                "records": [record.to_dict() for record in self._records],
                "terminal_sha256": self._records[-1].record_sha256 if self._records else _ZERO,
                "integrity_only": True, "authenticity_verified": False,
            }
            encoded = _canonical(body)
            if len(encoded) > self.limits.max_snapshot_bytes:
                raise NexusProvenanceError("snapshot byte limit reached")
            return {**body, "snapshot_sha256": sha256(encoded).hexdigest()}

    @staticmethod
    def verify_snapshot(snapshot: Mapping[str, Any], *, max_bytes: int = 8_000_000) -> bool:
        """Validate internal chain consistency. Does NOT verify evidence authenticity."""
        if not isinstance(snapshot, Mapping) or type(max_bytes) is not int or not 1 <= max_bytes <= 8_000_000:
            raise NexusProvenanceError("invalid snapshot or byte limit")
        if set(snapshot) != _SNAPSHOT_KEYS or snapshot.get("schema") != PROVENANCE_SCHEMA:
            raise NexusProvenanceError("invalid provenance snapshot schema")
        _mission(snapshot["mission_id"])
        if snapshot["integrity_only"] is not True or snapshot["authenticity_verified"] is not False:
            raise NexusProvenanceError("misleading authenticity flags")
        _sha(snapshot["snapshot_sha256"], "snapshot")
        body = {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
        encoded = _canonical(body)
        if len(encoded) > max_bytes or not hmac.compare_digest(snapshot["snapshot_sha256"], sha256(encoded).hexdigest()):
            raise NexusProvenanceError("snapshot digest mismatch or byte limit exceeded")
        rows = snapshot["records"]
        count = snapshot["record_count"]
        if (not isinstance(rows, list) or type(count) is not int or count != len(rows)
                or count > 10_000):
            raise NexusProvenanceError("invalid snapshot record count")
        previous = _ZERO
        seen: set[str] = set()
        for sequence, row in enumerate(rows, 1):
            if not isinstance(row, dict) or set(row) != _RECORD_KEYS:
                raise NexusProvenanceError("invalid record fields")
            if (row["schema"] != PROVENANCE_SCHEMA or row["mission_id"] != snapshot["mission_id"]
                    or type(row["sequence"]) is not int or row["sequence"] != sequence
                    or not isinstance(row["subject_kind"], str) or row["subject_kind"] not in SUBJECT_KINDS
                    or not isinstance(row["origin_type"], str) or row["origin_type"] not in ORIGIN_TYPES
                    or not isinstance(row["capture_method"], str) or row["capture_method"] not in CAPTURE_METHODS
                    or not isinstance(row["operation"], str) or row["operation"] not in OPERATIONS):
                raise NexusProvenanceError("invalid provenance labels or sequence")
            for key in ("subject_sha256", "origin_id_sha256", "previous_sha256", "record_sha256"):
                _sha(row[key], key)
            if row["source_event_sha256"] is not None:
                _sha(row["source_event_sha256"], "source event")
                if row["subject_kind"] != "evidence":
                    raise NexusProvenanceError("source event cannot describe non-evidence subject")
            _timestamp(row["captured_at_utc"])
            parents = row["parent_record_ids"]
            if not isinstance(parents, list) or len(parents) > 32:
                raise NexusProvenanceError("invalid provenance parents")
            if (any(not isinstance(p, str) or not _HEX.fullmatch(p) or p not in seen for p in parents)
                    or len(parents) != len(set(parents))
                    or (row["operation"] == "derived" and not parents)):
                raise NexusProvenanceError("invalid or forward provenance parents")
            digest = _digest({k: v for k, v in row.items() if k != "record_sha256"})
            if (row["previous_sha256"] != previous or not hmac.compare_digest(row["record_sha256"], digest)
                    or row["record_sha256"] in seen):
                raise NexusProvenanceError("broken provenance hash chain")
            seen.add(row["record_sha256"])
            previous = row["record_sha256"]
        if snapshot["terminal_sha256"] != previous:
            raise NexusProvenanceError("terminal record hash mismatch")
        return True

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any], *, limits: ProvenanceLimits | None = None) -> "NexusProvenanceLedger":
        """Reconstitute metadata for inspection, with no writes or trust upgrade."""
        cls.verify_snapshot(snapshot)
        result = cls(snapshot["mission_id"], limits=limits)
        if len(snapshot["records"]) > result.limits.max_records:
            raise NexusProvenanceError("snapshot exceeds configured record limit")
        with result._lock:
            for row in snapshot["records"]:
                if len(row["parent_record_ids"]) > result.limits.max_parents:
                    raise NexusProvenanceError("snapshot exceeds configured parent limit")
                record = ProvenanceRecord(**{**row, "parent_record_ids": tuple(row["parent_record_ids"])})
                result._records.append(record)
                result._by_id[record.record_sha256] = record
                if record.origin_type == "nexus_evidence":
                    result._imported_runs.add(record.origin_id_sha256)
            # No external source has been authenticated by this reconstruction.
            if len(_canonical(result.snapshot())) > result.limits.max_snapshot_bytes:
                raise NexusProvenanceError("snapshot exceeds configured byte limit")
        return result

    def ingest_evidence_trail(self, trail: Any) -> dict[str, Any]:
        """Validate and incorporate an original NexusEvidenceTrail atomically.

        The original EvidenceTrail remains unchanged. Imported event identities
        are digests and source status is a declaration, not authenticity proof.
        """
        from ..evidence import NexusEvidenceTrail
        from .proof_graph import NexusProofGraph
        if not isinstance(trail, NexusEvidenceTrail):
            raise NexusProvenanceError("expected original NexusEvidenceTrail")
        # The original ProofGraph already contains a strict validator for the
        # Nexus evidence event chain; reuse it instead of duplicating the schema.
        try:
            NexusProofGraph(self.mission_id).ingest_evidence_trail(trail)
        except Exception as exc:
            raise NexusProvenanceError("original evidence trail did not validate") from exc
        run_digest = sha256(trail.run_id.encode("ascii")).hexdigest()
        events = trail.events
        with self._lock:
            if run_digest in self._imported_runs:
                raise NexusProvenanceError("evidence run already imported")
            if len(self._records) + len(events) > self.limits.max_records:
                raise NexusProvenanceError("insufficient record capacity")
            old_records = self._records[:]
            old_by_id = self._by_id.copy()
            old_runs = self._imported_runs.copy()
            try:
                parent: tuple[str, ...] = ()
                for event in events:
                    row = self.record(
                        "evidence", event.sha256, origin_type="nexus_evidence",
                        origin_id_sha256=run_digest, capture_method="imported",
                        operation="derived" if parent else "registered",
                        parent_record_ids=parent, source_event_sha256=event.sha256,
                        captured_at_utc=event.at_utc,
                    )
                    parent = (row.record_sha256,)
                self._imported_runs.add(run_digest)
                return {
                    "run_sha256": run_digest,
                    "imported_events": len(events),
                    "last_record_sha256": parent[0],
                    "terminal_sha256": self._records[-1].record_sha256,
                }
            except Exception:
                self._records = old_records
                self._by_id = old_by_id
                self._imported_runs = old_runs
                raise

    def to_proof_graph(self) -> Any:
        """Produce an independent graph representation, without mutating any graph."""
        from .proof_graph import NexusProofGraph, ProofGraphLimits
        with self._lock:
            rows = tuple(self._records)
        graph = NexusProofGraph(self.mission_id, limits=ProofGraphLimits(
            max_nodes=max(1024, min(10000, 1 + len(rows) * 2)),
            max_edges=max(4096, min(40000, len(rows) * (self.limits.max_parents + 1))),
        ))
        ids: dict[str, str] = {}
        for row in rows:
            node = graph.add_node(
                "evidence", f"provenance:{row.record_sha256}",
                status="observed", source="internal", stage="artifact",
                digest_sha256=row.record_sha256,
                metrics={"sequence": row.sequence},
            )
            ids[row.record_sha256] = node
            subject = graph.add_node(
                row.subject_kind, f"subject:{row.subject_sha256}",
                digest_sha256=row.subject_sha256,
            )
            if row.subject_kind == "evidence":
                graph.add_edge("derives_from", node, subject)
            else:
                graph.add_edge("documents", node, subject)
            for parent in row.parent_record_ids:
                graph.add_edge("derives_from", node, ids[parent])
        return graph


__all__ = [
    "PROVENANCE_SCHEMA", "NexusProvenanceError", "ProvenanceLimits",
    "ProvenanceRecord", "NexusProvenanceLedger", "SUBJECT_KINDS",
    "ORIGIN_TYPES", "CAPTURE_METHODS", "OPERATIONS",
]
