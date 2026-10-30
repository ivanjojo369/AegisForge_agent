"""Trust integration contracts with synthetic host attestations ONLY.

No real human approval, credentials, file mutation, Docker or network use.
The host simulation exists solely to verify the approval gate's state machine.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from aegisforge.nexus.workspace.manager import NexusWorkspaceManager
from aegisforge.nexus.workspace.workspace_policy import (
    NexusWorkspacePolicy, WorkspaceRule, NexusWorkspaceAccessDenied,
)
from aegisforge.nexus.trust.approval_gate import (
    ActionBinding, ApprovalAttestation, NexusApprovalGate,
)
from aegisforge.nexus.trust.action_policy import (
    ActionIntent, ActionRule, NexusActionPolicy, NexusActionDenied,
)
from aegisforge.nexus.trust.secret_guard import NexusSecretGuard, NexusSecretGuardError
from aegisforge.nexus.trust.sandbox_runner import (
    NexusSandboxRunner, NexusSandboxError,
)

MISSION = "phase2.trust"
DIGEST = "a" * 64


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "safe.py").write_bytes(b"print('safe')\n")
    manager = NexusWorkspaceManager(root)
    rules = [WorkspaceRule(MISSION, "safe.py", frozenset({"read_text", "apply_patch", "execute_tool"}))]
    return root, manager, rules


def _policy(env, *, allow_mutations=False, verifier=None):
    _, manager, rules = env
    return NexusWorkspacePolicy(manager, rules=rules, allow_mutations=allow_mutations,
                                approval_verifier=verifier)


def _intent(operation="read_text", *, tool="reader", path="safe.py", payload=None, recipe=None):
    return ActionIntent(MISSION, tool, operation, path, DIGEST, payload or {}, recipe)


def test_approved_read_is_not_mutation_authority(env):
    policy = _policy(env)
    guard = NexusActionPolicy(policy, rules=[ActionRule(MISSION, "reader", "read_text", "safe.py")])
    decision = guard.require_read(_intent())
    assert decision.allowed is True and decision.approval_required is False
    with pytest.raises(NexusWorkspaceAccessDenied):
        policy.require(MISSION, "apply_patch", "safe.py")


def test_impactful_action_policy_default_denies(env):
    policy = _policy(env)
    guard = NexusActionPolicy(policy, rules=[ActionRule(MISSION, "patcher", "apply_patch", "safe.py")])
    decision = guard.evaluate(_intent("apply_patch", tool="patcher"))
    assert decision.allowed is False
    assert decision.status == "denied"


def test_impactful_preflight_still_requires_review(env):
    policy = _policy(env, allow_mutations=True, verifier=lambda action: False)
    guard = NexusActionPolicy(policy, rules=[ActionRule(MISSION, "patcher", "apply_patch", "safe.py")])
    decision = guard.require_review(_intent("apply_patch", tool="patcher"))
    assert decision.allowed is False
    assert decision.status == "review_required"
    assert decision.approval_required is True
    assert policy.check(MISSION, "apply_patch", "safe.py").allowed is False


@pytest.mark.parametrize("path", ["../safe.py", "/tmp/safe.py", "C:\\safe.py", "safe.py/../safe.py"])
def test_action_policy_rejects_traversal(env, path):
    policy = _policy(env)
    guard = NexusActionPolicy(policy, rules=[ActionRule(MISSION, "reader", "read_text", "safe.py")])
    assert not guard.evaluate(_intent(path=path)).allowed


def test_action_policy_no_tool_rule_denies(env):
    policy = _policy(env)
    guard = NexusActionPolicy(policy, rules=[ActionRule(MISSION, "reader", "read_text", "safe.py")])
    decision = guard.evaluate(_intent(tool="missing"))
    assert decision.status == "denied"


def test_action_policy_unlisted_payload_field_denies(env):
    policy = _policy(env)
    guard = NexusActionPolicy(policy, rules=[ActionRule(MISSION, "reader", "read_text", "safe.py")])
    assert not guard.evaluate(_intent(payload={"unexpected": 42})).allowed


def test_secret_guard_rejects_sensitive_payload(env):
    guard = NexusSecretGuard()
    # Fully synthetic token matching a well-known AWS key shape; no valid key.
    token = "AKIA" + "Q" * 16
    scan = guard.scan_text("aws=" + token)
    assert not scan.clean
    assert token not in str(scan.to_dict())
    with pytest.raises(NexusSecretGuardError):
        guard.require_clean_payload({"text": "aws=" + token})


def test_approval_request_not_implicitly_approved(env):
    root, _, _ = env
    binding = ActionBinding(DIGEST, "b" * 64)
    gate = NexusApprovalGate(root, reviewer=lambda request: None,
                             binding_provider=lambda action: binding)
    request = gate.request(MISSION, "apply_patch", "safe.py", binding=binding)
    assert gate.status(request.request_id).status == "pending"
    assert gate.review(request.request_id).status == "denied"
    assert gate.status(request.request_id).status == "denied"


def test_one_use_approval_bound_to_exact_action(env):
    root, manager, rules = env
    binding = ActionBinding(DIGEST, "b" * 64)
    gate = NexusApprovalGate(root,
        reviewer=lambda request: ApprovalAttestation(request.request_sha256, "trusted_operator", "review_001"),
        binding_provider=lambda action: binding)
    policy = NexusWorkspacePolicy(manager, rules=rules, allow_mutations=True,
                                  approval_verifier=gate.verify_workspace_action)
    req = gate.request(MISSION, "apply_patch", "safe.py", binding=binding)
    assert gate.review(req.request_id).status == "approved"
    assert policy.require(MISSION, "apply_patch", "safe.py", action_id=req.request_id).allowed
    assert gate.status(req.request_id).status == "consumed"
    with pytest.raises(NexusWorkspaceAccessDenied):
        policy.require(MISSION, "apply_patch", "safe.py", action_id=req.request_id)
    assert (root / "safe.py").read_bytes() == b"print('safe')\n"


def test_binding_mismatch_denies_even_after_review(env):
    root, manager, rules = env
    binding = ActionBinding(DIGEST, "b" * 64)
    gate = NexusApprovalGate(root,
        reviewer=lambda request: ApprovalAttestation(request.request_sha256, "trusted_operator", "review_002"),
        binding_provider=lambda action: ActionBinding("c" * 64, "b" * 64))
    policy = NexusWorkspacePolicy(manager, rules=rules, allow_mutations=True,
                                  approval_verifier=gate.verify_workspace_action)
    req = gate.request(MISSION, "apply_patch", "safe.py", binding=binding)
    assert gate.review(req.request_id).status == "approved"
    with pytest.raises(NexusWorkspaceAccessDenied):
        policy.require(MISSION, "apply_patch", "safe.py", action_id=req.request_id)
    assert gate.status(req.request_id).status == "denied"


def test_approval_expires(env):
    root, _, _ = env
    now = [1000.0]
    gate = NexusApprovalGate(root, reviewer=lambda request: None,
                             binding_provider=lambda action: ActionBinding(DIGEST, "b"*64),
                             clock=lambda: now[0])
    req = gate.request(MISSION, "apply_patch", "safe.py", binding=ActionBinding(DIGEST, "b"*64), ttl_seconds=30)
    now[0] = 1030.0
    assert gate.status(req.request_id).status == "expired"


def test_sandbox_no_backend_prevents_execution(env):
    policy = _policy(env)
    runner = NexusSandboxRunner(policy)
    plan = runner.prepare(MISSION, "python_ast", "safe.py")
    assert plan.recipe == "python_ast"
    with pytest.raises(NexusSandboxError):
        runner.run(plan, action_id="0" * 32)


def test_sandbox_unauthorized_mission_rejected(env):
    policy = _policy(env)
    runner = NexusSandboxRunner(policy)
    with pytest.raises((NexusSandboxError, NexusWorkspaceAccessDenied, ValueError)):
        runner.prepare("another.mission", "python_ast", "safe.py")


def test_sandbox_prepare_does_not_modify_workspace(env):
    root, _, _ = env
    policy = _policy(env)
    before = (root / "safe.py").read_bytes()
    runner = NexusSandboxRunner(policy)
    runner.prepare(MISSION, "python_ast", "safe.py")
    assert (root / "safe.py").read_bytes() == before


def test_approval_for_wrong_mission_is_not_usable(env):
    root, manager, rules = env
    binding = ActionBinding(DIGEST, "b" * 64)
    gate = NexusApprovalGate(root,
        reviewer=lambda req: ApprovalAttestation(req.request_sha256, "trusted_operator", "review_003"),
        binding_provider=lambda action: binding)
    policy = NexusWorkspacePolicy(manager, rules=rules, allow_mutations=True,
                                  approval_verifier=gate.verify_workspace_action)
    req = gate.request("another.mission", "apply_patch", "safe.py", binding=binding)
    assert gate.review(req.request_id).status == "approved"
    with pytest.raises(NexusWorkspaceAccessDenied):
        policy.require(MISSION, "apply_patch", "safe.py", action_id=req.request_id)
    assert gate.status(req.request_id).status == "approved"


def test_review_failure_is_denied_and_does_not_raise(env):
    root, _, _ = env
    binding = ActionBinding(DIGEST, "b" * 64)
    def broken_reviewer(req):
        raise RuntimeError("synthetic reviewer unavailable")
    gate = NexusApprovalGate(root, reviewer=broken_reviewer,
                             binding_provider=lambda action: binding)
    req = gate.request(MISSION, "apply_patch", "safe.py", binding=binding)
    result = gate.review(req.request_id)
    assert result.status == "denied"
    assert "unavailable" not in str(result.to_dict())


def test_approval_status_does_not_reveal_attestation(env):
    root, _, _ = env
    binding = ActionBinding(DIGEST, "b" * 64)
    gate = NexusApprovalGate(root,
        reviewer=lambda req: ApprovalAttestation(req.request_sha256, "trusted_operator", "review_004"),
        binding_provider=lambda action: binding)
    req = gate.request(MISSION, "apply_patch", "safe.py", binding=binding)
    gate.review(req.request_id)
    result = gate.status(req.request_id).to_dict()
    assert result["status"] == "approved"
    assert "reviewer_id" not in result
    assert "review_004" not in str(result)
