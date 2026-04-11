import json
from pathlib import Path

from src.aegisforge.adapters.crmarenapro.payload_mapper import map_crmarena_payload
from src.aegisforge.strategy.crmarena import build_crmarena_policy


def test_playbook_maps_attack_payload_and_sets_high_security():
    payload_path = Path("harness/AegisForge_scenarios/data/crmarena/saleforceonespy/payloads/extraction_query.json")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    mapped = map_crmarena_payload(payload)
    policy = build_crmarena_policy(mapped)
    assert mapped["possible_extraction"] is True
    assert policy["security_posture"] == "high"
