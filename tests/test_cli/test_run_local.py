from __future__ import annotations

from pathlib import Path


def test_run_local_script_exists():
    script = Path("scripts/run_local_a2a.sh")
    assert script.exists()
    assert script.is_file()


def test_run_local_script_mentions_docker_run():
    content = Path("scripts/run_local_a2a.sh").read_text(encoding="utf-8")
    assert "docker run" in content
    assert "AEGISFORGE_PUBLIC_URL" in content
    assert "/.well-known/agent-card.json" in content
    