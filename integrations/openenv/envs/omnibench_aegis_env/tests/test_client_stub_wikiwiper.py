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
        "scenario_id": "wikiwiper",
        "mission_id": "wikiwiper_stub_test",
        "options": {
            "env_id": "omnibench_aegis_env:game.wikiwiper",
            "domain": "game",
            "max_steps": 10,
            "target_score": 100,
        },
    }


def test_client_can_reset_wikiwiper_and_expose_actions() -> None:
    client = InProcessOpenEnvClient()

    health = client.health()
    assert health["status"] == "ok"
    assert health["env"] == "omnibench_aegis_env"

    reset = client.reset(_build_reset_payload())
    assert reset["env_id"] == "omnibench_aegis_env:game.wikiwiper"
    assert reset["scenario_id"] == "wikiwiper"
    assert reset["state"]["target_score"] == 100
    assert reset["state"]["failure_mode"] == "none"
    assert "hidden_target_node_id" not in reset["state"]
    assert reset["actions"] == [
        "inspect_objective",
        "scan_zone",
        "select_tool",
        "navigate_route",
        "engage_threat",
        "wipe_target",
        "verify_cleanup",
        "submit_run",
    ]


def test_stub_run_reaches_clean_terminal_state_for_wikiwiper() -> None:
    client = InProcessOpenEnvClient()
    stub = HeuristicLLMAgentStub(client, max_decisions=10)

    result = stub.run(_build_reset_payload(seed=7))

    assert result.status == "pass"
    assert result.success is True
    assert result.done is True
    assert result.truncated is False
    assert result.error is None
    assert result.domain == "game"
    assert result.scenario_id == "wikiwiper"

    actions = [turn.action["name"] for turn in result.turns]
    assert actions == [
        "inspect_objective",
        "scan_zone",
        "select_tool",
        "navigate_route",
        "engage_threat",
        "wipe_target",
        "verify_cleanup",
        "submit_run",
    ]

    assert len(result.turns) == 8
    assert abs(result.total_reward - 2.2) < 1e-9
    assert result.final_state["final_outcome"] == "clean_run"
    assert result.final_state["terminal_reason"] == "clean_wikiwiper_submission"
    assert result.final_state["failure_mode"] == "none"
    assert result.final_state["cleanup_verified"] is True
    assert result.final_state["selected_tool_slot"] == "hotbar.4"


def test_seed_variation_is_visible_for_wikiwiper() -> None:
    client = InProcessOpenEnvClient()
    reset_a = client.reset(_build_reset_payload(seed=1))
    reset_b = client.reset(_build_reset_payload(seed=2))
    assert reset_a["observation"]["visible_zone_brief"] != reset_b["observation"]["visible_zone_brief"]
