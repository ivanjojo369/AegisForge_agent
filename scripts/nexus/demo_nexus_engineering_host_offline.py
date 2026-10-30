"""Colossus demonstration using only disposable code snapshots.

Default: offline preview. The explicit Docker mode supports the fixed calculator
or one approved, hash-bound scorecard diff, including a model-proposed diff
already reviewed by the operator. The scorecard's source is copied to temporary
workspaces; the live repository is never mounted or modified. This function
never calls Nebius and is not a production authorization service.
"""
from __future__ import annotations

from hashlib import sha256
import ast
import argparse
import re
import subprocess
import json
from pathlib import Path
from typing import Callable
from tempfile import TemporaryDirectory

from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.planner import NexusPlanner
from aegisforge.nexus.executor import NexusExecutor
from aegisforge.nexus.orchestrator import NexusOrchestrator
from aegisforge.nexus.mission_control.mission_registry import NexusMissionRegistry
from aegisforge.nexus.mission_control.mission_service import NexusMissionService
from aegisforge.nexus.mission_control.mission_orchestrator_bridge import (
    NexusMissionOrchestratorBridge, verify_bridge_receipt,
)
from aegisforge.nexus.mission_control.mission_engineering_host import NexusMissionEngineeringHost
from aegisforge.nexus.proof.proof_graph import NexusProofGraph


class FixturePlanner:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, max_tokens=512, temperature=None):
        self.calls += 1
        plan = {
            "goal": "Review the synthetic calculator defect without executing tools",
            "steps": [
                {"id": "s1", "kind": "reason", "tool": None,
                 "title": "Inspect", "description": "Review the defect hypothesis",
                 "depends_on": [], "success_criteria": "Hypothesis recorded"},
                {"id": "s2", "kind": "verify", "tool": None,
                 "title": "Request a real test", "description": "Require an isolated test in a later phase",
                 "depends_on": ["s1"], "success_criteria": "Verification limitation recorded"},
            ],
            "assumptions": [], "risks": [],
        }
        return NebiusReply(content=json.dumps(plan), model="synthetic-client",
                           request_id="synthetic")


def main() -> None:
    actor, mission_id = "fixture.actor", "mission.synthetic.repair"
    with TemporaryDirectory(prefix="colossus_eng_host_") as work:
        root = Path(work)
        target = root / "calculator.py"
        target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        original = target.read_bytes()
        # These callbacks are deliberately open for a TEMPORARY OFFLINE FIXTURE.
        service = NexusMissionService(
            NexusMissionRegistry(), authorizer=lambda *args: True,
        )
        service.create_mission(
            mission_id, product="nexus", owner_id="fixture.owner",
            objective_sha256=sha256(b"synthetic code inspection").hexdigest(),
            actor_id=actor,
            expected_registry_sha256=service.registry_version(actor_id=actor),
        )
        client = FixturePlanner()
        orchestrator = NexusOrchestrator(NexusPlanner(client), NexusExecutor())
        bridge = NexusMissionOrchestratorBridge(service, orchestrator)
        host = NexusMissionEngineeringHost(
            bridge, workspace_root=root, workspace_authorizer=lambda *args: True,
        )
        inspected = host.inspect(
            mission_id, actor_id=actor,
            task="Inspect a synthetic incorrect addition function",
            target_file="calculator.py",
        )
        prepared = orchestrator.prepare("offline fixture: do not execute or change code")
        bound = host.bind_prepared(inspected["session_id"], actor_id=actor,
                                   prepared=prepared)
        preview = host.preview(inspected["session_id"], actor_id=actor)
        completed = host.finish_offline(inspected["session_id"], actor_id=actor)
        proof = host.proof_snapshot(inspected["session_id"], actor_id=actor)
        linked = host.link_evidence(inspected["session_id"], actor_id=actor)
        assert verify_bridge_receipt(preview) and verify_bridge_receipt(completed)
        assert NexusProofGraph.verify_snapshot(proof)
        assert target.read_bytes() == original
        assert client.calls == 1
        print(json.dumps({
            "schema": "colossus.engineering_host.offline_demo.v1",
            "synthetic": True,
            "repository_inspected": inspected["indexed_files"] == 1,
            "mission_bound": bound["stage"] == "bound",
            "preview_completed": preview["phase"] == "previewed",
            "offline_execution_phase": completed["phase"],
            "independent_verification": completed["verification_status"],
            "proof_graph_integrity": True,
            "evidence_linked": linked["reference_count"] == 1,
            "repository_modified": target.read_bytes() != original,
            "bug_repaired": False,
            "tools_invoked": completed["tools_invoked"],
            "nebius_calls": 0,
            "authenticity_verified": False,
        }, indent=2, sort_keys=True))


