"""Offline tests for aegisforge.nexus.proof.proof_graph (no Nebius/Docker)."""

import copy
import hashlib
import json
import unittest

from aegisforge.nexus.evidence import NexusEvidenceTrail
from aegisforge.nexus.executor import NexusExecutor
from aegisforge.nexus.planner import NexusPlan, PlanStep
from aegisforge.nexus.verifier import IndependentCheck, NexusVerifier
from aegisforge.nexus.proof.proof_graph import (
    NexusProofGraph, NexusProofGraphError, ProofGraphLimits,
)


def sample_trail(stage_count=3):
    plan = NexusPlan(
        goal="Offline demonstration of an audit graph",
        steps=(
            PlanStep(id="s1", kind="reason", title="Review",
                     description="Analyze a harmless example",
                     success_criteria="Documented reasoning"),
            PlanStep(id="s2", kind="verify", title="Verify",
                     description="Perform independent review",
                     success_criteria="A host-owned check is recorded",
                     depends_on=("s1",)),
        ),
        model="offline-fixture",
    )
    trail = NexusEvidenceTrail()
    trail.record_plan(plan)
    if stage_count >= 2:
        execution = NexusExecutor().run(plan, manual_evidence={"s1": "fixture", "s2": "fixture"})
        trail.record_execution(execution)
        if stage_count >= 3:
            verification = NexusVerifier().verify(
                plan, execution,
                independent_checks=(IndependentCheck(
                    id="fixture-check", step_id="s2", description="Test fixture",
                    handler=lambda step, record: True, evidence_ref="fixture:check",
                ),),
            )
            trail.record_verification(verification)
    return trail


