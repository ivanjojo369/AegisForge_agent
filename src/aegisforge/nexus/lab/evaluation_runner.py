"""Deterministic, non-executing evaluation of Colossus scenario observations.

Repository destination: src/aegisforge/nexus/lab/evaluation_runner.py

The trusted host supplies bounded observations and configures source IDs. This
module NEVER runs a scenario, invokes Nebius, starts Docker, reads the workspace,
or grants authorization. A pass means the *supplied observations* satisfied the
scenario's declarative criteria; it does not prove that evidence is authentic.

Only metadata, verdicts, and hashes leave this module in result dictionaries.
Do not provide live credentials, sensitive text, or private user data as input.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import hmac
import json
import math
import re
from typing import Any, Mapping, Sequence

from .scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation,
    NexusScenarioCatalogError, _canonical as _catalog_canonical,
    _valid_json as _catalog_validate_json,
)

PLAN_SCHEMA = "aegisforge.nexus.lab.evaluation_plan.v1"
REPORT_SCHEMA = "aegisforge.nexus.lab.evaluation_report.v1"
OBSERVATION_SCHEMA = "aegisforge.nexus.lab.observation.v1"
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SOURCE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_EVIDENCE = re.compile(r"^[a-z][a-z0-9_.:/-]{2,159}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_HEX = re.compile(r"^[a-f0-9]{64}$")
_MISSING = object()


class NexusEvaluationError(ValueError):
    """Evaluation cannot proceed on inconsistent or untrusted input."""


def _canonical(value: Any) -> bytes:
    try:
        return _catalog_canonical(value)
    except (NexusScenarioCatalogError, TypeError, ValueError, RecursionError) as exc:
        raise NexusEvaluationError("invalid JSON-compatible evaluation data") from exc


def _hash(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _id(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise NexusEvaluationError(f"invalid {label}")
    return value


@dataclass(frozen=True, slots=True)
class EvaluationLimits:
    max_cases: int = 128
    max_observation_bytes: int = 32_000
    max_report_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        for name, low, high in (("max_cases", 1, 2048),
                                ("max_observation_bytes", 256, 32_000),
                                ("max_report_bytes", 4096, 8_000_000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise NexusEvaluationError(f"invalid {name}")


@dataclass(frozen=True, slots=True, init=False)
class EvaluationObservation:
    """Host-reported observation. The digest binds bytes, not their truth.

    status: observed | error | unavailable. Only observed payloads are scored.
    Evidence reference is an opaque local identifier, never a signed URL.
    """

    scenario_id: str
    case_sha256: str
    source_id: str
    evidence_ref: str
    status: str
    duration_ms: int
    failure_code: str | None
    payload_sha256: str
    _payload: bytes = field(repr=False)

    def __init__(self, scenario_id: str, case_sha256: str, *, source_id: str,
                 evidence_ref: str, payload: Mapping[str, Any] | None = None,
                 status: str = "observed", duration_ms: int = 0,
                 failure_code: str | None = None) -> None:
        _id(scenario_id, _ID, "scenario ID")
        _id(source_id, _SOURCE, "source ID")
        _id(evidence_ref, _EVIDENCE, "evidence reference")
        if "://" in evidence_ref or "//" in evidence_ref or ".." in evidence_ref:
            raise NexusEvaluationError("evidence reference must be a local identifier")
        if not isinstance(case_sha256, str) or _HEX.fullmatch(case_sha256) is None:
            raise NexusEvaluationError("invalid case digest")
        if status not in {"observed", "error", "unavailable"}:
            raise NexusEvaluationError("unsupported observation status")
        if type(duration_ms) is not int or not 0 <= duration_ms <= 3_600_000:
            raise NexusEvaluationError("invalid observation duration")
        if status == "observed":
            if failure_code is not None or type(payload) is not dict:
                raise NexusEvaluationError("observed status requires a payload and no failure code")
        else:
            if payload is not None or not isinstance(failure_code, str) or _CODE.fullmatch(failure_code) is None:
                raise NexusEvaluationError("non-observed status requires a safe failure code, no payload")
        try:
            encoded = _catalog_validate_json(payload if payload is not None else {}, size_limit=32_000)
        except NexusScenarioCatalogError as exc:
            raise NexusEvaluationError("observation payload rejected") from exc
        for name, val in (("scenario_id", scenario_id), ("case_sha256", case_sha256),
                          ("source_id", source_id), ("evidence_ref", evidence_ref),
                          ("status", status), ("duration_ms", duration_ms),
                          ("failure_code", failure_code), ("_payload", encoded),
                          ("payload_sha256", sha256(encoded).hexdigest())):
            object.__setattr__(self, name, val)

    @property
    def payload(self) -> dict[str, Any]:
        """Defensive copy; never include this in user-facing reports."""
        return json.loads(self._payload)

    def summary(self) -> dict[str, Any]:
        """Safe identifiers only; no actual observation data."""
        return {"schema": OBSERVATION_SCHEMA, "scenario_id": self.scenario_id,
                "case_sha256": self.case_sha256, "source_id": self.source_id,
                "evidence_ref": self.evidence_ref, "status": self.status,
                "duration_ms": self.duration_ms, "failure_code": self.failure_code,
                "payload_sha256": self.payload_sha256}


@dataclass(frozen=True, slots=True)
class EvaluationPlan:
    """Snapshot-bound, non-executing plan; owned by one EvaluationRunner."""

    catalog_sha256: str
    plan_sha256: str
    allow_external: bool
    cases: tuple[ScenarioCase, ...] = field(repr=False)
    _owner: object = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": PLAN_SCHEMA, "catalog_sha256": self.catalog_sha256,
                "plan_sha256": self.plan_sha256, "allow_external": self.allow_external,
                "cases": [{"scenario_id": x.scenario_id, "case_sha256": x.case_sha256,
                           "revision": x.revision, "mode": x.mode} for x in self.cases],
                "executed": False}


def _at_path(root: Mapping[str, Any], path: str) -> Any:
    curr: Any = root
    for part in path.split("."):
        if type(curr) is not dict or part not in curr:
            return _MISSING
        curr = curr[part]
    return curr


def _strict_equal(left: Any, right: Any) -> bool:
    """Compare JSON values including nested data without conflating bool and int."""
    if type(left) is not type(right):
        return False
    if type(left) is list:
        return len(left) == len(right) and all(_strict_equal(a, b) for a, b in zip(left, right))
    if type(left) is dict:
        return left.keys() == right.keys() and all(_strict_equal(left[k], right[k]) for k in left)
    return left == right


def _test_expectation(expectation: ScenarioExpectation, data: Mapping[str, Any]) -> tuple[str, str]:
    value = _at_path(data, expectation.path)
    if expectation.operator == "exists":
        return ("pass", "field_present") if value is not _MISSING else ("fail", "field_missing")
    if value is _MISSING:
        return "fail", "field_missing"
    expected = expectation.expected
    op = expectation.operator
    if op == "equals":
        ok = _strict_equal(value, expected)
    elif op == "not_equals":
        ok = not _strict_equal(value, expected)
    elif op in {"gte", "lte"}:
        ok = type(value) in (int, float) and math.isfinite(value) and (value >= expected if op == "gte" else value <= expected)
    elif op == "contains":
        if type(value) is str and type(expected) is str:
            ok = expected in value
        elif type(value) is list:
            ok = any(_strict_equal(item, expected) for item in value)
        else:
            ok = False
    else:
        raise NexusEvaluationError("unsupported scenario expectation")
    return ("pass", "condition_met") if ok else ("fail", "condition_not_met")


class NexusEvaluationRunner:
    """Score host-supplied observations, never execute any scenario.

    A trusted_source ID is a configuration assertion by the host, NOT a proof of
    independence, authenticity, or correctness. Models must not choose it.
    """

    def __init__(self, catalog: NexusScenarioCatalog, *,
                 trusted_sources: Sequence[str] = (), limits: EvaluationLimits | None = None) -> None:
        if not isinstance(catalog, NexusScenarioCatalog):
            raise NexusEvaluationError("catalog must be a NexusScenarioCatalog")
        self.catalog = catalog
        self.limits = limits or EvaluationLimits()
        if not isinstance(self.limits, EvaluationLimits):
            raise NexusEvaluationError("invalid evaluation limits")
        if not isinstance(trusted_sources, (list, tuple, frozenset)) or len(trusted_sources) > 64:
            raise NexusEvaluationError("invalid source allowlist")
        for source in trusted_sources:
            _id(source, _SOURCE, "trusted source")
        self.trusted_sources = frozenset(trusted_sources)
        self._owner = object()

    def prepare(self, scenario_ids: Sequence[str] | None = None, *,
                allow_external: bool = False) -> EvaluationPlan:
        """Freeze a bounded catalog selection; no network or runtime actions."""
        if type(allow_external) is not bool:
            raise NexusEvaluationError("invalid external mode flag")
        snap = self.catalog.snapshot()
        if scenario_ids is None:
            selected = tuple(self.catalog.select())
        else:
            if not isinstance(scenario_ids, (list, tuple)) or len(scenario_ids) > self.limits.max_cases:
                raise NexusEvaluationError("invalid scenario selection")
            if len(set(map(str, scenario_ids))) != len(scenario_ids):
                raise NexusEvaluationError("duplicate scenario selection")
            selected = tuple(sorted((self.catalog.get(_id(sid, _ID, "scenario ID")) for sid in scenario_ids),
                                    key=lambda x: x.scenario_id))
        if not selected or len(selected) > self.limits.max_cases:
            raise NexusEvaluationError("scenario selection empty or exceeds limit")
        if any(case.mode == "external" for case in selected) and not allow_external:
            raise NexusEvaluationError("external scenario requires explicit host opt-in")
        catalog_sha256 = snap["catalog_sha256"]
        base = {"schema": PLAN_SCHEMA, "catalog_sha256": catalog_sha256,
                "allow_external": allow_external,
                "cases": [{"scenario_id": c.scenario_id, "case_sha256": c.case_sha256,
                           "revision": c.revision, "mode": c.mode} for c in selected]}
        return EvaluationPlan(catalog_sha256, _hash(base), allow_external, selected, self._owner)

    def evaluate(self, plan: EvaluationPlan,
                 observations: Sequence[EvaluationObservation]) -> dict[str, Any]:
        """Evaluate without side effects. Omitted observations are inconclusive."""
        if not isinstance(plan, EvaluationPlan) or plan._owner is not self._owner:
            raise NexusEvaluationError("foreign or invalid evaluation plan")
        snapshot = self.catalog.snapshot()
        if snapshot["catalog_sha256"] != plan.catalog_sha256:
            raise NexusEvaluationError("catalog changed since preparation")
        if not isinstance(plan.cases, tuple) or not 1 <= len(plan.cases) <= self.limits.max_cases:
            raise NexusEvaluationError("invalid plan cases")
        available = {c["scenario_id"]: c["case_sha256"] for c in snapshot["cases"]}
        if any(not isinstance(c, ScenarioCase) or available.get(c.scenario_id) != c.case_sha256
               for c in plan.cases):
            raise NexusEvaluationError("plan contains unregistered or changed cases")
        if [c.scenario_id for c in plan.cases] != sorted(set(c.scenario_id for c in plan.cases)):
            raise NexusEvaluationError("plan case IDs must be unique and ordered")
        if not plan.allow_external and any(c.mode == "external" for c in plan.cases):
            raise NexusEvaluationError("external cases require explicit opt-in")
        bound_plan = {"schema": PLAN_SCHEMA, "catalog_sha256": plan.catalog_sha256,
                      "allow_external": plan.allow_external,
                      "cases": [{"scenario_id": c.scenario_id, "case_sha256": c.case_sha256,
                                 "revision": c.revision, "mode": c.mode} for c in plan.cases]}
        if not hmac.compare_digest(plan.plan_sha256, _hash(bound_plan)):
            raise NexusEvaluationError("evaluation plan digest is inconsistent")
        if not isinstance(observations, (tuple, list)) or len(observations) > self.limits.max_cases:
            raise NexusEvaluationError("invalid observation collection")
        expected = {x.scenario_id: x for x in plan.cases}
        lookup: dict[str, EvaluationObservation] = {}
        for obs in observations:
            if not isinstance(obs, EvaluationObservation):
                raise NexusEvaluationError("observation is not an EvaluationObservation")
            case = expected.get(obs.scenario_id)
            if case is None or not hmac.compare_digest(case.case_sha256, obs.case_sha256):
                raise NexusEvaluationError("unknown scenario or mismatched case digest")
            if obs.scenario_id in lookup:
                raise NexusEvaluationError("duplicate observation")
            if obs.source_id not in self.trusted_sources:
                raise NexusEvaluationError("observation source is not configured by the host")
            if len(obs._payload) > self.limits.max_observation_bytes:
                raise NexusEvaluationError("observation exceeds byte limit")
            lookup[obs.scenario_id] = obs

        result_rows: list[dict[str, Any]] = []
        check_counts = {"pass": 0, "fail": 0, "inconclusive": 0}
        case_counts = {"pass": 0, "fail": 0, "inconclusive": 0}
        for case in plan.cases:
            obs = lookup.get(case.scenario_id)
            checks: list[dict[str, Any]] = []
            reasons: list[str] = []
            if obs is None:
                status = "inconclusive"
                reasons.append("observation_missing")
                for exp in case.expectations:
                    checks.append({"path": exp.path, "operator": exp.operator,
                                   "status": "inconclusive", "code": "observation_missing"})
            elif obs.status != "observed":
                status = "failed" if obs.status == "error" else "inconclusive"
                reasons.append("host_reported_error" if obs.status == "error" else "host_reported_unavailable")
                for exp in case.expectations:
                    checks.append({"path": exp.path, "operator": exp.operator,
                                   "status": "inconclusive", "code": "not_evaluated"})
            else:
                data = obs.payload
                for exp in case.expectations:
                    verdict, code = _test_expectation(exp, data)
                    checks.append({"path": exp.path, "operator": exp.operator,
                                   "status": verdict, "code": code})
                if obs.duration_ms > case.max_seconds * 1000:
                    reasons.append("time_budget_exceeded")
                if any(c["status"] == "fail" for c in checks):
                    reasons.append("expectation_failed")
                status = "fail" if reasons else "pass"
                if status == "fail":
                    status = "failed"
            for check in checks:
                check_counts[check["status"]] += 1
            normalized_status = "fail" if status == "failed" else status
            case_counts[normalized_status] += 1
            row = {"scenario_id": case.scenario_id, "case_sha256": case.case_sha256,
                   "revision": case.revision, "product": case.product,
                   "category": case.category, "mode": case.mode,
                   "status": "failed" if normalized_status == "fail" else normalized_status,
                   "reason_codes": reasons, "checks": checks,
                   "observation": obs.summary() if obs is not None else None}
            result_rows.append(row)

        total = len(result_rows)
        passed, failed, unknown = (case_counts[k] for k in ("pass", "fail", "inconclusive"))
        total_checks = sum(check_counts.values())
        metrics = {"total_scenarios": total, "passed": passed, "failed": failed,
                   "inconclusive": unknown, "pass_rate": round(passed / total, 6),
                   "evaluated_pass_rate": round(passed / (passed + failed), 6) if passed + failed else None,
                   "expectations_total": total_checks,
                   "expectations_passed": check_counts["pass"],
                   "expectations_failed": check_counts["fail"],
                   "expectations_inconclusive": check_counts["inconclusive"]}
        status = "failed" if failed else "inconclusive" if unknown else "passed"
        base = {"schema": REPORT_SCHEMA, "plan_sha256": plan.plan_sha256,
                "catalog_sha256": plan.catalog_sha256,
                "status": status,
                "assurance": "host_supplied_observations_not_independently_authenticated",
                "metrics": metrics, "cases": result_rows}
        report = {**base, "report_sha256": _hash(base)}
        if len(_canonical(report)) > self.limits.max_report_bytes:
            raise NexusEvaluationError("evaluation report exceeds size limit")
        return report

    @staticmethod
    def verify_report(report: Mapping[str, Any]) -> bool:
        """Check internal hash consistency only; NOT authenticity or source truth."""
        try:
            if not isinstance(report, Mapping):
                return False
            if set(report) != {"schema", "plan_sha256", "catalog_sha256", "status", "assurance",
                               "metrics", "cases", "report_sha256"}:
                return False
            if report["schema"] != REPORT_SCHEMA or not isinstance(report["report_sha256"], str):
                return False
            if _HEX.fullmatch(report["report_sha256"]) is None or len(_canonical(report)) > 8_000_000:
                return False
            for key in ("plan_sha256", "catalog_sha256"):
                if not isinstance(report[key], str) or _HEX.fullmatch(report[key]) is None:
                    return False
            if report["status"] not in {"passed", "failed", "inconclusive"}:
                return False
            if report["assurance"] != "host_supplied_observations_not_independently_authenticated":
                return False
            cases = report["cases"]
            m = report["metrics"]
            if not isinstance(cases, list) or not 1 <= len(cases) <= 2048 or not isinstance(m, dict):
                return False
            if any(not isinstance(row, dict) or row.get("status") not in {"pass", "failed", "inconclusive"}
                   or not isinstance(row.get("checks"), list) for row in cases):
                return False
            if m.get("total_scenarios") != len(cases):
                return False
            if any(m.get(key) != sum(row["status"] == val for row in cases)
                   for key, val in (("passed", "pass"), ("failed", "failed"), ("inconclusive", "inconclusive"))):
                return False
            expected_status = ("failed" if m["failed"] else "inconclusive" if m["inconclusive"] else "passed")
            if report["status"] != expected_status:
                return False
            base = {k: v for k, v in report.items() if k != "report_sha256"}
            return hmac.compare_digest(report["report_sha256"], _hash(base))
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return False


__all__ = ["PLAN_SCHEMA", "REPORT_SCHEMA", "OBSERVATION_SCHEMA", "NexusEvaluationError",
           "EvaluationLimits", "EvaluationObservation", "EvaluationPlan", "NexusEvaluationRunner"]
