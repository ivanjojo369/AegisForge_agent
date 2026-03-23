from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def test_aegisarena_solver_module_imports() -> None:
    from aegisforge.adapters.openenv import solver as solver_module  # noqa: F401


def test_aegisarena_solver_config_exists() -> None:
    from aegisforge.adapters.openenv.solver import AegisArenaSolverConfig

    assert AegisArenaSolverConfig is not None


def test_aegisarena_solver_class_exists() -> None:
    from aegisforge.adapters.openenv.solver import AegisArenaSolver

    assert AegisArenaSolver is not None


def test_aegisarena_solver_can_be_constructed() -> None:
    from aegisforge.adapters.openenv.solver import (
        AegisArenaSolver,
        AegisArenaSolverConfig,
    )

    config = AegisArenaSolverConfig(
        base_url="http://127.0.0.1:8012",
        timeout=10.0,
        model_name="favorite_llm",
        max_solver_steps=6,
    )

    solver = AegisArenaSolver(config=config)

    assert solver is not None
    assert solver.config.base_url == "http://127.0.0.1:8012"
    assert solver.config.model_name == "favorite_llm"
    assert solver.config.max_solver_steps == 6


def test_aegisarena_solver_metadata() -> None:
    from aegisforge.adapters.openenv.solver import (
        AegisArenaSolver,
        AegisArenaSolverConfig,
    )

    config = AegisArenaSolverConfig(
        base_url="http://127.0.0.1:8012",
        timeout=10.0,
        model_name="favorite_llm",
        max_solver_steps=4,
    )

    solver = AegisArenaSolver(config=config)
    metadata = solver.to_metadata()

    assert metadata["solver"] == "aegisarena"
    assert metadata["base_url"] == "http://127.0.0.1:8012"
    assert metadata["model_name"] == "favorite_llm"
    assert metadata["max_solver_steps"] == 4


