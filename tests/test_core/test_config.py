from __future__ import annotations

from aegisforge.config import AppConfig


def test_app_config_from_env_defaults(app_config: AppConfig):
    assert app_config.host == "0.0.0.0"
    assert app_config.port == 8000
    assert app_config.agent_port == 8000
    assert app_config.log_level == "INFO"
    assert app_config.environment == "test"
    assert app_config.public_url == "http://127.0.0.1:8000"


def test_app_config_agent_identity(app_config: AppConfig):
    assert app_config.agent_id == "aegisforge"
    assert app_config.agent_name == "AegisForge"
    assert app_config.agent_version == "0.1.0"
    assert app_config.health_path == "/health"
    assert app_config.agent_card_path == "/.well-known/agent-card.json"


def test_app_config_enabled_integrations_default_empty(app_config: AppConfig):
    assert app_config.enabled_integrations() == []


def test_app_config_urls(app_config: AppConfig):
    assert app_config.health_url() == "http://127.0.0.1:8000/health"
    assert app_config.agent_card_url() == "http://127.0.0.1:8000/.well-known/agent-card.json"


def test_runtime_summary(app_config: AppConfig):
    summary = app_config.runtime_summary().to_dict()
    assert summary["host"] == "0.0.0.0"
    assert summary["port"] == 8000
    assert summary["agent_port"] == 8000
    assert summary["environment"] == "test"
    assert summary["log_level"] == "INFO"
    