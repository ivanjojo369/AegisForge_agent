"""Offline integration checks for sandbox bridge. NO Docker execution or real review."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_supervised_executor import NexusMissionSupervisedExecutor
from aegisforge.nexus.mission_control.mission_sandbox_execution_bridge import (
    NexusMissionSandboxExecutionBridge, NexusMissionSandboxBridgeError,
    SANDBOX_BRIDGE_SCHEMA,
    verify_sandbox_bridge_receipt,
)
from aegisforge.nexus.workspace.manager import NexusWorkspaceManager
from aegisforge.nexus.workspace.workspace_policy import NexusWorkspacePolicy, WorkspaceRule
from aegisforge.nexus.trust.action_policy import NexusActionPolicy, ActionRule
from aegisforge.nexus.trust.approval_gate import NexusApprovalGate, ApprovalAttestation
from aegisforge.nexus.trust.sandbox_runner import (
    NexusSandboxRunner, NexusSandboxBackend, SandboxOutcome,
)

MISSION = "mission.sandbox"
DIGEST = "a" * 64


class StubSandbox(NexusSandboxBackend):
    """Nonexecuting fixture. Must NEVER be admitted to production effect gate."""
    def identity(self):
        return {"backend": "synthetic-test-only"}

    def preflight(self, live_root, path, source_sha256, limits):
        assert path in {"safe.py", "test_safe.py"}

    def execute(self, recipe, path, limits):
        raise AssertionError("bridge MUST NOT execute this synthetic backend")


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "safe.py").write_bytes(b"value = 1\n")
    (root / "test_safe.py").write_bytes(b"def test_safe():\n    assert True\n")
    reg = NexusMissionRegistry()
    reg.register(MISSION, product="colossus", owner_id="owner.demo",
                 objective_sha256=DIGEST, actor_id="actor.demo")
    for comp in ("mission_control", "trust"):
        reg.assign_component(MISSION, component=comp, actor_id="actor.demo")
    reg.transition(MISSION, "ready", actor_id="actor.demo")
    manager = NexusWorkspaceManager(root)
    wp = NexusWorkspacePolicy(manager, rules=[
        WorkspaceRule(MISSION, ".", frozenset({"read_text", "execute_tool", "run_tests"}))
    ], allow_mutations=True, approval_verifier=lambda _: False)
    action = NexusActionPolicy(wp, rules=[
        ActionRule(MISSION, "sandbox.tool", "execute_tool", ".",
                   recipes=frozenset({"python_ast", "node_check"})),
        ActionRule(MISSION, "sandbox.tool", "run_tests", ".",
                   recipes=frozenset({"pytest_file", "node_test_file"})),
    ])
    sup = NexusMissionSupervisedExecutor(reg, action)
    runner = NexusSandboxRunner(wp, backend=StubSandbox())
    # Synthetic gate only. Not wired to WorkspacePolicy; no effect authority.
    holder = {}
    def binding_provider(action):
        return runner.binding_for(holder["plan"])
    gate = NexusApprovalGate(root, reviewer=lambda req: ApprovalAttestation(
        req.request_sha256, "fixture_reviewer", "synthetic_review_1"),
        binding_provider=binding_provider)
    bridge = NexusMissionSandboxExecutionBridge(
        reg, action, sup, runner, approval_gate=gate)
    return root, reg, sup, runner, gate, bridge, holder


def staged(env, recipe="python_ast", path="safe.py", mission=MISSION, tool="sandbox.tool"):
    _, reg, sup, runner, gate, bridge, holder = env
    receipt = bridge.stage(mission, recipe, path, tool_id=tool,
                           expected_registry_sha256=reg.snapshot()["registry_sha256"])
    holder["plan"] = bridge._staged[receipt["session_id"]].sandbox
    return receipt


def test_stage_receipt_sanitized(setup):
    result = staged(setup)
    assert result["status"] == "review_required"
    assert verify_sandbox_bridge_receipt(result)
    assert result["effects_attempted"] is False
    assert result["approval_consumed"] is False
    assert "safe.py" not in json.dumps(result)
    assert "value = 1" not in json.dumps(result)


def test_stage_does_not_change_file_or_registry(setup):
    root, reg, *_ = setup
    before = reg.snapshot()
    content = (root / "safe.py").read_bytes()
    staged(setup)
    assert reg.snapshot() == before
    assert (root / "safe.py").read_bytes() == content


def test_stage_inspect_repeatable(setup):
    r = staged(setup)
    bridge = setup[5]
    assert bridge.inspect(r["session_id"]) == bridge.inspect(r["session_id"])


def test_review_request_inert(setup):
    r = staged(setup)
    _, _, _, _, gate, bridge, _ = setup
    req = bridge.request_review(r["session_id"])
    assert req["status"] == "review_required"
    assert gate.status(req["request_id"]).status == "pending"
    assert bridge.inspect(r["session_id"])["status"] == "approval_pending"
    assert not bridge._staged[r["session_id"]].consumed


def test_review_called_only_explicitly_by_host_fixture(setup):
    r = staged(setup)
    gate, bridge = setup[4], setup[5]
    req = bridge.request_review(r["session_id"])
    assert gate.status(req["request_id"]).status == "pending"
    assert gate.review(req["request_id"]).status == "approved"
    assert bridge.inspect(r["session_id"])["status"] == "approval_pending"


def test_production_execution_disabled_even_with_approval(setup):
    r = staged(setup)
    gate, bridge = setup[4], setup[5]
    req = bridge.request_review(r["session_id"])
    gate.review(req["request_id"])
    with pytest.raises(NexusMissionSandboxBridgeError, match="disabled"):
        bridge.run_approved(r["session_id"])
    assert gate.status(req["request_id"]).status == "denied"
    assert bridge._staged[r["session_id"]].consumed


def test_synthetic_backend_cannot_execute_even_if_flag_changed(setup):
    r = staged(setup)
    gate, bridge = setup[4], setup[5]
    req = bridge.request_review(r["session_id"])
    gate.review(req["request_id"])
    bridge.effects_enabled = True  # emulate misconfigured host
    with pytest.raises(NexusMissionSandboxBridgeError, match="Docker"):
        bridge.run_approved(r["session_id"])
    assert gate.status(req["request_id"]).status == "denied"


def test_request_review_repeated_rejected(setup):
    r = staged(setup)
    bridge = setup[5]
    bridge.request_review(r["session_id"])
    with pytest.raises(NexusMissionSandboxBridgeError):
        bridge.request_review(r["session_id"])


def test_after_registry_mutation_stage_stale(setup):
    r = staged(setup)
    reg, bridge = setup[1], setup[5]
    reg.link_reference(MISSION, kind="evidence", reference_sha256="b" * 64, actor_id="actor.demo")
    assert bridge.inspect(r["session_id"])["status"] == "stale"
    with pytest.raises(NexusMissionSandboxBridgeError, match="changed"):
        bridge.request_review(r["session_id"])


def test_source_byte_change_stale_on_inspection(setup):
    r = staged(setup)
    root, bridge = setup[0], setup[5]
    (root / "safe.py").write_bytes(b"value = 2\n")
    assert bridge.inspect(r["session_id"])["status"] == "stale"


def test_backend_configuration_change_stale_on_inspection(setup):
    r = staged(setup)
    runner, bridge = setup[3], setup[5]
    runner.backend = None
    assert bridge.inspect(r["session_id"])["status"] == "stale"


def test_source_byte_change_stale_on_request(setup):
    r = staged(setup)
    root, bridge = setup[0], setup[5]
    (root / "safe.py").write_bytes(b"value = 2\n")
    with pytest.raises(NexusMissionSandboxBridgeError, match="unavailable"):
        bridge.request_review(r["session_id"])


def test_source_removed_stale_on_request(setup):
    r = staged(setup)
    root, bridge = setup[0], setup[5]
    (root / "safe.py").unlink()
    with pytest.raises(NexusMissionSandboxBridgeError):
        bridge.request_review(r["session_id"])


def test_cross_mission_rejected(setup):
    with pytest.raises(NexusMissionSandboxBridgeError):
        staged(setup, mission="other.mission")


def test_unregistered_recipe_rejected(setup):
    with pytest.raises(NexusMissionSandboxBridgeError):
        staged(setup, recipe="shell_command")


def test_mismatched_extension_rejected(setup):
    with pytest.raises(NexusMissionSandboxBridgeError):
        staged(setup, recipe="node_check")


def test_recipe_pytest_fixture(setup):
    r = staged(setup, recipe="pytest_file", path="test_safe.py")
    assert verify_sandbox_bridge_receipt(r)


def test_wrong_tool_denied(setup):
    with pytest.raises(NexusMissionSandboxBridgeError):
        staged(setup, tool="malicious.runner")


def test_path_traversal_rejected(setup):
    with pytest.raises(NexusMissionSandboxBridgeError):
        staged(setup, path="../outside.py")


def test_absolute_path_rejected(setup):
    with pytest.raises(NexusMissionSandboxBridgeError):
        staged(setup, path="/etc/passwd")


def test_receipt_forgery_detected(setup):
    r = staged(setup)
    for key, newval in [("effects_attempted", True), ("approval_consumed", True),
                        ("status", "sandbox_passed"), ("authenticity_verified", True),
                        ("sandbox_plan_sha256", "0" * 64)]:
        c = deepcopy(r)
        c[key] = newval
        assert verify_sandbox_bridge_receipt(c) is False


def test_receipt_rehash_flags_cannot_claim_real_authority(setup):
    r = staged(setup)
    c = deepcopy(r)
    c["authenticity_verified"] = True
    c["receipt_sha256"] = sha256(json.dumps(
        {k: v for k, v in c.items() if k != "receipt_sha256"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()).hexdigest()
    assert not verify_sandbox_bridge_receipt(c)


def test_receipt_outcome_requires_effect_marker(setup):
    r = staged(setup)
    c = deepcopy(r)
    c["status"] = "sandbox_passed"
    c["receipt_sha256"] = sha256(json.dumps(
        {k:v for k,v in c.items() if k != "receipt_sha256"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()).hexdigest()
    assert not verify_sandbox_bridge_receipt(c)


def test_cancel_denies_request(setup):
    r = staged(setup)
    bridge, gate = setup[5], setup[4]
    req = bridge.request_review(r["session_id"])
    bridge.cancel(r["session_id"])
    assert gate.status(req["request_id"]).status == "denied"
    with pytest.raises(NexusMissionSandboxBridgeError):
        bridge.inspect(r["session_id"])


def test_cancel_without_request(setup):
    r = staged(setup)
    bridge = setup[5]
    bridge.cancel(r["session_id"])
    with pytest.raises(NexusMissionSandboxBridgeError):
        bridge.request_review(r["session_id"])


def test_request_id_not_in_receipt(setup):
    r = staged(setup)
    bridge = setup[5]
    req = bridge.request_review(r["session_id"])
    assert req["request_id"] not in json.dumps(bridge.inspect(r["session_id"]))


def test_wrong_registry_rejected(setup):
    _, reg, sup, runner, gate, bridge, _ = setup
    with pytest.raises(NexusMissionSandboxBridgeError):
        NexusMissionSandboxExecutionBridge(NexusMissionRegistry(), bridge.policy, sup, runner)


def test_wrong_action_policy_rejected(setup):
    _, reg, sup, runner, gate, bridge, _ = setup
    bad = NexusActionPolicy(runner.policy, rules=[])
    with pytest.raises(NexusMissionSandboxBridgeError):
        NexusMissionSandboxExecutionBridge(reg, bad, sup, runner)


def test_wrong_workspace_policy_rejected(setup, tmp_path):
    _, reg, sup, runner, gate, bridge, _ = setup
    root = tmp_path / "other"
    root.mkdir()
    other = NexusSandboxRunner(NexusWorkspacePolicy(NexusWorkspaceManager(root)))
    with pytest.raises(NexusMissionSandboxBridgeError):
        NexusMissionSandboxExecutionBridge(reg, bridge.policy, sup, other)


def test_wrong_approval_gate_root_rejected(setup, tmp_path):
    _, reg, sup, runner, gate, bridge, _ = setup
    other = tmp_path / "other"
    other.mkdir()
    gate2 = NexusApprovalGate(other, reviewer=lambda r: None,
                              binding_provider=lambda action: runner.binding_for(bridge._staged[next(iter(bridge._staged))].sandbox))
    with pytest.raises(NexusMissionSandboxBridgeError):
        NexusMissionSandboxExecutionBridge(reg, bridge.policy, sup, runner, approval_gate=gate2)


def test_missing_gate_blocks_review(setup):
    _, reg, sup, runner, gate, bridge, _ = setup
    simple = NexusMissionSandboxExecutionBridge(reg, bridge.policy, sup, runner)
    stage = simple.stage(MISSION, "python_ast", "safe.py", tool_id="sandbox.tool",
                         expected_registry_sha256=reg.snapshot()["registry_sha256"])
    with pytest.raises(NexusMissionSandboxBridgeError, match="unavailable"):
        simple.request_review(stage["session_id"])


def test_finalize_is_not_run(setup):
    r = staged(setup)
    _, _, sup, runner, gate, bridge, _ = setup
    bridge.inspect(r["session_id"])
    assert not runner._used
    assert not runner.policy._consumed_action_ids


@pytest.mark.parametrize("bad", [None, 42, [], "garbage", {}, {"schema": SANDBOX_BRIDGE_SCHEMA}])
def test_invalid_receipts_are_false(bad):
    assert not verify_sandbox_bridge_receipt(bad)


def test_release_consumed_stage(setup):
    r = staged(setup)
    bridge = setup[5]
    bridge.cancel(r["session_id"])
    bridge.release(r["session_id"])
    assert r["session_id"] not in bridge._staged


def test_release_active_stage_denied(setup):
    r = staged(setup)
    with pytest.raises(NexusMissionSandboxBridgeError):
        setup[5].release(r["session_id"])
