"""Original Nexus Orchestrator compatibility tests, entirely offline.

A fake ChatClient returns fixed NebiusReply data; host tool callbacks are
counters, never shell commands or file modifications. Actual Nebius, cloud,
Docker and external authorization are explicitly outside this suite.
"""
from __future__ import annotations

import json

import pytest

from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.planner import NexusPlanner, NexusPlanningError
from aegisforge.nexus.executor import NexusExecutor, RegisteredTool, ToolResult
from aegisforge.nexus.orchestrator import NexusOrchestrator, NexusOrchestrationError
from aegisforge.nexus.evidence import NexusEvidenceTrail


def _plan_json(*, tool=None):
    steps = [{
        "id": "s1", "kind": "reason" if tool is None else "tool",
        "title": "Prepare", "description": "Prepare a read-only sample",
        "success_criteria": "The sample is checked", "tool": tool,
        "depends_on": [],
    }, {
        "id": "s2", "kind": "verify", "title": "Verify", "tool": None,
        "description": "Inspect the recorded result",
        "depends_on": ["s1"], "success_criteria": "Evidence was assessed",
    }]
    return json.dumps({
        "goal": "Inspect an offline example", "steps": steps,
        "assumptions": [], "risks": [],
    })


class OfflineClient:
    def __init__(self, *, tool=None, response=None):
        self.response = _plan_json(tool=tool) if response is None else response
        self.calls = 0
        self.messages = None

    def chat(self, messages, *, max_tokens=512, temperature=0.2):
        self.calls += 1
        self.messages = messages
        return NebiusReply(content=self.response, model="offline-fixture", request_id="fixture-run")


def make_orchestrator(*, tool=False, mutating=False):
    client = OfflineClient(tool="sample.read" if tool else None)
    calls = []

    def handler(arguments):
        calls.append(dict(arguments))
        return ToolResult(True, "Fixture observed", {"source": "synthetic"})

    registry = ({"sample.read": RegisteredTool("sample.read", "Synthetic fixture", handler, mutating=mutating)}
                if tool else {})
    orchestrator = NexusOrchestrator(NexusPlanner(client), NexusExecutor(registry))
    return orchestrator, client, calls


def test_prepare_calls_offline_client_once():
    orchestrator, client, calls = make_orchestrator()
    prepared = orchestrator.prepare("Inspect synthetic evidence")
    assert client.calls == 1
    assert len(prepared.evidence.events) == 1
    assert calls == []
    assert not prepared.consumed


def test_preview_is_nonexecuting_and_repeatable():
    orchestrator, client, calls = make_orchestrator(tool=True)
    prepared = orchestrator.prepare("Inspect")
    first = orchestrator.preview(prepared)
    second = orchestrator.preview(prepared)
    assert first.tools_invoked == second.tools_invoked == 0
    assert calls == []
    assert not prepared.consumed
    assert len(prepared.evidence.events) == 1


def test_run_default_without_tools_is_one_shot():
    orchestrator, client, calls = make_orchestrator(tool=True)
    prepared = orchestrator.prepare("Inspect")
    result = orchestrator.run(prepared)
    assert calls == [] and result.execution.tools_invoked == 0
    assert prepared.consumed
    assert len(result.evidence.events) == 3
    with pytest.raises(NexusOrchestrationError):
        orchestrator.run(prepared)


def test_run_with_read_only_host_tool_is_explicit():
    orchestrator, client, calls = make_orchestrator(tool=True)
    prepared = orchestrator.prepare("Inspect")
    outcome = orchestrator.run(prepared, execute_tools=True)
    assert calls == [{}]
    assert outcome.execution.tools_invoked == 1
    assert outcome.status in ("verified", "failed", "inconclusive")
    assert outcome.to_summary()["audit_events"] == 3


def test_mutating_host_tool_needs_step_approval():
    orchestrator, client, calls = make_orchestrator(tool=True, mutating=True)
    prepared = orchestrator.prepare("Inspect")
    outcome = orchestrator.run(prepared, execute_tools=True)
    assert calls == []
    assert outcome.execution.tools_invoked == 0


def test_mutating_step_approval_only_on_explicit_opt_in():
    # Synthetic host handler only. This is NOT proof of production authorization.
    orchestrator, client, calls = make_orchestrator(tool=True, mutating=True)
    prepared = orchestrator.prepare("Inspect")
    outcome = orchestrator.run(prepared, execute_tools=True, approved_mutations=frozenset({"s1"}))
    assert calls == [{}]
    assert outcome.execution.tools_invoked == 1


