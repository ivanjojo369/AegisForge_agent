"""Non-executing, paired ablation analysis for AegisForge Nexus Laboratory.

Destination: src/aegisforge/nexus/lab/ablation_study.py

Compares two reports for the SAME scenario definitions: a reference config
with one component enabled, and an ablated config with that component disabled.
The host is responsible for obtaining reports independently and ensuring that
its config/seed/environment declarations reflect actual runs.

This module NEVER invokes a model, runs a scenario, writes a file, starts a
process, or grants approval. SHA-256 is a consistency check, NOT authenticity.
Paired differences are descriptive, NOT causal evidence or significance tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Mapping, Sequence

from .regression_detector import NexusRegressionDetector, NexusRegressionError, RegressionLimits

ABLATION_SCHEMA = "aegisforge.nexus.lab.ablation_study.v1"
_IDENT = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


class NexusAblationError(ValueError):
    """An ablation design or its underlying evaluation data is not trustworthy enough."""


def _bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise NexusAblationError("invalid JSON data") from exc


def _sha(value: Any) -> str:
    return sha256(_bytes(value)).hexdigest()


def _identifier(value: Any, field: str) -> str:
    if type(value) is not str or _IDENT.fullmatch(value) is None:
        raise NexusAblationError(f"invalid {field}")
    return value


def _digest(value: Any, field: str) -> str:
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise NexusAblationError(f"invalid {field}")
    return value


def _check(test: bool, reason: str) -> None:
    if not test:
        raise NexusAblationError(reason)


@dataclass(frozen=True, slots=True)
class AblationLimits:
    max_pairs: int = 32
    max_cases_per_pair: int = 256
    max_report_bytes: int = 2_000_000
    max_output_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        for name, lower, upper in (("max_pairs", 1, 128),
                                   ("max_cases_per_pair", 1, 2048),
                                   ("max_report_bytes", 4096, 8_000_000),
                                   ("max_output_bytes", 4096, 8_000_000)):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise NexusAblationError(f"invalid {name}")


@dataclass(frozen=True, slots=True)
class AblationDesign:
    """Declared configurations: ablated side removes exactly one component.

    This cannot verify that the deployed configurations match these labels.
    """
    study_id: str
    removed_component: str
    reference_components: tuple[str, ...]
    ablated_components: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.study_id, "study ID")
        _identifier(self.removed_component, "component ID")
        for name in ("reference_components", "ablated_components"):
            items = getattr(self, name)
            _check(type(items) is tuple and 0 <= len(items) <= 64
                   and all(type(x) is str and _IDENT.fullmatch(x) for x in items)
                   and tuple(sorted(set(items))) == items, f"invalid {name}")
        _check(self.removed_component in self.reference_components,
               "component must be present in reference")
        _check(self.ablated_components == tuple(x for x in self.reference_components
                                                if x != self.removed_component),
               "ablated configuration must remove exactly the chosen component")

    def to_dict(self) -> dict[str, Any]:
        return {"study_id": self.study_id,
                "removed_component": self.removed_component,
                "reference_components": list(self.reference_components),
                "ablated_components": list(self.ablated_components)}


@dataclass(frozen=True, slots=True)
class AblationPair:
    """A declared paired trial with reports provided by an external host.

    Fingerprints and seeds SHOULD be produced from the actual run setup;
    a matching declaration does not prove that setup was truly identical.
    """
    pair_id: str
    reference_report: Mapping[str, Any]
    ablated_report: Mapping[str, Any]
    reference_environment_sha256: str
    ablated_environment_sha256: str
    reference_input_sha256: str
    ablated_input_sha256: str
    reference_seed: int
    ablated_seed: int

    def __post_init__(self) -> None:
        _identifier(self.pair_id, "pair ID")
        for name in ("reference_environment_sha256", "ablated_environment_sha256",
                     "reference_input_sha256", "ablated_input_sha256"):
            _digest(getattr(self, name), name)
        for name in ("reference_seed", "ablated_seed"):
            val = getattr(self, name)
            _check(type(val) is int and 0 <= val < 2**63, f"invalid {name}")
        _check(isinstance(self.reference_report, Mapping) and
               isinstance(self.ablated_report, Mapping), "reports must be mappings")


class NexusAblationStudy:
    """Descriptive paired comparison; no causal or statistical guarantees."""

    def __init__(self, *, limits: AblationLimits | None = None) -> None:
        self.limits = limits if limits is not None else AblationLimits()
        if not isinstance(self.limits, AblationLimits):
            raise NexusAblationError("invalid limits")
        self._regression = NexusRegressionDetector(limits=RegressionLimits(
            max_cases=self.limits.max_cases_per_pair,
            max_report_bytes=self.limits.max_report_bytes,
            max_output_bytes=self.limits.max_output_bytes))

    def analyze(self, design: AblationDesign, pairs: Sequence[AblationPair]) -> dict[str, Any]:
        """Compare paired reports; never execute the underlying scenarios."""
        if not isinstance(design, AblationDesign):
            raise NexusAblationError("invalid ablation design")
        if type(pairs) not in (list, tuple) or not 1 <= len(pairs) <= self.limits.max_pairs:
            raise NexusAblationError("invalid pair collection")
        identifiers: set[str] = set()
        seen_reports: set[str] = set()
        reference_case_set: tuple[tuple[str, str], ...] | None = None
        pair_summaries: list[dict[str, Any]] = []
        scenario_counts: dict[str, dict[str, Any]] = {}
        product_counts: dict[str, dict[str, int]] = {}
        totals = {"reference_pass": 0, "ablated_pass": 0, "reference_failed": 0,
                  "ablated_failed": 0, "reference_inconclusive": 0, "ablated_inconclusive": 0,
                  "loss_when_removed": 0, "gain_when_removed": 0,
                  "both_pass": 0, "both_failed": 0, "uncertain_pairs": 0,
                  "duration_pairs": 0, "duration_increase_ms_total": 0}
        expected_catalog: str | None = None

        for pair in pairs:
            _check(isinstance(pair, AblationPair), "pair must be an AblationPair")
            _check(pair.pair_id not in identifiers, "duplicate pair ID")
            identifiers.add(pair.pair_id)
            _check(pair.reference_environment_sha256 == pair.ablated_environment_sha256,
                   "paired environments differ")
            _check(pair.reference_input_sha256 == pair.ablated_input_sha256,
                   "paired inputs differ")
            _check(pair.reference_seed == pair.ablated_seed, "paired seeds differ")
            before, after = pair.reference_report, pair.ablated_report
            _check(type(before) is dict and type(after) is dict, "reports must be plain dictionaries")
            _check(len(_bytes(before)) <= self.limits.max_report_bytes and
                   len(_bytes(after)) <= self.limits.max_report_bytes, "report exceeds size limit")
            try:
                comparison = self._regression.compare(before, after)
            except (NexusRegressionError, ValueError, TypeError, RecursionError) as exc:
                raise NexusAblationError("invalid or inconsistent evaluation reports") from exc
            _check(comparison["same_selected_suite"] and
                   comparison["counts"]["comparable"] == len(before["cases"]) == len(after["cases"]),
                   "paired reports must contain identical scenario definitions")
            _check(before["catalog_sha256"] == after["catalog_sha256"],
                   "paired reports use different catalogs")
            if expected_catalog is None:
                expected_catalog = before["catalog_sha256"]
            _check(before["catalog_sha256"] == expected_catalog,
                   "all pairs must refer to the same catalog")
            scenarios = tuple((row["scenario_id"], row["case_sha256"]) for row in before["cases"])
            _check(reference_case_set is None or scenarios == reference_case_set,
                   "all pairs must evaluate the same scenario selection")
            reference_case_set = scenarios
            ids = (before["report_sha256"], after["report_sha256"])
            _check(ids[0] != ids[1], "reference and ablated reports must be distinct")
            _check(not any(k in seen_reports for k in ids),
                   "evaluation report reused across trials")
            seen_reports.update(ids)
            transitions: dict[str, int] = {"loss_when_removed": 0, "gain_when_removed": 0,
                                           "uncertain_pairs": 0, "both_pass": 0,
                                           "both_failed": 0, "duration_pairs": 0}
            for b, a in zip(before["cases"], after["cases"]):
                sid = b["scenario_id"]
                _check(sid == a["scenario_id"] and b["case_sha256"] == a["case_sha256"],
                       "scenario row mismatch")
                product = b["product"]
                t = scenario_counts.setdefault(sid, {"scenario_id": sid, "product": product,
                     "case_sha256": b["case_sha256"], "loss_when_removed": 0,
                     "gain_when_removed": 0, "both_pass": 0, "both_failed": 0,
                     "uncertain_pairs": 0, "duration_pairs": 0,
                     "duration_increase_ms_total": 0})
                p = product_counts.setdefault(product, {"loss_when_removed": 0,
                     "gain_when_removed": 0, "both_pass": 0, "both_failed": 0,
                     "uncertain_pairs": 0})
                old, new = b["status"], a["status"]
                totals["reference_pass"] += old == "pass"
                totals["ablated_pass"] += new == "pass"
                totals["reference_failed"] += old == "failed"
                totals["ablated_failed"] += new == "failed"
                totals["reference_inconclusive"] += old == "inconclusive"
                totals["ablated_inconclusive"] += new == "inconclusive"
                if old == "pass" and new == "failed":
                    classification = "loss_when_removed"
                elif old == "failed" and new == "pass":
                    classification = "gain_when_removed"
                elif old == new == "pass":
                    classification = "both_pass"
                elif old == new == "failed":
                    classification = "both_failed"
                else:
                    classification = "uncertain_pairs"
                totals[classification] += 1
                transitions[classification] += 1
                p[classification] += 1
                t[classification] += 1
                obs_b, obs_a = b["observation"], a["observation"]
                if (obs_b is not None and obs_a is not None and
                        obs_b["status"] == obs_a["status"] == "observed"):
                    delta = obs_a["duration_ms"] - obs_b["duration_ms"]
                    t["duration_pairs"] += 1
                    t["duration_increase_ms_total"] += delta
                    totals["duration_pairs"] += 1
                    totals["duration_increase_ms_total"] += delta
                    transitions["duration_pairs"] += 1
            pair_summaries.append({"pair_id": pair.pair_id,
                "environment_sha256": pair.reference_environment_sha256,
                "input_sha256": pair.reference_input_sha256,
                "seed": pair.reference_seed,
                "reference_report_sha256": ids[0],
                "ablated_report_sha256": ids[1],
                "comparison_sha256": comparison["comparison_sha256"],
                "transitions": transitions})

        total_obs = len(pairs) * len(reference_case_set or ())
        missing = totals["uncertain_pairs"] > 0
        # A large difference is still descriptive. The host must check for confounders.
        status = "review_required" if missing else "descriptive_complete"
        scenario_out = [scenario_counts[k] for k in sorted(scenario_counts)]
        analysis = {"schema": ABLATION_SCHEMA,
            "assurance": "paired_host_reported_data_not_causally_authenticated",
            "status": status,
            "design": design.to_dict(),
            "design_sha256": _sha(design.to_dict()),
            "catalog_sha256": expected_catalog,
            "pair_count": len(pairs),
            "scenario_count": len(reference_case_set or ()),
            "paired_observations": total_obs,
            "totals": totals,
            "pass_rate_reference": round(totals["reference_pass"]/total_obs, 6),
            "pass_rate_ablated": round(totals["ablated_pass"]/total_obs, 6),
            "pass_rate_difference_reference_minus_ablated": round(
                (totals["reference_pass"] - totals["ablated_pass"])/total_obs, 6),
            "by_product": {k: product_counts[k] for k in sorted(product_counts)},
            "by_scenario": scenario_out,
            "pairs": sorted(pair_summaries, key=lambda item: item["pair_id"]),
            "interpretation": "descriptive_only_no_causal_attribution_or_significance_test",
            "limitations": ["host supplied reports and configuration labels are not authenticated",
                            "matched fingerprints do not prove equal runtime conditions",
                            "repeated trials are not assumed statistically independent",
                            "unknown observations are never counted as passes"]}
        out = {**analysis, "study_sha256": _sha(analysis)}
        _check(len(_bytes(out)) <= self.limits.max_output_bytes,
               "ablation report exceeds byte limit")
        return out

    def verify(self, report: Mapping[str, Any], design: AblationDesign,
               pairs: Sequence[AblationPair]) -> bool:
        """Recalculate from source reports, not merely verify a self-reported hash."""
        try:
            if (type(report) is not dict or "study_sha256" not in report
                    or type(report["study_sha256"]) is not str):
                return False
            if len(_bytes(report)) > self.limits.max_output_bytes:
                return False
            clean = {k: v for k, v in report.items() if k != "study_sha256"}
            if not hmac.compare_digest(report["study_sha256"], _sha(clean)):
                return False
            return report == self.analyze(design, pairs)
        except (NexusAblationError, NexusRegressionError, ValueError, TypeError,
                KeyError, OverflowError, RecursionError):
            return False


__all__ = ["ABLATION_SCHEMA", "NexusAblationError", "AblationLimits",
           "AblationDesign", "AblationPair", "NexusAblationStudy"]
