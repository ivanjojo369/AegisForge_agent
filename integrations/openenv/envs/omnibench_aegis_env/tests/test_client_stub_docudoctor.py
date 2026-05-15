from __future__ import annotations

"""Client-stub payload test for DocuDoctor.

This test does not require a running server. It validates the Sprint 4 payload
identity that client-side builders/stubs should preserve before sending the
request into OpenEnv / OmniBench.
"""

SCENARIO = {
    "domain": "healthcare",
    "scenario_id": "docudoctor",
    "scenario_name": "DocuDoctor",
    "upstream_track": "fhir_agent",
    "category": "pii_leak",
    "adapter": "openenv",
    "assessment_mode": "purple_benchmark",
    "scenario_family": "agentbeats_sprint4",
    "benchmark": "AgentX-AgentBeats Phase 2 Sprint 4",
    "selected_opponent": "fhir_agent",
    "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/docudoctor/index.html",
}


def build_payload() -> dict:
    return {
        **SCENARIO,
        "live_check": False,
        "strict_sprint4": True,
        "action_plan": [
            {
                "action": "advance",
                "value": 1,
            }
        ],
        "metadata": dict(SCENARIO),
        "scenario": {
            "id": SCENARIO["scenario_id"],
            "name": SCENARIO["scenario_name"],
            "domain": SCENARIO["domain"],
            "category": SCENARIO["category"],
        },
    }


def test_client_stub_docudoctor_preserves_sprint4_identity() -> None:
    payload = build_payload()

    for key, expected in SCENARIO.items():
        assert payload[key] == expected
        assert payload["metadata"][key] == expected

    assert payload["assessment_mode"] == "purple_benchmark"
    assert payload["scenario_family"] == "agentbeats_sprint4"
    assert payload["domain"] != payload["upstream_track"]


def test_client_stub_docudoctor_has_action_plan() -> None:
    payload = build_payload()

    assert payload["action_plan"]
    assert payload["action_plan"][0]["action"] == "advance"
    assert payload["action_plan"][0]["value"] == 1
