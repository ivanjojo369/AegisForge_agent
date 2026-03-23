from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_prepare_submission_script_exists():
    script = Path("scripts/prepare_submission.py")
    assert script.exists()
    assert script.is_file()


def test_prepare_submission_generates_json(tmp_path):
    output_path = tmp_path / "submission.json"

    cmd = [
        sys.executable,
        "scripts/prepare_submission.py",
        "--submission-name",
        "aegisforge-test",
        "--track",
        "purple",
        "--public-url",
        "http://127.0.0.1:8000",
        "--docker-image",
        "local/aegisforge:test",
        "--output",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert output_path.exists()

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["submission_name"] == "aegisforge-test"
    assert data["track"] == "purple"
    assert data["public_url"] == "http://127.0.0.1:8000"
    assert data["agent_card_url"] == "http://127.0.0.1:8000/.well-known/agent-card.json"
    assert data["docker_image"] == "local/aegisforge:test"
    