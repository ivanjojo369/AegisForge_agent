"""Deterministic, read-only tests for Nexus Laboratory regression detection."""
from __future__ import annotations

import copy
import json
import hashlib

import pytest

from aegisforge.nexus.lab.scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation,
)
from aegisforge.nexus.lab.evaluation_runner import (
    NexusEvaluationRunner, EvaluationObservation,
)
from aegisforge.nexus.lab.regression_detector import (
    NexusRegressionDetector, NexusRegressionError, RegressionLimits, REGRESSION_SCHEMA,
)


def _case(sid="nexus.planner.good", *, product="nexus", expected=True, revision=1,
          mode="offline", category="functional", second=False):
    exps = [ScenarioExpectation("result.ok", "equals", expected)]
    if second:
        exps.append(ScenarioExpectation("result.count", "gte", 2))
    return ScenarioCase(sid, product=product, category=category, title="Test scenario",
                        description="Deterministic test only", revision=revision, mode=mode,
                        inputs={"fixture": 1}, expectations=tuple(exps))


def _report(cases=None, results=None):
    if cases is None:
        cases = [_case()]
    if results is None:
        results = {}
    catalog = NexusScenarioCatalog()
    catalog.register_many(cases)
    evaluator = NexusEvaluationRunner(catalog, trusted_sources=("trusted_test_harness",))
    observations = []
    for case in cases:
        spec = results.get(case.scenario_id, {})
        state = spec.get("state", "pass")
        if state == "missing":
            continue
        opts = dict(source_id="trusted_test_harness", evidence_ref="trial_001/check_1",
                    duration_ms=spec.get("ms", 100))
        if state == "error":
            opts.update(status="error", failure_code="host_failure")
        elif state == "unavailable":
            opts.update(status="unavailable", failure_code="not_available")
        else:
            val = (state == "pass")
            if state == "partial":
                val = True
            payload = {"result": {"ok": val, "count": spec.get("count", 1)}}
            opts.update(payload=payload)
        observations.append(EvaluationObservation(case.scenario_id, case.case_sha256, **opts))
    return evaluator.evaluate(evaluator.prepare(allow_external=True), observations)


def _pair(before="pass", after="pass", **options):
    case = _case(**options)
    return (_report([case], {case.scenario_id: {"state": before}}),
            _report([case], {case.scenario_id: {"state": after}}))


