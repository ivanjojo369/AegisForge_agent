"""Service security, CAS, state, integration and privacy regression tests."""
from hashlib import sha256
import json

import pytest

from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_state import (
    MissionTransitionRule, MissionStatePolicy, NexusMissionState,
)
from aegisforge.nexus.mission_control.mission_coordinator import (
    MissionWorkflowStep, MissionCoordinationBlueprint, NexusMissionCoordinator,
)
from aegisforge.nexus.mission_control.mission_read_adapters import NexusMissionReadAdapters
from aegisforge.nexus.mission_control.mission_runtime import verify_runtime_report
from aegisforge.nexus.mission_control.mission_service import (
    NexusMissionService, NexusMissionServiceError,
)


class Auth:
    def __init__(self, denied=(), side_effect=None):
        self.denied = set(denied)
        self.calls = []
        self.side_effect = side_effect
    def __call__(self, actor, action, mission, anchor, request):
        self.calls.append((actor, action, mission, anchor, request))
        if self.side_effect:
            self.side_effect(actor, action, mission, anchor, request)
        return (actor, action, mission) not in self.denied and action not in self.denied


def build_service(auth=None, *, mission="mission.service", create=True):
    registry = NexusMissionRegistry()
    checker = auth if auth is not None else Auth()
    service = NexusMissionService(registry, authorizer=checker)
    if create:
        service.create_mission(mission, product="nexus", owner_id="owner.service",
                               objective_sha256="a"*64, actor_id="actor.service",
                               expected_registry_sha256=service.registry_version(actor_id="actor.service"))
    return registry, service, checker


def policy(mission="mission.service", targets=("ready",), *, checker=None):
    rules = tuple(MissionTransitionRule(target) for target in sorted(targets))
    return NexusMissionState(MissionStatePolicy(mission, "nexus", rules), host_checker=checker)


def bind_one_step(registry, service, *, mission="mission.service"):
    state = policy(mission)
    bp = MissionCoordinationBlueprint(mission, "nexus", "ready", (
        MissionWorkflowStep("step.registry", "mission_control", "summarize_mission"),
    ))
    coordinator = NexusMissionCoordinator(bp, state)
    return service.configure_workflow(mission, actor_id="actor.service",
                                      coordinator=coordinator,
                                      adapters=NexusMissionReadAdapters(registry),
                                      expected_registry_sha256=service.registry_version(actor_id="actor.service"))


def test_authorizer_is_mandatory():
    with pytest.raises(NexusMissionServiceError):
        NexusMissionService(NexusMissionRegistry(), authorizer=None)


def test_create_and_get_mission_no_raw_objective():
    reg, svc, auth = build_service()
    row = svc.get_mission("mission.service", actor_id="actor.service")
    assert row["status"] == "draft"
    assert row["objective_sha256"] == "a"*64
    assert "objective" not in str(reg.snapshot()).replace("objective_sha256", "")
    assert "create" in [v[1] for v in auth.calls]


def test_version_is_snapshot_hash():
    reg, svc, _ = build_service()
    assert svc.registry_version(actor_id="actor.service") == reg.snapshot()["registry_sha256"]


def test_no_unauthed_create():
    r = NexusMissionRegistry()
    a = Auth(denied={"create"})
    s = NexusMissionService(r, authorizer=a)
    before = r.snapshot()
    with pytest.raises(NexusMissionServiceError, match="denied"):
        s.create_mission("mission.denied",product="nexus",owner_id="owner.service",
                         objective_sha256="a"*64,actor_id="actor.service",
                         expected_registry_sha256=r.snapshot()["registry_sha256"])
    assert r.snapshot() == before


@pytest.mark.parametrize("action", ["read","history","assign_component","link_reference","configure","preview","inspect","transition"])
def test_denied_authorizer_fails_closed(action):
    auth = Auth(denied={action})
    reg, svc, _ = build_service(auth)
    before = reg.snapshot()
    with pytest.raises(NexusMissionServiceError):
        if action == "read": svc.get_mission("mission.service",actor_id="actor.service")
        elif action == "history": svc.mission_history("mission.service",actor_id="actor.service")
        elif action == "assign_component": svc.assign_component("mission.service",component="mission_control",actor_id="actor.service",expected_registry_sha256=before["registry_sha256"])
        elif action == "link_reference": svc.link_reference("mission.service",kind="evidence",reference_sha256="b"*64,actor_id="actor.service",expected_registry_sha256=before["registry_sha256"])
        elif action == "configure": bind_one_step(reg, svc)
        elif action == "preview": svc.preview_workflow("mission.service",actor_id="actor.service")
        elif action == "inspect": svc.inspect_workflow("mission.service",actor_id="actor.service")
        else: svc.transition_mission("mission.service","ready",actor_id="actor.service",expected_registry_sha256=before["registry_sha256"],state=policy())
    assert reg.snapshot() == before


