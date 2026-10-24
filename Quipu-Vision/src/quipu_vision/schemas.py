"""Typed, JSON-ready contracts for Quipu Vision visual evidence.

These structures describe *image differences*, not object identities or safety.
The numeric ``changed_pixel_fraction`` is a pixel measurement, not model
confidence.  No confidence score is fabricated by this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping


class VisionSchemaError(ValueError):
    """Invalid or internally inconsistent visual evidence."""


def _rectangle(raw: Any, *, field_name: str) -> tuple[int, int, int, int]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise VisionSchemaError(f"{field_name} must have four coordinates")
    if any(type(value) is not int for value in raw):
        raise VisionSchemaError(f"{field_name} coordinates must be integers")
    x, y, width, height = raw
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise VisionSchemaError(f"{field_name} must have a nonnegative origin and positive size")
    return x, y, width, height


def _fraction(value: Any, *, field_name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise VisionSchemaError(f"{field_name} must be a finite value between 0 and 1")
    return float(value)


@dataclass(frozen=True, slots=True)
class VisualRegion:
    bbox_xywh: tuple[int, int, int, int]
    area_px: float
    area_fraction: float

    @classmethod
    def from_core(cls, raw: Mapping[str, Any]) -> "VisualRegion":
        if not isinstance(raw, Mapping):
            raise VisionSchemaError("each region must be an object")
        box = _rectangle(raw.get("bbox_xywh"), field_name="region.bbox_xywh")
        area = raw.get("area_px")
        if type(area) not in (int, float) or not math.isfinite(area) or area <= 0:
            raise VisionSchemaError("region.area_px must be positive and finite")
        return cls(box, float(area), _fraction(raw.get("area_fraction"), field_name="region.area_fraction"))

    def to_dict(self) -> dict[str, Any]:
        return {"bbox_xywh": list(self.bbox_xywh), "area_px": self.area_px,
                "area_fraction": self.area_fraction}


@dataclass(frozen=True, slots=True)
class VisualObservation:
    roi_xywh: tuple[int, int, int, int]
    change_detected: bool
    changed_pixel_fraction: float
    regions: tuple[VisualRegion, ...]
    regions_total: int

    @classmethod
    def from_core(cls, raw: Mapping[str, Any]) -> "VisualObservation":
        if not isinstance(raw, Mapping):
            raise VisionSchemaError("observation must be an object")
        roi = _rectangle(raw.get("roi_xywh"), field_name="observation.roi_xywh")
        change = raw.get("change_detected")
        if type(change) is not bool:
            raise VisionSchemaError("observation.change_detected must be boolean")
        regions_raw = raw.get("regions")
        if not isinstance(regions_raw, list):
            raise VisionSchemaError("observation.regions must be a list")
        regions = tuple(VisualRegion.from_core(item) for item in regions_raw)
        count = raw.get("regions_total")
        if type(count) is not int or count < len(regions):
            raise VisionSchemaError("observation.regions_total is inconsistent")
        x, y, w, h = roi
        for region in regions:
            rx, ry, rw, rh = region.bbox_xywh
            if rx < x or ry < y or rx + rw > x + w or ry + rh > y + h:
                raise VisionSchemaError("detected region is outside the region of interest")
        if change and not regions:
            raise VisionSchemaError("detected change needs at least one region")
        return cls(
            roi_xywh=roi,
            change_detected=change,
            changed_pixel_fraction=_fraction(raw.get("changed_pixel_fraction"), field_name="observation.changed_pixel_fraction"),
            regions=regions,
            regions_total=count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"roi_xywh": list(self.roi_xywh), "change_detected": self.change_detected,
                "changed_pixel_fraction": self.changed_pixel_fraction,
                "regions": [region.to_dict() for region in self.regions],
                "regions_total": self.regions_total}


@dataclass(frozen=True, slots=True)
class VisualVerification:
    observation: VisualObservation
    persistent_region: bool
    matched_by: str

    @classmethod
    def from_core(cls, raw: Mapping[str, Any]) -> "VisualVerification":
        if not isinstance(raw, Mapping) or type(raw.get("persistent_region")) is not bool:
            raise VisionSchemaError("verification needs a boolean persistent_region")
        matched = raw.get("matched_by")
        if not isinstance(matched, str) or not matched.strip():
            raise VisionSchemaError("verification.matched_by must describe the method")
        return cls(VisualObservation.from_core(raw.get("observation")),
                   raw["persistent_region"], matched)

    def to_dict(self) -> dict[str, Any]:
        return {"observation": self.observation.to_dict(),
                "persistent_region": self.persistent_region, "matched_by": self.matched_by}


@dataclass(frozen=True, slots=True)
class VisualDecision:
    status: str
    next_action: str
    reason: str
    confirmed_pixel_persistence: bool = False
    human_review_required_for_safety: bool = True
    safety_certified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VisionWorkflowResult:
    """Serializable output suitable for the existing AegisForge plugin bridge."""

    observation: VisualObservation
    verification: VisualVerification | None
    decision: VisualDecision
    source_sha256: dict[str, str]
    cv2_version: str
    events: tuple[dict[str, str], ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    annotated_output: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.decision.status,
            "next_action": self.decision.next_action,
            "reason": self.decision.reason,
            "verified": self.decision.confirmed_pixel_persistence,
            "verification_scope": "persistent_pixel_change_only",
            "safety_certified": False,
            "observation": self.observation.to_dict(),
            "verification": self.verification.to_dict() if self.verification else None,
            "decision": self.decision.to_dict(),
            "source_sha256": dict(self.source_sha256),
            "cv2_version": self.cv2_version,
            "opencv5_confirmed": self.cv2_version.split(".", 1)[0] == "5",
            "events": list(self.events),
            "warnings": list(self.warnings),
        }
        if self.annotated_output is not None:
            result["annotated_output"] = self.annotated_output
        return result
