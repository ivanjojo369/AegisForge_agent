"""Read-only, source-bound scorecards for AegisForge Nexus Laboratory.

Destination: src/aegisforge/nexus/lab/scorecard.py

Consolidates already generated EvaluationRunner reports, RegressionDetector
comparisons and AblationStudy analyses.  Every linked comparison/study must be
recomputed against its source evaluations; a digest by itself is insufficient.
The scorecard is descriptive and cannot authenticate host-supplied evidence,
claim causal effects, or run a tool/model/scenario.  It never writes files.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Mapping, Sequence

from .ablation_study import (
    AblationDesign, AblationLimits, AblationPair, NexusAblationError, NexusAblationStudy,
)
from .regression_detector import (
    NexusRegressionDetector, NexusRegressionError, RegressionLimits,
)
from .scenario_catalog import PRODUCTS, CATEGORIES

SCORECARD_SCHEMA = "aegisforge.nexus.lab.scorecard.v1"
ASSURANCE = "host_supplied_observations_integrity_only_not_authenticated"
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


class NexusScorecardError(ValueError):
    """Missing, malformed or inconsistent source evidence for a scorecard."""


def _encode(value: Any) -> bytes:
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusScorecardError("invalid scorecard JSON data") from exc


def _hash(value: Any) -> str:
    return sha256(_encode(value)).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NexusScorecardError(message)


def _digest(value: Any) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _counts() -> dict[str, int]:
    return {"evaluations": 0, "passed": 0, "failed": 0, "inconclusive": 0}


def _tally(group: dict[str, int], status: str) -> None:
    group["evaluations"] += 1
    group[{"pass": "passed", "failed": "failed", "inconclusive": "inconclusive"}[status]] += 1


def _summary(counter: dict[str, int]) -> dict[str, int | float | None]:
    number = counter["evaluations"]
    assessed = counter["passed"] + counter["failed"]
    return {**counter,
            "pass_fraction_all_observations": round(counter["passed"] / number, 6) if number else None,
            "pass_fraction_conclusive_only": round(counter["passed"] / assessed, 6) if assessed else None}


@dataclass(frozen=True, slots=True)
class ScorecardLimits:
    max_evaluations: int = 32
    max_comparisons: int = 16
    max_ablations: int = 8
    max_cases_per_evaluation: int = 256
    max_input_report_bytes: int = 2_000_000
    max_output_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        for key, low, high in (
            ("max_evaluations", 1, 128), ("max_comparisons", 0, 64),
            ("max_ablations", 0, 32), ("max_cases_per_evaluation", 1, 2048),
            ("max_input_report_bytes", 4096, 8_000_000),
            ("max_output_bytes", 4096, 8_000_000),
        ):
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise NexusScorecardError(f"invalid {key}")


@dataclass(frozen=True, slots=True)
class ScorecardComparison:
    """Source-bound regression comparison, not just an unauthenticated digest."""
    baseline_report: Mapping[str, Any]
    candidate_report: Mapping[str, Any]
    comparison_report: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ScorecardAblation:
    """Source-bound ablation report with the original design and paired trials."""
    design: AblationDesign
    pairs: tuple[AblationPair, ...]
    study_report: Mapping[str, Any]


class NexusScorecard:
    """Deterministically aggregates selected reports, never invokes any scenario."""

    def __init__(self, *, limits: ScorecardLimits | None = None) -> None:
        if limits is not None and not isinstance(limits, ScorecardLimits):
            raise NexusScorecardError("invalid scorecard limits")
        self.limits = limits if limits is not None else ScorecardLimits()
        self._regression = NexusRegressionDetector(limits=RegressionLimits(
            max_cases=self.limits.max_cases_per_evaluation,
            max_report_bytes=self.limits.max_input_report_bytes,
            max_output_bytes=self.limits.max_output_bytes))
        self._ablation = NexusAblationStudy(limits=AblationLimits(
            max_pairs=32,
            max_cases_per_pair=self.limits.max_cases_per_evaluation,
            max_report_bytes=self.limits.max_input_report_bytes,
            max_output_bytes=self.limits.max_output_bytes))

    def build(self, scorecard_id: str, evaluations: Sequence[Mapping[str, Any]], *,
              comparisons: Sequence[ScorecardComparison] = (),
              ablations: Sequence[ScorecardAblation] = ()) -> dict[str, Any]:
        """Return JSON-ready summary, ordered independently of input ordering.

        Rates are fractions of *evaluation observations* and must not be
        presented as success rates over unique scenarios or independent trials.
        """
        _require(type(scorecard_id) is str and _ID.fullmatch(scorecard_id) is not None,
                 "invalid scorecard ID")
        for label, items, limit in (("evaluations", evaluations, self.limits.max_evaluations),
                                    ("comparisons", comparisons, self.limits.max_comparisons),
                                    ("ablations", ablations, self.limits.max_ablations)):
            _require(type(items) in (list, tuple) and
                     (1 if label == "evaluations" else 0) <= len(items) <= limit,
                     f"invalid {label} collection")
        indexed: dict[str, Mapping[str, Any]] = {}
        for report in evaluations:
            _require(type(report) is dict, "evaluation report must be a plain dictionary")
            _require(len(_encode(report)) <= self.limits.max_input_report_bytes,
                     "evaluation report too large")
            try:
                cases = self._regression._validate_report(report)
            except (NexusRegressionError, TypeError, KeyError, ValueError, RecursionError,
                    OverflowError) as exc:
                raise NexusScorecardError("invalid evaluation report") from exc
            _require(len(cases) <= self.limits.max_cases_per_evaluation,
                     "too many cases in evaluation")
            identifier = report["report_sha256"]
            _require(_digest(identifier) and identifier not in indexed,
                     "duplicate or invalid evaluation digest")
            indexed[identifier] = report

        grouped_product: dict[str, dict[str, int]] = {}
        grouped_category: dict[str, dict[str, int]] = {}
        overview = _counts()
        entries: list[dict[str, Any]] = []
        catalog_hashes: set[str] = set()
        suite_hashes: set[str] = set()
        for digest in sorted(indexed):
            report = indexed[digest]
            catalog_hashes.add(report["catalog_sha256"])
            selection = [(r["scenario_id"], r["case_sha256"]) for r in report["cases"]]
            suite = _hash(selection)
            suite_hashes.add(suite)
            case_counts = _counts()
            for case in report["cases"]:
                _tally(overview, case["status"])
                _tally(case_counts, case["status"])
                _tally(grouped_product.setdefault(case["product"], _counts()), case["status"])
                _tally(grouped_category.setdefault(case["category"], _counts()), case["status"])
            entries.append({"report_sha256": digest,
                            "catalog_sha256": report["catalog_sha256"],
                            "suite_sha256": suite,
                            "status": report["status"],
                            "metrics": _summary(case_counts)})

        reg_entries: list[dict[str, Any]] = []
        reg_seen: set[tuple[str, str]] = set()
        for item in comparisons:
            _require(isinstance(item, ScorecardComparison), "invalid comparison item")
            b, c, r = item.baseline_report, item.candidate_report, item.comparison_report
            _require(type(b) is type(c) is type(r) is dict, "invalid comparison input")
            _require(len(_encode(r)) <= self.limits.max_input_report_bytes,
                     "regression comparison too large")
            b_id, c_id = b.get("report_sha256"), c.get("report_sha256")
            _require(_digest(b_id) and _digest(c_id) and b_id != c_id,
                     "invalid comparison source identifiers")
            _require(b_id in indexed and c_id in indexed and
                     indexed[b_id] == b and indexed[c_id] == c,
                     "comparison references evaluations not present in scorecard")
            _require((b_id, c_id) not in reg_seen, "duplicate comparison")
            try:
                valid = self._regression.verify_comparison(r, b, c)
            except (NexusRegressionError, ValueError, TypeError, OverflowError, RecursionError):
                valid = False
            _require(valid, "comparison does not match its source reports")
            reg_seen.add((b_id, c_id))
            reg_entries.append({"comparison_sha256": r["comparison_sha256"],
                                "baseline_report_sha256": b_id,
                                "candidate_report_sha256": c_id,
                                "status": r["status"],
                                "catalog_changed": r["catalog_changed"],
                                "same_selected_suite": r["same_selected_suite"],
                                "counts": dict(r["counts"])})
        reg_entries.sort(key=lambda x: (x["baseline_report_sha256"], x["candidate_report_sha256"]))

        abl_entries: list[dict[str, Any]] = []
        abl_seen: set[str] = set()
        for item in ablations:
            _require(isinstance(item, ScorecardAblation), "invalid ablation item")
            report = item.study_report
            _require(type(report) is dict and len(_encode(report)) <= self.limits.max_input_report_bytes,
                     "invalid or oversized ablation report")
            _require(type(item.pairs) is tuple and 1 <= len(item.pairs) <= 32,
                     "invalid ablation trials")
            try:
                valid = self._ablation.verify(report, item.design, item.pairs)
            except (NexusAblationError, TypeError, ValueError, OverflowError, RecursionError):
                valid = False
            _require(valid, "ablation report does not match its source trials")
            for pair in item.pairs:
                for source in (pair.reference_report, pair.ablated_report):
                    identifier = source["report_sha256"]
                    _require(identifier in indexed and indexed[identifier] == source,
                             "ablation references an evaluation not present in scorecard")
            identifier = report["study_sha256"]
            _require(identifier not in abl_seen, "duplicate ablation study")
            abl_seen.add(identifier)
            abl_entries.append({"study_sha256": identifier,
                                "study_id": item.design.study_id,
                                "removed_component": item.design.removed_component,
                                "status": report["status"],
                                "pair_count": report["pair_count"],
                                "scenario_count": report["scenario_count"],
                                "totals": dict(report["totals"]),
                                "pass_rate_difference_reference_minus_ablated":
                                    report["pass_rate_difference_reference_minus_ablated"]})
        abl_entries.sort(key=lambda x: x["study_sha256"])

        flags = []
        if overview["failed"]:
            flags.append("failed_evaluations_present")
        if overview["inconclusive"]:
            flags.append("inconclusive_evaluations_present")
        if len(catalog_hashes) > 1:
            flags.append("different_catalog_snapshots")
        if len(suite_hashes) > 1:
            flags.append("different_selected_suites")
        if any(r["status"] == "regressions_detected" for r in reg_entries):
            flags.append("regressions_detected")
        if any(r["status"] == "review_required" for r in reg_entries):
            flags.append("regression_review_required")
        if any(a["status"] == "review_required" for a in abl_entries):
            flags.append("ablation_review_required")
        status = "review_required" if flags else "descriptive_complete"
        base = {"schema": SCORECARD_SCHEMA, "scorecard_id": scorecard_id,
                "assurance": ASSURANCE, "status": status,
                "evaluation_report_count": len(entries),
                "regression_report_count": len(reg_entries),
                "ablation_study_count": len(abl_entries),
                "distinct_catalog_snapshots": len(catalog_hashes),
                "distinct_selected_suites": len(suite_hashes),
                "aggregate": _summary(overview),
                "by_product": {p: _summary(grouped_product[p]) for p in sorted(grouped_product)},
                "by_category": {c: _summary(grouped_category[c]) for c in sorted(grouped_category)},
                "evaluations": entries,
                "regressions": reg_entries,
                "ablations": abl_entries,
                "review_flags": flags,
                "limitations": [
                    "source observations and experiment configuration labels are host-supplied and not independently authenticated",
                    "aggregated counts represent case observations across evaluation reports, not unique scenarios or independent trials",
                    "mixed suites or catalogs must not be interpreted as a directly comparable performance trend",
                    "ablation contrasts are descriptive and do not establish causality or statistical significance",
                    "SHA-256 checks internal integrity, not the accuracy or authenticity of observations",
                ]}
        out = {**base, "scorecard_sha256": _hash(base)}
        _require(len(_encode(out)) <= self.limits.max_output_bytes,
                 "scorecard exceeds output byte limit")
        return out

    def verify(self, scorecard: Mapping[str, Any], scorecard_id: str,
               evaluations: Sequence[Mapping[str, Any]], *,
               comparisons: Sequence[ScorecardComparison] = (),
               ablations: Sequence[ScorecardAblation] = ()) -> bool:
        """Recompute from source reports, not simply trust a rehashed summary."""
        try:
            if type(scorecard) is not dict or set(scorecard) != {
                "schema", "scorecard_id", "assurance", "status", "evaluation_report_count",
                "regression_report_count", "ablation_study_count", "distinct_catalog_snapshots",
                "distinct_selected_suites", "aggregate", "by_product", "by_category",
                "evaluations", "regressions", "ablations", "review_flags", "limitations",
                "scorecard_sha256"}:
                return False
            if len(_encode(scorecard)) > self.limits.max_output_bytes:
                return False
            original = scorecard.get("scorecard_sha256")
            if not _digest(original) or not hmac.compare_digest(original, _hash({
                key: value for key, value in scorecard.items() if key != "scorecard_sha256"})):
                return False
            return scorecard == self.build(scorecard_id, evaluations,
                                           comparisons=comparisons, ablations=ablations)
        except (NexusScorecardError, NexusRegressionError, NexusAblationError,
                TypeError, KeyError, ValueError, OverflowError, RecursionError):
            return False


__all__ = ["SCORECARD_SCHEMA", "ASSURANCE", "NexusScorecardError", "ScorecardLimits",
           "ScorecardComparison", "ScorecardAblation", "NexusScorecard"]
