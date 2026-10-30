"""Offline tests for NexusVerifier. No Nebius token, network or cloud required."""

from __future__ import annotations

from dataclasses import replace
import unittest

from aegisforge.nexus.planner import NexusPlan, PlanStep
from aegisforge.nexus.executor import (
    ExecutionReport, NexusExecutor, RegisteredTool, StepRecord, ToolResult,
)
from aegisforge.nexus.verifier import (
    EvidenceExpectation, IndependentCheck, NexusVerificationError, NexusVerifier,
)


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.plan = NexusPlan(
            goal="Record a verified sample artifact",
            request_id="req-42",
            steps=(
                PlanStep("s1", "reason", "Identify target", "Specify artifact", "Artifact name chosen"),
                PlanStep("s2", "tool", "Read artifact", "Read stable artifact", "Hash recorded", ("s1",), "read_sample"),
                PlanStep("s3", "verify", "Verify hash", "Independently check artifact", "Hash matches", ("s2",)),
            ),
        )
        self.calls = []
        def handler(_args):
            self.calls.append("read_sample")
            return ToolResult(True, "Read stable artifact", {"sha256": "f" * 64})
        self.executor = NexusExecutor({
            "read_sample": RegisteredTool("read_sample", "Read immutable sample", handler),
        })
        self.verifier = NexusVerifier()

    def completed_report(self):
        return self.executor.run(
            self.plan,
            execute_tools=True,
            manual_evidence={"s1": "Identifier selected", "s3": "I reviewed the result"},
        )

    def checks(self):
        # Controlled read-only checks in tests: production checks MUST inspect
        # independent trusted state rather than blindly returning True.
        return (
            IndependentCheck("artifact_hash", "s2", "Match hash against independent index", lambda s, r: s.id == "s2" and r.evidence.get("sha256") == "f" * 64, "checks/sha256-index.txt"),
            IndependentCheck("goal_check", "s3", "Verify externally obtained artifact", lambda s, r: s.id == "s3" and r.status == "attested_by_operator", "checks/independent-run.txt"),
        )

    def test_default_does_not_claim_verified(self):
        result = self.verifier.verify(self.plan, self.completed_report())
        self.assertEqual(result.status, "inconclusive")
        self.assertEqual(result.independent_checks_run, 0)

    def test_independent_checks_can_verify_all_covered_steps(self):
        r = self.completed_report()
        verdict = self.verifier.verify(self.plan, r,
            expectations=(EvidenceExpectation("s2", "sha256", "f" * 64),),
            independent_checks=self.checks())
        self.assertEqual(verdict.status, "verified")
        self.assertEqual(verdict.independent_checks_passed, 2)
        self.assertEqual(verdict.independent_checks_run, 2)
        self.assertEqual(verdict.to_dict()["plan_request_id"], "req-42")
        self.assertNotIn("f" * 64, str(verdict.to_dict()))

    def test_missing_one_independent_check_is_inconclusive(self):
        result = self.verifier.verify(self.plan, self.completed_report(), independent_checks=self.checks()[:1])
        self.assertEqual(result.status, "inconclusive")
        self.assertIn("s3", [f.step_id for f in result.findings if f.kind == "coverage"])

    def test_false_check_makes_failure(self):
        checks = list(self.checks())
        checks[0] = replace(checks[0], handler=lambda *_: False)
        result = self.verifier.verify(self.plan, self.completed_report(), independent_checks=checks)
        self.assertEqual(result.status, "failed")
        self.assertTrue(any(f.code == "independent_evidence_failed" for f in result.findings))

    def test_callback_exception_is_redacted(self):
        def broken(*_):
            raise RuntimeError("confidential_token_should_not_be_leaked")
        checks = (replace(self.checks()[0], handler=broken), self.checks()[1])
        result = self.verifier.verify(self.plan, self.completed_report(), independent_checks=checks)
        self.assertEqual(result.status, "inconclusive")
        self.assertNotIn("confidential_token_should_not_be_leaked", str(result.to_dict()))

    def test_nonbool_check_is_inconclusive(self):
        checks = (replace(self.checks()[0], handler=lambda *_: 1), self.checks()[1])
        result = self.verifier.verify(self.plan, self.completed_report(), independent_checks=checks)
        self.assertEqual(result.status, "inconclusive")

    def test_preview_checks_not_called(self):
        record = []
        preview = self.executor.run(self.plan, manual_evidence={"s1": "Selected"})
        chk = IndependentCheck("spy", "s2", "Spy", lambda *_: record.append(1) or True, "checks/test-log")
        result = self.verifier.verify(self.plan, preview, independent_checks=(chk,))
        self.assertEqual(result.status, "inconclusive")
        self.assertEqual(record, [])
        self.assertEqual(self.calls, [])

    def test_failed_tool_report_does_not_run_checks(self):
        bad_executor = NexusExecutor({"read_sample": RegisteredTool(
            "read_sample", "Bad fake handler", lambda _: ToolResult(False, "failed"))})
        bad = bad_executor.run(self.plan, execute_tools=True, manual_evidence={"s1": "Selected"})
        spy = []
        verdict = self.verifier.verify(self.plan, bad, independent_checks=(
            IndependentCheck("spy", "s2", "No call", lambda *_: spy.append(1) or True, "test/none"),
        ))
        self.assertEqual(verdict.status, "failed")
        self.assertEqual(spy, [])

    def test_reported_evidence_mismatch_fails(self):
        result = self.verifier.verify(self.plan, self.completed_report(),
            expectations=(EvidenceExpectation("s2", "sha256", "0" * 64),),
            independent_checks=self.checks())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.independent_checks_run, 0)

    def test_reported_evidence_missing_inconclusive(self):
        result = self.verifier.verify(self.plan, self.completed_report(),
            expectations=(EvidenceExpectation("s2", "nonexistent", "x"),),
            independent_checks=self.checks())
        self.assertEqual(result.status, "inconclusive")
        self.assertEqual(result.independent_checks_run, 0)

    def test_matching_expectation_alone_not_enough(self):
        result = self.verifier.verify(self.plan, self.completed_report(),
            expectations=(EvidenceExpectation("s2", "sha256", "f" * 64),))
        self.assertEqual(result.status, "inconclusive")

    def test_wrong_goal_rejected(self):
        report = replace(self.completed_report(), goal="Different task")
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, report)

    def test_wrong_request_id_rejected(self):
        report = replace(self.completed_report(), model_request_id="different")
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, report)

    def test_record_kind_mismatch_rejected(self):
        report = self.completed_report()
        records = list(report.records)
        records[1] = replace(records[1], kind="verify")
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, replace(report, records=tuple(records)))

    def test_record_missing_rejected(self):
        report = self.completed_report()
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, replace(report, records=report.records[:2]))

    def test_wrong_status_rejected(self):
        report = self.completed_report()
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, replace(report, status="verified"))

    def test_wrong_invocation_count_rejected(self):
        report = self.completed_report()
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, replace(report, tools_invoked=0))

    def test_bypassed_dependency_rejected(self):
        report = self.executor.run(self.plan, execute_tools=True)
        records = list(report.records)
        records[1] = replace(records[1], status="completed")
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, replace(report, records=tuple(records)))

    def test_operator_attestation_without_payload_rejected(self):
        report = self.completed_report()
        records = list(report.records)
        records[2] = replace(records[2], evidence={})
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, replace(report, records=tuple(records)))

    def test_duplicate_expectations_rejected(self):
        exp = EvidenceExpectation("s2", "sha256", "f" * 64)
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, self.completed_report(), expectations=(exp, exp))

    def test_expectations_on_manual_steps_rejected(self):
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, self.completed_report(),
                expectations=(EvidenceExpectation("s3", "operator_attestation", "x"),))

    def test_duplicate_check_id_rejected(self):
        check = self.checks()[0]
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, self.completed_report(), independent_checks=(check, check))

    def test_bad_check_evidence_reference_rejected(self):
        chk = replace(self.checks()[0], evidence_ref="https://x.example/?secret=TOKEN")
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, self.completed_report(), independent_checks=(chk,))

    def test_check_on_reason_step_rejected(self):
        chk = replace(self.checks()[0], step_id="s1")
        with self.assertRaises(NexusVerificationError):
            self.verifier.verify(self.plan, self.completed_report(), independent_checks=(chk,))

    def test_zero_tool_plan_requires_independent_final_check(self):
        plan = NexusPlan(goal="Summarize", steps=(
            PlanStep("s1", "reason", "Reason", "Summarize", "Good"),
            PlanStep("s2", "verify", "Verify", "Check summary", "Correct", ("s1",)),
        ))
        report = NexusExecutor().run(plan, manual_evidence={"s1": "done", "s2": "reviewed"})
        self.assertEqual(self.verifier.verify(plan, report).status, "inconclusive")
        check = IndependentCheck("summary", "s2", "Check against reference", lambda *_: True, "tests/snapshot.txt")
        self.assertEqual(self.verifier.verify(plan, report, independent_checks=(check,)).status, "verified")


if __name__ == "__main__":
    unittest.main()
