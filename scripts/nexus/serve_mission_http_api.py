"""Local-only Mission Control HTTP server; no mutations or external calls.

PowerShell:
    $env:AEGISFORGE_MISSION_API_TOKEN = python -c "import secrets;print(secrets.token_urlsafe(48))"
    $env:AEGISFORGE_MISSION_API_ACTOR = "actor.local"
    python scripts/nexus/serve_mission_http_api.py --demo

The --demo flag installs a single synthetic in-memory record, not production
information. Without --demo or --checkpoint-dir the registry starts empty.
"""
from __future__ import annotations

import argparse
import os
from hashlib import sha256
from pathlib import Path

import uvicorn

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_http_api import create_mission_http_app
from aegisforge.nexus.mission_control.mission_monitor_views import (
    NexusMissionMonitorViews, MonitorLabSource, MonitorTrustSource,
)
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.lab.scenario_catalog import NexusScenarioCatalog, ScenarioCase, ScenarioExpectation
from aegisforge.nexus.lab.evaluation_runner import NexusEvaluationRunner, EvaluationObservation
from aegisforge.nexus.lab.scorecard import NexusScorecard
from aegisforge.nexus.trust.approval_gate import NexusApprovalGate, ActionBinding
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from aegisforge.nexus.mission_control.mission_source_archive import NexusMissionSourceArchive
from aegisforge.nexus.mission_control.mission_history_view import NexusMissionHistoryView
from aegisforge.nexus.mission_control.mission_state import (
    MissionTransitionRule, MissionStatePolicy, NexusMissionState,
)
from aegisforge.nexus.mission_control.mission_coordinator import (
    MissionWorkflowStep, MissionCoordinationBlueprint, NexusMissionCoordinator,
)
from aegisforge.nexus.mission_control.mission_read_adapters import NexusMissionReadAdapters


ALLOWED = frozenset({"list", "read", "history", "preview", "inspect"})


def demo_monitor_views() -> NexusMissionMonitorViews:
    """Synthetic, disposable in-memory sources. No models, tools or effects."""
    mid = "mission.demo"
    graph = NexusProofGraph(mid)
    mission_node = graph.add_node("mission", mid, status="observed")
    run_node = graph.add_node("run", "demo.run", status="completed",
                              stage="execution")
    evidence_node = graph.add_node("evidence", "demo.evidence", status="observed",
                                   source="nexus_evidence", stage="verification")
    graph.add_edge("contains", mission_node, run_node)
    graph.add_edge("records", run_node, evidence_node)

    catalog = NexusScenarioCatalog()
    scenarios = (
        ("nexus.planning", "nexus", True),
        ("aegisgrid.cost", "aegisgrid", False),
        ("live_forge.replay", "live_forge", None),
    )
    for sid, product, _ in scenarios:
        catalog.register(ScenarioCase(sid, product=product, category="functional",
                       title="Synthetic demonstration", description="Inert demo data",
                       inputs={"sample": 1},
                       expectations=(ScenarioExpectation("result.ok", "equals", True),)))
    runner = NexusEvaluationRunner(catalog, trusted_sources=("demo_fixture",))
    observations = []
    for sid, _, result in scenarios:
        if result is None:
            continue
        case = catalog.get(sid)
        observations.append(EvaluationObservation(sid, case.case_sha256,
                            source_id="demo_fixture", evidence_ref="synthetic/" + sid,
                            payload={"result": {"ok": result}}, duration_ms=1))
    evaluation = runner.evaluate(runner.prepare(), observations)
    score_engine = NexusScorecard()
    score_report = score_engine.build("colossus.demo", [evaluation])

    # An inert, never-reviewed request, displayed only as a status.
    # The reviewer is never called by the dashboard.
    root = Path.cwd().resolve()
    gate = NexusApprovalGate(root, reviewer=lambda request: None,
                             binding_provider=lambda action: ActionBinding(
                                 sha256(b"demo.payload").hexdigest(),
                                 sha256(b"demo.context").hexdigest()))
    request = gate.request(mid, "write_file", "demo/output.txt",
                           binding=ActionBinding(sha256(b"demo.payload").hexdigest(),
                                                 sha256(b"demo.context").hexdigest()))
    return NexusMissionMonitorViews(
        graphs={mid: graph},
        labs={mid: MonitorLabSource(score_engine, score_report, (evaluation,))},
        trusts={mid: MonitorTrustSource(gate, (request,))},
    )



