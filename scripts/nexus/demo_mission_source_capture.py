"""Offline disposable demonstration of explicit post-closure source capture.

NO production approval or tool permission is granted by this synthetic demo.
No Nebius, network, Docker, repository changes, or real project sources.
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
from aegisforge.nexus.mission_control.mission_source_capture import NexusMissionSourceCapture
from aegisforge.nexus.mission_control.mission_source_archive import NexusMissionSourceArchive
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.lab.scenario_catalog import NexusScenarioCatalog, ScenarioCase, ScenarioExpectation
from aegisforge.nexus.lab.evaluation_runner import NexusEvaluationRunner, EvaluationObservation
from aegisforge.nexus.lab.scorecard import NexusScorecard
from aegisforge.nexus.trust.approval_gate import NexusApprovalGate, ActionBinding


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def demo() -> dict:
    root=Path(tempfile.mkdtemp(prefix='colossus_capture_demo_'))
    actor='actor.synthetic'
    mission='mission.nexus.closed.demo'
    registry=NexusMissionRegistry()
    registry.register(mission,product='nexus',owner_id='owner.synthetic',
                      objective_sha256=digest('synthetic objective'),actor_id=actor)
    registry.transition(mission,'ready',actor_id=actor)
    registry.transition(mission,'running',actor_id=actor)
    registry.link_reference(mission,kind='evaluation',reference_sha256=digest('synthetic evaluation'),actor_id=actor)
    registry.transition(mission,'reported_complete',actor_id=actor)
    g=NexusProofGraph(mission)
    root_node=g.add_node('mission',mission,status='observed')
    run=g.add_node('run','run.synthetic',status='completed',stage='execution')
    g.add_edge('contains',root_node,run)
    catalog=NexusScenarioCatalog()
    key='scenario.synthetic'
    catalog.register(ScenarioCase(key,product='nexus',category='functional',
                     title='Synthetic evidence',description='No model or network used',
                     inputs={'fixture':True},
                     expectations=(ScenarioExpectation('check.ok','equals',True),)))
    case=catalog.get(key)
    eval_runner=NexusEvaluationRunner(catalog,trusted_sources=('synthetic_fixture',))
    obs=EvaluationObservation(key,case.case_sha256,source_id='synthetic_fixture',
        evidence_ref='synthetic/demo',payload={'check':{'ok':True}},duration_ms=1)
    result=eval_runner.evaluate(eval_runner.prepare(),(obs,))
    scorer=NexusScorecard()
    score=scorer.build(mission,(result,))
    binding=ActionBinding(digest('synthetic_payload'),digest('synthetic_context'))
    gate=NexusApprovalGate(root,reviewer=lambda _:None,binding_provider=lambda _:binding)
    request=gate.request(mission,'write_file','synthetic/never_used.txt',binding=binding)
    views=NexusMissionMonitorViews(graphs={mission:g},
        labs={mission:MonitorLabSource(scorer,score,(result,))},
        trusts={mission:MonitorTrustSource(gate,(request,))})
    store=NexusMissionPersistence(root)
    store.save(registry)

    # This callback is intentionally limited to this *synthetic* fixture.
    # Never copy it into a production authorizer or treat it as identity proof.
    def synthetic_only(actor_id,operation,ids,checkpoint):
        return (actor_id==actor and operation=='mission_sources.capture_closed_missions'
                and ids==(mission,) and checkpoint['generation']==1)

    capture=NexusMissionSourceCapture(store,views,authorizer=synthetic_only)
    plan=capture.prepare([mission],actor_id=actor)
    receipt=capture.commit(plan,actor_id=actor)
    # Simulate restart: new store/monitor/archive instances, no live gate restored.
    restarted_store=NexusMissionPersistence(root)
    monitor=NexusMissionCheckpointMonitor(restarted_store,restarted_store.load())
    archive=NexusMissionSourceArchive(root/'mission_sources.json',monitor)
    return {
        'schema':'colossus.mission_source_capture.demo.v1',
        'synthetic':True,'checkpoint_generation':receipt['checkpoint_generation'],
        'mission_status':monitor._registry.get(mission)['status'],
        'mission_id':mission,
        'archive_verified':NexusMissionSourceCapture.verify_capture_report(receipt,monitor,archive),
        'graph_archived':archive.summary('graph',mission) is not None,
        'laboratory_archived':archive.summary('laboratory',mission) is not None,
        'trust_archived_historical_only':archive.summary('trust',mission)['historical_only'],
        'authorization_restored':False,'effects_executed':False,
        'external_services_called':False,
    }


if __name__=='__main__':
    print(json.dumps(demo(),sort_keys=True,indent=2))
