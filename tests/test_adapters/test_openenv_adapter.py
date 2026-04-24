from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest


def _find_repo_root() -> Path:
    """Find the repository root without assuming this test file's depth."""
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "src" / "aegisforge").exists():
            return candidate
    # Fallback for the common layout tests/<file>.py.
    return here.parents[1]


REPO_ROOT = _find_repo_root()
SRC_ROOT = REPO_ROOT / "src"

for path in (REPO_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_openenv_adapter_module_imports() -> None:
    adapter_module = importlib.import_module("aegisforge.adapters.openenv.adapter")
    assert adapter_module is not None


def test_openenv_config_module_imports() -> None:
    config_module = importlib.import_module("aegisforge.adapters.openenv.config")
    assert config_module is not None


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

    for method_name in ("health", "reset", "step", "state"):
        assert hasattr(adapter, method_name), f"OpenEnvAdapter is missing {method_name}()"


def _call_reset(adapter: Any) -> dict[str, Any]:
    """Call reset while tolerating the legacy and newer adapter signatures."""
    try:
        return adapter.reset(seed=123)
    except TypeError:
        try:
            return adapter.reset({"seed": 123})
        except TypeError:
            return adapter.reset()


def _call_step(adapter: Any) -> dict[str, Any]:
    """Call step while tolerating action-as-kwargs and action-as-dict adapters."""
    try:
        return adapter.step(action="advance", value=1)
    except TypeError:
        try:
            return adapter.step({"action": "advance", "value": 1})
        except TypeError:
            return adapter.step({"name": "advance", "args": {"value": 1}})


def test_openenv_adapter_delegates_health_reset_step_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegisforge.adapters.openenv import adapter as adapter_module
    from aegisforge.adapters.openenv.adapter import OpenEnvAdapter
    from aegisforge.adapters.openenv.config import OpenEnvAdapterConfig

    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        """Small client double that accepts both old and new adapter call styles."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs
            self.base_url = kwargs.get("base_url") or (args[0] if args else "http://127.0.0.1:8011")
            self.timeout = kwargs.get("timeout") or (args[1] if len(args) > 1 else 10.0)

        def health(self) -> dict[str, Any]:
            calls.append(("health", {}))
            return {
                "status": "ok",
                "env": "demo_env",
                "initialized": False,
            }

        def reset(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            seed = kwargs.get("seed")
            if seed is None and args:
                first = args[0]
                if isinstance(first, dict):
                    seed = first.get("seed")
                elif isinstance(first, int):
                    seed = first
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

        def step(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            action = kwargs.get("action")
            value = kwargs.get("value", 1)

            if args:
                first = args[0]
                if isinstance(first, dict):
                    action = first.get("action") or first.get("name") or action
                    action_args = first.get("args")
                    if isinstance(action_args, dict):
                        value = action_args.get("value", value)
                    value = first.get("value", value)
                elif isinstance(first, str):
                    action = first

            action = action or "advance"
            value = int(value)
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

        def state(self) -> dict[str, Any]:
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

    # Different repo revisions have used different client names internally.
    monkeypatch.setattr(adapter_module, "DemoEnvClient", FakeClient, raising=False)
    monkeypatch.setattr(adapter_module, "OpenEnvClient", FakeClient, raising=False)

    config = OpenEnvAdapterConfig(
        base_url="http://127.0.0.1:8011",
        timeout=10.0,
        env_name="demo_env",
    )

    adapter = OpenEnvAdapter(config=config)

    health_payload = adapter.health()
    reset_payload = _call_reset(adapter)
    step_payload = _call_step(adapter)
    state_payload = adapter.state()

    assert health_payload["status"] == "ok"
    assert reset_payload["info"]["env_name"] == "demo_env"
    assert step_payload["reward"] == 1.0
    assert state_payload["last_action"] == "advance"

    assert [name for name, _payload in calls] == ["health", "reset", "step", "state"]