def test_stale_create_denied_without_write():
    reg, svc, _ = build_service()
    with pytest.raises(NexusMissionServiceError, match="stale"):
        svc.create_mission("mission.next",product="nexus",owner_id="owner.service",
                           objective_sha256="a"*64,actor_id="actor.service",
                           expected_registry_sha256="0"*64)
    assert reg.snapshot()["mission_count"] == 1


@pytest.mark.parametrize("method", ["assign", "link", "transition", "configure"])
def test_stale_mutation_fails(method):
    reg, svc, _ = build_service()
    with pytest.raises(NexusMissionServiceError, match="stale"):
        if method == "assign":
            svc.assign_component("mission.service",component="mission_control",actor_id="actor.service",expected_registry_sha256="1"*64)
        elif method == "link":
            svc.link_reference("mission.service",kind="evidence",reference_sha256="a"*64,actor_id="actor.service",expected_registry_sha256="1"*64)
        elif method == "transition":
            svc.transition_mission("mission.service","ready",actor_id="actor.service",state=policy(),expected_registry_sha256="1"*64)
        else:
            state=policy()
            c=NexusMissionCoordinator(MissionCoordinationBlueprint("mission.service","nexus","ready",(
                MissionWorkflowStep("step.registry","mission_control","summarize_mission"),)),state)
            svc.configure_workflow("mission.service",actor_id="actor.service",coordinator=c,adapters=NexusMissionReadAdapters(reg),expected_registry_sha256="1"*64)


