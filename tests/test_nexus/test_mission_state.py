"""Read-only Mission Control readiness contract and regression tests."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
import pytest

from aegisforge.nexus.mission_control.mission_registry import (
    NexusMissionRegistry, MissionRegistryLimits,
)
from aegisforge.nexus.mission_control.mission_state import (
    MISSION_STATE_SCHEMA, MissionTransitionRule, MissionStatePolicy,
    NexusMissionState, NexusMissionStateError, verify_assessment,
)

H1, H2, H3, H4 = "1" * 64, "2" * 64, "3" * 64, "4" * 64
T0 = "2026-10-28T00:00:00Z"

def at(n): return f"2026-10-28T00:00:{n:02d}Z"


def registry(*, product="nexus", mission_id="mission.alpha"):
    r = NexusMissionRegistry()
    r.register(mission_id, product=product, owner_id="owner.team",
               objective_sha256=H1, actor_id="owner.team", at_utc=T0)
    return r


def step(r, status, ident="mission.alpha", n=1):
    return r.transition(ident, status, actor_id="owner.team", at_utc=at(n),
                        decision_sha256=H4 if status == "verified" else None)


def ref(r, kind, digest=H2, ident="mission.alpha", n=3):
    return r.link_reference(ident, kind=kind, reference_sha256=digest,
                            actor_id="owner.team", at_utc=at(n))


def component(r, name, ident="mission.alpha", n=1):
    return r.assign_component(ident, component=name, actor_id="owner.team", at_utc=at(n))


def policy(target="ready", **kwargs):
    return MissionStatePolicy("mission.alpha", "nexus", (MissionTransitionRule(target, **kwargs),))


def gate(*args): return True


def assess(r=None, target="ready", p=None, host_checker=None):
    r = r if r is not None else registry()
    return NexusMissionState(p if p is not None else policy(target),
                             host_checker=host_checker).assess(r, target)


def to_complete():
    r = registry()
    step(r, "ready", n=1)
    step(r, "running", n=2)
    ref(r, "evidence", n=3)
    step(r, "reported_complete", n=4)
    return r


def make_dep(r, status="verified"):
    r.register("mission.dep", product="nexus", owner_id="owner.team",
               objective_sha256=H1, actor_id="owner.team", at_utc=T0)
    if status == "draft": return
    step(r, "ready", "mission.dep", 1)
    if status == "ready": return
    step(r, "running", "mission.dep", 2)
    if status == "running": return
    ref(r, "evidence", H2, "mission.dep", 3)
    step(r, "reported_complete", "mission.dep", 4)
    if status == "reported_complete": return
    ref(r, "approval", H3, "mission.dep", 5)
    step(r, "verified", "mission.dep", 6)


def assert_blocked(result, reason):
    assert result.outcome == "blocked"
    assert reason in result.reasons
    assert result.to_dict()["authorization_granted"] is False


def test_ready_advisory_only():
    result = assess()
    assert result.outcome == "ready_for_host_review"
    assert result.reasons == ()
    assert result.to_dict()["authorization_granted"] is False
    assert result.to_dict()["authenticity_verified"] is False
    assert result.to_dict()["effects_executed"] is False
    assert result.to_dict()["integrity_only"] is True
    assert verify_assessment(result.to_dict())


def test_no_mutations_of_registry():
    r = registry()
    old = r.snapshot()
    for _ in range(3): assess(r)
    assert r.snapshot() == old


def test_record_independent_immutable():
    result = assess()
    first = result.to_dict()
    first["reasons"].append("injected")
    assert "injected" not in result.to_dict()["reasons"]
    with pytest.raises(FrozenInstanceError): result._payload = b"changed"


def test_missing_policy_is_blocked():
    r = registry()
    p = policy("cancelled")
    assert_blocked(assess(r, p=p), "policy_missing")


def test_invalid_transition_is_blocked():
    assert_blocked(assess(target="verified", p=policy("verified")),
                   "transition_not_allowed")


def test_terminal_state_not_readiness():
    r = to_complete()
    ref(r, "approval", H3, n=5)
    step(r, "verified", n=6)
    assert_blocked(assess(r, target="ready", p=policy("ready")),
                   "transition_not_allowed")


def test_product_mismatch_fail_closed():
    r = registry(product="aegisgrid")
    assert_blocked(assess(r), "policy_product_mismatch")


def test_missing_component():
    p = policy(required_components=("engineering", "trust"))
    result = assess(p=p)
    assert_blocked(result, "missing_component:trust")
    assert result.to_dict()["missing_components"] == ["engineering", "trust"]


def test_components_added_allows_advisory():
    r = registry()
    component(r, "engineering")
    component(r, "trust")
    assert assess(r, p=policy(required_components=("engineering", "trust"))).outcome == "ready_for_host_review"


def test_missing_reference_kind():
    assert_blocked(assess(p=policy(required_reference_kinds=("run",))), "missing_reference:run")


def test_reference_supplied():
    r = registry()
    ref(r, "run", n=1)
    assert assess(r, p=policy(required_reference_kinds=("run",))).outcome == "ready_for_host_review"


def test_dependency_missing():
    result = assess(p=policy(dependencies=("mission.dep",)))
    assert_blocked(result, "dependency_missing:mission.dep")
    assert result.to_dict()["dependencies"] == [{"mission_id": "mission.dep", "status": None, "head_sha256": None}]


@pytest.mark.parametrize("status", ["draft", "ready", "running", "reported_complete"])
def test_dependency_not_verified(status):
    r = registry()
    make_dep(r, status)
    assert_blocked(assess(r, p=policy(dependencies=("mission.dep",))),
                   "dependency_not_verified:mission.dep")


def test_recorded_verified_dependency():
    r = registry()
    make_dep(r)
    result = assess(r, p=policy(dependencies=("mission.dep",)))
    assert result.outcome == "ready_for_host_review"
    assert result.to_dict()["dependencies"][0]["status"] == "verified"
    assert result.to_dict()["authenticity_verified"] is False


def test_running_requires_host_checker():
    r = registry()
    step(r, "ready")
    assert_blocked(assess(r, "running"), "host_checker_missing")


def test_running_callback_true():
    r = registry()
    step(r, "ready")
    result = assess(r, "running", host_checker=gate)
    assert result.outcome == "ready_for_host_review"
    assert result.to_dict()["authorization_granted"] is False


def test_running_callback_denied():
    r = registry()
    step(r, "ready")
    assert_blocked(assess(r, "running", host_checker=lambda *a: False),
                   "host_gate_not_satisfied")


@pytest.mark.parametrize("value", [None, 1, "yes", {}, [], object()])
def test_running_callback_truthy_not_true(value):
    r = registry()
    step(r, "ready")
    assert_blocked(assess(r, "running", host_checker=lambda *a: value),
                   "host_gate_not_satisfied")


def test_running_callback_failure_closed():
    r = registry()
    step(r, "ready")
    def boom(*args): raise RuntimeError("Synthetic provider failure")
    assert_blocked(assess(r, "running", host_checker=boom), "host_checker_unavailable")


def test_bind_callback_to_snapshot_policy_and_head():
    r = registry()
    step(r, "ready")
    p = policy("running")
    def callback(mission, target, head, registry_hash, policy_hash, approval_hash):
        assert mission == "mission.alpha" and target == "running"
        assert head == r.get("mission.alpha")["head_sha256"]
        assert registry_hash == r.snapshot()["registry_sha256"]
        assert policy_hash == p.sha256 and approval_hash is None
        return True
    assert assess(r, "running", p=p, host_checker=callback).outcome == "ready_for_host_review"


def test_host_checker_does_not_run_if_not_required():
    def fail(*args): raise AssertionError("should not run")
    assert assess(host_checker=fail).outcome == "ready_for_host_review"


def test_voluntary_host_review_in_ready_rule():
    assert_blocked(assess(p=policy(requires_host_review=True)), "host_checker_missing")
    assert assess(p=policy(requires_host_review=True), host_checker=gate).outcome == "ready_for_host_review"


def test_reported_completion_without_evidence():
    r = registry()
    step(r, "ready", n=1)
    step(r, "running", n=2)
    result = assess(r, "reported_complete")
    assert_blocked(result, "result_reference_missing")
    assert result.to_dict()["missing_references"] == ["result"]


@pytest.mark.parametrize("kind", ["evidence", "evaluation", "scorecard"])
def test_reported_completion_with_result(kind):
    r = registry()
    step(r, "ready", n=1)
    step(r, "running", n=2)
    ref(r, kind, n=3)
    assert assess(r, "reported_complete").outcome == "ready_for_host_review"


def test_verified_requires_gate_and_approval():
    r = to_complete()
    result = assess(r, "verified")
    assert_blocked(result, "host_checker_missing")
    result2 = assess(r, "verified", host_checker=gate)
    assert_blocked(result2, "approval_reference_missing")


def test_verified_success_is_still_advisory():
    r = to_complete()
    ref(r, "approval", H3, n=5)
    called = []
    def check(*args):
        called.append(args)
        return args[-1] == H3
    result = assess(r, "verified", host_checker=check)
    assert result.outcome == "ready_for_host_review"
    assert result.to_dict()["authorization_granted"] is False
    assert called[0][-1] == H3
    assert r.get("mission.alpha")["status"] == "reported_complete"


def test_verified_denied():
    r = to_complete()
    ref(r, "approval", H3, n=5)
    assert_blocked(assess(r, "verified", host_checker=lambda *a: False),
                   "host_gate_not_satisfied")


def test_multiple_approvals_check_each_until_first_valid():
    r = to_complete()
    ref(r, "approval", H2, n=5)
    ref(r, "approval", H3, n=6)
    calls = []
    def check(*args):
        calls.append(args[-1])
        return args[-1] == H3
    result = assess(r, "verified", host_checker=check)
    assert result.outcome == "ready_for_host_review"
    assert calls == [H2, H3]


def test_host_checker_edit_registry_marks_stale():
    r = registry()
    step(r, "ready", n=1)
    def change(*args):
        component(r, "trust", n=2)
        return True
    result = assess(r, "running", host_checker=change)
    assert_blocked(result, "registry_changed_during_assessment")
    assert result.to_dict()["registry_sha256"] != r.snapshot()["registry_sha256"]


def test_snapshot_advisory_works_without_live_object():
    r = registry()
    s = r.snapshot()
    result = assess(s)
    assert result.outcome == "ready_for_host_review" and r.snapshot() == s


def test_reject_invalid_hash():
    s = registry().snapshot()
    s["registry_sha256"] = H3
    with pytest.raises(NexusMissionStateError, match="invalid registry snapshot"):
        assess(s)


def test_reject_invalid_history_even_with_rehashed_outer_snapshot():
    s = registry().snapshot()
    s["missions"][0]["events"][0]["status"] = "verified"
    body = {k: v for k, v in s.items() if k != "registry_sha256"}
    s["registry_sha256"] = sha256(json.dumps(body, ensure_ascii=True,
             sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(NexusMissionStateError, match="invalid registry snapshot"):
        assess(s)


def test_reject_mission_not_present():
    r = NexusMissionRegistry()
    with pytest.raises(NexusMissionStateError, match="not present"):
        assess(r)


def test_snapshot_copied_and_not_mutated():
    s = registry().snapshot()
    original = deepcopy(s)
    assess(s)
    assert s == original


def test_policy_snapshot_limit_lower_than_content():
    r = NexusMissionRegistry(limits=MissionRegistryLimits(max_snapshot_bytes=10000))
    r.register("mission.alpha", product="nexus", owner_id="owner.team",
               objective_sha256=H1, actor_id="owner.team", at_utc=T0)
    gate_object = NexusMissionState(policy(), snapshot_limits=MissionRegistryLimits(max_snapshot_bytes=4096))
    assert gate_object.assess(r.snapshot(), "ready").outcome == "ready_for_host_review"


def test_assessment_hash_valid():
    report = assess().to_dict()
    assert report["schema"] == MISSION_STATE_SCHEMA
    assert len(report["assessment_sha256"]) == 64
    assert verify_assessment(report)


@pytest.mark.parametrize("field", ["outcome", "mission_id", "mission_head_sha256", "policy_sha256", "registry_sha256", "reasons", "dependencies", "authorization_granted"])
def test_tampered_report_detected(field):
    report = assess().to_dict()
    if field == "reasons": report[field].append("injected")
    elif field == "dependencies": report[field].append({"mission_id":"mission.foo","status":None,"head_sha256":None})
    elif field == "authorization_granted": report[field] = True
    elif field == "outcome": report[field] = "blocked"
    else: report[field] = "0" * 64
    assert not verify_assessment(report)


@pytest.mark.parametrize("bad", [None, "", 1, [], {}, True])
def test_invalid_report_inputs(bad):
    assert verify_assessment(bad) is False


def test_bad_outcome_even_with_rehash():
    report = assess().to_dict()
    report["outcome"] = "pretend_permission"
    body = {k: v for k, v in report.items() if k != "assessment_sha256"}
    report["assessment_sha256"] = sha256(json.dumps(body, sort_keys=True,
                    ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    assert not verify_assessment(report)


def test_bad_authorization_even_with_rehash():
    report = assess().to_dict()
    report["authorization_granted"] = True
    body = {k: v for k, v in report.items() if k != "assessment_sha256"}
    report["assessment_sha256"] = sha256(json.dumps(body, sort_keys=True,
                    ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    assert not verify_assessment(report)


def test_outcome_blocked_requires_reasons_even_rehash():
    report = assess().to_dict()
    report["outcome"] = "blocked"
    body = {k: v for k, v in report.items() if k != "assessment_sha256"}
    report["assessment_sha256"] = sha256(json.dumps(body, sort_keys=True,
                    ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    assert not verify_assessment(report)


@pytest.mark.parametrize("bad", [None, 1, "running", [MissionTransitionRule("ready")], ()])
def test_invalid_policy_rules(bad):
    with pytest.raises(NexusMissionStateError):
        MissionStatePolicy("mission.alpha", "nexus", bad)


@pytest.mark.parametrize("bad", ["bad/path", "A", "a", None, "", 1, []])
def test_invalid_policy_mission_id(bad):
    with pytest.raises(NexusMissionStateError):
        MissionStatePolicy(bad, "nexus", (MissionTransitionRule("ready"),))


@pytest.mark.parametrize("bad", ["other", [], None, 1, ""])
def test_invalid_policy_product(bad):
    with pytest.raises(NexusMissionStateError):
        MissionStatePolicy("mission.alpha", bad, (MissionTransitionRule("ready"),))


@pytest.mark.parametrize("bad", ["draft", "bogus", 1, None, []])
def test_invalid_rule_target(bad):
    with pytest.raises(NexusMissionStateError): MissionTransitionRule(bad)


def test_invalid_rule_order_and_duplicates():
    with pytest.raises(NexusMissionStateError):
        MissionStatePolicy("mission.alpha", "nexus", (
            MissionTransitionRule("running"), MissionTransitionRule("ready")))
    with pytest.raises(NexusMissionStateError):
        MissionStatePolicy("mission.alpha", "nexus", (
            MissionTransitionRule("ready"), MissionTransitionRule("ready")))


@pytest.mark.parametrize("kwargs", [
    {"required_components": ["trust"]},
    {"required_components": ("trust", "trust")},
    {"required_components": ("unknown",)},
    {"required_components": ("trust", "engineering")},
    {"required_reference_kinds": ("missing",)},
    {"required_reference_kinds": ["run"]},
    {"dependencies": ("mission.alpha", "mission.alpha")},
    {"dependencies": ("bad/path",)},
    {"dependencies": ["mission.dep"]},
    {"requires_host_review": 1},
])
def test_invalid_rule_options(kwargs):
    with pytest.raises(NexusMissionStateError): MissionTransitionRule("ready", **kwargs)


def test_self_dependency_policy_invalid():
    with pytest.raises(NexusMissionStateError):
        policy(dependencies=("mission.alpha",))


def test_disallows_untrusted_checker_type():
    with pytest.raises(NexusMissionStateError):
        NexusMissionState(policy(), host_checker="yes")


@pytest.mark.parametrize("target", [None, 1, [], "bogus", ""])
def test_invalid_requested_status(target):
    with pytest.raises(NexusMissionStateError): assess(target=target)


def test_non_registry_input_rejected():
    with pytest.raises(NexusMissionStateError): assess(3)


def test_policy_sha_stable():
    assert policy(required_components=("engineering", "trust")).sha256 == policy(required_components=("engineering", "trust")).sha256
    assert policy().sha256 != policy(required_components=("trust",)).sha256


def test_data_does_not_contain_sensitive_text():
    result = assess()
    dump = json.dumps(result.to_dict())
    assert "owner.team" not in dump
    assert "private_prompt" not in dump
    assert "C:\\Users" not in dump


def test_policy_cross_mission_bound():
    r = registry(mission_id="mission.beta")
    with pytest.raises(NexusMissionStateError, match="not present"):
        assess(r)


def test_missions_not_executed():
    r = registry()
    original = r.snapshot()
    result = assess(r)
    assert result.to_dict()["effects_executed"] is False
    assert r.snapshot() == original


def test_skip_host_check_when_missing_component():
    r = registry()
    step(r, "ready")
    called = []
    def checker(*args):
        called.append(args)
        return True
    result = assess(r, "running", p=policy("running", required_components=("trust",)),
                    host_checker=checker)
    assert_blocked(result, "missing_component:trust")
    assert called == []


def test_skip_host_check_when_missing_approval():
    r = to_complete()
    called = []
    def checker(*args):
        called.append(args)
        return True
    assert_blocked(assess(r, "verified", host_checker=checker), "approval_reference_missing")
    assert called == []


def test_skip_host_check_on_invalid_transition():
    called = []
    def checker(*args):
        called.append(args)
        return True
    assert_blocked(assess(target="running", host_checker=checker), "transition_not_allowed")
    assert called == []
