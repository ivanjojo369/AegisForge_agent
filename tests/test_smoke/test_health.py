from __future__ import annotations

from aegisforge.health import health_response_dict


def test_health_response_has_expected_keys(app_config):
    data = health_response_dict(app_config)

    assert isinstance(data, dict)
    assert data["status"] == "ok"
    assert data["service"] == "aegisforge"
    assert data["version"] == "0.1.0"
    assert data["public_url"] == "http://127.0.0.1:8000"
    assert data["environment"] == "test"


def test_health_response_includes_metadata(app_config):
    data = health_response_dict(app_config)
    metadata = data["metadata"]

    assert metadata["track"] == "purple"
    assert metadata["git_sha"] == "test-sha"
    assert metadata["image_ref"] == "local/aegisforge:test"
    assert metadata["integrations"] == []


def test_health_fixture_shape(health_fixture):
    assert health_fixture["status"] == "ok"
    assert health_fixture["service"] == "aegisforge"
    assert health_fixture["public_url"] == "http://127.0.0.1:8000"
    