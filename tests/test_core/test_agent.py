from __future__ import annotations

import asyncio
import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any

AGENT_CLASS_NAMES = ("AegisForgeAgent", "Agent")
SUMMARY_NAMES = ("summary", "get_summary", "agent_summary")
STATUS_NAMES = ("status", "get_status", "agent_status")
REQUEST_NAMES = ("handle_request", "invoke", "run", "process")


@dataclass
class DummyMessage:
    payload: dict[str, Any]
    role: str = "user"
    text: str = "ping"
    content: str = "ping"
    parts: list[dict[str, Any]] = field(default_factory=lambda: [{"type": "text", "text": "ping"}])
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.task = self.payload.get("task")
        self.adapter = self.payload.get("adapter")
        self.data = self.payload
        self.message = self
        self.request = self.payload

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]


class DummyUpdater:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.status_updates: list[tuple[Any, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self._terminal_state_reached = False

    async def update_status(self, state: Any, message: Any = None) -> None:
        self.status_updates.append((state, message))
        self.events.append({"kind": "status", "state": state, "message": message})

    async def add_artifact(self, **kwargs: Any) -> None:
        self.artifacts.append(kwargs)
        self.events.append({"kind": "artifact", **kwargs})

    async def start_work(self) -> None:
        self.events.append({"kind": "start_work"})

    async def complete(self) -> None:
        self._terminal_state_reached = True
        self.events.append({"kind": "complete"})

    async def failed(self, message: Any = None) -> None:
        self._terminal_state_reached = True
        self.events.append({"kind": "failed", "message": message})


def _load_agent_module():
    return importlib.import_module("aegisforge.agent")


def _load_agent_class():
    module = _load_agent_module()

    for name in AGENT_CLASS_NAMES:
        cls = getattr(module, name, None)
        if cls is not None:
            return cls

    raise AssertionError("aegisforge.agent must expose AegisForgeAgent or Agent")


def _make_agent(app_config):
    cls = _load_agent_class()
    sig = inspect.signature(cls)
    kwargs: dict[str, Any] = {}

    for name in sig.parameters:
        if name in {"config", "app_config", "settings"}:
            kwargs[name] = app_config

    if kwargs:
        return cls(**kwargs)

    try:
        return cls(app_config)
    except TypeError:
        return cls()


def _resolve_awaitable(value: Any) -> Any:
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def _get_callable(obj: Any, names: tuple[str, ...]):
    for name in names:
        candidate = getattr(obj, name, None)
        if callable(candidate):
            return candidate
    return None


def _coerce_value(value: Any) -> Any:
    value = _resolve_awaitable(value)

    if isinstance(value, (dict, str, bool)) or value is None:
        return value

    if hasattr(value, "model_dump"):
        return value.model_dump()

    if hasattr(value, "dict"):
        return value.dict()

    if hasattr(value, "__dict__"):
        return dict(value.__dict__)

    return value


def test_agent_summary(app_config):
    agent = _make_agent(app_config)
    method = _get_callable(agent, SUMMARY_NAMES)

    if callable(method):
        result = _coerce_value(method())
        assert result is not None
        if isinstance(result, dict) and "name" in result:
            assert result["name"]
        elif isinstance(result, str):
            assert result.strip()
        return

    assert agent is not None
    assert hasattr(agent, "run")


def test_agent_status(app_config):
    agent = _make_agent(app_config)
    method = _get_callable(agent, STATUS_NAMES)

    if callable(method):
        result = _coerce_value(method())
        assert result is not None
        if isinstance(result, dict) and "status" in result:
            assert isinstance(result["status"], str)
            assert result["status"]
        return

    assert agent is not None
    assert hasattr(agent, "run")


def test_agent_handle_request_generic(app_config, monkeypatch):
    module = _load_agent_module()
    monkeypatch.setattr(
        module,
        "get_message_text",
        lambda message: getattr(message, "text", None)
        or getattr(message, "content", None)
        or getattr(message, "payload", {}).get("text")
        or getattr(message, "payload", {}).get("task")
        or "",
    )
    monkeypatch.setattr(
        module,
        "new_agent_text_message",
        lambda text, **kwargs: {"text": text, **kwargs},
    )

    agent = _make_agent(app_config)
    method = _get_callable(agent, REQUEST_NAMES)
    assert callable(method), "agent must expose a request-handling method"

    message = DummyMessage({"task": "ping", "text": "ping"})
    updater = DummyUpdater()
    result = _resolve_awaitable(method(message, updater))

    assert result is None
    assert len(updater.status_updates) >= 1
    assert len(updater.artifacts) >= 1

    artifact = updater.artifacts[-1]
    assert artifact.get("name") == "AegisForgeResponse"
    parts = artifact.get("parts")
    assert parts is not None

    if isinstance(parts, list) and parts:
        rendered = repr(parts[-1])
        assert "AegisForge" in rendered or "Purple" in rendered or "runtime" in rendered

    assert getattr(agent, "turns", 0) >= 1
