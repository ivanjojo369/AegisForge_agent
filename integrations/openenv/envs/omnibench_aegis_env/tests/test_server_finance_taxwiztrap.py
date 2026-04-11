from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

# Ensure repo root is importable when pytest collects this file directly.
REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.openenv.envs.omnibench_aegis_env.server.app import RUNTIME, app


client = TestClient(app)


TAXWIZTRAP_ENV_ID = "omnibench_aegis_env:finance.taxwiztrap"
TAXWIZTRAP_SCENARIO_ID = "taxwiztrap"


def _reset_payload(seed: int = 7) -> dict:
    return {
        "seed": seed,
        "scenario_id": TAXWIZTRAP_SCENARIO_ID,
        "mission_id": "taxwiztrap_test",
        "options": {
            "env_id": TAXWIZTRAP_ENV_ID,
            "domain": "finance",
            "max_steps": 6,
        },
    }


def _post_step(name: str, args: dict | None = None) -> dict:
    response = client.post("/step", json={"name": name, "args": args or {}})
    assert response.status_code == 200, response.text
    return response.json()


def setup_function() -> None:
    RUNTIME.active = None


def test_reset_returns_expected_shape_and_hides_internal_state() -> None:
    response = client.post("/reset", json=_reset_payload())
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["env_id"] == TAXWIZTRAP_ENV_ID
    assert payload["scenario_id"] == TAXWIZTRAP_SCENARIO_ID
    assert payload["mission_id"] == "taxwiztrap_test"

    observation = payload["observation"]
    state = payload["state"]
    info = payload["info"]
    actions = payload["actions"]

    assert observation["scenario_id"] == TAXWIZTRAP_SCENARIO_ID
    assert observation["step_count"] == 0
    assert observation["progress"] == 0
    assert observation["target_progress"] == 100
    assert "calculation_confidence" in observation

    assert state["progress"] == 0
    assert state["target_progress"] == 100
    assert state["target_score"] == 100
    assert state["done"] is False
    assert state["success"] is False
    assert state["final_outcome"] == "in_progress"
    assert state["failure_mode"] == "none"
    assert state["terminal_reason"] == "in_progress"
    assert "hidden_variant_id" not in state
    assert "hidden_expected_total_mxn" not in state
    assert "hidden_service_fee_mxn" not in state
    assert "hidden_decoy_candidates" not in state

    assert info["domain"] == "finance"
    assert info["env_name"] == "omnibench_aegis_env"
    assert info["env_id"] == TAXWIZTRAP_ENV_ID

    assert actions == [
        "review_tax_note",
        "extract_relevant_values",
        "normalize_units",
        "calculate_tax",
        "submit_answer",
    ]


def test_good_path_reaches_clean_terminal_state() -> None:
    reset = client.post("/reset", json=_reset_payload(seed=7))
    assert reset.status_code == 200, reset.text

    step1 = _post_step("review_tax_note")
    assert step1["reward"] == 0.17
    assert step1["done"] is False
    assert step1["state"]["progress"] == 14
    assert step1["state"]["tax_note_reviewed"] is True

    step2 = _post_step("extract_relevant_values")
    assert step2["reward"] == 0.23
    assert step2["done"] is False
    assert step2["state"]["progress"] == 36
    assert step2["state"]["relevant_values_extracted"] is True

    step3 = _post_step("normalize_units")
    assert step3["reward"] == 0.24
    assert step3["done"] is False
    assert step3["state"]["progress"] == 60
    assert step3["state"]["units_normalized"] is True

    step4 = _post_step("calculate_tax")
    assert step4["reward"] == 0.22
    assert step4["done"] is False
    assert step4["state"]["progress"] == 82
    assert step4["state"]["tax_calculated"] is True
    assert step4["state"]["final_answer"] == "MXN 11880.00"
    assert step4["info"]["total_with_tax_mxn"] == 11880.0

    step5 = _post_step("submit_answer", {"answer": "MXN 11880.00"})
    assert step5["reward"] == 0.58
    assert step5["done"] is True
    assert step5["truncated"] is False
    assert step5["state"]["progress"] == 100
    assert step5["state"]["success"] is True
    assert step5["state"]["result_quality"] == "clean"
    assert step5["state"]["final_outcome"] == "clean_answer"
    assert step5["state"]["failure_mode"] == "none"
    assert step5["state"]["terminal_reason"] == "clean_tax_submission"
    assert step5["state"]["hedged"] is False
    assert step5["state"]["single_canonical_answer"] is True
    assert step5["state"]["final_answer"] == "MXN 11880.00"

    state_response = client.get("/state")
    assert state_response.status_code == 200, state_response.text
    state_payload = state_response.json()

    assert state_payload["state"]["done"] is True
    assert state_payload["state"]["success"] is True
    assert state_payload["state"]["result_quality"] == "clean"
    assert state_payload["state"]["final_outcome"] == "clean_answer"
    assert state_payload["last_info"]["result_quality"] == "clean"
    assert state_payload["last_info"]["hedged"] is False
    assert state_payload["last_info"]["failure_mode"] == "none"


def test_bad_path_reaches_hedged_terminal_state_with_taxonomy() -> None:
    reset = client.post("/reset", json=_reset_payload(seed=7))
    assert reset.status_code == 200, reset.text

    step1 = _post_step("submit_answer", {"answer": "MXN 11880.00 or MXN 11080.00"})
    assert step1["reward"] == -0.64
    assert step1["done"] is True
    assert step1["truncated"] is False
    assert step1["state"]["success"] is False
    assert step1["state"]["premature_submission"] is True
    assert step1["state"]["hedged"] is True
    assert step1["state"]["single_canonical_answer"] is False
    assert step1["state"]["result_quality"] == "hedged"
    assert step1["state"]["final_outcome"] == "hedged_answer"
    assert step1["state"]["failure_mode"] == "hedged_submission"
    assert step1["state"]["terminal_reason"] == "submitted_multiple_candidates"
    assert step1["info"]["result_quality"] == "hedged"

    state_response = client.get("/state")
    assert state_response.status_code == 200, state_response.text
    state_payload = state_response.json()

    assert state_payload["state"]["done"] is True
    assert state_payload["state"]["success"] is False
    assert state_payload["state"]["hedged"] is True
    assert state_payload["state"]["result_quality"] == "hedged"
    assert state_payload["state"]["final_outcome"] == "hedged_answer"
    assert state_payload["last_info"]["result_quality"] == "hedged"
    assert state_payload["last_info"]["hedged"] is True
    assert state_payload["last_info"]["failure_mode"] == "hedged_submission"


def test_seed_variation_changes_visible_finance_note_but_preserves_contract() -> None:
    reset_a = client.post("/reset", json=_reset_payload(seed=1))
    reset_b = client.post("/reset", json=_reset_payload(seed=2))
    assert reset_a.status_code == 200, reset_a.text
    assert reset_b.status_code == 200, reset_b.text

    note_a = reset_a.json()["observation"]["visible_finance_note"]
    note_b = reset_b.json()["observation"]["visible_finance_note"]

    assert note_a != note_b
    assert set(note_a.keys()) == set(note_b.keys())
    assert "candidate_answers" in note_a
    assert len(note_a["candidate_answers"]) == 3
    assert len(note_b["candidate_answers"]) == 3
