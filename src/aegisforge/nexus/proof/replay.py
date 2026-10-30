"""Deterministic, non-executing replay of Colossus evidence metadata.

Destination: src/aegisforge/nexus/proof/replay.py

Reconstructs a *reported* chronology from already captured NexusEvidenceTrail,
NexusProofGraph, NexusProvenanceLedger and NexusArtifactManifest snapshots.
All input snapshots are verified with their OWN existing validators before use.
The implementation never reruns a tool, applies a patch, reads an artifact,
calls Nebius, invokes Docker, or writes files. Timestamps are source-reported;
chronological sorting is not proof of causal ordering or genuine time.

All exported timeline identifiers are digests, not raw file paths or prompts.
Snapshot hashes demonstrate internal consistency, not authenticity. The caller
must authenticate sources independently. Missing cross-source linkage is
reported rather than silently inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Mapping

from .proof_graph import NexusProofGraph
from .provenance import NexusProvenanceLedger, SUBJECT_KINDS, OPERATIONS
from .artifact_manifest import NexusArtifactManifest, ARTIFACT_KINDS

REPLAY_SCHEMA = "aegisforge.nexus.proof.replay.v1"
_MISSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HEX = re.compile(r"^[a-f0-9]{64}$")
_STREAMS = frozenset({"evidence", "provenance", "artifact"})
_SOURCES = frozenset({"graph", "provenance", "manifest", "evidence"})
_TIMELINE_KEYS = frozenset({"stream", "sequence", "at_utc", "ref_sha256", "kind", "status"})
_REPORT_KEYS = frozenset({
    "schema", "mission_id", "status", "integrity_verified", "authenticity_verified",
    "effects_replayed", "source_sha256", "source_counts", "timeline",
    "timeline_count", "links", "warnings", "report_sha256",
})


class NexusReplayError(ValueError):
    """Invalid snapshot, incompatible mission, or unbounded replay input."""


def _canonical(data: Any) -> bytes:
    try:
        return json.dumps(data, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise NexusReplayError("expected bounded, JSON-compatible replay metadata") from exc


def _digest(data: Any) -> str:
    return sha256(_canonical(data)).hexdigest()


def _mission(value: Any) -> str:
    if not isinstance(value, str) or _MISSION.fullmatch(value) is None:
        raise NexusReplayError("invalid opaque mission identifier")
    return value


def _hash(value: Any) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise NexusReplayError("invalid SHA-256 identifier")
    return value


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 36 or not value.endswith("Z"):
        raise NexusReplayError("timestamp must be an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("non-UTC timestamp")
        return parsed
    except (ValueError, OverflowError) as exc:
        raise NexusReplayError("invalid UTC timestamp") from exc


def _sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (_parse_time(row["at_utc"]), row["stream"], row["sequence"], row["ref_sha256"])


def _frozen_snapshot(value: Mapping[str, Any], maximum: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NexusReplayError("source snapshot must be a mapping")
    encoded = _canonical(value)
    if len(encoded) > maximum:
        raise NexusReplayError("source snapshot exceeds replay byte budget")
    result = json.loads(encoded)
    if not isinstance(result, dict):
        raise NexusReplayError("invalid snapshot object")
    return result


@dataclass(frozen=True, slots=True)
class ReplayLimits:
    max_timeline_entries: int = 4096
    max_source_bytes: int = 2_000_000
    max_report_bytes: int = 3_000_000
    max_warnings: int = 64

    def __post_init__(self) -> None:
        for name, ceiling in (
            ("max_timeline_entries", 30_000),
            ("max_source_bytes", 8_000_000),
            ("max_report_bytes", 8_000_000),
            ("max_warnings", 512),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise NexusReplayError(f"invalid {name} limit")


class NexusReplay:
    """Validate and correlate immutable copies of existing mission records.

    construct() returns a JSON-safe report; it does NOT change the underlying
    graph, ledger, manifest, or evidence trail. Supplying a graph alone is valid.
    A link check becomes mandatory only when the source declares that a given
    run was ingested. Other unrelated records cannot prove missing provenance.
    """

    def __init__(self, mission_id: str, *, limits: ReplayLimits | None = None) -> None:
        self.mission_id = _mission(mission_id)
        self.limits = limits or ReplayLimits()
        if not isinstance(self.limits, ReplayLimits):
            raise NexusReplayError("invalid replay limits")

    def reconstruct(
        self, *, graph_snapshot: Mapping[str, Any] | None = None,
        provenance_snapshot: Mapping[str, Any] | None = None,
        artifact_snapshot: Mapping[str, Any] | None = None,
        evidence_trail: Any | None = None,
    ) -> dict[str, Any]:
        """Return digest-only timeline and explicitly evaluated cross-links.

        The original evidence trail is validated by ProofGraph's original-chain
        ingester before reconstruction. Exported artifact snapshots do not
        include original paths, so this operation cannot rehash files on disk.
        """
        if all(x is None for x in (graph_snapshot, provenance_snapshot, artifact_snapshot, evidence_trail)):
            raise NexusReplayError("at least one previously captured source is required")

        snapshots: dict[str, dict[str, Any]] = {}
        validators = (
            ("graph", graph_snapshot, NexusProofGraph.verify_snapshot),
            ("provenance", provenance_snapshot, NexusProvenanceLedger.verify_snapshot),
            ("manifest", artifact_snapshot, NexusArtifactManifest.verify_snapshot),
        )
        for name, source, validator in validators:
            if source is None:
                continue
            frozen = _frozen_snapshot(source, self.limits.max_source_bytes)
            try:
                if not validator(frozen, max_bytes=self.limits.max_source_bytes):
                    raise NexusReplayError("source rejected by validator")
            except Exception as exc:
                raise NexusReplayError(f"{name} snapshot failed integrity validation") from exc
            if frozen["mission_id"] != self.mission_id:
                raise NexusReplayError("cross-mission replay is forbidden")
            snapshots[name] = frozen

        events: tuple[dict[str, Any], ...] = ()
        run_id: str | None = None
        if evidence_trail is not None:
            # This bridge validates ordering, event SHA links, and the Nexus
            # plan/execution/verification contract using existing code.
            from ..evidence import NexusEvidenceTrail
            if not isinstance(evidence_trail, NexusEvidenceTrail):
                raise NexusReplayError("evidence_trail must be NexusEvidenceTrail")
            try:
                validator = NexusProofGraph(self.mission_id)
                validator.ingest_evidence_trail(evidence_trail)
                events = tuple(e.to_dict() for e in evidence_trail.events)
                run_id = evidence_trail.run_id
            except Exception as exc:
                raise NexusReplayError("original evidence chain did not validate") from exc
            if not events or len(_canonical(events)) > self.limits.max_source_bytes:
                raise NexusReplayError("invalid or oversized evidence trail")

        source_sha = {
            "graph": snapshots["graph"]["graph_sha256"] if "graph" in snapshots else None,
            "provenance": snapshots["provenance"]["snapshot_sha256"] if "provenance" in snapshots else None,
            "manifest": snapshots["manifest"]["snapshot_sha256"] if "manifest" in snapshots else None,
            "evidence": _digest(list(events)) if events else None,
        }
        timelines: list[dict[str, Any]] = []
        for ev in events:
            timelines.append({"stream": "evidence", "sequence": ev["sequence"],
                              "at_utc": ev["at_utc"], "ref_sha256": ev["sha256"],
                              "kind": ev["stage"], "status": ev["status"]})
        for rec in snapshots.get("provenance", {}).get("records", ()):
            timelines.append({"stream": "provenance", "sequence": rec["sequence"],
                              "at_utc": rec["captured_at_utc"],
                              "ref_sha256": rec["record_sha256"],
                              "kind": rec["subject_kind"], "status": rec["operation"]})
        for rec in snapshots.get("manifest", {}).get("records", ()):
            timelines.append({"stream": "artifact", "sequence": rec["sequence"],
                              "at_utc": rec["captured_at_utc"],
                              "ref_sha256": rec["record_sha256"],
                              "kind": rec["kind"], "status": "recorded"})
        if len(timelines) > self.limits.max_timeline_entries:
            raise NexusReplayError("timeline exceeds configured entry limit")
        timelines.sort(key=_sort_key)

        # Correlate only source-asserted links. Never manufacture a proof edge.
        links: dict[str, Any] = {
            "graph_evidence": "not_applicable",
            "provenance_evidence": "not_applicable",
            "manifest_provenance_matches": 0,
        }
        warnings: list[str] = []
        if events:
            ev_sha = {ev["sha256"] for ev in events}
            run_sha = sha256(run_id.encode("ascii")).hexdigest()
            graph = snapshots.get("graph")
            if graph is not None:
                run_found = any(n["kind"] == "run" and n["key_sha256"] == run_sha
                                for n in graph["nodes"])
                if run_found:
                    found = {n["digest_sha256"] for n in graph["nodes"]
                             if n["kind"] == "evidence" and n["source"] == "nexus_evidence"}
                    missing = ev_sha - found
                    links["graph_evidence"] = "matched" if not missing else "incomplete"
                    if missing:
                        warnings.append("graph_run_has_missing_evidence_events")
                else:
                    links["graph_evidence"] = "unlinked"
                    warnings.append("graph_does_not_reference_supplied_run")
            ledger = snapshots.get("provenance")
            if ledger is not None:
                matching = {r["source_event_sha256"] for r in ledger["records"]
                            if r["origin_type"] == "nexus_evidence"
                            and r["origin_id_sha256"] == run_sha
                            and r["source_event_sha256"] is not None}
                if matching:
                    missing = ev_sha - matching
                    links["provenance_evidence"] = "matched" if not missing else "incomplete"
                    if missing:
                        warnings.append("provenance_run_has_missing_evidence_events")
                else:
                    links["provenance_evidence"] = "unlinked"
                    warnings.append("provenance_does_not_reference_supplied_run")
        manifest = snapshots.get("manifest")
        provenance = snapshots.get("provenance")
        if manifest is not None and provenance is not None:
            observed = {r["subject_sha256"] for r in provenance["records"]
                        if r["subject_kind"] == "artifact"}
            links["manifest_provenance_matches"] = sum(
                record["content_sha256"] in observed for record in manifest["records"]
            )
            if manifest["records"] and not links["manifest_provenance_matches"]:
                warnings.append("no_artifact_content_digest_links_in_provenance")
        # This is NOT an integrity failure; sources might represent distinct
        # runs, or one snapshot may have been recorded earlier than another.
        if len(warnings) > self.limits.max_warnings:
            raise NexusReplayError("warning count exceeds configured limit")
        status = "partial" if warnings else "consistent"
        source_counts = {
            "graph_nodes": snapshots["graph"]["node_count"] if "graph" in snapshots else 0,
            "graph_edges": snapshots["graph"]["edge_count"] if "graph" in snapshots else 0,
            "provenance_records": snapshots["provenance"]["record_count"] if "provenance" in snapshots else 0,
            "artifact_records": snapshots["manifest"]["record_count"] if "manifest" in snapshots else 0,
            "evidence_events": len(events),
        }
        report: dict[str, Any] = {
            "schema": REPLAY_SCHEMA, "mission_id": self.mission_id,
            "status": status, "integrity_verified": True,
            "authenticity_verified": False, "effects_replayed": False,
            "source_sha256": source_sha, "source_counts": source_counts,
            "timeline": timelines, "timeline_count": len(timelines),
            "links": links, "warnings": warnings,
        }
        if len(_canonical(report)) > self.limits.max_report_bytes:
            raise NexusReplayError("replay report exceeds configured byte limit")
        report["report_sha256"] = _digest(report)
        return report

    @staticmethod
    def verify_report(report: Mapping[str, Any], *, max_bytes: int = 8_000_000) -> bool:
        """Validate report encoding and internal hash, not original sources."""
        if type(max_bytes) is not int or not 1 <= max_bytes <= 8_000_000:
            raise NexusReplayError("invalid report size limit")
        result = _frozen_snapshot(report, max_bytes)
        if set(result) != _REPORT_KEYS or result["schema"] != REPLAY_SCHEMA:
            raise NexusReplayError("invalid report schema")
        _mission(result["mission_id"])
        if (result["status"] not in ("consistent", "partial")
                or result["integrity_verified"] is not True
                or result["authenticity_verified"] is not False
                or result["effects_replayed"] is not False):
            raise NexusReplayError("invalid replay trust flags")
        for key in _SOURCES:
            value = result["source_sha256"].get(key) if isinstance(result["source_sha256"], dict) else None
            if value is not None:
                _hash(value)
        if not isinstance(result["source_sha256"], dict) or set(result["source_sha256"]) != _SOURCES:
            raise NexusReplayError("invalid source hashes")
        counts = result["source_counts"]
        if (not isinstance(counts, dict) or set(counts) != {
                "graph_nodes", "graph_edges", "provenance_records", "artifact_records", "evidence_events"
            } or any(type(v) is not int or not 0 <= v <= 40000 for v in counts.values())):
            raise NexusReplayError("invalid source counts")
        rows = result["timeline"]
        if not isinstance(rows, list) or type(result["timeline_count"]) is not int or len(rows) != result["timeline_count"] or len(rows) > 30000:
            raise NexusReplayError("invalid timeline count")
        observed: dict[str, int] = {name: 0 for name in _STREAMS}
        seen_sequence: dict[str, set[int]] = {name: set() for name in _STREAMS}
        for row in rows:
            if not isinstance(row, dict) or set(row) != _TIMELINE_KEYS or row["stream"] not in _STREAMS:
                raise NexusReplayError("invalid timeline record")
            if type(row["sequence"]) is not int or row["sequence"] <= 0:
                raise NexusReplayError("invalid timeline sequence")
            _hash(row["ref_sha256"])
            _parse_time(row["at_utc"])
            valid_labels = (
                (row["kind"] in {"plan", "execution", "verification"}
                 and row["status"] in {"proposed", "reported_complete", "failed",
                     "approval_required", "preview", "awaiting_evidence", "verified", "inconclusive"})
                if row["stream"] == "evidence" else
                (row["kind"] in SUBJECT_KINDS and row["status"] in OPERATIONS)
                if row["stream"] == "provenance" else
                (row["kind"] in ARTIFACT_KINDS and row["status"] == "recorded")
            )
            if not valid_labels or row["sequence"] in seen_sequence[row["stream"]]:
                raise NexusReplayError("invalid or duplicate timeline labels")
            seen_sequence[row["stream"]].add(row["sequence"])
            observed[row["stream"]] += 1
        if any(seen_sequence[name] != set(range(1, observed[name] + 1)) for name in _STREAMS):
            raise NexusReplayError("non-contiguous timeline sequence")
        if rows != sorted(rows, key=_sort_key):
            raise NexusReplayError("timeline is not deterministic")
        if (observed["artifact"] != counts["artifact_records"]
                or observed["provenance"] != counts["provenance_records"]
                or observed["evidence"] != counts["evidence_events"]):
            raise NexusReplayError("timeline records disagree with source counts")
        links = result["links"]
        if (not isinstance(links, dict) or set(links) != {
                "graph_evidence", "provenance_evidence", "manifest_provenance_matches"
            } or links["graph_evidence"] not in ("not_applicable", "matched", "unlinked", "incomplete")
            or links["provenance_evidence"] not in ("not_applicable", "matched", "unlinked", "incomplete")
            or type(links["manifest_provenance_matches"]) is not int
            or not 0 <= links["manifest_provenance_matches"] <= counts["artifact_records"]):
            raise NexusReplayError("invalid link results")
        warnings = result["warnings"]
        if not isinstance(warnings, list) or len(warnings) > 512 or any(not isinstance(w, str) or len(w) > 96 for w in warnings):
            raise NexusReplayError("invalid replay warnings")
        if (result["status"] == "consistent") != (not warnings):
            raise NexusReplayError("report status and warnings disagree")
        actual = _hash(result["report_sha256"])
        body = {k: v for k, v in result.items() if k != "report_sha256"}
        if not hmac.compare_digest(actual, _digest(body)):
            raise NexusReplayError("replay report hash mismatch")
        return True


__all__ = ["REPLAY_SCHEMA", "NexusReplay", "NexusReplayError", "ReplayLimits"]
