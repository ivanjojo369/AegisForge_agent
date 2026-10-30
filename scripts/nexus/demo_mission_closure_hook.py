"""Fully offline demonstration: supervised closure and explicit archive recovery.

Synthetic authorizers are test fixtures only. NEVER use these in production.
No model calls, network, Docker, repository modifications or real approvals.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile

from aegisforge.nexus.mission_control.mission_closure_hook import NexusMissionClosureHook
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    NexusMissionState, MissionStatePolicy, MissionTransitionRule,
)
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_monitor_views import NexusMissionMonitorViews
from aegisforge.nexus.mission_control.mission_source_archive import NexusMissionSourceArchive
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from aegisforge.nexus.proof.proof_graph import NexusProofGraph


def digest(s: str) -> str:
    return sha256(s.encode()).hexdigest()


def main() -> dict:
    actor = 'actor.synthetic'
    mid = 'mission.synthetic.closure'
    with tempfile.TemporaryDirectory(prefix='colossus_closure_demo_') as scratch:
        root = Path(scratch)
        registry = NexusMissionRegistry()
        registry.register(mid, product='nexus', owner_id='owner.synthetic',
                          objective_sha256=digest('synthetic objective'), actor_id=actor)
        registry.transition(mid, 'ready', actor_id=actor)
        registry.transition(mid, 'running', actor_id=actor)
        registry.link_reference(mid, kind='evidence',
                                reference_sha256=digest('synthetic observation'),
                                actor_id=actor)
        service = NexusMissionService(
            registry,
            authorizer=lambda who, action, mission, anchor, request:
                who == actor and action in {'transition', 'export_snapshot'},
        )
        store = NexusMissionPersistence(root)
        store.save(registry)
        graph = NexusProofGraph(mid)
        node_m = graph.add_node('mission', mid, status='observed')
        node_r = graph.add_node('run', 'run.synthetic', status='completed', stage='execution')
        graph.add_edge('contains', node_m, node_r)
        sources = NexusMissionMonitorViews(graphs={mid:graph})
        state = NexusMissionState(MissionStatePolicy(
            mid, 'nexus', (MissionTransitionRule('reported_complete',
                         required_reference_kinds=('evidence',)),)))

        def synthetic_archive_authorizer(who, action, ids, metadata):
            return (who == actor and action == 'mission_sources.capture_closed_missions'
                    and ids == (mid,) and metadata['generation'] == 2)

        hook = NexusMissionClosureHook(service, store, sources,
                                       capture_authorizer=synthetic_archive_authorizer)
        plan = hook.prepare(mid, 'reported_complete', actor_id=actor, state=state)
        report = hook.commit(plan, actor_id=actor, state=state)
        # Independent reader after the closure, using fresh checkpoint objects.
        fresh_store = NexusMissionPersistence(root)
        monitor = NexusMissionCheckpointMonitor(fresh_store, fresh_store.load())
        archive = NexusMissionSourceArchive(root / 'mission_sources.json', monitor)
        return {
            'schema':'colossus.mission_closure_hook.demo.v1',
            'synthetic':True, 'mission_id':mid,
            'outcome':report['outcome'],
            'receipt_verified_structurally':NexusMissionClosureHook.verify_receipt(report),
            'checkpoint_generation':monitor.status()['generation'],
            'persisted_status':monitor._registry.get(mid)['status'],
            'graph_archived':archive.summary('graph',mid) is not None,
            'trust_permissions_restored':False,
            'authenticity_verified':False,
            'effects_executed':False,
            'external_services_called':False,
        }


if __name__ == '__main__':
    print(json.dumps(main(), sort_keys=True, indent=2))
