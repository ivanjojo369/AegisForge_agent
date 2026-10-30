"""Windows-friendly, isolated regression coverage for Mission Runtime v1."""
import copy
import hashlib
import json

import pytest

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    MissionTransitionRule, MissionStatePolicy, NexusMissionState,
)
from aegisforge.nexus.mission_control.mission_coordinator import (
    MissionWorkflowStep, MissionCoordinationBlueprint, NexusMissionCoordinator,
)
from aegisforge.nexus.mission_control.mission_read_adapters import NexusMissionReadAdapters
from aegisforge.nexus.mission_control.mission_runtime import (
    NexusMissionRuntime, NexusMissionRuntimeError, verify_runtime_preview,
    verify_runtime_report,
)
from aegisforge.nexus.lab.scenario_catalog import NexusScenarioCatalog
from aegisforge.nexus.proof.proof_graph import NexusProofGraph


def make_runtime(*, steps=None, policy_target="ready", assigned=("mission_control",),
                 host_checker=None, scenarios=None, proof_graph=None):
    registry = NexusMissionRegistry()
    mission_id = "mission.runtime"
    registry.register(mission_id, product="nexus", owner_id="owner.runtime",
                      objective_sha256="a"*64, actor_id="actor.runtime")
    for name in sorted(set(assigned)):
        registry.assign_component(mission_id, component=name, actor_id="actor.runtime")
    rule = MissionTransitionRule(policy_target)
    state = NexusMissionState(MissionStatePolicy(mission_id, "nexus", (rule,)),
                              host_checker=host_checker)
    if steps is None:
        steps = (MissionWorkflowStep("step.registry", "mission_control", "summarize_mission"),)
    bp = MissionCoordinationBlueprint(mission_id, "nexus", policy_target, tuple(steps))
    coordinator = NexusMissionCoordinator(bp, state)
    adapters = NexusMissionReadAdapters(registry, scenarios=scenarios, proof_graph=proof_graph)
    return registry, NexusMissionRuntime(registry, coordinator, adapters)


def digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def reseal(body, field):
    body[field] = digest({k: v for k, v in body.items() if k != field})
    return body


def test_initial_preview_valid_and_immutable():
    reg, runtime = make_runtime()
    result = runtime.preview()
    body = result.to_dict()
    assert verify_runtime_preview(body)
    assert result.outcome == "host_review_required"
    assert body["authorization_granted"] is False
    assert body["effects_executed"] is False
    body["reasons"].append("changed")
    assert result.to_dict()["reasons"] == []
    assert reg.get("mission.runtime")["status"] == "draft"


def test_inspection_real_receipt_deterministic():
    registry, runtime = make_runtime()
    before = registry.snapshot()
    first = runtime.inspect().to_dict()
    second = runtime.inspect().to_dict()
    assert first == second
    assert verify_runtime_report(first)
    assert first["outcome"] == "completed_read_only"
    assert first["receipt_count"] == 1
    assert first["receipts"][0]["summary"]["status"] == "draft"
    assert registry.snapshot() == before


def test_ordered_dependencies_and_multi_step_reads():
    graph = NexusProofGraph("mission.runtime")
    steps = (
        MissionWorkflowStep("step.proof", "proof", "verify_integrity", ("step.registry",)),
        MissionWorkflowStep("step.registry", "mission_control", "summarize_mission"),
    )
    r, rt = make_runtime(steps=steps, assigned=("mission_control", "proof"), proof_graph=graph)
    result = rt.inspect().to_dict()
    assert result["outcome"] == "completed_read_only"
    assert [x["step_id"] for x in result["receipts"]] == ["step.registry", "step.proof"]
    assert result["receipt_count"] == 2


def test_missing_registered_component_blocks_without_source_reads():
    steps = (MissionWorkflowStep("step.proof", "proof", "verify_integrity"),)
    _, rt = make_runtime(steps=steps)
    assert rt.preview().outcome == "blocked"
    rep = rt.inspect().to_dict()
    assert rep["outcome"] == "blocked" and rep["receipts"] == []
    assert verify_runtime_report(rep)


