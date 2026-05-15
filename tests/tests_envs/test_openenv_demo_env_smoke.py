from __future__ import annotations

"""Smoke tests for the optional OpenEnv demo_env.

This file must never fail during pytest collection when demo_env is absent.
The demo environment is optional in this repository, so the test module skips
cleanly if ``integrations/openenv/envs/demo_env`` is not available.
"""

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "integrations" / "openenv" / "envs").exists():
            return candidate
    return Path(__file__).resolve().parents[2]


def _find_demo_env_root() -> Path | None:
    repo_root = _find_repo_root()
    demo_root = repo_root / "integrations" / "openenv" / "envs" / "demo_env"
    return demo_root if demo_root.exists() else None


REPO_ROOT = _find_repo_root()
DEMO_ENV_ROOT = _find_demo_env_root()

pytestmark = pytest.mark.skipif(
    DEMO_ENV_ROOT is None,
    reason="demo_env is not present in this checkout.",
)


def _remove_openenv_env_paths() -> None:
    marker = str(Path("integrations") / "openenv" / "envs")
    sys.path[:] = [
        path
        for path in sys.path
        if marker not in path.replace("\\", "/")
    ]


def _purge_demo_env_modules() -> None:
    """Reload demo_env between tests so server-level state does not leak."""
    for module_name in list(sys.modules):
        if (
            module_name in {"models", "client"}
            or module_name == "server"
            or module_name.startswith("server.")
        ):
            sys.modules.pop(module_name, None)


def _import_demo_app() -> ModuleType:
    assert DEMO_ENV_ROOT is not None

    _purge_demo_env_modules()
    _remove_openenv_env_paths()
    sys.path.insert(0, str(DEMO_ENV_ROOT))

    return importlib.import_module("server.app")


@pytest.fixture()
def app_module() -> Iterator[ModuleType]:
    module = _import_demo_app()
    yield module
    _purge_demo_env_modules()
    _remove_openenv_env_paths()


@pytest.fixture()
def client(app_module: ModuleType) -> Iterator[TestClient]:
    with TestClient(app_module.app) as test_client:
        yield test_client


def _json(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_openenv_demo_env_health_smoke(client: TestClient) -> None:
    payload = _json(client.get("/health"))

    assert payload["status"] == "ok"
    assert payload["env"] == "demo_env"
    assert "initialized" in payload


def test_openenv_demo_env_reset_smoke(client: TestClient) -> None:
    payload = _json(client.post("/reset", json={}))

    assert "observation" in payload
    assert "state" in payload
    assert "info" in payload

    assert payload["info"]["env_name"] == "demo_env"
    assert payload["info"]["reset"] is True

    state = payload["state"]
    assert state["score"] == 0
    assert state["step_count"] == 0
    assert state["done"] is False
    assert state["success"] is False


def test_openenv_demo_env_step_smoke(client: TestClient) -> None:
    _json(client.post("/reset", json={}))

    payload = _json(client.post("/step", json={"action": "advance", "value": 1}))

    assert "observation" in payload
    assert "reward" in payload
    assert "state" in payload

    assert payload["reward"] == 1.0
    assert payload["done"] is False
    assert payload["state"]["score"] == 1
    assert payload["state"]["step_count"] == 1
    assert payload["state"]["last_action"] == "advance"


def test_openenv_demo_env_state_smoke(client: TestClient) -> None:
    _json(client.post("/reset", json={}))
    _json(client.post("/step", json={"action": "advance", "value": 2}))

    payload = _json(client.get("/state"))

    assert payload["score"] == 2
    assert payload["step_count"] == 1
    assert payload["max_steps"] == 5
    assert payload["target_score"] == 3
    assert payload["done"] is False
    assert payload["success"] is False


def test_openenv_demo_env_success_path_smoke(client: TestClient) -> None:
    _json(client.post("/reset", json={}))

    step_1 = client.post("/step", json={"action": "advance", "value": 1})
    assert step_1.status_code == 200
    assert step_1.json()["done"] is False

    payload = _json(client.post("/step", json={"action": "advance", "value": 2}))

    assert payload["state"]["score"] == 3
    assert payload["done"] is True
    assert payload["state"]["success"] is True


def test_openenv_demo_env_step_before_reset_returns_409(client: TestClient, app_module: ModuleType) -> None:
    for name in ("_ENV_STATE", "_ENV_META", "_ENGINE_META"):
        if hasattr(app_module, name):
            setattr(app_module, name, None)

    response = client.post("/step", json={"action": "advance", "value": 1})

    assert response.status_code == 409
    assert "Call POST /reset first" in response.json()["detail"]