def scorecard_candidate_json(source: str) -> str:
    """A bounded, offline patch definition shared by preview and Docker run."""
    if not isinstance(source, str) or not 0 < len(source) <= 10_000:
        raise ValueError("Scorecard source outside reviewed limit")
    tree = ast.parse(source, filename="scripts/build_scorecard.py")
    # Limit this first real-source demo to the examined scorecard implementation.
    allowed_imports = {"__future__", "argparse", "json", "pathlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name not in allowed_imports for alias in node.names):
                raise ValueError("Unreviewed scorecard import")
        if isinstance(node, ast.ImportFrom):
            if node.module not in allowed_imports or node.level:
                raise ValueError("Unreviewed scorecard import")
    newline = "\r\n" if "\r\n" in source else "\n"
    search = newline.join((
        '    warning_count = len(summary_payload.get("warnings", []))',
        '    fallback_used = any("fallback" in w.lower() for w in summary_payload.get("warnings", []))',
    ))
    replacement = newline.join((
        '    warnings = summary_payload.get("warnings", [])',
        '    if not isinstance(warnings, list):',
        '        warnings = []',
        '    warning_count = len(warnings)',
        '    fallback_used = any("fallback" in w.lower() for w in warnings if isinstance(w, str))',
    ))
    if source.count(search) != 1:
        raise ValueError("Reviewed scorecard defect changed or is missing")
    return json.dumps({
        "summary": "Avoid crashing when warnings contain null or other non-string values.",
        "edits": [{"path": "scripts/build_scorecard.py", "search": search,
                   "replace": replacement,
                   "rationale": "Only lowercase string warnings; tolerate non-list warnings."}],
    })


_SCORECARD_REGRESSION = '# Disposable regression; generated only inside a TemporaryDirectory.\nimport json\nimport runpy\nimport sys\nfrom pathlib import Path\n\n\ndef test_scorecard_warning_values(tmp_path, monkeypatch):\n    source = Path(__file__).parent / "scripts" / "build_scorecard.py"\n    for warnings, expected_count, expected_fallback in (([None, "fallback used", 7], 3, True), (None, 0, False), (42, 0, False), ({"reason": "fallback"}, 0, False), ("fallback", 0, False)):\n        inp = tmp_path / "summary.json"\n        out = tmp_path / "scorecard.json"\n        inp.write_text(json.dumps({"status": "completed", "warnings": warnings}), encoding="utf-8")\n        monkeypatch.setattr(sys, "argv", [str(source), "--summary", str(inp), "--out", str(out)])\n        runpy.run_path(str(source), run_name="__main__")\n        result = json.loads(out.read_text(encoding="utf-8"))\n        assert result["status"] == "completed"\n        assert result["robustness_hint"] <= 1.0\n        assert any(f"Warnings observed: {expected_count}" in note for note in result["notes"]) == bool(expected_count)\n        assert any("Fallback path was required." in note for note in result["notes"]) == expected_fallback\n'

def run_docker_verified_repair(
    image: str, *,
    approval_callback: Callable[[str, str], bool] | None = None,
    scorecard_source: str | None = None,
    expected_source_sha256: str | None = None,
    expected_patchset_sha256: str | None = None,
    reviewed_candidate_json: str | None = None,
    reviewed_task: str | None = None,
    reviewed_mission_id: str | None = None,
    proposal_origin: str = "deterministic_offline_rule",
    reviewed_target_path: str | None = None,
    reviewed_regression_source: str | None = None,
) -> dict[str, object]:
    """One-file, supervised regression in Docker with Trust and ProofGraph.

    The only accepted targets are a fixed synthetic calculator or a reviewed
    snapshot of scripts/build_scorecard.py. A provided model diff must be
    rebuilt from the exact original bytes and match its approved patch SHA.
    No repo mounts, arbitrary commands or implicit approvals are permitted.
    """
    from aegisforge.nexus.mission_control.mission_supervised_executor import (
        NexusMissionSupervisedExecutor,
    )
    from aegisforge.nexus.mission_control.mission_sandbox_execution_bridge import (
        NexusMissionSandboxExecutionBridge,
    )
    from aegisforge.nexus.workspace.manager import NexusWorkspaceManager
    from aegisforge.nexus.workspace.workspace_policy import (
        NexusWorkspacePolicy, WorkspaceRule,
    )
    from aegisforge.nexus.trust.action_policy import NexusActionPolicy, ActionRule
    from aegisforge.nexus.trust.approval_gate import (
        NexusApprovalGate, ApprovalAttestation,
    )
    from aegisforge.nexus.trust.sandbox_runner import (
        DockerSandboxBackend, NexusSandboxRunner,
    )

    if not isinstance(image, str) or re.fullmatch(
        r"colossus-pytest@sha256:[0-9a-f]{64}", image
    ) is None:
        raise ValueError("Provide a local colossus-pytest@sha256 digest-pinned image")
    # Read-only image check. Never pull images or use an unpinned tag.
    check = subprocess.run(
        ["docker", "image", "inspect", image],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=15, check=False,
    )
    if check.returncode != 0:
        raise RuntimeError("Digest-pinned Docker image not available locally")

    real_source = scorecard_source is not None
    custom_regression = reviewed_regression_source is not None
    if custom_regression:
        from aegisforge.nexus.workspace.manager import _TEXT_SUFFIXES
        from pathlib import PurePosixPath
        if not real_source or not isinstance(reviewed_target_path, str):
            raise ValueError("Custom regression requires a reviewed target and source")
        if (len(reviewed_target_path) > 260 or reviewed_target_path.startswith("/")
                or ":" in reviewed_target_path or any(x in ("", ".", "..") for x in reviewed_target_path.split("/"))
                or PurePosixPath(reviewed_target_path).suffix.casefold() not in _TEXT_SUFFIXES):
            raise ValueError("Unsafe or unsupported reviewed target")
        if (type(reviewed_regression_source) is not str or not 20 <= len(reviewed_regression_source) <= 6000
                or "\x00" in reviewed_regression_source):
            raise ValueError("Invalid operator regression")
        test_tree = ast.parse(reviewed_regression_source, filename="test_colossus_review.py")
        if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
                   for n in test_tree.body):
            raise ValueError("Regression must define at least one pytest test function")
    if real_source:
        if not isinstance(scorecard_source, str) or not isinstance(expected_source_sha256, str) or not isinstance(expected_patchset_sha256, str):
            raise ValueError("Exact reviewed source and proposal digests are required")
        if sha256(scorecard_source.encode("utf-8")).hexdigest() != expected_source_sha256:
            raise ValueError("Source no longer matches the reviewed content")
        if not re.fullmatch(r"[a-f0-9]{64}", expected_patchset_sha256):
            raise ValueError("Reviewed patch digest invalid")
        if reviewed_candidate_json is not None:
            if (type(reviewed_candidate_json) is not str or len(reviewed_candidate_json) > (28000 if custom_regression else 16000)
                    or type(reviewed_task) is not str or not 10 <= len(reviewed_task) <= 900
                    or reviewed_mission_id not in {"mission.local.nebius.review", "mission.local.imported.review", "mission.local.generic.review"}
                    or proposal_origin not in {"nvidia_via_nebius", "user_imported_unverified"}):
                raise ValueError("Reviewed model candidate binding invalid")
            proposed_json = reviewed_candidate_json
            actor, mission_id = "fixture.actor", reviewed_mission_id
            inspection_task = reviewed_task
        else:
            proposed_json = scorecard_candidate_json(scorecard_source)
            actor, mission_id = "fixture.actor", "mission.local.scorecard.repair"
            inspection_task = "Handle null and non-string warnings safely"
        target_path = reviewed_target_path if custom_regression else "scripts/build_scorecard.py"
        test_path = "test_colossus_review.py" if custom_regression else "test_scorecard.py"
        original_source = scorecard_source
        fixture_test = reviewed_regression_source if custom_regression else _SCORECARD_REGRESSION
        patch_display = f"{target_path}: regression revisada por el operador"
    else:
        actor, mission_id = "fixture.actor", "mission.synthetic.repair"
        inspection_task = "Fix the synthetic addition defect"
        target_path, test_path = "calculator.py", "test_calculator.py"
        original_source = "def add(a, b):\n    return a - b\n"
        fixture_test = (
            "from calculator import add\n\n"
            "def test_addition():\n"
            "    assert add(2, 3) == 5\n"
        )
        proposed_json = json.dumps({
            "summary": "Fix an incorrect subtraction in the fixture addition function",
            "edits": [{"path": "calculator.py", "search": "return a - b",
                       "replace": "return a + b",
                       "rationale": "The regression test requires addition"}],
        })
        patch_display = "calculator.py: return a - b  →  return a + b"
    expected_hash = sha256(original_source.encode("utf-8")).hexdigest()
    test_hash = sha256(fixture_test.encode("utf-8")).hexdigest()

    def approve_interactively(label: str, fingerprint: str) -> bool:
        print(f"\n[{label}]\nFingerprint SHA-256: {fingerprint}")
        print("Only a disposable, reviewed code snapshot will be affected.")
        print("Type the following confirmation exactly to authorize this one effect:")
        token = "AUTORIZAR " + fingerprint[:12]
        print(token)
        if approval_callback is not None:
            return approval_callback(label, fingerprint) is True
        try:
            return input("Confirmation: ").strip() == token
        except (EOFError, KeyboardInterrupt):
            return False

    # An execution approval must be fresh when Docker starts. Review the host
    # effect *before* asking Trust for its exact, expiring, one-use approval.
    # Keep only the exact action fingerprint, never an unrestricted permit.
    preauthorized_validation_actions: set[str] = set()

    def candidate_write_authorizer(actor_id, mid, patch_sha, action_sha):
        if actor_id != actor or mid != mission_id:
            return False
        if action_sha in preauthorized_validation_actions:
            preauthorized_validation_actions.remove(action_sha)  # one use only
            return True
        print("Candidate write: TWO temporary copies only, not the live repository.")
        print("Reviewed patch:", patch_sha)
        return approve_interactively("HOST CANDIDATE WRITE", action_sha)

    with TemporaryDirectory(prefix="colossus_docker_repair_") as base_dir:
        parent = Path(base_dir)
        live_root = parent / "live_original"
        staging_root = parent / "policy_candidate"
        docker_root = parent / "docker_candidate"
        for root in (live_root, staging_root, docker_root):
            root.mkdir()
            target = root / target_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original_source.encode("utf-8"))
            (root / test_path).write_text(fixture_test, encoding="utf-8", newline="")

        registry = NexusMissionRegistry()
        registry.register(mission_id, product="nexus", owner_id="fixture.owner",
                          objective_sha256=sha256(("local validated source" if real_source else "local verified repair fixture").encode()).hexdigest(),
                          actor_id=actor)
        for component in ("trust", "mission_control", "engineering"):
            registry.assign_component(mission_id, component=component, actor_id=actor)
        registry.transition(mission_id, "ready", actor_id=actor)
        # Fixture-only allowlist; NOT a real identity and access-control provider.
        service = NexusMissionService(registry, authorizer=lambda *args: True)
        orchestrator = NexusOrchestrator(NexusPlanner(FixturePlanner()), NexusExecutor())
        mission_bridge = NexusMissionOrchestratorBridge(service, orchestrator)
        host = NexusMissionEngineeringHost(
            mission_bridge, workspace_root=live_root,
            workspace_authorizer=lambda *args: True,  # fixed fixture only
            repair_effects_authorizer=candidate_write_authorizer,
            allow_text_targets=custom_regression,
        )

        # A trusted host recalculates bindings from its actual stored plan.
        # Never use the request-supplied digest as the live binding provider.
        pending: dict[str, object] = {}
        controller: dict[str, object] = {}

        def current_binding(action):
            plan = pending[action.action_id]
            return controller["runner"].binding_for(plan)

        def reviewer(request):
            if request.mission_id != mission_id or request.relative_path != test_path:
                return None
            if request.operation != "run_tests":
                return None
            if not approve_interactively("TRUST TEST REVIEW", request.request_sha256):
                return None
            return ApprovalAttestation(
                request.request_sha256, "human.local_fixture",
                "interactive_fixture_review",
            )

        gate = NexusApprovalGate(staging_root, reviewer=reviewer,
                                 binding_provider=current_binding)
        policy = NexusWorkspacePolicy(
            NexusWorkspaceManager(staging_root),
            rules=[WorkspaceRule(mission_id, ".", frozenset({"read_text", "run_tests"}))],
            allow_mutations=True, approval_verifier=gate.verify_workspace_action,
        )
        action_policy = NexusActionPolicy(policy, rules=[
            ActionRule(mission_id, "sandbox.tool", "run_tests", test_path,
                       recipes=frozenset({"pytest_file"})),
        ])
        supervised = NexusMissionSupervisedExecutor(registry, action_policy)
        backend = DockerSandboxBackend(docker_root, python_image=image)
        runner = NexusSandboxRunner(policy, backend=backend)
        controller["runner"] = runner
        sandbox_bridge = NexusMissionSandboxExecutionBridge(
            registry, action_policy, supervised, runner,
            approval_gate=gate, effects_enabled=True,
        )
        inspected = host.inspect(mission_id, actor_id=actor,
                                 task=inspection_task,
                                 target_file=target_path)
        sid = inspected["session_id"]
        patch = host.review_repair_candidate(sid, actor_id=actor,
                                              candidate_json=proposed_json)
        if real_source and patch["patchset_sha256"] != expected_patchset_sha256:
            raise ValueError("Patch no longer matches the one reviewed in the interface")
        print("\nReviewed patch:", patch["patchset_sha256"])
        print("Original target hash:", expected_hash)
        print("Regression-test hash:", test_hash)
        print("No model call is made. Only disposable source snapshots are mounted.")

        def validated_run(*, baseline: bool) -> dict[str, object]:
            receipt = host.stage_repair_validation(
                sid, actor_id=actor, sandbox_bridge=sandbox_bridge,
                recipe="pytest_file", tool_id="sandbox.tool",
                test_file=test_path, baseline=baseline,
            )
            sandbox_id = receipt["sandbox_receipt"]["session_id"]
            action_sha = receipt["sandbox_receipt"]["action_sha256"]
            print("Host validation: ONE fixed pytest recipe on a temporary checkout.")
            if not approve_interactively("HOST VALIDATION REVIEW", action_sha):
                raise PermissionError("Host denied this Docker test")
            # Require BOTH independently reviewed grants for the same stage.
            # Trust's 600-second request begins after host review, so waiting
            # at the browser's host-confirmation dialog cannot expire it.
            preauthorized_validation_actions.add(action_sha)
            req = None
            try:
                req = host.request_repair_validation_review(
                    sid, actor_id=actor, sandbox_session_id=sandbox_id,
                )
                pending[req["request_id"]] = sandbox_bridge._staged[sandbox_id].sandbox
                decision = gate.review(req["request_id"])
                if decision.status != "approved":
                    raise PermissionError("Human review denied this Docker test")
                return host.run_repair_validation(
                    sid, actor_id=actor, sandbox_session_id=sandbox_id,
                    allow_effects=True,
                )
            finally:
                preauthorized_validation_actions.discard(action_sha)
                if req is not None:
                    gate.deny(req["request_id"])  # cancel any unused grant
                    pending.pop(req["request_id"], None)

        baseline = validated_run(baseline=True)
        if baseline["sandbox_receipt"]["status"] != "sandbox_failed":
            raise RuntimeError("Baseline must fail the regression test before repair")

        applied = host.materialize_repair_candidate(
            sid, actor_id=actor, sandbox_bridge=sandbox_bridge,
            allow_candidate_write=True,
        )
        if not applied["candidate_write_confirmed"]:
            raise RuntimeError("The reviewed repair was not written to both copies")
        candidate = validated_run(baseline=False)
        proof = host.repair_verification_proof(
            sid, actor_id=actor, proof_graph=NexusProofGraph(mission_id),
        )
        original_untouched = (live_root / target_path).read_bytes() == original_source.encode("utf-8")
        if not original_untouched:
            raise RuntimeError("Original fixture was changed: invariant violated")
        result = {
            "mode": "local_docker_verified_repair",
            "image": image,
            "synthetic": not real_source,
            "source_snapshot": real_source,
            "target_path": target_path,
            "base_sha256": expected_hash,
            "patchset_sha256": patch["patchset_sha256"],
            "regression_test_sha256": test_hash,
            "nebius_calls": 0,  # Validation session itself performs no inference.
            "proposal_origin": proposal_origin if real_source else "synthetic_fixture",
            "baseline": baseline["sandbox_receipt"]["status"],
            "candidate": candidate["sandbox_receipt"]["status"],
            "verified_repair": proof["verified_repair"],
            "verification_scope": (
                "operator_supplied_docker_regression_only" if custom_regression
                and proof["verified_repair"] is True else proof["verification_scope"]
            ),
            "proof_graph_sha256": proof["proof_graph_sha256"],
            "original_untouched": original_untouched,
            "human_review_required": True,
            "authenticity_verified": False,
        }
        # A model's candidate may fail its regression. Report the observed
        # failure and its ProofGraph digest; never silently substitute the
        # known local fix or claim verification succeeded.
        return result


def _entry() -> None:
    parser = argparse.ArgumentParser(description="Colossus engineering host demonstrations")
    parser.add_argument("--docker-verified-repair", action="store_true",
                        help="Run a real, explicitly approved Docker regression fixture")
    parser.add_argument("--image", default=None,
                        help="Existing local digest-pinned Python+pytest Docker image")
    args = parser.parse_args()
    if args.docker_verified_repair:
        if not args.image:
            parser.error("--image is required with --docker-verified-repair")
        print(json.dumps(run_docker_verified_repair(args.image), indent=2, sort_keys=True))
    else:
        if args.image:
            parser.error("--image requires --docker-verified-repair")
        main()


if __name__ == "__main__":
    _entry()
