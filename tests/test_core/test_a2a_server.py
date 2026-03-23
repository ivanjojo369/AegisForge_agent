from __future__ import annotations

import importlib
import inspect
from typing import Any, Callable

from fastapi.testclient import TestClient


APP_FACTORY_NAMES = ("create_app", "build_app", "get_app", "make_app")


def _load_server_module():
    return importlib.import_module("aegisforge.a2a_server")


def _resolve_app_factory() -> Callable[..., Any] | None:
    module = _load_server_module()
    for name in APP_FACTORY_NAMES:
        factory = getattr(module, name, None)
        if callable(factory):
            return factory
    return None


def _cfg_get(app_config: Any, key: str, default: Any) -> Any:
    if isinstance(app_config, dict):
        return app_config.get(key, default)
    return getattr(app_config, key, default)


def _call_factory(factory: Callable[..., Any], app_config: Any):
    sig = inspect.signature(factory)
    kwargs: dict[str, Any] = {}

    host = _cfg_get(app_config, "host", "127.0.0.1")
    port = _cfg_get(app_config, "port", 8000)
    card_url = _cfg_get(
        app_config,
        "card_url",
        f"http://{host}:{port}/.well-known/agent-card.json",
    )

    for name in sig.parameters:
        if name in {"config", "app_config", "settings"}:
            kwargs[name] = app_config
        elif name == "host":
            kwargs[name] = host
        elif name == "port":
            kwargs[name] = port
        elif name in {"card_url", "agent_card_url"}:
            kwargs[name] = card_url

    if kwargs:
        return factory(**kwargs)
    return factory()


def _build_app(app_config):
    module = _load_server_module()
    factory = _resolve_app_factory()
    if factory is not None:
        return _call_factory(factory, app_config)

    app = getattr(module, "app", None)
    if app is not None:
        return app

    raise AssertionError(
        "aegisforge.a2a_server must expose one of: create_app, build_app, get_app, make_app, or app"
    )


def _get_json(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_server_health(app_config):
    client = TestClient(_build_app(app_config))
    data = _get_json(client.get("/health"))

    assert isinstance(data, dict)
    assert data.get("status") in {"ok", "healthy", "up"}

    if "service" in data:
        assert isinstance(data["service"], str)
        assert data["service"]



def test_server_agent_card(app_config):
    client = TestClient(_build_app(app_config))
    data = _get_json(client.get("/.well-known/agent-card.json"))

    assert isinstance(data, dict)
    assert "name" in data, "missing agent-card key: name"
    assert data["name"], "agent-card key name is empty"

    if "id" in data:
        assert isinstance(data["id"], str)
        assert data["id"].strip()

    if "health_url" in data:
        assert "/health" in str(data["health_url"])

def test_server_root_or_health_available(app_config):
    client = TestClient(_build_app(app_config))

    root_response = client.get("/")
    if root_response.status_code == 200:
        payload = root_response.json()
        assert isinstance(payload, dict)
        return

    health_response = client.get("/health")
    assert health_response.status_code == 200, health_response.text
