from __future__ import annotations

import asyncio
import importlib
import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

EXECUTOR_NAMES = ("AegisForgeExecutor", "Executor")
STATUS_METHOD_NAMES = ("adapter_statuses", "get_adapter_statuses", "adapters_status")


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
        self.request = self.payload

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]


@dataclass
class DummyTask:
    id: str = "task-1"
    context_id: str = "ctx-1"
    state: str | None = None

    def __post_init__(self) -> None:
        self.status = SimpleNamespace(state=self.state)


@dataclass
class DummyContext:
    payload: dict[str, Any]
    current_task: Any = None

    def __post_init__(self) -> None:
        self.message = DummyMessage(self.payload)


class DummyEventQueue:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)


class RecordingUpdater:
    instances: list["RecordingUpdater"] = []

    def __init__(self, event_queue: DummyEventQueue, task_id: str, context_id: str) -> None:
        self.event_queue = event_queue
        self.task_id = task_id
        self.context_id = context_id
        self._terminal_state_reached = False
        self.actions: list[tuple[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        RecordingUpdater.instances.append(self)

    async def start_work(self) -> None:
        self.actions.append(("start_work", None))

    async def complete(self) -> None:
        self._terminal_state_reached = True
        self.actions.append(("complete", None))

    async def failed(self, message: Any = None) -> None:
        self._terminal_state_reached = True
        self.actions.append(("failed", message))

    async def update_status(self, state: Any, message: Any = None) -> None:
        self.actions.append(("update_status", {"state": state, "message": message}))

    async def add_artifact(self, **kwargs: Any) -> None:
        self.artifacts.append(kwargs)
        self.actions.append(("add_artifact", kwargs))


class PassingAgent:
    instances: list["PassingAgent"] = []

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []
        PassingAgent.instances.append(self)

    async def run(self, message: Any, updater: RecordingUpdater) -> None:
        self.calls.append((message, updater))
        await updater.update_status("working", {"text": "Thinking..."})
        await updater.add_artifact(parts=[{"type": "text", "text": "pong"}], name="AegisForgeResponse")


class FailingAgent:
    async def run(self, message: Any, updater: RecordingUpdater) -> None:
        raise RuntimeError("openenv disabled")


def _load_executor_module():
    return importlib.import_module("aegisforge.executor")


def _load_executor_class():
    module = _load_executor_module()

    for name in EXECUTOR_NAMES:
        cls = getattr(module, name, None)
        if cls is not None:
            return cls

    raise AssertionError("aegisforge.executor must expose AegisForgeExecutor or Executor")


def _make_executor(app_config):
    cls = _load_executor_class()
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


def _run(coro):
    return asyncio.run(coro)


def test_executor_generic_path(app_config, monkeypatch):
    module = _load_executor_module()
    RecordingUpdater.instances.clear()
    PassingAgent.instances.clear()

    monkeypatch.setattr(module, "TaskUpdater", RecordingUpdater)
    monkeypatch.setattr(module, "AegisForgeAgent", PassingAgent)
    monkeypatch.setattr(module, "new_task", lambda msg: DummyTask())

    executor = _make_executor(app_config)
    context = DummyContext({"task": "ping"}, current_task=None)
    event_queue = DummyEventQueue()

    result = _run(executor.execute(context, event_queue))

    assert result is None
    assert len(event_queue.events) == 1
    assert isinstance(event_queue.events[0], DummyTask)
    assert len(PassingAgent.instances) == 1
    assert len(RecordingUpdater.instances) == 1

    updater = RecordingUpdater.instances[0]
    action_names = [name for name, _ in updater.actions]
    assert "start_work" in action_names
    assert "update_status" in action_names
    assert "add_artifact" in action_names
    assert "complete" in action_names
    assert updater._terminal_state_reached is True


def test_executor_openenv_disabled_returns_error_or_non_success(app_config, monkeypatch):
    module = _load_executor_module()
    RecordingUpdater.instances.clear()

    monkeypatch.setattr(module, "TaskUpdater", RecordingUpdater)
    monkeypatch.setattr(module, "AegisForgeAgent", FailingAgent)
    monkeypatch.setattr(module, "new_task", lambda msg: DummyTask(context_id="ctx-openenv"))
    monkeypatch.setattr(module, "new_agent_text_message", lambda text, **kwargs: {"text": text, **kwargs})

    executor = _make_executor(app_config)
    context = DummyContext({"adapter": "openenv", "task": "ping"}, current_task=None)
    event_queue = DummyEventQueue()

    result = _run(executor.execute(context, event_queue))

    assert result is None
    assert len(RecordingUpdater.instances) == 1
    updater = RecordingUpdater.instances[0]
    failed_events = [payload for name, payload in updater.actions if name == "failed"]
    assert failed_events, "executor should fail the task when agent.run raises"

    payload_repr = repr(failed_events[-1])
    assert "openenv disabled" in payload_repr or "Agent error" in payload_repr
    assert updater._terminal_state_reached is True


def test_executor_adapter_statuses_shape(app_config):
    executor = _make_executor(app_config)

    statuses_fn = None
    for name in STATUS_METHOD_NAMES:
        candidate = getattr(executor, name, None)
        if callable(candidate):
            statuses_fn = candidate
            break

    if callable(statuses_fn):
        statuses = statuses_fn()
        if inspect.isawaitable(statuses):
            statuses = asyncio.run(statuses)
        assert isinstance(statuses, dict)
        return

    adapters = getattr(executor, "adapters", None)
    if adapters is not None:
        assert isinstance(adapters, dict)
        return

    assert hasattr(executor, "execute")