def test_aegisarena_solver_solve_once_finance_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegisforge.adapters.openenv.solver import (
        AegisArenaSolver,
        AegisArenaSolverConfig,
    )

    calls: list[tuple[str, dict]] = []

    class FakeAegisArenaEnvClient:
        def __init__(self, base_url: str, timeout: float) -> None:
            self.base_url = base_url
            self.timeout = timeout
            self._state = {
                "episode_id": "episode-finance-001",
                "mission_id": "finance-demo-001",
                "mission_type": "finance_ops",
                "hidden_truth": {
                    "expected_answer": "PROFITABLE",
                    "success_label": "PROFITABLE",
                    "profit": 50,
                },
                "step_count": 0,
                "max_steps": 8,
                "budget_remaining": 100,
                "cost_so_far": 0,
                "score": 0.0,
                "success": False,
                "done": False,
                "failure_mode": None,
                "history": [],
            }

        def health(self) -> dict:
            calls.append(("health", {}))
            return {
                "status": "ok",
                "env": "aegisarena_env",
                "initialized": False,
                "active_mission_type": None,
            }

        def reset(
            self,
            seed: int | None = None,
            mission_type: str | None = None,
            heldout_mode: bool = False,
        ) -> dict:
            calls.append(
                (
                    "reset",
                    {
                        "seed": seed,
                        "mission_type": mission_type,
                        "heldout_mode": heldout_mode,
                    },
                )
            )

            return {
                "observation": {
                    "mission_id": "finance-demo-001",
                    "mission_type": "finance_ops",
                    "mission_summary": "Finance mission",
                    "available_tools": [
                        "inspect_context",
                        "query_tool",
                        "submit_final",
                        "table_lookup",
                    ],
                    "observed_context": [
                        {
                            "type": "finance_table",
                            "value": [{"quarter": "Q1", "revenue": 160, "cost": 110}],
                        }
                    ],
                    "recent_actions": [],
                    "step_count": 0,
                    "max_steps": 8,
                    "budget_remaining": 100,
                    "cost_so_far": 0,
                    "done": False,
                },
                "state": dict(self._state),
                "info": {
                    "env_name": "aegisarena_env",
                    "reset": True,
                    "mission_type": "finance_ops",
                    "heldout_mode": heldout_mode,
                },
            }

        def step(
            self,
            action: str,
            target: str | None = None,
            tool_name: str | None = None,
            answer: str | None = None,
            plan_text: str | None = None,
            payload: dict | None = None,
        ) -> dict:
            calls.append(
                (
                    "step",
                    {
                        "action": action,
                        "target": target,
                        "tool_name": tool_name,
                        "answer": answer,
                        "plan_text": plan_text,
                        "payload": payload or {},
                    },
                )
            )

            if action == "query_tool":
                self._state["step_count"] = 1
                self._state["budget_remaining"] = 98
                self._state["cost_so_far"] = 2
                self._state["score"] = -0.025
                self._state["history"].append(
                    {
                        "action": "query_tool",
                        "tool_name": tool_name,
                        "payload": payload or {},
                        "reward": -0.025,
                        "step_count_after": 1,
                        "budget_remaining_after": 98,
                        "done": False,
                        "success": False,
                    }
                )

                return {
                    "observation": {
                        "mission_id": "finance-demo-001",
                        "mission_type": "finance_ops",
                        "mission_summary": "Finance mission",
                        "available_tools": [
                            "inspect_context",
                            "query_tool",
                            "submit_final",
                            "table_lookup",
                        ],
                        "observed_context": [],
                        "recent_actions": self._state["history"][-1:],
                        "step_count": 1,
                        "max_steps": 8,
                        "budget_remaining": 98,
                        "cost_so_far": 2,
                        "done": False,
                    },
                    "reward": -0.025,
                    "done": False,
                    "truncated": False,
                    "info": {
                        "mission_type": "finance_ops",
                        "invalid_action": False,
                        "action": "query_tool",
                        "action_cost": 2,
                    },
                    "state": dict(self._state),
                }

            if action == "submit_final":
                self._state["step_count"] = 2
                self._state["budget_remaining"] = 96
                self._state["cost_so_far"] = 4
                self._state["score"] = 17.925
                self._state["success"] = answer == "PROFITABLE"
                self._state["done"] = True
                self._state["history"].append(
                    {
                        "action": "submit_final",
                        "answer": answer,
                        "payload": payload or {},
                        "reward": 17.95,
                        "step_count_after": 2,
                        "budget_remaining_after": 96,
                        "done": True,
                        "success": self._state["success"],
                    }
                )

                return {
                    "observation": {
                        "mission_id": "finance-demo-001",
                        "mission_type": "finance_ops",
                        "mission_summary": "Finance mission",
                        "available_tools": [
                            "inspect_context",
                            "query_tool",
                            "submit_final",
                            "table_lookup",
                        ],
                        "observed_context": [],
                        "recent_actions": self._state["history"][-2:],
                        "step_count": 2,
                        "max_steps": 8,
                        "budget_remaining": 96,
                        "cost_so_far": 4,
                        "done": True,
                    },
                    "reward": 17.95,
                    "done": True,
                    "truncated": False,
                    "info": {
                        "mission_type": "finance_ops",
                        "invalid_action": False,
                        "action": "submit_final",
                        "action_cost": 2,
                    },
                    "state": dict(self._state),
                }

            raise AssertionError(f"Unexpected action in fake client: {action}")

        def state(self) -> dict:
            calls.append(("state", {}))
            return dict(self._state)

    monkeypatch.setattr(
        "aegisforge.adapters.openenv.solver.AegisArenaEnvClient",
        FakeAegisArenaEnvClient,
    )

    solver = AegisArenaSolver(
        config=AegisArenaSolverConfig(
            base_url="http://127.0.0.1:8012",
            timeout=10.0,
            model_name="favorite_llm",
            max_solver_steps=6,
        )
    )

    result = solver.solve_once(
        mission_type="finance_ops",
        seed=12345,
        heldout_mode=False,
    )

    assert result["health"]["status"] == "ok"
    assert result["reset"]["info"]["mission_type"] == "finance_ops"
    assert result["final_state"]["mission_type"] == "finance_ops"
    assert result["final_state"]["done"] is True
    assert result["final_state"]["success"] is True
    assert result["solver_summary"]["validation"] == "AEGISARENA SOLVER OK"
    assert result["success_rate"]["success_rate"] == 1.0

    assert len(result["trajectory"]) == 2
    assert result["trajectory"][0]["request"]["action"] == "query_tool"
    assert result["trajectory"][1]["request"]["action"] == "submit_final"
    assert result["trajectory"][1]["request"]["answer"] == "PROFITABLE"

    assert len(result["llm_calls"]) == 2
    assert result["llm_calls"][0]["model_name"] == "favorite_llm"

    assert calls == [
        ("health", {}),
        ("reset", {"seed": 12345, "mission_type": "finance_ops", "heldout_mode": False}),
        (
            "step",
            {
                "action": "query_tool",
                "target": None,
                "tool_name": "table_lookup",
                "answer": None,
                "plan_text": None,
                "payload": {"row": 0},
            },
        ),
        (
            "step",
            {
                "action": "submit_final",
                "target": None,
                "tool_name": None,
                "answer": "PROFITABLE",
                "plan_text": None,
                "payload": {},
            },
        ),
        ("state", {}),
    ]
