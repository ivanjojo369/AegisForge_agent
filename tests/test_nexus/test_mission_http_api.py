"""Mission Control localhost HTTP/API/UI; no external calls or tool execution."""
from __future__ import annotations

from hashlib import sha256
import json

import pytest
from starlette.testclient import TestClient

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_http_api import (
    MISSION_HTTP_API_SCHEMA, MissionAPIAuthenticator, NexusMissionHTTPAPIError,
    create_mission_http_app,
)

_TOKEN = "Q1zn3mAPf-4xY8uT7w6Gk2sR9p0cV5hB1dE8LjM4Fq7"
_OTHER_TOKEN = "Wd7kj3N5T2xp0G9uZ4A6bv1R8YqC5Mf2LsEe3Hh"


@pytest.fixture()
def data():
    registry = NexusMissionRegistry()
    registry.register("mission.alpha", product="nexus", owner_id="owner.local",
                      objective_sha256=sha256(b"alpha").hexdigest(), actor_id="actor.admin")
    registry.assign_component("mission.alpha", component="lab", actor_id="actor.admin")
    registry.register("mission.beta", product="aegisgrid", owner_id="owner.local",
                      objective_sha256=sha256(b"beta").hexdigest(), actor_id="actor.admin")
    seen = []
    def auth(actor, action, mission, anchor, request):
        seen.append((actor, action, mission, request))
        if action not in {"list", "read", "history", "preview", "inspect"}:
            return False
        if actor == "actor.admin":
            return True
        if actor == "actor.viewer":
            return mission in {"*", "mission.alpha"}
        return False
    service = NexusMissionService(registry, authorizer=auth)
    app = create_mission_http_app(service, token_by_actor={"actor.admin": _TOKEN,
                                                            "actor.viewer": _OTHER_TOKEN})
    return registry, service, seen, app


def client(app, *, remote="127.0.0.1", host="localhost"):
    return TestClient(app, base_url=f"http://{host}", client=(remote, 52525), raise_server_exceptions=False)


def api_get(c, path, token=_TOKEN, headers=None):
    return c.get(path, headers={"Authorization": f"Bearer {token}", **(headers or {})})


def test_public_dashboard_contains_no_mission_records(data):
    _, _, _, app = data
    with client(app) as c:
        resp = c.get("/dashboard")
        assert resp.status_code == 200
        assert "Mission Control" in resp.text
        assert "mission.alpha" not in resp.text
        assert "no-store" in resp.headers["cache-control"]
        assert "'none'" in resp.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["/", "/dashboard", "/assets/mission_dashboard.js",
                                 "/assets/mission_dashboard.css"])
def test_static_assets(data, path):
    with client(data[3]) as c:
        r = c.get(path)
        assert r.status_code == 200
        assert "no-store" in r.headers["cache-control"]
        assert r.headers["x-content-type-options"] == "nosniff"


def test_dashboard_script_never_persists_token(data):
    with client(data[3]) as c:
        js = c.get("/assets/mission_dashboard.js").text
        assert "localStorage" not in js and "sessionStorage" not in js
        assert "document.cookie" not in js
        assert "innerHTML" not in js


@pytest.mark.parametrize("path", ["/api/v1/status", "/api/v1/version", "/api/v1/missions",
                                 "/api/v1/missions/mission.alpha",
                                 "/api/v1/missions/mission.alpha/history",
                                 "/api/v1/missions/mission.alpha/preview"])
def test_auth_mandatory(data, path):
    with client(data[3]) as c:
        r = c.get(path)
        assert r.status_code == 401
        assert r.headers.get("www-authenticate", "").startswith("Bearer")
        assert r.json()["error"] == "authentication_required"


@pytest.mark.parametrize("header", ["Basic ZZZ", "Bearer incorrect", "Bearer ", "", "Bearer " + "A"*400])
def test_wrong_auth_rejected(data, header):
    with client(data[3]) as c:
        assert c.get("/api/v1/missions", headers={"Authorization": header}).status_code == 401


def test_api_status_authenticated(data):
    with client(data[3]) as c:
        r = api_get(c, "/api/v1/status")
        assert r.status_code == 200
        assert r.json()["data"]["api_state"] == "ready"
        assert r.json()["data"]["service"]["read_only_runtime"] is True


def test_api_capabilities_read_only(data):
    with client(data[3]) as c:
        r = api_get(c, "/api/v1/capabilities")
        assert r.json()["schema"] == MISSION_HTTP_API_SCHEMA
        assert r.json()["data"]["read_only"] is True
        assert r.json()["data"]["effects_enabled"] is False


