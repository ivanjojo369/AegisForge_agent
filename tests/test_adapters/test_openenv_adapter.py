from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def test_openenv_adapter_module_imports() -> None:
    from aegisforge.adapters.openenv import adapter as adapter_module  # noqa: F401


def test_openenv_config_module_imports() -> None:
    from aegisforge.adapters.openenv import config as config_module  # noqa: F401


def test_openenv_adapter_class_exists() -> None:
    from aegisforge.adapters.openenv.adapter import OpenEnvAdapter

    assert OpenEnvAdapter is not None


def test_openenv_adapter_config_class_exists() -> None:
    from aegisforge.adapters.openenv.config import OpenEnvAdapterConfig

    assert OpenEnvAdapterConfig is not None


def test_openenv_adapter_can_be_constructed_from_config() -> None:
    from aegisforge.adapters.openenv.adapter import OpenEnvAdapter
    from aegisforge.adapters.openenv.config import OpenEnvAdapterConfig

    config = OpenEnvAdapterConfig(
        base_url="http://127.0.0.1:8011",
        timeout=10.0,
        env_name="demo_env",
    )

    adapter = OpenEnvAdapter(config=config)

    assert adapter is not None
    assert getattr(adapter, "config", None) is not None
    assert adapter.config.base_url == "http://127.0.0.1:8011"
    assert adapter.config.env_name == "demo_env"


def test_openenv_adapter_exposes_expected_methods() -> None:
    from aegisforge.adapters.openenv.adapter import OpenEnvAdapter
    from aegisforge.adapters.openenv.config import OpenEnvAdapterConfig

    config = OpenEnvAdapterConfig(
        base_url="http://127.0.0.1:8011",
        timeout=10.0,
        env_name="demo_env",
    )

    adapter = OpenEnvAdapter(config=config)

    assert hasattr(adapter, "health")
    assert hasattr(adapter, "reset")
    assert hasattr(adapter, "step")
    assert hasattr(adapter, "state")


def test_openenv_adapter_delegates_health_reset_step_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegisforge.adapters.openenv.adapter import OpenEnvAdapter
    from aegisforge.adapters.openenv.config import OpenEnvAdapterConfig

    calls: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self, base_url: str, timeout: float) -> None:
            self.base_url = base_url
            self.timeout = timeout

        def health(self) -> dict:
            calls.append(("health", {}))
            return {
                "status": "ok",
                "env": "demo_env",
                "initialized": False,
            }

        def reset(self, seed: int | None = None) -> dict:
            calls.append(("reset", {"seed": seed}))
            return {
                "observation": {
                    "message": "reset ok",
                    "score": 0,
                    "step_count": 0,
                    "remaining_steps": 5,
                },
                "state": {
                    "episode_id": "episode-1",
                    "score": 0,
                    "step_count": 0,
                    "max_steps": 5,
                    "target_score": 3,
                    "done": False,
                    "success": False,
                    "last_action": None,
                    "history": [],
                },
                "info": {
                    "env_name": "demo_env",
                    "reset": True,
                },
            }

        def step(self, action: str = "advance", value: int = 1) -> dict:
            calls.append(("step", {"action": action, "value": value}))
            return {
                "observation": {
                    "message": "step ok",
                    "score": value,
                    "step_count": 1,
                    "remaining_steps": 4,
                },
                "reward": float(value),
                "done": False,
                "truncated": False,
                "info": {
                    "target_score": 3,
                    "max_steps": 5,
                },
                "state": {
                    "episode_id": "episode-1",
                    "score": value,
                    "step_count": 1,
                    "max_steps": 5,
                    "target_score": 3,
                    "done": False,
                    "success": False,
                    "last_action": action,
                    "history": [],
                },
            }

        def state(self) -> dict:
            calls.append(("state", {}))
            return {
                "episode_id": "episode-1",
                "score": 1,
                "step_count": 1,
                "max_steps": 5,
                "target_score": 3,
                "done": False,
                "success": False,
                "last_action": "advance",
                "history": [],
            }

    monkeypatch.setattr(
        "aegisforge.adapters.openenv.adapter.DemoEnvClient",
        FakeClient,
    )

    config = OpenEnvAdapterConfig(
        base_url="http://127.0.0.1:8011",
        timeout=10.0,
        env_name="demo_env",
    )

    adapter = OpenEnvAdapter(config=config)

    health_payload = adapter.health()
    reset_payload = adapter.reset(seed=123)
    step_payload = adapter.step(action="advance", value=1)
    state_payload = adapter.state()

    assert health_payload["status"] == "ok"
    assert reset_payload["info"]["env_name"] == "demo_env"
    assert step_payload["reward"] == 1.0
    assert state_payload["last_action"] == "advance"

    assert calls == [
        ("health", {}),
        ("reset", {"seed": 123}),
        ("step", {"action": "advance", "value": 1}),
        ("state", {}),
    ]
    