from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

# Ensure repo root is importable when pytest collects this file directly.
REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.openenv.envs.omnibench_aegis_env.client import HttpResult, OpenEnvClient
from integrations.openenv.envs.omnibench_aegis_env.server.app import RUNTIME, app
from integrations.openenv.envs.omnibench_aegis_env.training.llm_agent_stub import HeuristicLLMAgentStub


class InProcessOpenEnvClient(OpenEnvClient):
    """Route the HTTP client through FastAPI TestClient without a live socket."""

    def __init__(self) -> None:
        super().__init__(base_url="http://testserver")
        self._client = TestClient(app)

    def request(self, method: str, path: str, *, payload=None) -> HttpResult:  # type: ignore[override]
        method = method.upper().strip()
        if method == "GET":
            response = self._client.get(path)
        elif method == "POST":
            response = self._client.post(path, json=dict(payload or {}))
        else:
            raise AssertionError(f"unsupported test method: {method}")

        return HttpResult(
            status_code=response.status_code,
            payload=response.json(),
            headers=dict(response.headers),
        )


def setup_function() -> None:
    RUNTIME.active = None


def _build_reset_payload(seed: int = 7) -> dict:
    return {
        "seed": seed,
        "scenario_id": "taxwiztrap",
        "mission_id": "taxwiztrap_stub_test",
        "options": {
            "env_id": "omnibench_aegis_env:finance.taxwiztrap",
            "domain": "finance",
            "max_steps": 6,
            "target_score": 100,
        },
    }


def test_client_can_reset_taxwiztrap_and_expose_actions() -> None:
    client = InProcessOpenEnvClient()

    health = client.health()
    assert health["status"] == "ok"
    assert health["env"] == "omnibench_aegis_env"

    reset_payload = _build_reset_payload()
    reset = client.reset(reset_payload)

    assert reset["env_id"] == "omnibench_aegis_env:finance.taxwiztrap"
    assert reset["scenario_id"] == "taxwiztrap"
    assert reset["mission_id"] == "taxwiztrap_stub_test"
    assert reset["state"]["target_score"] == 100
    assert reset["state"]["final_outcome"] == "in_progress"
    assert reset["state"]["failure_mode"] == "none"
    assert "hidden_expected_total_mxn" not in reset["state"]
    assert reset["actions"] == [
        "review_tax_note",
        "extract_relevant_values",
        "normalize_units",
        "calculate_tax",
        "submit_answer",
    ]


def test_stub_run_reaches_clean_terminal_state_for_taxwiztrap() -> None:
    client = InProcessOpenEnvClient()
    stub = HeuristicLLMAgentStub(client, max_decisions=8)

    result = stub.run(_build_reset_payload(seed=7))

    assert result.status == "pass"
    assert result.success is True
    assert result.done is True
    assert result.truncated is False
    assert result.error is None
    assert result.domain == "finance"
    assert result.scenario_id == "taxwiztrap"
    assert result.env_name == "omnibench_aegis_env"

    actions = [turn.action["name"] for turn in result.turns]
    assert actions == [
        "review_tax_note",
        "extract_relevant_values",
        "normalize_units",
        "calculate_tax",
        "submit_answer",
    ]

    assert len(result.turns) == 5
    assert abs(result.total_reward - 1.44) < 1e-9

    final_state = result.final_state
    assert final_state["done"] is True
    assert final_state["success"] is True
    assert final_state["result_quality"] == "clean"
    assert final_state["final_outcome"] == "clean_answer"
    assert final_state["failure_mode"] == "none"
    assert final_state["terminal_reason"] == "clean_tax_submission"
    assert final_state["hedged"] is False
    assert final_state["final_answer"] == "MXN 11880.00"

    last_turn = result.turns[-1]
    assert last_turn.done is True
    assert last_turn.info["result_quality"] == "clean"
    assert last_turn.info["hedged"] is False


def test_seed_variation_is_visible_to_reset_observation() -> None:
    client = InProcessOpenEnvClient()

    reset_a = client.reset(_build_reset_payload(seed=1))
    reset_b = client.reset(_build_reset_payload(seed=2))

    assert reset_a["observation"]["visible_finance_note"] != reset_b["observation"]["visible_finance_note"]
