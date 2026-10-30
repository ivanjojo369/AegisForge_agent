"""Deterministic, privacy-minimizing evidence relationship graph for Colossus.

Destination: src/aegisforge/nexus/proof/proof_graph.py

The graph links missions, runs, plans, steps, checks, claims, artifacts and
summarized evidence. It never executes tools, calls a model, reads a repository
or writes a file. Its exported snapshot is a content-addressed *record*, not
an attestation that observations are genuine or independently verified.

Integration: ingest_evidence_trail() accepts the EXISTING NexusEvidenceTrail
without replacing evidence.py's SHA-256-linked journal. Only bounded statuses,
counts, identifiers-as-digests and event hashes are exported. No prompts, tool
outputs, source-code diffs, file content, tokens, or arbitrary text metadata.

The caller must separately authenticate evidence sources, reviewer identities,
and external artifact digests. SHA-256 detects accidental changes in the
snapshot but cannot prevent a malicious party from rehashing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
import json
import re
from threading import RLock
from typing import Any, Mapping

PROOF_GRAPH_SCHEMA = "aegisforge.nexus.proof.graph.v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
_MISSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ZERO = "0" * 64

NODE_KINDS = frozenset({
    "mission", "run", "plan", "step", "action", "check", "claim",
    "evidence", "artifact", "decision", "agent", "patch", "test_run",
})
STATUSES = frozenset({
    "unknown", "proposed", "pending", "prepared", "review_required",
    "approved", "denied", "completed", "reported_complete", "failed",
    "inconclusive", "verified", "awaiting_evidence", "approval_required",
    "preview", "blocked", "observed", "passed", "skipped", "consumed",
})
SOURCES = frozenset({"internal", "nexus_evidence", "engineering", "trust", "operator", "external"})
STAGES = frozenset({"none", "plan", "execution", "verification", "test", "review", "artifact"})
METRICS = frozenset({
    "sequence", "steps", "reason", "tool", "verify", "tools_invoked",
    "independent_checks_run", "independent_checks_passed", "findings_pass",
    "findings_fail", "findings_inconclusive", "file_count", "test_count",
})

# Direction matters; declarations do not imply that a claim is true.
RELATIONS = {
    "contains": frozenset({("mission", "run"), ("plan", "step"),
                            ("run", "action"), ("run", "test_run")}),
    "planned": frozenset({("run", "plan")}),
    "records": frozenset({("run", "evidence")}),
    "documents": frozenset({("evidence", "plan"), ("evidence", "step"),
                             ("evidence", "run"), ("evidence", "action"),
                             ("evidence", "check"), ("evidence", "artifact"),
                             ("evidence", "test_run"), ("evidence", "decision")}),
    "evaluates": frozenset({("check", "claim"), ("check", "action"),
                             ("check", "step"), ("check", "artifact"),
                             ("check", "patch"), ("check", "test_run")}),
    "supports": frozenset({("evidence", "claim")}),
    "contradicts": frozenset({("evidence", "claim")}),
    "derives_from": frozenset({("artifact", "artifact"), ("evidence", "evidence"),
                                ("patch", "artifact"), ("patch", "patch")}),
    "depends_on": frozenset({("step", "step"), ("action", "action")}),
    "authorizes": frozenset({("decision", "action")}),
    "performed_by": frozenset({("action", "agent"), ("check", "agent")}),
    "proposes": frozenset({("agent", "plan"), ("agent", "patch")}),
}


class NexusProofGraphError(ValueError):
    """Invalid graph content, evidence event chain, scope or graph limit."""


def _canonical(value: Any) -> bytes:
    """One canonical representation for graph hashes (not a signature)."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusProofGraphError("data must be bounded JSON") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _sha(value: str, name: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise NexusProofGraphError(f"invalid {name} SHA-256")
    return value


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise NexusProofGraphError("invalid opaque identifier; do not supply raw task text")
    return value


def _mission(value: str) -> str:
    if not isinstance(value, str) or not _MISSION.fullmatch(value):
        raise NexusProofGraphError("invalid mission identifier")
    return value


def _metrics(values: Mapping[str, int] | None) -> dict[str, int]:
    if values is None:
        return {}
    if not isinstance(values, Mapping) or len(values) > 12:
        raise NexusProofGraphError("metrics must be a bounded mapping")
    result = {}
    for key, value in values.items():
        if key not in METRICS or type(value) is not int or not 0 <= value <= 1_000_000:
            raise NexusProofGraphError("metrics must contain only approved counters")
        result[key] = value
    return dict(sorted(result.items()))


