"""Offline, synthetic two-closure history demo. No API calls or real approvals.

Authorizers below are ONLY local test fixtures. Never use them in production.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_state import (
    NexusMissionState, MissionStatePolicy, MissionTransitionRule,
)
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_monitor_views import NexusMissionMonitorViews
from aegisforge.nexus.mission_control.mission_closure_hook import NexusMissionClosureHook
from aegisforge.nexus.mission_control.mission_archive_history import NexusMissionArchiveHistory
from aegisforge.nexus.mission_control.mission_closure_host import NexusMissionClosureHost
from aegisforge.nexus.proof.proof_graph import NexusProofGraph


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def main() -> dict:
    actor = "actor.synthetic"
    records = (("mission.synthetic.nexus", "nexus"),
               ("mission.synthetic.grid", "aegisgrid"))
    with TemporaryDirectory(prefix="colossus_closure_history_demo_") as tmp:
        root = Path(tmp)
        reg = NexusMissionRegistry()
        graphs = {}
        for mid, product in records:
            reg.register(mid, product=product, owner_id="owner.synthetic",
                         objective_sha256=digest("objective:" + mid), actor_id=actor)
            reg.transition(mid, "ready", actor_id=actor)
            reg.transition(mid, "running", actor_id=actor)
            reg.link_reference(mid, kind="evidence",
                               reference_sha256=digest("evidence:" + mid), actor_id=actor)
            g = NexusProofGraph(mid)
            root_node = g.add_node("mission", mid, status="observed")
            run_node = g.add_node("run", "run." + mid,
                                  status="completed", stage="execution")
            g.add_edge("contains", root_node, run_node)
            graphs[mid] = g

        # Authorized here ONLY because all data is synthetic and temporary.
        svc = NexusMissionService(reg, authorizer=lambda who, *_: who == actor)
        store = NexusMissionPersistence(root)
        store.save(reg)
        views = NexusMissionMonitorViews(graphs=graphs)
        capture_auth = lambda who, action, ids, ctx: (who == actor and
                        action == "mission_sources.capture_closed_missions")
        history_auth = lambda who, action, ctx: (who == actor and action in {
            "mission_history.preserve_current", "mission_history.retire_current"})
        hook = NexusMissionClosureHook(svc, store, views,
                                       capture_authorizer=capture_auth)
        history = NexusMissionArchiveHistory(store, authorizer=history_auth)
        host = NexusMissionClosureHost(hook, history)

        reports = []
        for i, (mid, product) in enumerate(records):
            state = NexusMissionState(MissionStatePolicy(
                mid, product, (MissionTransitionRule("reported_complete",
                           required_reference_kinds=("evidence",)),)))
            report = host.close(mid, "reported_complete", actor_id=actor,
                                state=state,
                                expected_registry_sha256=reg.snapshot()["registry_sha256"],
                                rotate_prior_archive=i > 0)
            reports.append(report)

        # Read the historical copies through a fresh persistence object.
        reopened = NexusMissionArchiveHistory(NexusMissionPersistence(root),
                                               authorizer=history_auth)
        generations = reopened.list_history()
        return {
            "schema": "colossus.mission_closure_history.demo.v1",
            "synthetic": True,
            "outcomes": [r["outcome"] for r in reports],
            "persisted_generation": store.load().receipt.generation,
            "historical_generations": [r["generation"] for r in generations],
            "first_graph_archived": reopened.read_summary(2, records[0][0], "graph") is not None,
            "second_graph_archived": reopened.read_summary(3, records[1][0], "graph") is not None,
            "archive_count": len(generations),
            "active_archive_present": (root / "mission_sources.json").is_file(),
            "trust_permissions_restored": False,
            "effects_executed": False,
            "external_services_called": False,
        }


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))
