from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


# Permite importar:
# - server.app
# - models.py
#
# desde integrations/openenv/envs/demo_env/
REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ENV_ROOT = REPO_ROOT / "integrations" / "openenv" / "envs" / "demo_env"

import importlib

if str(DEMO_ENV_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ENV_ROOT))

app = importlib.import_module("server.app").app

client = TestClient(app)


def test_openenv_demo_env_health_smoke() -> None:
    response = client.get("/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["env"] == "demo_env"
    assert "initialized" in payload


def test_openenv_demo_env_reset_smoke() -> None:
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


def test_openenv_demo_env_step_smoke() -> None:
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


def test_openenv_demo_env_state_smoke() -> None:
    reset_response = client.post("/reset", json={})
    assert reset_response.status_code == 200

    client.post(
        "/step",
        json={
            "action": "advance",
            "value": 2,
        },
    )

    response = client.get("/state")
    assert response.status_code == 200

    payload = response.json()
    assert payload["score"] == 2
    assert payload["step_count"] == 1
    assert payload["max_steps"] == 5
    assert payload["target_score"] == 3
    assert payload["done"] is False
    assert payload["success"] is False


def test_openenv_demo_env_success_path_smoke() -> None:
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

def test_openenv_demo_env_step_before_reset_returns_409() -> None:

    response = client.post(
        "/step",
        json={
            "action": "advance",
            "value": 1,
        },
    )
    assert response.status_code == 409
    assert "Call POST /reset first" in response.json()["detail"]
