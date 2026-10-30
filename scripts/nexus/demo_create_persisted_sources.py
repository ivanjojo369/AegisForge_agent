"""Offline, disposable demo: four Mission Registry checkpoints + archived views.

Contains synthetic data only. Does not load real projects, touch existing
checkpoints, use a model, restore permissions or execute tools.
"""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from aegisforge.nexus.mission_control.mission_monitor_views import (
    NexusMissionMonitorViews, MonitorLabSource, MonitorTrustSource,
)
from aegisforge.nexus.mission_control.mission_source_archive import NexusMissionSourceArchive
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.lab.scenario_catalog import NexusScenarioCatalog, ScenarioCase, ScenarioExpectation
from aegisforge.nexus.lab.evaluation_runner import NexusEvaluationRunner, EvaluationObservation
from aegisforge.nexus.lab.scorecard import NexusScorecard
from aegisforge.nexus.trust.approval_gate import NexusApprovalGate, ActionBinding


def create_demo() -> dict:
    root = Path(tempfile.mkdtemp(prefix="colossus_archived_sources_demo_"))
    registry = NexusMissionRegistry()
    products = (("mission.nexus.demo", "nexus"), ("mission.grid.demo", "aegisgrid"),
                ("mission.forge.demo", "live_forge"),
                ("mission.colossus.demo", "colossus"))
    graphs = {}
    labs = {}
    for mid, product in products:
        registry.register(mid, product=product, owner_id="owner.synthetic",
                          objective_sha256=sha256(mid.encode()).hexdigest(),
                          actor_id="actor.synthetic")
        g = NexusProofGraph(mid)
        mission_node = g.add_node("mission", mid, status="observed")
        run = g.add_node("run", "sample.run", status="completed", stage="execution")
        g.add_edge("contains", mission_node, run)
        graphs[mid] = g
        catalog = NexusScenarioCatalog()
        scenario = "sample." + product
        catalog.register(ScenarioCase(scenario, product=product,
             category="functional", title="Synthetic observation", description="No external model",
             inputs={"fixture": True},
             expectations=(ScenarioExpectation("check.ok", "equals", True),)))
        case = catalog.get(scenario)
        runner = NexusEvaluationRunner(catalog, trusted_sources=("demo_fixture",))
        observed = EvaluationObservation(scenario, case.case_sha256,
                          source_id="demo_fixture", evidence_ref="synthetic/"+product,
                          payload={"check": {"ok": True}}, duration_ms=1)
        evaluation = runner.evaluate(runner.prepare(), (observed,))
        engine = NexusScorecard()
        report = engine.build(mid, (evaluation,))
        labs[mid] = MonitorLabSource(engine, report, (evaluation,))
    payload = sha256(b"synthetic.payload").hexdigest()
    context = sha256(b"synthetic.context").hexdigest()
    gate = NexusApprovalGate(root, reviewer=lambda _: None,
                             binding_provider=lambda _: ActionBinding(payload, context))
    req = gate.request("mission.nexus.demo", "write_file", "synthetic/dummy.txt",
                       binding=ActionBinding(payload, context))
    views = NexusMissionMonitorViews(graphs=graphs, labs=labs,
                    trusts={"mission.nexus.demo": MonitorTrustSource(gate, (req,))})
    store = NexusMissionPersistence(root)
    receipt = store.save(registry)
    loaded = store.load()
    monitor = NexusMissionCheckpointMonitor(store, loaded)
    archive = NexusMissionSourceArchive.write_new(root, monitor, views,
                                                   (mid for mid, _ in products))
    loaded_archive = NexusMissionSourceArchive(archive, monitor)
    return {"checkpoint_dir": str(root), "source_archive": archive.name,
            "generation": receipt.generation, "missions": len(products),
            "archived_graphs": len(graphs), "archived_labs": len(labs),
            "archived_trust_snapshots": 1, "archive_verified": loaded_archive.summary("graph", "mission.nexus.demo") is not None,
            "permissions_restored": False, "effects_executed": False,
            "synthetic": True}


if __name__ == "__main__":
    print(json.dumps(create_demo(), sort_keys=True))
