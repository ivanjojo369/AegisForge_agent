"""Read-only Mission Control coordination regression tests."""
from __future__ import annotations

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
    MISSION_COORDINATION_SCHEMA, MissionWorkflowStep, MissionCoordinationBlueprint,
    NexusMissionCoordinator, NexusMissionCoordinatorError,
    verify_coordination_preview,
)

H1, H2, H3 = "1" * 64, "2" * 64, "3" * 64


def registry(product="nexus", components=("workspace", "intelligence", "engineering", "trust", "proof")):
    r = NexusMissionRegistry()
    r.register("mission.alpha", product=product, owner_id="team.dev",
               objective_sha256=H1, actor_id="team.dev",
               at_utc="2026-10-28T00:00:00Z")
    for ix, component in enumerate(components, start=1):
        r.assign_component("mission.alpha", component=component, actor_id="team.dev",
                           at_utc=f"2026-10-28T00:00:{ix:02d}Z")
    return r


def blueprint(steps=None, *, product="nexus", target="ready", mission="mission.alpha"):
    if steps is None:
        steps = (
            MissionWorkflowStep("step.inventory", "workspace", "inventory"),
            MissionWorkflowStep("step.plan", "intelligence", "plan", ("step.inventory",)),
            MissionWorkflowStep("step.propose", "engineering", "propose_change", ("step.plan",)),
            MissionWorkflowStep("step.trust", "trust", "review_action", ("step.propose",)),
            MissionWorkflowStep("step.effect", "engineering", "request_effect", ("step.trust",), H2),
            MissionWorkflowStep("step.proof", "proof", "collect_evidence", ("step.effect",)),
        )
    return MissionCoordinationBlueprint(mission, product, target, tuple(steps))


def state(product="nexus", target="ready", **rule_kwargs):
    p = MissionStatePolicy("mission.alpha", product,
                           (MissionTransitionRule(target, **rule_kwargs),))
    return NexusMissionState(p)


def preview(r=None, bp=None, st=None):
    r = r if r is not None else registry()
    bp = bp if bp is not None else blueprint()
    st = st if st is not None else state()
    return NexusMissionCoordinator(bp, st).preview(r)


def make_step(component="engineering", operation="propose_change", step_id="step.alpha",
              depends_on=(), intent=None):
    return MissionWorkflowStep(step_id, component, operation, depends_on, intent)


def assert_invalid_step(*args, **kwargs):
    with pytest.raises(NexusMissionCoordinatorError):
        make_step(*args, **kwargs)


def mutate_and_rehash(report, change):
    c = deepcopy(report)
    change(c)
    c["coordination_sha256"] = sha256(json.dumps({k: v for k, v in c.items()
                                                   if k != "coordination_sha256"},
                                                  sort_keys=True, separators=(",", ":"),
                                                  ensure_ascii=True, allow_nan=False)
                                           .encode()).hexdigest()
    return c


def test_happy_path_is_advisory_only():
    p = preview()
    d = p.to_dict()
    assert p.outcome == "host_review_required"
    assert p.reasons == ("host_review_required",)
    assert verify_coordination_preview(d)
    assert d["schema"] == MISSION_COORDINATION_SCHEMA
    assert d["integrity_only"] is True
    assert d["authenticity_verified"] is False
    assert d["authorization_granted"] is False
    assert d["effects_executed"] is False
    assert d["required_action_reviews"] == [{"step_id": "step.effect", "intent_sha256": H2}]
    assert d["planned_layers"] == [["step.inventory"], ["step.plan"], ["step.propose"],
                                    ["step.trust"], ["step.effect"], ["step.proof"]]


def test_non_mutating_live_registry():
    r = registry()
    before = r.snapshot()
    preview(r)
    assert r.snapshot() == before
    assert r.get("mission.alpha")["status"] == "draft"


def test_identical_preview_has_same_hash():
    r = registry()
    a = preview(r).to_dict()
    b = preview(r).to_dict()
    assert a == b
    assert len(a["coordination_sha256"]) == 64


