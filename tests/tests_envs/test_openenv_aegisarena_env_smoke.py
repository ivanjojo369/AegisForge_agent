from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
ARENA_ENV_ROOT = REPO_ROOT / "integrations" / "openenv" / "envs" / "aegisarena_env"

if str(ARENA_ENV_ROOT) not in sys.path:
    sys.path.insert(0, str(ARENA_ENV_ROOT))

app_module = importlib.import_module("server.app")
app = app_module.app

client = TestClient(app)


def test_aegisarena_health_smoke() -> None:
    response = client.get("/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["env"] == "aegisarena_env"
    assert "initialized" in payload
    assert "active_mission_type" in payload


def test_aegisarena_reset_default_smoke() -> None:
    response = client.post("/reset", json={})
    assert response.status_code == 200

    payload = response.json()
    assert "observation" in payload
    assert "state" in payload
    assert "info" in payload

    info = payload["info"]
    state = payload["state"]
    observation = payload["observation"]

    assert info["env_name"] == "aegisarena_env"
    assert info["reset"] is True
    assert info["mission_type"] in {"game_ops", "finance_ops", "business_ops"}

    assert state["step_count"] == 0
    assert state["done"] is False
    assert state["success"] is False
    assert state["budget_remaining"] > 0

    assert observation["mission_type"] in {"game_ops", "finance_ops", "business_ops"}
    assert observation["step_count"] == 0
    assert observation["done"] is False


def test_aegisarena_reset_forced_game_ops_smoke() -> None:
    response = client.post(
        "/reset",
        json={
            "seed": 123,
            "mission_type": "game_ops",
            "heldout_mode": False,
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["info"]["mission_type"] == "game_ops"
    assert payload["observation"]["mission_type"] == "game_ops"
    assert payload["state"]["mission_type"] == "game_ops"


def test_aegisarena_step_query_tool_smoke() -> None:
    reset_response = client.post(
        "/reset",
        json={
            "seed": 123,
            "mission_type": "finance_ops",
            "heldout_mode": False,
        },
    )
    assert reset_response.status_code == 200

    step_response = client.post(
        "/step",
        json={
            "action": "query_tool",
            "tool_name": "table_lookup",
            "payload": {"row": 0},
        },
    )
    assert step_response.status_code == 200

    payload = step_response.json()
    assert "reward" in payload
    assert "state" in payload
    assert "info" in payload

    assert payload["state"]["step_count"] == 1
    assert payload["state"]["budget_remaining"] < reset_response.json()["state"]["budget_remaining"]
    assert payload["info"]["action"] == "query_tool"
    assert payload["info"]["invalid_action"] is False
    assert payload["done"] is False


def test_aegisarena_submit_final_finance_success_smoke() -> None:
    reset_response = client.post(
        "/reset",
        json={
            "seed": 123,
            "mission_type": "finance_ops",
            "heldout_mode": False,
        },
    )
    assert reset_response.status_code == 200

    hidden_truth = reset_response.json()["state"]["hidden_truth"]
    expected_answer = hidden_truth["expected_answer"]

    step_response = client.post(
        "/step",
        json={
            "action": "submit_final",
            "answer": expected_answer,
            "payload": {},
        },
    )
    assert step_response.status_code == 200

    payload = step_response.json()
    assert payload["done"] is True
    assert payload["state"]["done"] is True
    assert payload["state"]["success"] is True
    assert payload["info"]["action"] == "submit_final"
    assert "reward_breakdown" in payload["info"]


def test_aegisarena_state_smoke() -> None:
    reset_response = client.post(
        "/reset",
        json={
            "seed": 456,
            "mission_type": "business_ops",
            "heldout_mode": False,
        },
    )
    assert reset_response.status_code == 200

    client.post(
        "/step",
        json={
            "action": "inspect_context",
            "payload": {},
        },
    )

    response = client.get("/state")
    assert response.status_code == 200

    payload = response.json()
    assert payload["mission_type"] == "business_ops"
    assert payload["step_count"] == 1
    assert payload["max_steps"] >= 1
    assert payload["budget_remaining"] >= 0
    assert "history" in payload


def test_aegisarena_step_before_reset_returns_409() -> None:
    setattr(app_module, "_ENV_STATE", None)
    setattr(app_module, "_ENV_META", None)
    setattr(app_module, "_ENGINE_META", None)

    response = client.post(
        "/step",
        json={
            "action": "inspect_context",
            "payload": {},
        },
    )
    assert response.status_code == 409
    assert "Call POST /reset first" in response.json()["detail"]


def test_aegisarena_state_before_reset_returns_409() -> None:
    setattr(app_module, "_ENV_STATE", None)
    setattr(app_module, "_ENV_META", None)
    setattr(app_module, "_ENGINE_META", None)

    response = client.get("/state")
    assert response.status_code == 409
    assert "Call POST /reset first" in response.json()["detail"]