class ProofGraphTests(unittest.TestCase):
    def setUp(self):
        self.graph = NexusProofGraph("colossus.audit")

    def test_initial_mission_node(self):
        self.assertEqual(self.graph.snapshot()["node_count"], 1)

    def test_initial_zero_edges(self):
        self.assertEqual(self.graph.snapshot()["edge_count"], 0)

    def test_snapshot_verified(self):
        self.assertTrue(NexusProofGraph.verify_snapshot(self.graph.snapshot()))

    def test_invalid_mission(self):
        with self.assertRaises(NexusProofGraphError):
            NexusProofGraph("private mission with spaces")

    def test_raw_text_identifier_rejected(self):
        with self.assertRaises(NexusProofGraphError):
            self.graph.add_node("claim", "This is arbitrary raw task text")

    def test_identifier_not_exported(self):
        self.graph.add_node("claim", "secretlikeidentifier")
        self.assertNotIn("secretlikeidentifier", json.dumps(self.graph.snapshot()))

    def test_conflicting_redefinition(self):
        self.graph.add_node("claim", "claim-one", status="pending")
        with self.assertRaises(NexusProofGraphError):
            self.graph.add_node("claim", "claim-one", status="verified")

    def test_idempotent_identical_node(self):
        a = self.graph.add_node("claim", "claim-one", status="pending")
        b = self.graph.add_node("claim", "claim-one", status="pending")
        self.assertEqual(a, b)

    def test_reject_unsupported_status(self):
        with self.assertRaises(NexusProofGraphError):
            self.graph.add_node("claim", "one", status="this is verified by me")

    def test_reject_metadata_text(self):
        with self.assertRaises(NexusProofGraphError):
            self.graph.add_node("check", "a", metrics={"secret": 12})

    def test_reject_non_integer_counts(self):
        with self.assertRaises(NexusProofGraphError):
            self.graph.add_node("check", "a", metrics={"file_count": True})

    def test_valid_evidence_to_claim(self):
        evidence = self.graph.add_node("evidence", "record-a")
        claim = self.graph.add_node("claim", "claim-a")
        self.graph.add_edge("supports", evidence, claim)
        self.assertEqual(self.graph.snapshot()["edge_count"], 1)

    def test_invalid_support_direction(self):
        evidence = self.graph.add_node("evidence", "record-a")
        claim = self.graph.add_node("claim", "claim-a")
        with self.assertRaises(NexusProofGraphError):
            self.graph.add_edge("supports", claim, evidence)

    def test_reject_self_edge(self):
        step = self.graph.add_node("step", "s1")
        with self.assertRaises(NexusProofGraphError):
            self.graph.add_edge("depends_on", step, step)

    def test_reject_missing_endpoint(self):
        step = self.graph.add_node("step", "s1")
        with self.assertRaises(NexusProofGraphError):
            self.graph.add_edge("depends_on", step, "0" * 64)

    def test_idempotent_edge(self):
        a = self.graph.add_node("step", "s1")
        b = self.graph.add_node("step", "s2")
        first = self.graph.add_edge("depends_on", b, a)
        second = self.graph.add_edge("depends_on", b, a)
        self.assertEqual(first, second)
        self.assertEqual(self.graph.snapshot()["edge_count"], 1)

    def test_neighborhood(self):
        a = self.graph.add_node("step", "s1")
        b = self.graph.add_node("step", "s2")
        self.graph.add_edge("depends_on", b, a)
        self.assertEqual(len(self.graph.neighbors(a, direction="in")), 1)
        self.assertEqual(len(self.graph.neighbors(a, direction="out")), 0)

    def test_bounded_nodes(self):
        tiny = NexusProofGraph("colossus.tiny", limits=ProofGraphLimits(max_nodes=1))
        with self.assertRaises(NexusProofGraphError):
            tiny.add_node("claim", "extra")

    def test_bounded_edges(self):
        tiny = NexusProofGraph("colossus.tiny", limits=ProofGraphLimits(max_edges=1))
        a = tiny.add_node("step", "s1")
        b = tiny.add_node("step", "s2")
        c = tiny.add_node("step", "s3")
        tiny.add_edge("depends_on", c, b)
        with self.assertRaises(NexusProofGraphError):
            tiny.add_edge("depends_on", b, a)

    def test_bounded_export(self):
        tiny = NexusProofGraph("colossus.tiny", limits=ProofGraphLimits(max_snapshot_bytes=100))
        with self.assertRaises(NexusProofGraphError):
            tiny.snapshot()

    def test_verify_snapshot_detects_tampering(self):
        snapshot = self.graph.snapshot()
        snapshot["nodes"][0]["status"] = "verified"
        with self.assertRaises(NexusProofGraphError):
            NexusProofGraph.verify_snapshot(snapshot)

    def test_verify_snapshot_detects_invalid_edges_even_if_rehashed(self):
        evidence = self.graph.add_node("evidence", "e1")
        claim = self.graph.add_node("claim", "c1")
        self.graph.add_edge("supports", evidence, claim)
        snapshot = self.graph.snapshot()
        snapshot["edges"][0]["source_node_id"] = claim
        snapshot["edges"][0]["target_node_id"] = evidence
        body = {k: v for k, v in snapshot.items() if k != "graph_sha256"}
        snapshot["graph_sha256"] = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False).encode()).hexdigest()
        with self.assertRaises(NexusProofGraphError):
            NexusProofGraph.verify_snapshot(snapshot)

    def test_import_plan_only(self):
        result = self.graph.ingest_evidence_trail(sample_trail(1))
        self.assertEqual(result["evidence_events"], 1)
        self.assertTrue(NexusProofGraph.verify_snapshot(self.graph.snapshot()))

    def test_import_two_events(self):
        result = self.graph.ingest_evidence_trail(sample_trail(2))
        self.assertEqual(result["evidence_events"], 2)

    def test_import_three_events(self):
        result = self.graph.ingest_evidence_trail(sample_trail(3))
        self.assertEqual(result["evidence_events"], 3)
        snapshot = self.graph.snapshot()
        self.assertEqual(len([n for n in snapshot["nodes"] if n["kind"] == "evidence"]), 3)
        self.assertEqual(len([e for e in snapshot["edges"] if e["relation"] == "records"]), 3)
        self.assertTrue(NexusProofGraph.verify_snapshot(snapshot))

    def test_import_duplicate_run_rejected(self):
        trail = sample_trail(3)
        self.graph.ingest_evidence_trail(trail)
        before = self.graph.snapshot()
        with self.assertRaises(NexusProofGraphError):
            self.graph.ingest_evidence_trail(trail)
        self.assertEqual(before, self.graph.snapshot())

    def test_import_rejects_wrong_object(self):
        with self.assertRaises(NexusProofGraphError):
            self.graph.ingest_evidence_trail("not a trail")

    def test_import_rejects_empty_trail(self):
        with self.assertRaises(NexusProofGraphError):
            self.graph.ingest_evidence_trail(NexusEvidenceTrail())

    def test_import_rejects_modified_event(self):
        trail = sample_trail(1)
        old = trail._events[0]
        from dataclasses import replace
        trail._events[0] = replace(old, status="failed")
        with self.assertRaises(NexusProofGraphError):
            self.graph.ingest_evidence_trail(trail)

    def test_import_bad_hash_rolls_back(self):
        trail = sample_trail(3)
        from dataclasses import replace
        trail._events[2] = replace(trail._events[2], summary={"unexpected": "text"})
        before = self.graph.snapshot()
        with self.assertRaises(NexusProofGraphError):
            self.graph.ingest_evidence_trail(trail)
        self.assertEqual(before, self.graph.snapshot())

    def test_node_limit_import_rolls_back(self):
        tiny = NexusProofGraph("colossus.tiny", limits=ProofGraphLimits(max_nodes=4))
        before = tiny.snapshot()
        with self.assertRaises(NexusProofGraphError):
            tiny.ingest_evidence_trail(sample_trail(3))
        self.assertEqual(tiny.snapshot(), before)

    def test_graph_order_independent_snapshot(self):
        one = NexusProofGraph("colossus.same")
        two = NexusProofGraph("colossus.same")
        one.add_node("claim", "a")
        one.add_node("claim", "b")
        two.add_node("claim", "b")
        two.add_node("claim", "a")
        self.assertEqual(one.snapshot()["graph_sha256"], two.snapshot()["graph_sha256"])

    def test_trust_flags_never_prove_authenticity(self):
        snapshot = self.graph.snapshot()
        self.assertFalse(snapshot["authenticity_verified"])
        self.assertTrue(snapshot["integrity_only"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
