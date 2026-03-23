from __future__ import annotations

from pathlib import Path


def test_check_e2e_script_exists():
    script = Path("scripts/check_a2a_e2e.sh")
    assert script.exists()
    assert script.is_file()


def test_check_e2e_script_has_health_and_card_checks():
    content = Path("scripts/check_a2a_e2e.sh").read_text(encoding="utf-8")
    assert "/health" in content
    assert ".well-known/agent-card.json" in content
    assert "docker build" in content
    assert "E2E OK" in content
    