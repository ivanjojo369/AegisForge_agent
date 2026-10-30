"""Engineering integration contracts: offline, temporary workspace, no effects.

Uses the actual Workspace Policy, CodeProposer, DiffBuilder, TestDiscovery,
and TestRunner. Never calls a live model, Docker, shell, or patch applicator.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from aegisforge.nexus.workspace.manager import NexusWorkspaceManager
from aegisforge.nexus.workspace.workspace_policy import (
    NexusWorkspacePolicy, WorkspaceRule, NexusWorkspaceAccessDenied,
)
from aegisforge.nexus.engineering.code_proposer import (
    NexusCodeProposer, NexusCodeProposalError,
)
from aegisforge.nexus.engineering.diff_builder import (
    NexusDiffBuilder, NexusDiffBuilderError,
)
from aegisforge.nexus.engineering.test_discovery import NexusTestDiscovery
from aegisforge.nexus.engineering.test_runner import (
    NexusTestRunner, NexusTestRunnerError,
)

MISSION = "phase2.engineering"
ORIGINAL = b"def price(x):\n    return x + 1\n"


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "sample.py").write_bytes(ORIGINAL)
    (root / "tests" / "test_sample.py").write_bytes(
        b"from src.sample import price\n\ndef test_price():\n    assert price(1) == 2\n")
    manager = NexusWorkspaceManager(root)
    policy = NexusWorkspacePolicy(manager, rules=[
        WorkspaceRule(MISSION, "src", frozenset({"read_text", "inventory"})),
        WorkspaceRule(MISSION, "tests", frozenset({"read_text", "inventory"})),
    ])
    proposer = NexusCodeProposer(policy)
    builder = NexusDiffBuilder(proposer)
    return root, policy, proposer, builder


def candidate(*, path="src/sample.py", search="return x + 1", replace_text="return x + 2"):
    return json.dumps({"summary": "Propose a small change", "edits": [{
        "path": path, "search": search, "replace": replace_text,
        "rationale": "Change a constant for review",
    }]})


def prepared(env):
    root, policy, proposer, builder = env
    context = proposer.prepare(MISSION, "Suggest adjustment", target_files=["src/sample.py"])
    proposal = proposer.review_response(context, candidate())
    return context, proposal


def test_review_only_pipeline_preserves_file(env):
    root, _, proposer, builder = env
    context, proposal = prepared(env)
    patch = builder.build(context, proposal)
    assert patch.status == "review_required"
    assert patch.to_dict()["applied"] is False
    assert proposal.to_dict()["applied"] is False
    assert patch.files[0].relative_path == "src/sample.py"
    assert (root / "src/sample.py").read_bytes() == ORIGINAL
    assert builder.verify(patch, context, proposal) is not False


def test_proposal_summary_excludes_code_and_diff(env):
    _, _, _, builder = env
    ctx, proposal = prepared(env)
    patch = builder.build(ctx, proposal)
    exported = json.dumps(patch.to_dict())
    assert "return x + 2" not in exported
    assert "return x + 1" not in exported
    assert "unified_diff" not in exported
    assert "unified_diff" in patch.to_dict(include_diffs=True)["files"][0]


def test_different_mission_cannot_read_source(env):
    _, policy, _, _ = env
    proposer = NexusCodeProposer(policy)
    with pytest.raises(NexusWorkspaceAccessDenied):
        proposer.prepare("other.mission", "Please change", target_files=["src/sample.py"])


@pytest.mark.parametrize("unsafe", ["../src/sample.py", "/tmp/sample.py", "C:\\tmp\\a.py", "src/../src/sample.py"])
def test_unsafe_target_paths_rejected(env, unsafe):
    _, _, proposer, _ = env
    with pytest.raises(NexusCodeProposalError):
        proposer.prepare(MISSION, "Inspect", target_files=[unsafe])


def test_unlisted_target_rejected(env):
    _, _, proposer, _ = env
    ctx = proposer.prepare(MISSION, "Inspect", target_files=["src/sample.py"])
    with pytest.raises(NexusCodeProposalError):
        proposer.review_response(ctx, candidate(path="tests/test_sample.py"))


@pytest.mark.parametrize("text", [
    '{"summary":"a","summary":"b","edits":[]}',
    '{"summary":"okay","edits":[{"path":"src/sample.py","search":"abc","replace":"def","rationale":"x"}]}',
    '{"summary":"okay","edits":[{"path":"src/sample.py","search":"return x + 1","replace":"return !!!","rationale":"bad"}]}',
])
def test_model_payload_invalid_or_nonapplicable(env, text):
    _, _, proposer, _ = env
    ctx = proposer.prepare(MISSION, "Inspect", target_files=["src/sample.py"])
    with pytest.raises(NexusCodeProposalError):
        proposer.review_response(ctx, text)


def test_changed_source_blocks_reviewable_patch(env):
    root, _, proposer, builder = env
    ctx, proposal = prepared(env)
    (root / "src/sample.py").write_bytes(b"def price(x):\n    return 999\n")
    assert proposer.revalidate(ctx)["fresh"] is False
    with pytest.raises(NexusDiffBuilderError):
        builder.build(ctx, proposal)


def test_forged_proposal_digest_rejected(env):
    _, _, _, builder = env
    ctx, proposal = prepared(env)
    with pytest.raises(NexusDiffBuilderError):
        builder.build(ctx, replace(proposal, context_sha256="0" * 64))


def test_changed_unified_diff_rejected(env):
    _, _, _, builder = env
    ctx, proposal = prepared(env)
    malicious_edit = replace(proposal.edits[0], unified_diff=proposal.edits[0].unified_diff + "unexpected\n")
    with pytest.raises(NexusDiffBuilderError):
        builder.build(ctx, replace(proposal, edits=(malicious_edit,)))


def test_no_model_client_refuses_inference(env):
    _, _, proposer, _ = env
    ctx = proposer.prepare(MISSION, "Inspect", target_files=["src/sample.py"])
    with pytest.raises(NexusCodeProposalError):
        proposer.propose(ctx)


def test_discovery_and_runner_prepare_is_nonexecuting(env):
    root, policy, _, _ = env
    discovery = NexusTestDiscovery(policy).discover(
        MISSION, test_scopes=["tests"], include_unlinked=True)
    assert discovery["selected_tests"]
    runner = NexusTestRunner(policy)  # no backend on purpose
    plan = runner.prepare(MISSION, discovery)
    assert plan.to_dict()["approval_required"] is True
    with pytest.raises(NexusTestRunnerError):
        runner.run(plan, approvals={})
    assert (root / "tests/test_sample.py").is_file()


def test_discovery_cannot_read_unapproved_test_directory(env):
    _, policy, _, _ = env
    with pytest.raises((NexusWorkspaceAccessDenied, ValueError)):
        NexusTestDiscovery(policy).discover("other.mission", test_scopes=["tests"], include_unlinked=True)


def test_test_plan_from_other_runner_is_rejected(env):
    _, policy, _, _ = env
    catalog = NexusTestDiscovery(policy).discover(MISSION, test_scopes=["tests"], include_unlinked=True)
    first = NexusTestRunner(policy)
    second = NexusTestRunner(policy)
    plan = first.prepare(MISSION, catalog)
    with pytest.raises(NexusTestRunnerError):
        second.run(plan, approvals={})


def test_engineering_pipeline_has_no_unplanned_mutation(env):
    root, policy, proposer, builder = env
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    ctx, proposal = prepared(env)
    builder.build(ctx, proposal)
    NexusTestDiscovery(policy).discover(MISSION, test_scopes=["tests"], include_unlinked=True)
    after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert before == after
