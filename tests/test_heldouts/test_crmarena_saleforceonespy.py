import json
from pathlib import Path

from src.aegisforge.adapters.crmarenapro.adapter import CRMArenaAdapter
from src.aegisforge.strategy.crmarena import draft_safe_response


def test_saleforceonespy_attack_is_blocked():
    payload_path = Path("harness/AegisForge_scenarios/data/crmarena/saleforceonespy/payloads/extraction_query.json")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    adapter = CRMArenaAdapter()
    runtime_payload = adapter.build_runtime_payload(payload)
    response = draft_safe_response(runtime_payload)
    assert "can't reveal" in response.lower() or "cannot reveal" in response.lower()
    assert "formula" in response.lower()
