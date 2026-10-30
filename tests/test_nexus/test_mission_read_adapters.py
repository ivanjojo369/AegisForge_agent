"""Read-only Mission Control adapter integration tests with real Nexus modules."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json

import pytest

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    MissionStatePolicy, MissionTransitionRule, NexusMissionState,
)
from aegisforge.nexus.mission_control.mission_coordinator import (
    MissionWorkflowStep, MissionCoordinationBlueprint, NexusMissionCoordinator,
)
from aegisforge.nexus.mission_control.mission_bridge import (
    MissionBridgeCatalog, MissionBridgePort, NexusMissionBridge,
)
from aegisforge.nexus.mission_control.mission_read_adapters import (
    READ_ONLY_CATALOG, READ_ADAPTER_SCHEMA, MissionReadReceipt,
    NexusMissionReadAdapters, NexusMissionReadAdapterError,
)
from aegisforge.nexus.lab.scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation,
)
from aegisforge.nexus.proof.proof_graph import NexusProofGraph

H = "b"*64
MID = "mission.alpha"


def fixture_flow(*, product="colossus", operations=None, missing_source=False):
    r = NexusMissionRegistry()
    r.register(MID, product=product, owner_id="team.local", objective_sha256=H,
               actor_id="team.local", at_utc="2026-10-28T10:00:00Z")
    for i, c in enumerate(("lab", "mission_control", "proof"), 1):
        r.assign_component(MID, component=c, actor_id="team.local",
                           at_utc=f"2026-10-28T10:00:{i:02d}Z")
    cat = NexusScenarioCatalog()
    cat.register(ScenarioCase("case.demo", product="nexus", category="functional",
                              title="Demo", description="Harmless synthetic scenario",
                              inputs={"value": "TOPSECRET_SENTINEL"},
                              expectations=(ScenarioExpectation("ok", "equals", True),)))
    graph = NexusProofGraph(MID)
    graph.add_node("run", "run.synthetic", status="observed")
    if operations is None:
        operations = (
            MissionWorkflowStep("step.mission", "mission_control", "summarize_mission"),
            MissionWorkflowStep("step.lab", "lab", "summarize", ("step.mission",)),
            MissionWorkflowStep("step.proof", "proof", "verify_integrity", ("step.lab",)),
        )
    state = NexusMissionState(MissionStatePolicy(MID, product,
        (MissionTransitionRule("ready"),)))
    preview = NexusMissionCoordinator(
        MissionCoordinationBlueprint(MID, product, "ready", tuple(operations)), state,
    ).preview(r)
    plan = NexusMissionBridge(READ_ONLY_CATALOG).plan(preview, r)
    adapter = NexusMissionReadAdapters(r, scenarios=None if missing_source else cat,
                                       proof_graph=graph)
    return r, cat, graph, plan, adapter


def test_catalog_is_sorted_and_three_read_ports():
    assert tuple((p.component, p.operation) for p in READ_ONLY_CATALOG.ports) == (
        ("lab", "summarize"), ("mission_control", "summarize_mission"),
        ("proof", "verify_integrity"),
    )


def test_catalog_is_stable_across_instances():
    assert READ_ONLY_CATALOG.sha256 == NexusMissionReadAdapters(fixture_flow()[0]).catalog.sha256


def test_catalog_contains_no_callable_or_capability():
    d = READ_ONLY_CATALOG.to_dict()
    assert len(d["ports"]) == 3
    assert all(row["metadata_only"] is True for row in d["ports"])
    assert "request_effect" not in str(d)


@pytest.mark.parametrize("step", ["step.mission", "step.lab", "step.proof"])
def test_real_source_read_is_verified(step):
    r, cat, g, plan, adapter = fixture_flow()
    before = (r.snapshot(), cat.snapshot(), g.snapshot())
    receipt = adapter.inspect(plan, step_id=step).to_dict()
    assert receipt["schema"] == READ_ADAPTER_SCHEMA
    assert receipt["step_id"] == step
    assert receipt["effects_executed"] is False
    assert receipt["authorization_granted"] is False
    assert receipt["authenticity_verified"] is False
    assert receipt["integrity_only"] is True
    assert adapter.verify_receipt(receipt, plan)
    assert before == (r.snapshot(), cat.snapshot(), g.snapshot())


def test_mission_source_is_compact_and_omits_owner():
    r, cat, g, plan, adapter = fixture_flow()
    d = adapter.inspect(plan, step_id="step.mission").to_dict()
    assert d["summary"]["status"] == "draft"
    assert d["summary"]["product"] == "colossus"
    assert d["summary"]["event_count"] == 4
    assert "owner_sha256" not in json.dumps(d)
    assert "objective_sha256" not in json.dumps(d)
    assert "events" not in d["summary"]
    assert "references" not in d["summary"]


def test_lab_counts_products_not_inputs():
    _, cat, _, plan, adapter = fixture_flow()
    d = adapter.inspect(plan, step_id="step.lab").to_dict()
    assert d["summary"]["count"] == 1
    assert d["summary"]["by_product"]["nexus"] == 1
    assert d["summary"]["by_mode"]["offline"] == 1
    assert "TOPSECRET_SENTINEL" not in json.dumps(d)
    assert "case.demo" not in json.dumps(d)


def test_proof_snapshot_summary():
    _, _, g, plan, adapter = fixture_flow()
    d = adapter.inspect(plan, step_id="step.proof").to_dict()
    assert d["summary"]["node_count"] == 2
    assert d["summary"]["edge_count"] == 0
    assert d["summary"]["graph_sha256"] == g.snapshot()["graph_sha256"]
    assert "nodes" not in d["summary"]


def test_repeated_read_deterministic():
    _, _, _, plan, a = fixture_flow()
    x = a.inspect(plan, step_id="step.lab").to_dict()
    y = a.inspect(plan, step_id="step.lab").to_dict()
    assert x == y


def test_receipt_is_immutable_and_detached():
    _, _, _, plan, a = fixture_flow()
    x = a.inspect(plan, step_id="step.lab")
    with pytest.raises(FrozenInstanceError):
        x._json = b"bad"
    d = x.to_dict()
    d["summary"]["count"] = 999
    assert x.to_dict()["summary"]["count"] == 1


def test_rejects_bad_constructor_inputs():
    r, cat, graph, _, _ = fixture_flow()
    with pytest.raises(NexusMissionReadAdapterError): NexusMissionReadAdapters(object())
    with pytest.raises(NexusMissionReadAdapterError): NexusMissionReadAdapters(r, scenarios={})
    with pytest.raises(NexusMissionReadAdapterError): NexusMissionReadAdapters(r, proof_graph={})


@pytest.mark.parametrize("step_id", ["", "../file", "A", "step effect", "a", "step/effect", 7, None])
def test_rejects_bad_step_id(step_id):
    *_, plan, a = fixture_flow()
    with pytest.raises(NexusMissionReadAdapterError): a.inspect(plan, step_id=step_id)


def test_rejects_step_not_in_plan():
    *_, plan, a = fixture_flow()
    with pytest.raises(NexusMissionReadAdapterError): a.inspect(plan, step_id="step.unknown")


def test_rejects_no_scenario_provider():
    *_, plan, a = fixture_flow(missing_source=True)
    with pytest.raises(NexusMissionReadAdapterError, match="laboratory source missing"):
        a.inspect(plan, step_id="step.lab")


def test_rejects_no_proof_provider():
    r, _, _, plan, _ = fixture_flow()
    a = NexusMissionReadAdapters(r)
    with pytest.raises(NexusMissionReadAdapterError, match="ProofGraph source missing"):
        a.inspect(plan, step_id="step.proof")


def test_rejects_proof_for_another_mission():
    r, cat, _, plan, _ = fixture_flow()
    a = NexusMissionReadAdapters(r, scenarios=cat, proof_graph=NexusProofGraph("mission.other"))
    with pytest.raises(NexusMissionReadAdapterError, match="another mission"):
        a.inspect(plan, step_id="step.proof")


def test_rejects_stale_registry_plan():
    r, _, _, plan, a = fixture_flow()
    r.assign_component(MID, component="trust", actor_id="team.local", at_utc="2026-10-28T10:00:08Z")
    with pytest.raises(NexusMissionReadAdapterError, match="stale"):
        a.inspect(plan, step_id="step.lab")


def test_receipt_stale_after_registry_change():
    r, _, _, plan, a = fixture_flow()
    receipt = a.inspect(plan, step_id="step.mission").to_dict()
    r.assign_component(MID, component="trust", actor_id="team.local", at_utc="2026-10-28T10:00:08Z")
    assert not a.verify_receipt(receipt, plan)


def test_receipt_stale_after_lab_change():
    _, cat, _, plan, a = fixture_flow()
    receipt = a.inspect(plan, step_id="step.lab").to_dict()
    cat.register(ScenarioCase("case.second", product="aegisgrid", category="functional",
                              title="Demo 2", description="Synthetic",
                              inputs={}, expectations=(ScenarioExpectation("ready", "exists"),)))
    assert not a.verify_receipt(receipt, plan)


def test_receipt_stale_after_proof_change():
    _, _, graph, plan, a = fixture_flow()
    receipt = a.inspect(plan, step_id="step.proof").to_dict()
    graph.add_node("run", "run.next", status="observed")
    assert not a.verify_receipt(receipt, plan)


@pytest.mark.parametrize("change", [
    lambda d: d.update(authorization_granted=True),
    lambda d: d.update(authenticity_verified=True),
    lambda d: d.update(effects_executed=True),
    lambda d: d.update(source_sha256="0"*64),
    lambda d: d["summary"].update(count=9),
    lambda d: d.update(step_id="step.proof"),
    lambda d: d.update(extra=True),
    lambda d: d.pop("source_sha256"),
])
def test_receipt_mutation_fails_even_if_rehashed(change):
    *_, plan, a = fixture_flow()
    d = a.inspect(plan, step_id="step.lab").to_dict()
    change(d)
    body = {k:v for k,v in d.items() if k != "receipt_sha256"}
    d["receipt_sha256"] = sha256(json.dumps(body, sort_keys=True,
        ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    assert not a.verify_receipt(d, plan)


def test_bridge_report_mutation_is_rejected():
    *_, plan, a = fixture_flow()
    d = plan.to_dict()
    d["handoffs"][0]["adapter_id"] = "malicious.executor"
    with pytest.raises(NexusMissionReadAdapterError):
        a.inspect(d, step_id="step.lab")


def test_forged_adapter_contract_is_rejected():
    r, cat, graph, plan, a = fixture_flow()
    ports = list(READ_ONLY_CATALOG.ports)
    ports[0] = MissionBridgePort("lab", "summarize", "local.other.v1", "a"*64)
    altered = MissionBridgeCatalog(tuple(ports))
    from aegisforge.nexus.mission_control.mission_bridge import NexusMissionBridge
    bridge = NexusMissionBridge(altered).plan(plan.to_dict()["coordination"], r)
    with pytest.raises(NexusMissionReadAdapterError):
        a.inspect(bridge, step_id="step.lab")


def test_unsupported_workflow_is_rejected_even_with_own_port():
    r = NexusMissionRegistry()
    r.register(MID, product="nexus", owner_id="team.local",
               objective_sha256=H, actor_id="team.local")
    r.assign_component(MID, component="trust", actor_id="team.local")
    r.assign_component(MID, component="engineering", actor_id="team.local")
    bp = MissionCoordinationBlueprint(MID, "nexus", "ready", (
        MissionWorkflowStep("step.review", "trust", "review_action"),
        MissionWorkflowStep("step.effect", "engineering", "request_effect", ("step.review",), H),
    ))
    state = NexusMissionState(MissionStatePolicy(MID, "nexus", (MissionTransitionRule("ready"),)))
    preview = NexusMissionCoordinator(bp, state).preview(r)
    foreign = MissionBridgeCatalog((
        MissionBridgePort("engineering", "request_effect", "local.effect.v1", H),
        MissionBridgePort("trust", "review_action", "local.trust.v1", H),
    ))
    external = NexusMissionBridge(foreign).plan(preview, r)
    a = NexusMissionReadAdapters(r)
    with pytest.raises(NexusMissionReadAdapterError):
        a.inspect(external, step_id="step.effect")


def test_only_read_methods_not_file_network_execution(monkeypatch):
    import subprocess, socket, pathlib, os
    def forbidden(*args, **kwargs):
        raise AssertionError("side-effect attempted")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(pathlib.Path, "read_bytes", forbidden)
    *_, plan, a = fixture_flow()
    for step in ("step.mission", "step.lab", "step.proof"):
        assert a.verify_receipt(a.inspect(plan, step_id=step).to_dict(), plan)


def test_fails_closed_if_registry_moves_during_read(monkeypatch):
    r, cat, graph, plan, a = fixture_flow()
    old = a._source_summary
    def changed(*args):
        result = old(*args)
        r.assign_component(MID, component="trust", actor_id="team.local", at_utc="2026-10-28T10:00:08Z")
        return result
    monkeypatch.setattr(a, "_source_summary", changed)
    with pytest.raises(NexusMissionReadAdapterError, match="during inspection"):
        a.inspect(plan, step_id="step.lab")


def test_rejects_plain_dict_with_corrupt_plan():
    *_, plan, a = fixture_flow()
    d = plan.to_dict()
    d["authorization_granted"] = True
    with pytest.raises(NexusMissionReadAdapterError): a.inspect(d, step_id="step.lab")


def test_integration_supports_all_three_products():
    for product in ("nexus", "aegisgrid", "live_forge"):
        *_, plan, a = fixture_flow(product=product)
        x = a.inspect(plan, step_id="step.mission").to_dict()
        assert x["product"] == product


def test_receipt_invalid_type_returns_false():
    *_, plan, a = fixture_flow()
    for bad in (None, [], 7, "abc", {}, {"receipt_sha256": "x"}):
        assert not a.verify_receipt(bad, plan)


def test_does_not_change_registered_state():
    r, cat, graph, plan, a = fixture_flow()
    before = (r.snapshot(), cat.snapshot(), graph.snapshot())
    for step in ("step.mission", "step.lab", "step.proof"):
        a.inspect(plan, step_id=step)
    assert before == (r.snapshot(), cat.snapshot(), graph.snapshot())


def test_fails_closed_if_lab_changes_during_read(monkeypatch):
    r, cat, graph, plan, a = fixture_flow()
    old = a._source_summary
    def changed(*args):
        result = old(*args)
        cat.register(ScenarioCase("case.later", product="colossus", category="safety",
                                  title="Later", description="Synthetic", inputs={},
                                  expectations=(ScenarioExpectation("ok", "exists"),)))
        return result
    monkeypatch.setattr(a, "_source_summary", changed)
    with pytest.raises(NexusMissionReadAdapterError, match="source changed during inspection"):
        a.inspect(plan, step_id="step.lab")


def test_fails_closed_if_graph_changes_during_read(monkeypatch):
    r, cat, graph, plan, a = fixture_flow()
    old = a._source_summary
    def changed(*args):
        result = old(*args)
        graph.add_node("run", "run.later", status="observed")
        return result
    monkeypatch.setattr(a, "_source_summary", changed)
    with pytest.raises(NexusMissionReadAdapterError, match="source changed during inspection"):
        a.inspect(plan, step_id="step.proof")
