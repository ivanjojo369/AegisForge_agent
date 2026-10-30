"""Offline integration checks for the scorecard built from real Nexus Lab contracts."""
from __future__ import annotations

from dataclasses import replace
import copy
import hashlib
import json

import pytest

from aegisforge.nexus.lab.scenario_catalog import (
    NexusScenarioCatalog, ScenarioCase, ScenarioExpectation,
)
from aegisforge.nexus.lab.evaluation_runner import NexusEvaluationRunner, EvaluationObservation
from aegisforge.nexus.lab.regression_detector import NexusRegressionDetector
from aegisforge.nexus.lab.ablation_study import (
    NexusAblationStudy, AblationDesign, AblationPair,
)
from aegisforge.nexus.lab.scorecard import (
    SCORECARD_SCHEMA, ASSURANCE, NexusScorecard, NexusScorecardError,
    ScorecardLimits, ScorecardComparison, ScorecardAblation,
)


def _hash_text(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _recalc(data, key):
    data[key] = hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != key},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    return data


def _case(sid="nexus.planning", product="nexus", category="functional", *, revision=1):
    return ScenarioCase(sid, product=product, category=category,
                        title="Deterministic synthetic evaluation",
                        description="Synthetic tests no external requests", revision=revision,
                        inputs={"sample": 1},
                        expectations=(ScenarioExpectation("result.ok", "equals", True),))


def _report(*, suffix="trial.one", result=None, cases=None, ms=100):
    if cases is None:
        cases = (_case(),)
    cat = NexusScenarioCatalog()
    cat.register_many(cases)
    runner = NexusEvaluationRunner(cat, trusted_sources=("test_harness",))
    out = []
    for item in cases:
        condition = True if result is None else result.get(item.scenario_id, True)
        if condition == "missing":
            continue
        spec = {"source_id": "test_harness", "evidence_ref": f"trial/{suffix}/{item.scenario_id}",
                "duration_ms": ms}
        if condition == "unavailable":
            spec.update(status="unavailable", failure_code="unavailable")
        elif condition == "error":
            spec.update(status="error", failure_code="host_error")
        else:
            spec["payload"] = {"result": {"ok": condition}}
        out.append(EvaluationObservation(item.scenario_id, item.case_sha256, **spec))
    return runner.evaluate(runner.prepare(), out)


def _sources():
    a = _report(suffix="trial.a")
    b = _report(suffix="trial.b", result={"nexus.planning": False})
    compare = ScorecardComparison(a, b, NexusRegressionDetector().compare(a, b))
    design = AblationDesign("colossus.memory_trial", "memory", ("memory", "planner", "trust"),
                            ("planner", "trust"))
    paired = AblationPair("trial.pair", a, b, _hash_text("env"), _hash_text("env"),
                         _hash_text("inputs"), _hash_text("inputs"), 4, 4)
    ablation = ScorecardAblation(design, (paired,), NexusAblationStudy().analyze(design, (paired,)))
    return a, b, compare, ablation


def test_single_report_and_verify():
    a = _report()
    sc = NexusScorecard()
    out = sc.build("colossus.dashboard", [a])
    assert out["schema"] == SCORECARD_SCHEMA
    assert out["assurance"] == ASSURANCE
    assert out["status"] == "descriptive_complete"
    assert out["aggregate"]["passed"] == 1
    assert out["aggregate"]["pass_fraction_all_observations"] == 1.0
    assert out["aggregate"]["pass_fraction_conclusive_only"] == 1.0
    assert sc.verify(out, "colossus.dashboard", [a])


def test_full_stack_report_and_verify():
    a, b, comparison, ablation = _sources()
    sc = NexusScorecard()
    out = sc.build("colossus.dashboard", [a, b], comparisons=[comparison], ablations=[ablation])
    assert out["status"] == "review_required"
    assert out["evaluation_report_count"] == 2
    assert out["regression_report_count"] == 1
    assert out["ablation_study_count"] == 1
    assert out["regressions"][0]["counts"]["regressed"] == 1
    assert out["ablations"][0]["totals"]["loss_when_removed"] == 1
    assert "regressions_detected" in out["review_flags"]
    assert sc.verify(out, "colossus.dashboard", [a,b], comparisons=[comparison], ablations=[ablation])


