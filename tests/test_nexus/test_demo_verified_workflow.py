"""Offline integration tests for scripts/nexus/demo_verified_workflow.py."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from aegisforge.nexus.evidence import verify_jsonl

FILE = Path(__file__).resolve().parents[2] / "scripts" / "nexus" / "demo_verified_workflow.py"
spec = importlib.util.spec_from_file_location("nexus_verified_demo", FILE)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class NexusVerifiedWorkflowTests(unittest.TestCase):
    def test_fixture_is_not_nemotron(self):
        result = demo.run_demo()
        self.assertFalse(result["live_inference"])
        self.assertEqual(result["planner_model"], "local-fixture-not-nemotron")

    def test_preview_is_non_mutating(self):
        result = demo.run_demo()
        self.assertEqual(result["mode"], "preview")
        self.assertEqual(result["tools_invoked"], 0)
        self.assertFalse(result["files_created"])

    def test_preview_never_calls_cloud_even_if_key_set(self):
        with patch.dict("os.environ", {"NEBIUS_API_KEY": "fake-must-not-be-read"}):
            result = demo.run_demo()
        self.assertFalse(result["live_inference"])

    def test_fixture_sha_validation(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            self.assertFalse(demo._verify_exact_fixture(work))
            (work / demo.SOURCE_NAME).write_bytes(demo.SOURCE_CODE.encode("utf-8"))
            (work / demo.TEST_NAME).write_bytes(demo.TEST_CODE.encode("utf-8"))
            self.assertTrue(demo._verify_exact_fixture(work))
            self.assertTrue(demo._run_static_tests(work))

    def test_modification_detected(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            (work / demo.SOURCE_NAME).write_bytes((demo.SOURCE_CODE + "\n# modified\n").encode("utf-8"))
            (work / demo.TEST_NAME).write_bytes(demo.TEST_CODE.encode("utf-8"))
            self.assertFalse(demo._verify_exact_fixture(work))
            self.assertFalse(demo._run_static_tests(work))

    def test_executor_requires_approval(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            orch = demo._build_orchestrator(work)
            prepared = orch.prepare("offline approved fixture")
            report = orch.executor.run(prepared.plan, execute_tools=True,
                                       manual_evidence={"s1": "host reviewed", "s4": "host reviewed"})
            self.assertEqual(report.status, "approval_required")
            self.assertEqual(report.tools_invoked, 0)
            self.assertFalse((work / demo.SOURCE_NAME).exists())

    def test_real_static_tools_verify(self):
        result = demo.run_demo(execute=True)
        self.assertEqual(result["execution_status"], "reported_complete")
        self.assertEqual(result["verification_status"], "verified")
        self.assertEqual(result["tools_invoked"], 2)
        self.assertEqual(result["independent_checks_passed"], 3)
        self.assertEqual(result["audit_events"], 3)

    def test_broken_fixed_fixture_is_not_verified(self):
        broken = demo.SOURCE_CODE.replace("return max(low, min(value, high))", "return value")
        with patch.object(demo, "SOURCE_CODE", broken):
            result = demo.run_demo(execute=True)
        self.assertEqual(result["verification_status"], "failed")
        self.assertEqual(result["tools_invoked"], 2)

    def test_evidence_exports_verify_and_no_secret(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict("os.environ", {"NEBIUS_API_KEY": "SENSITIVE_TEST_TOKEN_DO_NOT_WRITE"}):
                result = demo.run_demo(execute=True, save_evidence=True, evidence_root=Path(root))
            self.assertEqual(result["verification_status"], "verified")
            events = verify_jsonl(result["audit_path"])
            self.assertEqual(len(events), 3)
            exported = Path(result["summary_path"]).read_text(encoding="utf-8")
            exported += Path(result["audit_path"]).read_text(encoding="utf-8")
            self.assertNotIn("SENSITIVE_TEST_TOKEN_DO_NOT_WRITE", exported)
            self.assertEqual(json.loads(Path(result["summary_path"]).read_text())["verification_status"], "verified")

    def test_no_files_written_without_export(self):
        with tempfile.TemporaryDirectory() as root:
            result = demo.run_demo(execute=True, save_evidence=False, evidence_root=Path(root))
            self.assertEqual(result["verification_status"], "verified")
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