def test_copy_is_immutable():
    p = preview()
    d = p.to_dict()
    d["reasons"].append("injected")
    d["planned_layers"].clear()
    assert p.reasons == ("host_review_required",)
    with pytest.raises(FrozenInstanceError):
        p._payload = b"injected"


def test_blueprint_hash_does_not_depend_on_input_step_order():
    b = blueprint()
    rev = blueprint(tuple(reversed(b.steps)))
    assert b.sha256 == rev.sha256
    assert b.to_dict() == rev.to_dict()
    assert b.planned_layers == rev.planned_layers


def test_independent_steps_share_layer():
    bp = blueprint((make_step(step_id="step.zed"),
                    make_step(component="proof", operation="collect_evidence",
                              step_id="step.aaa")))
    assert bp.planned_layers == (("step.aaa", "step.zed"),)
    r = registry()
    assert NexusMissionCoordinator(bp, state()).preview(r).outcome == "host_review_required"


def test_missing_component_blocks():
    r = registry(components=("workspace", "intelligence", "engineering", "trust"))
    d = preview(r).to_dict()
    assert d["outcome"] == "blocked"
    assert d["missing_components"] == ["proof"]
    assert d["reasons"] == ["component_missing:proof"]
    assert verify_coordination_preview(d)


def test_multiple_missing_components_sorted():
    r = registry(components=("engineering", "trust"))
    d = preview(r).to_dict()
    assert d["outcome"] == "blocked"
    assert d["missing_components"] == ["intelligence", "proof", "workspace"]
    assert verify_coordination_preview(d)


def test_blocked_state_reflected_in_preview():
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.dev", at_utc="2026-10-28T00:00:10Z")
    d = preview(r).to_dict()
    assert d["outcome"] == "blocked"
    assert "state_blocked" in d["reasons"]
    assert "state:transition_not_allowed" in d["reasons"]
    assert verify_coordination_preview(d)


def test_blocked_state_plus_missing_components():
    r = registry(components=("engineering",))
    r.transition("mission.alpha", "ready", actor_id="team.dev", at_utc="2026-10-28T00:00:10Z")
    d = preview(r).to_dict()
    assert "state_blocked" in d["reasons"]
    assert "component_missing:trust" in d["reasons"]
    assert verify_coordination_preview(d)


def test_registry_snapshot_input():
    r = registry()
    assert preview(r.snapshot()).to_dict() == preview(r).to_dict()


def test_tampered_registry_snapshot_rejected():
    snap = registry().snapshot()
    snap["missions"][0]["status"] = "verified"
    with pytest.raises(NexusMissionCoordinatorError, match="invalid registry"):
        preview(snap)


def test_wrong_mission_throws():
    with pytest.raises(NexusMissionCoordinatorError, match="mission missing"):
        NexusMissionCoordinator(blueprint(mission="mission.alpha"), state()).preview(
            NexusMissionRegistry().snapshot())


def test_policy_wrong_mission_rejected():
    p = MissionStatePolicy("mission.other", "nexus", (MissionTransitionRule("ready"),))
    with pytest.raises(NexusMissionCoordinatorError, match="disagree"):
        NexusMissionCoordinator(blueprint(), NexusMissionState(p))


def test_policy_wrong_product_rejected():
    with pytest.raises(NexusMissionCoordinatorError, match="disagree"):
        NexusMissionCoordinator(blueprint(), state(product="aegisgrid"))


def test_policy_wrong_target_rejected():
    with pytest.raises(NexusMissionCoordinatorError, match="requested target"):
        NexusMissionCoordinator(blueprint(), state(target="cancelled"))


def test_registry_wrong_product_blocks():
    r = registry(product="aegisgrid")
    d = preview(r).to_dict()
    assert d["outcome"] == "blocked"
    assert "state:policy_product_mismatch" in d["reasons"]


