"""Nexus orchestration integration tests: offline, synthetic, no Nebius charges."""

import json
import tempfile
import unittest
from pathlib import Path

from aegisforge.nexus.evidence import verify_jsonl
from aegisforge.nexus.executor import NexusExecutor, RegisteredTool, ToolResult, NexusExecutionError
from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.orchestrator import NexusOrchestrator, NexusOrchestrationError
from aegisforge.nexus.planner import NexusPlanner, NexusPlanningError
from aegisforge.nexus.verifier import EvidenceExpectation, IndependentCheck


def make_payload(tool_id="read_fixture"):
    return {
        "goal": "Inspect permitted synthetic fixture",
        "steps": [
            {"id": "s1", "kind": "reason", "title": "Select fixture",
             "description": "Locate synthetic fixture", "tool": None, "depends_on": [],
             "success_criteria": "Fixture choice documented"},
            {"id": "s2", "kind": "tool", "title": "Inspect fixture",
             "description": "Read permitted metadata", "tool": tool_id, "depends_on": ["s1"],
             "success_criteria": "Expected fixture evidence reported"},
            {"id": "s3", "kind": "verify", "title": "Independently validate fixture",
             "description": "Compare independent fixture metadata", "tool": None,
             "depends_on": ["s2"], "success_criteria": "Independent check agrees"},
        ],
        "assumptions": [], "risks": [],
    }


