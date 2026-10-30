"""Bounded read-only visual summaries for Mission Control v2.

Sources are deliberately host-injected. No source means 'not configured',
never fake data. All result fields are from verified source snapshots or
host-owned ApprovalGate status; no file content, commands or approval tokens.

MissionService must authenticate/authorize EACH request before using these
views. These views are NOT a permission check and must not be exposed alone.
Graph integrity and scorecard verification do NOT establish authenticity.
ApprovalGate.status() can expire a pending request as TTL housekeeping, but
never approves, denies, consumes, or executes an action.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from .mission_registry import NexusMissionRegistry
from ..proof.proof_graph import NexusProofGraph
from ..lab.scorecard import NexusScorecard, ScorecardComparison, ScorecardAblation
from ..trust.approval_gate import NexusApprovalGate, ApprovalRequest

MONITOR_VIEW_SCHEMA = "aegisforge.nexus.mission_control.monitor_views.v1"


class NexusMissionMonitorError(ValueError):
    """A supplied source is invalid, stale, or not in mission scope."""


@dataclass(frozen=True)
class MonitorLabSource:
    engine: NexusScorecard
    report: dict[str, Any]
    evaluations: tuple[dict[str, Any], ...]
    comparisons: tuple[ScorecardComparison, ...] = ()
    ablations: tuple[ScorecardAblation, ...] = ()

    def __post_init__(self) -> None:
        if (type(self.engine) is not NexusScorecard or type(self.report) is not dict
                or type(self.evaluations) is not tuple
                or not 1 <= len(self.evaluations) <= 128
                or any(type(e) is not dict for e in self.evaluations)
                or type(self.comparisons) is not tuple or len(self.comparisons) > 64
                or type(self.ablations) is not tuple or len(self.ablations) > 32):
            raise NexusMissionMonitorError("invalid laboratory source")


@dataclass(frozen=True)
class MonitorTrustSource:
    gate: NexusApprovalGate
    requests: tuple[ApprovalRequest, ...]

    def __post_init__(self) -> None:
        if (type(self.gate) is not NexusApprovalGate or type(self.requests) is not tuple
                or len(self.requests) > 24
                or any(type(r) is not ApprovalRequest for r in self.requests)
                or len({r.request_id for r in self.requests}) != len(self.requests)):
            raise NexusMissionMonitorError("invalid Trust source")


class NexusMissionMonitorViews:
    """In-memory sources scoped by explicit mission ID; never enumerate hidden data."""

    def __init__(self, *,
                 graphs: Mapping[str, NexusProofGraph] | None = None,
                 labs: Mapping[str, MonitorLabSource] | None = None,
                 trusts: Mapping[str, MonitorTrustSource] | None = None) -> None:
        for sources, expected in ((graphs, NexusProofGraph),
                                  (labs, MonitorLabSource),
                                  (trusts, MonitorTrustSource)):
            if sources is not None and (type(sources) is not dict or len(sources) > 128
                                        or any(type(k) is not str or type(v) is not expected
                                               for k, v in sources.items())):
                raise NexusMissionMonitorError("invalid or excessive monitor source mapping")
        self._graphs = dict(graphs or {})
        self._labs = dict(labs or {})
        self._trusts = dict(trusts or {})
        for mission_id, graph in self._graphs.items():
            if graph.mission_id != mission_id:
                raise NexusMissionMonitorError("graph mapped to another mission")
        for mission_id, trust in self._trusts.items():
            if any(req.mission_id != mission_id for req in trust.requests):
                raise NexusMissionMonitorError("Trust request mapped to another mission")

    @staticmethod
    def absent(kind: str) -> dict[str, Any]:
        return {"schema": MONITOR_VIEW_SCHEMA, "kind": kind,
                "available": False, "reason": "source_not_configured",
                "integrity_only": True, "authenticity_verified": False}

    def graph(self, mission_id: str) -> dict[str, Any]:
        graph = self._graphs.get(mission_id)
        if graph is None:
            return self.absent("proof_graph")
        try:
            snap = graph.snapshot()
            if snap["mission_id"] != mission_id or not NexusProofGraph.verify_snapshot(snap):
                raise NexusMissionMonitorError("graph mismatch")
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise NexusMissionMonitorError("graph source inconsistent") from exc
        nodes = snap["nodes"]
        edges = snap["edges"]
        if len(nodes) > 64 or len(edges) > 128:
            raise NexusMissionMonitorError("graph exceeds visual budget")
        return {"schema": MONITOR_VIEW_SCHEMA, "kind": "proof_graph", "available": True,
                "mission_id": mission_id, "graph_sha256": snap["graph_sha256"],
                "node_count": snap["node_count"], "edge_count": snap["edge_count"],
                "nodes": [{k: n[k] for k in ("node_id", "kind", "status", "stage", "source")}
                          for n in nodes],
                "edges": [{k: e[k] for k in ("source_node_id", "target_node_id", "relation")}
                          for e in edges],
                "integrity_only": True, "authenticity_verified": False}

    def laboratory(self, mission_id: str) -> dict[str, Any]:
        src = self._labs.get(mission_id)
        if src is None:
            return self.absent("laboratory")
        report = src.report
        try:
            valid = src.engine.verify(report, report["scorecard_id"], src.evaluations,
                                      comparisons=src.comparisons, ablations=src.ablations)
            if not valid:
                raise NexusMissionMonitorError("scorecard mismatch")
            aggregate = report["aggregate"]
            return {"schema": MONITOR_VIEW_SCHEMA, "kind": "laboratory", "available": True,
                    "mission_id": mission_id, "scorecard_sha256": report["scorecard_sha256"],
                    "status": report["status"], "aggregate": {
                        k: aggregate[k] for k in ("evaluations", "passed", "failed", "inconclusive")},
                    "by_product": {k: {m: v[m] for m in ("evaluations", "passed", "failed", "inconclusive")}
                                   for k, v in report["by_product"].items()},
                    "by_category": {k: {m: v[m] for m in ("evaluations", "passed", "failed", "inconclusive")}
                                    for k, v in report["by_category"].items()},
                    "evaluation_reports": report["evaluation_report_count"],
                    "regression_reports": report["regression_report_count"],
                    "ablation_studies": report["ablation_study_count"],
                    "review_flags": report["review_flags"][:20],
                    "verified_against_sources": True,
                    "integrity_only": True, "authenticity_verified": False}
        except (TypeError, ValueError, KeyError, RecursionError, OverflowError) as exc:
            raise NexusMissionMonitorError("laboratory source inconsistent") from exc

    def trust(self, mission_id: str) -> dict[str, Any]:
        src = self._trusts.get(mission_id)
        if src is None:
            return self.absent("trust")
        rows: list[dict[str, Any]] = []
        try:
            for request in src.requests:
                if request.mission_id != mission_id:
                    raise NexusMissionMonitorError("Trust mission mismatch")
                decision = src.gate.status(request.request_id)
                if decision.request_sha256 != request.request_sha256 or decision.code != "status_read":
                    raise NexusMissionMonitorError("Trust request missing or mismatched")
                rows.append({"request_sha256": request.request_sha256,
                             "operation": request.operation,
                             "status": decision.status})
        except (ValueError, TypeError, KeyError) as exc:
            raise NexusMissionMonitorError("Trust source inconsistent") from exc
        summary = {s: sum(r["status"] == s for r in rows)
                   for s in ("pending", "reviewing", "approved", "denied", "expired", "consumed", "verifying")}
        return {"schema": MONITOR_VIEW_SCHEMA, "kind": "trust", "available": True,
                "mission_id": mission_id, "count": len(rows),
                "requests": rows, "counts": summary,
                "status_only": True, "authorization_granted": False,
                "effects_executed": False,
                "integrity_only": True, "authenticity_verified": False}

    @staticmethod
    def timeline(mission_id: str, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not isinstance(events, (tuple, list)) or len(events) > 512:
            raise NexusMissionMonitorError("invalid timeline length")
        timeline = []
        prev = "0" * 64
        for idx, event in enumerate(events, start=1):
            if (not isinstance(event, Mapping) or event.get("mission_id") != mission_id
                    or event.get("sequence") != idx or event.get("previous_sha256") != prev):
                raise NexusMissionMonitorError("invalid timeline chain")
            prev = event["event_sha256"]
            timeline.append({k: event.get(k) for k in
                             ("sequence", "at_utc", "operation", "status", "component", "event_sha256")})
        return {"schema": MONITOR_VIEW_SCHEMA, "kind": "timeline", "available": True,
                "mission_id": mission_id, "count": len(timeline), "events": timeline,
                "integrity_only": True, "authenticity_verified": False}


__all__ = ["MONITOR_VIEW_SCHEMA", "MonitorLabSource", "MonitorTrustSource",
           "NexusMissionMonitorViews", "NexusMissionMonitorError"]
