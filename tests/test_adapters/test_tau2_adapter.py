from __future__ import annotations

import pytest

from aegisforge.adapters.tau2 import Tau2Adapter, Tau2AdapterConfig


def _build_quipu_lab_task() -> dict[str, object]:
    return {
        "task_id": "quipu_lab_test_task_001",
        "title": "Validate quipu_lab tau2 adapter",
        "user_goal": (
            "Confirm that the tau2 adapter can normalize and execute a "
            "quipu_lab task in a structured way."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": "Please validate the adapter with a minimal structured task.",
            }
        ],
        "required_tools": [
            "list_available_assets",
            "draft_structured_plan",
        ],
        "success_criteria": [
            "task is normalized",
            "tools are executed",
            "result shape is structured",
        ],
        "constraints": [
            "do not invent extra tools",
            "keep the output concise",
        ],
        "metadata": {
            "domain": "quipu_lab",
            "mode": "tau2-style",
            "split": "base",
        },
    }


def test_tau2_config_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEGISFORGE_ENABLE_TAU2", raising=False)
    monkeypatch.delenv("TAU2_BASE_URL", raising=False)
    monkeypatch.delenv("TAU2_DOMAIN_NAME", raising=False)
    monkeypatch.delenv("TAU2_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TAU2_STRICT_MODE", raising=False)

    config = Tau2AdapterConfig.from_env()

    assert config.enabled is False
    assert config.base_url == "http://127.0.0.1:8020"
    assert config.domain_name == "quipu_lab"
    assert config.timeout_seconds == 30
    assert config.strict_mode is False
    assert config.provider_name == "tau2"


