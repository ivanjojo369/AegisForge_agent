from __future__ import annotations

from aegisforge.adapters.security import SecurityAdapter, SecurityAdapterConfig


def test_security_adapter_status_defaults(clean_env, monkeypatch):
    monkeypatch.setenv("AEGISFORGE_ENABLE_SECURITY", "true")
    adapter = SecurityAdapter()
    status = adapter.status()

    assert status["provider"] == "security_arena"
    assert status["enabled"] is True
    assert status["role"] == "defender"


def test_security_adapter_translate_request(clean_env, monkeypatch):
    monkeypatch.setenv("AEGISFORGE_ENABLE_SECURITY", "true")
    monkeypatch.setenv("SECURITY_ROLE", "attacker")
    monkeypatch.setenv("SECURITY_SCENARIO_NAME", "lambda-arena")
    adapter = SecurityAdapter()

    translated = adapter.translate_request({"task": "attack"})
    assert translated["provider"] == "security_arena"
    assert translated["role"] == "attacker"
    assert translated["scenario_name"] == "lambda-arena"
    assert translated["input"]["task"] == "attack"


def test_security_adapter_execute_returns_stable_shape():
    config = SecurityAdapterConfig(
        enabled=True,
        role="defender",
        scenario_name="lambda-arena",
        timeout_seconds=30,
        strict_mode=False,
    )
    adapter = SecurityAdapter(config=config)

    result = adapter.execute({"task": "defend"}).to_dict()
    assert result["ok"] is True
    assert result["provider"] == "security_arena"
    assert result["role"] == "defender"
    assert result["scenario_name"] == "lambda-arena"
    assert result["payload"]["echo"]["task"] == "defend"
    