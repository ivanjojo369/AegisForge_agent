"""Offline regression checks for AegisForge Nexus provenance.py."""
import copy
from dataclasses import replace
from hashlib import sha256
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from aegisforge.nexus.proof.provenance import (
    NexusProvenanceError, NexusProvenanceLedger, ProvenanceLimits,
    PROVENANCE_SCHEMA,
)
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.evidence import NexusEvidenceTrail
from aegisforge.nexus.planner import NexusPlan, PlanStep
from aegisforge.nexus.executor import NexusExecutor
from aegisforge.nexus.verifier import NexusVerifier, IndependentCheck


def h(value):
    return sha256(value.encode()).hexdigest()


def add(ledger, name="file-a", **kwargs):
    return ledger.record("artifact", h(name), origin_type="tool", origin_id_sha256=h("tool-a"),
                         capture_method="reported", **kwargs)


def fresh_snapshot_sha(snapshot):
    body = {k:v for k,v in snapshot.items() if k != "snapshot_sha256"}
    snapshot["snapshot_sha256"] = sha256(json.dumps(body, sort_keys=True,
            separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def sample_trail(stages=3):
    plan = NexusPlan(
        goal="Offline integrity test",
        steps=(
            PlanStep(id="s1", kind="reason", title="Inspect", description="Review fixture",
                     success_criteria="Fixture reviewed"),
            PlanStep(id="s2", kind="verify", title="Check", description="Review evidence",
                     success_criteria="Independent check", depends_on=("s1",)),
        ),
        model="offline-test",
    )
    trail = NexusEvidenceTrail()
    trail.record_plan(plan)
    if stages >= 2:
        execution = NexusExecutor().run(plan, manual_evidence={"s1": "fixture", "s2": "fixture"})
        trail.record_execution(execution)
        if stages >= 3:
            report = NexusVerifier().verify(plan, execution, independent_checks=(
                IndependentCheck(id="independent-fixture", step_id="s2", description="Read fixture",
                                 handler=lambda step, record: True, evidence_ref="fixture:independent"),
            ))
            trail.record_verification(report)
    return trail


def test_empty_snapshot():
    led = NexusProvenanceLedger("colossus.audit")
    s = led.snapshot()
    assert s["record_count"] == 0 and s["terminal_sha256"] == "0" * 64
    assert NexusProvenanceLedger.verify_snapshot(s)


def test_invalid_mission():
    with pytest.raises(NexusProvenanceError):
        NexusProvenanceLedger("my private mission with spaces")


def test_invalid_limits():
    with pytest.raises(NexusProvenanceError):
        ProvenanceLimits(max_records=0)


def test_bool_limits():
    with pytest.raises(NexusProvenanceError):
        ProvenanceLimits(max_parents=True)


def test_record_append_and_sequence():
    led = NexusProvenanceLedger("colossus.audit")
    first = add(led)
    second = add(led,"file-b")
    assert first.sequence == 1 and second.sequence == 2
    assert second.previous_sha256 == first.record_sha256
    assert led.snapshot()["record_count"] == 2


def test_opaque_no_raw_names_exported():
    led = NexusProvenanceLedger("colossus.audit")
    add(led, "private-token-file-name")
    assert "private-token-file-name" not in json.dumps(led.snapshot())


def test_query_get_is_copy():
    led = NexusProvenanceLedger("colossus.audit")
    first = add(led)
    row = led.get(first.record_sha256)
    row["parent_record_ids"].append(h("forgery"))
    assert led.get(first.record_sha256)["parent_record_ids"] == []


def test_query_missing_returns_none():
    assert NexusProvenanceLedger("m1").get(h("unused")) is None


def test_find_subject():
    led = NexusProvenanceLedger("m1")
    add(led)
    add(led,"file-b")
    assert len(led.find_subject("artifact", h("file-a"))) == 1


def test_ancestors_multiple_parents():
    led = NexusProvenanceLedger("m1")
    a, b = add(led), add(led,"b")
    c = add(led,"c", operation="derived", parent_record_ids=(a.record_sha256,b.record_sha256))
    assert led.ancestors(c.record_sha256) == (a.record_sha256,b.record_sha256)


def test_ancestors_depth_limit():
    led = NexusProvenanceLedger("m1")
    a = add(led)
    b = add(led,"b", operation="derived", parent_record_ids=(a.record_sha256,))
    c = add(led,"c", operation="derived", parent_record_ids=(b.record_sha256,))
    assert led.ancestors(c.record_sha256, max_depth=1) == (b.record_sha256,)


def test_reject_parent_unknown_transactional():
    led = NexusProvenanceLedger("m1")
    original = led.snapshot()
    with pytest.raises(NexusProvenanceError):
        add(led, operation="derived", parent_record_ids=(h("missing"),))
    assert led.snapshot() == original


def test_reject_forward_parent_even_hashed():
    led = NexusProvenanceLedger("m1")
    with pytest.raises(NexusProvenanceError):
        add(led, parent_record_ids=(h("future"),))


def test_reject_duplicate_parent():
    led = NexusProvenanceLedger("m1")
    a = add(led)
    with pytest.raises(NexusProvenanceError):
        add(led,"b", parent_record_ids=(a.record_sha256,a.record_sha256))


def test_derived_requires_parent():
    with pytest.raises(NexusProvenanceError):
        add(NexusProvenanceLedger("m1"), operation="derived")


def test_reject_bad_subject_digest():
    with pytest.raises(NexusProvenanceError):
        NexusProvenanceLedger("m1").record("artifact", "arbitrary text", origin_type="host",
                                              origin_id_sha256=h("host"),capture_method="reported")


@pytest.mark.parametrize("key,value", [
    ("subject_kind", "private-file"),
    ("origin_type", "untrusted-provider"),
    ("capture_method", "passed"),
    ("operation", "publish"),
])
def test_reject_unsupported_labels(key, value):
    args = dict(subject_kind="artifact",subject_sha256=h("s"),origin_type="host",
                origin_id_sha256=h("o"),capture_method="reported",operation="registered")
    args[key] = value
    with pytest.raises(NexusProvenanceError):
        NexusProvenanceLedger("m1").record(**args)


def test_source_event_only_evidence():
    with pytest.raises(NexusProvenanceError):
        add(NexusProvenanceLedger("m1"), source_event_sha256=h("event"))


def test_invalid_timestamp():
    with pytest.raises(NexusProvenanceError):
        add(NexusProvenanceLedger("m1"),captured_at_utc="2026-10-28T12:00:00+03:00")


def test_timestamp_utc():
    led = NexusProvenanceLedger("m1")
    item = add(led, captured_at_utc="2026-10-28T12:00:00Z")
    assert item.captured_at_utc.endswith("Z")
    assert NexusProvenanceLedger.verify_snapshot(led.snapshot())


def test_max_records_limit():
    led = NexusProvenanceLedger("m1",limits=ProvenanceLimits(max_records=1))
    add(led)
    with pytest.raises(NexusProvenanceError):
        add(led,"b")


def test_max_parents_limit():
    led = NexusProvenanceLedger("m1",limits=ProvenanceLimits(max_parents=1))
    a,b=add(led),add(led,"b")
    with pytest.raises(NexusProvenanceError):
        add(led,"c",parent_record_ids=(a.record_sha256,b.record_sha256))


def test_snapshot_budget():
    led = NexusProvenanceLedger("m1",limits=ProvenanceLimits(max_snapshot_bytes=100))
    with pytest.raises(NexusProvenanceError):
        led.snapshot()


def test_snapshot_roundtrip():
    led = NexusProvenanceLedger("m1")
    a=add(led)
    add(led,"b",operation="derived",parent_record_ids=(a.record_sha256,))
    recovered = NexusProvenanceLedger.from_snapshot(led.snapshot())
    assert recovered.snapshot() == led.snapshot()


def test_restore_with_restrictive_limits():
    led = NexusProvenanceLedger("m1")
    add(led)
    add(led,"b")
    with pytest.raises(NexusProvenanceError):
        NexusProvenanceLedger.from_snapshot(led.snapshot(), limits=ProvenanceLimits(max_records=1))


def test_snapshot_detects_value_tamper():
    led = NexusProvenanceLedger("m1")
    add(led)
    snapshot = led.snapshot()
    snapshot["records"][0]["origin_id_sha256"] = h("other")
    with pytest.raises(NexusProvenanceError):
        NexusProvenanceLedger.verify_snapshot(snapshot)


def test_snapshot_rehashed_rejects_forward_parent():
    led = NexusProvenanceLedger("m1")
    a=add(led)
    snapshot=led.snapshot()
    snapshot["records"][0]["parent_record_ids"] = [h("nonexistent")]
    row=snapshot["records"][0]
    row["record_sha256"] = h("different")
    snapshot["terminal_sha256"] = row["record_sha256"]
    fresh_snapshot_sha(snapshot)
    with pytest.raises(NexusProvenanceError):
        NexusProvenanceLedger.verify_snapshot(snapshot)


def test_snapshot_rehashed_rejects_invalid_status():
    led = NexusProvenanceLedger("m1")
    add(led)
    snapshot=led.snapshot()
    snapshot["authenticity_verified"] = True
    fresh_snapshot_sha(snapshot)
    with pytest.raises(NexusProvenanceError):
        NexusProvenanceLedger.verify_snapshot(snapshot)


def test_snapshot_bad_terminal():
    led=NexusProvenanceLedger("m1")
    add(led)
    snapshot=led.snapshot()
    snapshot["terminal_sha256"] = h("other")
    fresh_snapshot_sha(snapshot)
    with pytest.raises(NexusProvenanceError):
        NexusProvenanceLedger.verify_snapshot(snapshot)


def test_import_original_evidence_one_event():
    led=NexusProvenanceLedger("m1")
    result=led.ingest_evidence_trail(sample_trail(1))
    assert result["imported_events"]==1
    assert NexusProvenanceLedger.verify_snapshot(led.snapshot())


def test_import_original_evidence_three_events():
    led=NexusProvenanceLedger("m1")
    result=led.ingest_evidence_trail(sample_trail(3))
    assert result["imported_events"]==3
    assert len(led.ancestors(result["last_record_sha256"]))==2


def test_duplicate_import_denied():
    led=NexusProvenanceLedger("m1")
    trail=sample_trail(2)
    led.ingest_evidence_trail(trail)
    before=led.snapshot()
    with pytest.raises(NexusProvenanceError):
        led.ingest_evidence_trail(trail)
    assert led.snapshot() == before


def test_import_bad_trail_rejects_no_changes():
    led=NexusProvenanceLedger("m1")
    trail=sample_trail(2)
    trail._events[1]=replace(trail._events[1], status="verified")
    before=led.snapshot()
    with pytest.raises(NexusProvenanceError):
        led.ingest_evidence_trail(trail)
    assert led.snapshot() == before


def test_import_capacity_atomic():
    led=NexusProvenanceLedger("m1",limits=ProvenanceLimits(max_records=2))
    before=led.snapshot()
    with pytest.raises(NexusProvenanceError):
        led.ingest_evidence_trail(sample_trail(3))
    assert led.snapshot()==before


def test_graph_projection_empty():
    ledger=NexusProvenanceLedger("m1")
    graph=ledger.to_proof_graph()
    assert graph.snapshot()["node_count"]==1
    assert NexusProofGraph.verify_snapshot(graph.snapshot())


def test_graph_projection_artifact_and_derivation():
    ledger=NexusProvenanceLedger("m1")
    a=add(ledger)
    add(ledger,"b",operation="derived",parent_record_ids=(a.record_sha256,))
    graph=ledger.to_proof_graph()
    snapshot=graph.snapshot()
    assert snapshot["edge_count"]==3 # two documents + one provenance derivation
    assert NexusProofGraph.verify_snapshot(snapshot)


def test_graph_projection_evidence():
    ledger=NexusProvenanceLedger("m1")
    ledger.ingest_evidence_trail(sample_trail(1))
    graph=ledger.to_proof_graph()
    assert NexusProofGraph.verify_snapshot(graph.snapshot())


def test_no_disk_io_module():
    ledger=NexusProvenanceLedger("m1")
    assert not hasattr(ledger,"save") and not hasattr(ledger,"write_output")


def test_thread_safe_append():
    ledger=NexusProvenanceLedger("m1")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: add(ledger, "file-"+str(i)), range(50)))
    assert ledger.snapshot()["record_count"]==50
    assert NexusProvenanceLedger.verify_snapshot(ledger.snapshot())


def test_duplicate_import_after_snapshot_restore_rejected():
    trail = sample_trail(2)
    ledger = NexusProvenanceLedger("m1")
    ledger.ingest_evidence_trail(trail)
    restored = NexusProvenanceLedger.from_snapshot(ledger.snapshot())
    with pytest.raises(NexusProvenanceError):
        restored.ingest_evidence_trail(trail)


def test_reject_unhashable_record_labels():
    ledger = NexusProvenanceLedger("m1")
    with pytest.raises(NexusProvenanceError):
        ledger.record(["artifact"], h("test"), origin_type="host",
                      origin_id_sha256=h("host"), capture_method="reported")


def test_malformed_parent_array_rejected_without_type_error():
    ledger = NexusProvenanceLedger("m1")
    add(ledger)
    snapshot = ledger.snapshot()
    snapshot["records"][0]["parent_record_ids"] = [{"bad": "dict"}]
    fresh_snapshot_sha(snapshot)
    with pytest.raises(NexusProvenanceError):
        NexusProvenanceLedger.verify_snapshot(snapshot)
