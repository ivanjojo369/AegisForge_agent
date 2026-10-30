"""Real-interface, offline tests for Mission Control / original Nexus bridge.

No live Nebius, cloud, Docker, effectful tools or filesystem mutations.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import copy
import json

import pytest

from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.planner import NexusPlanner
from aegisforge.nexus.executor import NexusExecutor, RegisteredTool, ToolResult
from aegisforge.nexus.orchestrator import NexusOrchestrator
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_orchestrator_bridge import (
    BRIDGE_SCHEMA, NexusMissionOrchestratorBridge,
    NexusMissionOrchestratorBridgeError, verify_bridge_receipt,
)
from aegisforge.nexus.proof.proof_graph import NexusProofGraph

ACTOR = "actor.test"
MISSION = "mission.test"


def _plan(*, tool=None):
    return json.dumps({
        "goal": "Inspect a synthetic task",
        "steps": [
            {"id": "s1", "kind": "reason" if tool is None else "tool",
             "title": "Prepare", "description": "Inspect known sample",
             "tool": tool, "depends_on": [],
             "success_criteria": "The sample was evaluated"},
            {"id": "s2", "kind": "verify", "title": "Check", "tool": None,
             "description": "Examine evaluation", "depends_on": ["s1"],
             "success_criteria": "A verified result is recorded"},
        ], "assumptions": [], "risks": [],
    })


class FakeClient:
    def __init__(self, tool=None):
        self.calls = 0
        self.text = _plan(tool=tool)

    def chat(self, messages, *, max_tokens=512, temperature=0.2):
        self.calls += 1
        return NebiusReply(content=self.text, model="offline-test", request_id="offline-request")


def setup_bridge(*, tool=False, mutating=False, service_authorizer=None,
                 planning_authorizer=None, external_handler=None):
    registry = NexusMissionRegistry()
    service = NexusMissionService(registry, authorizer=service_authorizer or
                                  (lambda actor, action, mission, version, request: True))
    service.create_mission(MISSION, product="colossus", owner_id="owner.test",
                           objective_sha256="b" * 64, actor_id=ACTOR,
                           expected_registry_sha256=service.registry_version(actor_id=ACTOR))
    client = FakeClient(tool="sample.inspect" if tool else None)
    calls = []

    def handler(args):
        calls.append(args)
        if external_handler:
            external_handler()
        return ToolResult(True, "sample", {"ok": True})

    tools = ({"sample.inspect": RegisteredTool("sample.inspect", "Synthetic read action", handler,
                                              mutating=mutating)} if tool else {})
    original = NexusOrchestrator(NexusPlanner(client), NexusExecutor(tools))
    bridge = NexusMissionOrchestratorBridge(service, original,
                                            planning_authorizer=planning_authorizer)
    return bridge, service, original, client, calls


def bind(bridge, service, original):
    prepared = original.prepare("synthetic task")
    return bridge.bind_prepared(MISSION, actor_id=ACTOR, prepared=prepared,
                                expected_registry_sha256=service.registry_version(actor_id=ACTOR))


def test_bind_original_plan_and_preview():
    bridge, service, original, client, calls = setup_bridge()
    bound = bind(bridge, service, original).to_dict()
    assert bound["schema"] == BRIDGE_SCHEMA
    assert bound["phase"] == "bound"
    assert bound["step_count"] == 2
    assert bound["audit_event_count"] == 1
    assert bound["tools_invoked"] == 0
    assert verify_bridge_receipt(bound)
    preview = bridge.preview(bound["session_id"], actor_id=ACTOR).to_dict()
    assert verify_bridge_receipt(preview)
    assert preview["phase"] == "previewed"
    assert preview["audit_sha256"] is None
    assert calls == []


def test_offline_run_generates_real_evidence_and_fresh_proof_graph():
    bridge, service, original, client, calls = setup_bridge()
    bound = bind(bridge, service, original).to_dict()
    session_id = bound["session_id"]
    finished = bridge.run_offline(session_id, actor_id=ACTOR).to_dict()
    assert verify_bridge_receipt(finished)
    assert finished["phase"] == "completed_read_only"
    assert finished["audit_event_count"] == 3
    assert finished["tools_invoked"] == 0 and finished["effects_executed"] is False
    assert finished["verification_status"] != "verified"
    assert isinstance(finished["audit_sha256"], str)
    snapshot = bridge.proof_snapshot(session_id, actor_id=ACTOR)
    assert NexusProofGraph.verify_snapshot(snapshot)
    assert snapshot["mission_id"] == MISSION
    assert calls == []


def test_authorized_evidence_link_changes_registry_but_not_state():
    bridge, service, original, client, calls = setup_bridge()
    session = bind(bridge, service, original).to_dict()["session_id"]
    report = bridge.run_offline(session, actor_id=ACTOR).to_dict()
    prev = service.registry_version(actor_id=ACTOR)
    result = bridge.link_evidence(session, actor_id=ACTOR,
                                  expected_registry_sha256=prev)
    assert result["status"] == "draft"
    assert {"kind": "evidence", "sha256": report["audit_sha256"]} in result["references"]
    assert service.registry_version(actor_id=ACTOR) != prev
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.link_evidence(session, actor_id=ACTOR, expected_registry_sha256=prev)
    assert NexusProofGraph.verify_snapshot(bridge.proof_snapshot(session, actor_id=ACTOR))


def test_deny_planning_by_default_without_calling_nebius():
    bridge, service, original, client, calls = setup_bridge()
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.prepare(MISSION, actor_id=ACTOR, task="synthetic task",
                       expected_registry_sha256=service.registry_version(actor_id=ACTOR))
    assert client.calls == 0


def test_explicit_authorized_planning_runs_exactly_once():
    checked = []
    def approve(actor, mission, task_digest, anchor, tool_digest):
        checked.append((actor, mission, task_digest, anchor, tool_digest))
        return True
    bridge, service, original, client, calls = setup_bridge(planning_authorizer=approve)
    receipt = bridge.prepare(MISSION, actor_id=ACTOR, task="synthetic task",
                             expected_registry_sha256=service.registry_version(actor_id=ACTOR),
                             allow_model_call=True).to_dict()
    assert client.calls == 1 and len(checked) == 1
    assert checked[0][:2] == (ACTOR, MISSION)
    assert checked[0][2] == sha256(b"synthetic task").hexdigest()
    assert verify_bridge_receipt(receipt)


def test_tool_step_is_never_invoked_even_if_read_only():
    bridge, service, original, client, calls = setup_bridge(tool=True)
    receipt = bind(bridge, service, original).to_dict()
    assert receipt["tool_step_count"] == 1
    preview = bridge.preview(receipt["session_id"], actor_id=ACTOR).to_dict()
    outcome = bridge.run_offline(receipt["session_id"], actor_id=ACTOR).to_dict()
    assert preview["tools_invoked"] == outcome["tools_invoked"] == 0
    assert not calls


def test_mutating_tool_is_never_invoked():
    bridge, service, original, client, calls = setup_bridge(tool=True, mutating=True)
    receipt = bind(bridge, service, original).to_dict()
    outcome = bridge.run_offline(receipt["session_id"], actor_id=ACTOR).to_dict()
    assert outcome["tools_invoked"] == 0 and calls == []


def test_actor_cannot_use_another_session():
    bridge, service, original, client, calls = setup_bridge()
    sid = bind(bridge, service, original).to_dict()["session_id"]
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.preview(sid, actor_id="actor.foreign")
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.run_offline(sid, actor_id="actor.foreign")


def test_second_run_and_preview_after_run_denied():
    bridge, service, original, client, calls = setup_bridge()
    sid = bind(bridge, service, original).to_dict()["session_id"]
    bridge.run_offline(sid, actor_id=ACTOR)
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.run_offline(sid, actor_id=ACTOR)
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.preview(sid, actor_id=ACTOR)


def test_stale_registry_blocks_run_without_consuming_plan():
    bridge, service, original, client, calls = setup_bridge()
    sid = bind(bridge, service, original).to_dict()["session_id"]
    service.assign_component(MISSION, component="proof", actor_id=ACTOR,
                             expected_registry_sha256=service.registry_version(actor_id=ACTOR))
    with pytest.raises(NexusMissionOrchestratorBridgeError, match="stale"):
        bridge.run_offline(sid, actor_id=ACTOR)
    assert bridge._sessions[sid].prepared.consumed is False


def test_host_authorization_denied_blocks_service_bind():
    def auth(actor, action, mission, anchor, request):
        return not (action == "read")
    bridge, service, original, client, calls = setup_bridge(service_authorizer=auth)
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bind(bridge, service, original)


def test_link_authorization_denied_without_mutating_registry():
    def auth(actor, action, mission, anchor, request):
        return action != "link_reference"
    bridge, service, original, client, calls = setup_bridge(service_authorizer=auth)
    sid = bind(bridge, service, original).to_dict()["session_id"]
    bridge.run_offline(sid, actor_id=ACTOR)
    before = service.registry_version(actor_id=ACTOR)
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.link_evidence(sid, actor_id=ACTOR, expected_registry_sha256=before)
    assert before == service.registry_version(actor_id=ACTOR)


def test_host_planning_authorizer_denial_prevents_nebius_call():
    bridge, service, original, client, calls = setup_bridge(planning_authorizer=lambda *x: False)
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.prepare(MISSION, actor_id=ACTOR, task="synthetic task",
                       expected_registry_sha256=service.registry_version(actor_id=ACTOR),
                       allow_model_call=True)
    assert client.calls == 0


def test_host_planning_authorizer_exception_prevents_call():
    def broken(*args):
        raise RuntimeError("sensitive token")
    bridge, service, original, client, calls = setup_bridge(planning_authorizer=broken)
    with pytest.raises(NexusMissionOrchestratorBridgeError) as error:
        bridge.prepare(MISSION, actor_id=ACTOR, task="synthetic task",
                       expected_registry_sha256=service.registry_version(actor_id=ACTOR),
                       allow_model_call=True)
    assert "sensitive token" not in str(error.value)
    assert client.calls == 0


def test_plan_raw_goal_is_not_exported():
    bridge, service, original, client, calls = setup_bridge()
    receipt = bind(bridge, service, original).to_dict()
    text = json.dumps(receipt)
    assert "Inspect a synthetic task" not in text
    assert "offline-request" not in text
    assert "Inspect known sample" not in text


def test_session_is_opaque_random_identifier():
    bridge, service, original, client, calls = setup_bridge()
    one = bind(bridge, service, original).to_dict()["session_id"]
    two = bind(bridge, service, original).to_dict()["session_id"]
    assert one != two and len(one) == len(two) == 32
    assert bridge.session_count() == 2


def test_spent_plan_cannot_be_rebound():
    bridge, service, original, client, calls = setup_bridge()
    prepared = original.prepare("synthetic")
    first = bridge.bind_prepared(MISSION, actor_id=ACTOR, prepared=prepared,
                                expected_registry_sha256=service.registry_version(actor_id=ACTOR))
    bridge.run_offline(first.to_dict()["session_id"], actor_id=ACTOR)
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.bind_prepared(MISSION, actor_id=ACTOR, prepared=prepared,
                             expected_registry_sha256=service.registry_version(actor_id=ACTOR))


def test_foreign_orchestrator_prepared_plan_rejected():
    bridge, service, original, client, calls = setup_bridge()
    other, s2, other_orch, c2, call2 = setup_bridge()
    prepared = other_orch.prepare("synthetic")
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.bind_prepared(MISSION, actor_id=ACTOR, prepared=prepared,
                             expected_registry_sha256=service.registry_version(actor_id=ACTOR))


def test_mutating_plan_after_binding_is_detected():
    bridge, service, original, client, calls = setup_bridge()
    sid = bind(bridge, service, original).to_dict()["session_id"]
    bridge._sessions[sid].prepared.plan.usage["changed"] = 1
    with pytest.raises(NexusMissionOrchestratorBridgeError, match="mutated"):
        bridge.preview(sid, actor_id=ACTOR)


def test_proof_snapshot_requires_completed_plan():
    bridge, service, original, client, calls = setup_bridge()
    sid = bind(bridge, service, original).to_dict()["session_id"]
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.proof_snapshot(sid, actor_id=ACTOR)


def test_link_evidence_requires_completed_plan():
    bridge, service, original, client, calls = setup_bridge()
    sid = bind(bridge, service, original).to_dict()["session_id"]
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.link_evidence(sid, actor_id=ACTOR,
                             expected_registry_sha256=service.registry_version(actor_id=ACTOR))


@pytest.mark.parametrize("field,value", [
    ("effects_executed", True), ("authorization_granted", True),
    ("integrity_only", False), ("tools_invoked", 1),
    ("audit_event_count", 200), ("plan_sha256", "bad"),
    ("phase", "effects_running"), ("step_count", 999),
    ("verification_status", "verified"), ("session_id", "bad"),
    ("run_id_sha256", "a" * 64), ("audit_sha256", "f" * 64),
])
def test_receipt_tampering_rejected(field, value):
    bridge, service, original, client, calls = setup_bridge()
    receipt = bind(bridge, service, original).to_dict()
    receipt[field] = value
    assert not verify_bridge_receipt(receipt)


@pytest.mark.parametrize("field", [
    "schema", "phase", "session_id", "mission_id", "product",
    "actor_sha256", "registry_sha256", "mission_head_sha256",
    "plan_sha256", "plan_fingerprint_sha256", "step_count",
    "tool_step_count", "run_id_sha256", "audit_sha256", "audit_event_count",
    "execution_status", "verification_status", "tools_invoked",
    "integrity_only", "authorization_granted", "effects_executed", "receipt_sha256",
])
def test_receipt_missing_field_rejected(field):
    bridge, service, original, client, calls = setup_bridge()
    receipt = bind(bridge, service, original).to_dict()
    del receipt[field]
    assert not verify_bridge_receipt(receipt)


@pytest.mark.parametrize("value", [None, {}, [], "str", 12, float("nan")])
def test_receipt_wrong_shape_rejected(value):
    assert not verify_bridge_receipt(value)


def test_rehash_of_forbidden_status_still_rejected():
    from aegisforge.nexus.mission_control.mission_orchestrator_bridge import _hash
    bridge, service, original, client, calls = setup_bridge()
    receipt = bind(bridge, service, original).to_dict()
    receipt["effects_executed"] = True
    receipt["receipt_sha256"] = _hash({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    assert not verify_bridge_receipt(receipt)


def test_two_runners_race_one_spends_plan():
    bridge, service, original, client, calls = setup_bridge()
    sid = bind(bridge, service, original).to_dict()["session_id"]
    def attempt(_):
        try:
            bridge.run_offline(sid, actor_id=ACTOR)
            return True
        except NexusMissionOrchestratorBridgeError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(attempt, range(2)))
    assert sorted(result) == [False, True]


def test_proof_snapshot_has_no_raw_task_content():
    bridge, service, original, client, calls = setup_bridge()
    sid = bind(bridge, service, original).to_dict()["session_id"]
    bridge.run_offline(sid, actor_id=ACTOR)
    raw = json.dumps(bridge.proof_snapshot(sid, actor_id=ACTOR))
    assert "Inspect a synthetic task" not in raw
    assert "offline-request" not in raw


def test_never_marks_mission_verified():
    bridge, service, original, client, calls = setup_bridge()
    sid = bind(bridge, service, original).to_dict()["session_id"]
    bridge.run_offline(sid, actor_id=ACTOR)
    bridge.link_evidence(sid, actor_id=ACTOR,
                         expected_registry_sha256=service.registry_version(actor_id=ACTOR))
    assert service.get_mission(MISSION, actor_id=ACTOR)["status"] == "draft"


def test_unknown_session_fails_closed():
    bridge, service, original, client, calls = setup_bridge()
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.preview("f"*32, actor_id=ACTOR)


def test_bad_mission_scope_fails_closed():
    bridge, service, original, client, calls = setup_bridge()
    prepared = original.prepare("synthetic")
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.bind_prepared("mission.other", actor_id=ACTOR, prepared=prepared,
                             expected_registry_sha256=service.registry_version(actor_id=ACTOR))


def test_registry_version_optimistic_concurrency():
    bridge, service, original, client, calls = setup_bridge()
    prepared = original.prepare("synthetic")
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.bind_prepared(MISSION, actor_id=ACTOR, prepared=prepared,
                             expected_registry_sha256="f"*64)


@pytest.mark.parametrize("field", ["task", "allow_model_call"])
def test_missing_explicit_planning_grant_fails(field):
    bridge, service, original, client, calls = setup_bridge(planning_authorizer=lambda *a: True)
    kwargs = dict(task="synthetic task", allow_model_call=True)
    kwargs[field] = "" if field == "task" else False
    with pytest.raises(NexusMissionOrchestratorBridgeError):
        bridge.prepare(MISSION, actor_id=ACTOR,
                       expected_registry_sha256=service.registry_version(actor_id=ACTOR),
                       **kwargs)
    assert client.calls == 0


def test_full_demo_workflow_with_model_opt_in():
    bridge, service, original, client, calls = setup_bridge(
        planning_authorizer=lambda *args: True)
    anchor = service.registry_version(actor_id=ACTOR)
    bound = bridge.prepare(MISSION, actor_id=ACTOR, task="synthetic task",
                           expected_registry_sha256=anchor, allow_model_call=True).to_dict()
    preview = bridge.preview(bound["session_id"], actor_id=ACTOR).to_dict()
    outcome = bridge.run_offline(bound["session_id"], actor_id=ACTOR).to_dict()
    graph = bridge.proof_snapshot(bound["session_id"], actor_id=ACTOR)
    assert verify_bridge_receipt(preview)
    assert verify_bridge_receipt(outcome)
    assert NexusProofGraph.verify_snapshot(graph)
    linked = bridge.link_evidence(bound["session_id"], actor_id=ACTOR,
                                  expected_registry_sha256=anchor)
    assert linked["status"] == "draft"
    assert linked["reference_count"] == 1
    assert calls == [] and client.calls == 1
