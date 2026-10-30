"""Offline tests for the paired, non-executing Nexus Laboratory ablation module."""
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
from aegisforge.nexus.lab.ablation_study import (
    NexusAblationStudy, AblationDesign, AblationPair, AblationLimits,
    NexusAblationError, ABLATION_SCHEMA,
)


def _h(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _case(id_="nexus.planning", product="nexus", expected=True, revision=1):
    return ScenarioCase(id_, product=product, category="functional", title="Controlled case",
                        description="No external calls", inputs={"fixture": True}, revision=revision,
                        expectations=(ScenarioExpectation("result.ok", "equals", expected),))


def _report(cases=None, *, flags=None, suffix="ref", ms=100, missing=()):
    cases = cases or (_case(),)
    flags = flags or {}
    catalog = NexusScenarioCatalog()
    catalog.register_many(cases)
    runner = NexusEvaluationRunner(catalog, trusted_sources=("test_harness",))
    plan = runner.prepare()
    observations = []
    for case in cases:
        if case.scenario_id in missing:
            continue
        val = flags.get(case.scenario_id, True)
        observations.append(EvaluationObservation(
            case.scenario_id, case.case_sha256, source_id="test_harness",
            evidence_ref=f"trial/{suffix}/{case.scenario_id}",
            duration_ms=ms,
            payload={"result": {"ok": val}},
        ))
    return runner.evaluate(plan, observations)


def _design():
    return AblationDesign("colossus.memory_ablation", "memory", ("memory", "planner", "trust"),
                         ("planner", "trust"))


def _pair(ref=None, ab=None, *, ident="trial.one", seed=7, env=None, inp=None):
    return AblationPair(ident, ref or _report(suffix=ident + ".ref"),
                       ab or _report(suffix=ident + ".abl"),
                       env or _h("env"), env or _h("env"),
                       inp or _h("input"), inp or _h("input"), seed, seed)


def _run(before=True, after=False, *, id_="trial.one"):
    ref = _report(flags={"nexus.planning": before}, suffix=id_ + ".reference")
    ab = _report(flags={"nexus.planning": after}, suffix=id_ + ".ablated")
    return _pair(ref, ab, ident=id_)


def _rehash(report):
    report["report_sha256"] = hashlib.sha256(json.dumps(
        {k: v for k, v in report.items() if k != "report_sha256"},
        sort_keys=True, ensure_ascii=True, allow_nan=False,
        separators=(",", ":")).encode()).hexdigest()
    return report


def test_declared_loss_when_component_removed():
    study = NexusAblationStudy()
    pair = _run()
    report = study.analyze(_design(), (pair,))
    assert report["schema"] == ABLATION_SCHEMA
    assert report["status"] == "descriptive_complete"
    assert report["totals"]["loss_when_removed"] == 1
    assert report["totals"]["gain_when_removed"] == 0
    assert report["pass_rate_difference_reference_minus_ablated"] == 1.0
    assert study.verify(report, _design(), (pair,))
    assert report["interpretation"] == "descriptive_only_no_causal_attribution_or_significance_test"


def test_report_does_not_expose_observation_payload():
    report = NexusAblationStudy().analyze(_design(), (_run(),))
    encoded = json.dumps(report)
    assert '"payload"' not in encoded
    assert '"result"' not in encoded
    assert '"ok"' not in encoded


@pytest.mark.parametrize("before,after,key", [
    (True, False, "loss_when_removed"),
    (False, True, "gain_when_removed"),
    (True, True, "both_pass"),
    (False, False, "both_failed"),
])
def test_paired_transitions(before, after, key):
    result = NexusAblationStudy().analyze(_design(), (_run(before, after),))
    assert result["totals"][key] == 1


def test_missing_observation_requires_review():
    baseline = _report(suffix="ref")
    ablated = _report(suffix="abl", missing=("nexus.planning",))
    output = NexusAblationStudy().analyze(_design(), (_pair(baseline, ablated),))
    assert output["status"] == "review_required"
    assert output["totals"]["uncertain_pairs"] == 1
    assert output["totals"]["ablated_inconclusive"] == 1


def test_multiple_replicates_and_deterministic_order():
    p1, p2 = _run(True, False, id_="trial.one"), _run(False, True, id_="trial.two")
    study = NexusAblationStudy()
    r1 = study.analyze(_design(), (p2, p1))
    r2 = study.analyze(_design(), (p1, p2))
    assert r1 == r2
    assert r1["pair_count"] == 2
    assert r1["totals"]["loss_when_removed"] == 1
    assert r1["totals"]["gain_when_removed"] == 1
    assert r1["pass_rate_difference_reference_minus_ablated"] == 0


def test_two_products_separate():
    c1 = _case("nexus.planning", "nexus")
    c2 = _case("aegisgrid.simulator", "aegisgrid")
    ref = _report((c1, c2), suffix="ref")
    ab = _report((c1, c2), suffix="abl", flags={c1.scenario_id: False})
    output = NexusAblationStudy().analyze(_design(), (_pair(ref, ab),))
    assert output["scenario_count"] == 2
    assert output["by_product"]["nexus"]["loss_when_removed"] == 1
    assert output["by_product"]["aegisgrid"]["both_pass"] == 1


def test_duration_delta_is_only_descriptive():
    baseline = _report(suffix="ref", ms=100)
    ablated = _report(suffix="abl", ms=400)
    r = NexusAblationStudy().analyze(_design(), (_pair(baseline, ablated),))
    assert r["totals"]["duration_increase_ms_total"] == 300
    assert r["totals"]["duration_pairs"] == 1
    assert "statistically independent" in " ".join(r["limitations"])


def test_mutated_report_detected():
    p = _run()
    out = NexusAblationStudy().analyze(_design(), (p,))
    out["totals"]["loss_when_removed"] = 0
    assert not NexusAblationStudy().verify(out, _design(), (p,))


def test_rehashed_fabrication_does_not_verify():
    pair = _run()
    study = NexusAblationStudy()
    report = study.analyze(_design(), (pair,))
    tampered = copy.deepcopy(report)
    tampered["totals"]["loss_when_removed"] = 0
    tampered["study_sha256"] = hashlib.sha256(json.dumps(
        {k: v for k, v in tampered.items() if k != "study_sha256"},
        sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert not study.verify(tampered, _design(), (pair,))


def test_wrong_design_invalidates_verification():
    pair = _run()
    r = NexusAblationStudy().analyze(_design(), (pair,))
    other = AblationDesign("colossus.other", "memory", ("memory", "planner", "trust"),
                          ("planner", "trust"))
    assert not NexusAblationStudy().verify(r, other, (pair,))


@pytest.mark.parametrize("bad", [
    ("memory", "planner", "trust"),
    ("memory",),
    ("memory", "planner", "trust", "other"),
    ("planner", "trust", "other"),
])
def test_cannot_change_more_than_single_component(bad):
    with pytest.raises(NexusAblationError):
        AblationDesign("colossus.ablation", "memory", ("memory", "planner", "trust"), bad)


def test_single_component_can_be_removed():
    d = AblationDesign("colossus.one", "memory", ("memory",), ())
    assert d.ablated_components == ()


def test_duplicate_components_rejected():
    with pytest.raises(NexusAblationError):
        AblationDesign("colossus.bad", "memory", ("memory", "memory"), ())


@pytest.mark.parametrize("field", ["reference_environment_sha256", "ablated_environment_sha256",
                                     "reference_input_sha256", "ablated_input_sha256"])
def test_weak_fingerprint_rejected(field):
    with pytest.raises(NexusAblationError):
        replace(_run(), **{field: "short"})


def test_environment_mismatch_rejected():
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (replace(_run(), ablated_environment_sha256=_h("other")),))


def test_input_mismatch_rejected():
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (replace(_run(), ablated_input_sha256=_h("other")),))


def test_seed_mismatch_rejected():
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (replace(_run(), ablated_seed=10),))


