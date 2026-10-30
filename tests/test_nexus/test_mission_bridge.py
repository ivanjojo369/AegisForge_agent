"""Mission Bridge v1: metadata-only binding, integrity, and fail-closed tests."""
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
    MISSION_BRIDGE_SCHEMA, BRIDGE_CATALOG_SCHEMA, MissionBridgePort,
    MissionBridgeCatalog, MissionBridgePlan, NexusMissionBridge,
    NexusMissionBridgeError, verify_bridge_plan,
)

H1, H2, H3, H4, H5, H6 = (c * 64 for c in "123456")


def registry(*, product="nexus", components=("workspace", "intelligence", "engineering", "trust", "proof")):
    r = NexusMissionRegistry()
    r.register("mission.alpha", product=product, owner_id="team.dev", objective_sha256=H1,
               actor_id="team.dev", at_utc="2026-10-28T00:00:00Z")
    for n, component in enumerate(components, start=1):
        r.assign_component("mission.alpha", component=component, actor_id="team.dev",
                           at_utc=f"2026-10-28T00:00:{n:02d}Z")
    return r


def blueprint(*, product="nexus", steps=None):
    if steps is None:
        steps = (
            MissionWorkflowStep("step.inventory", "workspace", "inventory"),
            MissionWorkflowStep("step.plan", "intelligence", "plan", ("step.inventory",)),
            MissionWorkflowStep("step.propose", "engineering", "propose_change", ("step.plan",)),
            MissionWorkflowStep("step.review", "trust", "review_action", ("step.propose",)),
            MissionWorkflowStep("step.effect", "engineering", "request_effect", ("step.review",), H2),
            MissionWorkflowStep("step.proof", "proof", "collect_evidence", ("step.effect",)),
        )
    return MissionCoordinationBlueprint("mission.alpha", product, "ready", tuple(steps))


def preview(r=None, bp=None, *, blocked=False):
    r = registry() if r is None else r
    bp = blueprint(product=r.get("mission.alpha")["product"]) if bp is None else bp
    required = ("lab",) if blocked else ()
    state = NexusMissionState(MissionStatePolicy(
        "mission.alpha", bp.product, (MissionTransitionRule("ready", required_components=required),)
    ))
    return NexusMissionCoordinator(bp, state).preview(r)


def catalog(*, missing=(), add=(), reverse=False):
    pairs = {
        ("workspace", "inventory"): ("adapter.workspace", H1),
        ("intelligence", "plan"): ("adapter.intelligence", H2),
        ("engineering", "propose_change"): ("adapter.engineering", H3),
        ("trust", "review_action"): ("adapter.trust", H4),
        ("engineering", "request_effect"): ("adapter.effect", H5),
        ("proof", "collect_evidence"): ("adapter.proof", H6),
    }
    for entry in missing:
        del pairs[entry]
    pairs.update(add)
    ports = [MissionBridgePort(component, operation, adapter, contract)
             for (component, operation), (adapter, contract) in pairs.items()]
    ports.sort(key=lambda p: (p.component, p.operation), reverse=reverse)
    return MissionBridgeCatalog(tuple(ports))


def build(r=None, p=None, c=None):
    r = registry() if r is None else r
    p = preview(r) if p is None else p
    c = catalog() if c is None else c
    return NexusMissionBridge(c).plan(p, r)