def test_effect_requires_trust_ancestor_not_peer():
    with pytest.raises(NexusMissionCoordinatorError, match="upstream Trust"):
        blueprint((make_step("trust", "review_action", "step.review"),
                   make_step("engineering", "request_effect", "step.effect", intent=H2)))


def test_effect_requires_review_not_any_trust_step():
    with pytest.raises(NexusMissionCoordinatorError, match="upstream Trust"):
        blueprint((make_step("trust", "review_transition", "step.review"),
                   make_step("engineering", "request_effect", "step.effect",
                             ("step.review",), H2)))


def test_effect_requires_trust_ancestor_not_sibling():
    with pytest.raises(NexusMissionCoordinatorError, match="upstream Trust"):
        blueprint((make_step("trust", "review_action", "step.review"),
                   make_step("engineering", "propose_change", "step.propose"),
                   make_step("engineering", "request_effect", "step.effect",
                             ("step.propose",), H2)))


def test_transitive_review_works():
    bp = blueprint((make_step("trust", "review_action", "step.review"),
                    make_step("proof", "verify_integrity", "step.verify", ("step.review",)),
                    make_step("engineering", "request_effect", "step.effect", ("step.verify",), H2)))
    r = registry(components=("trust", "proof", "engineering"))
    assert NexusMissionCoordinator(bp, state()).preview(r).outcome == "host_review_required"


def test_multiple_effects_each_require_review():
    with pytest.raises(NexusMissionCoordinatorError, match="upstream Trust"):
        blueprint((make_step("trust", "review_action", "step.review"),
                   make_step("engineering", "request_effect", "step.effect", ("step.review",), H2),
                   make_step("live_forge", "request_effect", "step.other", intent=H3)))


def test_two_effects_both_linked():
    bp = blueprint((make_step("trust", "review_action", "step.review"),
                    make_step("engineering", "request_effect", "step.first", ("step.review",), H2),
                    make_step("aegisgrid", "request_effect", "step.second", ("step.review",), H3)),
                   product="colossus")
    r = registry(product="colossus", components=("trust", "engineering", "aegisgrid"))
    p = MissionStatePolicy("mission.alpha", "colossus", (MissionTransitionRule("ready"),))
    d = NexusMissionCoordinator(bp, NexusMissionState(p)).preview(r).to_dict()
    assert len(d["required_action_reviews"]) == 2
    assert d["authorization_granted"] is False
    assert d["effects_executed"] is False


@pytest.mark.parametrize("value", ["", "x", "bad space", "../escape", "UPPER", "0start", 12, None,
                                    "x" * 65, "a/b/c", "a\nnext", "a" * 10000])
def test_invalid_step_id(value):
    assert_invalid_step(step_id=value)


@pytest.mark.parametrize("component", ["", "unknown", "TRUST", 3, None, "trust/inject"])
def test_invalid_component(component):
    assert_invalid_step(component=component)


@pytest.mark.parametrize("component,operation", [
    ("engineering", "review_action"), ("trust", "request_effect"),
    ("lab", "execute"), ("workspace", "request_effect"),
    ("intelligence", "request_effect"), ("proof", "evaluate"),
    ("aegisgrid", "draft_overlay"), ("live_forge", "simulate_energy"),
    ("mission_control", "authorize"), ("engineering", 4),
])
def test_invalid_component_operation(component, operation):
    assert_invalid_step(component=component, operation=operation)


@pytest.mark.parametrize("deps", [
    ["step.dep"], ("step.dep", "step.dep"), ("step.alpha",),
    ("step.zed", "step.aaa"), ("unknown", None),
    tuple(f"step.{i:03d}" for i in range(13)),
])
def test_invalid_dependencies(deps):
    assert_invalid_step(depends_on=deps)


@pytest.mark.parametrize("digest", [None, "1" * 63, "F" * 64, "anything", 42, True])
def test_invalid_effect_digest(digest):
    assert_invalid_step(component="engineering", operation="request_effect", intent=digest)


def test_non_effect_digest_rejected():
    assert_invalid_step(intent=H2)


