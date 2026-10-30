"""Offline contract/regression tests for Colossus replay; no shell, model, Docker or writes by replay."""
from __future__ import annotations
import copy
from hashlib import sha256
import json

import pytest

from aegisforge.nexus.proof.replay import NexusReplay, NexusReplayError, ReplayLimits, REPLAY_SCHEMA
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.proof.provenance import NexusProvenanceLedger
from aegisforge.nexus.proof.artifact_manifest import NexusArtifactManifest
from aegisforge.nexus.evidence import NexusEvidenceTrail
from aegisforge.nexus.planner import NexusPlan, PlanStep
from aegisforge.nexus.executor import NexusExecutor
from aegisforge.nexus.verifier import NexusVerifier, IndependentCheck

MISSION = "colossus.audit"


def h(s):
    return sha256(s.encode("utf8")).hexdigest()


def rehash(s, key):
    body = {k: v for k, v in s.items() if k != key}
    s[key] = sha256(json.dumps(body, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@pytest.fixture
def trail():
    plan = NexusPlan(goal="Fixture", steps=(
        PlanStep(id="s1", kind="reason", title="Review", description="Inspect fixture", success_criteria="Documented"),
        PlanStep(id="s2", kind="verify", title="Verify", description="Check evidence", success_criteria="Observed", depends_on=("s1",)),
    ), model="offline-fixture")
    t = NexusEvidenceTrail()
    t.record_plan(plan)
    execution = NexusExecutor().run(plan, manual_evidence={"s1": "checked", "s2": "checked"})
    t.record_execution(execution)
    t.record_verification(NexusVerifier().verify(plan, execution, independent_checks=(
        IndependentCheck(id="independent", step_id="s2", description="Review sample", handler=lambda *_: True, evidence_ref="fixture:sample"),
    )))
    return t


@pytest.fixture
def sources(tmp_path, trail):
    folder = tmp_path / "workspace"
    folder.mkdir()
    (folder / "file.py").write_bytes(b"value = 1\n")
    manifest = NexusArtifactManifest(MISSION, folder)
    artifact = manifest.register_file("source-a", "file.py", kind="source")
    graph = NexusProofGraph(MISSION)
    graph.ingest_evidence_trail(trail)
    ledger = NexusProvenanceLedger(MISSION)
    ledger.ingest_evidence_trail(trail)
    ledger.record("artifact", artifact.content_sha256, origin_type="host", origin_id_sha256=h("host"), capture_method="direct_observation")
    return dict(graph_snapshot=graph.snapshot(), provenance_snapshot=ledger.snapshot(), artifact_snapshot=manifest.snapshot(), evidence_trail=trail)


def test_requires_sources():
    with pytest.raises(NexusReplayError):
        NexusReplay(MISSION).reconstruct()


def test_invalid_mission():
    with pytest.raises(NexusReplayError):
        NexusReplay("secret with spaces")


@pytest.mark.parametrize("field", ["max_timeline_entries", "max_source_bytes", "max_report_bytes", "max_warnings"])
def test_limit_validation(field):
    with pytest.raises(NexusReplayError):
        ReplayLimits(**{field: 0})
    with pytest.raises(NexusReplayError):
        ReplayLimits(**{field: True})


def test_full_replay(sources):
    result = NexusReplay(MISSION).reconstruct(**sources)
    assert result["status"] == "consistent"
    assert result["integrity_verified"] and not result["authenticity_verified"]
    assert result["effects_replayed"] is False
    assert result["links"] == {"graph_evidence": "matched", "provenance_evidence": "matched", "manifest_provenance_matches": 1}
    assert result["source_counts"]["evidence_events"] == 3
    assert result["source_counts"]["artifact_records"] == 1
    assert result["source_counts"]["provenance_records"] == 4
    assert len(result["timeline"]) == 8
    assert NexusReplay.verify_report(result)


def test_deterministic_same_inputs(sources):
    a = NexusReplay(MISSION).reconstruct(**sources)
    b = NexusReplay(MISSION).reconstruct(**sources)
    assert a == b


def test_source_unchanged(sources):
    graph = copy.deepcopy(sources["graph_snapshot"])
    provenance = copy.deepcopy(sources["provenance_snapshot"])
    manifest = copy.deepcopy(sources["artifact_snapshot"])
    trail_json = sources["evidence_trail"].to_jsonl()
    NexusReplay(MISSION).reconstruct(**sources)
    assert (graph, provenance, manifest, trail_json) == (
        sources["graph_snapshot"], sources["provenance_snapshot"], sources["artifact_snapshot"], sources["evidence_trail"].to_jsonl())


def test_graph_only():
    g = NexusProofGraph(MISSION)
    report = NexusReplay(MISSION).reconstruct(graph_snapshot=g.snapshot())
    assert report["timeline"] == [] and report["source_counts"]["graph_nodes"] == 1
    assert NexusReplay.verify_report(report)


def test_provenance_only():
    led = NexusProvenanceLedger(MISSION)
    led.record("artifact", h("file"), origin_type="operator", origin_id_sha256=h("op"), capture_method="reported")
    report = NexusReplay(MISSION).reconstruct(provenance_snapshot=led.snapshot())
    assert report["timeline_count"] == 1 and report["timeline"][0]["stream"] == "provenance"


def test_artifact_only(tmp_path):
    (tmp_path / "file.py").write_bytes(b"print(1)\r\n")
    m = NexusArtifactManifest(MISSION, tmp_path)
    m.register_file("a", "file.py", kind="source")
    r = NexusReplay(MISSION).reconstruct(artifact_snapshot=m.snapshot())
    assert r["source_counts"]["artifact_records"] == 1
    assert r["source_counts"]["provenance_records"] == 0


def test_original_evidence_only(trail):
    r = NexusReplay(MISSION).reconstruct(evidence_trail=trail)
    assert r["timeline_count"] == 3
    assert [t["kind"] for t in r["timeline"]] == ["plan", "execution", "verification"]


def test_graph_trail_unlinked(trail):
    g = NexusProofGraph(MISSION)
    report = NexusReplay(MISSION).reconstruct(graph_snapshot=g.snapshot(), evidence_trail=trail)
    assert report["status"] == "partial"
    assert report["links"]["graph_evidence"] == "unlinked"


def test_provenance_trail_unlinked(trail):
    led = NexusProvenanceLedger(MISSION)
    led.record("artifact", h("a"), origin_type="host", origin_id_sha256=h("host"), capture_method="reported")
    report = NexusReplay(MISSION).reconstruct(provenance_snapshot=led.snapshot(), evidence_trail=trail)
    assert report["status"] == "partial"
    assert "provenance_does_not_reference_supplied_run" in report["warnings"]


def test_provenance_trail_partial(trail):
    led = NexusProvenanceLedger(MISSION)
    row = trail.events[0]
    led.record("evidence", row.sha256, origin_type="nexus_evidence",
        origin_id_sha256=sha256(trail.run_id.encode("ascii")).hexdigest(),
        capture_method="imported", source_event_sha256=row.sha256)
    report = NexusReplay(MISSION).reconstruct(provenance_snapshot=led.snapshot(), evidence_trail=trail)
    assert report["links"]["provenance_evidence"] == "incomplete"


def test_manifest_unlinked_provenance(tmp_path):
    (tmp_path / "a.py").write_bytes(b"a = 1\n")
    m = NexusArtifactManifest(MISSION, tmp_path)
    m.register_file("a", "a.py", kind="source")
    ledger = NexusProvenanceLedger(MISSION)
    report = NexusReplay(MISSION).reconstruct(artifact_snapshot=m.snapshot(), provenance_snapshot=ledger.snapshot())
    assert report["status"] == "partial"
    assert "no_artifact_content_digest_links_in_provenance" in report["warnings"]


def test_cross_mission_rejected(sources):
    with pytest.raises(NexusReplayError, match="cross-mission"):
        NexusReplay("another.mission").reconstruct(**sources)


@pytest.mark.parametrize("source,field", [
    ("graph_snapshot", "graph_sha256"), ("provenance_snapshot", "snapshot_sha256"),
    ("artifact_snapshot", "snapshot_sha256"),
])
def test_corrupted_snapshot_rejected(sources, source, field):
    altered = dict(sources)
    altered[source] = copy.deepcopy(altered[source])
    altered[source][field] = "0" * 64
    with pytest.raises(NexusReplayError, match="integrity"):
        NexusReplay(MISSION).reconstruct(**altered)


def test_rehashed_malformed_graph_rejected(sources):
    args = dict(sources)
    s = copy.deepcopy(args["graph_snapshot"])
    s["node_count"] += 1
    rehash(s, "graph_sha256")
    args["graph_snapshot"] = s
    with pytest.raises(NexusReplayError):
        NexusReplay(MISSION).reconstruct(**args)


def test_invalid_snapshot_type():
    with pytest.raises(NexusReplayError):
        NexusReplay(MISSION).reconstruct(graph_snapshot="not a graph")


def test_invalid_evidence_type():
    with pytest.raises(NexusReplayError):
        NexusReplay(MISSION).reconstruct(evidence_trail={"fake": True})


def test_timeline_entry_limit(sources):
    with pytest.raises(NexusReplayError, match="timeline"):
        NexusReplay(MISSION, limits=ReplayLimits(max_timeline_entries=2)).reconstruct(**sources)


def test_source_size_limit(sources):
    with pytest.raises(NexusReplayError, match="byte budget"):
        NexusReplay(MISSION, limits=ReplayLimits(max_source_bytes=100)).reconstruct(**sources)


def test_report_size_limit(sources):
    with pytest.raises(NexusReplayError, match="report"):
        NexusReplay(MISSION, limits=ReplayLimits(max_report_bytes=100)).reconstruct(**sources)


def test_warning_limit(trail):
    g = NexusProofGraph(MISSION)
    l = NexusProvenanceLedger(MISSION)
    with pytest.raises(NexusReplayError, match="warning"):
        NexusReplay(MISSION, limits=ReplayLimits(max_warnings=1)).reconstruct(
            graph_snapshot=g.snapshot(), provenance_snapshot=l.snapshot(), evidence_trail=trail)


def test_report_digest_tamper(sources):
    r = NexusReplay(MISSION).reconstruct(**sources)
    r["status"] = "partial"
    with pytest.raises(NexusReplayError):
        NexusReplay.verify_report(r)


def test_report_flag_not_authentic(sources):
    r = NexusReplay(MISSION).reconstruct(**sources)
    r["authenticity_verified"] = True
    rehash(r, "report_sha256")
    with pytest.raises(NexusReplayError, match="flags"):
        NexusReplay.verify_report(r)


def test_report_no_replayed_effects(sources):
    r = NexusReplay(MISSION).reconstruct(**sources)
    r["effects_replayed"] = True
    rehash(r, "report_sha256")
    with pytest.raises(NexusReplayError, match="flags"):
        NexusReplay.verify_report(r)


def test_report_missing_timeline(sources):
    r = NexusReplay(MISSION).reconstruct(**sources)
    r["timeline"].pop()
    rehash(r, "report_sha256")
    with pytest.raises(NexusReplayError, match="timeline"):
        NexusReplay.verify_report(r)


def test_report_unsorted(sources):
    r = NexusReplay(MISSION).reconstruct(**sources)
    r["timeline"].reverse()
    rehash(r, "report_sha256")
    with pytest.raises(NexusReplayError, match="deterministic"):
        NexusReplay.verify_report(r)


def test_missing_but_rehashed_manifest_record(sources):
    args = dict(sources)
    s = copy.deepcopy(args["artifact_snapshot"])
    s["records"][0]["size_bytes"] += 1
    rehash(s, "snapshot_sha256")
    args["artifact_snapshot"] = s
    with pytest.raises(NexusReplayError):
        NexusReplay(MISSION).reconstruct(**args)


def test_nothing_sensitive_in_report(sources):
    result = NexusReplay(MISSION).reconstruct(**sources)
    raw = json.dumps(result)
    assert "value = 1" not in raw and "file.py" not in raw
    assert "Fixture" not in raw and "Inspect fixture" not in raw


def test_offline_status_and_schema(sources):
    result = NexusReplay(MISSION).reconstruct(**sources)
    assert result["schema"] == REPLAY_SCHEMA
    assert result["source_sha256"]["graph"] == sources["graph_snapshot"]["graph_sha256"]
    assert result["status"] == "consistent"


def test_requires_explicit_snapshots_no_filesystem_read(tmp_path):
    (tmp_path / "a.py").write_bytes(b"x = 1")
    report = NexusReplay(MISSION).reconstruct(graph_snapshot=NexusProofGraph(MISSION).snapshot())
    assert report["source_counts"]["artifact_records"] == 0


def test_report_bad_source_counts(sources):
    r = NexusReplay(MISSION).reconstruct(**sources)
    r["source_counts"]["artifact_records"] = 99
    rehash(r, "report_sha256")
    with pytest.raises(NexusReplayError, match="disagree"):
        NexusReplay.verify_report(r)


def test_report_rejects_duplicate_sequence_even_when_rehashed(sources):
    report = NexusReplay(MISSION).reconstruct(**sources)
    indices = [i for i, row in enumerate(report["timeline"]) if row["stream"] == "provenance"]
    report["timeline"][indices[1]]["sequence"] = report["timeline"][indices[0]]["sequence"]
    rehash(report, "report_sha256")
    with pytest.raises(NexusReplayError, match="duplicate"):
        NexusReplay.verify_report(report)


def test_report_rejects_invented_label_even_when_rehashed(sources):
    report = NexusReplay(MISSION).reconstruct(**sources)
    report["timeline"][0]["status"] = "trust_me_this_was_approved"
    rehash(report, "report_sha256")
    with pytest.raises(NexusReplayError, match="labels"):
        NexusReplay.verify_report(report)


def test_report_rejects_gap_in_sequence_even_when_rehashed(sources):
    report = NexusReplay(MISSION).reconstruct(**sources)
    index = next(i for i, row in enumerate(report["timeline"]) if row["stream"] == "artifact")
    report["timeline"][index]["sequence"] = 9
    rehash(report, "report_sha256")
    with pytest.raises(NexusReplayError, match="non-contiguous"):
        NexusReplay.verify_report(report)
