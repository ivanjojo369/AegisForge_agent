from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .payload_mapper import map_taxwiztrap_payload


@dataclass(slots=True)
class OfficeQAAdapter:
    """Small adapter that normalizes OfficeQA/TaxWizTrap inputs for AegisForge."""

    name: str = "officeqa"

    def supports(self, payload: Mapping[str, Any] | None) -> bool:
        if not payload:
            return False
        track = str(payload.get("track") or payload.get("adapter") or payload.get("scenario_id") or "").lower()
        return "officeqa" in track or "taxwiztrap" in track

    def normalize(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return map_taxwiztrap_payload(payload)

    def build_runtime_context(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self.normalize(payload)
        flags = dict(normalized.get("security_flags") or {})
        return {
            "track": self.name,
            "scenario_id": normalized["scenario_id"],
            "variant": normalized["variant"],
            "document_id": normalized["document"]["id"],
            "contains_embedded_instruction": bool(flags.get("contains_embedded_instruction")),
        }
