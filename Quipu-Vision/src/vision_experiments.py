"""Quipu Vision reproducible parameter-comparison harness (offline, read-only).

Run from Quipu-Vision root:
    python src/vision_experiments.py --json-out outputs/quipu_vision/evaluation-lab.json

Requires quipu_vision and the evaluation images referenced by
examples/evaluation/manifest.json. Only explores configurations; never modifies
Quipu Vision's runtime defaults, input images, AegisForge, or AWS infrastructure.

This small *correlated synthetic* benchmark must not be treated as independent
holdout data or a real-world accuracy estimate. Any chosen setting should be
validated later against new and representative data.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import Any

SCHEMA = "quipu_vision.evaluation_manifest.v1"
REPORT_SCHEMA = "quipu_vision.experiment_matrix.v1"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CASES = 1000
MAX_CONFIGS = 16


def _confusion(rows: list[dict[str, Any]], truth: str, prediction: str) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for row in rows:
        actual, got = row.get(truth), row.get(prediction)
        if type(actual) is not bool or type(got) is not bool:
            continue
        counts["tp" if actual and got else "tn" if not actual and not got else "fp" if got else "fn"] += 1
    tp, tn, fp, fn = (counts[name] for name in ("tp", "tn", "fp", "fn"))
    n = tp + tn + fp + fn
    def ratio(a: float, b: float) -> float | None:
        return round(a / b, 4) if b else None
    return {"total": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "accuracy": ratio(tp + tn, n), "precision": ratio(tp, tp + fp),
            "recall": ratio(tp, tp + fn), "f1": ratio(2 * tp, 2 * tp + fp + fn),
            "false_positive_rate": ratio(fp, fp + tn),
            "false_negative_rate": ratio(fn, fn + tp)}


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[max(0, math.ceil(p * len(values)) - 1)], 3)


def _rel_image(root: Path, name: Any) -> Path:
    if (not isinstance(name, str) or not name or "\\" in name
            or Path(name).is_absolute() or Path(name).suffix.lower() != ".png"):
        raise ValueError("Evaluation images must be relative PNG paths")
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Evaluation image escapes dataset directory")
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError(f"Image missing, empty or too large: {name}")
    return path


def _manifest(path: Path) -> tuple[dict[str, Any], dict[str, Path], str]:
    path = path.expanduser().resolve(strict=True)
    if path.stat().st_size > 1_000_000:
        raise ValueError("Evaluation manifest exceeds 1 MiB")
    payload = path.read_bytes()
    data = json.loads(payload)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"Expected manifest schema {SCHEMA}")
    cases, images = data.get("cases"), data.get("image_sha256")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("Manifest needs 1–1000 cases")
    if not isinstance(images, dict) or not images:
        raise ValueError("Manifest must contain image_sha256")
    root = path.parent
    resolved: dict[str, Path] = {}
    for relative, expected in images.items():
        image_path = _rel_image(root, relative)
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"Invalid SHA-256 for {relative}")
        actual = hashlib.sha256(image_path.read_bytes()).hexdigest()
        if actual != expected.lower():
            raise ValueError(f"Image integrity check failed: {relative}")
        resolved[relative] = image_path
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Every evaluation case must be an object")
        name = case.get("id")
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("Every evaluation case must have a unique nonempty id")
        seen.add(name)
        for field in ("reference", "observed"):
            if case.get(field) not in resolved:
                raise ValueError(f"{name}: unknown {field} image")
        followup = case.get("followup")
        truth_persist = case.get("expected_persistent")
        if followup is not None and followup not in resolved:
            raise ValueError(f"{name}: unknown followup image")
        if type(case.get("expected_change")) is not bool:
            raise ValueError(f"{name}: expected_change must be boolean")
        if (followup is None) != (truth_persist is None):
            raise ValueError(f"{name}: followup and persistence label must correspond")
        if truth_persist is not None and type(truth_persist) is not bool:
            raise ValueError(f"{name}: expected_persistent must be boolean")
        roi = case.get("roi_xywh")
        if roi is not None and (not isinstance(roi, list) or len(roi) != 4
                                or any(type(x) is not int for x in roi)
                                or roi[0] < 0 or roi[1] < 0 or roi[2] < 1 or roi[3] < 1):
            raise ValueError(f"{name}: invalid ROI")
    return data, resolved, hashlib.sha256(payload).hexdigest()


def _numbers(raw: str, *, fraction: bool) -> list[int] | list[float]:
    values = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            raise ValueError("Empty parameter in list")
        try:
            value = float(item) if fraction else int(item)
        except ValueError as exc:
            raise ValueError(f"Invalid parameter: {item}") from exc
        if not math.isfinite(value) or not (0 < value <= (0.5 if fraction else 255)):
            raise ValueError(f"Parameter outside allowed range: {item}")
        if value not in values:
            values.append(value)
    return values


def _configurations(data: dict[str, Any], thresholds: str, fractions: str) -> tuple[list[dict[str, Any]], str]:
    source = data.get("settings") or {}
    base_t, base_f = source.get("threshold", 32), source.get("min_region_fraction", 0.004)
    if type(base_t) is not int or not 1 <= base_t <= 255:
        raise ValueError("Invalid manifest threshold")
    if type(base_f) not in (int, float) or not math.isfinite(base_f) or not 0 < base_f <= 0.5:
        raise ValueError("Invalid manifest min_region_fraction")
    thresholds_ = _numbers(thresholds, fraction=False)
    fractions_ = _numbers(fractions, fraction=True)
    combos = [(base_t, float(base_f))]
    combos.extend((t, f) for t in thresholds_ for f in fractions_)
    configs = []
    seen = set()
    for threshold, fraction in combos:
        key = (threshold, fraction)
        if key in seen:
            continue
        seen.add(key)
        configs.append({"id": f"t{threshold}_f{fraction:.6g}",
                        "threshold": threshold, "min_region_fraction": fraction,
                        "role": "baseline" if len(configs) == 0 else "exploratory"})
    if len(configs) > MAX_CONFIGS:
        raise ValueError(f"Too many configurations; maximum is {MAX_CONFIGS}")
    return configs, configs[0]["id"]


def _evaluate(data: dict[str, Any], images: dict[str, Path],
              config: dict[str, Any], repeat: int) -> dict[str, Any]:
    from quipu_vision.workflow import run_visual_workflow
    rows: list[dict[str, Any]] = []
    times: list[float] = []
    inconsistent: list[str] = []
    cv_versions: set[str] = set()
    for case in data["cases"]:
        row: dict[str, Any] = {"id": case["id"], "category": case.get("category", "unknown"),
                               "expected_change": case["expected_change"],
                               "expected_persistent": case.get("expected_persistent"),
                               "predicted_change": None, "predicted_persistent": None,
                               "next_action": None, "error": None, "elapsed_ms": None}
        outputs: list[tuple[bool, bool | None]] = []
        durations: list[float] = []
        for _ in range(repeat):
            try:
                kwargs: dict[str, Any] = {"threshold": config["threshold"],
                                          "min_region_fraction": config["min_region_fraction"]}
                if case.get("roi_xywh") is not None:
                    kwargs["roi"] = tuple(case["roi_xywh"])
                if case.get("followup") is not None:
                    kwargs["followup_path"] = images[case["followup"]]
                before = time.perf_counter()
                result = run_visual_workflow(images[case["reference"]],
                                             images[case["observed"]], **kwargs)
                elapsed = (time.perf_counter() - before) * 1000
                durations.append(elapsed)
                cv_versions.add(result.cv2_version)
                outcomes = (result.observation.change_detected,
                            result.decision.confirmed_pixel_persistence if case.get("followup") else None)
                outputs.append(outcomes)
                if len(outputs) == 1:
                    row["predicted_change"], row["predicted_persistent"] = outcomes
                    row["next_action"] = result.decision.next_action
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                break
        if durations:
            row["elapsed_ms"] = round(statistics.mean(durations), 3)
            times.extend(durations)
        if len(set(outputs)) > 1:
            inconsistent.append(case["id"])
        rows.append(row)
    valid = [row for row in rows if row["error"] is None]
    initial = _confusion(valid, "expected_change", "predicted_change")
    persistent = _confusion(valid, "expected_persistent", "predicted_persistent")
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        groups[str(row["category"])].append(row)
    mismatches = [{"id": row["id"], "category": row["category"], "stage": stage,
                   "expected": row[expected], "observed": row[predicted]}
                  for row in valid
                  for stage, expected, predicted in (("initial", "expected_change", "predicted_change"),
                                                      ("persistence", "expected_persistent", "predicted_persistent"))
                  if row[expected] is not None and row[expected] != row[predicted]]
    failures = [{"id": row["id"], "reason": row["error"]} for row in rows if row["error"]]
    return {"id": config["id"], "role": config["role"],
            "settings": {"threshold": config["threshold"], "min_region_fraction": config["min_region_fraction"]},
            "case_count": len(rows), "successful_cases": len(valid),
            "initial_change_confusion": initial, "spatial_persistence_confusion": persistent,
            "category_initial_breakdown": {k: _confusion(v, "expected_change", "predicted_change")
                                           for k, v in sorted(groups.items())},
            "mismatches": mismatches, "failures": failures, "unstable_cases": inconsistent,
            "timing_ms": {"mean": round(statistics.mean(times), 3) if times else None,
                          "p50_nearest_rank": _percentile(times, .5),
                          "p95_nearest_rank": _percentile(times, .95),
                          "measurements": len(times)},
            "opencv_versions": sorted(cv_versions), "cases": rows}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Quipu Vision offline evidence and parameter comparisons")
    parser.add_argument("--manifest", type=Path, default=root / "examples/evaluation/manifest.json")
    parser.add_argument("--thresholds", default="24,32,40", help="Exploratory candidate thresholds")
    parser.add_argument("--min-fractions", default="0.004", help="Exploratory minimum area fractions")
    parser.add_argument("--repeat", type=int, default=1, help="Repeats per case, 1–5")
    parser.add_argument("--json-out", type=Path, default=root / "outputs/quipu_vision/evaluation-lab.json")
    parser.add_argument("--allow-other-opencv", action="store_true", help="Local testing only; flags non-OpenCV-5 results")
    args = parser.parse_args()
    try:
        if not 1 <= args.repeat <= 5:
            raise ValueError("--repeat must be 1–5")
        import cv2
        if not args.allow_other_opencv and cv2.__version__.split(".", 1)[0] != "5":
            raise ValueError("OpenCV 5 required; --allow-other-opencv is ONLY for local smoke tests")
        data, images, manifest_hash = _manifest(args.manifest)
        configurations, baseline = _configurations(data, args.thresholds, args.min_fractions)
        runs = [_evaluate(data, images, config, args.repeat) for config in configurations]
        versions = sorted({v for run in runs for v in run["opencv_versions"]})
        report = {
            "report_schema": REPORT_SCHEMA,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dataset_name": data.get("dataset_name"), "dataset_type": data.get("dataset_origin"),
            "manifest_sha256": manifest_hash, "python_version": platform.python_version(),
            "opencv_version": cv2.__version__, "opencv5_confirmed": cv2.__version__.split(".", 1)[0] == "5",
            "baseline_id": baseline, "repeats_per_case": args.repeat, "runs": runs,
            "interpretation": "Exploratory settings on correlated synthetic images; NOT held-out validation or real-world accuracy. No settings were applied to the runtime.",
            "aws_execution_verified": False,
        }
        dest = args.json_out.expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Quipu Vision experimental comparison | OpenCV {cv2.__version__} | {len(data['cases'])} synthetic cases")
        for run in runs:
            i, p = run["initial_change_confusion"], run["spatial_persistence_confusion"]
            print(f"  {run['id']} ({run['role']}): initial F1={i['f1']} accuracy={i['accuracy']} FP={i['fp']} FN={i['fn']}; "
                  f"persistence F1={p['f1']}; p95={run['timing_ms']['p95_nearest_rank']} ms; "
                  f"errors={len(run['failures'])}")
        print(f"Report: {dest}")
        print("CAUTION: synthetic, correlated cases; experiment does not improve or configure the detector.")
        if versions != [cv2.__version__]:
            print("WARNING: inconsistent OpenCV version in workflow results", file=sys.stderr)
            return 1
        return 0 if all(not x["failures"] and not x["unstable_cases"] for x in runs) else 1
    except (ValueError, OSError, ImportError, json.JSONDecodeError) as exc:
        print(f"Evaluation could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
