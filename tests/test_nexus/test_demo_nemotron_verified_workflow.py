"""Offline-only tests for the live-plan-to-trusted-tool bridge."""
from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

from aegisforge.nexus.evidence import verify_jsonl
from aegisforge.nexus.nebius_client import NebiusReply

_ROOT = Path(__file__).resolve().parents[2]
_DIR = _ROOT / 'scripts' / 'nexus'
sys.path.insert(0, str(_DIR))
spec = importlib.util.spec_from_file_location('nexus_live_bridge', _DIR / 'demo_nemotron_verified_workflow.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class RecordingClient:
    def __init__(self, transform=None, raises=False):
        self.calls = 0
        self.transform = transform
        self.raises = raises
        self.base = bridge.trusted_demo._FixedOfflinePlannerClient()

    def chat(self, messages, *, max_tokens=512, temperature=0.2):
        self.calls += 1
        if self.raises:
            raise RuntimeError('Simulated provider error')
        initial = self.base.chat(messages, max_tokens=max_tokens, temperature=temperature)
        data = json.loads(initial.content)
        if self.transform:
            self.transform(data)
        return NebiusReply(content=json.dumps(data), model='MOCK-NOT-ACTUAL-NEMOTRON',
                           request_id='synthetic-test-id', finish_reason='stop',
                           usage={'total_tokens': 0})


class LiveBridgeTests(unittest.TestCase):
    def test_offline_preview_has_no_execution(self):
        output = bridge.run_workflow()
        self.assertEqual(output['status'], 'review_only')
        self.assertEqual(output['tools_invoked'], 0)
        self.assertEqual(output['verification_status'], 'not_run')
        self.assertFalse(output['live_inference'])

    def test_offline_approved_execute_is_verified(self):
        output = bridge.run_workflow(execute=True, approved=True)
        self.assertEqual(output['status'], 'completed')
        self.assertEqual(output['verification_status'], 'verified')
        self.assertEqual(output['tools_invoked'], 2)
        self.assertEqual(output['independent_checks_run'], 3)
        self.assertEqual(output['independent_checks_passed'], 3)
        self.assertTrue(output['temporary_workspace_removed_on_exit'])

    def test_execute_without_approval_denied_before_planning(self):
        client = RecordingClient()
        with self.assertRaises(PermissionError):
            bridge.run_workflow(execute=True, client=client)
        self.assertEqual(client.calls, 0)

    def test_review_declined_blocks_execution(self):
        client = RecordingClient()
        result = bridge.run_workflow(execute=True, review_callback=lambda p: False, client=client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result['tools_invoked'], 0)
        self.assertEqual(result['status'], 'operator_declined')

    def test_review_yes_allows_fixed_tools(self):
        observed = []
        def approve(plan):
            observed.append(plan['steps'])
            return True
        result = bridge.run_workflow(execute=True, review_callback=approve)
        self.assertEqual(result['verification_status'], 'verified')
        self.assertEqual(observed[0][1]['tool'], 'nexus.demo.write')

    def test_model_only_proposes_plan_and_inference_once(self):
        client = RecordingClient()
        result = bridge.run_workflow(live=True, execute=True, approved=True, client=client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result['mode'], 'injected_test_client')
        self.assertFalse(result['live_inference'])
        self.assertEqual(result['verification_status'], 'verified')

    def test_reordered_tool_steps_fail_closed(self):
        client = RecordingClient(lambda plan: plan['steps'][1].update(tool='nexus.demo.tests'))
        with self.assertRaises(bridge.UnsafePlanError):
            bridge.run_workflow(execute=True, approved=True, client=client)
        self.assertEqual(client.calls, 1)

    def test_unexpected_dependency_fails_closed(self):
        client = RecordingClient(lambda plan: plan['steps'][2].update(depends_on=['s1']))
        with self.assertRaises(bridge.UnsafePlanError):
            bridge.run_workflow(execute=True, approved=True, client=client)

    def test_valid_but_fewer_tools_fail_closed(self):
        def mutate(plan):
            plan['steps'][2]['kind'] = 'reason'
            plan['steps'][2]['tool'] = None
        with self.assertRaises(bridge.UnsafePlanError):
            bridge.run_workflow(execute=True, approved=True, client=RecordingClient(mutate))

    def test_mock_provider_failure_does_not_retry(self):
        client = RecordingClient(raises=True)
        with self.assertRaises(RuntimeError):
            bridge.run_workflow(client=client)
        self.assertEqual(client.calls, 1)

    def test_broken_fixture_does_not_verify(self):
        with patch.object(bridge.trusted_demo, 'SOURCE_CODE',
                          bridge.trusted_demo.SOURCE_CODE.replace('return max(low, min(value, high))', 'return value')):
            report = bridge.run_workflow(execute=True, approved=True)
        self.assertEqual(report['verification_status'], 'failed')
        self.assertEqual(report['status'], 'not_verified')

    def test_exported_evidence_verified_and_no_api_key(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict('os.environ', {'NEBIUS_API_KEY': 'PRIVATE_TEST_CREDENTIAL_123'}):
                report = bridge.run_workflow(execute=True, approved=True,
                                             save_evidence=True, evidence_root=Path(folder))
            self.assertEqual(report['verification_status'], 'verified')
            self.assertEqual(len(verify_jsonl(report['audit_path'])), 3)
            saved = Path(report['summary_path']).read_text(encoding='utf-8')
            saved += Path(report['audit_path']).read_text(encoding='utf-8')
            self.assertNotIn('PRIVATE_TEST_CREDENTIAL_123', saved)
            self.assertNotIn('plan_steps', json.loads(Path(report['summary_path']).read_text()))

    def test_no_file_export_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge.run_workflow(execute=True, approved=True,
                                save_evidence=False, evidence_root=Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_export_requires_execution(self):
        with self.assertRaises(ValueError):
            bridge.run_workflow(save_evidence=True)

    def test_model_text_is_not_executed(self):
        def mutate(plan):
            plan['steps'][1]['description'] = 'Here is a different proposed source script, do not trust it'
        report = bridge.run_workflow(execute=True, approved=True, client=RecordingClient(mutate))
        self.assertEqual(report['verification_status'], 'verified')
        self.assertTrue(report['fixed_fixture_only'])

    def test_digest_can_bind_without_leaking_raw_plan(self):
        client = RecordingClient()
        report = bridge.run_workflow(client=client)
        self.assertEqual(len(report['plan_sha256']), 64)
        self.assertEqual(len(report['plan_steps']), 4)


if __name__ == '__main__':
    unittest.main()