def _node_id(mission_id: str, kind: str, key_sha256: str) -> str:
    return _digest({"mission_id": mission_id, "kind": kind, "key_sha256": key_sha256})


def _edge_id(relation: str, source: str, target: str) -> str:
    return _digest({"relation": relation, "source": source, "target": target})


@dataclass(frozen=True, slots=True)
class ProofGraphLimits:
    max_nodes: int = 1024
    max_edges: int = 4096
    max_snapshot_bytes: int = 1_500_000

    def __post_init__(self) -> None:
        for name, upper in (("max_nodes", 10000), ("max_edges", 40000),
                            ("max_snapshot_bytes", 8_000_000)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= upper:
                raise NexusProofGraphError(f"invalid {name}")


@dataclass(frozen=True, slots=True)
class ProofNode:
    node_id: str
    kind: str
    key_sha256: str
    status: str
    source: str
    stage: str
    digest_sha256: str | None
    metrics: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id, "kind": self.kind,
            "key_sha256": self.key_sha256, "status": self.status,
            "source": self.source, "stage": self.stage,
            "digest_sha256": self.digest_sha256, "metrics": dict(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class ProofEdge:
    edge_id: str
    relation: str
    source_node_id: str
    target_node_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "edge_id": self.edge_id, "relation": self.relation,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
        }


class NexusProofGraph:
    """Host-managed, in-memory graph. Fail closed on conflicting facts.

    Nodes are addressed by opaque IDs derived from the mission and a *hashed*
    identifier. The raw identifier never appears in exported snapshots.
    No inferred relation can prove truth; evidence-support edges are assertions
    supplied by a trusted caller, not automatically inferred from success status.
    """

    def __init__(self, mission_id: str, *, limits: ProofGraphLimits | None = None) -> None:
        self.mission_id = _mission(mission_id)
        self.limits = limits or ProofGraphLimits()
        if not isinstance(self.limits, ProofGraphLimits):
            raise NexusProofGraphError("invalid graph limits")
        self._nodes: dict[str, ProofNode] = {}
        self._edges: dict[str, ProofEdge] = {}
        self._runs: set[str] = set()
        self._lock = RLock()
        self.add_node("mission", self.mission_id, status="observed")

    def add_node(
        self, kind: str, identifier: str, *, status: str = "unknown",
        source: str = "internal", stage: str = "none",
        digest_sha256: str | None = None,
        metrics: Mapping[str, int] | None = None,
    ) -> str:
        if kind not in NODE_KINDS or status not in STATUSES:
            raise NexusProofGraphError("invalid node kind or status")
        if source not in SOURCES or stage not in STAGES:
            raise NexusProofGraphError("invalid node source or stage")
        key_sha256 = sha256(_identifier(identifier).encode("utf-8")).hexdigest()
        if digest_sha256 is not None:
            _sha(digest_sha256, "node content")
        counts = _metrics(metrics)
        node_id = _node_id(self.mission_id, kind, key_sha256)
        node = ProofNode(node_id, kind, key_sha256, status, source, stage,
                         digest_sha256, tuple(counts.items()))
        with self._lock:
            prior = self._nodes.get(node_id)
            if prior is not None:
                if prior != node:
                    raise NexusProofGraphError("conflicting node already exists")
                return node_id
            if len(self._nodes) >= self.limits.max_nodes:
                raise NexusProofGraphError("graph node limit reached")
            self._nodes[node_id] = node
        return node_id

    def add_edge(self, relation: str, source_node_id: str, target_node_id: str) -> str:
        with self._lock:
            source = self._nodes.get(source_node_id)
            target = self._nodes.get(target_node_id)
            if source is None or target is None:
                raise NexusProofGraphError("edge endpoints must exist")
            if source.node_id == target.node_id:
                raise NexusProofGraphError("self-referential edges forbidden")
            if (source.kind, target.kind) not in RELATIONS.get(relation, frozenset()):
                raise NexusProofGraphError("relation not allowed for these node kinds")
            edge_id = _edge_id(relation, source_node_id, target_node_id)
            if edge_id in self._edges:
                return edge_id
            if len(self._edges) >= self.limits.max_edges:
                raise NexusProofGraphError("graph edge limit reached")
            self._edges[edge_id] = ProofEdge(edge_id, relation, source_node_id, target_node_id)
            return edge_id

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self._lock:
            node = self._nodes.get(node_id)
            return node.to_dict() if node else None

    def neighbors(self, node_id: str, *, relation: str | None = None,
                  direction: str = "both", limit: int = 100) -> tuple[dict[str, str], ...]:
        if direction not in ("in", "out", "both") or type(limit) is not int or not 1 <= limit <= 500:
            raise NexusProofGraphError("invalid neighborhood query")
        if relation is not None and relation not in RELATIONS:
            raise NexusProofGraphError("unknown relation")
        with self._lock:
            if node_id not in self._nodes:
                raise NexusProofGraphError("unknown node")
            result = [edge.to_dict() for edge in self._edges.values()
                      if (relation is None or edge.relation == relation)
                      and ((direction in ("out", "both") and edge.source_node_id == node_id)
                           or (direction in ("in", "both") and edge.target_node_id == node_id))]
            return tuple(sorted(result, key=lambda row: row["edge_id"])[:limit])

    def ingest_evidence_trail(self, trail: Any) -> dict[str, Any]:
        """Link existing NexusEvidenceTrail; validate its digest-linked events.

        Transactional on failure. No synthetic checks or claims are created:
        'verified' is a *reported verifier verdict*, not authenticity proof.
        """
        # Lazy import avoids coupling normal graph operations to the planner.
        from ..evidence import EvidenceEvent, NexusEvidenceTrail, SCHEMA
        if not isinstance(trail, NexusEvidenceTrail):
            raise NexusProofGraphError("expected NexusEvidenceTrail")
        events = trail.events
        if not isinstance(events, tuple) or not 1 <= len(events) <= 3:
            raise NexusProofGraphError("expected 1-3 existing evidence events")
        run_id = trail.run_id
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise NexusProofGraphError("invalid evidence run ID")
        previous = _ZERO
        plan_hash = None
        stages = ("plan", "execution", "verification")
        expected_states = (
            {"proposed"},
            {"reported_complete", "failed", "approval_required", "preview", "awaiting_evidence"},
            {"verified", "failed", "inconclusive"},
        )
        safe_rows: list[dict[str, Any]] = []
        for index, event in enumerate(events):
            if type(event) is not EvidenceEvent:
                raise NexusProofGraphError("unrecognized evidence event")
            row = event.to_dict()
            if (row.get("schema") != SCHEMA or row.get("run_id") != run_id
                    or type(row.get("sequence")) is not int or row["sequence"] != index + 1
                    or row.get("stage") != stages[index]
                    or row.get("status") not in expected_states[index]
                    or row.get("previous_sha256") != previous):
                raise NexusProofGraphError("evidence event order or scope invalid")
            _sha(row.get("plan_sha256"), "plan")
            _sha(row.get("sha256"), "evidence event")
            if plan_hash is not None and row["plan_sha256"] != plan_hash:
                raise NexusProofGraphError("evidence events use inconsistent plans")
            if len(_canonical(row)) > 16_384:
                raise NexusProofGraphError("evidence event exceeds size limit")
            at_utc = row.get("at_utc")
            if not isinstance(at_utc, str) or not at_utc.endswith("Z") or len(at_utc) > 40:
                raise NexusProofGraphError("invalid evidence timestamp")
            try:
                if datetime.fromisoformat(at_utc.replace("Z", "+00:00")).utcoffset() != timedelta(0):
                    raise ValueError("not UTC")
            except ValueError as exc:
                raise NexusProofGraphError("invalid evidence timestamp") from exc
            if not hmac.compare_digest(row["sha256"], _digest({
                k: v for k, v in row.items() if k != "sha256"})):
                raise NexusProofGraphError("evidence event digest mismatch")
            summary = row.get("summary")
            if not isinstance(summary, dict):
                raise NexusProofGraphError("invalid evidence summary")
            if index == 0:
                if (set(summary) != {"steps", "reason", "tool", "verify"}
                        or any(type(x) is not int or not 0 <= x <= 12 for x in summary.values())
                        or not 2 <= summary["steps"] <= 12
                        or summary["steps"] != sum(summary[k] for k in ("reason", "tool", "verify"))
                        or summary["verify"] < 1):
                    raise NexusProofGraphError("invalid plan summary")
            elif index == 1:
                rows = summary.get("steps")
                invoked = summary.get("tools_invoked")
                if (set(summary) != {"steps", "tools_invoked"}
                        or not isinstance(rows, list) or len(rows) != safe_rows[0]["summary"]["steps"]
                        or type(invoked) is not int or not 0 <= invoked <= len(rows)):
                    raise NexusProofGraphError("invalid execution summary")
                permitted = {"completed", "tool_failed", "tool_error", "preview_only",
                             "approval_required", "blocked_dependency", "attested_by_operator", "awaiting_evidence"}
                for i, step in enumerate(rows, 1):
                    if (not isinstance(step, dict) or set(step) != {"id", "status"}
                            or step["id"] != f"s{i}" or step["status"] not in permitted):
                        raise NexusProofGraphError("invalid execution step")
                count = sum(step["status"] in {"completed", "tool_failed", "tool_error"} for step in rows)
                if count != invoked:
                    raise NexusProofGraphError("invalid tool invocation count")
            elif index == 2:
                findings = summary.get("findings")
                if (set(summary) != {"independent_checks_run", "independent_checks_passed", "findings"}
                        or type(summary["independent_checks_run"]) is not int
                        or type(summary["independent_checks_passed"]) is not int
                        or not 0 <= summary["independent_checks_passed"] <= summary["independent_checks_run"] <= 1000
                        or not isinstance(findings, dict) or set(findings) != {"pass", "fail", "inconclusive"}
                        or any(type(x) is not int or not 0 <= x <= 1000 for x in findings.values())):
                    raise NexusProofGraphError("invalid verification summary")
                if (row["status"] == "verified"
                        and (safe_rows[1]["status"] != "reported_complete"
                             or summary["independent_checks_run"] == 0
                             or summary["independent_checks_passed"] != summary["independent_checks_run"]
                             or findings["fail"] > 0)):
                    raise NexusProofGraphError("unjustified verification verdict")
            safe_rows.append(row)
            previous = row["sha256"]
            plan_hash = row["plan_sha256"]

        with self._lock:
            if run_id in self._runs:
                raise NexusProofGraphError("evidence run already ingested")
            old_nodes = self._nodes.copy()
            old_edges = self._edges.copy()
            old_runs = self._runs.copy()
            try:
                mission = self.add_node("mission", self.mission_id, status="observed")
                run = self.add_node("run", run_id, status="observed",
                                    source="nexus_evidence")
                plan = self.add_node("plan", plan_hash, status="proposed",
                                     source="nexus_evidence", stage="plan",
                                     digest_sha256=plan_hash,
                                     metrics=safe_rows[0]["summary"])
                self.add_edge("contains", mission, run)
                self.add_edge("planned", run, plan)
                for i in range(1, safe_rows[0]["summary"]["steps"] + 1):
                    step = self.add_node("step", f"{run_id}:s{i}", status="proposed",
                                         source="nexus_evidence", stage="plan")
                    self.add_edge("contains", plan, step)
                for row in safe_rows:
                    stage = row["stage"]
                    metrics = {}
                    if stage == "execution":
                        metrics = {"tools_invoked": row["summary"]["tools_invoked"]}
                    elif stage == "verification":
                        s = row["summary"]
                        metrics = {"independent_checks_run": s["independent_checks_run"],
                                   "independent_checks_passed": s["independent_checks_passed"],
                                   "findings_pass": s["findings"]["pass"],
                                   "findings_fail": s["findings"]["fail"],
                                   "findings_inconclusive": s["findings"]["inconclusive"]}
                    evidence = self.add_node("evidence", row["sha256"],
                                             status=row["status"], source="nexus_evidence",
                                             stage=stage, digest_sha256=row["sha256"],
                                             metrics={"sequence": row["sequence"], **metrics})
                    self.add_edge("records", run, evidence)
                    self.add_edge("documents", evidence, plan if stage == "plan" else run)
                    if stage == "execution":
                        for step_record in row["summary"]["steps"]:
                            step_id = self._get_node_id("step", f"{run_id}:{step_record['id']}")
                            self.add_edge("documents", evidence, step_id)
                self._runs.add(run_id)
                result = {"run_sha256": sha256(run_id.encode("ascii")).hexdigest(),
                          "evidence_events": len(safe_rows),
                          "evidence_terminal_sha256": safe_rows[-1]["sha256"],
                          "graph_sha256": self.snapshot()["graph_sha256"]}
            except Exception:
                self._nodes = old_nodes
                self._edges = old_edges
                self._runs = old_runs
                raise
            return result

    def _get_node_id(self, kind: str, identifier: str) -> str:
        return _node_id(self.mission_id, kind,
                        sha256(_identifier(identifier).encode("utf-8")).hexdigest())

    def snapshot(self) -> dict[str, Any]:
        """Deterministic, bounded export: digests, statuses and counters ONLY."""
        with self._lock:
            data: dict[str, Any] = {
                "schema": PROOF_GRAPH_SCHEMA,
                "mission_id": self.mission_id,
                "node_count": len(self._nodes), "edge_count": len(self._edges),
                "nodes": [node.to_dict() for node in sorted(self._nodes.values(), key=lambda x: x.node_id)],
                "edges": [edge.to_dict() for edge in sorted(self._edges.values(), key=lambda x: x.edge_id)],
                "integrity_only": True,
                "authenticity_verified": False,
            }
            encoded = _canonical(data)
            if len(encoded) > self.limits.max_snapshot_bytes:
                raise NexusProofGraphError("snapshot byte budget exceeded")
            data["graph_sha256"] = sha256(encoded).hexdigest()
            return data

    @staticmethod
    def verify_snapshot(snapshot: Mapping[str, Any], *, max_bytes: int = 8_000_000) -> bool:
        """Validate a snapshot's internal integrity, NOT evidence authenticity."""
        if not isinstance(snapshot, Mapping) or type(max_bytes) is not int or not 1 <= max_bytes <= 8_000_000:
            raise NexusProofGraphError("invalid snapshot or limit")
        expected = {"schema", "mission_id", "node_count", "edge_count", "nodes", "edges",
                    "integrity_only", "authenticity_verified", "graph_sha256"}
        if set(snapshot) != expected or snapshot["schema"] != PROOF_GRAPH_SCHEMA:
            raise NexusProofGraphError("invalid snapshot schema")
        mission = _mission(snapshot["mission_id"])
        saved_hash = _sha(snapshot["graph_sha256"], "graph")
        body = {k: v for k, v in snapshot.items() if k != "graph_sha256"}
        encoded = _canonical(body)
        if len(encoded) > max_bytes or not hmac.compare_digest(saved_hash, sha256(encoded).hexdigest()):
            raise NexusProofGraphError("snapshot hash mismatch or too large")
        if snapshot["integrity_only"] is not True or snapshot["authenticity_verified"] is not False:
            raise NexusProofGraphError("misleading snapshot trust flags")
        nodes = snapshot["nodes"]
        edges = snapshot["edges"]
        if (not isinstance(nodes, list) or not isinstance(edges, list)
                or type(snapshot["node_count"]) is not int or type(snapshot["edge_count"]) is not int
                or len(nodes) != snapshot["node_count"] or len(edges) != snapshot["edge_count"]
                or len(nodes) > 10000 or len(edges) > 40000):
            raise NexusProofGraphError("invalid snapshot counts")
        by_id = {}
        node_fields = {"node_id", "kind", "key_sha256", "status", "source", "stage", "digest_sha256", "metrics"}
        for row in nodes:
            if not isinstance(row, dict) or set(row) != node_fields:
                raise NexusProofGraphError("invalid snapshot node")
            kind = row["kind"]
            if kind not in NODE_KINDS or row["status"] not in STATUSES or row["source"] not in SOURCES or row["stage"] not in STAGES:
                raise NexusProofGraphError("invalid snapshot node labels")
            key_sha = _sha(row["key_sha256"], "identifier")
            if row["digest_sha256"] is not None:
                _sha(row["digest_sha256"], "content")
            _metrics(row["metrics"])
            if _node_id(mission, kind, key_sha) != row["node_id"] or row["node_id"] in by_id:
                raise NexusProofGraphError("invalid or duplicate node ID")
            by_id[row["node_id"]] = kind
        edge_fields = {"edge_id", "relation", "source_node_id", "target_node_id"}
        edge_ids: set[str] = set()
        for row in edges:
            if not isinstance(row, dict) or set(row) != edge_fields:
                raise NexusProofGraphError("invalid snapshot edge")
            relation = row["relation"]
            src, dst = row["source_node_id"], row["target_node_id"]
            if (src == dst or src not in by_id or dst not in by_id
                    or (by_id[src], by_id[dst]) not in RELATIONS.get(relation, frozenset())
                    or _edge_id(relation, src, dst) != row["edge_id"] or row["edge_id"] in edge_ids):
                raise NexusProofGraphError("invalid or duplicate graph edge")
            edge_ids.add(row["edge_id"])
        return True


__all__ = [
    "NexusProofGraph", "NexusProofGraphError", "ProofGraphLimits", "ProofNode",
    "ProofEdge", "PROOF_GRAPH_SCHEMA", "NODE_KINDS", "RELATIONS",
]