def test_missing_adapter_binding_blocks():
    steps = (MissionWorkflowStep("step.reason", "intelligence", "reason"),)
    _, rt = make_runtime(steps=steps, assigned=("intelligence",))
    assert rt.preview().outcome == "blocked"
    assert rt.inspect().outcome == "blocked"


def test_running_requires_host_checker_and_readiness_blocks():
    _, rt = make_runtime(policy_target="running")
    assert rt.preview().outcome == "blocked"


def test_effectful_step_is_never_executed():
    steps = (
        MissionWorkflowStep("step.trust", "trust", "review_action"),
        MissionWorkflowStep("step.apply", "engineering", "request_effect",
                            ("step.trust",), "a" * 64),
    )
    r, rt = make_runtime(steps=steps, assigned=("trust", "engineering"))
    rep = rt.inspect().to_dict()
    assert rep["outcome"] == "blocked"
    assert rep["receipts"] == []
    assert rep["effects_executed"] is False
    assert r.get("mission.runtime")["status"] == "draft"


def test_fake_trust_review_does_not_grant_authority():
    steps = (MissionWorkflowStep("step.review", "trust", "review_action"),)
    _, rt = make_runtime(steps=steps, assigned=("trust",))
    assert rt.inspect().outcome == "blocked"


def test_runtime_does_not_write_registry_when_read_source_is_missing():
    steps = (MissionWorkflowStep("step.proof", "proof", "verify_integrity"),)
    r, rt = make_runtime(steps=steps, assigned=("proof",))
    before = r.snapshot()
    report = rt.inspect().to_dict()
    assert report["outcome"] == "failed"
    assert report["reasons"] == ["read_adapter_rejected_step"]
    assert report["receipts"] == []
    assert before == r.snapshot()


def test_runtime_failure_does_not_expose_exception_text():
    steps = (MissionWorkflowStep("step.proof", "proof", "verify_integrity"),)
    _, rt = make_runtime(steps=steps, assigned=("proof",))
    report = rt.inspect().to_dict()
    assert "/home/" not in str(report)
    assert "source missing" not in str(report)


def test_registry_changes_after_preview_give_new_anchor():
    r, rt = make_runtime()
    old = rt.preview().to_dict()
    r.link_reference("mission.runtime", kind="evidence", reference_sha256="b"*64,
                     actor_id="actor.runtime")
    new = rt.preview().to_dict()
    assert old["registry_sha256"] != new["registry_sha256"]
    assert verify_runtime_preview(new)


def test_bad_registry_instance_fails():
    registry, runtime = make_runtime()
    other = NexusMissionRegistry()
    with pytest.raises(NexusMissionRuntimeError):
        NexusMissionRuntime(other, runtime.coordinator, runtime.adapters)


@pytest.mark.parametrize("bad", [None, False, [], "text", 5, {}, {"schema": "wrong"}])
def test_preview_rejects_bad_types(bad):
    assert not verify_runtime_preview(bad)


@pytest.mark.parametrize("bad", [None, False, [], "text", 5, {}, {"schema": "wrong"}])
def test_report_rejects_bad_types(bad):
    assert not verify_runtime_report(bad)


@pytest.mark.parametrize("field,value", [
    ("authorization_granted", True), ("effects_executed", True),
    ("step_count", 0), ("reasons", ["arbitrary"]),
    ("planned_layers", [["step.registry", "step.registry"]]),
    ("registry_sha256", "z"*64), ("outcome", "approved"),
])
def test_preview_detects_tampering_even_if_rehashed(field, value):
    _, rt = make_runtime()
    body = rt.preview().to_dict()
    body[field] = value
    reseal(body, "preview_sha256")
    assert not verify_runtime_preview(body)


