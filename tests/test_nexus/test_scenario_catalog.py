"""Read-only scenario catalog contract tests, no network or external services."""
import copy
from dataclasses import FrozenInstanceError
import hashlib
import json
import threading

import pytest

from aegisforge.nexus.lab.scenario_catalog import (
    CATALOG_SCHEMA, SCENARIO_SCHEMA, NexusScenarioCatalog,
    NexusScenarioCatalogError as Error, ScenarioCatalogLimits,
    ScenarioCase, ScenarioExpectation,
)


def case(sid="nexus.plan.valid", **changes):
    defaults = dict(
        product="nexus", category="functional", title="Valid plan",
        description="Check structural planning contract without calling Nebius",
        mode="offline", inputs={"task": "Inspect plan", "steps": [1, 2]},
        expectations=(ScenarioExpectation("plan.valid", "equals", True),),
        tags=("offline", "planner"), max_seconds=30,
    )
    defaults.update(changes)
    return ScenarioCase(sid, **defaults)


def test_case_and_explicit_approval_flag():
    c = case()
    assert c.requires_approval is False
    assert c.to_dict()["schema"] == SCENARIO_SCHEMA
    assert len(c.case_sha256) == 64
    assert ScenarioCase.from_dict(c.to_dict()).to_dict() == c.to_dict()


def test_catalog_roundtrip_and_hash():
    cat = NexusScenarioCatalog()
    cat.register_many([case("nexus.plan.valid"), case("aegisgrid.grid.cost", product="aegisgrid")])
    snap = cat.snapshot()
    assert snap["schema"] == CATALOG_SCHEMA
    assert cat.count == 2
    assert snap["cases"][0]["scenario_id"] == "aegisgrid.grid.cost"
    assert NexusScenarioCatalog.verify_snapshot(snap)
    assert NexusScenarioCatalog.from_snapshot(snap).snapshot() == snap
    assert cat.snapshot() == snap


def test_tags_product_categories_and_deterministic_order():
    cat = NexusScenarioCatalog()
    cat.register_many([
        case("live_forge.poll", product="live_forge", category="integration", mode="simulated", tags=("twitch",)),
        case("nexus.plan.valid"),
        case("aegisgrid.grid.cost", product="aegisgrid", category="performance", tags=("energy",)),
    ])
    assert [x.scenario_id for x in cat.select()] == [
        "aegisgrid.grid.cost", "live_forge.poll", "nexus.plan.valid"
    ]
    assert len(cat.select(product="live_forge", tag="twitch")) == 1
    assert cat.select(product="nexus", tag="energy") == ()
    assert cat.get("nexus.plan.valid").title == "Valid plan"


def test_frozen_inputs():
    original = {"settings": {"max_attempts": 3}, "items": [1, 2]}
    c = case(inputs=original)
    original["settings"]["max_attempts"] = 999
    c.inputs["items"].append(3)
    assert c.inputs["settings"]["max_attempts"] == 3
    assert c.inputs["items"] == [1, 2]
    with pytest.raises(FrozenInstanceError):
        c.title = "tamper"


def test_frozen_expected():
    value = {"count": [2]}
    e = ScenarioExpectation("result", "equals", value)
    value["count"].append(3)
    e.expected["count"].append(4)
    assert e.expected == {"count": [2]}


@pytest.mark.parametrize("operator, expected", [("equals", None), ("not_equals", False),
                                                ("gte", 1), ("lte", 2.5), ("contains", "abc")])
def test_expected_operators(operator, expected):
    assert ScenarioExpectation("output.value", operator, expected).expected == expected


def test_exists_omits_expected():
    assert ScenarioExpectation("output.value", "exists").to_dict() == {
        "path": "output.value", "operator": "exists"
    }
    with pytest.raises(Error):
        ScenarioExpectation("output", "exists", None)


@pytest.mark.parametrize("invalid", ["eval", "import", "lambda", "python", "__class__()", ""])
def test_invalid_operators(invalid):
    with pytest.raises(Error):
        ScenarioExpectation("status", invalid, True)


@pytest.mark.parametrize("path", ["a[0]", "__import__('os')", "a..b", "a;rm", ".a", "a/other"])
def test_reject_expression_paths(path):
    with pytest.raises(Error):
        ScenarioExpectation(path, "equals", True)


@pytest.mark.parametrize("sid", ["../secret", "A.BAD", "ab", "a/b", "", "a:" ] )
def test_invalid_scenario_ids(sid):
    with pytest.raises(Error):
        case(sid)


@pytest.mark.parametrize("changes", [
    {"mode": "execute_shell"}, {"product": "unknown"},
    {"category": "exploit"}, {"max_seconds": 0}, {"max_seconds": 3601},
    {"revision": 0}, {"revision": True}, {"title": "\n"},
    {"tags": ("a", "a")}, {"tags": ("NO-UPPER",)},
    {"expectations": ()}, {"expectations": (ScenarioExpectation("a", "equals", 1),) * 2},
    {"inputs": []}, {"inputs": {"api_key": "should not store"}},
    {"inputs": {"nested": {"access_token": "do not capture"}}},
    {"inputs": {"number": float("nan")}},
    {"inputs": {"bytes": b"xxx"}},
    {"inputs": {"big": 10**100}},
    {"inputs": {"text": "x"*13000}},
])
def test_invalid_case(changes):
    with pytest.raises(Error):
        case(**changes)


