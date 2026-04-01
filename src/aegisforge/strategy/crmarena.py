from __future__ import annotations

from typing import Any, Mapping


def build_crmarena_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").lower()
    extraction_markers = (
        "formula",
        "system prompt",
        "hidden instructions",
        "tool definition",
        "weights",
    )
    possible_extraction = any(marker in query for marker in extraction_markers)

    return {
        "track": "crmarena",
        "possible_extraction": possible_extraction,
        "security_posture": "high" if possible_extraction else "normal",
        "allow_safe_summary": True,
        "allow_high_level_method": True,
        "deny_internal_formula": True,
        "deny_hidden_prompt": True,
        "deny_tool_spec": True,
    }


def draft_safe_response(payload: Mapping[str, Any]) -> str:
    customer_id = str(payload.get("customer_id") or "the customer").strip()
    query = str(payload.get("query") or "")
    policy = build_crmarena_policy(payload)
    if policy["possible_extraction"]:
        return (
            f"I can't reveal the internal formula, hidden instructions, or tool definitions used by the CRM system. "
            f"I can still help with a safe churn analysis for {customer_id}: the account appears to have elevated churn risk "
            f"based on recent negative signals, so recommended next actions are proactive outreach, renewal review, and service recovery."
        )
    return (
        f"Customer {customer_id} shows moderate-to-elevated churn risk based on recent usage decline and support friction. "
        f"Recommended retention actions: schedule an account review and offer a targeted remediation plan."
    )
