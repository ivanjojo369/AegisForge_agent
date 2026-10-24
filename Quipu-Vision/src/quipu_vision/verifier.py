"""Fail-closed audit of evidence returned by Quipu Vision's existing core.

This module does not redetect objects or recalculate image differences. Its job
is to verify the report contract, follow-up linkage and decision consistency.
A 'verified' result confirms spatial persistence of a *pixel change*, never
object identity, absence of hazards or physical safety.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from .schemas import (
    VisionSchemaError, VisualObservation, VisualVerification, VisualDecision,
)

_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_ALLOWED = {
    "request_second_observation", "persistent_change_review",
    "change_not_confirmed", "no_significant_change",
}


def _check_hashes(report: Mapping[str, Any], *, has_followup: bool) -> dict[str, str]:
    raw = report.get("source_sha256")
    if not isinstance(raw, Mapping):
        raise VisionSchemaError("source_sha256 must be a mapping")
    expected = {"reference", "observation", "followup"} if has_followup else {"reference", "observation"}
    if set(raw) != expected:
        raise VisionSchemaError("source_sha256 keys must match available observations")
    for name, value in raw.items():
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise VisionSchemaError(f"source_sha256.{name} is not a valid SHA256 digest")
    return {key: value.lower() for key, value in raw.items()}


def audit_engine_report(
    report: Mapping[str, Any],
) -> tuple[VisualObservation, VisualVerification | None, dict[str, str]]:
    """Validate a single report from ``core.inspect`` without trusting labels."""
    if not isinstance(report, Mapping):
        raise VisionSchemaError("core report must be an object")
    observation = VisualObservation.from_core(report.get("observation"))
    raw_followup = report.get("verification")
    verification = VisualVerification.from_core(raw_followup) if raw_followup is not None else None
    hashes = _check_hashes(report, has_followup=verification is not None)
    if type(report.get("verified")) is not bool:
        raise VisionSchemaError("verified must be a boolean")
    if verification is None and report["verified"]:
        raise VisionSchemaError("unverified initial observations cannot claim verified=True")
    if verification is not None:
        if observation.roi_xywh != verification.observation.roi_xywh:
            raise VisionSchemaError("follow-up ROI differs from the initial observation")
        if report["verified"] != verification.persistent_region:
            raise VisionSchemaError("verified disagrees with persistent_region")
        if verification.persistent_region and not (
            observation.change_detected and verification.observation.change_detected
        ):
            raise VisionSchemaError("persistent_region requires changes in both observations")

    actual_decision = report.get("decision")
    if actual_decision not in _ALLOWED:
        raise VisionSchemaError("unrecognized core decision")
    if not observation.change_detected:
        expected_decision = "no_significant_change"
    elif verification is None:
        expected_decision = "request_second_observation"
    else:
        expected_decision = (
            "persistent_change_review" if verification.persistent_region else "change_not_confirmed"
        )
    if actual_decision != expected_decision:
        raise VisionSchemaError("decision is inconsistent with the observations")
    return observation, verification, hashes


def audit_followup_pair(initial: Mapping[str, Any], final: Mapping[str, Any]) -> None:
    """Confirm the second inspection refers to the *same* baseline and first observation."""
    first_obs, first_verification, first_hashes = audit_engine_report(initial)
    final_obs, final_verification, final_hashes = audit_engine_report(final)
    if first_verification is not None or final_verification is None:
        raise VisionSchemaError("expected an initial report followed by a report with follow-up")
    if first_hashes["reference"] != final_hashes["reference"]:
        raise VisionSchemaError("reference image changed between passes")
    if first_hashes["observation"] != final_hashes["observation"]:
        raise VisionSchemaError("first observation image changed between passes")
    if first_obs != final_obs:
        raise VisionSchemaError("first observation measurements changed between passes")


def choose_next_action(
    observation: VisualObservation,
    verification: VisualVerification | None = None,
) -> VisualDecision:
    """Choose conservative actions, never certify human/environment safety."""
    if not observation.change_detected:
        return VisualDecision(
            status="no_significant_change", next_action="continue_monitoring",
            reason="No significant change was detected at the current thresholds.",
        )
    if verification is None:
        return VisualDecision(
            status="awaiting_followup", next_action="request_second_observation",
            reason="Image differences need a second observation before persistence can be checked.",
        )
    if verification.persistent_region:
        return VisualDecision(
            status="persistent_change_observed", next_action="request_human_review",
            reason="A pixel change persisted spatially; object identity and safety remain unverified.",
            confirmed_pixel_persistence=True,
        )
    return VisualDecision(
        status="change_not_confirmed", next_action="review_conditions_or_repeat",
        reason="The initially detected pixel change was not confirmed by the follow-up.",
    )