def test_assign_and_link_update_registry():
    reg, svc, _ = build_service()
    svc.assign_component("mission.service",component="mission_control",actor_id="actor.service",expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    svc.link_reference("mission.service",kind="evidence",reference_sha256="b"*64,actor_id="actor.service",expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    assert reg.get("mission.service")["components"] == ["mission_control"]
    assert reg.get("mission.service")["reference_count"] == 1
    assert len(svc.mission_history("mission.service",actor_id="actor.service")) == 3


def test_workflow_can_be_configured_and_inspected():
    reg, svc, _ = build_service()
    svc.assign_component("mission.service",component="mission_control",actor_id="actor.service",expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    sha=bind_one_step(reg,svc)
    assert len(sha) == 64
    preview=svc.preview_workflow("mission.service",actor_id="actor.service")
    assert preview.outcome == "host_review_required"
    report=svc.inspect_workflow("mission.service",actor_id="actor.service")
    assert report.outcome == "completed_read_only"
    assert verify_runtime_report(report.to_dict())
    assert reg.get("mission.service")["status"] == "draft"


def test_workflow_without_component_is_blocked():
    reg, svc, _ = build_service()
    bind_one_step(reg,svc)
    assert svc.preview_workflow("mission.service",actor_id="actor.service").outcome == "blocked"


def test_readonly_inspection_never_changes_registry():
    reg, svc, _ = build_service()
    svc.assign_component("mission.service",component="mission_control",actor_id="actor.service",expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    bind_one_step(reg, svc)
    old = reg.snapshot()
    svc.inspect_workflow("mission.service",actor_id="actor.service")
    assert reg.snapshot() == old


def test_bad_adapter_registry_rejected():
    reg, svc, _ = build_service()
    other = NexusMissionRegistry()
    bp=MissionCoordinationBlueprint("mission.service","nexus","ready",(
        MissionWorkflowStep("step.registry","mission_control","summarize_mission"),))
    c=NexusMissionCoordinator(bp,policy())
    with pytest.raises(NexusMissionServiceError):
        svc.configure_workflow("mission.service",actor_id="actor.service",coordinator=c,
                               adapters=NexusMissionReadAdapters(other),
                               expected_registry_sha256=svc.registry_version(actor_id="actor.service"))


def test_unbound_workflow_rejected():
    _, svc, _ = build_service()
    with pytest.raises(NexusMissionServiceError,match="not configured"):
        svc.inspect_workflow("mission.service",actor_id="actor.service")


def test_state_policy_is_required_for_transition():
    _, svc, _ = build_service()
    with pytest.raises(NexusMissionServiceError,match="host state policy required"):
        svc.transition_mission("mission.service","ready",actor_id="actor.service",expected_registry_sha256=svc.registry_version(actor_id="actor.service"))


def test_ready_transition_works_when_host_authorizes():
    reg, svc, _ = build_service()
    result=svc.transition_mission("mission.service","ready",actor_id="actor.service",
                                  expected_registry_sha256=svc.registry_version(actor_id="actor.service"),
                                  state=policy())
    assert result["status"] == "ready"
    assert reg.get("mission.service")["event_count"] == 2


def test_running_requires_host_readiness_checker():
    reg, svc, _ = build_service()
    svc.transition_mission("mission.service","ready",actor_id="actor.service",expected_registry_sha256=svc.registry_version(actor_id="actor.service"),state=policy())
    before=reg.snapshot()
    with pytest.raises(NexusMissionServiceError,match="not ready"):
        svc.transition_mission("mission.service","running",actor_id="actor.service",
                               expected_registry_sha256=svc.registry_version(actor_id="actor.service"),
                               state=policy(targets=("running",)))
    assert reg.snapshot()==before


def test_host_checker_denial_blocks_running():
    reg, svc, _ = build_service()
    svc.transition_mission("mission.service","ready",actor_id="actor.service",expected_registry_sha256=svc.registry_version(actor_id="actor.service"),state=policy())
    with pytest.raises(NexusMissionServiceError,match="not ready"):
        svc.transition_mission("mission.service","running",actor_id="actor.service",
                               expected_registry_sha256=svc.registry_version(actor_id="actor.service"),
                               state=policy(targets=("running",),checker=lambda *a: False))
    assert reg.get("mission.service")["status"]=="ready"


def test_running_allowed_by_host_checker_records_status_without_execution():
    reg, svc, _ = build_service()
    svc.transition_mission("mission.service","ready",actor_id="actor.service",expected_registry_sha256=svc.registry_version(actor_id="actor.service"),state=policy())
    svc.transition_mission("mission.service","running",actor_id="actor.service",
                           expected_registry_sha256=svc.registry_version(actor_id="actor.service"),
                           state=policy(targets=("running",),checker=lambda *a: True))
    assert reg.get("mission.service")["status"]=="running"
    assert reg.snapshot()["effects_executed"] is False


def test_failed_transition_does_not_modify_registry():
    reg, svc, _ = build_service()
    old=reg.snapshot()
    with pytest.raises(NexusMissionServiceError,match="not ready"):
        svc.transition_mission("mission.service","verified",actor_id="actor.service",
                               decision_sha256="b"*64,
                               expected_registry_sha256=svc.registry_version(actor_id="actor.service"),
                               state=policy(targets=("verified",),checker=lambda *a: True))
    assert reg.snapshot()==old


def test_verified_requires_decision_digest():
    reg, svc, _ = build_service()
    with pytest.raises(NexusMissionServiceError,match="hash"):
        svc.transition_mission("mission.service","verified",actor_id="actor.service",
                               expected_registry_sha256=svc.registry_version(actor_id="actor.service"),
                               state=policy(targets=("verified",)))


def test_authentication_callback_exceptions_are_fail_closed():
    reg=NexusMissionRegistry()
    def bad(*_): raise RuntimeError('private/path/secret')
    svc=NexusMissionService(reg,authorizer=bad)
    with pytest.raises(NexusMissionServiceError,match="unavailable"):
        svc.registry_version(actor_id="actor.service")


def test_non_bool_one_is_not_authorization():
    svc=NexusMissionService(NexusMissionRegistry(), authorizer=lambda *_: 1)
    with pytest.raises(NexusMissionServiceError,match="denied"):
        svc.registry_version(actor_id="actor.service")


def test_host_callback_changing_registry_is_rejected():
    reg=NexusMissionRegistry()
    def mutate(*_):
        if not reg.snapshot()["mission_count"]:
            reg.register("mission.injected",product="nexus",owner_id="owner.service",objective_sha256="a"*64,actor_id="actor.service")
        return True
    svc=NexusMissionService(reg,authorizer=mutate)
    with pytest.raises(NexusMissionServiceError,match="registry changed"):
        svc.registry_version(actor_id="actor.service")


def test_list_hides_missions_without_individual_authorization():
    auth=Auth(denied={('actor.service','read','mission.hidden')})
    reg,svc,_=build_service(auth)
    svc.create_mission("mission.hidden",product="nexus",owner_id="owner.service",objective_sha256="a"*64,
                       actor_id="actor.service",expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    rows=svc.list_missions(actor_id="actor.service")
    assert [r["mission_id"] for r in rows]==["mission.service"]


def test_export_requires_admin_authorization():
    auth=Auth(denied={"export_snapshot"})
    reg,svc,_=build_service(auth)
    with pytest.raises(NexusMissionServiceError):
        svc.export_snapshot(actor_id="actor.service")


def test_status_reports_no_permissions():
    reg,svc,_=build_service()
    status=svc.status(actor_id="actor.service")
    d=status.to_dict()
    assert d["execution_permission_granted"] is False
    assert d["read_only_runtime"] is True
    assert d["network_server_started"] is False
    assert d["mission_count"]==1
    assert status.to_dict()==d


def test_status_is_immutable_to_caller():
    _,svc,_=build_service()
    status=svc.status(actor_id="actor.service")
    x=status.to_dict(); x["mission_count"]=444
    assert status.to_dict()["mission_count"]==1


@pytest.mark.parametrize("actor", [None,"x","Actor.Test","actor/secret",True,0])
def test_invalid_actor_identifiers_denied(actor):
    _,svc,_=build_service()
    with pytest.raises(NexusMissionServiceError):
        svc.registry_version(actor_id=actor)


def test_configure_rejects_mismatched_mission():
    reg,svc,_=build_service()
    bp=MissionCoordinationBlueprint("mission.other","nexus","ready",(
        MissionWorkflowStep("step.registry","mission_control","summarize_mission"),))
    c=NexusMissionCoordinator(bp,policy("mission.other"))
    with pytest.raises(NexusMissionServiceError,match="mismatch"):
        svc.configure_workflow("mission.service",actor_id="actor.service",
                               coordinator=c,adapters=NexusMissionReadAdapters(reg),
                               expected_registry_sha256=svc.registry_version(actor_id="actor.service"))


def test_authorizer_receives_exact_component_scope():
    reg, svc, auth = build_service()
    svc.assign_component("mission.service", component="proof", actor_id="actor.service",
                         expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    row = next(v for v in auth.calls if v[1] == "assign_component")
    assert row[4] == {"component": "proof"}


def test_authorizer_receives_exact_reference_scope():
    reg, svc, auth = build_service()
    svc.link_reference("mission.service", kind="evidence", reference_sha256="b"*64,
                       actor_id="actor.service",
                       expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    row = next(v for v in auth.calls if v[1] == "link_reference")
    assert row[4] == {"kind": "evidence", "reference_sha256": "b"*64}


def test_authorizer_can_reject_specific_target_status_without_changing_registry():
    class LimitedAuth(Auth):
        def __call__(self, actor, action, mission, anchor, request):
            if action == "transition" and request["target_status"] == "running":
                return False
            return super().__call__(actor, action, mission, anchor, request)
    auth = LimitedAuth()
    reg, svc, _ = build_service(auth)
    svc.transition_mission("mission.service", "ready", actor_id="actor.service",
                           expected_registry_sha256=svc.registry_version(actor_id="actor.service"),
                           state=policy())
    before = reg.snapshot()
    with pytest.raises(NexusMissionServiceError, match="denied"):
        svc.transition_mission("mission.service", "running", actor_id="actor.service",
                               expected_registry_sha256=svc.registry_version(actor_id="actor.service"),
                               state=policy(targets=("running",), checker=lambda *_: True))
    assert reg.snapshot() == before


def test_host_can_reject_specific_component_without_reading_internal_files():
    class LimitedAuth(Auth):
        def __call__(self, actor, action, mission, anchor, request):
            if action == "assign_component" and request.get("component") == "engineering":
                return False
            return super().__call__(actor, action, mission, anchor, request)
    reg, svc, _ = build_service(LimitedAuth())
    before = reg.snapshot()
    with pytest.raises(NexusMissionServiceError, match="denied"):
        svc.assign_component("mission.service", component="engineering",
                             actor_id="actor.service",
                             expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    assert reg.snapshot() == before


def test_service_accepts_latest_lab_adapter_for_readonly_mission_step():
    from aegisforge.nexus.mission_control.mission_lab_adapters import NexusMissionLabReadAdapters
    reg, svc, _ = build_service()
    svc.assign_component("mission.service", component="mission_control", actor_id="actor.service",
                         expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    state = policy()
    bp = MissionCoordinationBlueprint("mission.service", "nexus", "ready", (
        MissionWorkflowStep("step.registry", "mission_control", "summarize_mission"),
    ))
    svc.configure_workflow("mission.service", actor_id="actor.service",
                           coordinator=NexusMissionCoordinator(bp, state),
                           adapters=NexusMissionLabReadAdapters(reg),
                           expected_registry_sha256=svc.registry_version(actor_id="actor.service"))
    report = svc.inspect_workflow("mission.service", actor_id="actor.service")
    assert report.outcome == "completed_read_only"
    assert verify_runtime_report(report.to_dict())
    assert report.to_dict()["authorization_granted"] is False
