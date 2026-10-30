"""Offline executor tests. They do not call Nebius, AWS, shells or networks."""

import json
import unittest

from aegisforge.nexus.executor import (
    NexusExecutor,
    NexusExecutionError,
    RegisteredTool,
    ToolResult,
)
from aegisforge.nexus.planner import NexusPlan, PlanStep


def make_plan(*, tool="read_file"):
    return NexusPlan(
        goal="Review a synthetic document",
        steps=(
            PlanStep("s1", "reason", "Choose file", "Select input", "Input selected"),
            PlanStep("s2", "tool", "Read file", "Read permitted file", "File inspected", ("s1",), tool),
            PlanStep("s3", "verify", "Review evidence", "Check result", "Evidence checked", ("s2",)),
        ),
        request_id="simulated-request-id",
    )


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def read_handler(args):
            self.calls.append(dict(args))
            return ToolResult(ok=True, summary="Synthetic file metadata inspected.", evidence={"fixture": "demo.txt"})

        def write_handler(args):
            self.calls.append(dict(args))
            return ToolResult(ok=True, summary="Synthetic write acknowledged.")

        def validate_read(args):
            if set(args) != {"path"} or args["path"] != "demo.txt":
                raise ValueError("Only the synthetic demo.txt fixture is supported")

        self.read_tool = RegisteredTool("read_file", "Read an allowed fixture", read_handler, validate_arguments=validate_read)
        self.write_tool = RegisteredTool("write_file", "Write synthetic output", write_handler, mutating=True,
                                         validate_arguments=validate_read)
        self.reason = {"s1": "Operator selected the synthetic fixture", "s3": "Operator checked fixture evidence"}
        self.args = {"s2": {"path": "demo.txt"}}

    def test_preview_does_not_call_tool(self):
        report = NexusExecutor({"read_file": self.read_tool}).run(make_plan(), tool_arguments=self.args, manual_evidence=self.reason)
        self.assertEqual(self.calls, [])
        self.assertEqual(report.status, "preview")
        self.assertEqual(report.tools_invoked, 0)
        self.assertEqual([x.status for x in report.records], ["attested_by_operator", "preview_only", "blocked_dependency"])

    def test_read_only_execution_with_operator_attestation(self):
        report = NexusExecutor({"read_file": self.read_tool}).run(
            make_plan(), execute_tools=True, tool_arguments=self.args, manual_evidence=self.reason,
        )
        self.assertEqual(report.status, "reported_complete")
        self.assertEqual(report.tools_invoked, 1)
        self.assertEqual(self.calls, [{"path": "demo.txt"}])
        self.assertEqual(report.records[1].evidence["fixture"], "demo.txt")
        self.assertEqual(report.records[-1].status, "attested_by_operator")
        self.assertEqual(report.model_request_id, "simulated-request-id")
        self.assertEqual(json.loads(json.dumps(report.to_dict()))["tools_invoked"], 1)

    def test_manual_reason_is_required(self):
        report = NexusExecutor({"read_file": self.read_tool}).run(make_plan(), execute_tools=True, tool_arguments=self.args)
        self.assertEqual(report.tools_invoked, 0)
        self.assertEqual(report.records[0].status, "awaiting_evidence")
        self.assertEqual(report.records[1].status, "blocked_dependency")

    def test_mutation_requires_step_approval(self):
        report = NexusExecutor({"write_file": self.write_tool}).run(
            make_plan(tool="write_file"), execute_tools=True, tool_arguments=self.args, manual_evidence=self.reason,
        )
        self.assertEqual(report.status, "approval_required")
        self.assertEqual(self.calls, [])
        self.assertEqual(report.records[1].status, "approval_required")

    def test_mutation_with_explicit_approval(self):
        report = NexusExecutor({"write_file": self.write_tool}).run(
            make_plan(tool="write_file"), execute_tools=True, approved_mutations={"s2"},
            tool_arguments=self.args, manual_evidence=self.reason,
        )
        self.assertEqual(report.status, "reported_complete")
        self.assertEqual(len(self.calls), 1)

    def test_approval_never_overrides_disabled_execution(self):
        report = NexusExecutor({"write_file": self.write_tool}).run(
            make_plan(tool="write_file"), approved_mutations={"s2"}, tool_arguments=self.args,
            manual_evidence=self.reason,
        )
        self.assertEqual(report.status, "preview")
        self.assertEqual(self.calls, [])

    def test_invalid_arguments_fail_before_any_execution(self):
        with self.assertRaisesRegex(NexusExecutionError, "arguments rejected"):
            NexusExecutor({"read_file": self.read_tool}).run(
                make_plan(), execute_tools=True, tool_arguments={"s2": {"path": "../sensitive.txt"}},
                manual_evidence=self.reason,
            )
        self.assertEqual(self.calls, [])

    def test_unknown_tool_rejected_without_invocation(self):
        with self.assertRaisesRegex(NexusExecutionError, "was not registered"):
            NexusExecutor({"read_file": self.read_tool}).run(make_plan(tool="run_shell"), execute_tools=True)
        self.assertEqual(self.calls, [])

    def test_exception_redacted_and_stops_dependents(self):
        def explode(_args):
            raise RuntimeError("SECRET_BEARER_123 raw problem")
        t = RegisteredTool("read_file", "Read fixture", explode, validate_arguments=lambda _: None)
        report = NexusExecutor({"read_file": t}).run(
            make_plan(), execute_tools=True, tool_arguments=self.args, manual_evidence=self.reason,
        )
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.records[1].status, "tool_error")
        self.assertNotIn("SECRET_BEARER_123", str(report.to_dict()))
        self.assertEqual(report.records[2].status, "blocked_dependency")

    def test_failed_result_blocks_verification(self):
        tool = RegisteredTool("read_file", "Read fixture", lambda _: ToolResult(False, "Fixture missing"),
                              validate_arguments=lambda _: None)
        report = NexusExecutor({"read_file": tool}).run(
            make_plan(), execute_tools=True, tool_arguments=self.args, manual_evidence=self.reason,
        )
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.records[1].status, "tool_failed")
        self.assertEqual(report.records[2].status, "blocked_dependency")

    def test_no_args_by_default(self):
        tool = RegisteredTool("read_file", "No arguments", lambda _: ToolResult(True, "OK"))
        with self.assertRaisesRegex(NexusExecutionError, "does not accept arguments"):
            NexusExecutor({"read_file": tool}).run(make_plan(), tool_arguments=self.args)

    def test_unknown_approval_rejected(self):
        with self.assertRaisesRegex(NexusExecutionError, "approval refers"):
            NexusExecutor({"read_file": self.read_tool}).run(make_plan(), approved_mutations={"s99"})

    def test_unknown_step_arguments_rejected(self):
        with self.assertRaisesRegex(NexusExecutionError, "unknown or incompatible"):
            NexusExecutor({"read_file": self.read_tool}).run(make_plan(), tool_arguments={"s1": {"path": "demo.txt"}})

    def test_invalid_plan_is_revalidated_even_when_hand_constructed(self):
        plan = NexusPlan(goal="Bad plan", steps=(
            PlanStep("s1", "tool", "A", "Do A", "A", ("s2",), "read_file"),
            PlanStep("s2", "verify", "B", "Do B", "B", ("s1",)),
        ))
        with self.assertRaisesRegex(NexusExecutionError, "dependencies"):
            NexusExecutor({"read_file": self.read_tool}).run(plan)

    def test_final_verify_is_required(self):
        plan = NexusPlan(goal="Bad", steps=(
            PlanStep("s1", "reason", "A", "Do A", "A"),
            PlanStep("s2", "reason", "B", "Do B", "B", ("s1",)),
        ))
        with self.assertRaisesRegex(NexusExecutionError, "last step"):
            NexusExecutor().run(plan)

    def test_empty_evidence_rejected(self):
        with self.assertRaisesRegex(NexusExecutionError, "invalid evidence"):
            NexusExecutor({"read_file": self.read_tool}).run(make_plan(), tool_arguments=self.args, manual_evidence={"s1": ""})

    def test_non_json_tool_inputs_rejected(self):
        with self.assertRaisesRegex(NexusExecutionError, "JSON-compatible"):
            NexusExecutor({"read_file": self.read_tool}).run(make_plan(), tool_arguments={"s2": {"data": object()}})

    def test_tool_names_match_planner_allowlist(self):
        self.assertEqual(NexusExecutor({"read_file": self.read_tool}).allowed_tools(),
                         {"read_file": "Read an allowed fixture"})

    def test_registration_rejects_wrong_id(self):
        with self.assertRaisesRegex(NexusExecutionError, "registration mismatch"):
            NexusExecutor({"wrong_name": self.read_tool})

    def test_tool_result_must_have_valid_contract(self):
        tool = RegisteredTool("read_file", "Read fixture", lambda _: "unexpected", validate_arguments=lambda _: None)
        report = NexusExecutor({"read_file": tool}).run(
            make_plan(), execute_tools=True, tool_arguments=self.args, manual_evidence=self.reason,
        )
        self.assertEqual(report.records[1].status, "tool_error")

    def test_invalid_second_step_arguments_preflight_before_first_handler(self):
        plan = NexusPlan(goal="Two tools", steps=(
            PlanStep("s1", "tool", "First", "Read first", "First read", tool="read_file"),
            PlanStep("s2", "tool", "Second", "Read second", "Second read", ("s1",), "read_file"),
            PlanStep("s3", "verify", "Verify", "Check", "Evidence", ("s2",)),
        ))
        with self.assertRaisesRegex(NexusExecutionError, "arguments rejected"):
            NexusExecutor({"read_file": self.read_tool}).run(
                plan, execute_tools=True,
                tool_arguments={"s1": {"path": "demo.txt"}, "s2": {"path": "unsafe.txt"}},
            )
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