def test_external_is_metadata_not_permission():
    external = case("live_forge.event.external", product="live_forge", mode="external")
    assert external.requires_approval
    assert not hasattr(NexusScenarioCatalog, "execute")
    assert not hasattr(ScenarioCase, "execute")


def test_atomic_registration_duplicate_and_limit():
    cat = NexusScenarioCatalog(limits=ScenarioCatalogLimits(max_scenarios=2))
    cat.register(case())
    before = cat.snapshot()
    with pytest.raises(Error):
        cat.register_many([case("aegisgrid.grid.cost", product="aegisgrid"), case()])
    assert cat.snapshot() == before
    with pytest.raises(Error):
        cat.register_many([case("x.other"), case("x.other")])
    assert cat.snapshot() == before
    with pytest.raises(Error):
        cat.register_many([case("x.other"), case("z.other")])
    assert cat.snapshot() == before


def test_register_many_rolls_back_on_snapshot_size():
    cat = NexusScenarioCatalog(limits=ScenarioCatalogLimits(max_snapshot_bytes=1200))
    with pytest.raises(Error):
        cat.register(case(inputs={"large": "x"*1800}))
    assert cat.count == 0


def test_compare_and_swap_replacement():
    cat = NexusScenarioCatalog()
    old = case()
    cat.register(old)
    new = case(revision=2, description="New detail")
    cat.replace(new, expected_sha256=old.case_sha256)
    assert cat.get(old.scenario_id).revision == 2
    with pytest.raises(Error):
        cat.replace(case(revision=3), expected_sha256=old.case_sha256)
    with pytest.raises(Error):
        cat.replace(case(revision=4), expected_sha256=new.case_sha256)
    assert cat.get(old.scenario_id).revision == 2


def test_hash_matches_canonical_payload():
    c = case()
    data = c.to_dict()
    digest = data.pop("case_sha256")
    assert digest == hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def test_modified_snapshot_and_rehashed_invalid_case():
    cat = NexusScenarioCatalog(); cat.register(case())
    good = cat.snapshot()
    altered = copy.deepcopy(good)
    altered["cases"][0]["title"] = "tampered"
    assert not NexusScenarioCatalog.verify_snapshot(altered)
    base = {k: v for k, v in altered.items() if k != "catalog_sha256"}
    altered["catalog_sha256"] = hashlib.sha256(json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    assert not NexusScenarioCatalog.verify_snapshot(altered)


def test_malformed_snapshot_count_unknown_and_unsorted():
    cat = NexusScenarioCatalog(); cat.register_many([case(), case("aegisgrid.grid.cost", product="aegisgrid")])
    original = cat.snapshot()
    for transform in [
        lambda x: x.update(count=3),
        lambda x: x.update(extra="unexpected"),
        lambda x: x["cases"].reverse(),
        lambda x: x["cases"].append(x["cases"][0]),
        lambda x: x.update(schema="wrong"),
        lambda x: x.update(catalog_sha256="0"*64),
    ]:
        obj = copy.deepcopy(original)
        transform(obj)
        assert not NexusScenarioCatalog.verify_snapshot(obj)


def test_empty_catalog_roundtrip():
    cat = NexusScenarioCatalog()
    assert NexusScenarioCatalog.from_snapshot(cat.snapshot()).snapshot() == cat.snapshot()


def test_too_many_scenarios_in_snapshot():
    cat = NexusScenarioCatalog()
    cat.register_many([case(f"nexus.case{i:03d}") for i in range(4)])
    assert not NexusScenarioCatalog.verify_snapshot(cat.snapshot(), limits=ScenarioCatalogLimits(max_scenarios=2))


def test_invalid_filter():
    cat = NexusScenarioCatalog()
    with pytest.raises(Error):
        cat.select(product="other")
    with pytest.raises(Error):
        cat.select(tag="../../etc")


def test_concurrent_duplicate_registration():
    cat = NexusScenarioCatalog()
    results = []
    def attempt():
        try:
            cat.register(case())
            results.append("ok")
        except Error:
            results.append("rejected")
    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert results.count("ok") == 1
    assert results.count("rejected") == 9
    assert cat.count == 1


def test_deterministic_snapshot_independent_of_insertion_order():
    a, b = case(), case("aegisgrid.grid.cost", product="aegisgrid")
    x = NexusScenarioCatalog(); x.register_many([a, b])
    y = NexusScenarioCatalog(); y.register_many([b, a])
    assert x.snapshot() == y.snapshot()


def test_no_io_by_default(tmp_path, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("unexpected file/network/command action")
    import subprocess
    monkeypatch.setattr(subprocess, "run", forbid)
    snap = NexusScenarioCatalog()
    snap.register(case())
    snap.select()
    snap.snapshot()
    assert list(tmp_path.iterdir()) == []


def test_wrong_input_types_fail_closed():
    with pytest.raises(Error):
        case(product=[])
    with pytest.raises(Error):
        case(tags=(["bad"],))
    with pytest.raises(Error):
        ScenarioExpectation("x", [], True)
    cat = NexusScenarioCatalog()
    with pytest.raises(Error):
        cat.select(mode=[])


def test_snapshot_with_bad_nested_case_fails_without_executing():
    cat = NexusScenarioCatalog(); cat.register(case())
    snap = cat.snapshot()
    snap["cases"][0]["tags"] = [["malformed"]]
    # Re-hash catalog to ensure we also validate the individual case contract.
    base = {k: v for k, v in snap.items() if k != "catalog_sha256"}
    snap["catalog_sha256"] = hashlib.sha256(
        json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    assert not NexusScenarioCatalog.verify_snapshot(snap)