def rehash(report):
    report["bridge_sha256"] = sha256(json.dumps(
        {k: v for k, v in report.items() if k != "bridge_sha256"},
        sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return report


def test_happy_path_full_binding():
    r = registry()
    p = preview(r)
    c = catalog()
    obj = build(r, p, c)
    d = obj.to_dict()
    assert d["schema"] == MISSION_BRIDGE_SCHEMA
    assert d["outcome"] == "host_review_required"
    assert d["reasons"] == ["host_review_required"]
    assert len(d["handoffs"]) == 6
    assert not d["missing_bindings"]
    assert verify_bridge_plan(d, registry=r, coordination=p, catalog=c)


def test_effect_step_inherits_review_chain():
    d = build().to_dict()
    effect = next(s for s in d["handoffs"] if s["operation"] == "request_effect")
    assert effect["intent_sha256"] == H2
    assert effect["depends_on"] == ["step.review"]
    assert effect["metadata_only"] is True
    assert d["required_action_reviews"] == [{"step_id": "step.effect", "intent_sha256": H2}]
    assert d["authorization_granted"] is False
    assert d["effects_executed"] is False


def test_verified_does_not_mean_authenticated():
    d = build().to_dict()
    assert d["integrity_only"] is True
    assert d["authenticity_verified"] is False
    assert not d["authorization_granted"]
    assert not d["effects_executed"]
    assert all(x["binding_status"] == "metadata_binding_present" for x in d["handoffs"])
    assert verify_bridge_plan(d)


def test_registry_unchanged_by_bridge():
    r = registry()
    before = r.snapshot()
    build(r)
    assert before == r.snapshot()


def test_repeated_build_identical():
    r = registry()
    p = preview(r)
    c = catalog()
    assert build(r, p, c).to_dict() == build(r, p, c).to_dict()


def test_catalog_deterministic_hash():
    assert catalog().sha256 == catalog().sha256
    assert catalog().sha256 != catalog(missing=[("trust", "review_action")]).sha256
    assert catalog().to_dict()["schema"] == BRIDGE_CATALOG_SCHEMA


def test_catalog_metadata_only_no_callable():
    row = catalog().to_dict()["ports"][0]
    assert row["metadata_only"] is True
    assert set(row) == {"component", "operation", "adapter_id", "contract_sha256", "metadata_only"}


def test_plan_object_returns_detached_copy():
    obj = build()
    report = obj.to_dict()
    report["handoffs"].clear()
    report["coordination"]["planned_layers"].clear()
    assert len(obj.to_dict()["handoffs"]) == 6


def test_plan_object_immutable():
    with pytest.raises(FrozenInstanceError):
        build()._payload = b"poison"


def test_no_execution_side_effects(monkeypatch):
    import subprocess
    import socket
    import os
    import pathlib
    def forbidden(*args, **kwargs):
        raise AssertionError("bridge must not execute commands, use a network, or inspect filesystem")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(pathlib.Path, "read_bytes", forbidden)
    assert build().outcome == "host_review_required"


def test_empty_catalog_blocks_all_bindings():
    p = build(c=MissionBridgeCatalog(())).to_dict()
    assert p["outcome"] == "blocked"
    assert len(p["missing_bindings"]) == 6
    assert verify_bridge_plan(p)


def test_one_missing_binding_blocks():
    p = build(c=catalog(missing=[("trust", "review_action")])).to_dict()
    assert p["outcome"] == "blocked"
    assert p["missing_bindings"] == ["trust.review_action"]
    assert "binding_missing:trust.review_action" in p["reasons"]
    assert verify_bridge_plan(p)


def test_multiple_missing_bindings_sorted():
    miss = [("proof", "collect_evidence"), ("workspace", "inventory")]
    p = build(c=catalog(missing=miss)).to_dict()
    assert p["missing_bindings"] == ["proof.collect_evidence", "workspace.inventory"]
    assert p["reasons"] == ["binding_missing:proof.collect_evidence", "binding_missing:workspace.inventory"]


def test_extra_binding_is_allowed_and_does_not_add_handoff():
    c = catalog(add={("lab", "evaluate"): ("adapter.lab", H6)})
    d = build(c=c).to_dict()
    assert len(d["handoffs"]) == 6
    assert d["outcome"] == "host_review_required"
    assert len(d["adapter_catalog"]["ports"]) == 7


def test_blocked_coordination_remains_blocked():
    r = registry()
    p = preview(r, blocked=True)
    d = build(r, p).to_dict()
    assert d["outcome"] == "blocked"
    assert d["reasons"] == ["coordination_blocked"]
    assert verify_bridge_plan(d, coordination=p, registry=r)


def test_blocked_coordination_and_missing_binding():
    r = registry()
    p = preview(r, blocked=True)
    d = build(r, p, catalog(missing=[("proof", "collect_evidence")])).to_dict()
    assert d["reasons"] == ["binding_missing:proof.collect_evidence", "coordination_blocked"]


def test_port_does_not_accept_code_like_adapter_identifier():
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgePort("proof", "collect_evidence", "python -c print(1)", H1)


def test_port_does_not_accept_path_like_adapter_identifier():
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgePort("proof", "collect_evidence", "../../tmp/private", H1)


def test_port_does_not_accept_newlines():
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgePort("proof", "collect_evidence", "adapter\nsecret", H1)


def test_port_does_not_accept_unicode():
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgePort("proof", "collect_evidence", "adaptador.é", H1)


@pytest.mark.parametrize("component,operation", [
    ("unknown", "reason"), ("trust", "request_effect"),
    ("workspace", "review_action"), ("engineering", "plan_energy"),
    ("lab", "run_shell"), ("aegisgrid", "python_eval"),
    ("live_forge", "execute"), ("proof", "approve"),
    ("mission_control", "execute"), ("intelligence", "read_secrets"),
    ("engineering", "run_tests"), ("workspace", "write")
])
def test_port_rejects_invalid_operation(component, operation):
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgePort(component, operation, "adapter.name", H1)


@pytest.mark.parametrize("value", [
    "", "AA" * 32, "x" * 63, "z" * 65, 1, None, True, False,
])
def test_port_rejects_bad_digest(value):
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgePort("proof", "collect_evidence", "adapter.name", value)


@pytest.mark.parametrize("value", [
    None, 4, True, "ab", "a" * 81, "a/path", "adapter name", "adapter:name"
])
def test_port_rejects_bad_adapter_id(value):
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgePort("proof", "collect_evidence", value, H1)


def test_port_slots_frozen():
    p = MissionBridgePort("proof", "collect_evidence", "adapter.name", H1)
    with pytest.raises(FrozenInstanceError):
        p.component = "trust"


def test_catalog_rejects_duplicate_operation():
    p = MissionBridgePort("proof", "collect_evidence", "adapter.proof", H1)
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgeCatalog((p, p))


def test_catalog_rejects_unsorted():
    with pytest.raises(NexusMissionBridgeError):
        catalog(reverse=True)


def test_catalog_rejects_list():
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgeCatalog([MissionBridgePort("proof", "collect_evidence", "adapter.proof", H1)])


def test_catalog_rejects_bad_element():
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgeCatalog(("hi",))


def test_catalog_rejects_excessive_count():
    good = MissionBridgePort("proof", "collect_evidence", "adapter.proof", H1)
    with pytest.raises(NexusMissionBridgeError):
        MissionBridgeCatalog(tuple(good for _ in range(129)))


def test_bridge_rejects_untyped_catalog():
    with pytest.raises(NexusMissionBridgeError):
        NexusMissionBridge({})


def test_bridge_rejects_wrong_registry_type():
    with pytest.raises(NexusMissionBridgeError):
        NexusMissionBridge(catalog()).plan(preview(), [])


def test_bridge_rejects_tampered_registry_snapshot():
    r = registry()
    s = r.snapshot()
    s["missions"][0]["product"] = "live_forge"
    with pytest.raises(NexusMissionBridgeError):
        NexusMissionBridge(catalog()).plan(preview(r), s)


def test_bridge_rejects_stale_registry_after_reference():
    r = registry()
    p = preview(r)
    r.link_reference("mission.alpha", kind="artifact", reference_sha256=H1,
                     actor_id="team.dev", at_utc="2026-10-28T00:00:12Z")
    with pytest.raises(NexusMissionBridgeError, match="changed"):
        NexusMissionBridge(catalog()).plan(p, r)


def test_bridge_rejects_stale_registry_after_component():
    r = registry()
    p = preview(r)
    r.assign_component("mission.alpha", component="lab", actor_id="team.dev",
                       at_utc="2026-10-28T00:00:12Z")
    with pytest.raises(NexusMissionBridgeError, match="changed"):
        NexusMissionBridge(catalog()).plan(p, r)


def test_bridge_rejects_stale_registry_after_state_transition():
    r = registry()
    p = preview(r)
    r.transition("mission.alpha", "ready", actor_id="team.dev",
                 at_utc="2026-10-28T00:00:12Z")
    with pytest.raises(NexusMissionBridgeError, match="changed"):
        NexusMissionBridge(catalog()).plan(p, r)


def test_bridge_can_plan_against_valid_snapshot():
    r = registry()
    d = NexusMissionBridge(catalog()).plan(preview(r), r.snapshot()).to_dict()
    assert verify_bridge_plan(d, registry=r.snapshot())


def test_reject_wrong_coordination_type():
    with pytest.raises(NexusMissionBridgeError):
        NexusMissionBridge(catalog()).plan("not a preview", registry())


def test_reject_tampered_coordination_hash():
    r = registry()
    d = preview(r).to_dict()
    d["outcome"] = "blocked"
    with pytest.raises(NexusMissionBridgeError):
        NexusMissionBridge(catalog()).plan(d, r)


def test_reject_coordinator_preview_from_another_mission():
    r = registry()
    r2 = registry()
    r2.link_reference("mission.alpha", kind="artifact", reference_sha256=H2,
                      actor_id="team.dev", at_utc="2026-10-28T00:00:12Z")
    with pytest.raises(NexusMissionBridgeError):
        NexusMissionBridge(catalog()).plan(preview(r2), r)


def test_reject_wrong_product_source():
    r = registry(product="live_forge", components=("live_forge", "trust"))
    other = registry()
    p = preview(r, blueprint(product="live_forge", steps=(
        MissionWorkflowStep("step.stream", "live_forge", "plan_stream"),
        MissionWorkflowStep("step.review", "trust", "review_action", ("step.stream",)),
    )))
    with pytest.raises(NexusMissionBridgeError):
        NexusMissionBridge(catalog()).plan(p, other)


def test_preview_can_be_given_as_dict():
    r = registry()
    d = NexusMissionBridge(catalog()).plan(preview(r).to_dict(), r).to_dict()
    assert verify_bridge_plan(d)


def test_live_forge_product():
    bp = blueprint(product="live_forge", steps=(
        MissionWorkflowStep("step.overlay", "live_forge", "draft_overlay"),
        MissionWorkflowStep("step.review", "trust", "review_action", ("step.overlay",)),
    ))
    r = registry(product="live_forge", components=("live_forge", "trust"))
    c = MissionBridgeCatalog(tuple(sorted((
        MissionBridgePort("live_forge", "draft_overlay", "adapter.overlay", H1),
        MissionBridgePort("trust", "review_action", "adapter.review", H2),
    ), key=lambda p: (p.component, p.operation))))
    d = build(r, preview(r, bp), c).to_dict()
    assert d["product"] == "live_forge"
    assert d["outcome"] == "host_review_required"


def test_aegisgrid_product():
    bp = blueprint(product="aegisgrid", steps=(
        MissionWorkflowStep("step.energy", "aegisgrid", "simulate_energy"),
    ))
    r = registry(product="aegisgrid", components=("aegisgrid",))
    c = MissionBridgeCatalog((MissionBridgePort("aegisgrid", "simulate_energy", "adapter.energy", H1),))
    d = build(r, preview(r, bp), c).to_dict()
    assert d["product"] == "aegisgrid"
    assert d["outcome"] == "host_review_required"


def test_colossus_product():
    bp = blueprint(product="colossus", steps=(
        MissionWorkflowStep("step.lab", "lab", "evaluate"),
    ))
    r = registry(product="colossus", components=("lab",))
    c = MissionBridgeCatalog((MissionBridgePort("lab", "evaluate", "adapter.lab", H1),))
    d = build(r, preview(r, bp), c).to_dict()
    assert d["product"] == "colossus"


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(outcome="host_review_required" if d["outcome"] == "blocked" else "blocked"),
    lambda d: d.update(reasons=[]),
    lambda d: d.update(missing_bindings=["trust.review_action"]),
    lambda d: d["handoffs"][0].update(binding_status="missing"),
    lambda d: d["handoffs"][0].update(adapter_id="adapter.other"),
    lambda d: d["handoffs"][0].update(contract_sha256=H6),
    lambda d: d["handoffs"][0].update(handoff_sha256=H6),
    lambda d: d["handoffs"][0].update(metadata_only=False),
    lambda d: d["handoffs"][0].update(depends_on=["step.inventory"]),
    lambda d: d["adapter_catalog"]["ports"][0].update(adapter_id="adapter.other"),
    lambda d: d["adapter_catalog"].update(catalog_sha256=H1),
    lambda d: d.update(required_action_reviews=[]),
    lambda d: d.update(planned_layers=[]),
    lambda d: d.update(mission_id="mission.other"),
    lambda d: d.update(product="aegisgrid"),
    lambda d: d.update(target_status="running"),
    lambda d: d.update(coordination_sha256=H2),
    lambda d: d.update(registry_sha256=H1),
    lambda d: d.update(policy_sha256=H1),
    lambda d: d.update(mission_head_sha256=H1),
    lambda d: d.update(authenticity_verified=True),
    lambda d: d.update(authorization_granted=True),
    lambda d: d.update(effects_executed=True),
    lambda d: d.update(integrity_only=False),
    lambda d: d["coordination"].update(outcome="blocked"),
    lambda d: d["coordination"]["state_assessment"].update(outcome="blocked"),
    lambda d: d.update(schema="aegisforge.nexus.mission_control.bridge.v999"),
    lambda d: d.update(unexpected="hey"),
])
def test_rejects_rehashed_metadata_forgery(mutate):
    d = build().to_dict()
    mutate(d)
    assert not verify_bridge_plan(rehash(d))