def test_deterministic_report_order():
    a, b, comp, abl = _sources()
    sc = NexusScorecard()
    one = sc.build("colossus.dashboard", [a,b], comparisons=[comp], ablations=[abl])
    two = sc.build("colossus.dashboard", [b,a], comparisons=(comp,), ablations=(abl,))
    assert one == two
    assert one["scorecard_sha256"] == two["scorecard_sha256"]


def test_json_round_trip():
    a,b,comp,abl = _sources()
    sc = NexusScorecard()
    card = sc.build("colossus.dashboard", [a,b], comparisons=[comp], ablations=[abl])
    assert sc.verify(json.loads(json.dumps(card)), "colossus.dashboard", [a,b],
                     comparisons=[comp], ablations=[abl])


def test_no_observation_payload_or_secret_references():
    a,b,comp,abl = _sources()
    out = NexusScorecard().build("colossus.dashboard", [a,b], comparisons=[comp], ablations=[abl])
    encoded = json.dumps(out)
    for private in ('"evidence_ref"', '"payload"', '"inputs"', '"checks"',
                    'trial/trial.a', '"result"', '"ok"', '"source_id"'):
        assert private not in encoded


def test_synthetic_multidomain_totals_and_categories():
    samples = (_case("nexus.planning"),
               _case("aegisgrid.energy", "aegisgrid", "performance"),
               _case("live_forge.stream", "live_forge", "integration"))
    a = _report(cases=samples, result={"aegisgrid.energy": False, "live_forge.stream": "missing"})
    card = NexusScorecard().build("colossus.dashboard", [a])
    assert card["aggregate"]["evaluations"] == 3
    assert card["aggregate"]["passed"] == 1
    assert card["aggregate"]["failed"] == 1
    assert card["aggregate"]["inconclusive"] == 1
    assert card["aggregate"]["pass_fraction_all_observations"] == 0.333333
    assert card["aggregate"]["pass_fraction_conclusive_only"] == 0.5
    assert card["by_product"]["aegisgrid"]["failed"] == 1
    assert card["by_product"]["live_forge"]["inconclusive"] == 1
    assert card["by_category"]["performance"]["failed"] == 1
    assert "inconclusive_evaluations_present" in card["review_flags"]


def test_all_inconclusive_denominator():
    a = _report(result={"nexus.planning": "missing"})
    out = NexusScorecard().build("colossus.dashboard", [a])
    assert out["aggregate"]["pass_fraction_conclusive_only"] is None
    assert out["aggregate"]["pass_fraction_all_observations"] == 0


def test_multiple_evaluations_not_unique_scenarios():
    a = _report(suffix="run.one")
    b = _report(suffix="run.two")
    out = NexusScorecard().build("colossus.dashboard", [a,b])
    assert out["aggregate"]["evaluations"] == 2
    assert out["evaluation_report_count"] == 2
    assert out["distinct_selected_suites"] == 1
    assert out["distinct_catalog_snapshots"] == 1


def test_different_catalogs_flagged():
    a = _report(cases=(_case(),))
    b = _report(cases=(_case(),_case("aegisgrid.energy", "aegisgrid")), suffix="other")
    out = NexusScorecard().build("colossus.dashboard", [a,b])
    assert "different_catalog_snapshots" in out["review_flags"]
    assert "different_selected_suites" in out["review_flags"]
    assert out["distinct_selected_suites"] == 2


def test_same_catalog_different_selected_suites_is_flagged():
    x, y = _case(), _case("nexus.other")
    cat = NexusScenarioCatalog()
    cat.register_many((x,y))
    runner = NexusEvaluationRunner(cat, trusted_sources=("test_harness",))
    def trial(which):
        obs = [EvaluationObservation(c.scenario_id, c.case_sha256, source_id="test_harness",
                  evidence_ref=f"trial/{c.scenario_id}", payload={"result": {"ok": True}}) for c in which]
        return runner.evaluate(runner.prepare([c.scenario_id for c in which]), obs)
    a,b = trial((x,)),trial((x,y))
    out = NexusScorecard().build("colossus.dashboard", [a,b])
    assert out["distinct_catalog_snapshots"] == 1
    assert out["distinct_selected_suites"] == 2
    assert "different_selected_suites" in out["review_flags"]


