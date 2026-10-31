"""Host-owned integration of synthetic AegisGrid runs with existing Colossus contracts.

Archive capture is bound to an exact checkpoint and contains display-only
summaries. Older archives are retired to the existing append-only history
before advancing the checkpoint. None of this authorizes physical effects.
"""
from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from aegisforge.nexus.lab.scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation,
)
from aegisforge.nexus.lab.evaluation_runner import (
    EvaluationObservation, NexusEvaluationRunner,
)
from aegisforge.nexus.lab.scorecard import NexusScorecard
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_monitor_views import (
    MonitorLabSource, NexusMissionMonitorViews,
)
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from aegisforge.nexus.mission_control.mission_source_archive import NexusMissionSourceArchive
from aegisforge.nexus.mission_control.mission_source_capture import NexusMissionSourceCapture
from aegisforge.nexus.mission_control.mission_archive_history import NexusMissionArchiveHistory

from .simulator import simulate
from .validation import verify_trace
from .multiday import simulate_multiday, SCHEMA as MULTIDAY_SCHEMA
from .multiday_validation import verify_multiday

_ACTOR = "operator.local"
_SCENARIO_ID = "aegisgrid.energy_accounting"
_ARCHIVE_NAME = "mission_sources.json"


def _build_sources(result: dict[str, Any], mission_id: str) -> dict[str, Any]:
    """Reconstruct verifiable in-memory lab and graph sources for one exact run."""
    replay = verify_multiday(result) if result.get("schema") == MULTIDAY_SCHEMA else verify_trace(result)
    internal = result.get("verification", {}).get("passed") is True
    case = ScenarioCase(
        _SCENARIO_ID, product="aegisgrid", category="functional", mode="simulated",
        title="Hypothetical community energy accounting",
        description="Check disclosed synthetic trace conservation, not physical deployment",
        inputs=result["scenario"], tags=("energy", "synthetic"),
        expectations=(
            ScenarioExpectation("replay.passed", "equals", True),
            ScenarioExpectation("internal.passed", "equals", True),
        ),
    )
    catalog = NexusScenarioCatalog()
    catalog.register(case)
    lab = NexusEvaluationRunner(catalog, trusted_sources=("aegisgrid_local_replay",))
    observation = EvaluationObservation(
        case.scenario_id, case.case_sha256,
        source_id="aegisgrid_local_replay",
        evidence_ref="local/" + result["result_sha256"][:24],
        payload={"replay": {"passed": replay["passed"]},
                 "internal": {"passed": internal}},
    )
    evaluation = lab.evaluate(lab.prepare(), [observation])
    if not NexusEvaluationRunner.verify_report(evaluation):
        raise ValueError("evaluation failed consistency verification")
    scorer = NexusScorecard()
    score = scorer.build("aegisgrid.energy.local", [evaluation])
    if not scorer.verify(score, "aegisgrid.energy.local", [evaluation]):
        raise ValueError("scorecard source verification failed")

    graph = NexusProofGraph(mission_id)
    mission_node = graph.add_node("mission", mission_id, status="observed")
    run = graph.add_node("run", mission_id + ".run", status="reported_complete",
                         source="internal", stage="execution",
                         digest_sha256=result["result_sha256"])
    evidence = graph.add_node("evidence", mission_id + ".lab", status="observed",
                              source="internal", stage="verification",
                              digest_sha256=score["scorecard_sha256"])
    claim = graph.add_node("claim", mission_id + ".balance", status="proposed",
                           source="internal", stage="verification",
                           digest_sha256=result["scenario_sha256"])
    check = graph.add_node("check", mission_id + ".replay",
                           status="passed" if replay["passed"] and internal else "failed",
                           source="internal", stage="verification",
                           digest_sha256=evaluation["report_sha256"],
                           metrics={"independent_checks_run": len(replay["checks"]),
                                    "independent_checks_passed": sum(replay["checks"].values())})
    graph.add_edge("contains", mission_node, run)
    graph.add_edge("records", run, evidence)
    graph.add_edge("documents", evidence, check)
    graph.add_edge("evaluates", check, claim)
    proof = graph.snapshot()
    if not NexusProofGraph.verify_snapshot(proof):
        raise ValueError("proof graph verification failed")
    views = NexusMissionMonitorViews(
        graphs={mission_id: graph},
        labs={mission_id: MonitorLabSource(scorer, score, (evaluation,))},
    )
    return {
        "replay": replay, "evaluation": evaluation, "score": score,
        "proof": proof, "views": views,
        "graph_view": views.graph(mission_id), "lab_view": views.laboratory(mission_id),
    }