def test_rejects_corrupted_self_digest():
    d = build().to_dict()
    d["bridge_sha256"] = H1
    assert not verify_bridge_plan(d)


def test_verify_report_with_original_anchors():
    r = registry()
    p = preview(r)
    c = catalog()
    d = build(r, p, c).to_dict()
    assert verify_bridge_plan(d, registry=r, coordination=p, catalog=c)


def test_verify_rejects_changed_registry_after_planning():
    r = registry()
    p = preview(r)
    d = build(r, p).to_dict()
    r.assign_component("mission.alpha", component="lab", actor_id="team.dev",
                       at_utc="2026-10-28T00:00:12Z")
    assert verify_bridge_plan(d)
    assert not verify_bridge_plan(d, registry=r)


def test_verify_rejects_other_catalog_anchor():
    d = build().to_dict()
    assert not verify_bridge_plan(d, catalog=catalog(missing=[("engineering", "propose_change")]))


def test_verify_rejects_other_coordination_anchor():
    r = registry()
    p = preview(r, blocked=True)
    d = build(r, p).to_dict()
    assert not verify_bridge_plan(d, coordination=preview(r))


def test_verify_rejects_invalid_report_types():
    for v in (None, [], {}, "", 42, True, {"schema": MISSION_BRIDGE_SCHEMA}):
        assert not verify_bridge_plan(v)


