"""Observable Quipu Vision decision loop built on the existing OpenCV engine.

Observe -> decide whether a follow-up is required -> process that observation
(if supplied) -> audit the resulting evidence -> recommend the next action.

The module never controls cameras or external equipment; an actual AegisForge
executor must obtain a requested follow-up before calling it again. Paths must
be sandboxed by the calling adapter for user-originated requests.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .core import inspect
from .schemas import VisionSchemaError, VisionWorkflowResult
from .verifier import audit_engine_report, audit_followup_pair, choose_next_action

InspectFunction = Callable[..., dict[str, Any]]


def run_visual_workflow(
    reference_path: str | Path,
    observed_path: str | Path,
    *,
    followup_path: str | Path | None = None,
    roi: tuple[int, int, int, int] | None = None,
    threshold: int = 32,
    min_region_fraction: float = 0.004,
    annotated_path: str | Path | None = None,
    _inspect: InspectFunction = inspect,
) -> VisionWorkflowResult:
    """Run a bounded image-change workflow and preserve the evidence trail.

    ``followup_path`` represents an image **already obtained by the caller**.
    If the first observation is changed and no follow-up exists, the workflow
    returns ``request_second_observation`` instead of inventing one.

    No object detection, video tracking, AWS deployment or safety certification
    is implied by this function.
    """
    kwargs = {
        "roi": roi, "threshold": threshold,
        "min_region_fraction": min_region_fraction,
        "annotated_path": annotated_path,
    }
    initial = _inspect(reference_path, observed_path, **kwargs)
    observation, _, hashes = audit_engine_report(initial)
    events: list[dict[str, str]] = [
        {"phase": "observe", "result": "change_detected" if observation.change_detected else "no_significant_change"}
    ]
    warnings = list(initial.get("warnings") or [])
    if not isinstance(initial.get("cv2_version"), str) or not initial["cv2_version"]:
        raise VisionSchemaError("core report is missing cv2_version")

    final: Mapping[str, Any] = initial
    verification = None
    if observation.change_detected:
        events.append({"phase": "plan", "result": "request_second_observation"})
        if followup_path is not None:
            # Second pass is a real call to OpenCV, with an actual second image.
            final = _inspect(
                reference_path, observed_path, followup_path=followup_path,
                roi=roi, threshold=threshold,
                min_region_fraction=min_region_fraction,
            )
            audit_followup_pair(initial, final)
            observation, verification, hashes = audit_engine_report(final)
            events.append({"phase": "act", "result": "process_supplied_followup_image"})
            events.append({"phase": "verify", "result": (
                "persistent_pixel_change" if verification and verification.persistent_region
                else "change_not_confirmed"
            )})
            warnings.extend(final.get("warnings") or [])
        else:
            events.append({"phase": "act", "result": "await_external_followup_image"})
    elif followup_path is not None:
        warnings.append("Follow-up image not processed: no initial change required verification.")

    decision = choose_next_action(observation, verification)
    events.append({"phase": "decision", "result": decision.next_action})
    version = str(initial["cv2_version"])
    if version.split(".", 1)[0] != "5":
        warnings.append("OpenCV 5 has not been confirmed in this environment; competition validation is pending.")
    return VisionWorkflowResult(
        observation=observation,
        verification=verification,
        decision=decision,
        source_sha256=hashes,
        cv2_version=version,
        events=tuple(events),
        warnings=tuple(dict.fromkeys(warnings)),
        annotated_output=str(initial["annotated_output"]) if initial.get("annotated_output") else None,
    )
