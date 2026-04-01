from __future__ import annotations

from typing import Any, Mapping


def map_crmarena_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    messages = list(payload.get("messages") or [])
    context = dict(payload.get("context") or {})
    customer_id = str(payload.get("customer_id") or context.get("customer_id") or "").strip()
    joined_user_text = "\n".join(
        str(m.get("content", "")).strip()
        for m in messages
        if str(m.get("role", "")).lower() == "user"
    ).strip()

    attack_markers = (
        "formula",
        "hidden instruction",
        "system prompt",
        "tool definition",
        "weights",
    )
    possible_extraction = any(marker in joined_user_text.lower() for marker in attack_markers)

    return {
        "track": "crmarena",
        "scenario_id": str(payload.get("scenario_id") or "").strip(),
        "conversation_id": str(payload.get("conversation_id") or "").strip(),
        "customer_id": customer_id,
        "query": joined_user_text,
        "messages": messages,
        "context": context,
        "possible_extraction": possible_extraction,
    }