@pytest.mark.parametrize("invalid", ["", "x", "A.B", "../private", "colossus/score", "écho", 17, None, True])
def test_invalid_ids_rejected(invalid):
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build(invalid, [_report()])


@pytest.mark.parametrize("bad", [None, {}, "invalid", 123, True, [], [1], [None]])
def test_invalid_evaluation_inputs_rejected(bad):
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard", bad)


def test_duplicate_evaluation_digest_rejected():
    a = _report()
    with pytest.raises(NexusScorecardError, match="duplicate"):
        NexusScorecard().build("colossus.dashboard", [a,a])


def test_tampered_evaluation_report_rejected():
    a = _report()
    a["metrics"]["passed"] = 2
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard", [a])


def test_rehashed_fabricated_evaluation_metrics_rejected():
    a = _report()
    a["metrics"]["passed"] = 2
    _recalc(a,"report_sha256")
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard", [a])


def test_comparison_must_recompute_against_sources():
    a,b,comp,_ = _sources()
    tampered = copy.deepcopy(comp.comparison_report)
    tampered["counts"]["regressed"] = 0
    _recalc(tampered,"comparison_sha256")
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard", [a,b], comparisons=[replace(comp, comparison_report=tampered)])


def test_comparison_must_reference_selected_source_evaluations():
    a,b,comp,_ = _sources()
    with pytest.raises(NexusScorecardError, match="not present"):
        NexusScorecard().build("colossus.dashboard", [a], comparisons=[comp])


def test_comparison_source_swapped_rejected():
    a,b,comp,_ = _sources()
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard", [a,b], comparisons=[replace(comp,baseline_report=b)])


def test_duplicate_comparison_rejected():
    a,b,comp,_ = _sources()
    with pytest.raises(NexusScorecardError, match="duplicate"):
        NexusScorecard().build("colossus.dashboard", [a,b], comparisons=[comp,comp])


def test_ablation_must_recompute_against_sources():
    a,b,_,abl = _sources()
    tampered = copy.deepcopy(abl.study_report)
    tampered["totals"]["loss_when_removed"] = 0
    _recalc(tampered,"study_sha256")
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard", [a,b], ablations=[replace(abl,study_report=tampered)])


def test_ablation_must_reference_selected_evaluations():
    a,b,_,abl = _sources()
    with pytest.raises(NexusScorecardError, match="not present"):
        NexusScorecard().build("colossus.dashboard", [a], ablations=[abl])


def test_duplicate_ablation_rejected():
    a,b,_,abl = _sources()
    with pytest.raises(NexusScorecardError, match="duplicate"):
        NexusScorecard().build("colossus.dashboard", [a,b], ablations=[abl,abl])


def test_reordered_ablation_source_pairs_invalid():
    a,b,_,abl = _sources()
    modified = replace(abl, pairs=(replace(abl.pairs[0], pair_id="trial.other"),))
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard", [a,b], ablations=[modified])


def test_wrong_ablation_design_rejected():
    a,b,_,abl = _sources()
    changed = replace(abl, design=AblationDesign("colossus.other_study", "memory",
                                       ("memory","planner","trust"),("planner","trust")))
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard", [a,b], ablations=[changed])


@pytest.mark.parametrize("field", ["aggregate", "by_product", "status", "evaluations", "limitations"])
def test_scorecard_tamper_detected(field):
    a = _report()
    card = NexusScorecard().build("colossus.dashboard", [a])
    if field == "aggregate": card[field]["passed"] = 0
    elif field == "by_product": card[field]["nexus"]["failed"] = 1
    elif field == "status": card[field] = "fake"
    elif field == "evaluations": card[field] = []
    else: card[field].append("fake")
    assert not NexusScorecard().verify(card, "colossus.dashboard", [a])


def test_rehashed_scorecard_tamper_detected_from_sources():
    a = _report()
    card = NexusScorecard().build("colossus.dashboard", [a])
    card["aggregate"]["failed"] = 1
    _recalc(card,"scorecard_sha256")
    assert not NexusScorecard().verify(card, "colossus.dashboard", [a])