def main() -> None:
    parser = argparse.ArgumentParser(description="Loopback-only read-only Colossus Mission Control")
    parser.add_argument("--demo", action="store_true", help="one synthetic in-memory mission")
    parser.add_argument("--checkpoint-dir", default=None, help="load existing primary checkpoint (read only)")
    parser.add_argument("--source-archive", action="store_true", help="read existing mission_sources.json from checkpoint dir")
    parser.add_argument("--history", action="store_true", help="pin and expose historical source generations (read only)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.demo and args.checkpoint_dir:
        parser.error("--demo and --checkpoint-dir cannot be combined")
    if args.source_archive and not args.checkpoint_dir:
        parser.error("--source-archive requires --checkpoint-dir")
    if args.history and not args.checkpoint_dir:
        parser.error("--history requires --checkpoint-dir")

    actor = os.environ.get("AEGISFORGE_MISSION_API_ACTOR", "")
    token = os.environ.get("AEGISFORGE_MISSION_API_TOKEN", "")
    if not actor or not token:
        parser.error("set AEGISFORGE_MISSION_API_ACTOR and AEGISFORGE_MISSION_API_TOKEN first")

    checkpoint_monitor = None
    source_archive = None
    history_view = None
    if args.checkpoint_dir:
        store = NexusMissionPersistence(args.checkpoint_dir)
        loaded = store.load()
        registry = loaded.registry
        checkpoint_monitor = NexusMissionCheckpointMonitor(store, loaded)
        if args.source_archive:
            source_archive = NexusMissionSourceArchive(store._root / "mission_sources.json", checkpoint_monitor)
        if args.history:
            history_view = NexusMissionHistoryView(store, checkpoint_monitor)
    else:
        registry = NexusMissionRegistry()
        if args.demo:
            registry.register("mission.demo", product="colossus", owner_id="owner.demo",
                              objective_sha256=sha256(b"synthetic-local-demo").hexdigest(),
                              actor_id="actor.demo")
            # A single allowlisted metadata query. Never a model/tool call.
            # Register the declared component so readiness is coherent.
            registry.assign_component("mission.demo", component="mission_control",
                                      actor_id="actor.demo")

    def authorizer(request_actor: str, action: str, mission_id: str,
                   anchor: str, metadata: dict) -> bool:
        # This deployment is a single authenticated local operator. No
        # privileged mutations, no cross-tenant sharing. The service still
        # evaluates each request independently.
        # Host-only configure permission is restricted to the demo fixture;
        # no HTTP endpoint can invoke configure_workflow.
        return request_actor == actor and (
            action in ALLOWED or
            (args.demo and action == "configure" and mission_id == "mission.demo")
        )

    service = NexusMissionService(registry, authorizer=authorizer)
    if args.demo:
        # Safe, in-memory workflow to exercise Preview and Inspection.
        # The runtime reads registered mission metadata only.
        policy = MissionStatePolicy("mission.demo", "colossus", (
            MissionTransitionRule("ready"),
        ))
        state = NexusMissionState(policy)
        blueprint = MissionCoordinationBlueprint(
            "mission.demo", "colossus", "ready", (
                MissionWorkflowStep("step.registry", "mission_control", "summarize_mission"),
            ),
        )
        coordinator = NexusMissionCoordinator(blueprint, state)
        service.configure_workflow(
            "mission.demo", actor_id=actor, coordinator=coordinator,
            adapters=NexusMissionReadAdapters(registry),
            expected_registry_sha256=service.registry_version(actor_id=actor),
        )
    app = create_mission_http_app(
        service, token_by_actor={actor: token},
        monitor_views=demo_monitor_views() if args.demo else None,
        checkpoint_monitor=checkpoint_monitor,
        source_archive=source_archive,
        history_view=history_view,
    )
    print(f"Colossus Mission Control: http://127.0.0.1:{args.port}/dashboard")
    print("Modo: solo lectura; ninguna operacion concede permisos o ejecuta efectos.")
    uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False,
                access_log=False, server_header=False)


if __name__ == "__main__":
    main()
