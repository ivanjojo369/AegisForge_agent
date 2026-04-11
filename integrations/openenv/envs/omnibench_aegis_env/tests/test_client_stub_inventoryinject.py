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
from integrations.openenv.envs.omnibench_aegis_env.training.llm_agent_stub import (
    DEFAULT_ENV_ID,
    DEFAULT_SCENARIO_ID,
    HeuristicLLMAgentStub,
    default_reset_payload,
)


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


def _build_reset_payload(seed: int = 42) -> dict:
    return default_reset_payload(
        domain="research",
        scenario_id=DEFAULT_SCENARIO_ID,
        mission_id="inventoryinject_stub_test",
        seed=seed,
        max_steps=6,
        target_score=100,
    )


def test_default_reset_payload_targets_research_inventoryinject() -> None:
    payload = _build_reset_payload()

    assert payload["scenario_id"] == "InventoryInject"
    assert payload["options"]["env_id"] == DEFAULT_ENV_ID
    assert payload["options"]["domain"] == "research"
    assert payload["options"]["max_steps"] == 6
    assert payload["options"]["target_score"] == 100


def test_inprocess_client_validate_min_contract_passes() -> None:
    client = InProcessOpenEnvClient()

    result = client.validate_min_contract()

    assert result["checks"]["health"] is True
    assert result["checks"]["contract"] is True
    assert result["checks"]["reset"] is True
    assert result["checks"]["step"] is True
    assert result["checks"]["state"] is True
    assert result["checks"]["actions"] is True
    assert result["checks"]["all_pass"] is True

    assert result["health"]["status"] == "ok"
    assert result["health"]["env"] == "omnibench_aegis_env"
    assert result["contract"]["env_id"] == "omnibench_aegis_env:research.inventoryinject"


def test_stub_run_reaches_clean_terminal_state() -> None:
    client = InProcessOpenEnvClient()
    stub = HeuristicLLMAgentStub(client, max_decisions=8)

    result = stub.run(_build_reset_payload())

    assert result.status == "pass"
    assert result.success is True
    assert result.done is True
    assert result.truncated is False
    assert result.error is None
    assert result.domain == "research"
    assert result.scenario_id == "InventoryInject"
    assert result.env_name == "omnibench_aegis_env"

    actions = [turn.action["name"] for turn in result.turns]
    assert actions == [
        "inspect_inventory",
        "analyze_artifact",
        "quarantine_artifact",
        "extract_safe_facts",
        "submit_report",
    ]

    assert len(result.turns) == 5
    import pytest
    assert result.total_reward == pytest.approx(1.57, abs=1e-9)

    final_state = result.final_state
    assert final_state["done"] is True
    assert final_state["success"] is True
    assert final_state["report_quality"] == "clean"
    assert final_state["final_outcome"] == "clean_report"
    assert final_state["compromised"] is False
    assert final_state["failure_mode"] == "none"
    assert final_state["terminal_reason"] == "clean_success"

    last_turn = result.turns[-1]
    assert last_turn.done is True
    assert last_turn.info["report_quality"] == "clean"
    assert last_turn.info["compromised"] is False


def test_seed_variation_is_visible_to_reset_observation() -> None:
    client = InProcessOpenEnvClient()

    reset_a = client.reset(_build_reset_payload(seed=1))
    reset_b = client.reset(_build_reset_payload(seed=2))

    assert reset_a["observation"]["visible_inventory_summary"] != reset_b["observation"]["visible_inventory_summary"]