def _rehash(report):
    clone = copy.deepcopy(report)
    clone["report_sha256"] = hashlib.sha256(json.dumps(
        {key: val for key, val in clone.items() if key != "report_sha256"},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode()).hexdigest()
    return clone


def test_same_pass_no_regression_and_verify():
    b, c = _pair()
    d = NexusRegressionDetector()
    result = d.compare(b, c)
    assert result["schema"] == REGRESSION_SCHEMA
    assert result["status"] == "no_regressions_detected"
    assert result["counts"]["comparable"] == 1
    assert result["counts"]["unchanged"] == 1
    assert result["same_selected_suite"]
    assert d.verify_comparison(result, b, c)


def test_pass_to_failed_is_regression():
    b, c = _pair("pass", "fail")
    result = NexusRegressionDetector().compare(b, c)
    assert result["status"] == "regressions_detected"
    assert result["cases"][0]["classification"] == "regressed"
    assert result["cases"][0]["regressed_checks"] == [{"path": "result.ok", "operator": "equals",
                                                               "previous": "pass", "current": "fail"}]


def test_fail_to_pass_is_improvement():
    b, c = _pair("fail", "pass")
    report = NexusRegressionDetector().compare(b, c)
    assert report["counts"]["improved"] == 1
    assert report["status"] == "no_regressions_detected"


@pytest.mark.parametrize("old,new,expected", [
    ("pass", "missing", "coverage_lost"),
    ("pass", "unavailable", "coverage_lost"),
    ("fail", "missing", "coverage_lost"),
    ("missing", "pass", "coverage_gained"),
    ("unavailable", "pass", "coverage_gained"),
    ("missing", "fail", "new_failure"),
    ("unavailable", "error", "new_failure"),
    ("pass", "pass", "unchanged"),
    ("fail", "fail", "unchanged"),
    ("error", "error", "unchanged"),
    ("missing", "missing", "unchanged"),
])
def test_transitions(old, new, expected):
    b, c = _pair(old, new)
    result = NexusRegressionDetector().compare(b, c)
    assert result["cases"][0]["classification"] == expected
    if expected in ("coverage_lost", "coverage_gained", "new_failure"):
        assert result["status"] == "review_required"


def test_different_case_digest_not_comparable_even_when_same_id():
    b, c = _report([_case()]), _report([_case(expected=False, revision=2)])
    result = NexusRegressionDetector().compare(b, c)
    assert result["status"] == "review_required"
    assert result["counts"]["changed_spec"] == 1
    assert result["counts"]["comparable"] == 0
    assert result["cases"][0]["revision_previous"] == 1
    assert result["cases"][0]["revision_current"] == 2


def test_new_and_removed_cases_are_coverage_changes():
    original = _case("nexus.one")
    added = _case("live_forge.two", product="live_forge")
    b = _report([original])
    c = _report([original, added])
    d = NexusRegressionDetector()
    out = d.compare(b, c)
    assert out["counts"]["added"] == 1
    assert out["counts"]["comparable"] == 1
    assert out["status"] == "review_required"
    assert not out["same_selected_suite"]
    reverse = d.compare(c, b)
    assert reverse["counts"]["removed"] == 1


def test_catalog_change_without_selected_case_change_is_observable():
    case = _case()
    cat1 = NexusScenarioCatalog()
    cat2 = NexusScenarioCatalog()
    cat1.register(case)
    cat2.register_many((case, _case("nexus.extra")))
    r1 = NexusEvaluationRunner(cat1, trusted_sources=("trusted_test_harness",))
    r2 = NexusEvaluationRunner(cat2, trusted_sources=("trusted_test_harness",))
    obs = EvaluationObservation(case.scenario_id, case.case_sha256,
                                source_id="trusted_test_harness", evidence_ref="trial_001/check_1",
                                payload={"result": {"ok": True}})
    a = r1.evaluate(r1.prepare(), [obs])
    b = r2.evaluate(r2.prepare((case.scenario_id,)), [obs])
    result = NexusRegressionDetector().compare(a, b)
    assert result["catalog_changed"]
    assert result["same_selected_suite"]
    assert result["counts"]["comparable"] == 1


def test_per_product_counts():
    a = _case("nexus.one")
    b = _case("aegisgrid.two", product="aegisgrid")
    c = _case("live_forge.three", product="live_forge")
    baseline = _report([a,b,c])
    candidate = _report([a,b,c], {a.scenario_id: {"state":"fail"}})
    result = NexusRegressionDetector().compare(baseline,candidate)
    assert result["counts"]["comparable"] == 3
    assert result["by_product"]["nexus"]["regressed"] == 1
    assert result["by_product"]["aegisgrid"]["unchanged"] == 1
    assert result["by_product"]["live_forge"]["unchanged"] == 1
    assert result["cases"] == sorted(result["cases"], key=lambda x:x["scenario_id"])


def test_same_failure_but_new_check_regression_flagged():
    case = _case(second=True)
    b = _report([case], {case.scenario_id: {"state": "pass", "count": 1}})  # fails count only
    c = _report([case], {case.scenario_id: {"state": "fail", "count": 2}})  # fails ok only
    result = NexusRegressionDetector().compare(b,c)
    assert result["counts"]["regressed"] == 1
    assert "check_regression" in result["cases"][0]["signals"]
    assert result["cases"][0]["regressed_checks"][0]["path"] == "result.ok"


def test_slowdown_threshold_alert_not_proof():
    case = _case()
    b = _report([case], {case.scenario_id: {"ms":1000}})
    c = _report([case], {case.scenario_id: {"ms":1600}})
    result = NexusRegressionDetector().compare(b,c)
    assert result["counts"]["performance_alerts"] == 1
    assert result["counts"]["unchanged"] == 1
    assert result["status"] == "review_required"
    assert "duration_increase_alert" in result["cases"][0]["signals"]
    reverse = NexusRegressionDetector().compare(c,b)
    assert reverse["counts"]["performance_alerts"] == 0


def test_slowdown_requires_both_thresholds_and_nonzero_baseline():
    d = NexusRegressionDetector()
    case = _case()
    for before, after in ((0,800), (1000,1100), (1000,1240), (1000,1250)):
        b = _report([case], {case.scenario_id: {"ms":before}})
        c = _report([case], {case.scenario_id: {"ms":after}})
        out = d.compare(b,c)
        assert out["counts"]["performance_alerts"] == (1 if before==1000 and after==1250 else 0)


def test_report_hash_modified_rejected():
    b,c = _pair()
    b["cases"][0]["status"] = "failed"
    with pytest.raises(NexusRegressionError, match="invalid evaluation"):
        NexusRegressionDetector().compare(b,c)


@pytest.mark.parametrize("mutator", [
    lambda r: r["metrics"].update(passed=0),
    lambda r: r["cases"][0].update(status="failed"),
    lambda r: r["cases"][0]["checks"][0].update(status="fail"),
    lambda r: r["cases"][0]["observation"].update(duration_ms=-1),
    lambda r: r["cases"][0].update(product="aegisgrid"),
    lambda r: r["cases"][0].update(reason_codes=["observation_missing"]),
    lambda r: r["cases"][0]["checks"].append(copy.deepcopy(r["cases"][0]["checks"][0])),
    lambda r: r["cases"][0]["observation"].update(case_sha256="0"*64),
    lambda r: r["cases"][0]["observation"].update(status="error"),
    lambda r: r["cases"][0]["checks"][0].update(code="field_missing"),
    lambda r: r["cases"][0]["observation"].update(evidence_ref="https://example.org/token"),
    lambda r: r["cases"][0]["observation"].update(duration_ms=True),
    lambda r: r["cases"][0].update(foo="bar"),
])
def test_maliciously_rehashed_semantic_tampering_denied(mutator):
    b,c = _pair()
    mutator(c)
    c = _rehash(c)
    with pytest.raises(NexusRegressionError):
        NexusRegressionDetector().compare(b,c)


def test_result_digest_tamper_detected():
    b,c = _pair("pass","fail")
    d = NexusRegressionDetector()
    out = d.compare(b,c)
    wrong = copy.deepcopy(out)
    wrong["counts"]["regressed"] = 0
    assert not d.verify_comparison(wrong,b,c)
    wrong = copy.deepcopy(out)
    wrong["cases"][0]["classification"] = "unchanged"
    wrong["comparison_sha256"] = hashlib.sha256(json.dumps(
        {k:v for k,v in wrong.items() if k!="comparison_sha256"},
        sort_keys=True, separators=(",", ":"),ensure_ascii=True).encode()).hexdigest()
    assert not d.verify_comparison(wrong,b,c)
    assert d.verify_comparison(out,b,c)


def test_comparison_deterministic_and_metadata_only():
    b,c = _pair()
    d = NexusRegressionDetector()
    assert d.compare(b,c) == d.compare(b,c)
    raw = json.dumps(d.compare(b,c),sort_keys=True)
    assert "trusted_test_harness" not in raw
    assert "trial_001/check_1" not in raw
    assert "fixture" not in raw
    assert "observed" not in raw


def test_report_too_large_denied():
    cases = [_case(f"nexus.scenario_{i:03d}") for i in range(6)]
    b,c = _report(cases), _report(cases)
    d = NexusRegressionDetector(limits=RegressionLimits(max_report_bytes=4096))
    with pytest.raises(NexusRegressionError, match="byte limit"):
        d.compare(b,c)


def test_output_limit_denied():
    cases = [_case(f"nexus.scenario_{i:03d}") for i in range(45)]
    b = _report(cases)
    c = _report(cases)
    d = NexusRegressionDetector(limits=RegressionLimits(max_output_bytes=4096))
    with pytest.raises(NexusRegressionError, match="output byte limit"):
        d.compare(b,c)


def test_case_count_limit_denied():
    cases = [_case(f"nexus.scenario_{i:03d}") for i in range(3)]
    b,c = _report(cases),_report(cases)
    with pytest.raises(NexusRegressionError, match="number of cases"):
        NexusRegressionDetector(limits=RegressionLimits(max_cases=2)).compare(b,c)


@pytest.mark.parametrize("name,value",[("max_cases",0),("max_cases",True),
     ("max_report_bytes",200), ("max_output_bytes",200),
     ("slowdown_percent",0),("slowdown_min_ms",0)])
def test_invalid_limits(name,value):
    with pytest.raises(NexusRegressionError,match=name):
        RegressionLimits(**{name:value})


def test_report_verify_missing_coverage_is_review_required():
    b,c = _pair("missing", "missing")
    d = NexusRegressionDetector()
    out = d.compare(b,c)
    assert out["status"] == "review_required"
    assert out["counts"]["comparable"] == 1
    # A matching 'inconclusive' scenario is not evidence of full coverage.
    assert out["cases"][0]["status_current"] == "inconclusive"


def test_no_filesystem_or_external_execution_methods():
    detector = NexusRegressionDetector()
    assert not hasattr(detector,"run")
    assert not hasattr(detector,"execute")
    assert not hasattr(detector,"write")
