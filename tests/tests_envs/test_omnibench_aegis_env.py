from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Mapping

from fastapi.testclient import TestClient

try:
    from integrations.openenv.envs.omnibench_aegis_env.server.app import (
        DEFAULT_ENV_ID,
        DEFAULT_SCENARIO_ID,
        RUNTIME,
        app,
    )
except ImportError:
    ROOT = Path(__file__).resolve().parents[2]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from integrations.openenv.envs.omnibench_aegis_env.server.app import (
        DEFAULT_ENV_ID,
        DEFAULT_SCENARIO_ID,
        RUNTIME,
        app,
    )

REQUIRED_HEALTH_KEYS = {"status", "env", "initialized"}
REQUIRED_RESET_KEYS = {"env_id", "scenario_id", "observation", "state", "info", "actions"}
REQUIRED_STEP_KEYS = {
    "env_id",
    "scenario_id",
    "observation",
    "reward",
    "done",
    "truncated",
    "info",
    "state",
    "actions",
}
REQUIRED_STATE_ENVELOPE_KEYS = {"env_id", "scenario_id", "state", "last_observation", "last_info"}
REQUIRED_PUBLIC_STATE_KEYS = {
    "domain",
    "scenario_id",
    "step_count",
    "max_steps",
    "done",
    "success",
    "last_action",
    "history",
}
EXPECTED_SUPPORTED_DOMAINS = {
    "business_process",
    "computer_use",
    "finance",
    "game",
    "multi_agent",
    "research",
    "tau2",
}


def _action_names(actions: list[Any]) -> set[str]:
    names: set[str] = set()
    for item in actions:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, Mapping):
            name = str(item.get("name") or item.get("action") or "").strip()
            if name:
                names.add(name)
    return names


def _last_action_name(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("name") or value.get("action") or "").strip()
    return str(value or "").strip()


class OmniBenchAegisEnvContractTests(unittest.TestCase):
    """Contract tests aligned to the current BaseDomain-backed OpenEnv server."""

    def setUp(self) -> None:
        RUNTIME.active = None
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        RUNTIME.active = None

    def assert_has_keys(self, payload: Mapping[str, Any], required: set[str]) -> None:
        missing = sorted(required.difference(payload.keys()))
        self.assertFalse(missing, f"missing keys: {missing}; payload={payload}")

    def test_health_exposes_minimum_contract(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assert_has_keys(payload, REQUIRED_HEALTH_KEYS)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["env"], "omnibench_aegis_env")
        self.assertIsInstance(payload["initialized"], bool)

    def test_state_before_reset_returns_409(self) -> None:
        response = self.client.get("/state")
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertIn("detail", body)

    def test_reset_returns_current_public_contract(self) -> None:
        response = self.client.post(
            "/reset",
            json={
                "seed": 123,
                "scenario_id": DEFAULT_SCENARIO_ID,
                "mission_id": "mission-test-1",
                "options": {
                    "target_score": 2,
                    "max_steps": 5,
                },
            },
        )
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assert_has_keys(payload, REQUIRED_RESET_KEYS)
        self.assertEqual(payload["env_id"], DEFAULT_ENV_ID)
        self.assertEqual(payload["scenario_id"], DEFAULT_SCENARIO_ID)

        state = payload["state"]
        observation = payload["observation"]
        self.assert_has_keys(state, REQUIRED_PUBLIC_STATE_KEYS)
        self.assertEqual(state["scenario_id"], DEFAULT_SCENARIO_ID)
        self.assertEqual(state["step_count"], 0)
        self.assertEqual(state["max_steps"], 5)
        self.assertEqual(state["target_score"], 100)
        self.assertFalse(state["done"])
        self.assertFalse(state["success"])
        self.assertIsInstance(state["history"], list)
        self.assertGreaterEqual(len(state["history"]), 0)

        self.assertEqual(payload["info"]["env_name"], "omnibench_aegis_env")
        self.assertEqual(payload["info"]["env_id"], DEFAULT_ENV_ID)
        self.assertEqual(observation.get("scenario_id"), DEFAULT_SCENARIO_ID)
        self.assertIn("available_actions", observation)
        self.assertIn("inspect_inventory", _action_names(payload["actions"]))

    def test_step_shorthand_updates_state_and_state_endpoint(self) -> None:
        reset = self.client.post(
            "/reset",
            json={"options": {"max_steps": 5}},
        )
        self.assertEqual(reset.status_code, 200)
        initial_state = reset.json()["state"]
        initial_history_len = len(initial_state["history"])

        response = self.client.post("/step", json={"action": "inspect_inventory"})
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assert_has_keys(payload, REQUIRED_STEP_KEYS)
        self.assert_has_keys(payload["state"], REQUIRED_PUBLIC_STATE_KEYS)
        self.assertEqual(payload["scenario_id"], DEFAULT_SCENARIO_ID)
        self.assertEqual(payload["state"]["step_count"], 1)
        self.assertEqual(_last_action_name(payload["state"]["last_action"]), "inspect_inventory")
        self.assertFalse(payload["done"])
        self.assertFalse(payload["state"]["done"])
        self.assertGreaterEqual(len(payload["state"]["history"]), initial_history_len + 1)

        state_response = self.client.get("/state")
        self.assertEqual(state_response.status_code, 200)
        state_payload = state_response.json()
        self.assert_has_keys(state_payload, REQUIRED_STATE_ENVELOPE_KEYS)
        self.assertEqual(state_payload["env_id"], DEFAULT_ENV_ID)
        self.assertEqual(state_payload["scenario_id"], DEFAULT_SCENARIO_ID)
        self.assertEqual(state_payload["state"]["step_count"], 1)
        self.assertEqual(_last_action_name(state_payload["state"]["last_action"]), "inspect_inventory")

    def test_step_canonical_action_shape_is_supported(self) -> None:
        reset = self.client.post("/reset", json={})
        self.assertEqual(reset.status_code, 200)

        response = self.client.post(
            "/step",
            json={"name": "inspect_inventory", "args": {}},
        )
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertEqual(payload["state"]["step_count"], 1)
        self.assertEqual(_last_action_name(payload["state"]["last_action"]), "inspect_inventory")

    def test_actions_endpoint_lists_current_available_actions(self) -> None:
        reset = self.client.post("/reset", json={})
        self.assertEqual(reset.status_code, 200)

        response = self.client.get("/actions")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("actions", payload)
        action_names = _action_names(payload["actions"])
        self.assertIn("inspect_inventory", action_names)
        self.assertIn("submit_report", action_names)

    def test_contract_endpoint_exposes_current_supported_domains(self) -> None:
        response = self.client.get("/contract")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["name"], "omnibench_aegis_env")
        self.assertIn("supported_domains", payload)
        self.assertEqual(set(payload["supported_domains"]), EXPECTED_SUPPORTED_DOMAINS)
        self.assertNotIn("web", payload["supported_domains"])
        self.assertIn(DEFAULT_ENV_ID, payload["supported_env_ids"])
        self.assertIn(DEFAULT_SCENARIO_ID, payload["primary_scenarios"])


if __name__ == "__main__":
    unittest.main()