def test_invalid_seed_bool_rejected():
    with pytest.raises(NexusAblationError):
        replace(_run(), reference_seed=True)


def test_duplicate_pair_id_rejected():
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (_run(), _run()))


def test_duplicate_evidence_report_rejected():
    p1 = _run(id_="trial.one")
    p2 = replace(p1, pair_id="trial.two")
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (p1, p2))


def test_identical_reports_not_independent_arms():
    r = _report(suffix="same")
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (_pair(r, r),))


def test_changed_case_digest_rejected():
    b = _report((_case(),), suffix="ref")
    a = _report((_case(expected=False, revision=2),), suffix="abl")
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (_pair(b, a),))


def test_missing_scenario_rejected():
    b = _report((_case(),), suffix="ref")
    a = _report((_case(), _case("nexus.other")), suffix="abl")
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (_pair(b, a),))


def test_different_selections_across_pairs_rejected():
    a = _run(id_="trial.one")
    b = _pair(_report((_case(), _case("nexus.other")), suffix="ref2"),
              _report((_case(), _case("nexus.other")), suffix="abl2"), ident="trial.two")
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (a, b))


def test_rehashed_but_inconsistent_evaluation_report_rejected():
    p = _run()
    fake = copy.deepcopy(p.reference_report)
    fake["metrics"]["passed"] = 999
    _rehash(fake)
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (replace(p, reference_report=fake),))


def test_invalid_json_report_rejected():
    p = _run()
    fake = copy.deepcopy(p.reference_report)
    fake["untrusted"] = object()
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), (replace(p, reference_report=fake),))


def test_empty_pair_collection_rejected():
    with pytest.raises(NexusAblationError):
        NexusAblationStudy().analyze(_design(), ())


def test_limits_exceeded_rejected():
    study = NexusAblationStudy(limits=AblationLimits(max_pairs=1))
    with pytest.raises(NexusAblationError):
        study.analyze(_design(), (_run(id_="trial.one"), _run(id_="trial.two")))


@pytest.mark.parametrize("name,value", [("max_pairs", 0), ("max_cases_per_pair", True),
                                          ("max_output_bytes", 10), ("max_report_bytes", 999999999)])
def test_bad_limits(name, value):
    with pytest.raises(NexusAblationError):
        AblationLimits(**{name: value})


def test_invalid_identifiers():
    with pytest.raises(NexusAblationError):
        AblationDesign("Bad Study", "memory", ("memory", "planner"), ("planner",))
    with pytest.raises(NexusAblationError):
        replace(_run(), pair_id="a")


def test_no_fs_or_executors(monkeypatch):
    # Interface contains only analyze and verify; no write/run/execution affordance.
    assert not any(hasattr(NexusAblationStudy, k) for k in ("execute", "write", "apply", "run_tests"))
    out = NexusAblationStudy().analyze(_design(), (_run(),))
    assert out["assurance"] == "paired_host_reported_data_not_causally_authenticated"
