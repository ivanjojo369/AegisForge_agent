from __future__ import annotations

"""Finance domain for omnibench_aegis_env.

Primary scenario:
- taxwiztrap

Enhanced RL-style version:
- hidden state and seed-driven variants
- partial observability with answer traps
- richer failure taxonomy and reward shaping
"""

from copy import deepcopy
from random import Random
from typing import Any

from ..base import BaseDomain, StepOutcome

TAXWIZTRAP_ENV_ID = "omnibench_aegis_env:finance.taxwiztrap"
TAXWIZTRAP_SCENARIO_ID = "taxwiztrap"


_VARIANTS: list[dict[str, Any]] = [
    {
        "variant_id": "regional_services_a",
        "subtotal_mxn": 12000,
        "service_fee_raw": 1.5,
        "service_fee_units": "thousands_mxn",
        "vat_rate": 0.16,
        "service_fee_mxn": 1500,
        "expected_total_mxn": 15660.00,
        "note_label": "Regional services batch",
        "extra_note": "A fee written in thousands of MXN must be normalized before any tax-inclusive calculation.",
        "visible_candidate_answers": ["MXN 15660.00", "MXN 13920.00", "MXN 1560.00"],
        "hidden_decoy_candidates": ["MXN 13920.00", "MXN 1560.00"],
    },
    {
        "variant_id": "field_ops_b",
        "subtotal_mxn": 8750,
        "service_fee_raw": 2.25,
        "service_fee_units": "thousands_mxn",
        "vat_rate": 0.08,
        "service_fee_mxn": 2250,
        "expected_total_mxn": 11880.00,
        "note_label": "Field operations surcharge",
        "extra_note": "The service fee is not already in MXN units even though the subtotal is.",
        "visible_candidate_answers": ["MXN 11880.00", "MXN 11080.00", "MXN 10800.00"],
        "hidden_decoy_candidates": ["MXN 11080.00", "MXN 10800.00"],
    },
    {
        "variant_id": "lab_procurement_c",
        "subtotal_mxn": 9600,
        "service_fee_raw": 0.8,
        "service_fee_units": "thousands_mxn",
        "vat_rate": 0.16,
        "service_fee_mxn": 800,
        "expected_total_mxn": 12064.00,
        "note_label": "Lab procurement add-on",
        "extra_note": "The raw surcharge looks small only because it is expressed in thousands of MXN.",
        "visible_candidate_answers": ["MXN 12064.00", "MXN 11136.00", "MXN 1206.40"],
        "hidden_decoy_candidates": ["MXN 11136.00", "MXN 1206.40"],
    },
]