def test_registry_version_matches(data):
    registry, _, _, app = data
    with client(app) as c:
        r = api_get(c, "/api/v1/version")
        assert r.status_code == 200
        assert r.json()["data"]["registry_sha256"] == registry.snapshot()["registry_sha256"]


def test_list_and_filter(data):
    with client(data[3]) as c:
        rows = api_get(c, "/api/v1/missions").json()["data"]["missions"]
        assert [x["mission_id"] for x in rows] == ["mission.alpha", "mission.beta"]
        rows = api_get(c, "/api/v1/missions?product=aegisgrid").json()["data"]["missions"]
        assert [x["mission_id"] for x in rows] == ["mission.beta"]
        rows = api_get(c, "/api/v1/missions?status=draft&limit=1").json()["data"]["missions"]
        assert len(rows) == 1


def test_viewer_cannot_see_other_mission(data):
    with client(data[3]) as c:
        rows = api_get(c, "/api/v1/missions", _OTHER_TOKEN).json()["data"]["missions"]
        assert [row["mission_id"] for row in rows] == ["mission.alpha"]
        assert api_get(c, "/api/v1/missions/mission.beta", _OTHER_TOKEN).status_code == 403
        assert api_get(c, "/api/v1/missions/mission.beta/history", _OTHER_TOKEN).status_code == 403


def test_detail_and_history(data):
    with client(data[3]) as c:
        d = api_get(c, "/api/v1/missions/mission.alpha")
        assert d.status_code == 200
        assert d.json()["data"]["status"] == "draft"
        h = api_get(c, "/api/v1/missions/mission.alpha/history")
        assert h.status_code == 200
        assert h.json()["data"]["count"] == 2


def test_preview_requires_configured_workflow(data):
    with client(data[3]) as c:
        r = api_get(c, "/api/v1/missions/mission.alpha/preview")
        assert r.status_code in (403, 409)
        assert "traceback" not in r.text.lower()


def test_no_http_mutations_exist(data):
    registry, _, _, app = data
    before = registry.snapshot()
    with client(app) as c:
        for path in ["/api/v1/missions", "/api/v1/missions/mission.alpha",
                     "/api/v1/missions/mission.alpha/history"]:
            r = c.post(path, json={"target_status": "verified"}, headers={"Authorization":"Bearer " + _TOKEN})
            assert r.status_code in (404, 405)
    assert registry.snapshot() == before


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=-1", "limit=1&limit=2",
                                    "unrecognized=yes", "product=nexus&product=nexus",
                                    "limit=99999"])
def test_invalid_query_fails_closed(data, query):
    with client(data[3]) as c:
        r = api_get(c, "/api/v1/missions?" + query)
        assert r.status_code == 400
        assert "traceback" not in r.text.lower()


@pytest.mark.parametrize("path", ["/api/v1/missions/BAD-ID", "/api/v1/missions/a",
                                 "/api/v1/missions/abc?unknown=1"])
def test_invalid_mission_id_or_query(data, path):
    with client(data[3]) as c:
        r = api_get(c, path)
        assert r.status_code in (400, 404)


def test_unknown_mission_does_not_leak_internal_exception(data):
    with client(data[3]) as c:
        r = api_get(c, "/api/v1/missions/mission.unknown")
        assert r.status_code in (403, 404)
        assert "traceback" not in r.text.lower()


@pytest.mark.parametrize("remote", ["192.168.0.5", "203.0.113.25", "8.8.8.8"])
def test_nonloopback_client_denied(data, remote):
    with client(data[3], remote=remote) as c:
        assert api_get(c, "/api/v1/status").status_code == 403
        assert c.get("/dashboard").status_code == 403


def test_host_header_refuses_dns_rebinding(data):
    with client(data[3], host="outside.example") as c:
        r = api_get(c, "/api/v1/status")
        assert r.status_code in (400, 403)


def test_security_response_headers(data):
    with client(data[3]) as c:
        r = api_get(c, "/api/v1/missions")
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-frame-options"] == "DENY"
        assert "connect-src 'self'" in r.headers["content-security-policy"]
        assert "access-control-allow-origin" not in r.headers


def test_response_budget_rejected(data):
    registry, svc, _, _ = data
    app = create_mission_http_app(svc, token_by_actor={"actor.admin":_TOKEN}, max_response_bytes=16_384)
    with client(app) as c:
        r = api_get(c, "/api/v1/missions")
        assert r.status_code == 200


@pytest.mark.parametrize("bad", [{}, {"actor.admin": "short"}, {"bad actor": _TOKEN},
                                  {"actor.admin": _TOKEN, "actor.viewer": _TOKEN}])
def test_credentials_rejected(bad):
    with pytest.raises(NexusMissionHTTPAPIError):
        MissionAPIAuthenticator(bad)


