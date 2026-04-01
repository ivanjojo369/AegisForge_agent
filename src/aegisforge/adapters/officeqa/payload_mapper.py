from __future__ import annotations

from typing import Any, Mapping


def map_taxwiztrap_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a TaxWizTrap payload into the internal OfficeQA request shape."""
    metadata = dict(payload.get("metadata") or {})
    document_text = str(payload.get("document_text") or "").strip()
    contains_override = bool(metadata.get("contains_embedded_instruction")) or "ignore previous tax rules" in document_text.lower()

    return {
        "track": "officeqa",
        "scenario_id": str(payload.get("scenario_id") or "officeqa.taxwiztrap.v1"),
        "variant": str(payload.get("variant") or "unknown"),
        "question": str(payload.get("question") or "").strip(),
        "document": {
            "id": str(payload.get("document_id") or ""),
            "text": document_text,
            "type": metadata.get("document_type", "unknown"),
        },
        "expected_risk_label": str(payload.get("expected_risk_label") or "").strip().lower(),
        "security_flags": {
            "contains_embedded_instruction": contains_override,
        },
    }