class FakeNebius:
    def __init__(self, payload=None):
        self.payload = payload or make_payload()
        self.calls = []

    def chat(self, messages, *, max_tokens=512, temperature=0.2):
        self.calls.append((messages, max_tokens, temperature))
        return NebiusReply(content=json.dumps(self.payload), model="fake-nemotron",
                           finish_reason="stop", request_id="synthetic-001",
                           usage={"total_tokens": 15})


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tool_calls = []
        self.check_calls = []
        self.client = FakeNebius()

        def handle(args):
            self.tool_calls.append(dict(args))
            return ToolResult(True, "Metadata obtained from synthetic fixture", {"sha256": "abc123"})

        def validate(args):
            if dict(args) != {"fixture": "example"}:
                raise ValueError("Only synthetic fixtures are allowed")

        tool = RegisteredTool("read_fixture", "Read authorized synthetic fixture", handle,
                              validate_arguments=validate)
        self.planner = NexusPlanner(self.client)
        self.executor = NexusExecutor({"read_fixture": tool})
        self.nexus = NexusOrchestrator(self.planner, self.executor)
        self.arguments = {"s2": {"fixture": "example"}}
        self.attestations = {"s1": "The synthetic fixture was selected", "s3": "Operator acknowledged review"}

    def prep(self):
        return self.nexus.prepare("Inspect the synthetic fixture", constraints=["Read-only"])

    def trusted_checks(self, *, result=True, raise_error=False):
        def check(step, record):
            self.check_calls.append(step.id)
            if raise_error:
                raise RuntimeError("SECRET: must not leak")
            return result

        return (
            IndependentCheck("fixture_digest", "s2", "Compare trusted fixture hash", check, "fixture/sha256"),
            IndependentCheck("final_validation", "s3", "Compare second trusted observation", check, "fixture/review"),
        )

    def test_prepare_makes_one_planner_call_without_tools(self):
        prepared = self.prep()
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(len(prepared.evidence.events), 1)
        self.assertEqual(prepared.evidence.events[0].stage, "plan")
        self.assertEqual(prepared.plan.steps[1].tool, "read_fixture")
        self.assertFalse(prepared.consumed)
        advertised = json.loads(self.client.calls[0][0][1]["content"])["allowed_tools"]
        self.assertEqual(set(advertised), {"read_fixture"})

    def test_preview_does_not_use_tools_or_consume_plan(self):
        prepared = self.prep()
        first = self.nexus.preview(prepared, tool_arguments=self.arguments,
                                   manual_evidence=self.attestations)
        second = self.nexus.preview(prepared, tool_arguments=self.arguments,
                                    manual_evidence=self.attestations)
        self.assertEqual(first.status, "preview")
        self.assertEqual(second.tools_invoked, 0)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(len(prepared.evidence.events), 1)
        self.assertEqual(self.tool_calls, [])
        self.assertFalse(prepared.consumed)

    def test_default_run_is_nonexecuting_and_inconclusive(self):
        prepared = self.prep()
        result = self.nexus.run(prepared, tool_arguments=self.arguments,
                                manual_evidence=self.attestations,
                                independent_checks=self.trusted_checks())
        self.assertEqual(result.execution.status, "preview")
        self.assertEqual(result.status, "inconclusive")
        self.assertEqual(result.execution.tools_invoked, 0)
        self.assertEqual(len(result.evidence.events), 3)
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(self.check_calls, [])
        self.assertTrue(prepared.consumed)
        self.assertEqual(result.to_summary()["audit_events"], 3)

    def test_read_only_execution_verified_only_with_independent_checks(self):
        result = self.nexus.run(
            self.prep(), execute_tools=True, tool_arguments=self.arguments,
            manual_evidence=self.attestations,
            expectations=(EvidenceExpectation("s2", "sha256", "abc123"),),
            independent_checks=self.trusted_checks(),
        )
        self.assertEqual(result.execution.status, "reported_complete")
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.verification.independent_checks_run, 2)
        self.assertEqual(self.tool_calls, [{"fixture": "example"}])
        self.assertEqual(self.check_calls, ["s2", "s3"])
        self.assertNotIn("fixture", str(result.to_summary()))

    def test_success_without_checks_remains_inconclusive(self):
        result = self.nexus.run(self.prep(), execute_tools=True,
                                tool_arguments=self.arguments, manual_evidence=self.attestations)
        self.assertEqual(result.execution.status, "reported_complete")
        self.assertEqual(result.status, "inconclusive")

    def test_missing_reason_evidence_blocks_tool(self):
        result = self.nexus.run(self.prep(), execute_tools=True, tool_arguments=self.arguments)
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(result.execution.status, "awaiting_evidence")
        self.assertEqual(result.status, "inconclusive")

    def test_failed_check_is_failed_even_after_tool_succeeds(self):
        result = self.nexus.run(self.prep(), execute_tools=True,
                                tool_arguments=self.arguments, manual_evidence=self.attestations,
                                independent_checks=self.trusted_checks(result=False))
        self.assertEqual(result.status, "failed")

    def test_check_error_is_redacted_and_inconclusive(self):
        result = self.nexus.run(self.prep(), execute_tools=True,
                                tool_arguments=self.arguments, manual_evidence=self.attestations,
                                independent_checks=self.trusted_checks(raise_error=True))
        self.assertEqual(result.status, "inconclusive")
        self.assertNotIn("SECRET", str(result.verification.to_dict()))
        self.assertNotIn("SECRET", result.evidence.to_jsonl())

    def test_wrong_expectation_prevents_checks(self):
        result = self.nexus.run(self.prep(), execute_tools=True,
                                tool_arguments=self.arguments, manual_evidence=self.attestations,
                                expectations=(EvidenceExpectation("s2", "sha256", "different"),),
                                independent_checks=self.trusted_checks())
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.check_calls, [])

    def test_prepared_plan_cannot_replay(self):
        prepared = self.prep()
        self.nexus.run(prepared, execute_tools=True,
                       tool_arguments=self.arguments, manual_evidence=self.attestations)
        with self.assertRaises(NexusOrchestrationError):
            self.nexus.run(prepared, execute_tools=True,
                           tool_arguments=self.arguments, manual_evidence=self.attestations)
        with self.assertRaises(NexusOrchestrationError):
            self.nexus.preview(prepared)
        self.assertEqual(len(self.tool_calls), 1)

    def test_foreign_orchestrator_rejected(self):
        foreign = NexusOrchestrator(self.planner, self.executor)
        prepared = self.prep()
        with self.assertRaises(NexusOrchestrationError):
            foreign.run(prepared)
        self.assertFalse(prepared.consumed)

    def test_invalid_preflight_consumes_once_preventing_replay(self):
        prepared = self.prep()
        with self.assertRaises(NexusExecutionError):
            self.nexus.run(prepared, execute_tools=True,
                           tool_arguments={"s2": {"fixture": "forbidden"}},
                           manual_evidence=self.attestations)
        self.assertEqual(self.tool_calls, [])
        self.assertTrue(prepared.consumed)
        self.assertEqual(len(prepared.evidence.events), 1)
        with self.assertRaises(NexusOrchestrationError):
            self.nexus.run(prepared)

    def test_mutation_denied_without_step_approval(self):
        payload = make_payload("write_fixture")
        client = FakeNebius(payload)
        calls = []
        tool = RegisteredTool("write_fixture", "Mutate synthetic fixture",
                              lambda args: (calls.append(dict(args)) or ToolResult(True, "Done")),
                              mutating=True,
                              validate_arguments=lambda args: None)
        nexus = NexusOrchestrator(NexusPlanner(client), NexusExecutor({"write_fixture": tool}))
        prepared = nexus.prepare("Try an authorized synthetic edit")
        report = nexus.run(prepared, execute_tools=True,
                           tool_arguments={"s2": {}}, manual_evidence=self.attestations)
        self.assertEqual(report.execution.status, "approval_required")
        self.assertEqual(calls, [])
        self.assertEqual(report.status, "inconclusive")

    def test_mutation_executes_with_explicit_step_approval(self):
        client = FakeNebius(make_payload("write_fixture"))
        calls = []
        tool = RegisteredTool("write_fixture", "Mutate synthetic fixture",
                              lambda args: (calls.append(1) or ToolResult(True, "Synthetic update")),
                              mutating=True)
        nexus = NexusOrchestrator(NexusPlanner(client), NexusExecutor({"write_fixture": tool}))
        result = nexus.run(nexus.prepare("Synthetic change"), execute_tools=True,
                           approved_mutations=frozenset({"s2"}), manual_evidence=self.attestations)
        self.assertEqual(calls, [1])
        self.assertEqual(result.execution.status, "reported_complete")
        self.assertEqual(result.status, "inconclusive")

    def test_plan_without_registered_tool_is_not_created(self):
        nexus = NexusOrchestrator(self.planner, NexusExecutor())
        with self.assertRaises(NexusPlanningError):
            nexus.prepare("Read a synthetic fixture")
        self.assertEqual(self.tool_calls, [])

    def test_serialized_audit_has_only_summaries_and_integrity_chain(self):
        result = self.nexus.run(self.prep(), execute_tools=True,
                                tool_arguments=self.arguments, manual_evidence=self.attestations,
                                independent_checks=self.trusted_checks())
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nexus_audit.jsonl"
            self.assertFalse(path.exists())
            result.evidence.save_jsonl(path)
            self.assertEqual(len(verify_jsonl(path)), 3)
            self.assertEqual(result.evidence.final_sha256, verify_jsonl(path)[-1].sha256)
            data = path.read_text("utf-8")
            self.assertNotIn("Operator acknowledged", data)
            self.assertNotIn("abc123", data)
            with self.assertRaises(FileExistsError):
                result.evidence.save_jsonl(path)

    def test_summary_does_not_include_task_or_raw_tool_data(self):
        result = self.nexus.run(self.prep(), execute_tools=True,
                                tool_arguments=self.arguments, manual_evidence=self.attestations)
        summary = str(result.to_summary())
        self.assertNotIn("Inspect permitted synthetic fixture", summary)
        self.assertNotIn("abc123", summary)
        self.assertNotIn("Operator", summary)
        self.assertEqual(result.to_summary()["tools_invoked"], 1)

    def test_type_contract_rejects_incompatible_constructor_inputs(self):
        with self.assertRaises(NexusOrchestrationError):
            NexusOrchestrator("not a planner", self.executor)
        with self.assertRaises(NexusOrchestrationError):
            NexusOrchestrator(self.planner, "not an executor")
        with self.assertRaises(NexusOrchestrationError):
            NexusOrchestrator(self.planner, self.executor, "not a verifier")


if __name__ == "__main__":
    unittest.main()
