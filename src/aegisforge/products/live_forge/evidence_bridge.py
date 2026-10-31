"""Local, read-only evidence adapter for Live Forge -> Colossus.

Records only operator-confirmed decisions, no chatter IDs, message content, OAuth
credentials, stream actions or raw chat. Reuses Nexus Laboratory, ProofGraph,
MissionRegistry, SourceCapture and archive history; never grants Trust rights.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .policy_engine import StreamerPolicy
from .creative_studio import verify_transition
from .activity_bridge import verify_transition as verify_activity_transition

_ACTOR = "operator.local"
_CASE = "live_forge.round_integrity"
_ARCHIVE = "mission_sources.json"
_ALLOWED = frozenset({"round_closed", "round_applied", "operator_preview", "emergency_stop", "studio_apply", "studio_reset", "bridge_apply", "bridge_reset"})


def _digest(data: Any) -> str:
    return sha256(json.dumps(data, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _bounded_event(operation: str, votes: dict[str, Any],
                   policy_data: dict[str, Any], effect: dict[str, Any] | None,
                   blocked_reason: str | None, live_connected: bool,
                   studio_transition: dict[str, Any] | None = None,
                   activity_transition: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, bool]]:
    """Replay non-sensitive totals and policy; never trust an exported passed flag."""
    if operation not in _ALLOWED or type(live_connected) is not bool:
        raise ValueError("unsupported_audit_operation")
    policy = StreamerPolicy.from_payload(policy_data)
    if type(votes) is not dict:
        raise ValueError("invalid_aggregate")
    for key in ("round", "choice_a", "choice_b", "unique_voters", "accepted_vote_events"):
        limit = 50000 if key == "accepted_vote_events" else 1000000 if key == "round" else 5000
        if type(votes.get(key)) is not int or not 0 <= votes[key] <= limit:
            raise ValueError("invalid_vote_totals")
    if type(votes.get("round_open")) is not bool:
        raise ValueError("invalid_round_status")
    counts_match = votes["choice_a"] + votes["choice_b"] == votes["unique_voters"]
    accepted_ge_unique = votes["accepted_vote_events"] >= votes["unique_voters"]
    expected_winner = ("A" if votes["choice_a"] > votes["choice_b"] else
                       "B" if votes["choice_b"] > votes["choice_a"] else None)
    round_operation = operation in ("round_closed", "round_applied")
    winner_match = not round_operation or (
        votes.get("winner") == expected_winner and votes["round_open"] is False)
    no_external_effect = (effect is None or (
        type(effect) is dict and effect.get("effect_type") == "overlay_only"))
    effect_info = None
    if effect is not None:
        pack_id = effect.get("id")
        if type(pack_id) is not str or pack_id not in (policy.option_a, policy.option_b):
            raise ValueError("effect_outside_streamer_policy")
        if type(effect.get("preview_only")) is not bool:
            raise ValueError("invalid_effect_marker")
        effect_info = {"mechanic_id": pack_id, "preview_only": effect["preview_only"],
                       "scope": "local_overlay_only"}
    if blocked_reason is not None and (type(blocked_reason) is not str or
                                       len(blocked_reason) > 90):
        raise ValueError("invalid_blocked_reason")
    effect_authorized = (
        effect_info is None or (
            policy.enabled and effect_info["mechanic_id"] in (policy.option_a, policy.option_b)
            and ((operation == "operator_preview") == effect_info["preview_only"])))
    effect_matches_winner = (not round_operation or effect_info is None or
                             (expected_winner is not None and effect_info["mechanic_id"] ==
                              (policy.option_a if expected_winner == "A" else policy.option_b)))
    stopped_without_effect = operation != "emergency_stop" or effect_info is None
    studio_operation = operation in ("studio_apply", "studio_reset") or (
        operation == "round_applied" and studio_transition is not None)
    bridge_operation = operation in ("bridge_apply", "bridge_reset") or (
        operation == "round_applied" and activity_transition is not None)
    if studio_operation:
        scene_valid = (verify_transition(studio_transition) and effect is None and
                       blocked_reason is None and not votes["round_open"] and
                       (operation == "studio_reset" and studio_transition["pack_id"] == "studio.reset" or
                        operation in ("studio_apply", "round_applied") and policy.enabled and policy.activity == "art" and
                        studio_transition["pack_id"] in (policy.option_a, policy.option_b)))
    else:
        scene_valid = studio_transition is None
    if bridge_operation:
        bridge_valid = (verify_activity_transition(activity_transition) and
                        studio_transition is None and effect is None and
                        blocked_reason is None and not votes["round_open"] and
                        activity_transition["before"]["activity"] == policy.activity and
                        ((operation == "bridge_reset" and activity_transition["pack_id"] == "bridge.reset") or
                         (operation in ("bridge_apply", "round_applied") and policy.enabled and
                          activity_transition["pack_id"] in (policy.option_a, policy.option_b))))
    else:
        bridge_valid = activity_transition is None
    checks = {
        "aggregated_votes_match": counts_match,
        "accepted_events_bound": accepted_ge_unique,
        "round_winner_recomputed": winner_match,
        "operator_policy_valid": True,
        "effect_within_policy": effect_authorized,
        "effect_matches_winner": effect_matches_winner,
        "stop_cannot_apply_effect": stopped_without_effect,
        "no_external_actions": no_external_effect,
    }
    if studio_operation:
        checks["studio_transition_replayed"] = scene_valid
    elif not scene_valid:
        raise ValueError("unexpected_studio_transition")
    if bridge_operation:
        checks["activity_transition_replayed"] = bridge_valid
    elif not bridge_valid:
        raise ValueError("unexpected_activity_transition")
    if operation == "round_applied":
        selected = policy.option_a if expected_winner == "A" else policy.option_b
        transition = studio_transition or activity_transition
        checks["winner_application_authorized"] = (
            expected_winner is not None and votes.get("resolution") == "pending_approval" and
            policy.enabled and blocked_reason is None and
            sum(x is not None for x in (effect, studio_transition, activity_transition)) == 1 and
            (transition is None or transition["pack_id"] == selected))
    simulated = votes.get("simulated", False)
    if type(simulated) is not bool or (simulated and live_connected):
        raise ValueError("invalid_vote_provenance")
    if not all(checks.values()):
        raise ValueError("audit_consistency_failed")
    event = {
        "schema": "aegisforge.live_forge.audit_event.v1",
        "operation": operation,
        "activity": policy.activity,
        "policy": policy.public(),
        "round": votes["round"],
        "counts": {k: votes[k] for k in ("choice_a", "choice_b", "unique_voters", "accepted_vote_events")},
        "winner": expected_winner if round_operation else None,
        "effect": effect_info,
        "blocked_reason": blocked_reason,
        "event_source": ("explicit_local_operator_action" if operation in ("studio_apply", "studio_reset", "bridge_apply", "bridge_reset") else
                         "twitch_eventsub_unverified" if live_connected else "local_no_twitch"),
        "twitch_connection_observed": live_connected,
        "operator_action_accepted": True,
        "audience_authenticity_verified": False,
        "no_external_stream_actions": True,
        "contains_personal_identifiers": False,
    }
    if studio_operation:
        event["schema"] = "aegisforge.live_forge.audit_event.v2"
        event["studio_transition"] = studio_transition
    if bridge_operation:
        event["schema"] = "aegisforge.live_forge.audit_event.v3"
        event["activity_transition"] = activity_transition
    # Preserve v1/v2/v3 serialization and replay. New vote applications and
    # isolated fixtures carry explicit provenance in a new event schema.
    if operation == "round_applied" or simulated:
        event["schema"] = "aegisforge.live_forge.audit_event.v4"
        event["simulated"] = simulated
        event["authorization"] = "explicit_local_operator"
        event["execution_claim"] = "authorized_transition_prepared_before_commit"
        if simulated:
            event["event_source"] = "isolated_synthetic_eventsub_fixture"
    return event, checks


class LiveForgeEvidenceBridge:
    """Separate product checkpoint; capture exactly one closed mission per generation."""

    def __init__(self, checkpoint_dir: str | None = None) -> None:
        from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
        self._lock = RLock()
        self._store = None
        self._head = None
        self._latest: dict[str, Any] | None = None
        self.storage_issue: str | None = None
        if checkpoint_dir:
            from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
            folder = Path(checkpoint_dir)
            if not folder.is_dir() or folder.is_symlink():
                raise ValueError("LIVE_FORGE_CHECKPOINT_DIR must be an existing directory, not a symlink")
            # Do not share AegisGrid's checkpoint: both writers hold independent heads.
            if folder.name.casefold() == "colossusmissions":
                raise ValueError("Use a separate LiveForgeMissions directory")
            self._store = NexusMissionPersistence(folder)
            if (folder / "mission_registry.json").exists():
                loaded = self._store.load()  # fail closed on invalid checkpoint
                if any(m['product'] != 'live_forge' for m in loaded.registry.snapshot()['missions']):
                    raise ValueError('checkpoint_belongs_to_another_product')
                self._registry = loaded.registry
                self._head = loaded.receipt.envelope_sha256
                if not (folder / _ARCHIVE).exists():
                    self.storage_issue = 'incomplete_archive_requires_operator_inspection'
            else:
                self._registry = NexusMissionRegistry()
        else:
            self._registry = NexusMissionRegistry()

    def last_summary(self) -> dict[str, Any] | None:
        with self._lock:
            if self._latest is None:
                return None
            return {k: v for k, v in self._latest.items() if k != "export"}

    def latest_export(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._latest is None else json.loads(json.dumps(self._latest["export"]))

    def _capture(self, mission_id: str, views: Any) -> dict[str, Any]:
        from aegisforge.nexus.mission_control.mission_checkpoint_monitor import NexusMissionCheckpointMonitor
        from aegisforge.nexus.mission_control.mission_source_capture import NexusMissionSourceCapture
        from aegisforge.nexus.mission_control.mission_source_archive import NexusMissionSourceArchive
        if self._store is None:
            return {"archived": False, "generation": None, "archive_sha256": None}
        loaded = self._store.load()
        if loaded.receipt.envelope_sha256 != self._head:
            raise ValueError("checkpoint_head_changed")
        monitor = NexusMissionCheckpointMonitor(self._store, loaded)
        pinned = monitor.status()

        def permit(actor: str, action: str, ids: tuple[str, ...], ctx: dict) -> bool:
            return (actor == _ACTOR and action == "mission_sources.capture_closed_missions" and
                    ids == (mission_id,) and
                    ctx.get("generation") == pinned["generation"] and
                    ctx.get("envelope_sha256") == pinned["envelope_sha256"] and
                    ctx.get("registry_sha256") == pinned["registry_sha256"] and
                    ctx.get("source_counts") == {"graph": 1, "laboratory": 1, "trust": 0})

        service = NexusMissionSourceCapture(self._store, views, authorizer=permit)
        prepared = service.prepare((mission_id,), actor_id=_ACTOR)
        report = service.commit(prepared, actor_id=_ACTOR)
        archive = NexusMissionSourceArchive(self._store._root / _ARCHIVE, monitor)
        if not NexusMissionSourceCapture.verify_capture_report(report, monitor, archive):
            raise ValueError("source_capture_verification_failed")
        return {"archived": True, "generation": report["checkpoint_generation"],
                "archive_sha256": report["archive_sha256"]}

    def _retire_previous(self, next_id: str, next_status: str) -> None:
        if self._store is None or not (self._store._root / _ARCHIVE).exists():
            return
        from aegisforge.nexus.mission_control.mission_archive_history import NexusMissionArchiveHistory

        def permit(actor: str, action: str, ctx: dict) -> bool:
            return (actor == _ACTOR and action == "mission_history.retire_current" and
                    ctx.get("next_mission_id") == next_id and
                    ctx.get("next_status") == next_status and
                    ctx.get("retire_active") is True and
                    ctx.get("checkpoint_envelope_sha256") == self._head and
                    type(ctx.get("mission_ids")) is list and bool(ctx["mission_ids"]))

        NexusMissionArchiveHistory(self._store, authorizer=permit).retire_current(
            actor_id=_ACTOR, next_mission_id=next_id, next_status=next_status)

    def _persist(self, registry, mission_id, final_status, views):
        try:
            self._retire_previous(mission_id, final_status)
            if self._store is not None:
                receipt = self._store.save_snapshot(
                    registry.snapshot(), expected_head_sha256=self._head)
                self._head = receipt.envelope_sha256
            return self._capture(mission_id, views)
        except Exception:
            # Nexus checkpoint and archive writes are separate. Never pretend a
            # partial archive proves an executed effect or silently retry it.
            if self._store is not None:
                self.storage_issue = 'incomplete_archive_requires_operator_inspection'
            raise

    def record(self, *, operation: str, votes: dict[str, Any], policy: dict[str, Any],
               effect: dict[str, Any] | None, blocked_reason: str | None,
               live_connected: bool,
               studio_transition: dict[str, Any] | None = None,
               activity_transition: dict[str, Any] | None = None) -> dict[str, Any]:
        from aegisforge.nexus.lab.scenario_catalog import NexusScenarioCatalog, ScenarioCase, ScenarioExpectation
        from aegisforge.nexus.lab.evaluation_runner import NexusEvaluationRunner, EvaluationObservation
        from aegisforge.nexus.lab.scorecard import NexusScorecard
        from aegisforge.nexus.proof.proof_graph import NexusProofGraph
        from aegisforge.nexus.mission_control.mission_monitor_views import MonitorLabSource, NexusMissionMonitorViews

        entry, checks = _bounded_event(operation, votes, policy, effect, blocked_reason,
                                       live_connected, studio_transition, activity_transition)
        entry_digest = _digest(entry)
        catalog = NexusScenarioCatalog()
        case = ScenarioCase(_CASE, product="live_forge", category="functional", mode="offline",
                            title="Live Forge operator round evidence",
                            description="Local replay and approved virtual studio effects; not Twitch authenticity",
                            inputs={"event_sha256": entry_digest, "operation": operation},
                            tags=("liveforge", "local"),
                            expectations=(ScenarioExpectation("checks.passed", "equals", True),
                                          ScenarioExpectation("scope.no_external_actions", "equals", True)))
        catalog.register(case)
        evaluator = NexusEvaluationRunner(catalog, trusted_sources=("liveforge_local_accounting",))
        observation = EvaluationObservation(
            case.scenario_id, case.case_sha256,
            source_id="liveforge_local_accounting", evidence_ref="local/" + entry_digest[:24],
            payload={"checks": {"passed": all(checks.values())},
                     "scope": {"no_external_actions": True}},
        )
        evaluation = evaluator.evaluate(evaluator.prepare(), [observation])
        if not NexusEvaluationRunner.verify_report(evaluation):
            raise ValueError("laboratory_report_inconsistent")
        scorer = NexusScorecard()
        score = scorer.build("live_forge.local_activity", [evaluation])
        if not scorer.verify(score, "live_forge.local_activity", [evaluation]):
            raise ValueError("scorecard_inconsistent")

        with self._lock:
            if self.storage_issue:
                raise ValueError(self.storage_issue)
            if self._store is not None and self._head is not None:
                if self._store.load().receipt.envelope_sha256 != self._head:
                    raise ValueError('checkpoint_head_changed')
            mission_id = "forge." + uuid4().hex
            graph = NexusProofGraph(mission_id)
            m = graph.add_node("mission", mission_id, status="observed")
            run = graph.add_node("run", mission_id + ".run", status="reported_complete",
                                 stage="execution", source="internal", digest_sha256=entry_digest)
            evidence_node = graph.add_node("evidence", mission_id + ".lab", status="observed",
                                           stage="verification", source="internal",
                                           digest_sha256=score["scorecard_sha256"])
            claim = graph.add_node("claim", mission_id + ".policy", status="proposed",
                                   stage="verification", source="internal", digest_sha256=entry_digest)
            check = graph.add_node("check", mission_id + ".checks", status="passed",
                                   stage="verification", source="internal",
                                   digest_sha256=evaluation["report_sha256"],
                                   metrics={"independent_checks_run": len(checks),
                                            "independent_checks_passed": sum(checks.values())})
            graph.add_edge("contains", m, run)
            graph.add_edge("records", run, evidence_node)
            graph.add_edge("documents", evidence_node, check)
            graph.add_edge("evaluates", check, claim)
            snap = graph.snapshot()
            if not NexusProofGraph.verify_snapshot(snap):
                raise ValueError("proof_graph_inconsistent")
            views = NexusMissionMonitorViews(
                graphs={mission_id: graph}, labs={mission_id: MonitorLabSource(scorer, score, (evaluation,))})
            final_status = "cancelled" if operation == "emergency_stop" else "reported_complete"
            registry = type(self._registry).from_snapshot(self._registry.snapshot())
            registry.register(mission_id, product="live_forge", owner_id=_ACTOR,
                              actor_id=_ACTOR, objective_sha256=entry_digest)
            for component in ("live_forge", "lab", "proof", "mission_control"):
                registry.assign_component(mission_id, component=component, actor_id=_ACTOR)
            for kind, digest in (
                ("artifact", entry_digest),
                ("evaluation", evaluation["report_sha256"]),
                ("scorecard", score["scorecard_sha256"]),
                ("provenance", snap["graph_sha256"]),
            ):
                registry.link_reference(mission_id, kind=kind, reference_sha256=digest, actor_id=_ACTOR)
            registry.transition(mission_id, "ready", actor_id=_ACTOR)
            registry.transition(mission_id, "running", actor_id=_ACTOR)
            registry.transition(mission_id, final_status, actor_id=_ACTOR)
            archive_meta = self._persist(registry, mission_id, final_status, views)
            self._registry = registry
            export = {"schema": "aegisforge.live_forge.evidence.v1", "record": entry,
                      "checks": checks, "mission": registry.get(mission_id),
                      "laboratory": {"status": evaluation["status"],
                                     "report_sha256": evaluation["report_sha256"],
                                     "scorecard_sha256": score["scorecard_sha256"]},
                      "proof_graph": {"graph_sha256": snap["graph_sha256"],
                                      "node_count": snap["node_count"], "edge_count": snap["edge_count"]},
                      "storage": archive_meta,
                      "record_sha256": entry_digest,
                      "replay": {"evaluation": evaluation, "scorecard": score, "graph": snap},
                      "assurance": "local_operator_event_replay_only_not_external_authenticity"}
            self._latest = {"mission_id": mission_id, "status": final_status,
                            "operation": operation, "checks": len(checks),
                            "archive": archive_meta, "export": export}
            return {k: v for k, v in self._latest.items() if k != "export"}