class AegisGridIntegration:
    """Reuse Colossus registry, lab, graph, source capture and archive history."""

    def __init__(self, *, checkpoint_dir: str | None = None) -> None:
        self._lock = RLock()
        self._store: NexusMissionPersistence | None = None
        self._head: str | None = None
        if checkpoint_dir is None:
            self._registry = NexusMissionRegistry()
        else:
            folder = Path(checkpoint_dir)
            if not folder.is_dir() or folder.is_symlink():
                raise ValueError("checkpoint directory must exist and must not be a link")
            store = NexusMissionPersistence(folder)
            if (folder / "mission_registry.json").exists():
                loaded = store.load()
                self._registry = loaded.registry
                self._head = loaded.receipt.envelope_sha256
            else:
                self._registry = NexusMissionRegistry()
            self._store = store

    @property
    def persistent(self) -> bool:
        return self._store is not None

    def _capture(self, mission_id: str, views: NexusMissionMonitorViews) -> dict[str, Any]:
        """Commit a one-time host-authorized archive for the pinned checkpoint."""
        if self._store is None:
            raise ValueError("source archive requires a persistent checkpoint")
        loaded = self._store.load()
        if loaded.receipt.envelope_sha256 != self._head:
            raise ValueError("checkpoint changed outside AegisGrid; restart to reconcile")
        monitor = NexusMissionCheckpointMonitor(self._store, loaded)
        status = monitor.status()

        # Explicit policy is bound to this local owner, exact mission, generation,
        # head digest and known two summaries. No model chooses archive effects.
        def capture_policy(actor: str, action: str, ids: tuple[str, ...], ctx: dict) -> bool:
            return (actor == _ACTOR and action == "mission_sources.capture_closed_missions"
                    and ids == (mission_id,)
                    and ctx.get("generation") == status["generation"]
                    and ctx.get("envelope_sha256") == status["envelope_sha256"]
                    and ctx.get("registry_sha256") == status["registry_sha256"]
                    and ctx.get("source_counts") == {"graph": 1, "laboratory": 1, "trust": 0})

        capture = NexusMissionSourceCapture(self._store, views, authorizer=capture_policy)
        plan = capture.prepare((mission_id,), actor_id=_ACTOR)
        report = capture.commit(plan, actor_id=_ACTOR)
        archive = NexusMissionSourceArchive(self._store._root / _ARCHIVE_NAME, monitor)
        if not NexusMissionSourceCapture.verify_capture_report(report, monitor, archive):
            raise ValueError("archive verification failed; inspect source file before retrying")
        return {"archive_sha256": report["archive_sha256"],
                "generation": report["checkpoint_generation"],
                "sources_archived": True, "historical_only": True}

    def _retire_active_for_next(self, mission_id: str, status: str) -> None:
        """Protect the current archive before a new checkpoint invalidates it."""
        if self._store is None:
            return
        root = self._store._root
        if not (root / _ARCHIVE_NAME).exists():
            return

        def retire_policy(actor: str, action: str, ctx: dict) -> bool:
            return (actor == _ACTOR and action == "mission_history.retire_current"
                    and ctx.get("next_mission_id") == mission_id
                    and ctx.get("next_status") == status
                    and ctx.get("retire_active") is True
                    and ctx.get("checkpoint_envelope_sha256") == self._head
                    and type(ctx.get("mission_ids")) is list
                    and bool(ctx["mission_ids"]))

        history = NexusMissionArchiveHistory(self._store, authorizer=retire_policy)
        history.retire_current(actor_id=_ACTOR, next_mission_id=mission_id,
                               next_status=status)

    def archive_existing(self, exported_result: dict[str, Any]) -> dict[str, Any]:
        """Backfill the original checkpoint using an exact, recomputed export.

        This DOES NOT add a mission, change the checkpoint or rerun any physical
        operation. Refuses data that do not match the existing mission's digests.
        Operator must call explicitly with the original evidence JSON.
        """
        if self._store is None or type(exported_result) is not dict:
            raise ValueError("existing archive requires operator-provided evidence and checkpoint")
        evidence = exported_result.get("colossus")
        if type(evidence) is not dict or type(evidence.get("mission")) is not dict:
            raise ValueError("export lacks the bound Colossus mission")
        mission_id = evidence["mission"].get("mission_id")
        with self._lock:
            actual = self._registry.get(mission_id)
            if actual["product"] != "aegisgrid" or actual["status"] not in ("reported_complete", "failed"):
                raise ValueError("mission not a closed AegisGrid run")
            if (actual["head_sha256"] != evidence["mission"].get("head_sha256")
                    or actual["objective_sha256"] != exported_result.get("scenario_sha256")):
                raise ValueError("exported mission does not match pinned registry")
            if (self._store._root / _ARCHIVE_NAME).exists():
                raise ValueError("active source archive already exists; no overwrite permitted")
            # Recompute the entire synthetic execution and derive evidence anew;
            # do not trust the exported summary or self-declared verification.
            recomputed = (simulate_multiday(exported_result["scenario"])
                          if exported_result.get("schema") == MULTIDAY_SCHEMA
                          else simulate(exported_result["scenario"]))
            if {k: v for k, v in exported_result.items() if k != "colossus"} != recomputed:
                raise ValueError("exported result differs from deterministic simulation")
            built = _build_sources(recomputed, mission_id)
            expected = {
                ("artifact", recomputed["result_sha256"]),
                ("evaluation", built["evaluation"]["report_sha256"]),
                ("scorecard", built["score"]["scorecard_sha256"]),
                ("provenance", built["proof"]["graph_sha256"]),
            }
            recorded = {(r["kind"], r["sha256"]) for r in actual["references"]}
            if recorded != expected:
                raise ValueError("recomputed evidence does not match existing mission references")
            return {"mission_id": mission_id, **self._capture(mission_id, built["views"])}

    def record(self, result: dict[str, Any]) -> dict[str, Any]:
        """Register hypothetical energy accounting, then archive display sources."""
        with self._lock:
            mission_id = "grid." + uuid4().hex
            built = _build_sources(result, mission_id)
            evaluation, score, proof = built["evaluation"], built["score"], built["proof"]
            outcome = "reported_complete" if evaluation["status"] == "passed" else "failed"
            registry = self._registry
            registry.register(mission_id, product="aegisgrid", owner_id=_ACTOR,
                              actor_id=_ACTOR, objective_sha256=result["scenario_sha256"])
            for component in ("aegisgrid", "lab", "proof", "mission_control"):
                registry.assign_component(mission_id, component=component, actor_id=_ACTOR)
            for kind, digest in (
                ("artifact", result["result_sha256"]),
                ("evaluation", evaluation["report_sha256"]),
                ("scorecard", score["scorecard_sha256"]),
                ("provenance", proof["graph_sha256"]),
            ):
                registry.link_reference(mission_id, kind=kind, reference_sha256=digest,
                                        actor_id=_ACTOR)
            registry.transition(mission_id, "ready", actor_id=_ACTOR)
            registry.transition(mission_id, "running", actor_id=_ACTOR)
            registry.transition(mission_id, outcome, actor_id=_ACTOR)
            receipt = None
            capture = None
            if self._store is not None:
                self._retire_active_for_next(mission_id, outcome)
                receipt = self._store.save_snapshot(registry.snapshot(),
                                                    expected_head_sha256=self._head)
                self._head = receipt.envelope_sha256
                capture = self._capture(mission_id, built["views"])
            return {
                "schema": "aegisforge.aegisgrid.colossus_integration.v1",
                "mission": registry.get(mission_id),
                "laboratory": {
                    "status": evaluation["status"], "assurance": evaluation["assurance"],
                    "scorecard_sha256": score["scorecard_sha256"],
                    "view": built["lab_view"], "replay": built["replay"],
                },
                "proof_graph": built["graph_view"],
                "storage": {
                    "kind": "checkpoint" if receipt is not None else "volatile",
                    "generation": receipt.generation if receipt is not None else None,
                    "registry_sha256": (receipt.registry_sha256 if receipt is not None
                                        else registry.snapshot()["registry_sha256"]),
                    "external_mission_dashboard_live": False,
                    "external_monitor_sources_archived": capture is not None,
                    "archive_sha256": capture["archive_sha256"] if capture else None,
                },
                "assurance": "synthetic_accounting_replayed_not_externally_validated",
            }