def test_cross_orchestrator_prepared_plan_rejected():
    orchestrator, client, calls = make_orchestrator()
    foreign, _, _ = make_orchestrator()
    prepared = orchestrator.prepare("Inspect")
    with pytest.raises(NexusOrchestrationError):
        foreign.run(prepared)
    assert not prepared.consumed


def test_tool_not_advertised_to_planner_fails_closed():
    client = OfflineClient(tool="unknown.tool")
    orchestrator = NexusOrchestrator(NexusPlanner(client), NexusExecutor())
    with pytest.raises(NexusPlanningError):
        orchestrator.prepare("Inspect")


def test_invalid_plan_does_not_create_evidence():
    client = OfflineClient(response="not-json")
    orchestrator = NexusOrchestrator(NexusPlanner(client), NexusExecutor())
    with pytest.raises(NexusPlanningError):
        orchestrator.prepare("Inspect")


def test_plan_reports_do_not_contain_credentials():
    orchestrator, client, calls = make_orchestrator()
    prepared = orchestrator.prepare("Inspect synthetic evidence")
    outcome = orchestrator.run(prepared)
    summary = json.dumps(outcome.to_summary())
    assert "synthetic evidence" not in summary
    assert "fixture-run" not in summary
    assert outcome.to_summary()["audit_sha256"] == outcome.evidence.final_sha256


def test_audit_events_form_valid_chain():
    orchestrator, _, _ = make_orchestrator()
    outcome = orchestrator.run(orchestrator.prepare("Inspect"))
    lines = outcome.evidence.to_jsonl()
    assert len(lines.strip().splitlines()) == 3
    assert outcome.evidence.final_sha256 != "0" * 64


def test_same_prepared_plan_preview_after_run_rejected():
    orchestrator, _, _ = make_orchestrator()
    prepared = orchestrator.prepare("Inspect")
    orchestrator.run(prepared)
    with pytest.raises(NexusOrchestrationError):
        orchestrator.preview(prepared)


def test_manual_evidence_is_not_independent_verification():
    orchestrator, _, _ = make_orchestrator()
    outcome = orchestrator.run(orchestrator.prepare("Inspect"),
                               manual_evidence={"s1": "Fixture observation", "s2": "Fixture reviewed"})
    assert outcome.verification.independent_checks_passed == 0
    assert outcome.status in ("inconclusive", "failed")


@pytest.mark.parametrize("bad", ["", "\n", "{" * 100, "[]"])
def test_planner_rejects_malformed_content(bad):
    client = OfflineClient(response=bad)
    orchestrator = NexusOrchestrator(NexusPlanner(client), NexusExecutor())
    with pytest.raises(NexusPlanningError):
        orchestrator.prepare("Inspect")


def test_tool_handler_exception_is_redacted_and_not_retried():
    client = OfflineClient(tool="sample.read")
    invocations = []
    def fail_handler(arguments):
        invocations.append(True)
        raise RuntimeError("sensitive fixture exception text")
    orch = NexusOrchestrator(NexusPlanner(client), NexusExecutor({
        "sample.read": RegisteredTool("sample.read", "Failure fixture", fail_handler),
    }))
    prepared = orch.prepare("Inspect")
    outcome = orch.run(prepared, execute_tools=True)
    assert invocations == [True]
    assert outcome.execution.status == "failed"
    assert "sensitive fixture" not in json.dumps(outcome.execution.to_dict())
    with pytest.raises(NexusOrchestrationError):
        orch.run(prepared)


def test_invalid_tool_argument_does_not_invoke_handler():
    from aegisforge.nexus.executor import NexusExecutionError
    orch, client, invocations = make_orchestrator(tool=True)
    prepared = orch.prepare("Inspect")
    with pytest.raises(NexusExecutionError):
        orch.preview(prepared, tool_arguments={"s1": {"extra": "not permitted"}})
    assert invocations == []
    assert len(prepared.evidence.events) == 1


def test_unknown_mutation_step_does_not_invoke_handler():
    from aegisforge.nexus.executor import NexusExecutionError
    orch, _, invocations = make_orchestrator(tool=True, mutating=True)
    prepared = orch.prepare("Inspect")
    with pytest.raises(NexusExecutionError):
        orch.run(prepared, execute_tools=True, approved_mutations=frozenset({"s404"}))
    assert invocations == []
    assert prepared.consumed  # one-shot claim precedes validation


def test_verified_status_not_inferred_from_tool_success():
    orch, _, invocations = make_orchestrator(tool=True)
    out = orch.run(orch.prepare("Inspect"), execute_tools=True,
                   manual_evidence={"s2": "Operator reports review"})
    assert invocations == [{}]
    assert out.verification.independent_checks_passed == 0
    assert out.status != "verified"
