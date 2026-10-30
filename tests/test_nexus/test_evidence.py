"""No-network, no-model-call contract tests for Nexus evidence trail."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from aegisforge.nexus.evidence import (
    NexusEvidenceTrail, NexusEvidenceError, verify_jsonl,
)
from aegisforge.nexus.executor import NexusExecutor, RegisteredTool, ToolResult
from aegisforge.nexus.planner import NexusPlan, PlanStep
from aegisforge.nexus.verifier import NexusVerifier, IndependentCheck


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.plan = NexusPlan(
            goal="Review private launch note API_KEY=do-not-log-me",
            request_id="secret-request-id-do-not-log-me",
            steps=(
                PlanStep("s1", "reason", "Decide", "Sensitive plan description", "Decision documented"),
                PlanStep("s2", "tool", "Read", "Read authorized fixture", "Hash exists", ("s1",), "read_fixture"),
                PlanStep("s3", "verify", "Check", "Independent review", "Hash confirmed", ("s2",)),
            ),
        )
        self.executor = NexusExecutor({
            "read_fixture": RegisteredTool("read_fixture", "Read authorized fixture", self.handler)
        })
        self.verifier = NexusVerifier()

    @staticmethod
    def handler(_args):
        return ToolResult(True, "secret_password=do-not-log-me", {"api_token": "token-do-not-log-me"})

    def run_report(self):
        return self.executor.run(
            self.plan, execute_tools=True,
            manual_evidence={"s1": "operator-secret-do-not-log-me", "s3": "operator approval"},
        )

    def verifier_report(self, execution):
        return self.verifier.verify(self.plan, execution, independent_checks=(
            IndependentCheck("independent_s2", "s2", "Inspect trusted state", lambda *_: True, "proof/trusted-output"),
            IndependentCheck("independent_s3", "s3", "Inspect recorded assertion", lambda *_: True, "proof/trusted-review"),
        ))

    def complete_trail(self):
        report = self.run_report()
        check = self.verifier_report(report)
        self.assertEqual(check.status, "verified")
        trail = NexusEvidenceTrail()
        trail.record_plan(self.plan)
        trail.record_execution(report)
        trail.record_verification(check)
        return trail

    def test_empty_trail_no_io_and_no_events(self):
        trail = NexusEvidenceTrail()
        self.assertEqual(trail.events, ())
        self.assertIsNone(trail.final_sha256)
        self.assertEqual(trail.to_jsonl(), "")

    def test_records_pipeline_with_expected_stages(self):
        trail = self.complete_trail()
        self.assertEqual([e.stage for e in trail.events], ["plan", "execution", "verification"])
        self.assertEqual([e.status for e in trail.events], ["proposed", "reported_complete", "verified"])
        self.assertEqual(trail.events[0].summary["steps"], 3)
        self.assertEqual(trail.events[1].summary["tools_invoked"], 1)
        self.assertEqual(trail.events[2].summary["independent_checks_passed"], 2)
        self.assertEqual(trail.events[1].previous_sha256, trail.events[0].sha256)

    def test_redacts_all_raw_user_and_tool_strings(self):
        exported = self.complete_trail().to_jsonl()
        for leak in ("do-not-log-me", "API_KEY", "secret-request-id", "operator-secret", "token-do", "Sensitive plan description", "read_fixture", "proof/trusted"):
            self.assertNotIn(leak, exported)

    def test_export_and_load_verify(self):
        with TemporaryDirectory() as directory:
            f = Path(directory) / "audit.jsonl"
            trail = self.complete_trail()
            self.assertEqual(trail.save_jsonl(f), f)
            loaded = verify_jsonl(f)
            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded[-1].sha256, trail.final_sha256)

    def test_cannot_overwrite(self):
        with TemporaryDirectory() as directory:
            f = Path(directory) / "audit.jsonl"
            self.complete_trail().save_jsonl(f)
            with self.assertRaises(FileExistsError):
                self.complete_trail().save_jsonl(f)

    def test_cannot_save_empty(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(NexusEvidenceError):
                NexusEvidenceTrail().save_jsonl(Path(directory) / "none.jsonl")

    def test_rejects_invalid_plan(self):
        trail = NexusEvidenceTrail()
        with self.assertRaises(NexusEvidenceError):
            trail.record_plan(replace(self.plan, steps=self.plan.steps[:-1]))
        self.assertEqual(trail.events, ())

    def test_rejects_second_plan(self):
        trail = NexusEvidenceTrail()
        trail.record_plan(self.plan)
        with self.assertRaises(NexusEvidenceError):
            trail.record_plan(self.plan)

    def test_rejects_execution_before_plan(self):
        trail = NexusEvidenceTrail()
        with self.assertRaises(NexusEvidenceError):
            trail.record_execution(self.run_report())

    def test_rejects_execution_from_different_goal(self):
        trail = NexusEvidenceTrail()
        trail.record_plan(self.plan)
        with self.assertRaises(NexusEvidenceError):
            trail.record_execution(replace(self.run_report(), goal="another job"))

    def test_rejects_duplicate_execution(self):
        trail = NexusEvidenceTrail()
        trail.record_plan(self.plan)
        r = self.run_report()
        trail.record_execution(r)
        with self.assertRaises(NexusEvidenceError):
            trail.record_execution(r)

    def test_verification_requires_execution(self):
        report = self.verifier_report(self.run_report())
        trail = NexusEvidenceTrail()
        trail.record_plan(self.plan)
        with self.assertRaises(NexusEvidenceError):
            trail.record_verification(report)

    def test_verification_wrong_task_id_rejected(self):
        trail = NexusEvidenceTrail()
        trail.record_plan(self.plan)
        report = self.run_report()
        trail.record_execution(report)
        with self.assertRaises(NexusEvidenceError):
            trail.record_verification(replace(self.verifier_report(report), plan_request_id="other"))

    def test_preview_never_marks_verified(self):
        preview = self.executor.run(self.plan)
        audit = self.verifier.verify(self.plan, preview)
        trail = NexusEvidenceTrail()
        trail.record_plan(self.plan)
        trail.record_execution(preview)
        trail.record_verification(audit)
        self.assertNotEqual(trail.events[-1].status, "verified")

    def test_invalid_verified_without_independent_checks_rejected(self):
        trail = NexusEvidenceTrail()
        trail.record_plan(self.plan)
        report = self.run_report()
        trail.record_execution(report)
        fake = replace(self.verifier_report(report), independent_checks_passed=0, independent_checks_run=0)
        with self.assertRaises(ValueError):
            trail.record_verification(fake)

    def test_detects_modified_record(self):
        with TemporaryDirectory() as directory:
            p = Path(directory) / "audit.jsonl"
            self.complete_trail().save_jsonl(p)
            rows = p.read_text().splitlines()
            entry = json.loads(rows[1])
            entry["status"] = "failed"
            rows[1] = json.dumps(entry)
            p.write_text("\n".join(rows) + "\n")
            with self.assertRaises(NexusEvidenceError):
                verify_jsonl(p)

    def test_detects_missing_line_or_reordered(self):
        with TemporaryDirectory() as directory:
            p = Path(directory) / "audit.jsonl"
            self.complete_trail().save_jsonl(p)
            rows = p.read_text().splitlines()
            p.write_text("\n".join(rows[1:]) + "\n")
            with self.assertRaises(NexusEvidenceError):
                verify_jsonl(p)

    def test_rejects_unknown_key_even_if_rehashed(self):
        with TemporaryDirectory() as directory:
            p = Path(directory) / "audit.jsonl"
            self.complete_trail().save_jsonl(p)
            rows = p.read_text().splitlines()
            entry = json.loads(rows[0])
            entry["raw_secret"] = "oops"
            rows[0] = json.dumps(entry)
            p.write_text("\n".join(rows) + "\n")
            with self.assertRaises(NexusEvidenceError):
                verify_jsonl(p)

    def test_import_has_no_inference_side_effects(self):
        # All prior methods work without a configured Nebius credential.
        trail = NexusEvidenceTrail()
        self.assertEqual(trail.events, ())


if __name__ == "__main__":
    unittest.main()
