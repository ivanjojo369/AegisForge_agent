"""Create a disposable synthetic two-generation closure history for Dashboard.

Prints JSON with checkpoint_dir. Does not use external services, credentials,
real project files or tool execution. The folder remains on disk for local
inspection and must be deleted manually once the demo is finished.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import mkdtemp

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_state import (
    NexusMissionState, MissionStatePolicy, MissionTransitionRule,
)
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_monitor_views import NexusMissionMonitorViews, MonitorLabSource
from aegisforge.nexus.mission_control.mission_closure_hook import NexusMissionClosureHook
from aegisforge.nexus.mission_control.mission_archive_history import NexusMissionArchiveHistory
from aegisforge.nexus.mission_control.mission_closure_host import NexusMissionClosureHost
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
from aegisforge.nexus.mission_control.mission_history_view import NexusMissionHistoryView
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.lab.scenario_catalog import NexusScenarioCatalog, ScenarioCase, ScenarioExpectation
from aegisforge.nexus.lab.evaluation_runner import NexusEvaluationRunner, EvaluationObservation
from aegisforge.nexus.lab.scorecard import NexusScorecard


def digest(x: str) -> str:
    return sha256(x.encode()).hexdigest()


def create_demo() -> dict:
    root = Path(mkdtemp(prefix='colossus_history_dashboard_demo_'))
    actor = 'actor.synthetic'
    records = (('mission.synthetic.nexus', 'nexus', True),
               ('mission.synthetic.grid', 'aegisgrid', False))
    registry = NexusMissionRegistry()
    graphs, labs = {}, {}
    for mid, product, pass_result in records:
        registry.register(mid, product=product, owner_id='owner.synthetic',
                          objective_sha256=digest('objective:'+mid), actor_id=actor)
        registry.transition(mid, 'ready', actor_id=actor)
        registry.transition(mid, 'running', actor_id=actor)
        registry.link_reference(mid, kind='evidence',
                                reference_sha256=digest('evidence:'+mid), actor_id=actor)
        graph = NexusProofGraph(mid)
        mission_node = graph.add_node('mission', mid, status='observed')
        run_node = graph.add_node('run', 'synthetic.'+mid, status='completed',stage='execution')
        graph.add_edge('contains',mission_node,run_node)
        graphs[mid] = graph
        catalog=NexusScenarioCatalog()
        sid='synthetic.'+product
        catalog.register(ScenarioCase(sid, product=product, category='functional',
                           title='Synthetic closure check', description='No external calls',
                           inputs={'synthetic':True},
                           expectations=(ScenarioExpectation('result.ok','equals',True),)))
        case=catalog.get(sid)
        runner=NexusEvaluationRunner(catalog,trusted_sources=('synthetic_fixture',))
        observation=EvaluationObservation(sid,case.case_sha256,source_id='synthetic_fixture',
                               evidence_ref='synthetic/'+mid,
                               payload={'result':{'ok':pass_result}},duration_ms=1)
        evaluation=runner.evaluate(runner.prepare(),(observation,))
        engine=NexusScorecard()
        scorecard=engine.build(mid,(evaluation,))
        labs[mid]=MonitorLabSource(engine,scorecard,(evaluation,))
    store=NexusMissionPersistence(root)
    store.save(registry)
    service=NexusMissionService(registry,authorizer=lambda a,*rest:a==actor)
    hook=NexusMissionClosureHook(service,store,
            NexusMissionMonitorViews(graphs=graphs,labs=labs),
            capture_authorizer=lambda a,action,ids,ctx: a==actor and action=='mission_sources.capture_closed_missions')
    history=NexusMissionArchiveHistory(store,authorizer=lambda a,*rest:a==actor)
    host=NexusMissionClosureHost(hook,history)
    outcomes=[]
    for index,(mid,product,_value) in enumerate(records):
        state=NexusMissionState(MissionStatePolicy(mid,product,(
           MissionTransitionRule('reported_complete',required_reference_kinds=('evidence',)),)))
        result=host.close(mid,'reported_complete',actor_id=actor,state=state,
             expected_registry_sha256=registry.snapshot()['registry_sha256'],
             rotate_prior_archive=index>0)
        outcomes.append(result['outcome'])
        if result['outcome']!='completed':
            raise RuntimeError('synthetic closure failed')
    monitor=NexusMissionCheckpointMonitor(store,store.load())
    readback=NexusMissionHistoryView(store,monitor)
    return {
       'schema':'colossus.history_dashboard.demo.v1',
       'synthetic':True,
       'checkpoint_dir':str(root),
       'generations':[x['generation'] for x in history.list_history()],
       'nexus_history':readback.generations(records[0][0])['count'],
       'grid_history':readback.generations(records[1][0])['count'],
       'lab_archived':all(readback.generations(mid)['lab_trend'] for mid,*_ in records),
       'outcomes':outcomes,
       'authorization_restored':False,
       'effects_executed':False,
       'external_calls':False,
       'cleanup':'Delete checkpoint_dir manually when finished',
    }


if __name__=='__main__':
    print(json.dumps(create_demo(),indent=2,sort_keys=True))
