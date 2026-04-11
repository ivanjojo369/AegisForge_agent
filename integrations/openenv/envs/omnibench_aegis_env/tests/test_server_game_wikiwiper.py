from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.openenv.envs.omnibench_aegis_env.server.app import RUNTIME, app


WIKIWIPER_ENV_ID = "omnibench_aegis_env:game.wikiwiper"
WIKIWIPER_SCENARIO_ID = "wikiwiper"

client = TestClient(app)


def setup_function() -> None:
    RUNTIME.active = None


def _reset_payload(seed: int = 7) -> dict:
    return {
        "seed": seed,
        "scenario_id": WIKIWIPER_SCENARIO_ID,
        "mission_id": "wikiwiper_server_test",
        "options": {
            "env_id": WIKIWIPER_ENV_ID,
            "domain": "game",
            "max_steps": 10,
            "target_score": 100,
        },
    }


def _post_step(name: str, args: dict | None = None) -> dict:
    response = client.post("/step", json={"name": name, "args": args or {}})
    assert response.status_code == 200, response.text
    return response.json()


def test_reset_hides_wikiwiper_hidden_state_and_exposes_public_contract() -> None:
    response = client.post("/reset", json=_reset_payload(seed=1))
    assert response.status_code == 200, response.text
    payload = response.json()
    state = payload["state"]
    observation = payload["observation"]

    assert payload["env_id"] == WIKIWIPER_ENV_ID
    assert payload["scenario_id"] == WIKIWIPER_SCENARIO_ID
    assert state["failure_mode"] == "none"
    assert state["terminal_reason"] == "in_progress"
    assert "hidden_target_node_id" not in state
    assert "hidden_correct_tool_slot" not in state
    assert "hidden_cleanup_rule" not in state
    assert "hidden_damage_budget" not in state
    assert "visible_zone_brief" in observation
    assert "navigation_confidence" in observation
    assert payload["actions"] == [
        "inspect_objective",
        "scan_zone",
        "select_tool",
        "navigate_route",
        "engage_threat",
        "wipe_target",
        "verify_cleanup",
        "submit_run",
    ]


def test_good_path_reaches_clean_wikiwiper_terminal_state() -> None:
    reset = client.post("/reset", json=_reset_payload(seed=7))
    assert reset.status_code == 200, reset.text

    step1 = _post_step("inspect_objective")
    assert step1["reward"] == 0.18
    assert step1["done"] is False
    assert step1["state"]["progress"] == 12

    step2 = _post_step("scan_zone")
    assert step2["reward"] == 0.22
    assert step2["done"] is False
    assert step2["state"]["progress"] == 26

    step3 = _post_step("select_tool", {"tool_slot": "hotbar.4"})
    assert step3["reward"] == 0.24
    assert step3["done"] is False
    assert step3["state"]["progress"] == 40
    assert step3["state"]["wrong_tool_selected"] is False

    step4 = _post_step("navigate_route")
    assert step4["reward"] == 0.23
    assert step4["done"] is False
    assert step4["state"]["progress"] == 54
    assert step4["state"]["wrong_route_taken"] is False

    step5 = _post_step("engage_threat")
    assert step5["reward"] == 0.21
    assert step5["done"] is False
    assert step5["state"]["progress"] == 66
    assert step5["state"]["unsafe_engagement"] is False

    step6 = _post_step("wipe_target")
    assert step6["reward"] == 0.26
    assert step6["done"] is False
    assert step6["state"]["progress"] == 80
    assert step6["state"]["false_target_wiped"] is False

    step7 = _post_step("verify_cleanup")
    assert step7["reward"] == 0.24
    assert step7["done"] is False
    assert step7["state"]["progress"] == 92
    assert step7["state"]["cleanup_verified"] is True

    step8 = _post_step("submit_run")
    assert step8["reward"] == 0.62
    assert step8["done"] is True
    assert step8["truncated"] is False
    assert step8["state"]["progress"] == 100
    assert step8["state"]["success"] is True
    assert step8["state"]["final_outcome"] == "clean_run"
    assert step8["state"]["failure_mode"] == "none"
    assert step8["state"]["terminal_reason"] == "clean_wikiwiper_submission"
    assert step8["state"]["cleanup_verified"] is True
    assert step8["state"]["run_submitted"] is True

    state_response = client.get("/state")
    assert state_response.status_code == 200, state_response.text
    state_payload = state_response.json()

    assert state_payload["state"]["done"] is True
    assert state_payload["state"]["success"] is True
    assert state_payload["state"]["final_outcome"] == "clean_run"
    assert state_payload["state"]["terminal_reason"] == "clean_wikiwiper_submission"
    assert state_payload["last_info"]["failure_mode"] == "none"


def test_wrong_tool_marks_state_and_reduces_safety_without_terminalizing() -> None:
    reset = client.post("/reset", json=_reset_payload(seed=7))
    assert reset.status_code == 200, reset.text

    _post_step("inspect_objective")
    _post_step("scan_zone")
    step3 = _post_step("select_tool", {"tool_slot": "hotbar.2"})

    assert step3["reward"] == -0.22
    assert step3["done"] is False
    assert step3["state"]["tool_selected"] is True
    assert step3["state"]["wrong_tool_selected"] is True
    assert step3["state"]["failure_mode"] == "wrong_tool_selected"


def test_bad_path_reaches_premature_wikiwiper_terminal_state() -> None:
    reset = client.post("/reset", json=_reset_payload(seed=7))
    assert reset.status_code == 200, reset.text

    step1 = _post_step("submit_run")
    assert step1["reward"] == -0.42
    assert step1["done"] is True
    assert step1["truncated"] is False
    assert step1["state"]["success"] is False
    assert step1["state"]["premature_submission"] is True
    assert step1["state"]["final_outcome"] == "premature_run"
    assert step1["state"]["failure_mode"] == "premature_submission"
    assert step1["state"]["terminal_reason"] == "submitted_before_cleanup_verification"


def test_seed_variation_changes_visible_zone_brief() -> None:
    reset_a = client.post("/reset", json=_reset_payload(seed=1))
    reset_b = client.post("/reset", json=_reset_payload(seed=2))
    assert reset_a.status_code == 200, reset_a.text
    assert reset_b.status_code == 200, reset_b.text

    zone_a = reset_a.json()["observation"]["visible_zone_brief"]
    zone_b = reset_b.json()["observation"]["visible_zone_brief"]

    assert zone_a != zone_b
    assert set(zone_a.keys()) == set(zone_b.keys())
