"""Quipu Vision v1 synthetic benchmark. No external services or image downloads.

Run from AegisForge_agent with Quipu-Vision installed in its venv:
  .\\.venv\\Scripts\\python.exe .\\Quipu-Vision\\examples\\evaluation\\evaluate.py
  .\\.venv\\Scripts\\python.exe .\\Quipu-Vision\\examples\\evaluation\\evaluate.py --json-out .\\outputs\\quipu_vision\\evaluation-results.json

The ground truth is synthetic scene-level change, not object identity or safety.
This script does not tune detector thresholds or certify real-world performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from quipu_vision.workflow import run_visual_workflow


SCHEMA = "quipu_vision.evaluation_manifest.v1"


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} must be true or false")
    return value


def _inside(base: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Invalid relative image path: {relative!r}")
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise ValueError("Absolute image paths are not allowed in the manifest")
    path = (base / relative_path).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Image path leaves evaluation folder: {relative}") from exc
    if path.suffix.lower() != ".png" or not path.is_file():
        raise ValueError(f"Image does not exist or is not PNG: {relative}")
    return path


def _read_manifest(manifest_path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent
    if manifest_path.stat().st_size > 1_000_000:
        raise ValueError("Manifest is too large")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError(f"Expected schema {SCHEMA}")
    cases = payload.get("cases")
    files = payload.get("image_sha256")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        raise ValueError("Manifest cases must be a nonempty list of at most 1000")
    if not isinstance(files, dict) or not files:
        raise ValueError("Manifest must include image_sha256")

    resolved: dict[str, Path] = {}
    for relative, expected in files.items():
        path = _inside(root, relative)
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"Invalid SHA256 manifest entry: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected.lower():
            raise ValueError(f"SHA256 mismatch for {relative}; dataset may have been modified")
        resolved[relative] = path

    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Every case must be an object")
        name = case.get("id")
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError(f"Duplicate or invalid case id: {name!r}")
        ids.add(name)
        _boolean(case.get("expected_change"), f"{name}.expected_change")
        persistence = case.get("expected_persistent")
        if persistence is not None:
            _boolean(persistence, f"{name}.expected_persistent")
        if (case.get("followup") is None) != (persistence is None):
            raise ValueError(f"{name}: followup and expected_persistent must be supplied together")
        for key in ("reference", "observed", "followup"):
            if key in case and case[key] is not None and case[key] not in resolved:
                raise ValueError(f"{name}: {key} is missing from image_sha256")
        if "reference" not in case or "observed" not in case:
            raise ValueError(f"{name}: missing reference or observed image")
        roi = case.get("roi_xywh")
        if roi is not None and (not isinstance(roi, list) or len(roi) != 4 or
                                any(type(v) is not int for v in roi)):
            raise ValueError(f"{name}: ROI must be four integers")
    return payload, resolved


def _counts(rows: list[dict[str, Any]], truth_key: str, prediction_key: str) -> dict[str, Any]:
    counts = Counter()
    for row in rows:
        truth = row[truth_key]
        predicted = row[prediction_key]
        if truth is None or predicted is None:
            continue
        counts["tp" if truth and predicted else
               "tn" if not truth and not predicted else
               "fp" if predicted else "fn"] += 1
    tp, tn, fp, fn = (counts[k] for k in ("tp", "tn", "fp", "fn"))
    ratio = lambda top, bottom: round(top / bottom, 4) if bottom else None
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    f1 = ratio(2 * tp, 2 * tp + fp + fn)
    return {
        "total": tp + tn + fp + fn, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": ratio(tp + tn, tp + tn + fp + fn),
        "precision": precision, "recall": recall, "f1": f1,
        "false_positive_rate": ratio(fp, fp + tn),
        "false_negative_rate": ratio(fn, fn + tp),
    }


def _percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    index = max(0, math.ceil(len(values) * percentile) - 1)
    return round(sorted_values[index], 2)


def evaluate(manifest_file: Path) -> dict[str, Any]:
    manifest, sources = _read_manifest(manifest_file)
    settings = manifest.get("settings") or {}
    threshold = settings.get("threshold", 32)
    minimum = settings.get("min_region_fraction", 0.004)
    if type(threshold) is not int or not 1 <= threshold <= 255:
        raise ValueError("Invalid threshold")
    if type(minimum) not in (int, float) or not 0 < minimum <= 0.5:
        raise ValueError("Invalid min_region_fraction")

    rows: list[dict[str, Any]] = []
    timing: list[float] = []
    version = None
    for case in manifest["cases"]:
        kwargs: dict[str, Any] = {"threshold": threshold, "min_region_fraction": minimum}
        if case.get("followup") is not None:
            kwargs["followup_path"] = sources[case["followup"]]
        if case.get("roi_xywh") is not None:
            kwargs["roi"] = tuple(case["roi_xywh"])
        row = {
            "id": case["id"],
            "category": case.get("category", "unknown"),
            "expected_change": case["expected_change"],
            "expected_persistent": case.get("expected_persistent"),
            "tags": case.get("tags", []),
        }
        start = time.perf_counter()
        try:
            result = run_visual_workflow(sources[case["reference"]], sources[case["observed"]], **kwargs)
            row.update({
                "predicted_change": result.observation.change_detected,
                "predicted_persistent": result.decision.confirmed_pixel_persistence if case.get("followup") is not None else None,
                "followup_processed": result.verification is not None,
                "next_action": result.decision.next_action,
                "regions_total": result.observation.regions_total,
                "changed_pixel_fraction": result.observation.changed_pixel_fraction,
                "opencv_version": result.cv2_version,
                "warnings": list(result.warnings),
                "error": None,
            })
            if version is not None and version != result.cv2_version:
                raise ValueError("Inconsistent OpenCV versions across cases")
            version = result.cv2_version
        except Exception as exc:
            row.update({"predicted_change": None, "predicted_persistent": None,
                        "followup_processed": False, "error": f"{type(exc).__name__}: {exc}"})
        row["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
        timing.append(row["elapsed_ms"])
        rows.append(row)

    valid_rows = [row for row in rows if row["error"] is None]
    initial = _counts(valid_rows, "expected_change", "predicted_change")
    followup_rows = [row for row in valid_rows if row["expected_persistent"] is not None]
    confirmation = _counts(followup_rows, "expected_persistent", "predicted_persistent")
    errors = [row["id"] for row in rows if row["error"] is not None]
    mismatches = [
        {"id": row["id"], "stage": stage, "expected": row[truth], "observed": row[pred]}
        for row in valid_rows
        for stage, truth, pred in (
            ("initial_change", "expected_change", "predicted_change"),
            ("persistent_change", "expected_persistent", "predicted_persistent"),
        )
        if row[truth] is not None and row[truth] != row[pred]
    ]
    groups = defaultdict(list)
    for row in valid_rows:
        groups[row["category"]].append(row)
    return {
        "report_schema": "quipu_vision.evaluation_results.v1",
        "dataset_name": manifest.get("dataset_name"),
        "dataset_type": manifest.get("dataset_origin"),
        "label_policy": manifest.get("ground_truth"),
        "settings": {"threshold": threshold, "min_region_fraction": minimum},
        "opencv_version": version,
        "case_count": len(rows),
        "successful_cases": len(valid_rows),
        "errors": errors,
        "initial_change_confusion": initial,
        "spatial_persistence_confusion": confirmation,
        "category_initial_breakdown": {name: _counts(subset, "expected_change", "predicted_change") for name, subset in sorted(groups.items())},
        "mismatches": mismatches,
        "timing_ms": {"mean": round(statistics.mean(timing), 2) if timing else None,
                      "p50_nearest_rank": _percentile_nearest_rank(timing, 0.5),
                      "p95_nearest_rank": _percentile_nearest_rank(timing, 0.95)},
        "scope_warning": "Small correlated synthetic cases; not an estimate of real-world reliability, object recognition, or safety.",
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Quipu Vision on labeled synthetic images.")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    parser.add_argument("--json-out", type=Path, help="Optional location for complete machine-readable report")
    args = parser.parse_args()
    try:
        summary = evaluate(args.manifest)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Dataset validation failed: {exc}", file=sys.stderr)
        return 2

    print(f"Quipu Vision | OpenCV {summary['opencv_version']} | Synthetic cases {summary['case_count']}")
    for stage, key in (("Initial scene change", "initial_change_confusion"),
                       ("Spatial persistence", "spatial_persistence_confusion")):
        stats = summary[key]
        print(f"{stage}: total={stats['total']} TP={stats['tp']} TN={stats['tn']} FP={stats['fp']} FN={stats['fn']}")
        print(f"  accuracy={stats['accuracy']} precision={stats['precision']} recall={stats['recall']} f1={stats['f1']}")
    print(f"Processing time: mean={summary['timing_ms']['mean']} ms, p95={summary['timing_ms']['p95_nearest_rank']} ms")
    print(f"Mismatches={len(summary['mismatches'])}, errors={len(summary['errors'])}")
    for item in summary["mismatches"]:
        print(f"  MISMATCH {item['id']} ({item['stage']}): expected={item['expected']}, observed={item['observed']}")
    print("Caution: synthetic scene-change evaluation, NOT an object/safety benchmark.")
    if args.json_out:
        path = args.json_out.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Full results: {path}")
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
