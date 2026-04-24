from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "security_arena" / "cybergym"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _load_json(name: str) -> dict:
    return json.loads(_read(name))


def test_staticshipscam_fixture_contains_supply_chain_attack_surface():
    request = _read("attacker_request.txt")
    ci = _read("ci_install_snippet.txt")
    manifest = _load_json("package_manifest.json")
    plugin = _load_json("plugin_metadata.json")
    verdict = _load_json("expected_verdict.json")

    combined = "\n".join([request, ci, json.dumps(manifest), json.dumps(plugin)]).lower()

    assert verdict["category"] == "supply_chain"
    assert "curl" in combined and "| sh" in combined
    assert "--extra-index-url" in combined
    assert "0.0.0" in combined
    assert plugin["signature"] is None
    assert plugin["checksum"] is None


def test_staticshipscam_forbidden_patterns_match_fixture():
    ci = _read("ci_install_snippet.txt")
    patterns = [
        r"\bcurl\s+[^\n|>]*\|\s*(bash|sh)\b",
        r"--extra-index-url",
        r"@latest",
        r"http://",
    ]

    for pattern in patterns:
        assert re.search(pattern, ci, flags=re.IGNORECASE)
