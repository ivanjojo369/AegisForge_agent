"""Checkpoint-backed Mission Control dashboard: local, authenticated, read-only."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from starlette.testclient import TestClient

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_checkpoint_monitor import (
    NexusMissionCheckpointMonitor, NexusMissionCheckpointMonitorError,
)
from aegisforge.nexus.mission_control.mission_http_api import (
    create_mission_http_app, NexusMissionHTTPAPIError,
)

TOKEN = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
VIEWER = "Wd7kj3N5T2xp0G9uZ4A6bv1R8YqC5Mf2LsEe3Hh"
_SERVER = Path(__file__).resolve().parents[2] / "scripts/nexus/serve_mission_http_api.py"


def client(app):
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 49000),
                      raise_server_exceptions=False)


def get(c, path, token=TOKEN):
    return c.get(path, headers={"Authorization": "Bearer " + token})


@pytest.fixture()
def checkpoint(tmp_path):
    root = tmp_path / "private_checkpoints"
    root.mkdir()
    registry = NexusMissionRegistry()
    for name, product in (("mission.nexus", "nexus"), ("mission.grid", "aegisgrid"),
                          ("mission.forge", "live_forge"), ("mission.colossus", "colossus")):
        registry.register(name, product=product, owner_id="owner.local",
                          objective_sha256=sha256(name.encode()).hexdigest(), actor_id="actor.local")
    store = NexusMissionPersistence(root)
    receipt = store.save(registry)
    loaded = store.load()
    return store, registry, receipt, loaded


@pytest.fixture()
def server(checkpoint):
    store, registry, receipt, loaded = checkpoint
    def auth(actor, action, mission_id, anchor, request):
        if actor == "actor.admin":
            return action in {"list", "read", "history", "preview", "inspect"}
        if actor == "actor.viewer":
            return action in {"list", "read", "history"} and mission_id in {"*", "mission.nexus"}
        return False
    service = NexusMissionService(loaded.registry, authorizer=auth)
    monitor = NexusMissionCheckpointMonitor(store, loaded)
    app = create_mission_http_app(service, token_by_actor={"actor.admin": TOKEN, "actor.viewer": VIEWER},
                                  checkpoint_monitor=monitor)
    return store, registry, receipt, loaded, service, monitor, app


def test_checkpoint_status_matches_loaded_disk(server):
    _, _, receipt, _, _, _, app = server
    with client(app) as c:
        r = get(c, "/api/v1/checkpoint")
        assert r.status_code == 200
        v = r.json()["data"]
        assert v["generation"] == receipt.generation == 1
        assert v["envelope_sha256"] == receipt.envelope_sha256
        assert v["registry_sha256"] == receipt.registry_sha256
        assert v["visible_mission_count"] == 4
        assert v["visible_by_product"] == {"nexus": 1, "aegisgrid": 1, "live_forge": 1, "colossus": 1}
        assert v["source"] == "persisted_primary"
        assert v["live_reload"] is False
        assert v["authenticity_verified"] is False


def test_checkpoint_does_not_leak_directory_or_mission_details(server, checkpoint):
    _, _, _, _, _, _, app = server
    with client(app) as c:
        payload = get(c, "/api/v1/checkpoint").text
        assert str(checkpoint[0]._root) not in payload
        assert "mission.nexus" not in payload
        assert "owner.local" not in payload


def test_viewer_sees_only_authorized_counts(server):
    with client(server[-1]) as c:
        d = get(c, "/api/v1/checkpoint", VIEWER).json()["data"]
        assert d["visible_mission_count"] == 1
        assert d["visible_by_product"]["nexus"] == 1
        assert sum(d["visible_by_product"].values()) == 1
        assert get(c, "/api/v1/missions/mission.grid", VIEWER).status_code == 403


@pytest.mark.parametrize("product,mid", [("nexus", "mission.nexus"), ("aegisgrid", "mission.grid"),
                                    ("live_forge", "mission.forge"), ("colossus", "mission.colossus")])
def test_filter_real_persisted_products(server, product, mid):
    with client(server[-1]) as c:
        d = get(c, "/api/v1/missions?product=" + product).json()["data"]
        assert len(d["missions"]) == 1
        assert d["missions"][0]["mission_id"] == mid
        assert get(c, "/api/v1/missions/" + mid + "/timeline").status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/checkpoint", "/api/v1/missions", "/api/v1/status"])
def test_authentication_required(server, path):
    with client(server[-1]) as c:
        assert c.get(path).status_code == 401
        assert get(c, path, "Z"*48).status_code == 401


@pytest.mark.parametrize("method", ["post", "patch", "put", "delete"])
def test_checkpoint_endpoint_has_no_mutations(server, method):
    with client(server[-1]) as c:
        r = getattr(c, method)("/api/v1/checkpoint", headers={"Authorization": "Bearer " + TOKEN})
        assert r.status_code in {404, 405}


def test_invalid_query_rejected(server):
    with client(server[-1]) as c:
        assert get(c, "/api/v1/checkpoint?path=private").status_code == 400


def test_in_memory_absent_status():
    registry = NexusMissionRegistry()
    service = NexusMissionService(registry, authorizer=lambda *args: True)
    app = create_mission_http_app(service, token_by_actor={"actor.admin": TOKEN})
    with client(app) as c:
        d = get(c, "/api/v1/checkpoint").json()["data"]
        assert d["source"] == "in_memory"
        assert d["available"] is False
        assert d["visible_mission_count"] == 0


def test_checkpoint_changes_are_fail_closed_for_all_api_routes(server):
    store, reg, receipt, _, _, monitor, app = server
    reg.assign_component("mission.nexus", component="mission_control", actor_id="actor.local")
    newer = store.save(reg, expected_head_sha256=receipt.envelope_sha256)
    assert newer.generation == 2
    with pytest.raises(NexusMissionCheckpointMonitorError):
        monitor.assert_current()
    with client(app) as c:
        for route in ("/api/v1/checkpoint", "/api/v1/status", "/api/v1/missions",
                      "/api/v1/missions/mission.nexus/history"):
            r = get(c, route)
            assert r.status_code == 409, route
            assert r.json()["error"] == "checkpoint_changed_restart_required"


def test_restarting_with_new_generation_restores_authorized_views(checkpoint):
    store, reg, receipt, loaded = checkpoint
    reg.assign_component("mission.nexus", component="mission_control", actor_id="actor.local")
    store.save(reg, expected_head_sha256=receipt.envelope_sha256)
    fresh = store.load()
    monitor = NexusMissionCheckpointMonitor(store, fresh)
    service = NexusMissionService(fresh.registry, authorizer=lambda a, act, m, anchor, meta: True)
    app = create_mission_http_app(service, token_by_actor={"actor.admin": TOKEN},checkpoint_monitor=monitor)
    with client(app) as c:
        assert get(c, "/api/v1/checkpoint").json()["data"]["generation"] == 2
        assert get(c, "/api/v1/missions/mission.nexus/history").json()["data"]["count"] == 2
        assert get(c, "/api/v1/missions/mission.nexus/preview").status_code == 409
        assert get(c, "/api/v1/missions/mission.nexus/preview").json()["error"] == "workflow_not_configured"
        assert get(c, "/api/v1/missions/mission.nexus/trust").json()["data"]["available"] is False


def test_corrupt_checkpoint_blocked(server):
    store, _, _, _, _, monitor, app = server
    store._primary.write_bytes(b'{"corrupt":true}')
    with pytest.raises(NexusMissionCheckpointMonitorError):
        monitor.assert_current()
    with client(app) as c:
        assert get(c, "/api/v1/checkpoint").status_code == 409


def test_missing_primary_rejects_every_api_read(server):
    store, _, _, _, _, monitor, app = server
    store._primary.unlink()
    with pytest.raises(NexusMissionCheckpointMonitorError):
        monitor.assert_current()
    with client(app) as c:
        assert get(c, "/api/v1/missions").status_code == 409


def test_previous_checkpoint_not_live_source(checkpoint):
    store, reg, receipt, loaded = checkpoint
    reg.assign_component("mission.nexus", component="mission_control", actor_id="actor.local")
    store.save(reg, expected_head_sha256=receipt.envelope_sha256)
    previous = store.inspect_previous()
    with pytest.raises(NexusMissionCheckpointMonitorError):
        NexusMissionCheckpointMonitor(store, previous)


def test_invalid_monitor_source_is_rejected(checkpoint):
    store, _, _, loaded = checkpoint
    with pytest.raises(NexusMissionCheckpointMonitorError):
        NexusMissionCheckpointMonitor(object(), loaded)
    with pytest.raises(NexusMissionCheckpointMonitorError):
        NexusMissionCheckpointMonitor(store, object())
    service = NexusMissionService(loaded.registry, authorizer=lambda *args: True)
    with pytest.raises(NexusMissionHTTPAPIError):
        create_mission_http_app(service, token_by_actor={"actor.admin": TOKEN}, checkpoint_monitor=object())


def test_no_bearer_leaked_from_status(server):
    with client(server[-1]) as c:
        assert TOKEN not in get(c, "/api/v1/checkpoint").text


def test_html_filters_visible_and_assets_have_safe_rendering(server):
    with client(server[-1]) as c:
        html = c.get("/dashboard").text
        js = c.get("/assets/mission_dashboard.js").text
        assert '<select id="product-filter" disabled>' in html
        assert "checkpoint-info" in html
        assert "checkpoint_changed_restart_required" in js
        assert "textContent" in js
        assert "innerHTML" not in js
        assert "localStorage" not in js


def test_server_cli_loads_existing_checkpoint_without_demo(monkeypatch, checkpoint):
    store, _, receipt, _ = checkpoint
    spec = importlib.util.spec_from_file_location("checkpoint_demo_cli", _SERVER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    running=[]
    monkeypatch.setattr(mod.uvicorn, "run", lambda app, **kw: running.append((app,kw)))
    monkeypatch.setenv("AEGISFORGE_MISSION_API_ACTOR", "actor.local")
    monkeypatch.setenv("AEGISFORGE_MISSION_API_TOKEN", TOKEN)
    monkeypatch.setattr(sys, "argv", [str(_SERVER), "--checkpoint-dir", str(store._root)])
    mod.main()
    assert len(running)==1
    assert running[0][1]["host"]=="127.0.0.1"
    with client(running[0][0]) as c:
        data=get(c,"/api/v1/checkpoint").json()["data"]
        assert data["generation"]==receipt.generation
        assert get(c,"/api/v1/missions").json()["data"]["count"]==4


def test_checkpoint_reads_do_not_mutate_files(server):
    store, _, _, _, _, _, app = server
    before = store._primary.read_bytes()
    with client(app) as c:
        for _ in range(3):
            for path in ("/api/v1/checkpoint","/api/v1/missions","/api/v1/missions/mission.grid/history"):
                assert get(c,path).status_code==200
    assert store._primary.read_bytes()==before


def test_checkpoint_source_failure_not_autofallback_to_previous(server):
    store, registry, receipt, _, _, _, app = server
    registry.assign_component("mission.nexus", component="mission_control", actor_id="actor.local")
    store.save(registry, expected_head_sha256=receipt.envelope_sha256)
    store._primary.unlink()
    with client(app) as c:
        response = get(c,"/api/v1/checkpoint")
        assert response.status_code == 409
        assert "checkpoint_changed_restart_required" in response.text


@pytest.mark.parametrize("kind", ["graph", "laboratory", "trust"])
def test_real_checkpoint_does_not_fake_optional_sources(server, kind):
    with client(server[-1]) as c:
        data = get(c,"/api/v1/missions/mission.nexus/"+kind).json()["data"]
        assert data["available"] is False
        assert data["reason"] == "source_not_configured"


def test_api_no_mutating_routes_when_checkpoint_loaded(server):
    with client(server[-1]) as c:
        response=c.post("/api/v1/missions",json={"mission_id":"mission.evil"},headers={"Authorization":"Bearer "+TOKEN})
        assert response.status_code in {404,405}


def test_checkpoint_generation_not_considered_authenticity(server):
    with client(server[-1]) as c:
        data=get(c,"/api/v1/checkpoint").json()["data"]
        assert data["integrity_only"] is True
        assert data["authenticity_verified"] is False
        assert data["workflow_bindings_restored"] is False


def test_memory_state_drift_is_rejected_even_if_disk_unchanged(server):
    _, _, _, loaded, _, monitor, app = server
    loaded.registry.assign_component("mission.nexus", component="mission_control", actor_id="actor.local")
    with pytest.raises(NexusMissionCheckpointMonitorError):
        monitor.assert_current()
    with client(app) as c:
        assert get(c, "/api/v1/missions").status_code == 409


def test_checkpoint_monitor_cannot_serve_unrelated_registry(server):
    monitor = server[-2]
    unrelated = NexusMissionRegistry()
    unrelated_service = NexusMissionService(unrelated, authorizer=lambda *args: True)
    with pytest.raises(NexusMissionHTTPAPIError):
        create_mission_http_app(unrelated_service, token_by_actor={"actor.admin": TOKEN}, checkpoint_monitor=monitor)


def test_disposable_demo_script_creates_distinct_multi_product_checkpoint(monkeypatch, tmp_path):
    from pathlib import Path as P
    import importlib.util
    import json as J
    import sys
    script = P(__file__).resolve().parents[2] / "scripts/nexus/demo_create_mission_checkpoint.py"
    spec = importlib.util.spec_from_file_location("demo_checkpoint_creator", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "new_sample"))
    # A real mkdtemp path already exists; model its guarantee in this isolated test.
    (tmp_path / "new_sample").mkdir()
    # The script must never modify an existing user checkout; it uses this isolated directory.
    module.main = module.main
    import io
    from contextlib import redirect_stdout
    stream = io.StringIO()
    with redirect_stdout(stream):
        module.main()
    output = J.loads(stream.getvalue())
    assert output["missions"] == 4 and output["effects_executed"] is False
    loaded = NexusMissionPersistence(output["checkpoint_dir"]).load()
    assert {m["product"] for m in loaded.registry.snapshot()["missions"]} == {"nexus", "aegisgrid", "live_forge", "colossus"}
