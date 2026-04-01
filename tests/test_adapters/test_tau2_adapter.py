from __future__ import annotations

import pytest

from aegisforge.adapters.tau2 import Tau2Adapter, Tau2AdapterConfig
from aegisforge.adapters.tau2.quipu_lab.tasks import (
    build_sample_task,
    get_smoke_tasks,
    get_task_by_id,
    iter_task_ids,
)


SMOKE_TASK_IDS = iter_task_ids(get_smoke_tasks())


@pytest.fixture
def enabled_adapter() -> Tau2Adapter:
    return Tau2Adapter(
        Tau2AdapterConfig(
            enabled=True,
            base_url="http://127.0.0.1:8020",
            domain_name="quipu_lab",
            timeout_seconds=30,
            strict_mode=False,
        )
    )


@pytest.fixture
def disabled_adapter() -> Tau2Adapter:
    return Tau2Adapter(
        Tau2AdapterConfig(
            enabled=False,
            base_url="http://127.0.0.1:8020",
            domain_name="quipu_lab",
            timeout_seconds=30,
            strict_mode=False,
        )
    )


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


def test_tau2_adapter_status_shape(enabled_adapter: Tau2Adapter) -> None:
    status = enabled_adapter.status()

    assert status["provider"] == "tau2"
    assert status["enabled"] is True
    assert status["base_url"] == "http://127.0.0.1:8020"
    assert status["domain_name"] == "quipu_lab"
    assert status["timeout_seconds"] == 30
    assert status["strict_mode"] is False


@pytest.mark.parametrize("task_id", SMOKE_TASK_IDS)
def test_tau2_validate_request_normalizes_catalog_task(
    enabled_adapter: Tau2Adapter, task_id: str
) -> None:
    task = get_task_by_id(task_id).to_dict()
    normalized = enabled_adapter.validate_request({"task": task})

    assert normalized["provider"] == "tau2"
    assert normalized["domain_name"] == "quipu_lab"
    assert normalized["task"]["task_id"] == task_id
    assert normalized["turns"] == task["conversation_context"]
    assert normalized["tools"] == task["required_tools"]
    assert len(normalized["turns"]) >= 2


def test_tau2_validate_request_fills_missing_task_id_from_sample(
    enabled_adapter: Tau2Adapter,
) -> None:
    sample = build_sample_task()
    normalized = enabled_adapter.validate_request(
        {
            "task": {
                "title": "Overridden sample title",
                "user_goal": sample.user_goal,
                "conversation_context": sample.conversation_context,
                "required_tools": sample.required_tools,
                "success_criteria": sample.success_criteria,
                "constraints": sample.constraints,
                "metadata": sample.metadata,
            }
        }
    )

    assert normalized["task"]["task_id"] == sample.task_id
    assert normalized["task"]["title"] == "Overridden sample title"
    assert normalized["tools"] == sample.required_tools


def test_tau2_validate_request_rejects_invalid_quipu_lab_task(
    enabled_adapter: Tau2Adapter,
) -> None:
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
        enabled_adapter.validate_request(bad_request)


def test_tau2_translate_request_includes_policy_excerpt(
    enabled_adapter: Tau2Adapter,
) -> None:
    task = get_task_by_id("quipu_lab_tau2_airline_policy_clarification").to_dict()
    translated = enabled_adapter.translate_request({"task": task})

    assert translated["provider"] == "tau2"
    assert translated["base_url"] == "http://127.0.0.1:8020"
    assert translated["domain_name"] == "quipu_lab"
    assert translated["timeout_seconds"] == 30
    assert translated["strict_mode"] is False
    assert translated["task"]["task_id"] == task["task_id"]
    assert translated["policy_excerpt"]["domain"] == "quipu_lab"
    assert len(translated["policy_excerpt"]["rules"]) >= 1


def test_tau2_execute_returns_error_when_disabled(
    disabled_adapter: Tau2Adapter,
) -> None:
    result = disabled_adapter.execute({"task": build_sample_task().to_dict()})

    assert result.ok is False
    assert result.provider == "tau2"
    assert result.domain_name == "quipu_lab"
    assert result.error == "Tau2 adapter is disabled via configuration."
    assert result.payload["ok"] is False


@pytest.mark.parametrize("task_id", SMOKE_TASK_IDS)
def test_tau2_execute_quipu_lab_returns_structured_result(
    enabled_adapter: Tau2Adapter, task_id: str
) -> None:
    task = get_task_by_id(task_id)
    result = enabled_adapter.execute({"task": task.to_dict()})

    assert result.ok is True
    assert result.provider == "tau2"
    assert result.domain_name == "quipu_lab"

    payload = result.payload
    assert payload["ok"] is True
    assert payload["domain_name"] == "quipu_lab"
    assert payload["task"]["task_id"] == task.task_id

    structured_result = payload["result"]
    assert structured_result["task_id"] == task.task_id
    assert structured_result["status"] == "ok"
    assert "structured response" in structured_result["summary"]
    assert structured_result["used_tools"] == task.required_tools

    assert isinstance(payload["tool_results"], list)
    assert len(payload["tool_results"]) == len(task.required_tools)
    assert all(tool_result["ok"] is True for tool_result in payload["tool_results"])

    assert payload["policy_excerpt"]["domain"] == "quipu_lab"

    assert isinstance(payload["trace"], list)
    assert len(payload["trace"]) == 3
    assert [event["event_type"] for event in payload["trace"]] == [
        "task_loaded",
        "tools_executed",
        "result_built",
    ]


def test_tau2_execute_non_quipu_lab_domain_echoes_input(
    enabled_adapter: Tau2Adapter,
) -> None:
    result = enabled_adapter.execute(
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