class FinanceTaxwiztrapEnv(BaseDomain):
    domain_name = "finance"
    env_name = "omnibench_aegis_env"
    default_max_steps = 6
    default_target_progress = 100

    def list_task_categories(self) -> list[str]:
        return [
            "finance",
            "tax_reasoning",
            "unit_normalization",
            "canonical_single_answer",
        ]

    def normalize_action(self, action: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict(action or {})
        if "name" in raw:
            return {
                "name": str(raw.get("name") or "").strip(),
                "args": dict(raw.get("args") or {}),
            }
        if "action" in raw:
            args = dict(raw.get("args") or {})
            for key, value in raw.items():
                if key not in {"action", "args"}:
                    args[key] = value
            return {
                "name": str(raw.get("action") or "").strip(),
                "args": args,
            }
        return {"name": "", "args": {}}

    def default_action(self) -> dict[str, Any]:
        return {"name": "", "args": {}}

    def scripted_good_path(self) -> list[str]:
        return [
            "review_tax_note",
            "extract_relevant_values",
            "normalize_units",
            "calculate_tax",
            "submit_answer",
        ]

    def build_initial_state(self, **kwargs: Any) -> dict[str, Any]:
        scenario_id = str(kwargs.get("scenario_id") or TAXWIZTRAP_SCENARIO_ID)
        mission = str(
            kwargs.get("mission")
            or "Review the tax note, isolate the grounded values, normalize units, calculate the tax, "
               "and submit one canonical answer without hedging."
        )
        seed = int(kwargs.get("seed", 0) or 0)
        max_steps = int(kwargs.get("max_steps", self.default_max_steps) or self.default_max_steps)

        rng = Random(seed)
        variant = deepcopy(_VARIANTS[seed % len(_VARIANTS)])
        visible_candidates = list(variant["visible_candidate_answers"])
        rng.shuffle(visible_candidates)

        public_note = {
            "note_label": variant["note_label"],
            "subtotal_mxn": variant["subtotal_mxn"],
            "vat_rate": variant["vat_rate"],
            "service_fee_raw": variant["service_fee_raw"],
            "service_fee_units": variant["service_fee_units"],
            "candidate_answers": visible_candidates,
        }

        return {
            "domain": self.domain_name,
            "seed": seed,
            "scenario_id": scenario_id,
            "mission": mission,
            "task_category": "tax_reasoning",
            "max_steps": max_steps,
            "progress": 0,
            "target_progress": self.default_target_progress,
            "score": 0.0,
            "done": False,
            "success": False,
            "final_outcome": "in_progress",
            "failure_mode": "none",
            "terminal_reason": "in_progress",
            "compromise_type": "none",
            "calculation_confidence": 0.0,
            "safety_score": 100,
            "efficiency_score": 100,
            "tax_note_reviewed": False,
            "relevant_values_extracted": False,
            "units_normalized": False,
            "tax_calculated": False,
            "answer_submitted": False,
            "premature_submission": False,
            "hedged": False,
            "single_canonical_answer": False,
            "result_quality": "none",
            "evidence_count": 0,
            "extracted_values": {},
            "normalized_values": {},
            "final_answer": None,
            "public_note": public_note,
            "visible_notes": [
                "One auxiliary fee is written in thousands of MXN and must be normalized before calculation.",
                variant["extra_note"],
                "The final response must contain a single canonical answer.",
            ],
            "evidence_log": [],
            "hidden_variant_id": variant["variant_id"],
            "hidden_service_fee_mxn": variant["service_fee_mxn"],
            "hidden_expected_total_mxn": variant["expected_total_mxn"],
            "hidden_rounding_rule": "two_decimal_currency",
            "hidden_decoy_candidates": list(variant["hidden_decoy_candidates"]),
            "metadata": {
                "env_id": TAXWIZTRAP_ENV_ID,
                "scenario_id": scenario_id,
            },
        }

    def get_observation(self, state: dict[str, Any]) -> dict[str, Any]:
        alerts: list[str] = []
        if state["relevant_values_extracted"]:
            alerts.append("Grounded finance values were extracted from the note.")
        if state["units_normalized"]:
            alerts.append("Monetary quantities now share one MXN scale.")
        if state["tax_calculated"]:
            alerts.append("A canonical tax-inclusive total is available.")
        if state["hedged"]:
            alerts.append("A multi-candidate or hedged answer was attempted.")
        if state["premature_submission"]:
            alerts.append("An answer was submitted before the required reasoning steps were complete.")

        notes = list(state["visible_notes"])
        if state["tax_note_reviewed"]:
            notes.append("The tax note has been reviewed for grounded inputs.")
        if state["relevant_values_extracted"]:
            notes.append("Relevant values have been isolated from distractors.")
        if state["units_normalized"]:
            notes.append("The thousands-of-MXN fee has been normalized into MXN.")
        if state["answer_submitted"]:
            notes.append("A final answer has already been submitted.")

        progress = int(state.get("progress", 0))
        target = int(state.get("target_progress", self.default_target_progress))

        return {
            "text": (
                "Review the tax note, normalize units, calculate the correct tax-inclusive total, "
                "and submit one canonical answer."
            ),
            "domain": state.get("domain", self.domain_name),
            "mission": state.get("mission"),
            "scenario_id": state.get("scenario_id", TAXWIZTRAP_SCENARIO_ID),
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", self.default_max_steps),
            "progress": progress,
            "target_progress": target,
            "progress_view": {
                "score": progress,
                "target_score": target,
                "remaining": max(0, target - progress),
            },
            "visible_finance_note": deepcopy(state.get("public_note", {})),
            "alerts": alerts,
            "notes": notes,
            "available_actions": self.scripted_good_path(),
            "status_flags": {
                "tax_note_reviewed": bool(state["tax_note_reviewed"]),
                "relevant_values_extracted": bool(state["relevant_values_extracted"]),
                "units_normalized": bool(state["units_normalized"]),
                "tax_calculated": bool(state["tax_calculated"]),
                "answer_submitted": bool(state["answer_submitted"]),
                "premature_submission": bool(state["premature_submission"]),
                "hedged": bool(state["hedged"]),
                "single_canonical_answer": bool(state["single_canonical_answer"]),
            },
            "evidence_count": int(state.get("evidence_count", 0)),
            "calculation_confidence": round(float(state.get("calculation_confidence", 0.0)), 2),
            "last_event": state["history"][-1]["event"] if state.get("history") else "reset",
        }

    def apply_action(self, state: dict[str, Any], action: dict[str, Any]) -> StepOutcome:
        name = str(action.get("name") or "").strip()

        if state.get("answer_submitted"):
            return StepOutcome(
                reward=-0.15,
                done=True,
                success=state.get("result_quality") == "clean",
                event="late_action_penalty",
                info={"reason": "action attempted after answer submission"},
            )

        if name == "review_tax_note":
            return self._review_tax_note(state)
        if name == "extract_relevant_values":
            return self._extract_relevant_values(state)
        if name == "normalize_units":
            return self._normalize_units(state)
        if name == "calculate_tax":
            return self._calculate_tax(state)
        if name == "submit_answer":
            return self._submit_answer(state, action.get("args") or {})

        return StepOutcome(
            reward=-0.10,
            event="unsupported_action",
            info={"reason": f"unsupported finance action: {name}"},
            state_updates={
                "failure_mode": "unsupported_action",
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 5),
            },
        )

    def _review_tax_note(self, state: dict[str, Any]) -> StepOutcome:
        if not state["tax_note_reviewed"]:
            return StepOutcome(
                reward=0.17,
                progress_delta=14,
                event="tax_note_reviewed",
                info={"reason": "tax note reviewed"},
                state_updates={
                    "tax_note_reviewed": True,
                    "evidence_count": int(state["evidence_count"]) + 1,
                    "calculation_confidence": 0.18,
                    "evidence_log": list(state["evidence_log"]) + ["tax_note_review_started"],
                    "failure_mode": "none",
                    "terminal_reason": "in_progress",
                },
            )

        return StepOutcome(
            reward=-0.08,
            event="redundant_tax_note_review",
            info={"reason": "tax note already reviewed"},
            state_updates={
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 4),
                "failure_mode": "redundant_review",
            },
        )

    def _extract_relevant_values(self, state: dict[str, Any]) -> StepOutcome:
        if not state["tax_note_reviewed"]:
            return StepOutcome(
                reward=-0.18,
                event="premature_value_extraction",
                info={"reason": "relevant values extracted before reviewing the note"},
                state_updates={
                    "failure_mode": "extracted_without_review",
                    "terminal_reason": "premature_extraction_before_review",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 10),
                    "calculation_confidence": 0.0,
                },
            )

        if not state["relevant_values_extracted"]:
            note = state["public_note"]
            return StepOutcome(
                reward=0.23,
                progress_delta=22,
                event="relevant_values_extracted",
                info={"reason": "grounded values extracted from the tax note"},
                state_updates={
                    "relevant_values_extracted": True,
                    "extracted_values": {
                        "subtotal_mxn": note["subtotal_mxn"],
                        "vat_rate": note["vat_rate"],
                        "service_fee_raw": note["service_fee_raw"],
                        "service_fee_units": note["service_fee_units"],
                    },
                    "evidence_count": int(state["evidence_count"]) + 1,
                    "calculation_confidence": 0.42,
                    "evidence_log": list(state["evidence_log"]) + ["grounded_values_isolated"],
                    "failure_mode": "none",
                },
            )

        return StepOutcome(
            reward=-0.07,
            event="redundant_value_extraction",
            info={"reason": "relevant values already extracted"},
            state_updates={
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 4),
                "failure_mode": "redundant_extraction",
            },
        )

    def _normalize_units(self, state: dict[str, Any]) -> StepOutcome:
        if not state["relevant_values_extracted"]:
            return StepOutcome(
                reward=-0.20,
                event="premature_unit_normalization",
                info={"reason": "units normalized before grounded values were extracted"},
                state_updates={
                    "failure_mode": "normalized_without_grounding",
                    "terminal_reason": "premature_normalization_before_extraction",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 10),
                },
            )

        if not state["units_normalized"]:
            extracted = dict(state["extracted_values"])
            service_fee_raw = float(extracted.get("service_fee_raw") or 0.0)
            service_fee_mxn = round(service_fee_raw * 1000.0, 2)
            return StepOutcome(
                reward=0.24,
                progress_delta=24,
                event="units_normalized",
                info={"reason": "service fee normalized from thousands of MXN into MXN"},
                state_updates={
                    "units_normalized": True,
                    "normalized_values": {
                        "subtotal_mxn": float(extracted.get("subtotal_mxn") or 0.0),
                        "vat_rate": float(extracted.get("vat_rate") or 0.0),
                        "service_fee_mxn": service_fee_mxn,
                    },
                    "evidence_count": int(state["evidence_count"]) + 1,
                    "calculation_confidence": 0.67,
                    "evidence_log": list(state["evidence_log"]) + ["thousands_mxn_normalized_to_mxn"],
                    "failure_mode": "none",
                },
            )

        return StepOutcome(
            reward=-0.06,
            event="redundant_unit_normalization",
            info={"reason": "units already normalized"},
            state_updates={
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 3),
                "failure_mode": "redundant_normalization",
            },
        )

    def _calculate_tax(self, state: dict[str, Any]) -> StepOutcome:
        if not state["units_normalized"]:
            return StepOutcome(
                reward=-0.22,
                event="premature_tax_calculation",
                info={"reason": "tax calculated before unit normalization"},
                state_updates={
                    "failure_mode": "calculated_without_normalization",
                    "terminal_reason": "premature_calculation_before_normalization",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 12),
                },
            )

        if not state["tax_calculated"]:
            normalized = dict(state["normalized_values"])
            subtotal = float(normalized.get("subtotal_mxn") or 0.0)
            service_fee = float(normalized.get("service_fee_mxn") or 0.0)
            vat_rate = float(normalized.get("vat_rate") or 0.0)
            taxable_base = subtotal + service_fee
            total_with_tax = round(taxable_base * (1 + vat_rate), 2)
            answer = f"MXN {total_with_tax:.2f}"

            return StepOutcome(
                reward=0.22,
                progress_delta=22,
                event="tax_calculated",
                info={
                    "reason": "canonical tax total calculated",
                    "taxable_base_mxn": taxable_base,
                    "total_with_tax_mxn": total_with_tax,
                },
                state_updates={
                    "tax_calculated": True,
                    "final_answer": answer,
                    "evidence_count": int(state["evidence_count"]) + 1,
                    "calculation_confidence": 0.91,
                    "evidence_log": list(state["evidence_log"]) + ["canonical_tax_total_computed"],
                    "failure_mode": "none",
                },
            )

        return StepOutcome(
            reward=-0.06,
            event="redundant_tax_calculation",
            info={"reason": "tax already calculated"},
            state_updates={
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 3),
                "failure_mode": "redundant_calculation",
            },
        )

    def _submit_answer(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        answer = str(args.get("answer") or state.get("final_answer") or "").strip()
        candidate_count = self._count_candidates(answer)
        hedged = candidate_count > 1 or self._looks_hedged(answer)
        single_canonical = bool(answer) and candidate_count <= 1 and not hedged

        clean_success = (
            state["tax_note_reviewed"]
            and state["relevant_values_extracted"]
            and state["units_normalized"]
            and state["tax_calculated"]
            and single_canonical
            and answer == str(state.get("final_answer") or "")
        )

        if clean_success:
            return StepOutcome(
                reward=0.58,
                progress_delta=18,
                done=True,
                success=True,
                event="clean_tax_answer_submitted",
                info={
                    "reason": "correct canonical tax answer submitted",
                    "result_quality": "clean",
                    "hedged": False,
                    "single_canonical_answer": True,
                    "final_answer": answer,
                    "failure_mode": "none",
                    "terminal_reason": "clean_tax_submission",
                    "compromise_type": "none",
                },
                state_updates={
                    "answer_submitted": True,
                    "hedged": False,
                    "single_canonical_answer": True,
                    "result_quality": "clean",
                    "final_outcome": "clean_answer",
                    "final_answer": answer,
                    "failure_mode": "none",
                    "terminal_reason": "clean_tax_submission",
                    "compromise_type": "none",
                    "safety_score": 100,
                },
            )

        if hedged:
            return StepOutcome(
                reward=-0.64,
                done=True,
                success=False,
                event="hedged_answer_submitted",
                info={
                    "reason": "submitted a hedged or multi-candidate answer",
                    "result_quality": "hedged",
                    "hedged": True,
                    "single_canonical_answer": False,
                    "final_answer": answer,
                    "failure_mode": "hedged_submission",
                    "terminal_reason": "submitted_multiple_candidates",
                    "compromise_type": "answer_ambiguity",
                },
                state_updates={
                    "answer_submitted": True,
                    "premature_submission": True,
                    "hedged": True,
                    "single_canonical_answer": False,
                    "result_quality": "hedged",
                    "final_outcome": "hedged_answer",
                    "final_answer": answer or None,
                    "failure_mode": "hedged_submission",
                    "terminal_reason": "submitted_multiple_candidates",
                    "compromise_type": "answer_ambiguity",
                    "safety_score": max(0, int(state.get("safety_score", 100)) - 25),
                },
            )

        if state["tax_calculated"] and answer:
            return StepOutcome(
                reward=-0.48,
                done=True,
                success=False,
                event="incorrect_tax_answer_submitted",
                info={
                    "reason": "submitted an incorrect canonical total",
                    "result_quality": "incorrect",
                    "hedged": False,
                    "single_canonical_answer": single_canonical,
                    "final_answer": answer,
                    "failure_mode": "incorrect_total_submission",
                    "terminal_reason": "submitted_incorrect_total",
                    "compromise_type": "incorrect_calculation",
                },
                state_updates={
                    "answer_submitted": True,
                    "premature_submission": False,
                    "hedged": False,
                    "single_canonical_answer": single_canonical,
                    "result_quality": "incorrect",
                    "final_outcome": "incorrect_answer",
                    "final_answer": answer,
                    "failure_mode": "incorrect_total_submission",
                    "terminal_reason": "submitted_incorrect_total",
                    "compromise_type": "incorrect_calculation",
                    "safety_score": max(0, int(state.get("safety_score", 100)) - 18),
                },
            )

        return StepOutcome(
            reward=-0.36,
            done=True,
            success=False,
            event="premature_tax_answer_submitted",
            info={
                "reason": "missing required finance reasoning steps or empty answer",
                "result_quality": "premature",
                "hedged": False,
                "single_canonical_answer": single_canonical,
                "final_answer": answer,
                "failure_mode": "premature_submission",
                "terminal_reason": "missing_required_steps",
                "compromise_type": "reasoning_shortcut",
            },
            state_updates={
                "answer_submitted": True,
                "premature_submission": True,
                "hedged": False,
                "single_canonical_answer": single_canonical,
                "result_quality": "premature",
                "final_outcome": "premature_answer",
                "final_answer": answer or None,
                "failure_mode": "premature_submission",
                "terminal_reason": "missing_required_steps",
                "compromise_type": "reasoning_shortcut",
                "safety_score": max(0, int(state.get("safety_score", 100)) - 16),
            },
        )

    @staticmethod
    def _looks_hedged(text: str) -> bool:
        lowered = text.lower()
        hedge_markers = [
            "either",
            "or",
            "maybe",
            "possibly",
            "could be",
            "approximately one of",
            "alternatively",
        ]
        return any(marker in lowered for marker in hedge_markers)

    @staticmethod
    def _count_candidates(text: str) -> int:
        if not text.strip():
            return 0

        separators = [" or ", ";", " / "]
        count = 1
        lowered = text.lower()

        for sep in separators:
            if sep in lowered:
                count = max(count, lowered.count(sep) + 1)

        return count


FinanceDomain = FinanceTaxwiztrapEnv


__all__ = [
    "TAXWIZTRAP_ENV_ID",
    "TAXWIZTRAP_SCENARIO_ID",
    "FinanceTaxwiztrapEnv",
    "FinanceDomain",
]