def test_duplicate_steps_rejected():
    with pytest.raises(NexusMissionCoordinatorError, match="duplicate"):
        blueprint((make_step(), make_step()))


def test_unknown_dependency_rejected():
    with pytest.raises(NexusMissionCoordinatorError, match="unknown dependency"):
        blueprint((make_step(depends_on=("step.missing",)),))


def test_cycle_rejected():
    with pytest.raises(NexusMissionCoordinatorError, match="cycle"):
        blueprint((make_step(step_id="step.one", depends_on=("step.two",)),
                   make_step(step_id="step.two", depends_on=("step.one",))))


def test_over_64_steps_rejected():
    steps = tuple(make_step(step_id=f"step.{i:03d}") for i in range(65))
    with pytest.raises(NexusMissionCoordinatorError, match="bounded"):
        blueprint(steps)


def test_bad_workflow_steps_container_rejected():
    with pytest.raises(NexusMissionCoordinatorError):
        MissionCoordinationBlueprint("mission.alpha", "nexus", "ready", [make_step()])


def test_bad_workflow_entry_rejected():
    with pytest.raises(NexusMissionCoordinatorError):
        blueprint(("step.alpha",))


@pytest.mark.parametrize("target", ["draft", "unknown", None, 4])
def test_invalid_target(target):
    with pytest.raises(NexusMissionCoordinatorError):
        blueprint(target=target)


@pytest.mark.parametrize("product", ["bogus", "", None, False])
def test_invalid_product(product):
    with pytest.raises(NexusMissionCoordinatorError):
        blueprint(product=product)


@pytest.mark.parametrize("ident", ["no", "../bad", "BAD", 7])
def test_invalid_mission_id(ident):
    with pytest.raises(NexusMissionCoordinatorError):
        blueprint(mission=ident)


def test_non_json_blob_rejected():
    with pytest.raises(NexusMissionCoordinatorError):
        preview(["not-a-snapshot"])


def test_no_bound_host_checker_for_running_blocks():
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.dev", at_utc="2026-10-28T00:00:10Z")
    bp = blueprint(target="running")
    st = state(target="running")
    d = NexusMissionCoordinator(bp, st).preview(r).to_dict()
    assert d["outcome"] == "blocked"
    assert "state:host_checker_missing" in d["reasons"]


def test_host_checker_not_permission_even_if_true():
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.dev", at_utc="2026-10-28T00:00:10Z")
    bp = blueprint(target="running")
    p = MissionStatePolicy("mission.alpha", "nexus", (MissionTransitionRule("running"),))
    calls = []
    def checker(*args):
        calls.append(args)
        return True
    d = NexusMissionCoordinator(bp, NexusMissionState(p, host_checker=checker)).preview(r).to_dict()
    assert len(calls) == 1
    assert d["outcome"] == "host_review_required"
    assert d["authorization_granted"] is False
    assert d["effects_executed"] is False


def test_host_checker_not_called_when_component_requirement_missing():
    r = registry(components=("engineering", "trust", "proof"))
    r.transition("mission.alpha", "ready", actor_id="team.dev", at_utc="2026-10-28T00:00:10Z")
    p = MissionStatePolicy("mission.alpha", "nexus", (
        MissionTransitionRule("running", required_components=("workspace",)),))
    calls = []
    def checker(*args):
        calls.append(args)
        return True
    d = NexusMissionCoordinator(blueprint(target="running"),
                                NexusMissionState(p, host_checker=checker)).preview(r).to_dict()
    assert d["outcome"] == "blocked"
    assert calls == []


def test_live_change_during_checker_fails_closed():
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.dev", at_utc="2026-10-28T00:00:10Z")
    bp = blueprint(target="running")
    p = MissionStatePolicy("mission.alpha", "nexus", (MissionTransitionRule("running"),))
    def checker(*args):
        r.link_reference("mission.alpha", kind="artifact", reference_sha256=H3,
                         actor_id="team.dev", at_utc="2026-10-28T00:00:11Z")
        return True
    d = NexusMissionCoordinator(bp, NexusMissionState(p, host_checker=checker)).preview(r).to_dict()
    assert d["outcome"] == "blocked"
    assert "registry_changed_during_preview" in d["reasons"]
    assert verify_coordination_preview(d)