def test_verify_rejects_oversized_report():
    d = build().to_dict()
    d["untrusted"] = "x" * 350_000
    assert not verify_bridge_plan(d)


def test_no_raw_payload_or_command():
    d = build().to_dict()
    raw = json.dumps(d)
    assert "argv" not in raw
    assert "shell" not in raw
    assert "authorization_granted\": true" not in raw
    assert "effects_executed\": true" not in raw


def test_handoff_sha_binds_catalog():
    r = registry()
    p = preview(r)
    d1 = build(r, p, catalog()).to_dict()
    revised = catalog(add={("lab", "evaluate"): ("adapter.lab", H6)})
    d2 = build(r, p, revised).to_dict()
    assert d1["handoffs"][0]["handoff_sha256"] != d2["handoffs"][0]["handoff_sha256"]


def test_handoff_sha_binds_preview():
    r = registry()
    d1 = build(r).to_dict()
    r.link_reference("mission.alpha", kind="evaluation", reference_sha256=H3,
                     actor_id="team.dev", at_utc="2026-10-28T00:00:12Z")
    d2 = build(r).to_dict()
    assert d1["handoffs"][0]["handoff_sha256"] != d2["handoffs"][0]["handoff_sha256"]


def test_handoff_order_stable_by_step_id():
    d = build().to_dict()
    assert [s["step_id"] for s in d["handoffs"]] == sorted(s["step_id"] for s in d["handoffs"])


