from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.openenv.envs.omnibench_aegis_env.server.app import RUNTIME, app

BIDBOT_ENV_ID = "omnibench_aegis_env:multi_agent.bidbot"
BIDBOT_SCENARIO_ID = "BidBot"

client = TestClient(app)


def setup_function() -> None:
    RUNTIME.active = None


def _reset_payload(seed: int = 7) -> dict:
    return {
        "seed": seed,
        "scenario_id": BIDBOT_SCENARIO_ID,
        "mission_id": "bidbot_server_test",
        "options": {
            "env_id": BIDBOT_ENV_ID,
            "domain": "multi_agent",
            "max_steps": 6,
            "target_score": 100,
        },
    }


def _post_step(name: str, args: dict | None = None) -> dict:
    response = client.post("/step", json={"action": {"name": name, "args": dict(args or {})}})
    assert response.status_code == 200, response.text
    return response.json()


def test_reset_hides_bidbot_hidden_state_and_exposes_public_contract() -> None:
    response = client.post("/reset", json=_reset_payload(seed=1))
    assert response.status_code == 200, response.text
    payload = response.json()

    observation = payload["observation"]
    state = payload["state"]
    info = payload["info"]
    actions = payload["actions"]

    assert payload["env_id"] == BIDBOT_ENV_ID
    assert payload["scenario_id"] == BIDBOT_SCENARIO_ID
    assert state["progress"] == 0
    assert state["final_outcome"] == "in_progress"
    assert state["failure_mode"] == "none"
    assert "hidden_equilibrium_winner" not in state
    assert "hidden_population_weights" not in state
    assert "hidden_market_regime" not in state

    assert observation["target_progress"] == 100
    assert observation["progress_view"]["target_score"] == 100
    assert observation["visible_market_brief"]
    assert observation["visible_pairwise_preview"]
    assert observation["visible_roster"] == []
    assert observation["equilibrium_certificate"] == {}
    assert info["domain"] == "multi_agent"
    assert info["env_name"] == "omnibench_aegis_env"
    assert info["env_id"] == BIDBOT_ENV_ID
    assert actions == [
        "build_roster",
        "simulate_matchups",
        "compute_scores",
        "solve_equilibrium",
        "submit_assessment",
    ]


def test_good_path_reaches_clean_bidbot_terminal_state() -> None:
    reset = client.post("/reset", json=_reset_payload(seed=7))
    assert reset.status_code == 200, reset.text

    step1 = _post_step("build_roster")
    assert step1["reward"] == 0.18
    assert step1["state"]["progress"] == 16
    assert step1["state"]["roster_built"] is True
    assert "bidbot_challenger" in step1["state"]["visible_roster"]

    step2 = _post_step("simulate_matchups")
    assert step2["reward"] == 0.24
    assert step2["state"]["progress"] == 36
    assert step2["state"]["matchups_simulated"] is True
    assert len(step2["state"]["public_matchup_summary"]) >= 1

    step3 = _post_step("compute_scores")
    assert step3["reward"] == 0.23
    assert step3["state"]["progress"] == 56
    assert step3["state"]["scores_computed"] is True
    assert step3["info"]["score_preview"]["top_candidate"] == "bidbot_challenger"
    assert step3["info"]["score_preview"]["coverage"] == "population_level"

    step4 = _post_step("solve_equilibrium")
    assert step4["reward"] == 0.24
    assert step4["state"]["progress"] == 80
    assert step4["state"]["equilibrium_solved"] is True
    assert step4["state"]["equilibrium_certificate"]["winner"] == "bidbot_challenger"

    step5 = _post_step("submit_assessment", {"winner": "bidbot_challenger"})
    assert step5["reward"] == 0.58
    assert step5["done"] is True
    assert step5["state"]["progress"] == 100
    assert step5["state"]["success"] is True
    assert step5["state"]["artifact_quality"] == "clean"
    assert step5["state"]["final_outcome"] == "clean_assessment"
    assert step5["state"]["terminal_reason"] == "clean_bidbot_submission"


def test_bad_path_reaches_hedged_bidbot_terminal_state() -> None:
    reset = client.post("/reset", json=_reset_payload(seed=7))
    assert reset.status_code == 200, reset.text

    step1 = _post_step("submit_assessment", {"winner": "bidbot_challenger or tough"})
    assert step1["reward"] == -0.61
    assert step1["done"] is True
    assert step1["state"]["success"] is False
    assert step1["state"]["hedged_winner"] is True
    assert step1["state"]["artifact_quality"] == "hedged"
    assert step1["state"]["failure_mode"] == "hedged_winner_submission"
    assert step1["state"]["terminal_reason"] == "submitted_multiple_candidate_winners"


def test_seed_variation_changes_visible_bidbot_observation_but_preserves_contract() -> None:
    reset_a = client.post("/reset", json=_reset_payload(seed=1))
    reset_b = client.post("/reset", json=_reset_payload(seed=2))
    assert reset_a.status_code == 200, reset_a.text
    assert reset_b.status_code == 200, reset_b.text

    obs_a = reset_a.json()["observation"]
    obs_b = reset_b.json()["observation"]

    assert obs_a["visible_market_brief"] != obs_b["visible_market_brief"]
    assert set(obs_a["visible_market_brief"].keys()) == set(obs_b["visible_market_brief"].keys())