def test_host_exception_blocks():
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.dev", at_utc="2026-10-28T00:00:10Z")
    def checker(*args):
        raise RuntimeError("unsafe")
    p = MissionStatePolicy("mission.alpha", "nexus", (MissionTransitionRule("running"),))
    d = NexusMissionCoordinator(blueprint(target="running"),
                                NexusMissionState(p, host_checker=checker)).preview(r).to_dict()
    assert d["outcome"] == "blocked"
    assert "state:host_checker_unavailable" in d["reasons"]


@pytest.mark.parametrize("field,value", [
    ("authorization_granted", True), ("effects_executed", True),
    ("authenticity_verified", True), ("integrity_only", False),
    ("outcome", "ready_to_execute"), ("mission_id", "mission.fake"),
    ("registry_sha256", "0" * 64), ("mission_head_sha256", "0" * 64),
    ("policy_sha256", "0" * 64), ("state_assessment_sha256", "0" * 64),
    ("blueprint_sha256", "0" * 64), ("schema", "fake"),
    ("planned_layers", [["step.proof"]]),
    ("required_action_reviews", []), ("missing_components", ["lab"]),
    ("reasons", []),
])
def test_rehashed_tampering_detected(field, value):
    original = preview().to_dict()
    forged = mutate_and_rehash(original, lambda r: r.__setitem__(field, value))
    assert not verify_coordination_preview(forged)


def test_blueprint_structural_tampering_rehashed():
    original = preview().to_dict()
    def tamper(r):
        r["blueprint"]["steps"][1]["depends_on"] = ["step.effect"]
        r["blueprint_sha256"] = sha256(json.dumps(r["blueprint"], sort_keys=True,
                                                  separators=(",", ":"),
                                                  ensure_ascii=True).encode()).hexdigest()
    forged = mutate_and_rehash(original, tamper)
    assert not verify_coordination_preview(forged)


def test_rehashed_but_structurally_valid_report_not_authentication():
    original = preview().to_dict()
    assert original["authenticity_verified"] is False
    assert original["authorization_granted"] is False


def test_invalid_preview_shapes():
    assert not verify_coordination_preview(None)
    assert not verify_coordination_preview({})
    assert not verify_coordination_preview([])
    assert not verify_coordination_preview("a")


def test_extra_field_rejected():
    d = preview().to_dict()
    d["credentials"] = "x"
    assert not verify_coordination_preview(d)


def test_remove_field_rejected():
    d = preview().to_dict()
    d.pop("blueprint")
    assert not verify_coordination_preview(d)


def test_invalid_blueprint_type_in_preview():
    d = preview().to_dict()
    d["blueprint"] = []
    assert not verify_coordination_preview(d)


def test_all_products_covered():
    examples = {
        "nexus": ("engineering", "propose_change"),
        "aegisgrid": ("aegisgrid", "plan_energy"),
        "live_forge": ("live_forge", "draft_overlay"),
        "colossus": ("mission_control", "coordinate"),
    }
    for product, (component, operation) in examples.items():
        bp = blueprint((make_step(component, operation),), product=product)
        r = registry(product=product, components=(component,))
        p = MissionStatePolicy("mission.alpha", product,
                               (MissionTransitionRule("ready"),))
        assert NexusMissionCoordinator(bp, NexusMissionState(p)).preview(r).outcome == "host_review_required"


def test_no_effect_intent_or_action_output_in_regular_steps():
    bp = blueprint((make_step("lab", "evaluate"),))
    r = registry(components=("lab",))
    d = NexusMissionCoordinator(bp, state()).preview(r).to_dict()
    assert d["required_action_reviews"] == []
    assert d["blueprint"]["steps"][0]["intent_sha256"] is None
    assert d["effects_executed"] is False
