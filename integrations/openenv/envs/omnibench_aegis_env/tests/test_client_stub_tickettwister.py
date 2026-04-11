from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.openenv.envs.omnibench_aegis_env.client import HttpResult, OpenEnvClient
from integrations.openenv.envs.omnibench_aegis_env.server.app import RUNTIME, app
from integrations.openenv.envs.omnibench_aegis_env.training.llm_agent_stub import HeuristicLLMAgentStub


class InProcessOpenEnvClient(OpenEnvClient):
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
        "scenario_id": "TicketTwister",
        "mission_id": "tickettwister_stub_test",
        "options": {
            "env_id": "omnibench_aegis_env:tau2.tickettwister",
            "domain": "tau2",
            "max_steps": 6,
            "target_score": 100,
        },
    }


def test_client_can_reset_tickettwister_and_expose_public_contract() -> None:
    client = InProcessOpenEnvClient()

    reset = client.reset(_build_reset_payload())
    assert reset["env_id"] == "omnibench_aegis_env:tau2.tickettwister"
    assert reset["scenario_id"] == "TicketTwister"
    assert reset["state"]["failure_mode"] == "none"
    assert "hidden_expected_rewards" not in reset["state"]
    assert reset["observation"]["target_progress"] == 100
    assert reset["observation"]["progress_view"]["target_score"] == 100
    assert reset["observation"]["conversation_trace_public"] == []
    assert reset["actions"] == [
        "load_tasks",
        "prepare_user",
        "run_conversation",
        "score_task_bundle",
        "submit_assessment",
    ]


def test_stub_run_reaches_clean_terminal_state_for_tickettwister() -> None:
    client = InProcessOpenEnvClient()
    stub = HeuristicLLMAgentStub(client, max_decisions=8)

    result = stub.run(_build_reset_payload(seed=7))

    assert result.status == "pass"
    assert result.success is True
    assert result.done is True
    assert result.domain == "tau2"
    assert result.scenario_id == "TicketTwister"

    actions = [turn.action["name"] for turn in result.turns]
    assert actions == [
        "load_tasks",
        "prepare_user",
        "run_conversation",
        "score_task_bundle",
        "submit_assessment",
    ]
    assert abs(result.total_reward - 1.4) < 1e-9
    assert result.final_state["result_quality"] == "clean"
    assert result.final_state["terminal_reason"] == "clean_tickettwister_submission"
    assert result.final_state["public_scorecard"]["coverage_basis"] == "visible_bundle_only"
    assert result.final_state["coverage_ratio"] == 1.0


def test_seed_variation_is_visible_for_tickettwister() -> None:
    client = InProcessOpenEnvClient()
    reset_a = client.reset(_build_reset_payload(seed=1))
    reset_b = client.reset(_build_reset_payload(seed=2))
    assert reset_a["observation"]["visible_ticket_bundle"] != reset_b["observation"]["visible_ticket_bundle"]