def test_planned_layers_stable():
    r = registry()
    p = preview(r)
    assert build(r, p).to_dict()["planned_layers"] == p.to_dict()["planned_layers"]


def test_branching_parallel_layers():
    steps = (
        MissionWorkflowStep("step.inventory", "workspace", "inventory"),
        MissionWorkflowStep("step.inspect", "engineering", "inspect_impact", ("step.inventory",)),
        MissionWorkflowStep("step.plan", "intelligence", "plan", ("step.inventory",)),
    )
    r = registry()
    bp = blueprint(steps=steps)
    c = MissionBridgeCatalog(tuple(sorted((
        MissionBridgePort("workspace", "inventory", "adapter.inventory", H1),
        MissionBridgePort("engineering", "inspect_impact", "adapter.inspect", H2),
        MissionBridgePort("intelligence", "plan", "adapter.plan", H3),
    ), key=lambda x: (x.component, x.operation))))
    d = build(r, preview(r, bp), c).to_dict()
    assert d["planned_layers"] == [["step.inventory"], ["step.inspect", "step.plan"]]


def test_byte_identical_on_windows_and_linux():
    d = build().to_dict()
    crlf = json.dumps(d, sort_keys=True).replace("\n", "\r\n")
    assert json.loads(crlf) == d
    assert verify_bridge_plan(json.loads(crlf))


