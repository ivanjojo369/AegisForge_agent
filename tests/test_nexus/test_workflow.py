"""Offline integration contract for Planner -> Executor -> Verifier -> Evidence.

Repository location: tests/test_nexus/test_workflow.py
All model replies, tools and independent checks are synthetic and in-process.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from aegisforge.nexus.evidence import verify_jsonl
from aegisforge.nexus.executor import NexusExecutor, RegisteredTool, ToolResult
from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.orchestrator import NexusOrchestrator, NexusOrchestrationError
from aegisforge.nexus.planner import NexusPlanner
from aegisforge.nexus.verifier import EvidenceExpectation, IndependentCheck


# A task plan proposed by a fake Nemotron; it contains no live system commands.
SYNTHETIC_PLAN = {
    "goal": "Inspect a synthetic fixture and verify its metadata",
    "steps": [
        {"id": "s1", "kind": "reason", "title": "Identify fixture",
         "description": "Choose the provided example fixture", "tool": None,
         "depends_on": [], "success_criteria": "Operator acknowledges fixture selection"},
        {"id": "s2", "kind": "tool", "title": "Read synthetic metadata",
         "description": "Query only the in-memory fixture", "tool": "read_fixture",
         "depends_on": ["s1"], "success_criteria": "Declared checksum is available"},
        {"id": "s3", "kind": "verify", "title": "Check metadata independently",
         "description": "Use trusted host check", "tool": None,
         "depends_on": ["s2"], "success_criteria": "Independent checksum check passes"},
    ],
    "assumptions": ["Fixture is synthetic"],
    "risks": ["An attestation is not independent evidence"],
}


class FakeNemotron:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, max_tokens=512, temperature=None):
        self.calls += 1
        return NebiusReply(content=json.dumps(SYNTHETIC_PLAN), model="fake-nemotron",
                           finish_reason="stop", request_id="offline-workflow-001",
                           usage={"total_tokens": 10})


class NexusWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNemotron()
        self.invocations = []
        self.check_invocations = []
        self.trusted_fixture = {"id": "sample", "checksum": "demo-checksum-001"}

        def validate(arguments):
            if dict(arguments) != {"id": "sample"}:
                raise ValueError("Only the synthetic sample fixture is permitted")

        def reader(arguments):
            self.invocations.append(dict(arguments))
            return ToolResult(ok=True, summary="Synthetic fixture read",
                              evidence={"checksum": self.trusted_fixture["checksum"]})

        self.nexus = NexusOrchestrator(
            NexusPlanner(self.fake),
            NexusExecutor({"read_fixture": RegisteredTool(
                "read_fixture", "Read synthetic fixture metadata only", reader,
                validate_arguments=validate,
            )}),
        )
        self.tool_arguments = {"s2": {"id": "sample"}}
        self.attestations = {"s1": "Selected sample fixture", "s3": "Ready for independent check"}

    def independent_checks(self, *, expected="demo-checksum-001"):
        def check_tool(step, record):
            self.check_invocations.append(step.id)
            # Independently inspect host fixture, not the model's assertions.
            return self.trusted_fixture["checksum"] == expected and step.id == "s2"

        def check_final(step, record):
            self.check_invocations.append(step.id)
            return self.trusted_fixture["id"] == "sample" and step.id == "s3"

        return (
            IndependentCheck("checksum_check", "s2", "Host fixture checksum check",
                             check_tool, "fixture/host-checksum"),
            IndependentCheck("fixture_check", "s3", "Host fixture selection check",
                             check_final, "fixture/host-id"),
        )

    def test_sample_request_is_valid_for_prepare_endpoint(self):
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "examples" / "nexus_demo" / "sample_task.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"task", "constraints"})
        self.assertTrue(isinstance(payload["task"], str) and payload["task"].strip())
        self.assertIsInstance(payload["constraints"], list)
        self.assertTrue(all(isinstance(c, str) and c.strip() for c in payload["constraints"]))
        self.assertNotIn("api_key", payload)

    def test_prepare_does_not_execute_registered_tool(self):
        prepared = self.nexus.prepare("Inspect a synthetic fixture")
        self.assertEqual(self.fake.calls, 1)
        self.assertEqual(prepared.plan.steps[1].tool, "read_fixture")
        self.assertEqual(self.invocations, [])
        self.assertEqual(len(prepared.evidence.events), 1)

    def test_preview_is_safe_and_repeatable(self):
        prepared = self.nexus.prepare("Inspect a synthetic fixture")
        for _ in range(2):
            report = self.nexus.preview(
                prepared, tool_arguments=self.tool_arguments,
                manual_evidence=self.attestations,
            )
            self.assertEqual(report.status, "preview")
        self.assertEqual(self.invocations, [])
        self.assertFalse(prepared.consumed)
        self.assertEqual(self.fake.calls, 1)

    def test_execution_is_disabled_by_default(self):
        outcome = self.nexus.run(
            self.nexus.prepare("Inspect a synthetic fixture"),
            tool_arguments=self.tool_arguments,
            manual_evidence=self.attestations,
            independent_checks=self.independent_checks(),
        )
        self.assertEqual(outcome.status, "inconclusive")
        self.assertEqual(outcome.execution.tools_invoked, 0)
        self.assertEqual(self.invocations, [])
        self.assertEqual(self.check_invocations, [])

    def test_tools_report_success_but_checks_still_required(self):
        outcome = self.nexus.run(
            self.nexus.prepare("Inspect a synthetic fixture"),
            execute_tools=True, tool_arguments=self.tool_arguments,
            manual_evidence=self.attestations,
        )
        self.assertEqual(outcome.execution.status, "reported_complete")
        self.assertEqual(outcome.status, "inconclusive")
        self.assertEqual(len(self.invocations), 1)

    def test_trusted_read_only_checks_make_workflow_verified(self):
        outcome = self.nexus.run(
            self.nexus.prepare("Inspect a synthetic fixture"),
            execute_tools=True, tool_arguments=self.tool_arguments,
            manual_evidence=self.attestations,
            expectations=(EvidenceExpectation("s2", "checksum", "demo-checksum-001"),),
            independent_checks=self.independent_checks(),
        )
        self.assertEqual(outcome.status, "verified")
        self.assertEqual(outcome.execution.tools_invoked, 1)
        self.assertEqual(self.check_invocations, ["s2", "s3"])
        self.assertEqual(outcome.to_summary()["audit_events"], 3)
        self.assertEqual(self.fake.calls, 1)

    def test_negative_independent_check_causes_failure(self):
        outcome = self.nexus.run(
            self.nexus.prepare("Inspect a synthetic fixture"),
            execute_tools=True, tool_arguments=self.tool_arguments,
            manual_evidence=self.attestations,
            independent_checks=self.independent_checks(expected="wrong-checksum"),
        )
        self.assertEqual(outcome.status, "failed")

    def test_invalid_arguments_do_not_invoke_tools(self):
        prepared = self.nexus.prepare("Inspect a synthetic fixture")
        with self.assertRaises(ValueError):
            self.nexus.run(
                prepared, execute_tools=True,
                tool_arguments={"s2": {"id": "outside-scope"}},
                manual_evidence=self.attestations,
            )
        self.assertEqual(self.invocations, [])
        self.assertTrue(prepared.consumed)
        with self.assertRaises(NexusOrchestrationError):
            self.nexus.run(prepared)

    def test_saved_audit_can_be_checked_offline(self):
        outcome = self.nexus.run(
            self.nexus.prepare("Inspect a synthetic fixture"),
            execute_tools=True, tool_arguments=self.tool_arguments,
            manual_evidence=self.attestations,
            independent_checks=self.independent_checks(),
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "run.jsonl"
            outcome.evidence.save_jsonl(path)
            events = verify_jsonl(path)
        self.assertEqual(len(events), 3)
        self.assertEqual([e.stage for e in events], ["plan", "execution", "verification"])
        self.assertNotIn("Selected sample fixture", outcome.evidence.to_jsonl())


if __name__ == "__main__":
    unittest.main()
