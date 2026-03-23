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


def test_aegisarena_game_ops_reset_forced() -> None:
    response = client.post(
        "/reset",
        json={
            "seed": 12345,
            "mission_type": "game_ops",
            "heldout_mode": False,
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["info"]["mission_type"] == "game_ops"
    assert payload["observation"]["mission_type"] == "game_ops"
    assert payload["state"]["mission_type"] == "game_ops"
    assert payload["state"]["hidden_truth"]["expected_answer"] == "objective_reached"


def test_aegisarena_game_ops_query_tool_map_probe() -> None:
    reset_response = client.post(
        "/reset",
        json={
            "seed": 12345,
            "mission_type": "game_ops",
            "heldout_mode": False,
        },
    )
    assert reset_response.status_code == 200
    initial_budget = reset_response.json()["state"]["budget_remaining"]

    step_response = client.post(
        "/step",
        json={
            "action": "query_tool",
            "tool_name": "map_probe",
            "payload": {"region": "forward_path"},
        },
    )
    assert step_response.status_code == 200

    payload = step_response.json()
    assert payload["info"]["action"] == "query_tool"
    assert payload["info"]["invalid_action"] is False
    assert payload["info"]["tool_name"] == "map_probe"
    assert payload["state"]["mission_type"] == "game_ops"
    assert payload["state"]["step_count"] == 1
    assert payload["state"]["budget_remaining"] < initial_budget
    assert payload["done"] is False


def test_aegisarena_game_ops_submit_final_success() -> None:
    reset_response = client.post(
        "/reset",
        json={
            "seed": 12345,
            "mission_type": "game_ops",
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
    assert payload["state"]["mission_type"] == "game_ops"
    assert payload["info"]["action"] == "submit_final"
    assert payload["info"]["invalid_action"] is False
    assert payload["info"]["final_submission_correct"] is True
    