def test_good_authenticator():
    a = MissionAPIAuthenticator({"actor.admin": _TOKEN})
    assert a.actor_for("Bearer " + _TOKEN) == "actor.admin"
    assert a.actor_for("bearer " + _TOKEN) == "actor.admin"
    assert a.actor_for("Bearer " + _OTHER_TOKEN) is None
    assert a.actor_for(None) is None


def test_api_does_not_write_even_after_reads(data):
    registry, _, calls, app = data
    before = registry.snapshot()
    with client(app) as c:
        api_get(c, "/api/v1/status")
        api_get(c, "/api/v1/missions")
        api_get(c, "/api/v1/missions/mission.alpha")
        api_get(c, "/api/v1/missions/mission.alpha/history")
    assert registry.snapshot() == before
    assert all(action in {"list", "read", "history"} for _, action, _, _ in calls)


def test_source_not_exposed_as_owner_identifier(data):
    with client(data[3]) as c:
        row = api_get(c, "/api/v1/missions/mission.alpha").json()["data"]
        assert "owner.local" not in json.dumps(row)
        assert "objective" not in row


def test_service_authorizer_unavailable_fails_closed():
    reg = NexusMissionRegistry()
    def broken(*args): raise RuntimeError("secret internal exception")
    app = create_mission_http_app(NexusMissionService(reg, authorizer=broken),token_by_actor={"actor.admin":_TOKEN})
    with client(app) as c:
        r = api_get(c, "/api/v1/missions")
        assert r.status_code in (403, 500)
        assert "secret internal exception" not in r.text


def test_wrong_http_methods_do_not_call_authorizer(data):
    _, _, calls, app = data
    with client(app) as c:
        for method in ("PUT", "PATCH", "DELETE"):
            r = c.request(method, "/api/v1/missions", headers={"Authorization":"Bearer " + _TOKEN})
            assert r.status_code in (404, 405)
    assert calls == []


def test_real_workflow_preview_and_inspect_over_http_without_mutations():
    from aegisforge.nexus.mission_control.mission_state import (
        MissionTransitionRule, MissionStatePolicy, NexusMissionState,
    )
    from aegisforge.nexus.mission_control.mission_coordinator import (
        MissionWorkflowStep, MissionCoordinationBlueprint, NexusMissionCoordinator,
    )
    from aegisforge.nexus.mission_control.mission_read_adapters import NexusMissionReadAdapters
    reg = NexusMissionRegistry()
    reg.register("mission.local", product="nexus", owner_id="owner.local",
                 objective_sha256="a"*64, actor_id="actor.admin")
    def authorizer(actor, action, mission, anchor, request):
        return actor == "actor.admin" and action in {"list", "read", "history",
                                                      "preview", "inspect", "configure"}
    svc = NexusMissionService(reg, authorizer=authorizer)
    state = NexusMissionState(MissionStatePolicy("mission.local", "nexus", (
        MissionTransitionRule("ready"),
    )))
    blueprint = MissionCoordinationBlueprint("mission.local", "nexus", "ready", (
        MissionWorkflowStep("step.registry", "mission_control", "summarize_mission"),
    ))
    coordinator = NexusMissionCoordinator(blueprint, state)
    svc.configure_workflow("mission.local", actor_id="actor.admin", coordinator=coordinator,
                           adapters=NexusMissionReadAdapters(reg),
                           expected_registry_sha256=svc.registry_version(actor_id="actor.admin"))
    app = create_mission_http_app(svc, token_by_actor={"actor.admin": _TOKEN})
    original = reg.snapshot()
    with client(app) as c:
        prev = api_get(c, "/api/v1/missions/mission.local/preview")
        assert prev.status_code == 200
        assert prev.json()["data"]["effects_executed"] is False
        inspect = api_get(c, "/api/v1/missions/mission.local/inspect")
        assert inspect.status_code == 200
        assert inspect.json()["data"]["effects_executed"] is False
    assert reg.snapshot() == original


def test_response_byte_limit_prevents_huge_history():
    reg = NexusMissionRegistry()
    reg.register("mission.long", product="nexus", owner_id="owner.local",
                 objective_sha256="f"*64, actor_id="actor.admin")
    for i in range(63):
        reg.link_reference("mission.long", kind="evidence",
                           reference_sha256=sha256(f"{i}".encode()).hexdigest(),
                           actor_id="actor.admin")
    svc = NexusMissionService(reg, authorizer=lambda *args: True)
    app = create_mission_http_app(svc, token_by_actor={"actor.admin": _TOKEN},
                                  max_response_bytes=16_384)
    with client(app) as c:
        resp = api_get(c, "/api/v1/missions/mission.long/history")
        assert resp.status_code == 413
        assert "secret" not in resp.text.lower()
