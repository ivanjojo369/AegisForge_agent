"""End-to-end host opt-in and append-only history; offline and Windows portable."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import os
import json
from pathlib import Path

import pytest

from aegisforge.nexus.mission_control.mission_archive_history import (
    NexusMissionArchiveHistory, NexusMissionArchiveHistoryError,
)
from aegisforge.nexus.mission_control.mission_closure_host import (
    NexusMissionClosureHost, NexusMissionClosureHostError,
)
from aegisforge.nexus.mission_control.mission_closure_hook import NexusMissionClosureHook
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_state import (
    NexusMissionState, MissionStatePolicy, MissionTransitionRule,
)
from aegisforge.nexus.mission_control.mission_persistence import NexusMissionPersistence
from aegisforge.nexus.mission_control.mission_monitor_views import NexusMissionMonitorViews
from aegisforge.nexus.proof.proof_graph import NexusProofGraph

ACTOR = "actor.synthetic"
M1, M2 = "mission.alpha", "mission.beta"


def dh(text):
    return sha256(text.encode()).hexdigest()


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    reg = NexusMissionRegistry()
    states = {}
    graphs = {}
    for mid in (M1, M2):
        reg.register(mid, product="nexus", owner_id="owner.synthetic",
                     objective_sha256=dh("objective" + mid), actor_id=ACTOR)
        reg.transition(mid, "ready", actor_id=ACTOR)
        reg.transition(mid, "running", actor_id=ACTOR)
        reg.link_reference(mid, kind="evidence", reference_sha256=dh(mid + "evidence"),
                           actor_id=ACTOR)
        states[mid] = NexusMissionState(MissionStatePolicy(
            mid, "nexus", (MissionTransitionRule("reported_complete",
                required_reference_kinds=("evidence",)),)))
        graph = NexusProofGraph(mid)
        node = graph.add_node("mission", mid, status="observed")
        run = graph.add_node("run", "run." + mid, status="completed", stage="execution")
        graph.add_edge("contains", node, run)
        graphs[mid] = graph
    auth_calls = []
    capture_calls = []
    history_calls = []

    def auth(actor, action, mission, anchor, req):
        auth_calls.append((actor, action, mission, deepcopy(req)))
        return actor == ACTOR

    def capture_auth(actor, action, ids, meta):
        capture_calls.append((actor, action, ids, deepcopy(meta)))
        return actor == ACTOR and action == "mission_sources.capture_closed_missions"

    def history_auth(actor, action, ctx):
        history_calls.append((actor, action, deepcopy(ctx)))
        return actor == ACTOR

    service = NexusMissionService(reg, authorizer=auth)
    store = NexusMissionPersistence(root)
    store.save(reg)
    views = NexusMissionMonitorViews(graphs=graphs)
    hook = NexusMissionClosureHook(service, store, views,
                                   capture_authorizer=capture_auth)
    history = NexusMissionArchiveHistory(store, authorizer=history_auth)
    host = NexusMissionClosureHost(hook, history)
    return locals()


def close(e, mid=M1, *, rotate=False):
    return e["host"].close(
        mid, "reported_complete", actor_id=ACTOR, state=e["states"][mid],
        expected_registry_sha256=e["reg"].snapshot()["registry_sha256"],
        rotate_prior_archive=rotate)


def test_first_closure_completed_and_history_present(env):
    r = close(env)
    assert r["outcome"] == "completed"
    assert r["current_archive_preserved"] is True
    assert r["prior_archive_retired"] is False
    assert r["effects_executed"] is False
    assert env["reg"].get(M1)["status"] == "reported_complete"
    assert env["store"].load().receipt.generation == 2
    rows = env["history"].list_history()
    assert len(rows) == 1 and rows[0]["generation"] == 2
    assert env["history"].read_summary(2, M1, "graph")["node_count"] == 2
    assert (env["root"] / "mission_sources.json").exists()


def test_two_closures_retained_with_multiple_generations(env):
    assert close(env)["outcome"] == "completed"
    second = close(env, M2, rotate=True)
    assert second["outcome"] == "completed"
    assert second["prior_archive_retired"] is True
    assert second["current_archive_preserved"] is True
    assert env["store"].load().receipt.generation == 3
    rows = env["history"].list_history()
    assert [r["generation"] for r in rows] == [2, 3]
    assert env["history"].read_summary(2, M1, "graph")["node_count"] == 2
    assert env["history"].read_summary(3, M2, "graph")["node_count"] == 2
    assert env["history"].read_summary(2, M1, "trust") is None
    assert all(r["authorization_restored"] is False for r in rows)
    assert env["reg"].get(M1)["status"] == "reported_complete"
    assert env["reg"].get(M2)["status"] == "reported_complete"


def test_closed_archive_must_be_explicitly_retired(env):
    close(env)
    with pytest.raises(NexusMissionClosureHostError, match="retirement"):
        close(env, M2)
    assert env["reg"].get(M2)["status"] == "running"
    assert len(env["history"].list_history()) == 1


def test_history_retirement_is_separately_authorized(env):
    close(env)
    before = (env["root"] / "mission_sources.json").read_bytes()
    env["history"]._authorize = lambda a, action, ctx: action != "mission_history.retire_current"
    with pytest.raises(NexusMissionArchiveHistoryError, match="denied"):
        close(env, M2, rotate=True)
    assert (env["root"] / "mission_sources.json").read_bytes() == before
    assert env["reg"].get(M2)["status"] == "running"


def test_history_context_binds_next_target_and_archive(env):
    close(env)
    close(env, M2, rotate=True)
    a, action, ctx = next(x for x in env["history_calls"]
                          if x[1] == "mission_history.retire_current")
    assert a == ACTOR and ctx["next_mission_id"] == M2
    assert ctx["next_status"] == "reported_complete"
    assert ctx["retire_active"] is True
    assert ctx["archive_sha256"] in {r["archive_sha256"] for r in env["history"].list_history()}
    assert len(ctx["archive_file_sha256"]) == 64


def test_stale_expected_registry_blocks_without_rotation(env):
    close(env)
    with pytest.raises(NexusMissionClosureHostError, match="registry changed"):
        env["host"].close(M2, "reported_complete", actor_id=ACTOR,
                          state=env["states"][M2], expected_registry_sha256=dh("stale"),
                          rotate_prior_archive=True)
    assert (env["root"] / "mission_sources.json").exists()


def test_prior_retired_and_subsequent_closure_denied_requires_reconciliation(env):
    close(env)
    env["service"]._authorizer = lambda actor, action, *_: action != "transition"
    outcome = close(env, M2, rotate=True)
    assert outcome["outcome"] == "operator_reconciliation_required"
    assert outcome["prior_archive_retired"] is True
    assert outcome["closure_outcome"] == "transition_failed"
    assert not (env["root"] / "mission_sources.json").exists()
    assert len(env["history"].list_history()) == 1
    assert env["reg"].get(M2)["status"] == "running"


def test_missing_source_rejected_and_prior_history_preserved(env):
    close(env)
    env["hook"]._views = NexusMissionMonitorViews(graphs={M1: env["graphs"][M1]})
    report = close(env, M2, rotate=True)
    assert report["stage"] == "preparation_or_commit_failed"
    assert len(env["history"].list_history()) == 1
    assert env["reg"].get(M2)["status"] == "running"


def test_capture_denied_after_transition_returns_partial(env):
    env["hook"]._capture_authorizer = lambda *_: False
    outcome = close(env)
    assert outcome["outcome"] == "operator_reconciliation_required"
    assert outcome["closure_outcome"] == "archive_failed"
    assert outcome["current_archive_preserved"] is False
    assert env["reg"].get(M1)["status"] == "reported_complete"
    assert env["history"].list_history() == ()


def test_resume_archive_requires_independent_authorization(env):
    old = env["hook"]._capture_authorizer
    env["hook"]._capture_authorizer = lambda *_: False
    assert close(env)["closure_outcome"] == "archive_failed"
    env["hook"]._capture_authorizer = old
    report = env["host"].resume_archive(M1, actor_id=ACTOR)
    assert report["outcome"] == "archive_resumed_and_preserved"
    assert len(env["history"].list_history()) == 1
    with pytest.raises(Exception):
        env["host"].resume_archive(M1, actor_id=ACTOR)


def test_preservation_failure_is_explicit_and_retryable(env):
    env["history"]._authorize = lambda *_: False
    outcome = close(env)
    assert outcome["stage"] == "history_preservation_failed"
    assert outcome["operator_reconciliation_required"] is True
    assert (env["root"] / "mission_sources.json").exists()
    env["history"]._authorize = env["history_auth"]
    receipt = env["host"].preserve_current_after_failure(actor_id=ACTOR)
    assert receipt["preserved_historical"] is True
    assert len(env["history"].list_history()) == 1


def test_preserving_twice_idempotent_and_never_overwrites(env):
    close(env)
    first = env["host"].preserve_current_after_failure(actor_id=ACTOR)
    second = env["host"].preserve_current_after_failure(actor_id=ACTOR)
    assert first["archive_sha256"] == second["archive_sha256"]
    assert len(env["history"].list_history()) == 1


def test_history_copy_detects_modification(env):
    close(env)
    path = next((env["root"] / "mission_source_history").glob("*.json"))
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'"integrity_only":true', b'"integrity_only":false'))
    with pytest.raises(NexusMissionArchiveHistoryError):
        env["history"].list_history()
    assert (env["root"] / "mission_sources.json").exists()


def test_history_rejects_extra_file(env):
    close(env)
    (env["root"] / "mission_source_history" / "unexpected.txt").write_text("junk")
    with pytest.raises(NexusMissionArchiveHistoryError):
        env["history"].list_history()


def test_history_rejects_wrong_name(env):
    close(env)
    path = next((env["root"] / "mission_source_history").glob("*.json"))
    path.rename(path.with_name("g00000000000000000002_" + "a"*64 + ".json"))
    with pytest.raises(NexusMissionArchiveHistoryError):
        env["history"].list_history()


def test_history_rejects_linked_file(env):
    close(env)
    path = next((env["root"] / "mission_source_history").glob("*.json"))
    other = env["root"] / "duplicate.json"
    try:
        os.link(path, other)
    except (OSError, NotImplementedError):
        pytest.skip("hardlinks unavailable")
    with pytest.raises(NexusMissionArchiveHistoryError):
        env["history"].list_history()


def test_retirement_checks_disk_lock(env):
    close(env)
    lock = env["root"] / ".mission_sources.capture.lock"
    lock.write_text("synthetic ongoing capture")
    with pytest.raises(NexusMissionArchiveHistoryError):
        close(env, M2, rotate=True)
    assert (env["root"] / "mission_sources.json").exists()


def test_cannot_restore_historical_approval(env):
    close(env)
    for row in env["history"].list_history():
        assert row["historical_only"] is True
        assert row["authorization_restored"] is False
        assert row["authenticity_verified"] is False


@pytest.mark.parametrize("bad", [None, {}, "", "actor wrong", 0, True])
def test_bad_history_actor_rejected(env, bad):
    close(env)
    with pytest.raises(NexusMissionArchiveHistoryError):
        env["history"].preserve_current(actor_id=bad)


@pytest.mark.parametrize("bad", [None, 0, "", True, "unknown"])
def test_invalid_generation_rejected(env, bad):
    close(env)
    with pytest.raises(NexusMissionArchiveHistoryError):
        env["history"].read_summary(bad, M1, "graph")


@pytest.mark.parametrize("bad", ["not_kind", "", None, 12])
def test_invalid_kind_rejected(env, bad):
    close(env)
    with pytest.raises(NexusMissionArchiveHistoryError):
        env["history"].read_summary(2, M1, bad)


def test_history_reopened_from_new_process_object(env):
    close(env)
    fresh = NexusMissionArchiveHistory(NexusMissionPersistence(env["root"]),
                                        authorizer=env["history_auth"])
    assert fresh.list_history() == env["history"].list_history()
    assert fresh.read_summary(2, M1, "graph")["node_count"] == 2


def test_history_has_no_implicit_create_during_listing(env):
    assert env["history"].list_history() == ()
    assert not (env["root"] / "mission_source_history").exists()


def test_host_rejects_mixed_store(env, tmp_path):
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = NexusMissionArchiveHistory(NexusMissionPersistence(other_dir),
                                       authorizer=env["history_auth"])
    with pytest.raises(NexusMissionClosureHostError):
        NexusMissionClosureHost(env["hook"], other)


def test_host_denies_improper_rotate_flag(env):
    with pytest.raises(NexusMissionClosureHostError):
        env["host"].close(M1, "reported_complete", actor_id=ACTOR,
                          state=env["states"][M1],
                          expected_registry_sha256=env["reg"].snapshot()["registry_sha256"],
                          rotate_prior_archive=1)


def test_history_does_not_execute_effects(env):
    first = close(env)
    assert first["effects_executed"] is False
    second = close(env, M2, rotate=True)
    assert second["effects_executed"] is False
    assert all(x["effects_executed"] is False
               for x in (first, second))


def test_latest_primary_is_current_archive(env):
    close(env)
    initial = json.loads((env["root"] / "mission_sources.json").read_bytes())
    close(env, M2, rotate=True)
    latest = json.loads((env["root"] / "mission_sources.json").read_bytes())
    assert latest["checkpoint_generation"] == 3
    assert initial["checkpoint_generation"] == 2
    assert latest["archive_sha256"] != initial["archive_sha256"]


def test_history_generation_capacity_denial(env):
    close(env)
    limited = NexusMissionArchiveHistory(env["store"],
                 authorizer=env["history_auth"], max_archives=1)
    env["host"] = NexusMissionClosureHost(env["hook"], limited)
    result = close(env, M2, rotate=True)
    assert result["stage"] == "history_preservation_failed"
    assert env["reg"].get(M2)["status"] == "reported_complete"
    assert len(limited.list_history()) == 1


def test_retirement_rejects_wrong_next_mission(env):
    close(env)
    with pytest.raises(NexusMissionArchiveHistoryError):
        env["history"].retire_current(actor_id=ACTOR,
                                      next_mission_id="bad name!", next_status="verified")
    assert (env["root"] / "mission_sources.json").exists()


def test_reports_have_digest_and_no_approval(env):
    report = close(env)
    body = {k: v for k, v in report.items() if k != "report_sha256"}
    assert sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode()).hexdigest() == report["report_sha256"]
    assert report["trust_authorizations_restored"] is False
    assert report["authenticity_verified"] is False
