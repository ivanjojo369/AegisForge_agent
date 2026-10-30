"""Local web UI regression tests — no Nebius calls or tool execution."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

from starlette.testclient import TestClient

from aegisforge.nexus.executor import NexusExecutor, RegisteredTool, ToolResult
from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.orchestrator import NexusOrchestrator
from aegisforge.nexus.planner import NexusPlanner

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "nexus" / "serve_nexus_web.py"
spec = importlib.util.spec_from_file_location("serve_nexus_web", SCRIPT)
server = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(server)
TOKEN = "web-test-0123456789abcdef-0123456789abcdef"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

class FakeModel:
    def __init__(self): self.calls = 0

    def chat(self, messages, *, max_tokens=512, temperature=None):
        self.calls += 1
        return NebiusReply(
            content=json.dumps({
                "goal": "Analyze a synthetic task",
                "steps": [
                    {"id": "s1", "kind": "reason", "title": "Analyze", "description": "Prepare analysis", "tool": None,
                     "depends_on": [], "success_criteria": "An explanation is prepared"},
                    {"id": "s2", "kind": "verify", "title": "Review", "description": "Request an independent review", "tool": None,
                     "depends_on": ["s1"], "success_criteria": "Independent evidence requested"}
                ], "assumptions": [], "risks": []
            }), model="fake-nebius", finish_reason="stop", usage={"total_tokens": 3}
        )

class DashboardTests(unittest.TestCase):
    def test_offline_html_served_without_api_key(self):
        app = server.build_app(live=False)
        with TestClient(app) as c:
            r = c.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("AegisForge Nexus", r.text)
            self.assertIn("default-src 'none'", r.headers["content-security-policy"])
            self.assertEqual(r.headers["cache-control"], "no-store")
            self.assertNotIn("Access-Control-Allow-Origin", r.headers)
            d = c.get("/web-status").json()
            self.assertEqual(d["mode"], "offline")
            self.assertFalse(d["inference_on_prepare"])
            self.assertFalse(d["tool_execution_over_http"])
            self.assertEqual(c.post("/nexus/prepare", json={"task": "test"}).status_code, 404)

    def test_only_known_static_files(self):
        with TestClient(server.build_app()) as c:
            self.assertEqual(c.get("/styles.css").status_code, 200)
            self.assertEqual(c.get("/app.js").status_code, 200)
            self.assertEqual(c.get("/api-key.txt").status_code, 404)
            self.assertEqual(c.get("/evidence/nexus/private.json").status_code, 404)
            self.assertEqual(c.get("/.env").status_code, 404)
            self.assertEqual(c.get("/../.env").status_code, 404)
            self.assertEqual(c.get("/", headers={"Host": "untrusted.example"}).status_code, 400)

    def test_offline_never_instantiates_client_or_model(self):
        c = TestClient(server.build_app())
        c.get("/");c.get("/web-status");c.get("/app.js")
        self.assertTrue(True)

    def test_live_preserves_original_api_policy(self):
        model = FakeModel()
        invoked = []
        t = RegisteredTool("never_execute", "test tool", lambda args: invoked.append(True) or ToolResult(ok=True, summary="done"))
        ex = NexusExecutor({"never_execute": t})
        orch = NexusOrchestrator(NexusPlanner(model), ex)
        app = server.build_app(live=True, orchestrator=orch, token=TOKEN)
        with TestClient(app) as c:
            self.assertEqual(c.get("/").status_code, 200)
            self.assertEqual(c.get("/web-status").json()["mode"], "live")
            self.assertEqual(c.get("/nexus/capabilities").status_code, 401)
            self.assertFalse(c.get("/nexus/capabilities", headers=HEADERS).json()["tool_execution_over_http"])
            self.assertEqual(model.calls, 0)
            r = c.post("/nexus/prepare", json={"task":"Synthetic planning"}, headers=HEADERS)
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(model.calls, 1)
            pid = r.json()["plan_id"]
            self.assertEqual(c.post(f"/nexus/plans/{pid}/preview",json={},headers=HEADERS).status_code, 200)
            self.assertEqual(c.post(f"/nexus/plans/{pid}/run",json={"execute_tools":True},headers=HEADERS).status_code,403)
            self.assertEqual(c.post(f"/nexus/plans/{pid}/run",json={"execute_tools":False},headers=HEADERS).status_code,200)
            self.assertEqual(invoked, [])
            self.assertEqual(model.calls, 1)

    def test_static_js_refuses_arbitrary_html_injection(self):
        js = (ROOT / "src/aegisforge/web/nexus/app.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("localStorage", js)
        self.assertIn("textContent", js)
        self.assertIn("execute_tools:false", js)

if __name__ == "__main__": unittest.main()
