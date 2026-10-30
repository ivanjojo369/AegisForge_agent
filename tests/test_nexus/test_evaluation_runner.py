"""Read-only evaluations. No network, Docker, subprocess or model calls."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from aegisforge.nexus.lab.scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation,
)
from aegisforge.nexus.lab.evaluation_runner import (
    EvaluationLimits, EvaluationObservation, NexusEvaluationError,
    NexusEvaluationRunner, PLAN_SCHEMA, REPORT_SCHEMA,
)


def scenario(sid="nexus.planner.good", *, product="nexus", mode="offline", expectations=None,
             max_seconds=30):
    return ScenarioCase(
        sid, product=product, category="functional", title="Check result",
        description="Evaluate declared outcomes without executing steps",
        mode=mode, inputs={"fixture": 1},
        expectations=expectations or (ScenarioExpectation("plan.valid", "equals", True),),
        max_seconds=max_seconds,
    )


def setup(cases=None, sources=("local_harness",)):
    cat = NexusScenarioCatalog()
    cat.register_many(cases or (scenario(),))
    return cat, NexusEvaluationRunner(cat, trusted_sources=sources)


def observed(case, payload=None, **changes):
    args = dict(source_id="local_harness", evidence_ref="run_a/check_1",
                payload={"plan": {"valid": True}}, duration_ms=100)
    if payload is not None:
        args["payload"] = payload
    args.update(changes)
    if args.get("status", "observed") != "observed" and payload is None and "payload" not in changes:
        args["payload"] = None
    return EvaluationObservation(case.scenario_id, case.case_sha256, **args)


def evaluate_one(case=None, payload=None):
    c = case or scenario()
    cat, runner = setup((c,))
    p = runner.prepare()
    return runner, runner.evaluate(p, [observed(c, payload)])


def test_simple_pass_and_report_integrity():
    runner, report = evaluate_one()
    assert report["schema"] == REPORT_SCHEMA
    assert report["status"] == "passed"
    assert report["metrics"]["passed"] == 1
    assert report["metrics"]["pass_rate"] == 1
    assert runner.verify_report(report)


def test_inert_plan_and_nonleakage():
    cat, runner = setup()
    plan = runner.prepare()
    assert plan.to_dict()["executed"] is False
    assert plan.to_dict()["schema"] == PLAN_SCHEMA
    assert "fixture" not in json.dumps(plan.to_dict())
    assert not hasattr(runner, "run_scenarios")
    assert not hasattr(runner, "execute")


def test_repeated_same_evaluation_is_deterministic():
    cat, runner = setup()
    plan = runner.prepare()
    c = cat.get("nexus.planner.good")
    obs = observed(c)
    assert runner.evaluate(plan, (obs,)) == runner.evaluate(plan, (obs,))


def test_missing_observation_is_inconclusive():
    cat, runner = setup()
    out = runner.evaluate(runner.prepare(), [])
    assert out["status"] == "inconclusive"
    assert out["metrics"]["expectations_inconclusive"] == 1
    assert out["cases"][0]["observation"] is None


def test_error_observation_fails_without_raw_error():
    cat, runner = setup()
    c = cat.get("nexus.planner.good")
    o = observed(c, payload=None, status="error", failure_code="timeout_error")
    out = runner.evaluate(runner.prepare(), [o])
    assert out["status"] == "failed"
    assert out["cases"][0]["reason_codes"] == ["host_reported_error"]
    assert out["metrics"]["expectations_inconclusive"] == 1


def test_unavailable_observation_inconclusive():
    cat, runner = setup()
    c = cat.get("nexus.planner.good")
    out = runner.evaluate(runner.prepare(), [observed(c, status="unavailable", failure_code="not_ready")])
    assert out["status"] == "inconclusive"


def test_false_observation_fails():
    _, out = evaluate_one(payload={"plan": {"valid": False}})
    assert out["status"] == "failed"
    assert out["metrics"]["expectations_failed"] == 1


def test_missing_field_fails():
    _, out = evaluate_one(payload={"plan": {}})
    assert out["cases"][0]["checks"][0]["code"] == "field_missing"


def test_exists_true_even_if_none():
    c = scenario(expectations=(ScenarioExpectation("plan.valid", "exists"),))
    _, out = evaluate_one(c, {"plan": {"valid": None}})
    assert out["status"] == "passed"


def test_exists_missing_fails():
    c = scenario(expectations=(ScenarioExpectation("plan.valid", "exists"),))
    _, out = evaluate_one(c, {"plan": {}})
    assert out["status"] == "failed"


@pytest.mark.parametrize("value,operator,expected,success", [
    (1,"equals",True,False), (True,"equals",1,False),
    (1,"not_equals",True,True), (False,"not_equals",False,False),
    (4,"gte",3,True), (2,"gte",3,False), (False,"gte",0,False),
    (2,"lte",3,True), (4,"lte",3,False),
    ("hello world","contains","world",True), ("hi","contains","world",False),
    (["x", 2],"contains",2,True), (["x",True],"contains",1,False),
    ({"x":2},"contains","x",False),
])
def test_operators(value, operator, expected, success):
    c = scenario(expectations=(ScenarioExpectation("plan.valid", operator, expected),))
    _, out = evaluate_one(c, {"plan":{"valid":value}})
    assert (out["status"] == "passed") is success


def test_time_budget_enforced():
    c = scenario(max_seconds=1)
    cat, runner = setup((c,))
    out = runner.evaluate(runner.prepare(), [observed(c, duration_ms=1001)])
    assert out["status"] == "failed"
    assert "time_budget_exceeded" in out["cases"][0]["reason_codes"]


def test_time_budget_at_boundary_pass():
    c = scenario(max_seconds=1)
    cat, runner = setup((c,))
    assert runner.evaluate(runner.prepare(), [observed(c, duration_ms=1000)])["status"] == "passed"


def test_multi_scenario_rates_and_order():
    cases = [scenario("nexus.two"), scenario("aegisgrid.one", product="aegisgrid"),
             scenario("live_forge.one", product="live_forge", mode="simulated")]
    cat, runner = setup(cases)
    obs = [observed(cases[0]), observed(cases[1], payload={"plan":{"valid":False}})]
    report = runner.evaluate(runner.prepare(), obs)
    assert report["metrics"]["total_scenarios"] == 3
    assert report["metrics"]["pass_rate"] == round(1 / 3, 6)
    assert report["metrics"]["evaluated_pass_rate"] == .5
    assert report["metrics"]["inconclusive"] == 1
    assert report["status"] == "failed"
    assert [row["scenario_id"] for row in report["cases"]] == sorted(c.scenario_id for c in cases)


def test_explicit_external_opt_in():
    c = scenario("live_forge.remote", product="live_forge", mode="external")
    cat, runner = setup((c,))
    with pytest.raises(NexusEvaluationError, match="external"):
        runner.prepare()
    p = runner.prepare(allow_external=True)
    assert p.allow_external
    # Opt-in to evaluating externally supplied data is NOT an execution permit.
    assert p.to_dict()["executed"] is False


def test_foreign_plan_rejected():
    cat, r1 = setup()
    r2 = NexusEvaluationRunner(cat, trusted_sources=("local_harness",))
    with pytest.raises(NexusEvaluationError, match="foreign"):
        r2.evaluate(r1.prepare(), ())


def test_stale_catalog_rejected():
    cat, runner = setup()
    p = runner.prepare()
    cat.register(scenario("nexus.additional"))
    with pytest.raises(NexusEvaluationError, match="catalog changed"):
        runner.evaluate(p, ())


def test_duplicate_observations_rejected():
    cat, runner = setup()
    c = cat.get("nexus.planner.good")
    with pytest.raises(NexusEvaluationError, match="duplicate observation"):
        runner.evaluate(runner.prepare(), [observed(c), observed(c)])


def test_wrong_case_digest_rejected():
    cat, runner = setup()
    c = cat.get("nexus.planner.good")
    o = EvaluationObservation(c.scenario_id, "a"*64, source_id="local_harness",
                              evidence_ref="run_a/check_1", payload={"plan": {"valid":True}})
    with pytest.raises(NexusEvaluationError, match="mismatched"):
        runner.evaluate(runner.prepare(), (o,))


def test_unknown_case_rejected():
    cat, runner = setup()
    c = scenario("nexus.not_registered")
    with pytest.raises(NexusEvaluationError, match="unknown"):
        runner.evaluate(runner.prepare(), (observed(c),))


def test_source_not_allowlisted_fails_closed():
    cat, runner = setup(sources=())
    with pytest.raises(NexusEvaluationError, match="source"):
        runner.evaluate(runner.prepare(), (observed(cat.get("nexus.planner.good")),))


def test_case_selection_and_duplicate_ids():
    c1, c2 = scenario(), scenario("nexus.other")
    cat, runner = setup((c1,c2))
    assert len(runner.prepare((c1.scenario_id,)).cases) == 1
    with pytest.raises(NexusEvaluationError, match="duplicate"):
        runner.prepare((c1.scenario_id, c1.scenario_id))


def test_empty_selection_denied():
    cat, runner = setup()
    with pytest.raises(NexusEvaluationError, match="empty"):
        runner.prepare(())


@pytest.mark.parametrize("kwargs", [
    {"source_id":"invalid?source"},
    {"evidence_ref":"https://service/path?token=abcdef"},
    {"duration_ms":True},
    {"duration_ms":-1},
    {"status":"verified"},
    {"failure_code":"oops"},
    {"payload":{"api_key":"not_allowed"}},
    {"payload":{"x":float("nan")}},
    {"payload":{"x":b"raw"}},
])
def test_invalid_observation(kwargs):
    c = scenario()
    with pytest.raises(NexusEvaluationError):
        observed(c, **kwargs)


def test_nonobserved_with_payload_denied():
    with pytest.raises(NexusEvaluationError):
        observed(scenario(), status="error", failure_code="timeout_error", payload={"x":1})


def test_observation_defensive_copy():
    c = scenario()
    payload = {"plan":{"valid":True}}
    o = observed(c, payload)
    payload["plan"]["valid"] = False
    o.payload["plan"]["valid"] = False
    assert o.payload["plan"]["valid"] is True


def test_report_does_not_leak_raw_payload():
    c = scenario(expectations=(ScenarioExpectation("result.answer", "contains", "ready"),))
    cat, runner = setup((c,))
    report = runner.evaluate(runner.prepare(), (observed(c, {"result":{"answer":"ready-private-marker"}}),))
    text = json.dumps(report)
    assert "ready-private-marker" not in text
    assert "ready" not in text
    assert "payload_sha256" in text


def test_report_tamper_detected():
    runner, report = evaluate_one()
    wrong = copy.deepcopy(report)
    wrong["cases"][0]["status"] = "failed"
    assert not runner.verify_report(wrong)


def test_report_integrity_not_authentication():
    runner, report = evaluate_one()
    assert report["assurance"] == "host_supplied_observations_not_independently_authenticated"


@pytest.mark.parametrize("kwargs", [
    {"max_cases":0},{"max_cases":True},{"max_observation_bytes":100},
    {"max_report_bytes":100}, {"max_cases":10000},
])
def test_invalid_limits(kwargs):
    with pytest.raises(NexusEvaluationError):
        EvaluationLimits(**kwargs)


def test_observation_size_budget():
    c = scenario(expectations=(ScenarioExpectation("payload", "exists"),))
    cat, runner = setup((c,))
    runner = NexusEvaluationRunner(cat, trusted_sources=("local_harness",),
                                   limits=EvaluationLimits(max_observation_bytes=256))
    o = observed(c, {"payload":"x"*300})
    with pytest.raises(NexusEvaluationError, match="byte limit"):
        runner.evaluate(runner.prepare(), (o,))


def test_catalog_snapshot_deterministic_and_not_changed():
    cat, runner = setup()
    before = cat.snapshot()
    p = runner.prepare()
    runner.evaluate(p, (observed(cat.get("nexus.planner.good")),))
    assert cat.snapshot() == before


def test_report_digest_matches_canonical():
    runner, report = evaluate_one()
    base = {k:v for k,v in report.items() if k != "report_sha256"}
    encoded = json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    assert report["report_sha256"] == hashlib.sha256(encoded).hexdigest()


def test_nested_bool_int_distinction():
    c = scenario(expectations=(ScenarioExpectation("plan.valid", "equals", {"ok": True}),))
    _, report = evaluate_one(c, {"plan": {"valid": {"ok": 1}}})
    assert report["status"] == "failed"


def test_nested_list_bool_int_distinction():
    c = scenario(expectations=(ScenarioExpectation("plan.valid", "equals", [True]),))
    _, report = evaluate_one(c, {"plan": {"valid": [1]}})
    assert report["status"] == "failed"


def test_plan_digest_forgery_rejected():
    from dataclasses import replace
    cat, runner = setup()
    p = runner.prepare()
    forged = replace(p, plan_sha256="1"*64)
    with pytest.raises(NexusEvaluationError, match="digest"):
        runner.evaluate(forged, ())


def test_plan_cases_foreign_rejected():
    from dataclasses import replace
    cat, runner = setup()
    p = runner.prepare()
    forged = replace(p, cases=(scenario("nexus.other"),))
    with pytest.raises(NexusEvaluationError, match="unregistered"):
        runner.evaluate(forged, ())


def test_reject_url_like_evidence_ref_even_without_query():
    with pytest.raises(NexusEvaluationError, match="local identifier"):
        observed(scenario(), evidence_ref="https://evil.example.com/path")


def test_multiple_expectations_counted():
    c = scenario(expectations=(ScenarioExpectation("plan.valid", "equals", True),
                               ScenarioExpectation("plan.other", "exists")))
    _, report = evaluate_one(c, {"plan":{"valid":True}})
    assert report["status"] == "failed"
    assert report["metrics"]["expectations_total"] == 2
    assert report["metrics"]["expectations_passed"] == 1
    assert report["metrics"]["expectations_failed"] == 1
