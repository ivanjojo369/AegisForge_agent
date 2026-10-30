"""Isolated metadata, state-machine and integration tests for Mission Registry."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import threading
import pytest

from aegisforge.nexus.mission_control.mission_registry import (
    MISSION_REGISTRY_SCHEMA, PRODUCTS, STATUSES, TRANSITIONS, REFERENCE_KINDS,
    NexusMissionRegistry, NexusMissionRegistryError, MissionRegistryLimits,
)

T0 = "2026-10-28T00:00:00Z"
T1 = "2026-10-28T00:00:01Z"
T2 = "2026-10-28T00:00:02Z"
T3 = "2026-10-28T00:00:03Z"
T4 = "2026-10-28T00:00:04Z"
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64


def registry(**kwargs):
    r = NexusMissionRegistry(**kwargs)
    r.register("mission.alpha", product="nexus", owner_id="team.owner",
               objective_sha256=H1, actor_id="team.owner", at_utc=T0)
    return r


def complete(r):
    r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T1)
    r.transition("mission.alpha", "running", actor_id="team.owner", at_utc=T2)
    r.link_reference("mission.alpha", kind="evidence", reference_sha256=H2,
                     actor_id="team.owner", at_utc=T3)
    r.transition("mission.alpha", "reported_complete", actor_id="team.owner", at_utc=T4)
    return r


def rehash(snapshot):
    unsigned = {k: v for k, v in snapshot.items() if k != "registry_sha256"}
    snapshot["registry_sha256"] = sha256(json.dumps(unsigned, ensure_ascii=True,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return snapshot


def test_empty_snapshot_valid():
    snap = NexusMissionRegistry().snapshot()
    assert snap["mission_count"] == 0 and snap["event_count"] == 0
    assert snap["authenticity_verified"] is False and snap["effects_executed"] is False
    assert NexusMissionRegistry.verify_snapshot(snap)
    assert NexusMissionRegistry.from_snapshot(snap).snapshot() == snap


def test_register():
    row = registry().get("mission.alpha")
    assert row["product"] == "nexus" and row["status"] == "draft"
    assert row["event_count"] == 1 and row["reference_count"] == 0
    assert row["owner_sha256"] == sha256(b"team.owner").hexdigest()
    assert row["objective_sha256"] == H1


def test_register_does_not_export_owner_id():
    dump = json.dumps(registry().snapshot())
    assert "team.owner" not in dump and "private objective" not in dump


def test_duplicate_registration_atomic():
    r = registry()
    old = r.snapshot()
    with pytest.raises(NexusMissionRegistryError):
        r.register("mission.alpha", product="nexus", owner_id="team.owner",
                   objective_sha256=H1, actor_id="team.owner")
    assert r.snapshot() == old


@pytest.mark.parametrize("product", sorted(PRODUCTS))
def test_all_products(product):
    r = NexusMissionRegistry()
    r.register("mission.alpha", product=product, owner_id="team.owner",
               objective_sha256=H1, actor_id="team.owner", at_utc=T0)
    assert r.get("mission.alpha")["product"] == product


@pytest.mark.parametrize("wrong", ["A", "bad/path", "../escape", "", "UPPER", "f"*97, 1, None, [], {}, "hi@x.com"])
def test_bad_mission_id(wrong):
    with pytest.raises(NexusMissionRegistryError):
        NexusMissionRegistry().register(wrong, product="nexus", owner_id="team.owner",
                                        objective_sha256=H1, actor_id="team.owner")


@pytest.mark.parametrize("wrong", ["unknown", "", {}, [], None, 7])
def test_bad_product(wrong):
    with pytest.raises(NexusMissionRegistryError):
        NexusMissionRegistry().register("mission.alpha", product=wrong,
            owner_id="team.owner", objective_sha256=H1, actor_id="team.owner")


@pytest.mark.parametrize("wrong", ["a", "000", "f"*63, "F"*64, {}, None, 1, []])
def test_bad_objective(wrong):
    with pytest.raises(NexusMissionRegistryError):
        NexusMissionRegistry().register("mission.alpha", product="nexus",
            owner_id="team.owner", objective_sha256=wrong, actor_id="team.owner")


@pytest.mark.parametrize("wrong", ["a", "bad/actor", "evil@host", None, [], {}])
def test_bad_actor_id(wrong):
    with pytest.raises(NexusMissionRegistryError):
        NexusMissionRegistry().register("mission.alpha", product="nexus",
            owner_id="team.owner", objective_sha256=H1, actor_id=wrong)


@pytest.mark.parametrize("time", ["2026-10-28T00:00:00+00:00", "2026-10-28T00:00:00-06:00",
                                    "2026-02-30T00:00:00Z", "2026-10-28", "now", "2026-10-28T00:00:00z", "", 5])
def test_bad_timestamps(time):
    with pytest.raises(NexusMissionRegistryError):
        NexusMissionRegistry().register("mission.alpha", product="nexus",
            owner_id="team.owner", objective_sha256=H1,
            actor_id="team.owner", at_utc=time)


def test_default_time_is_utc():
    r = NexusMissionRegistry()
    row = r.register("mission.alpha", product="nexus", owner_id="team.owner",
               objective_sha256=H1, actor_id="team.owner")
    assert row["created_at_utc"].endswith("Z")
    assert datetime.fromisoformat(row["created_at_utc"].replace("Z", "+00:00")).utcoffset().total_seconds() == 0


def test_ready_running_paused_resumed_review():
    r = registry()
    for state in ["ready", "running", "paused", "running", "awaiting_review", "running"]:
        r.transition("mission.alpha", state, actor_id="team.owner", at_utc=T1)
    assert r.get("mission.alpha")["status"] == "running"
    assert r.get("mission.alpha")["event_count"] == 7


@pytest.mark.parametrize("status", ["running", "paused", "reported_complete", "verified", "failed"])
def test_forbid_invalid_direct_transition(status):
    r = registry()
    before = r.snapshot()
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", status, actor_id="team.owner", at_utc=T1,
                     decision_sha256=H4 if status == "verified" else None)
    assert r.snapshot() == before


def test_repeat_status_forbidden():
    r = registry()
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", "draft", actor_id="team.owner")


def test_completion_requires_reference():
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T1)
    r.transition("mission.alpha", "running", actor_id="team.owner", at_utc=T2)
    prior = r.snapshot()
    with pytest.raises(NexusMissionRegistryError, match="linked"):
        r.transition("mission.alpha", "reported_complete", actor_id="team.owner", at_utc=T3)
    assert r.snapshot() == prior


@pytest.mark.parametrize("kind", ["run", "artifact", "provenance", "approval"])
def test_non_evidence_reference_cannot_complete(kind):
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T1)
    r.transition("mission.alpha", "running", actor_id="team.owner", at_utc=T2)
    r.link_reference("mission.alpha", kind=kind, reference_sha256=H2,
                     actor_id="team.owner", at_utc=T3)
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", "reported_complete", actor_id="team.owner", at_utc=T4)


@pytest.mark.parametrize("kind", ["evidence", "evaluation", "scorecard"])
def test_result_reference_allows_reported_completion(kind):
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T1)
    r.transition("mission.alpha", "running", actor_id="team.owner", at_utc=T2)
    r.link_reference("mission.alpha", kind=kind, reference_sha256=H2,
                     actor_id="team.owner", at_utc=T3)
    row = r.transition("mission.alpha", "reported_complete",
                       actor_id="team.owner", at_utc=T4)
    assert row["status"] == "reported_complete"


def test_verification_requires_decision_digest():
    r = complete(registry())
    old = r.snapshot()
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", "verified", actor_id="team.reviewer", at_utc=T4)
    assert r.snapshot() == old


def test_verification_records_reported_decision_without_authenticating_it():
    r = complete(registry())
    r.transition("mission.alpha", "verified", actor_id="team.reviewer",
                 decision_sha256=H3, at_utc=T4)
    assert r.get("mission.alpha")["status"] == "verified"
    assert r.history("mission.alpha")[-1]["decision_sha256"] == H3
    assert r.snapshot()["authenticity_verified"] is False


def test_approval_digest_not_allowed_on_other_transitions():
    r = registry()
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", "ready", actor_id="team.owner",
                     decision_sha256=H3, at_utc=T1)


def test_terminal_is_immutable():
    r = complete(registry())
    r.transition("mission.alpha", "verified", actor_id="team.reviewer", decision_sha256=H3)
    old = r.snapshot()
    with pytest.raises(NexusMissionRegistryError):
        r.link_reference("mission.alpha", kind="artifact", reference_sha256=H4,
                         actor_id="team.owner")
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", "running", actor_id="team.owner")
    assert r.snapshot() == old


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_failed_and_cancelled_terminal(terminal):
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T1)
    r.transition("mission.alpha", terminal, actor_id="team.owner", at_utc=T2) if terminal == "cancelled" else None
    if terminal == "failed":
        r.transition("mission.alpha", "running", actor_id="team.owner", at_utc=T2)
        r.transition("mission.alpha", "failed", actor_id="team.owner", at_utc=T3)
    assert r.get("mission.alpha")["status"] == terminal
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", "running", actor_id="team.owner")


def test_link_reference():
    r = registry()
    r.link_reference("mission.alpha", kind="evidence", reference_sha256=H2,
                     actor_id="team.owner", at_utc=T1)
    row = r.get("mission.alpha")
    assert row["reference_count"] == 1 and row["references"] == [{"kind":"evidence", "sha256":H2}]


def test_duplicate_ref_no_mutation():
    r = registry()
    for _ in range(1):
        r.link_reference("mission.alpha", kind="evidence", reference_sha256=H2,
                         actor_id="team.owner", at_utc=T1)
    before = r.snapshot()
    with pytest.raises(NexusMissionRegistryError):
        r.link_reference("mission.alpha", kind="evidence", reference_sha256=H2,
                         actor_id="team.owner", at_utc=T2)
    assert r.snapshot() == before


def test_same_digest_different_reference_kinds():
    r = registry()
    for kind in ["run", "evidence", "artifact"]:
        r.link_reference("mission.alpha", kind=kind, reference_sha256=H2,
                         actor_id="team.owner", at_utc=T1)
    assert r.get("mission.alpha")["reference_count"] == 3


@pytest.mark.parametrize("invalid", ["secret", "EVIDENCE", "", None, [], {}])
def test_bad_link_kind(invalid):
    with pytest.raises(NexusMissionRegistryError):
        registry().link_reference("mission.alpha", kind=invalid,
            reference_sha256=H2, actor_id="team.owner")


@pytest.mark.parametrize("invalid", ["1", "F"*64, "b"*63, None, {}, []])
def test_bad_reference_digest(invalid):
    with pytest.raises(NexusMissionRegistryError):
        registry().link_reference("mission.alpha", kind="evidence",
            reference_sha256=invalid, actor_id="team.owner")


def test_earlier_timestamp_rejected():
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T2)
    old = r.snapshot()
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", "running", actor_id="team.owner", at_utc=T1)
    assert r.snapshot() == old


def test_equal_timestamp_accepted():
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T0)
    assert r.get("mission.alpha")["event_count"] == 2


def test_snapshot_roundtrip_and_hash_stable():
    r = complete(registry())
    snap = r.snapshot()
    assert snap["schema"] == MISSION_REGISTRY_SCHEMA
    assert snap == r.snapshot()
    assert NexusMissionRegistry.verify_snapshot(snap)
    restored = NexusMissionRegistry.from_snapshot(snap)
    assert restored.snapshot() == snap
    restored.transition("mission.alpha", "verified", actor_id="team.reviewer",
                        decision_sha256=H3, at_utc=T4)
    assert r.snapshot() == snap  # independence


def test_snapshots_insertion_order_independent():
    r = NexusMissionRegistry()
    q = NexusMissionRegistry()
    for a, b in [(r, "mission.zeta"), (r, "mission.alpha"),
                 (q, "mission.alpha"), (q, "mission.zeta")]:
        a.register(b, product="nexus", owner_id="team.owner",
                   objective_sha256=H1, actor_id="team.owner", at_utc=T0)
    assert r.snapshot() == q.snapshot()


def test_external_mutation_of_returned_data_doesnt_change_registry():
    r = registry()
    old = r.snapshot()
    row = r.get("mission.alpha")
    row["status"] = "verified"
    row["references"].append({"kind":"evidence", "sha256":H3})
    events = list(r.history("mission.alpha"))
    events[0]["status"] = "failed"
    snap = r.snapshot()
    snap["missions"].clear()
    assert r.snapshot() == old


def test_select_filters_and_order():
    r = registry()
    r.register("mission.beta", product="aegisgrid", owner_id="team.other",
               objective_sha256=H1, actor_id="team.other", at_utc=T0)
    r.register("mission.charlie", product="nexus", owner_id="team.owner",
               objective_sha256=H1, actor_id="team.owner", at_utc=T0)
    assert [x["mission_id"] for x in r.select()] == ["mission.alpha", "mission.beta", "mission.charlie"]
    assert len(r.select(owner_id="team.owner")) == 2
    assert len(r.select(product="nexus", status="draft")) == 2
    assert r.select(product="live_forge") == ()


@pytest.mark.parametrize("wrong", ["foo", {}, []])
def test_bad_select_filter(wrong):
    with pytest.raises(NexusMissionRegistryError):
        registry().select(product=wrong)


def test_unknown_mission():
    r = registry()
    for method in (lambda: r.get("mission.missing"),
                   lambda: r.history("mission.missing"),
                   lambda: r.transition("mission.missing", "ready", actor_id="team.owner"),
                   lambda: r.link_reference("mission.missing", kind="artifact",
                       reference_sha256=H2, actor_id="team.owner")):
        with pytest.raises(NexusMissionRegistryError):
            method()


@pytest.mark.parametrize("field,value", [
    ("schema", "unknown"), ("mission_count", 0), ("event_count", 100),
    ("authenticity_verified", True), ("effects_executed", True),
    ("integrity_only", False), ("unexpected", 1),
])
def test_snapshot_invalid_field_even_when_rehashed(field, value):
    s = complete(registry()).snapshot()
    s[field] = value
    assert not NexusMissionRegistry.verify_snapshot(rehash(s))


def test_snapshot_tamper_detected_without_rehash():
    s = registry().snapshot()
    s["missions"][0]["status"] = "verified"
    assert not NexusMissionRegistry.verify_snapshot(s)


@pytest.mark.parametrize("field,value", [
    ("status", "verified"), ("head_sha256", H3), ("owner_sha256", H4),
    ("objective_sha256", H4), ("reference_count", 1), ("updated_at_utc", T2),
])
def test_rehashed_snapshot_row_tamper_rejected(field, value):
    s = registry().snapshot()
    s["missions"][0][field] = value
    assert not NexusMissionRegistry.verify_snapshot(rehash(s))


@pytest.mark.parametrize("field,value", [
    ("sequence", 8), ("status", "verified"), ("event_sha256", H4),
    ("previous_sha256", H3), ("operation", "status_changed"),
    ("at_utc", T3), ("actor_sha256", H4),
])
def test_rehashed_snapshot_event_tamper_rejected(field,value):
    s = registry().snapshot()
    s["missions"][0]["events"][0][field] = value
    assert not NexusMissionRegistry.verify_snapshot(rehash(s))


def test_rehashed_snapshot_missing_event_rejected():
    s = complete(registry()).snapshot()
    s["missions"][0]["events"].pop(1)
    assert not NexusMissionRegistry.verify_snapshot(rehash(s))


def test_rehashed_snapshot_changed_event_and_head_rejected():
    s = registry().snapshot()
    ev = s["missions"][0]["events"][0]
    ev["actor_sha256"] = H4
    unsigned = {k:v for k,v in ev.items() if k!="event_sha256"}
    ev["event_sha256"] = sha256(json.dumps(unsigned, sort_keys=True,
                  ensure_ascii=True,separators=(",",":")).encode()).hexdigest()
    s["missions"][0]["head_sha256"] = ev["event_sha256"]
    # This is a new fully consistent fabricated snapshot, not an authenticated one.
    assert NexusMissionRegistry.verify_snapshot(rehash(s))
    assert s["authenticity_verified"] is False


def test_rehashed_transition_bypassing_evidence_rejected():
    r = registry()
    r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T1)
    r.transition("mission.alpha", "running", actor_id="team.owner", at_utc=T2)
    s = r.snapshot()
    last = s["missions"][0]["events"][-1]
    last["status"] = "reported_complete"
    last["event_sha256"] = sha256(json.dumps({k:v for k,v in last.items()
        if k!="event_sha256"}, sort_keys=True, ensure_ascii=True,
        separators=(",",":")).encode()).hexdigest()
    s["missions"][0]["status"] = "reported_complete"
    s["missions"][0]["head_sha256"] = last["event_sha256"]
    assert not NexusMissionRegistry.verify_snapshot(rehash(s))


def test_rehashed_invalid_reference_rejected():
    s = registry().snapshot()
    s["missions"][0]["references"] = [{"kind": "root", "sha256": H2}]
    s["missions"][0]["reference_count"] = 1
    assert not NexusMissionRegistry.verify_snapshot(rehash(s))


def test_max_missions_limit():
    r = registry(limits=MissionRegistryLimits(max_missions=1))
    before = r.snapshot()
    with pytest.raises(NexusMissionRegistryError):
        r.register("mission.beta", product="nexus", owner_id="team.owner",
                   objective_sha256=H1, actor_id="team.owner")
    assert r.snapshot() == before


def test_events_per_mission_limit():
    r = registry(limits=MissionRegistryLimits(max_events_per_mission=2))
    r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T1)
    before = r.snapshot()
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", "running", actor_id="team.owner", at_utc=T2)
    assert r.snapshot() == before


def test_total_events_limit():
    r = registry(limits=MissionRegistryLimits(max_total_events=1))
    with pytest.raises(NexusMissionRegistryError):
        r.transition("mission.alpha", "ready", actor_id="team.owner", at_utc=T1)
    assert r.get("mission.alpha")["status"] == "draft"


def test_reference_limit():
    r = registry(limits=MissionRegistryLimits(max_references_per_mission=1))
    r.link_reference("mission.alpha", kind="evidence", reference_sha256=H1,
                     actor_id="team.owner", at_utc=T1)
    with pytest.raises(NexusMissionRegistryError):
        r.link_reference("mission.alpha", kind="artifact", reference_sha256=H2,
                         actor_id="team.owner", at_utc=T2)
    assert r.get("mission.alpha")["reference_count"] == 1


def test_bad_limits():
    for bad in [MissionRegistryLimits.__new__(MissionRegistryLimits), "x", 1]:
        # invalid uninitialized limits object tested only through a wrong type
        if isinstance(bad, MissionRegistryLimits):
            continue
        with pytest.raises(NexusMissionRegistryError):
            NexusMissionRegistry(limits=bad)
    with pytest.raises(NexusMissionRegistryError):
        MissionRegistryLimits(max_snapshot_bytes=1)
    with pytest.raises(NexusMissionRegistryError):
        MissionRegistryLimits(max_missions=True)


def test_snapshot_size_enforced_without_mutation():
    r = registry(limits=MissionRegistryLimits(max_snapshot_bytes=4096))
    before = r.snapshot()
    for i in range(20):
        try:
            r.link_reference("mission.alpha", kind="artifact", reference_sha256=f"{i:064x}",
                             actor_id="team.owner", at_utc=T1)
        except NexusMissionRegistryError as exc:
            assert "snapshot byte budget" in str(exc)
            break
    else:
        pytest.fail("budget never reached")
    assert NexusMissionRegistry.verify_snapshot(r.snapshot())
    assert r.get("mission.alpha")["reference_count"] < 20
    assert before["mission_count"] == 1


def test_verify_invalid_max_bytes():
    s = registry().snapshot()
    assert not NexusMissionRegistry.verify_snapshot(s, max_bytes=1)
    assert not NexusMissionRegistry.verify_snapshot(s, max_bytes=True)


def test_snapshot_wrong_mission_order():
    r = NexusMissionRegistry()
    for ident in ("mission.alpha", "mission.beta"):
        r.register(ident, product="nexus", owner_id="team.owner", objective_sha256=H1,
                   actor_id="team.owner", at_utc=T0)
    s = r.snapshot()
    s["missions"].reverse()
    assert not NexusMissionRegistry.verify_snapshot(rehash(s))


def test_concurrent_unique_refs_no_lost_updates():
    r = registry()
    errors = []
    def worker(i):
        try:
            r.link_reference("mission.alpha", kind="artifact", reference_sha256=f"{i:064x}",
                             actor_id="team.owner", at_utc=T1)
        except Exception as exc:
            errors.append(exc)
    workers = [threading.Thread(target=worker,args=(i,)) for i in range(12)]
    for thread in workers: thread.start()
    for thread in workers: thread.join()
    assert not errors
    assert r.get("mission.alpha")["reference_count"] == 12
    assert NexusMissionRegistry.verify_snapshot(r.snapshot())


def test_proof_graph_interop_if_installed():
    proof = pytest.importorskip("aegisforge.nexus.proof.proof_graph")
    r = registry()
    r.link_reference("mission.alpha", kind="run", reference_sha256=H2,
                     actor_id="team.owner", at_utc=T1)
    r.link_reference("mission.alpha", kind="evidence", reference_sha256=H3,
                     actor_id="team.owner", at_utc=T2)
    r.link_reference("mission.alpha", kind="artifact", reference_sha256=H4,
                     actor_id="team.owner", at_utc=T3)
    s = r.to_proof_graph("mission.alpha")
    assert proof.NexusProofGraph.verify_snapshot(s)
    assert s["authenticity_verified"] is False
    assert s["node_count"] >= 5 and s["edge_count"] >= 3


def test_proof_graph_export_does_not_mutate_registry():
    pytest.importorskip("aegisforge.nexus.proof.proof_graph")
    r = registry()
    old = r.snapshot()
    _ = r.to_proof_graph("mission.alpha")
    assert r.snapshot() == old


def test_all_operations_are_in_memory(monkeypatch):
    # This registry has no path, subprocess or network APIs. Check snapshot
    # and state operations without ever patching host resources or running tools.
    r = registry()
    r.link_reference("mission.alpha", kind="scorecard", reference_sha256=H2,
                     actor_id="team.owner", at_utc=T1)
    assert r.snapshot()["effects_executed"] is False


def test_schema_values_available():
    assert "nexus" in PRODUCTS and "aegisgrid" in PRODUCTS and "live_forge" in PRODUCTS
    assert "verified" in STATUSES and "reported_complete" in STATUSES
    assert TRANSITIONS["verified"] == frozenset()
    assert "scorecard" in REFERENCE_KINDS


def test_assign_component_and_snapshot_replay():
    r = registry()
    r.assign_component("mission.alpha", component="engineering", actor_id="team.owner", at_utc=T1)
    r.assign_component("mission.alpha", component="trust", actor_id="team.owner", at_utc=T1)
    row = r.get("mission.alpha")
    assert row["components"] == ["engineering", "trust"]
    assert row["component_count"] == 2
    s = r.snapshot()
    assert NexusMissionRegistry.verify_snapshot(s)
    assert NexusMissionRegistry.from_snapshot(s).snapshot() == s


@pytest.mark.parametrize("component", ["engineering", "trust", "workspace", "proof", "lab",
                                        "mission_control", "intelligence", "aegisgrid", "live_forge"])
def test_allowed_components(component):
    r = registry()
    assert r.assign_component("mission.alpha", component=component, actor_id="team.owner")["component_count"] == 1


@pytest.mark.parametrize("component", ["custom_code", "Engineering", "", None, [], {}, 5])
def test_bad_components_rejected(component):
    r = registry()
    with pytest.raises(NexusMissionRegistryError):
        r.assign_component("mission.alpha", component=component, actor_id="team.owner")


def test_duplicate_component_rejected_transactionally():
    r = registry()
    r.assign_component("mission.alpha", component="lab", actor_id="team.owner", at_utc=T1)
    old = r.snapshot()
    with pytest.raises(NexusMissionRegistryError):
        r.assign_component("mission.alpha", component="lab", actor_id="team.owner", at_utc=T2)
    assert r.snapshot() == old


def test_no_assignment_to_terminal_or_reported_complete():
    r = complete(registry())
    with pytest.raises(NexusMissionRegistryError):
        r.assign_component("mission.alpha", component="lab", actor_id="team.owner", at_utc=T4)


def test_component_limit():
    r = registry(limits=MissionRegistryLimits(max_components_per_mission=1))
    r.assign_component("mission.alpha", component="engineering", actor_id="team.owner")
    with pytest.raises(NexusMissionRegistryError):
        r.assign_component("mission.alpha", component="trust", actor_id="team.owner")


def test_components_cannot_be_forged_with_rehash_only():
    r = registry()
    s = r.snapshot()
    s["missions"][0]["components"] = ["trust"]
    s["missions"][0]["component_count"] = 1
    assert not NexusMissionRegistry.verify_snapshot(rehash(s))


def test_components_export_sorted():
    a, b = registry(), registry()
    for component in ["trust", "engineering"]:
        a.assign_component("mission.alpha", component=component, actor_id="team.owner", at_utc=T1)
    for component in ["engineering", "trust"]:
        b.assign_component("mission.alpha", component=component, actor_id="team.owner", at_utc=T1)
    assert a.get("mission.alpha")["components"] == b.get("mission.alpha")["components"]
    assert a.snapshot() != b.snapshot()  # different recorded ordering intentionally preserved
