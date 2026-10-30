"""Deterministic comparison of Nexus Laboratory evaluation reports.

Repository destination: src/aegisforge/nexus/lab/regression_detector.py

This module only compares bounded, integrity-checked evaluation *reports*.
It never launches tests, tools, containers, model requests or file writes.

Caveats:
- Report hashes prove internal consistency, NOT authentic external evidence.
- Different case digests are NOT comparable, even when IDs match.
- Missing observations and changed suites are not silently treated as passes.
- Durations are heuristic alerts, NOT statistically significant performance claims.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Mapping

from .evaluation_runner import NexusEvaluationRunner, REPORT_SCHEMA, OBSERVATION_SCHEMA
from .scenario_catalog import PRODUCTS, CATEGORIES, MODES, OPERATORS

REGRESSION_SCHEMA = "aegisforge.nexus.lab.regression_report.v1"
_HEX = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_FIELD = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}(\.[a-zA-Z_][a-zA-Z0-9_]{0,63}){0,7}$")
_SOURCE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_REF = re.compile(r"^[a-z][a-z0-9_.:/-]{2,159}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_STATUSES = {"pass", "failed", "inconclusive"}
_CHECK_STATUSES = {"pass", "fail", "inconclusive"}
_REASON_CODES = {"observation_missing", "host_reported_error", "host_reported_unavailable",
                 "time_budget_exceeded", "expectation_failed"}
_ROW_KEYS = {"scenario_id", "case_sha256", "revision", "product", "category", "mode",
             "status", "reason_codes", "checks", "observation"}
_CHECK_KEYS = {"path", "operator", "status", "code"}
_OBS_KEYS = {"schema", "scenario_id", "case_sha256", "source_id", "evidence_ref", "status",
             "duration_ms", "failure_code", "payload_sha256"}
_METRIC_KEYS = {"total_scenarios", "passed", "failed", "inconclusive", "pass_rate",
                "evaluated_pass_rate", "expectations_total", "expectations_passed",
                "expectations_failed", "expectations_inconclusive"}
_COUNTS = ("comparable", "regressed", "improved", "unchanged", "coverage_lost", "coverage_gained",
           "new_failure", "changed_spec", "added", "removed", "performance_alerts")


class NexusRegressionError(ValueError):
    """Reports are invalid, inconsistent, too large, or cannot be compared."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise NexusRegressionError("comparison data cannot be encoded safely") from exc