def test_wrong_scorecard_id_invalidates_verify():
    a = _report()
    card = NexusScorecard().build("colossus.dashboard", [a])
    assert not NexusScorecard().verify(card, "colossus.other", [a])


def test_missing_source_report_invalidates_verify():
    a,b,comp,_ = _sources()
    card = NexusScorecard().build("colossus.dashboard", [a,b], comparisons=[comp])
    assert not NexusScorecard().verify(card, "colossus.dashboard", [a])


def test_extra_fields_in_scorecard_rejected():
    a = _report()
    card = NexusScorecard().build("colossus.dashboard", [a])
    card["injected"] = "secret"
    assert not NexusScorecard().verify(card,"colossus.dashboard",[a])


@pytest.mark.parametrize("name,value", [
    ("max_evaluations",0), ("max_evaluations",129), ("max_comparisons",65),
    ("max_ablations",33), ("max_cases_per_evaluation",0),
    ("max_input_report_bytes",1024), ("max_output_bytes",1024),
    ("max_evaluations",True), ("max_comparisons",1.5),
])
def test_invalid_limits_rejected(name,value):
    with pytest.raises(NexusScorecardError):
        ScorecardLimits(**{name:value})


def test_evaluation_limit_enforced():
    a,b,_,_ = _sources()
    sc = NexusScorecard(limits=ScorecardLimits(max_evaluations=1))
    with pytest.raises(NexusScorecardError):
        sc.build("colossus.dashboard",[a,b])


def test_comparison_limit_enforced():
    a,b,comp,_ = _sources()
    sc = NexusScorecard(limits=ScorecardLimits(max_comparisons=0))
    with pytest.raises(NexusScorecardError):
        sc.build("colossus.dashboard",[a,b],comparisons=[comp])


def test_ablation_limit_enforced():
    a,b,_,abl = _sources()
    sc = NexusScorecard(limits=ScorecardLimits(max_ablations=0))
    with pytest.raises(NexusScorecardError):
        sc.build("colossus.dashboard",[a,b],ablations=[abl])


def test_input_size_limit_enforced():
    a = _report()
    sc = NexusScorecard(limits=ScorecardLimits(max_input_report_bytes=4096))
    with pytest.raises(NexusScorecardError):
        sc.build("colossus.dashboard", [a | {"noise":"x" * 4000}])


def test_report_still_json_safe():
    a,b,comp,abl = _sources()
    card = NexusScorecard().build("colossus.dashboard",[a,b],comparisons=[comp],ablations=[abl])
    assert json.dumps(card,allow_nan=False)


def test_invalid_comparison_type_rejected():
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard",[_report()],comparisons=[{}])


def test_invalid_ablation_type_rejected():
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard",[_report()],ablations=[{}])


def test_counters_are_not_misreported_as_unique_cases():
    a,b,comp,abl = _sources()
    card = NexusScorecard().build("colossus.dashboard",[a,b],comparisons=[comp],ablations=[abl])
    assert any("not unique scenarios" in x for x in card["limitations"])
    assert any("not independently authenticated" in x for x in card["limitations"])
    assert any("do not establish causality" in x for x in card["limitations"])


def test_regression_review_without_regressions():
    a = _report(suffix="base")
    b = _report(suffix="missing", result={"nexus.planning":"missing"})
    comp = ScorecardComparison(a,b,NexusRegressionDetector().compare(a,b))
    card = NexusScorecard().build("colossus.dashboard",[a,b],comparisons=[comp])
    assert "regression_review_required" in card["review_flags"]
    assert "regressions_detected" not in card["review_flags"]


def test_valid_false_from_non_dict():
    a = _report()
    assert not NexusScorecard().verify([],"colossus.dashboard",[a])


def test_disallow_bad_limits_constructor():
    with pytest.raises(NexusScorecardError):
        NexusScorecard(limits={})


def test_source_comparison_wrong_digest_rejected():
    a,b,comp,_ = _sources()
    wrong = copy.deepcopy(comp.comparison_report)
    wrong["comparison_sha256"] = "0"*64
    with pytest.raises(NexusScorecardError):
        NexusScorecard().build("colossus.dashboard",[a,b],comparisons=[replace(comp,comparison_report=wrong)])
