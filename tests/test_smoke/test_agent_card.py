from __future__ import annotations

from aegisforge.agent_card import agent_card_response_dict


def test_agent_card_has_expected_keys(app_config):
    data = agent_card_response_dict(app_config)

    assert isinstance(data, dict)
    assert data["id"] == "aegisforge"
    assert data["name"] == "AegisForge"
    assert data["version"] == "0.1.0"
    assert data["url"] == "http://127.0.0.1:8000"
    assert data["health_url"] == "http://127.0.0.1:8000/health"
    assert "capabilities" in data
    assert "metadata" in data


def test_agent_card_capabilities_include_base_runtime_flags(app_config):
    data = agent_card_response_dict(app_config)
    capabilities = data["capabilities"]

    assert "a2a" in capabilities
    assert "judge-friendly" in capabilities
    assert "fresh-state" in capabilities
    assert "purple-agent" in capabilities


def test_agent_card_tracks_default_to_purple(app_config):
    data = agent_card_response_dict(app_config)
    assert "purple" in data["tracks"]


def test_agent_card_fixture_shape(agent_card_fixture):
    assert agent_card_fixture["id"] == "aegisforge"
    assert agent_card_fixture["name"] == "AegisForge"
    assert agent_card_fixture["url"] == "http://127.0.0.1:8000"
    assert agent_card_fixture["health_url"].endswith("/health")
    