@pytest.mark.parametrize("field,value", [
    ("authorization_granted", True), ("effects_executed", True),
    ("authenticity_verified", True), ("receipt_count", 0),
    ("completed_count", 0), ("outcome", "executed"),
    ("registry_sha256", "z"*64),
])
def test_report_detects_tampering_even_if_rehashed(field, value):
    _, rt = make_runtime()
    body = rt.inspect().to_dict()
    body[field] = value
    reseal(body, "report_sha256")
    assert not verify_runtime_report(body)


def test_receipt_changed_even_with_rehashed_report_is_rejected():
    _, rt = make_runtime()
    body = rt.inspect().to_dict()
    body["receipts"][0]["summary"]["status"] = "verified"
    reseal(body, "report_sha256")
    assert not verify_runtime_report(body)


def test_receipt_removed_even_with_rehashed_report_is_rejected():
    _, rt = make_runtime()
    body = rt.inspect().to_dict()
    body["receipts"].clear()
    body["completed_count"] = body["receipt_count"] = 0
    reseal(body, "report_sha256")
    assert not verify_runtime_report(body)


def test_receipt_is_independently_copied():
    _, rt = make_runtime()
    rec = rt.inspect()
    old = rec.to_dict()
    old["receipts"].clear()
    assert len(rec.to_dict()["receipts"]) == 1


def test_two_steps_same_wave_have_deterministic_order():
    graph = NexusProofGraph("mission.runtime")
    steps = (
        MissionWorkflowStep("step.z", "proof", "verify_integrity"),
        MissionWorkflowStep("step.a", "mission_control", "summarize_mission"),
    )
    _, rt = make_runtime(steps=steps, assigned=("mission_control", "proof"), proof_graph=graph)
    assert [r["step_id"] for r in rt.inspect().to_dict()["receipts"]] == ["step.a", "step.z"]


def test_readonly_does_not_generate_proof_nodes():
    graph = NexusProofGraph("mission.runtime")
    steps = (MissionWorkflowStep("step.graph", "proof", "verify_integrity"),)
    _, rt = make_runtime(steps=steps, assigned=("proof",), proof_graph=graph)
    before = graph.snapshot()
    assert rt.inspect().outcome == "completed_read_only"
    assert graph.snapshot() == before

@pytest.mark.parametrize("field,value", [
    ("authenticity_verified", True),
    ("schema", "unsafe.runtime"),
    ("product", "aegisgrid"),
    ("mission_head_sha256", "f"*64),
    ("source_sha256", "not-a-digest"),
    ("contract_sha256", "f"),
    ("step_id", "step.other"),
])
def test_receipt_semantics_rejected_after_self_consistent_rehash(field,value):
    _, rt = make_runtime()
    rep = rt.inspect().to_dict()
    rec = rep["receipts"][0]
    rec[field] = value
    reseal(rec,"receipt_sha256")
    reseal(rep,"report_sha256")
    assert not verify_runtime_report(rep)


def test_runtime_fail_closed_when_adapter_changes_registry(monkeypatch):
    reg, rt = make_runtime()
    original = rt.adapters.inspect
    def unexpected_change(*args, **kwargs):
        reg.link_reference("mission.runtime", kind="evidence", reference_sha256="b"*64,
                           actor_id="actor.runtime")
        return original(*args, **kwargs)
    monkeypatch.setattr(rt.adapters, "inspect", unexpected_change)
    report = rt.inspect().to_dict()
    assert report["outcome"] == "failed"
    assert report["effects_executed"] is False
    assert verify_runtime_report(report)


def test_runtime_does_not_consider_rehashed_preview_an_authorization():
    _, rt = make_runtime()
    report = rt.preview().to_dict()
    report["authorization_granted"] = True
    reseal(report,"preview_sha256")
    assert not verify_runtime_preview(report)


def test_invalid_receipt_summary_type_is_not_verified():
    _, rt = make_runtime()
    report = rt.inspect().to_dict()
    report["receipts"][0]["summary"] = set(["bad"])
    assert not verify_runtime_report(report)
