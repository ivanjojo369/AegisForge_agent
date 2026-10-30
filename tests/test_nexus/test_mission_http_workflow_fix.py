"""HTTP/GUI regression for demo workflow binding; no network or effects."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
from pathlib import Path
import sys

import pytest
from starlette.testclient import TestClient

from aegisforge.nexus.mission_control.mission_http_api import create_mission_http_app
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService

_TOKEN = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_ROOT = Path(__file__).resolve().parents[2]
_SERVER = _ROOT / "scripts" / "nexus" / "serve_mission_http_api.py"


def _client(app):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 48723),
                      raise_server_exceptions=False)


def _get(client, path, token=_TOKEN):
    return client.get(path, headers={"Authorization": "Bearer " + token})


@pytest.fixture
def demo_app(monkeypatch):
    spec = importlib.util.spec_from_file_location("colossus_local_demo_fix", _SERVER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    captured = []
    def fake_run(app, **kwargs):
        captured.append((app, kwargs))
    monkeypatch.setattr(module.uvicorn, "run", fake_run)
    monkeypatch.setenv("AEGISFORGE_MISSION_API_ACTOR", "actor.local")
    monkeypatch.setenv("AEGISFORGE_MISSION_API_TOKEN", _TOKEN)
    monkeypatch.setattr(sys, "argv", [str(_SERVER), "--demo"])
    module.main()
    assert len(captured) == 1
    assert captured[0][1]["host"] == "127.0.0.1"
    assert captured[0][1]["proxy_headers"] is False
    return captured[0][0]


def test_demo_preview_returns_real_report(demo_app):
    with _client(demo_app) as c:
        r = _get(c, "/api/v1/missions/mission.demo/preview")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["outcome"] == "host_review_required"
        assert data["step_count"] == 1
        assert data["effects_executed"] is False
        assert data["authorization_granted"] is False


def test_demo_inspection_returns_real_metadata_receipt(demo_app):
    with _client(demo_app) as c:
        r = _get(c, "/api/v1/missions/mission.demo/inspect")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["outcome"] == "completed_read_only"
        assert data["receipt_count"] == 1
        assert data["receipts"][0]["component"] == "mission_control"
        assert data["effects_executed"] is False
        assert data["authorization_granted"] is False


def test_demo_remains_in_draft_and_does_not_change_history(demo_app):
    with _client(demo_app) as c:
        before = _get(c, "/api/v1/missions/mission.demo/history").json()["data"]
        for _ in range(2):
            assert _get(c, "/api/v1/missions/mission.demo/inspect").status_code == 200
        after = _get(c, "/api/v1/missions/mission.demo/history").json()["data"]
        assert before == after
        assert _get(c, "/api/v1/missions/mission.demo").json()["data"]["status"] == "draft"


def _no_workflow_app(allowed=True):
    registry = NexusMissionRegistry()
    registry.register("mission.demo", product="colossus", owner_id="owner.demo",
                      objective_sha256=sha256(b"demo").hexdigest(), actor_id="actor.demo")
    def authorizer(actor, action, mission, anchor, request):
        return bool(allowed and actor == "actor.local" and action in {
            "list", "read", "preview", "inspect", "history",
        })
    service = NexusMissionService(registry, authorizer=authorizer)
    return create_mission_http_app(service, token_by_actor={"actor.local": _TOKEN})


@pytest.mark.parametrize("endpoint", ["preview", "inspect"])
def test_missing_workflow_returns_actionable_conflict(endpoint):
    with _client(_no_workflow_app()) as c:
        r = _get(c, f"/api/v1/missions/mission.demo/{endpoint}")
        assert r.status_code == 409
        assert r.json() == {"error": "workflow_not_configured"}
        assert "traceback" not in r.text.lower()


@pytest.mark.parametrize("endpoint", ["preview", "inspect"])
def test_unauthorized_operator_never_gets_workflow_disclosure(endpoint):
    with _client(_no_workflow_app(False)) as c:
        r = _get(c, f"/api/v1/missions/mission.demo/{endpoint}")
        assert r.status_code == 403
        assert r.json()["error"] == "operation_denied_or_unavailable"


def test_missing_token_remains_unauthorized(demo_app):
    with _client(demo_app) as c:
        r = c.get("/api/v1/missions/mission.demo/inspect")
        assert r.status_code == 401


def test_no_write_routes_added(demo_app):
    with _client(demo_app) as c:
        for endpoint in ("preview", "inspect"):
            r = c.post(f"/api/v1/missions/mission.demo/{endpoint}",
                       headers={"Authorization": "Bearer " + _TOKEN}, json={"action":"run"})
            assert r.status_code in (404, 405)


def test_dashboard_error_message_is_actionable(demo_app):
    with _client(demo_app) as c:
        js = c.get("/assets/mission_dashboard.js")
        assert js.status_code == 200
        assert "workflow_not_configured" in js.text
        assert "no tiene un workflow configurado" in js.text
        assert "localStorage" not in js.text
        assert "sessionStorage" not in js.text
        assert "innerHTML" not in js.text
