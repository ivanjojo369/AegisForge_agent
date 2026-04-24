from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


def _find_repo_root() -> Path:
    """Find the repository root without assuming this test file's depth."""
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        demo_root = candidate / "integrations" / "openenv" / "envs" / "demo_env"
        if demo_root.exists():
            return candidate
    raise AssertionError("Could not find integrations/openenv/envs/demo_env from this test file.")


REPO_ROOT = _find_repo_root()
DEMO_ENV_ROOT = REPO_ROOT / "integrations" / "openenv" / "envs" / "demo_env"


def _purge_demo_env_modules() -> None:
    """Reload demo_env between tests so server-level state does not leak."""
    for module_name in list(sys.modules):
        if (
            module_name == "models"
            or module_name == "server"
            or module_name.startswith("server.")
        ):
            sys.modules.pop(module_name, None)


def _import_demo_app() -> ModuleType:
    if str(DEMO_ENV_ROOT) not in sys.path:
        sys.path.insert(0, str(DEMO_ENV_ROOT))

    _purge_demo_env_modules()
    return importlib.import_module("server.app")


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app_module = _import_demo_app()
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_openenv_demo_env_health_smoke(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["env"] == "demo_env"
    assert "initialized" in payload


def test_openenv_demo_env_reset_smoke(client: TestClient) -> None:
    response = client.post("/reset", json={})
    assert response.status_code == 200

    payload = response.json()
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
    reset_response = client.post("/reset", json={})
    assert reset_response.status_code == 200

    step_response = client.post(
        "/step",
        json={
            "action": "advance",
            "value": 1,
        },
    )
    assert step_response.status_code == 200

    payload = step_response.json()
    assert "observation" in payload
    assert "reward" in payload
    assert "state" in payload

    assert payload["reward"] == 1.0
    assert payload["done"] is False
    assert payload["state"]["score"] == 1
    assert payload["state"]["step_count"] == 1
    assert payload["state"]["last_action"] == "advance"


def test_openenv_demo_env_state_smoke(client: TestClient) -> None:
    reset_response = client.post("/reset", json={})
    assert reset_response.status_code == 200

    step_response = client.post(
        "/step",
        json={
            "action": "advance",
            "value": 2,
        },
    )
    assert step_response.status_code == 200

    response = client.get("/state")
    assert response.status_code == 200

    payload = response.json()
    assert payload["score"] == 2
    assert payload["step_count"] == 1
    assert payload["max_steps"] == 5
    assert payload["target_score"] == 3
    assert payload["done"] is False
    assert payload["success"] is False


def test_openenv_demo_env_success_path_smoke(client: TestClient) -> None:
    reset_response = client.post("/reset", json={})
    assert reset_response.status_code == 200

    step_1 = client.post("/step", json={"action": "advance", "value": 1})
    assert step_1.status_code == 200
    assert step_1.json()["done"] is False

    step_2 = client.post("/step", json={"action": "advance", "value": 2})
    assert step_2.status_code == 200

    payload = step_2.json()
    assert payload["state"]["score"] == 3
    assert payload["done"] is True
    assert payload["state"]["success"] is True


def test_openenv_demo_env_step_before_reset_returns_409(client: TestClient) -> None:
    response = client.post(
        "/step",
        json={
            "action": "advance",
            "value": 1,
        },
    )

    assert response.status_code == 409
    assert "Call POST /reset first" in response.json()["detail"]
