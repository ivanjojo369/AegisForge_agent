from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "security_arena" / "NetArena" / "devcontainerdoom"


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _load_json(name: str) -> dict:
    return json.loads(_read(name))


def test_devcontainerdoom_fixture_contains_container_supply_chain_risks():
    dockerfile = _read("Dockerfile.sample")
    devcontainer = _load_json("devcontainer.json")
    k8s = _read("k8s_manifest_snippet.yaml")
    verdict = _load_json("expected_verdict.json")

    combined = "\n".join([dockerfile, json.dumps(devcontainer), k8s]).lower()

    assert verdict["category"] == "supply_chain"
    assert "unverified-registry.example.invalid" in combined
    assert ":latest" in combined
    assert "curl" in combined and "| sh" in combined
    assert "/var/run/docker.sock" in combined
    assert "privileged" in combined


def test_devcontainerdoom_forbidden_patterns_match_fixture():
    combined = "\n".join(
        [
            _read("Dockerfile.sample"),
            _read("environment_mininet.yml"),
            _read("k8s_manifest_snippet.yaml"),
            json.dumps(_load_json("devcontainer.json")),
        ]
    )
    forbidden = _load_json("forbidden_container_patterns.json")

    for pattern in forbidden["forbidden_patterns"]:
        assert re.search(pattern, combined, flags=re.IGNORECASE), pattern
