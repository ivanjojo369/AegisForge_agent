"""No-effect integration checks for mission action preflight.

Only synthetic temporary workspaces. Never execute tools, shell, Docker,
external requests, file mutations through the policy, or approvals.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_supervised_executor import (
    NexusMissionSupervisedExecutor, NexusSupervisedExecutorError,
    SUPERVISED_PLAN_SCHEMA, SUPERVISED_REPORT_SCHEMA,
    verify_supervised_plan, verify_supervised_report,
)
from aegisforge.nexus.workspace.manager import NexusWorkspaceManager
from aegisforge.nexus.workspace.workspace_policy import NexusWorkspacePolicy, WorkspaceRule
from aegisforge.nexus.trust.action_policy import NexusActionPolicy, ActionRule, ActionIntent

MISSION = "mission.supervised"
OTHER = "mission.other"
DIGEST = "a" * 64
ACTOR = "actor.demo"


def _seal(data, field):
    body = {k: v for k, v in data.items() if k != field}
    return {**body, field: sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=True, allow_nan=False).encode()).hexdigest()}


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "sample.py").write_bytes(b"value = 1\n")
    registry = NexusMissionRegistry()
    registry.register(MISSION, product="colossus", owner_id="owner.demo",
                      objective_sha256=DIGEST, actor_id=ACTOR)
    registry.assign_component(MISSION, component="mission_control", actor_id=ACTOR)
    registry.assign_component(MISSION, component="trust", actor_id=ACTOR)
    registry.transition(MISSION, "ready", actor_id=ACTOR)
    manager = NexusWorkspaceManager(root)
    wp = NexusWorkspacePolicy(manager, rules=[WorkspaceRule(
        MISSION, "sample.py", frozenset({"read_text", "apply_patch"}))],
        allow_mutations=True, approval_verifier=lambda action: False)
    policy = NexusActionPolicy(wp, rules=[
        ActionRule(MISSION, "inspector", "read_text", "sample.py"),
        ActionRule(MISSION, "editor", "apply_patch", "sample.py"),
    ])
    executor = NexusMissionSupervisedExecutor(registry, policy)
    return root, registry, policy, executor


def intent(operation="read_text", tool=None, *, mission=MISSION, path="sample.py", payload=None):
    return ActionIntent(mission, tool or ("editor" if operation == "apply_patch" else "inspector"),
                        operation, path, DIGEST, payload if payload is not None else {})


def prepare(env, intents=None):
    _, registry, _, ex = env
    return ex.prepare(MISSION, (intent(),) if intents is None else intents,
                      expected_registry_sha256=registry.snapshot()["registry_sha256"])


def test_prepare_read_only_plan(env):
    plan = prepare(env).to_dict()
    assert plan["schema"] == SUPERVISED_PLAN_SCHEMA
    assert plan["outcome"] == "read_only_prepared"
    assert plan["mission_id"] == MISSION
    assert verify_supervised_plan(plan)
    assert plan["actions"][0]["allowed"] is True
    assert plan["actions"][0]["policy_status"] == "allow_read_only"


def test_prepare_review_action_without_granting_auth(env):
    plan = prepare(env, (intent("apply_patch"),)).to_dict()
    assert plan["outcome"] == "review_required"
    assert plan["actions"][0]["approval_required"] is True
    assert plan["actions"][0]["allowed"] is False
    assert plan["authorization_granted"] is False


def test_prepare_denied_action_does_not_raise_secret(env):
    sensitive = "AKIA" + "Q" * 16
    read = intent(payload={"api_key": sensitive})
    plan = prepare(env, (read,)).to_dict()
    assert plan["outcome"] == "denied"
    assert sensitive not in json.dumps(plan)
    assert "api_key" not in json.dumps(plan)


def test_read_only_policy_does_not_consume_approval(env):
    _, _, policy, ex = env
    plan = prepare(env, (intent("apply_patch"),))
    assert ex.inspect(plan.session_id).outcome == "review_required"
    assert not policy.workspace_policy._consumed_action_ids


def test_inspect_does_not_finalize(env):
    _, _, _, ex = env
    plan = prepare(env)
    assert ex.inspect(plan.session_id).outcome == "read_only_prepared"
    assert ex.inspect(plan.session_id).outcome == "read_only_prepared"


def test_finalize_is_one_shot(env):
    _, _, _, ex = env
    plan = prepare(env)
    report = ex.finalize(plan.session_id)
    assert report.outcome == "read_only_prepared"
    with pytest.raises(NexusSupervisedExecutorError, match="already finalized"):
        ex.finalize(plan.session_id)


def test_finalize_no_files_changed(env):
    root, registry, _, ex = env
    before = (root / "sample.py").read_bytes()
    snapshot = registry.snapshot()
    plan = prepare(env, (intent("apply_patch"),))
    result = ex.finalize(plan.session_id).to_dict()
    assert (root / "sample.py").read_bytes() == before
    assert registry.snapshot() == snapshot
    assert result["effects_executed"] is False
    assert result["approval_consumed"] is False


def test_report_valid_and_matches_plan(env):
    _, _, _, ex = env
    plan = prepare(env).to_dict()
    report = ex.finalize(plan["session_id"]).to_dict()
    assert verify_supervised_report(report)
    assert verify_supervised_report(report, plan=plan)
    assert report["schema"] == SUPERVISED_REPORT_SCHEMA


def test_stale_after_registry_mutation(env):
    _, registry, _, ex = env
    plan = prepare(env)
    registry.link_reference(MISSION, kind="evidence", reference_sha256="c" * 64, actor_id=ACTOR)
    report = ex.inspect(plan.session_id).to_dict()
    assert report["outcome"] == "stale"
    assert "registry_changed" in report["reasons"]
    assert verify_supervised_report(report, plan=plan.to_dict())


def test_stale_after_policy_change(env):
    _, _, policy, ex = env
    plan = prepare(env)
    policy.rules = ()  # host-side change to rules
    report = ex.inspect(plan.session_id).to_dict()
    assert report["outcome"] == "stale"
    assert "policy_changed" in report["reasons"]


def test_stale_after_file_deleted(env):
    root, _, _, ex = env
    plan = prepare(env)
    (root / "sample.py").unlink()
    report = ex.inspect(plan.session_id).to_dict()
    assert report["outcome"] == "stale"
    assert "policy_decisions_changed" in report["reasons"]


def test_equal_byte_content_change_is_not_detected_by_policy_preflight(env):
    root, _, _, ex = env
    plan = prepare(env)
    (root / "sample.py").write_bytes(b"value = 2\n")
    report = ex.inspect(plan.session_id).to_dict()
    # Preflight is NOT content-integrity verification. Other modules own that.
    assert report["outcome"] == "read_only_prepared"


def test_old_anchor_rejected(env):
    _, _, _, ex = env
    with pytest.raises(NexusSupervisedExecutorError, match="anchor is stale"):
        ex.prepare(MISSION, (intent(),), expected_registry_sha256="0" * 64)


def test_registry_draft_rejected(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    registry = NexusMissionRegistry()
    registry.register(MISSION, product="nexus", owner_id="owner.demo",
                      objective_sha256=DIGEST, actor_id=ACTOR)
    manager = NexusWorkspaceManager(root)
    policy = NexusActionPolicy(NexusWorkspacePolicy(manager), rules=())
    ex = NexusMissionSupervisedExecutor(registry, policy)
    with pytest.raises(NexusSupervisedExecutorError, match="not ready"):
        ex.prepare(MISSION, (intent(),), expected_registry_sha256=registry.snapshot()["registry_sha256"])


def test_cross_mission_denied(env):
    _, _, _, ex = env
    with pytest.raises(NexusSupervisedExecutorError, match="mission mismatch"):
        prepare(env, (intent(mission=OTHER),))


def test_missing_mission_rejected(env):
    _, registry, _, ex = env
    with pytest.raises(NexusSupervisedExecutorError, match="registry unavailable"):
        ex.prepare(OTHER, (intent(mission=OTHER),), expected_registry_sha256=registry.snapshot()["registry_sha256"])


def test_duplicate_actions_rejected(env):
    with pytest.raises(NexusSupervisedExecutorError, match="duplicate action"):
        prepare(env, (intent(), intent()))


def test_capacity_and_forget(env):
    _, reg, policy, _ = env
    ex = NexusMissionSupervisedExecutor(reg, policy, max_sessions=1)
    anchor = reg.snapshot()["registry_sha256"]
    p = ex.prepare(MISSION, (intent(),), expected_registry_sha256=anchor)
    with pytest.raises(NexusSupervisedExecutorError, match="capacity"):
        ex.prepare(MISSION, (intent("apply_patch"),), expected_registry_sha256=anchor)
    ex.forget(p.session_id)
    assert ex.prepare(MISSION, (intent(),), expected_registry_sha256=anchor)


def test_forget_unknown_session(env):
    _, _, _, ex = env
    with pytest.raises(NexusSupervisedExecutorError, match="unknown"):
        ex.forget("0" * 32)


def test_untrusted_input_rejected(env):
    with pytest.raises(NexusSupervisedExecutorError, match="invalid intent"):
        prepare(env, (object(),))


def test_wrong_sha_rejected(env):
    _, reg, _, ex = env
    with pytest.raises(NexusSupervisedExecutorError, match="anchor required"):
        ex.prepare(MISSION, (intent(),), expected_registry_sha256="abc")


def test_empty_plan_rejected(env):
    with pytest.raises(NexusSupervisedExecutorError, match="action count"):
        prepare(env, ())


def test_many_actions_rejected(env):
    with pytest.raises(NexusSupervisedExecutorError, match="action count"):
        prepare(env, tuple(intent(path=f"f{i}.py") for i in range(33)))


def test_bad_intent_payload_rejected(env):
    with pytest.raises(NexusSupervisedExecutorError, match="invalid JSON"):
        prepare(env, (intent(payload={"object": object()}),))


def test_bad_intent_payload_nan_rejected(env):
    with pytest.raises(NexusSupervisedExecutorError, match="invalid JSON"):
        prepare(env, (intent(payload={"number": float("nan")}),))


def test_no_raw_paths_in_export(env):
    _, _, _, ex = env
    plan = prepare(env)
    report = ex.inspect(plan.session_id)
    assert "sample.py" not in json.dumps(plan.to_dict())
    assert "sample.py" not in json.dumps(report.to_dict())


def test_unique_sessions(env):
    a = prepare(env)
    b = prepare(env)
    assert a.session_id != b.session_id


@pytest.mark.parametrize("name,value", [
    ("authorization_granted", True), ("effects_executed", True),
    ("approval_consumed", True), ("authenticity_verified", True),
    ("integrity_only", False),
])
def test_plan_rehashed_flags_still_rejected(env, name, value):
    data = prepare(env).to_dict()
    data[name] = value
    assert not verify_supervised_plan(_seal(data, "plan_sha256"))


@pytest.mark.parametrize("name,value", [
    ("authorization_granted", True), ("effects_executed", True),
    ("approval_consumed", True), ("authenticity_verified", True),
    ("integrity_only", False),
])
def test_report_rehashed_flags_still_rejected(env, name, value):
    _, _, _, ex = env
    p = prepare(env)
    data = ex.inspect(p.session_id).to_dict()
    data[name] = value
    assert not verify_supervised_report(_seal(data, "report_sha256"), plan=p.to_dict())


@pytest.mark.parametrize("edit", [
    lambda d: d["actions"][0].update(index=5),
    lambda d: d["actions"][0].update(allowed=False),
    lambda d: d["actions"][0].update(policy_status="approved"),
    lambda d: d.update(outcome="review_required"),
    lambda d: d.update(action_count=10),
    lambda d: d.update(session_id="outside"),
    lambda d: d.update(mission_status="verified"),
])
def test_rehashed_structural_plan_tamper_denied(env, edit):
    data = prepare(env).to_dict()
    edit(data)
    assert not verify_supervised_plan(_seal(data, "plan_sha256"))


@pytest.mark.parametrize("edit", [
    lambda d: d["actions"][0].update(index=4),
    lambda d: d["actions"][0].update(allowed=False),
    lambda d: d.update(outcome="review_required"),
    lambda d: d.update(action_count=0),
    lambda d: d.update(reasons=["not_a_stale_reason"]),
    lambda d: d.update(session_id="garbage"),
])
def test_rehashed_structural_report_tamper_denied(env, edit):
    _, _, _, ex = env
    p = prepare(env)
    data = ex.inspect(p.session_id).to_dict()
    edit(data)
    assert not verify_supervised_report(_seal(data, "report_sha256"), plan=p.to_dict())


def test_rehash_cannot_change_plan_reference(env):
    _, _, _, ex = env
    p = prepare(env)
    data = ex.inspect(p.session_id).to_dict()
    data["plan_sha256"] = "b" * 64
    assert not verify_supervised_report(_seal(data, "report_sha256"), plan=p.to_dict())


def test_inspect_changes_to_denied_when_workspace_rule_removed(env):
    _, _, policy, ex = env
    p = prepare(env)
    policy.workspace_policy.rules = ()
    assert ex.inspect(p.session_id).outcome == "stale"


def test_one_payload_mutated_after_prepare_detected(env):
    payload = {"hello": 1}
    root, registry, policy, ex = env
    policy.rules = tuple(list(policy.rules) + [
        ActionRule(MISSION, "jsonreader", "read_text", "sample.py",
                   allowed_payload_keys=frozenset({"hello"}))])
    item = intent(tool="jsonreader", payload=payload)
    p = ex.prepare(MISSION, (item,), expected_registry_sha256=registry.snapshot()["registry_sha256"])
    payload["hello"] = 2
    assert ex.inspect(p.session_id).outcome == "stale"


def test_session_capacity_input_validation(env):
    _, reg, policy, _ = env
    for bad in (True, 0, -1, 129, "2", None):
        with pytest.raises(NexusSupervisedExecutorError, match="session capacity"):
            NexusMissionSupervisedExecutor(reg, policy, max_sessions=bad)


def test_restricted_effect_intent_has_no_grant_and_no_side_effect(env):
    root, reg, policy, ex = env
    before = reg.snapshot()
    p = prepare(env, (intent(), intent("apply_patch")))
    result = ex.finalize(p.session_id).to_dict()
    assert result["outcome"] == "review_required"
    assert all(result[k] is False for k in ("authorization_granted", "effects_executed", "approval_consumed"))
    assert reg.snapshot() == before
    assert (root / "sample.py").read_bytes() == b"value = 1\n"


def test_reject_empty_mission_components_for_effects(env):
    _, reg, policy, _ = env
    reg2 = NexusMissionRegistry()
    reg2.register(MISSION, product="colossus", owner_id="owner.demo", objective_sha256=DIGEST, actor_id=ACTOR)
    reg2.assign_component(MISSION, component="mission_control", actor_id=ACTOR)
    reg2.transition(MISSION, "ready", actor_id=ACTOR)
    ex = NexusMissionSupervisedExecutor(reg2, policy)
    with pytest.raises(NexusSupervisedExecutorError, match="trust component required"):
        ex.prepare(MISSION, (intent("apply_patch"),), expected_registry_sha256=reg2.snapshot()["registry_sha256"])


def test_does_not_offer_arbitrary_callable_dispatch(env):
    _, _, _, ex = env
    assert not hasattr(ex, "dispatch")
    assert not hasattr(ex, "execute")
    assert not hasattr(ex, "run_shell")


def test_prepare_fails_closed_when_policy_raises(env):
    _, _, policy, ex = env
    policy.evaluate = lambda intent: (_ for _ in ()).throw(RuntimeError("private path"))
    with pytest.raises(NexusSupervisedExecutorError, match="preflight failed") as info:
        prepare(env)
    assert "private path" not in str(info.value)


def test_invalid_session_input(env):
    _, _, _, ex = env
    for bad in (None, "../secret", "x" * 32, 123):
        with pytest.raises(NexusSupervisedExecutorError, match="session identifier"):
            ex.inspect(bad)


def test_frozen_response_no_leaky_mutability(env):
    _, _, _, ex = env
    p = prepare(env)
    body = p.to_dict()
    body["actions"].clear()
    assert verify_supervised_plan(p.to_dict())
    r = ex.inspect(p.session_id)
    altered = r.to_dict()
    altered["actions"].clear()
    assert verify_supervised_report(r.to_dict(), plan=p.to_dict())


def test_explicit_stale_report_still_no_effects(env):
    _, registry, _, ex = env
    p = prepare(env, (intent("apply_patch"),))
    registry.transition(MISSION, "running", actor_id=ACTOR)
    r = ex.finalize(p.session_id).to_dict()
    assert r["outcome"] == "stale"
    assert r["effects_executed"] is False
    assert verify_supervised_report(r, plan=p.to_dict())
