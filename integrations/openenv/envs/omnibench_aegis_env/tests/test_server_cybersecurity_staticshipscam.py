from __future__ import annotations

"""Server smoke test for StaticShipScam.

This test imports the FastAPI app through the full repository package path.
That matters because server/app.py uses relative imports such as
``from ..evaluation_lab.models import ...``.
"""

import importlib
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


SCENARIO = {
    "domain": "cybersecurity",
    "scenario_id": "staticshipscam",
    "scenario_name": "StaticShipScam",
    "upstream_track": "cybergym",
    "category": "supply_chain",
    "adapter": "openenv",
    "assessment_mode": "purple_benchmark",
    "scenario_family": "agentbeats_sprint4",
    "benchmark": "AgentX-AgentBeats Phase 2 Sprint 4",
    "selected_opponent": "cybergym",
    "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/staticshipscam/index.html",
}


def _repo_root() -> Path:
    # tests/ -> omnibench_aegis_env/ -> envs/ -> openenv/ -> integrations/ -> repo
    return Path(__file__).resolve().parents[5]


def _load_app() -> Any:
    root = _repo_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    module = importlib.import_module(
        "integrations.openenv.envs.omnibench_aegis_env.server.app"
    )
    app = getattr(module, "app")
    return app


def _state_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    state = payload.get("state")
    return state if isinstance(state, dict) else payload


def test_server_cybersecurity_staticshipscam_flow() -> None:
    client = TestClient(_load_app())

    health = client.get("/health")
    assert health.status_code == 200
    assert isinstance(health.json(), dict)

    reset = client.post(
        "/reset",
        json={
            "seed": 42,
            "options": {
                "domain": SCENARIO["domain"],
                "scenario_id": SCENARIO["scenario_id"],
                "scenario_name": SCENARIO["scenario_name"],
                "upstream_track": SCENARIO["upstream_track"],
                "category": SCENARIO["category"],
                "assessment_mode": SCENARIO["assessment_mode"],
                "scenario_family": SCENARIO["scenario_family"],
            },
        },
    )
    assert reset.status_code == 200
    assert isinstance(reset.json(), dict)

    step = client.post("/step", json={"action": "advance", "value": 1})
    assert step.status_code == 200
    assert isinstance(step.json(), dict)

    state_response = client.get("/state")
    assert state_response.status_code == 200
    state_json = state_response.json()
    assert isinstance(state_json, dict)

    envelope = _state_envelope(state_json)
    assert isinstance(envelope, dict)

    assert any(
        key in state_json or key in envelope
        for key in ("score", "progress", "done", "success", "step_count", "last_action")
    )
