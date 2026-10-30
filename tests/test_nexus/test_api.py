"""Nexus HTTP API tests: all offline, using an injected fake Nemotron model."""

from __future__ import annotations

import json
import unittest

from starlette.testclient import TestClient

from aegisforge.nexus.api import create_app, NexusAPIError
from aegisforge.nexus.executor import NexusExecutor, RegisteredTool, ToolResult
from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.orchestrator import NexusOrchestrator
from aegisforge.nexus.planner import NexusPlanner


TOKEN = "nexus-test-bearer-token-0123456789abcdef"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class FakeNebius:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, max_tokens=512, temperature=None):
        self.calls += 1
        return NebiusReply(content=json.dumps({
            "goal": "Evaluate a harmless synthetic exercise",
            "steps": [
                {"id": "s1", "kind": "reason", "title": "Outline",
                 "description": "Propose a synthetic approach", "tool": None,
                 "depends_on": [], "success_criteria": "Operator acknowledges proposal"},
                {"id": "s2", "kind": "verify", "title": "Check",
                 "description": "Request independent evidence", "tool": None,
                 "depends_on": ["s1"], "success_criteria": "An independent check is recorded"},
            ],
            "assumptions": [], "risks": [],
        }), model="fake-nemotron", finish_reason="stop", usage={"total_tokens": 4})


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNebius()
        self.nexus = NexusOrchestrator(NexusPlanner(self.fake), NexusExecutor())
        self.now = [100.0]
        self.app = create_app(self.nexus, api_token=TOKEN, clock=lambda: self.now[0], ttl_seconds=120)
        self.client = TestClient(self.app, raise_server_exceptions=True)

    def prepare(self, task="Make a harmless plan"):
        return self.client.post("/nexus/prepare", json={"task": task}, headers=HEADERS)

    def test_liveness_without_credentials(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_no_inference_during_app_creation(self):
        self.assertEqual(self.fake.calls, 0)
        self.client.get("/health")
        self.assertEqual(self.fake.calls, 0)

    def test_auth_missing_in_every_private_route(self):
        for path in ("/nexus/capabilities", "/nexus/plans/anything"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 401)
        r = self.client.post("/nexus/prepare", json={"task": "x"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.fake.calls, 0)

    def test_auth_wrong_or_basic(self):
        for authorization in ("Bearer wrong", "Basic 123", "bearer " + TOKEN, "Bearer "):
            self.assertEqual(self.client.get("/nexus/capabilities", headers={"Authorization": authorization}).status_code, 401)

    def test_capabilities_restrict_tool_execution(self):
        j = self.client.get("/nexus/capabilities", headers=HEADERS).json()
        self.assertFalse(j["tool_execution_over_http"])
        self.assertFalse(j["independent_verification_over_http"])

    def test_full_flow_no_tools_and_sanitized_result(self):
        r = self.prepare("Synthetic task")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.fake.calls, 1)
        plan_id = r.json()["plan_id"]
        self.assertGreater(len(plan_id), 24)
        url = f"/nexus/plans/{plan_id}"
        self.assertEqual(self.client.get(url, headers=HEADERS).json()["status"], "prepared")
        for _ in range(2):
            p = self.client.post(url + "/preview", json={}, headers=HEADERS)
            self.assertEqual(p.status_code, 200, p.text)
            self.assertEqual(p.json()["tools_invoked"], 0)
        o = self.client.post(url + "/run", json={"manual_evidence": {"s1": "Acknowledged", "s2": "Reviewed"}}, headers=HEADERS)
        self.assertEqual(o.status_code, 200, o.text)
        self.assertEqual(o.json()["verification_status"], "inconclusive")
        self.assertEqual(o.json()["tools_invoked"], 0)
        self.assertEqual(o.json()["audit_events"], 3)
        self.assertNotIn("Acknowledged", o.text)
        self.assertEqual(self.client.get(url + "/result", headers=HEADERS).json()["audit_events"], 3)
        evidence = self.client.get(url + "/evidence", headers=HEADERS)
        self.assertEqual(evidence.status_code, 200)
        self.assertEqual(len(evidence.text.splitlines()), 3)
        self.assertNotIn("Acknowledged", evidence.text)
        self.assertNotIn("Synthetic task", evidence.text)
        self.assertEqual(self.client.post(url + "/run", json={}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.fake.calls, 1)

    def test_refuse_tools_even_if_requested(self):
        r = self.prepare()
        url = "/nexus/plans/" + r.json()["plan_id"] + "/run"
        self.assertEqual(self.client.post(url, json={"execute_tools": True}, headers=HEADERS).status_code, 403)
        self.assertEqual(self.client.post(url, json={"approved_mutations": ["s1"]}, headers=HEADERS).status_code, 422)
        self.assertEqual(self.client.post(url, json={"tool_arguments": {}}, headers=HEADERS).status_code, 422)
        self.assertEqual(self.client.post(url, json={}, headers=HEADERS).status_code, 200)

    def test_bad_input_does_not_spend_inference(self):
        for data in ({"task": ""}, {"task": 123}, {"task": "ok", "unexpected": True},
                     {"task": "ok", "constraints": "bad"}, {"task": "ok", "constraints": [""]}):
            r = self.client.post("/nexus/prepare", json=data, headers=HEADERS)
            self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self.fake.calls, 0)

    def test_no_nan_in_json(self):
        r = self.client.post("/nexus/prepare", content='{"task": "ok", "NaN": NaN}',
                             headers={**HEADERS, "Content-Type":"application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.fake.calls, 0)

    def test_unknown_plan_id_no_privacy_leak(self):
        r = self.client.get("/nexus/plans/not-a-real-id", headers=HEADERS)
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(TOKEN, r.text)

    def test_token_never_echoed_in_reply(self):
        r = self.prepare("Use secret token " + TOKEN + " as a synthetic string")
        self.assertEqual(r.status_code, 201)
        self.assertNotIn(TOKEN, r.headers.get("cache-control", ""))
        r2 = self.client.get("/nexus/capabilities", headers=HEADERS)
        self.assertNotIn(TOKEN, r2.text)

    def test_malformed_json_and_wrong_type(self):
        r = self.client.post("/nexus/prepare", content="{", headers={**HEADERS,"Content-Type":"application/json"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/nexus/prepare", content="[]", headers={**HEADERS,"Content-Type":"application/json"})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/nexus/prepare", content="hi", headers={**HEADERS,"Content-Type":"text/plain"})
        self.assertEqual(r.status_code, 415)

    def test_large_request_rejected_without_inference(self):
        r = self.client.post("/nexus/prepare", json={"task": "A"*20000}, headers=HEADERS)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.fake.calls, 0)

    def test_plan_expiration(self):
        plan_id = self.prepare().json()["plan_id"]
        self.now[0] += 121
        self.assertEqual(self.client.get(f"/nexus/plans/{plan_id}", headers=HEADERS).status_code, 404)

    def test_capacity_bound(self):
        app = create_app(self.nexus, api_token=TOKEN, max_plans=1)
        c = TestClient(app)
        self.assertEqual(c.post("/nexus/prepare", json={"task":"one"},headers=HEADERS).status_code,201)
        self.assertEqual(c.post("/nexus/prepare", json={"task":"two"},headers=HEADERS).status_code,429)

    def test_pending_result_before_run_rejected(self):
        plan_id = self.prepare().json()["plan_id"]
        url = f"/nexus/plans/{plan_id}"
        self.assertEqual(self.client.get(url + "/result", headers=HEADERS).status_code,409)
        self.assertEqual(self.client.get(url + "/evidence", headers=HEADERS).status_code,409)

    def test_preview_after_run_rejected(self):
        plan_id = self.prepare().json()["plan_id"]
        url = f"/nexus/plans/{plan_id}"
        self.client.post(url + "/run",json={},headers=HEADERS)
        self.assertEqual(self.client.post(url + "/preview",json={},headers=HEADERS).status_code,409)

    def test_no_query_parameter_auth(self):
        r = self.client.get("/nexus/capabilities?token=" + TOKEN)
        self.assertEqual(r.status_code,401)

    def test_reject_insecure_credential_or_configuration(self):
        for token in ("", "short", None):
            with self.assertRaises(NexusAPIError):
                create_app(self.nexus, api_token=token)
        with self.assertRaises(NexusAPIError):
            create_app(self.nexus, api_token=TOKEN, max_plans=0)
        with self.assertRaises(NexusAPIError):
            create_app(self.nexus, api_token=TOKEN, ttl_seconds=10)

    def test_even_a_registered_tool_cannot_run_over_http(self):
        invoked = []
        def tool_handler(_args):
            invoked.append(True)
            return ToolResult(ok=True, summary="done")
        tool = RegisteredTool("safe_tool", "Synthetic test-only tool", tool_handler)

        class ToolModel:
            def chat(self, messages, *, max_tokens=512, temperature=None):
                payload = {"goal": "Synthetic", "steps": [
                    {"id":"s1", "kind":"reason", "title":"Think", "description":"Think safely",
                     "tool":None, "depends_on":[], "success_criteria":"Acknowledge"},
                    {"id":"s2", "kind":"tool", "title":"Read", "description":"Read fixture",
                     "tool":"safe_tool", "depends_on":["s1"], "success_criteria":"Read completed"},
                    {"id":"s3", "kind":"verify", "title":"Verify", "description":"Independent check",
                     "tool":None, "depends_on":["s2"], "success_criteria":"Check completed"}],
                    "assumptions":[], "risks":[]}
                return NebiusReply(content=json.dumps(payload),model="mock",finish_reason="stop")

        nexus = NexusOrchestrator(NexusPlanner(ToolModel()), NexusExecutor({"safe_tool": tool}))
        app = create_app(nexus,api_token=TOKEN)
        c = TestClient(app)
        prepared = c.post("/nexus/prepare",json={"task":"Test"},headers=HEADERS)
        self.assertEqual(prepared.status_code,201)
        url = f'/nexus/plans/{prepared.json()["plan_id"]}/run'
        r = c.post(url,json={"manual_evidence":{"s1":"Reviewed"}},headers=HEADERS)
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["tools_invoked"],0)
        self.assertEqual(invoked,[])

    def test_provider_failure_does_not_leak_exception_text(self):
        class FailModel:
            def chat(self, messages, *, max_tokens=512, temperature=None):
                raise RuntimeError("PRIVATE_FAKE_KEY_123")
        nexus = NexusOrchestrator(NexusPlanner(FailModel()),NexusExecutor())
        c = TestClient(create_app(nexus,api_token=TOKEN))
        r = c.post("/nexus/prepare",json={"task":"Test"},headers=HEADERS)
        self.assertEqual(r.status_code,502)
        self.assertNotIn("PRIVATE_FAKE_KEY_123",r.text)

    def test_trusted_host_blocks_unknown_host(self):
        r = self.client.get("/health", headers={"host":"some-site.example"})
        self.assertEqual(r.status_code,400)

    def test_without_manual_evidence_stays_inconclusive(self):
        plan_id = self.prepare().json()["plan_id"]
        r = self.client.post(f"/nexus/plans/{plan_id}/run",json={},headers=HEADERS)
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["verification_status"],"inconclusive")


if __name__ == "__main__":
    unittest.main()