def test_tau2_config_from_env_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEGISFORGE_ENABLE_TAU2", "true")
    monkeypatch.setenv("TAU2_BASE_URL", "http://localhost:9123")
    monkeypatch.setenv("TAU2_DOMAIN_NAME", "quipu_lab")
    monkeypatch.setenv("TAU2_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("TAU2_STRICT_MODE", "true")

    config = Tau2AdapterConfig.from_env()

    assert config.enabled is True
    assert config.base_url == "http://localhost:9123"
    assert config.domain_name == "quipu_lab"
    assert config.timeout_seconds == 45
    assert config.strict_mode is True
    assert config.to_dict()["provider_name"] == "tau2"


def test_tau2_adapter_status_shape() -> None:
    config = Tau2AdapterConfig(
        enabled=True,
        base_url="http://127.0.0.1:8020",
        domain_name="quipu_lab",
        timeout_seconds=30,
        strict_mode=False,
    )
    adapter = Tau2Adapter(config)

    status = adapter.status()

    assert status["provider"] == "tau2"
    assert status["enabled"] is True
    assert status["base_url"] == "http://127.0.0.1:8020"
    assert status["domain_name"] == "quipu_lab"
    assert status["timeout_seconds"] == 30
    assert status["strict_mode"] is False


def test_tau2_validate_request_normalizes_quipu_lab_task() -> None:
    config = Tau2AdapterConfig(
        enabled=True,
        base_url="http://127.0.0.1:8020",
        domain_name="quipu_lab",
        timeout_seconds=30,
        strict_mode=False,
    )
    adapter = Tau2Adapter(config)

    request = {
        "task": _build_quipu_lab_task(),
    }

    normalized = adapter.validate_request(request)

    assert normalized["provider"] == "tau2"
    assert normalized["domain_name"] == "quipu_lab"
    assert isinstance(normalized["task"], dict)
    assert normalized["task"]["task_id"] == "quipu_lab_test_task_001"
    assert isinstance(normalized["turns"], list)
    assert len(normalized["turns"]) == 1
    assert normalized["tools"] == [
        "list_available_assets",
        "draft_structured_plan",
    ]


def test_tau2_validate_request_rejects_invalid_quipu_lab_task() -> None:
    config = Tau2AdapterConfig(
        enabled=True,
        base_url="http://127.0.0.1:8020",
        domain_name="quipu_lab",
        timeout_seconds=30,
        strict_mode=False,
    )
    adapter = Tau2Adapter(config)

    bad_request = {
        "task": {
            "task_id": "bad_task",
            "title": "",
            "user_goal": "",
            "conversation_context": "not-a-list",
            "required_tools": "not-a-list",
            "success_criteria": [],
            "constraints": [],
            "metadata": {},
        }
    }

    with pytest.raises(ValueError, match="invalid"):
        adapter.validate_request(bad_request)


def test_tau2_translate_request_includes_policy_excerpt() -> None:
    config = Tau2AdapterConfig(
        enabled=True,
        base_url="http://127.0.0.1:8020",
        domain_name="quipu_lab",
        timeout_seconds=30,
        strict_mode=False,
    )
    adapter = Tau2Adapter(config)

    translated = adapter.translate_request({"task": _build_quipu_lab_task()})

    assert translated["provider"] == "tau2"
    assert translated["base_url"] == "http://127.0.0.1:8020"
    assert translated["domain_name"] == "quipu_lab"
    assert translated["timeout_seconds"] == 30
    assert translated["strict_mode"] is False
    assert "task" in translated
    assert "policy_excerpt" in translated
    assert translated["policy_excerpt"]["domain"] == "quipu_lab"
    assert isinstance(translated["policy_excerpt"]["rules"], list)
    assert len(translated["policy_excerpt"]["rules"]) >= 1


def test_tau2_execute_returns_error_when_disabled() -> None:
    config = Tau2AdapterConfig(
        enabled=False,
        base_url="http://127.0.0.1:8020",
        domain_name="quipu_lab",
        timeout_seconds=30,
        strict_mode=False,
    )
    adapter = Tau2Adapter(config)

    result = adapter.execute({"task": _build_quipu_lab_task()})

    assert result.ok is False
    assert result.provider == "tau2"
    assert result.domain_name == "quipu_lab"
    assert result.error == "Tau2 adapter is disabled via configuration."
    assert result.payload["ok"] is False


def test_tau2_execute_quipu_lab_returns_structured_result() -> None:
    config = Tau2AdapterConfig(
        enabled=True,
        base_url="http://127.0.0.1:8020",
        domain_name="quipu_lab",
        timeout_seconds=30,
        strict_mode=False,
    )
    adapter = Tau2Adapter(config)

    result = adapter.execute({"task": _build_quipu_lab_task()})

    assert result.ok is True
    assert result.provider == "tau2"
    assert result.domain_name == "quipu_lab"

    payload = result.payload
    assert payload["ok"] is True
    assert payload["domain_name"] == "quipu_lab"
    assert payload["task"]["task_id"] == "quipu_lab_test_task_001"

    structured_result = payload["result"]
    assert structured_result["task_id"] == "quipu_lab_test_task_001"
    assert structured_result["status"] == "ok"
    assert "structured response" in structured_result["summary"]
    assert structured_result["used_tools"] == [
        "list_available_assets",
        "draft_structured_plan",
    ]

    assert isinstance(payload["tool_results"], list)
    assert len(payload["tool_results"]) == 2
    assert payload["tool_results"][0]["ok"] is True

    assert "policy_excerpt" in payload
    assert payload["policy_excerpt"]["domain"] == "quipu_lab"

    assert isinstance(payload["trace"], list)
    assert len(payload["trace"]) == 3
    assert payload["trace"][0]["event_type"] == "task_loaded"


def test_tau2_execute_non_quipu_lab_domain_echoes_input() -> None:
    config = Tau2AdapterConfig(
        enabled=True,
        base_url="http://127.0.0.1:8020",
        domain_name="quipu_lab",
        timeout_seconds=30,
        strict_mode=False,
    )
    adapter = Tau2Adapter(config)

    result = adapter.execute(
        {
            "domain_name": "telecom_like_demo",
            "task": "probe",
            "turns": [{"role": "user", "content": "hello"}],
            "tools": ["dummy_tool"],
        }
    )

    assert result.ok is True
    assert result.provider == "tau2"
    assert result.domain_name == "telecom_like_demo"
    assert result.payload["echo"]["domain_name"] == "telecom_like_demo"
    assert result.payload["echo"]["task"] == "probe"
    