def test_verified_on_original_snapshot():
    r = registry()
    d = build(r).to_dict()
    assert verify_bridge_plan(d, registry=r.snapshot())


def test_empty_adapter_catalog_has_stable_sha():
    c = MissionBridgeCatalog(())
    assert c.sha256 == MissionBridgeCatalog(()).sha256


def test_ports_cover_entire_supported_surface():
    supported = [
        ("workspace", "inventory"), ("workspace", "index"),
        ("intelligence", "plan"), ("intelligence", "reason"),
        ("engineering", "inspect_impact"), ("engineering", "propose_change"),
        ("engineering", "request_effect"),
        ("trust", "review_action"), ("trust", "review_transition"),
        ("proof", "collect_evidence"), ("proof", "verify_integrity"),
        ("lab", "evaluate"), ("lab", "summarize"),
        ("mission_control", "coordinate"), ("mission_control", "summarize_mission"),
        ("aegisgrid", "plan_energy"), ("aegisgrid", "simulate_energy"),
        ("aegisgrid", "request_effect"),
        ("live_forge", "draft_overlay"), ("live_forge", "plan_stream"),
        ("live_forge", "request_effect"),
    ]
    for component, operation in supported:
        assert MissionBridgePort(component, operation, "adapter.test", H1).to_dict()["metadata_only"] is True