def _hash(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _hex(value: Any) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _bounded_id(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _ensure(condition: bool, message: str) -> None:
    if not condition:
        raise NexusRegressionError(message)


@dataclass(frozen=True, slots=True)
class RegressionLimits:
    max_cases: int = 256
    max_report_bytes: int = 2_000_000
    max_output_bytes: int = 2_000_000
    slowdown_percent: int = 25
    slowdown_min_ms: int = 250

    def __post_init__(self) -> None:
        for name, lo, hi in (("max_cases", 1, 2048),
                              ("max_report_bytes", 4096, 8_000_000),
                              ("max_output_bytes", 4096, 8_000_000),
                              ("slowdown_percent", 1, 1000),
                              ("slowdown_min_ms", 1, 600_000)):
            val = getattr(self, name)
            if type(val) is not int or not lo <= val <= hi:
                raise NexusRegressionError(f"invalid {name}")


class NexusRegressionDetector:
    """Compare two EvaluationRunner reports without running anything.

    Status `no_regressions_detected` applies only to comparable, covered cases.
    An unknown/missing/changed case makes the summary `review_required`.
    """

    def __init__(self, *, limits: RegressionLimits | None = None) -> None:
        self.limits = limits or RegressionLimits()
        if not isinstance(self.limits, RegressionLimits):
            raise NexusRegressionError("invalid regression limits")

    def _validate_report(self, report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        _ensure(isinstance(report, dict), "report must be a plain JSON dictionary")
        _ensure(len(_canonical(report)) <= self.limits.max_report_bytes, "report exceeds byte limit")
        _ensure(NexusEvaluationRunner.verify_report(report), "invalid evaluation report or digest")
        _ensure(report["schema"] == REPORT_SCHEMA, "unsupported evaluation report schema")
        _ensure(_hex(report["report_sha256"]) and _hex(report["plan_sha256"])
                and _hex(report["catalog_sha256"]), "invalid report hash")
        _ensure(report["assurance"] == "host_supplied_observations_not_independently_authenticated",
                "unsupported assurance label")
        rows = report["cases"]
        _ensure(1 <= len(rows) <= self.limits.max_cases, "invalid number of cases")
        known: dict[str, dict[str, Any]] = {}
        pass_n = fail_n = unknown_n = 0
        pc = fc = uc = 0
        for row in rows:
            _ensure(type(row) is dict and set(row) == _ROW_KEYS, "invalid case fields")
            sid = row["scenario_id"]
            _ensure(_bounded_id(sid) and sid not in known, "duplicate or invalid case ID")
            _ensure(_hex(row["case_sha256"]), "invalid case digest")
            _ensure(type(row["revision"]) is int and 1 <= row["revision"] <= 1_000_000,
                    "invalid case revision")
            _ensure(type(row["product"]) is str and row["product"] in PRODUCTS
                    and type(row["category"]) is str and row["category"] in CATEGORIES
                    and type(row["mode"]) is str and row["mode"] in MODES, "invalid case classification")
            _ensure(type(row["status"]) is str and row["status"] in _STATUSES, "invalid case verdict")
            reasons = row["reason_codes"]
            _ensure(type(reasons) is list and len(reasons) <= 4
                    and all(type(x) is str and x in _REASON_CODES for x in reasons)
                    and len(set(reasons)) == len(reasons),
                    "invalid case reason codes")
            checks = row["checks"]
            _ensure(type(checks) is list and 1 <= len(checks) <= 32, "invalid checks")
            check_ids: set[tuple[str, str]] = set()
            for check in checks:
                _ensure(type(check) is dict and set(check) == _CHECK_KEYS, "invalid check fields")
                _ensure(type(check["path"]) is str and _FIELD.fullmatch(check["path"]) is not None
                        and type(check["operator"]) is str and check["operator"] in OPERATORS
                        and type(check["status"]) is str and check["status"] in _CHECK_STATUSES,
                        "invalid check contents")
                _ensure(type(check["code"]) is str and check["code"] in {"condition_met", "condition_not_met", "field_missing",
                                          "field_present", "observation_missing", "not_evaluated"},
                        "unsupported check result code")
                check_key = (check["path"], check["operator"])
                _ensure(check_key not in check_ids, "duplicate check")
                check_ids.add(check_key)
                _ensure((check["status"] == "pass" and check["code"] in {"condition_met", "field_present"})
                        or (check["status"] == "fail" and check["code"] in {"condition_not_met", "field_missing"})
                        or (check["status"] == "inconclusive" and check["code"] in
                            {"observation_missing", "not_evaluated"}), "check verdict/code conflict")
                pc += check["status"] == "pass"
                fc += check["status"] == "fail"
                uc += check["status"] == "inconclusive"
            obs = row["observation"]
            if obs is None:
                _ensure(row["status"] == "inconclusive" and reasons == ["observation_missing"]
                        and all(x["code"] == "observation_missing" for x in checks),
                        "missing observation case inconsistent")
            else:
                _ensure(type(obs) is dict and set(obs) == _OBS_KEYS, "invalid observation metadata")
                _ensure(obs["schema"] == OBSERVATION_SCHEMA and obs["scenario_id"] == sid
                        and obs["case_sha256"] == row["case_sha256"],
                        "observation does not match case")
                _ensure(_bounded_id(obs["source_id"]) and type(obs["evidence_ref"]) is str
                        and _REF.fullmatch(obs["evidence_ref"]) is not None
                        and ".." not in obs["evidence_ref"] and "//" not in obs["evidence_ref"]
                        and _hex(obs["payload_sha256"]), "invalid observation identifiers")
                _ensure(type(obs["duration_ms"]) is int and 0 <= obs["duration_ms"] <= 3_600_000,
                        "invalid duration")
                _ensure(type(obs["status"]) is str and obs["status"] in {"observed", "error", "unavailable"}, "invalid observation status")
                if obs["status"] == "observed":
                    _ensure(obs["failure_code"] is None
                            and (row["status"] == "pass" and not reasons
                                 and all(x["status"] == "pass" for x in checks)
                                 or row["status"] == "failed"
                                 and ("expectation_failed" in reasons or "time_budget_exceeded" in reasons)
                                 and ("expectation_failed" in reasons) == any(x["status"] == "fail" for x in checks))
                            and "host_reported_error" not in reasons
                            and "host_reported_unavailable" not in reasons
                            and "observation_missing" not in reasons,
                            "observed case inconsistent")
                else:
                    _ensure(type(obs["failure_code"]) is str and _CODE.fullmatch(obs["failure_code"]) is not None
                            and all(x["status"] == "inconclusive" and x["code"] == "not_evaluated"
                                    for x in checks), "invalid error observation")
                    if obs["status"] == "error":
                        _ensure(row["status"] == "failed" and reasons == ["host_reported_error"],
                                "error observation status conflict")
                    else:
                        _ensure(row["status"] == "inconclusive"
                                and reasons == ["host_reported_unavailable"],
                                "unavailable observation status conflict")
            pass_n += row["status"] == "pass"
            fail_n += row["status"] == "failed"
            unknown_n += row["status"] == "inconclusive"
            known[sid] = row
        _ensure(list(known) == sorted(known), "case ordering is not canonical")
        metrics = report["metrics"]
        _ensure(type(metrics) is dict and set(metrics) == _METRIC_KEYS, "invalid metrics")
        expected_ints = dict(total_scenarios=len(rows), passed=pass_n, failed=fail_n,
                             inconclusive=unknown_n, expectations_total=pc+fc+uc,
                             expectations_passed=pc, expectations_failed=fc,
                             expectations_inconclusive=uc)
        _ensure(all(type(metrics[key]) is int and metrics[key] == val
                    for key, val in expected_ints.items()), "metric counters inconsistent")
        _ensure(type(metrics["pass_rate"]) is float and metrics["pass_rate"] == round(pass_n/len(rows), 6),
                "pass rate mismatch")
        expected_rate = round(pass_n/(pass_n+fail_n), 6) if pass_n+fail_n else None
        _ensure(metrics["evaluated_pass_rate"] == expected_rate
                and (expected_rate is None or type(metrics["evaluated_pass_rate"]) is float),
                "evaluated pass rate mismatch")
        _ensure(report["status"] == ("failed" if fail_n else "inconclusive" if unknown_n else "passed"),
                "report status inconsistent")
        return known

    def compare(self, baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
        """Return deterministic, digest-bound, metadata-only comparison."""
        before = self._validate_report(baseline)
        after = self._validate_report(candidate)
        counts = {key: 0 for key in _COUNTS}
        rows: list[dict[str, Any]] = []
        by_product: dict[str, dict[str, int]] = {}
        for sid in sorted(before.keys() | after.keys()):
            b = before.get(sid)
            c = after.get(sid)
            item = c if c is not None else b
            assert item is not None
            product = item["product"]
            product_totals = by_product.setdefault(product, {key: 0 for key in _COUNTS})
            event = "unchanged"
            signals: list[str] = []
            check_regressions: list[dict[str, str]] = []
            if b is None:
                event = "added"
            elif c is None:
                event = "removed"
            elif b["case_sha256"] != c["case_sha256"]:
                event = "changed_spec"
            else:
                _ensure(all(b[key] == c[key] for key in ("product", "category", "mode", "revision")),
                        "same case digest has conflicting metadata")
                counts["comparable"] += 1
                product_totals["comparable"] += 1
                before_status, after_status = b["status"], c["status"]
                if before_status == "pass" and after_status == "failed":
                    event = "regressed"
                elif before_status == "failed" and after_status == "pass":
                    event = "improved"
                elif after_status == "inconclusive" and before_status != "inconclusive":
                    event = "coverage_lost"
                elif before_status == "inconclusive" and after_status == "pass":
                    event = "coverage_gained"
                elif before_status == "inconclusive" and after_status == "failed":
                    event = "new_failure"
                old_checks = {(x["path"], x["operator"]): x["status"] for x in b["checks"]}
                new_checks = {(x["path"], x["operator"]): x["status"] for x in c["checks"]}
                _ensure(old_checks.keys() == new_checks.keys(), "identical case has inconsistent checks")
                for key in sorted(old_checks):
                    if old_checks[key] == "pass" and new_checks[key] == "fail":
                        check_regressions.append({"path": key[0], "operator": key[1],
                                                  "previous": "pass", "current": "fail"})
                if check_regressions:
                    signals.append("check_regression")
                    if event == "unchanged":
                        event = "regressed"
                old_obs, new_obs = b["observation"], c["observation"]
                if (old_obs is not None and new_obs is not None
                        and old_obs["status"] == new_obs["status"] == "observed"):
                    old_ms, new_ms = old_obs["duration_ms"], new_obs["duration_ms"]
                    if (old_ms > 0 and new_ms-old_ms >= self.limits.slowdown_min_ms
                            and new_ms * 100 >= old_ms * (100 + self.limits.slowdown_percent)):
                        signals.append("duration_increase_alert")
                        counts["performance_alerts"] += 1
                        product_totals["performance_alerts"] += 1
            counts[event] += 1
            product_totals[event] += 1
            rows.append({"scenario_id": sid, "product": product,
                         "case_sha256_previous": b["case_sha256"] if b else None,
                         "case_sha256_current": c["case_sha256"] if c else None,
                         "revision_previous": b["revision"] if b else None,
                         "revision_current": c["revision"] if c else None,
                         "status_previous": b["status"] if b else None,
                         "status_current": c["status"] if c else None,
                         "classification": event, "signals": signals,
                         "regressed_checks": check_regressions})
        if counts["regressed"]:
            status = "regressions_detected"
        elif (any(counts[key] for key in ("coverage_lost", "coverage_gained", "new_failure",
                                            "changed_spec", "added", "removed", "performance_alerts"))
              or any(row["status_current"] == "inconclusive" for row in rows)):
            status = "review_required"
        else:
            status = "no_regressions_detected"
        paired_same_spec = counts["comparable"]
        suite_changed = baseline["catalog_sha256"] != candidate["catalog_sha256"]
        base = {"schema": REGRESSION_SCHEMA,
                "assurance": "comparison_of_host_supplied_unverified_observations",
                "status": status, "baseline_report_sha256": baseline["report_sha256"],
                "candidate_report_sha256": candidate["report_sha256"],
                "baseline_catalog_sha256": baseline["catalog_sha256"],
                "candidate_catalog_sha256": candidate["catalog_sha256"],
                "catalog_changed": suite_changed,
                "same_selected_suite": counts["added"] == counts["removed"] == counts["changed_spec"] == 0,
                "comparable_fraction": round(paired_same_spec / len(rows), 6),
                "counts": counts,
                "by_product": {key: by_product[key] for key in sorted(by_product)},
                "cases": rows}
        result = {**base, "comparison_sha256": _hash(base)}
        _ensure(len(_canonical(result)) <= self.limits.max_output_bytes, "comparison exceeds output byte limit")
        return result

    def verify_comparison(self, result: Mapping[str, Any], baseline: Mapping[str, Any],
                          candidate: Mapping[str, Any]) -> bool:
        """Recompute and compare from reports; not proof of external truth."""
        try:
            if type(result) is not dict or set(result) != {
                "schema", "assurance", "status", "baseline_report_sha256", "candidate_report_sha256",
                "baseline_catalog_sha256", "candidate_catalog_sha256", "catalog_changed",
                "same_selected_suite", "comparable_fraction", "counts", "by_product", "cases",
                "comparison_sha256"}:
                return False
            if not _hex(result["comparison_sha256"]) or len(_canonical(result)) > self.limits.max_output_bytes:
                return False
            if not hmac.compare_digest(result["comparison_sha256"], _hash({
                k: v for k, v in result.items() if k != "comparison_sha256"})):
                return False
            return result == self.compare(baseline, candidate)
        except (NexusRegressionError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return False

__all__ = ["REGRESSION_SCHEMA", "NexusRegressionError", "RegressionLimits", "NexusRegressionDetector"]
