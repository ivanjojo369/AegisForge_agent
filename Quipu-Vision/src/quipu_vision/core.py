"""Quipu Vision MVP: deterministic visual change evidence for static-camera scenes.

A visible change is *not* an object label or a safety assessment.  Human review
is required; results are limited to images taken from the same viewpoint.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import cv2
import numpy as np

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000


def _load(path: str | Path) -> tuple[np.ndarray, str]:
    source = Path(path).resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"Image must be a file of at most {MAX_IMAGE_BYTES} bytes: {source}")
    contents = source.read_bytes()
    # imdecode supports non-ASCII paths on Windows.
    img = cv2.imdecode(np.frombuffer(contents, np.uint8), cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        raise ValueError(f"Cannot decode image: {source}")
    h, w = img.shape[:2]
    if h * w > MAX_IMAGE_PIXELS:
        raise ValueError("Image dimensions exceed the processing limit")
    return img, hashlib.sha256(contents).hexdigest()


def _measure(reference: np.ndarray, observed: np.ndarray, *,
             roi: tuple[int, int, int, int] | None = None,
             threshold: int = 32,
             min_region_fraction: float = 0.004) -> dict[str, Any]:
    if reference.shape != observed.shape:
        raise ValueError("Reference and observed images must have identical dimensions")
    height, width = observed.shape[:2]
    x, y, rw, rh = roi or (0, 0, width, height)
    if not (0 <= x < width and 0 <= y < height and rw > 0 and rh > 0 and
            x + rw <= width and y + rh <= height):
        raise ValueError("ROI must be inside the image and have positive size")
    if not (1 <= threshold <= 255):
        raise ValueError("threshold must be between 1 and 255")
    if not (0.0 < min_region_fraction <= 0.5):
        raise ValueError("min_region_fraction must be in (0, 0.5]")

    baseline = cv2.GaussianBlur(reference[y:y+rh, x:x+rw], (5, 5), 0)
    candidate = cv2.GaussianBlur(observed[y:y+rh, x:x+rw], (5, 5), 0)
    diff = cv2.cvtColor(cv2.absdiff(baseline, candidate), cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = rw * rh
    minimum = max(16, int(area * min_region_fraction))
    regions = []
    for contour in contours:
        pixels = cv2.contourArea(contour)
        if pixels < minimum:
            continue
        rx, ry, ww, hh = cv2.boundingRect(contour)
        regions.append({"bbox_xywh": [int(rx+x), int(ry+y), int(ww), int(hh)],
                        "area_px": round(float(pixels), 1),
                        "area_fraction": round(float(pixels / area), 5)})
    regions.sort(key=lambda z: z["area_px"], reverse=True)
    changed_fraction = float(np.count_nonzero(mask) / area)
    return {"roi_xywh": [x, y, rw, rh], "changed_pixel_fraction": round(changed_fraction, 5),
            "regions": regions[:12], "regions_total": len(regions),
            "change_detected": bool(regions and changed_fraction >= min_region_fraction)}


def _iou(a: list[int], b: list[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0, right-left) * max(0, bottom-top)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def inspect(reference_path: str | Path, observed_path: str | Path,
            *, followup_path: str | Path | None = None,
            roi: tuple[int, int, int, int] | None = None,
            threshold: int = 32, min_region_fraction: float = 0.004,
            annotated_path: str | Path | None = None) -> dict[str, Any]:
    """Compare current/follow-up image with a fixed reference.

    Does not classify objects or decide whether an actual walkway is safe.
    """
    ref, ref_hash = _load(reference_path)
    obs, obs_hash = _load(observed_path)
    first = _measure(ref, obs, roi=roi, threshold=threshold,
                     min_region_fraction=min_region_fraction)
    warnings = ["Changes are not object detections; fixed viewpoint and stable lighting required.",
                "Human review required before any consequential action."]
    result: dict[str, Any] = {
        "version": "0.1.0", "cv2_version": cv2.__version__,
        "method": "opencv_absolute_difference_morphology_contours",
        "source_sha256": {"reference": ref_hash, "observation": obs_hash},
        "observation": first, "verification": None,
        "decision": "request_second_observation" if first["change_detected"] else "no_significant_change",
        "verified": False, "warnings": warnings,
    }
    if followup_path is not None:
        second_img, second_hash = _load(followup_path)
        second = _measure(ref, second_img, roi=roi, threshold=threshold,
                          min_region_fraction=min_region_fraction)
        persistent = (first["change_detected"] and second["change_detected"] and
                      any(_iou(a["bbox_xywh"], b["bbox_xywh"]) >= 0.2
                          for a in first["regions"] for b in second["regions"]))
        result["source_sha256"]["followup"] = second_hash
        result["verification"] = {"observation": second,
                                  "persistent_region": bool(persistent),
                                  "matched_by": "bounding_box_iou>=0.2"}
        result["decision"] = "persistent_change_review" if persistent else (
            "change_not_confirmed" if first["change_detected"] else "no_significant_change")
        # Verified means there was a second measurement confirming a change,
        # NOT that a dangerous obstruction or specific object was confirmed.
        result["verified"] = bool(persistent)
    if annotated_path is not None:
        annotated = obs.copy()
        xx, yy, rw, rh = first["roi_xywh"]
        cv2.rectangle(annotated, (xx, yy), (xx+rw-1, yy+rh-1), (225, 160, 0), 2)
        for region in first["regions"]:
            a, b, w, h = region["bbox_xywh"]
            cv2.rectangle(annotated, (a,b), (a+w,b+h), (30,30,245), 2)
        target = Path(annotated_path)
        if target.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("Annotated output must be PNG or JPEG")
        target.parent.mkdir(parents=True, exist_ok=True)
        ext = ".png" if target.suffix.lower() == ".png" else ".jpg"
        ok, data = cv2.imencode(ext, annotated)
        if not ok:
            raise RuntimeError("Could not encode annotated output")
        target.write_bytes(data.tobytes())
        result["annotated_output"] = str(target.resolve())
    return result
