from __future__ import annotations

from pathlib import Path

import pytest

from aegisforge.config import AppConfig
from aegisforge.utils.json_io import load_json


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def agent_card_fixture(fixtures_dir: Path) -> dict:
    return load_json(fixtures_dir / "agent_card_min.json")


@pytest.fixture
def health_fixture(fixtures_dir: Path) -> dict:
    return load_json(fixtures_dir / "health_ok.json")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "HOST",
        "PORT",
        "AGENT_PORT",
        "LOG_LEVEL",
        "ENVIRONMENT",
        "AEGISFORGE_PUBLIC_URL",
        "AEGISFORGE_AGENT_ID",
        "AEGISFORGE_AGENT_NAME",
        "AEGISFORGE_AGENT_VERSION",
        "AEGISFORGE_AGENT_DESCRIPTION",
        "HEALTH_PATH",
        "AGENT_CARD_PATH",
        "AEGISFORGE_ENABLE_OPENENV",
        "AEGISFORGE_ENABLE_TAU2",
        "AEGISFORGE_ENABLE_SECURITY",
        "AEGISFORGE_GIT_SHA",
        "AEGISFORGE_IMAGE_REF",
        "AEGISFORGE_TRACK",
        "OPENENV_BASE_URL",
        "OPENENV_ENVIRONMENT_NAME",
        "OPENENV_TIMEOUT_SECONDS",
        "OPENENV_STRICT_MODE",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def app_config(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setenv("AGENT_PORT", "8000")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AEGISFORGE_PUBLIC_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("AEGISFORGE_AGENT_ID", "aegisforge")
    monkeypatch.setenv("AEGISFORGE_AGENT_NAME", "AegisForge")
    monkeypatch.setenv("AEGISFORGE_AGENT_VERSION", "0.1.0")
    monkeypatch.setenv("AEGISFORGE_AGENT_DESCRIPTION", "Submission-ready A2A purple agent runtime.")
    monkeypatch.setenv("HEALTH_PATH", "/health")
    monkeypatch.setenv("AGENT_CARD_PATH", "/.well-known/agent-card.json")
    monkeypatch.setenv("AEGISFORGE_ENABLE_OPENENV", "false")
    monkeypatch.setenv("AEGISFORGE_ENABLE_TAU2", "false")
    monkeypatch.setenv("AEGISFORGE_ENABLE_SECURITY", "false")
    monkeypatch.setenv("AEGISFORGE_GIT_SHA", "test-sha")
    monkeypatch.setenv("AEGISFORGE_IMAGE_REF", "local/aegisforge:test")
    monkeypatch.setenv("AEGISFORGE_TRACK", "purple")
    return AppConfig.from_env()
