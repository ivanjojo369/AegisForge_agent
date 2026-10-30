"""AegisForge Nexus: one Nebius planning call -> approved fixed tools -> independent checks.

Exact repository location: scripts/nexus/demo_nemotron_verified_workflow.py
Requires the existing scripts/nexus/demo_verified_workflow.py trusted fixture.

--offline: synthetic planning; no network or cost.
--live: one explicitly approved Nemotron inference, no automatic retries.
--execute: after the proposed plan is printed, requires a SECOND, separate
           interactive approval (EJECUTAR) before running two pinned local tools.

The model controls only the PROPOSAL of step types / allowlisted tool IDs. It
never supplies executable code, filesystem paths, shell commands, or tool
arguments. Only the pre-reviewed, static demo fixture can be executed. The
TemporaryDirectory is scoped but is NOT an OS sandbox for arbitrary code.
"""
from __future__ import annotations

import argparse
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping

from aegisforge.nexus.config import NexusConfig
from aegisforge.nexus.evidence import verify_jsonl
from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.orchestrator import NexusOrchestrator
from aegisforge.nexus.planner import NexusPlanner, NexusPlanningError
from aegisforge.nexus.verifier import EvidenceExpectation, IndependentCheck

# This is an already-reviewed local fixture, NOT a file supplied by Nemotron.
import demo_verified_workflow as trusted_demo

REPO_ROOT = Path(__file__).resolve().parents[2]

# Exactly four steps are accepted. The model cannot expand its privileges.
_EXPECTED_TOOLS = (None, "nexus.demo.write", "nexus.demo.tests", None)
_EXPECTED_KINDS = ("reason", "tool", "tool", "verify")
_EXPECTED_DEPS = ((), ("s1",), ("s2",), ("s3",))
_TASK = (
    "Propose an execution plan for a trusted, static Python clamp() fixture. "
    "The HOST has already vetted the exact fixture source and its six tests. "
    "You must NOT generate, modify or suggest executable code, paths or commands. "
    "The host tools accept zero arguments and run only the fixed vetted fixture. "
    "Return EXACTLY four steps: s1 reason reviewing scope, s2 tool 'nexus.demo.write' "
    "to write the fixed fixture, s3 tool 'nexus.demo.tests' to run the static tests, "
    "s4 verify with no tool. Required dependencies: s1 none, s2 [s1], "
    "s3 [s2], s4 [s3]. Verification will be performed by the HOST using "
    "separate trusted callbacks, not by the model."
)
_CONSTRAINTS = (
    "Exactly four steps in the order reason/tool/tool/verify; no other tools",
    "Tool IDs must be nexus.demo.write then nexus.demo.tests; zero arguments",
    "Do not generate code, shell commands, paths or tool arguments",
    "Only approved host fixture may run; final verification independent",
)


class UnsafePlanError(ValueError):
    """The model-proposed plan does not match the one permitted host workflow."""


def _safe_planning_diagnostic(error: NexusPlanningError) -> tuple[str, str]:
    """Classify planner failures without exposing provider output or secrets.

    NexusPlanningError is raised after local validation or model response
    validation. These fixed descriptions do not include the raw model answer.
    """
    message = str(error)
    if "truncated by the token limit" in message:
        return ("PLAN_TRUNCATED", "Nemotron termino por limite de tokens; el plan no es utilizable.")
    if any(x in message for x in (
        "did not return one valid JSON", "did not return JSON",
        "incomplete code fence", "plan must be a JSON object",
    )):
        return ("PLAN_INVALID_JSON", "La respuesta no era un objeto JSON valido conforme al contrato.")
    if "emitted tool calls" in message:
        return ("PLAN_TOOL_CALL_BLOCKED", "El modelo propuso llamadas a herramientas en vez de un plan JSON.")
    if "requested an unapproved tool" in message:
        return ("PLAN_TOOL_NOT_ALLOWED", "El plan incluyo una herramienta fuera de la lista autorizada.")
    if "steps must contain" in message:
        return ("PLAN_STEP_COUNT", "El numero de pasos no cumple el contrato del planificador.")
    if "final step must verify" in message:
        return ("PLAN_MISSING_VERIFICATION", "El plan no termina con una verificacion valida.")
    if "invalid dependencies" in message:
        return ("PLAN_DEPENDENCIES", "Hay dependencias invalidas entre los pasos.")
    if "must use ID" in message:
        return ("PLAN_STEP_IDS", "Los identificadores de pasos no son consecutivos.")
    if "unsupported kind" in message:
        return ("PLAN_STEP_KIND", "Uno de los pasos tiene una clase no permitida.")
    if "must have tool=null" in message:
        return ("PLAN_TOOL_CONTRACT", "Un paso de razonamiento/verificacion solicito una herramienta.")
    return ("PLAN_VALIDATION_REJECTED", "La validacion del plan rechazo la propuesta; no se ejecuto ninguna herramienta.")


def _checked_plan(plan: Any) -> None:
    """Fail closed: no partial or reordered tool sequences are executable."""
    steps = plan.steps
    if len(steps) != 4:
        raise UnsafePlanError("Plan blocked: expected exactly 4 steps. No tools were run.")
    for index, step in enumerate(steps):
        if (step.id != f"s{index + 1}" or step.kind != _EXPECTED_KINDS[index]
                or step.tool != _EXPECTED_TOOLS[index]
                or tuple(step.depends_on) != _EXPECTED_DEPS[index]):
            raise UnsafePlanError("Plan blocked: unexpected steps, tools or dependencies. No tools were run.")


def _build(live: bool, workspace: Path, *, client: Any = None, api_key: str | None = None):
    """Bind the same registered trusted tools proven by the earlier offline demo."""
    original_executor = trusted_demo._build_orchestrator(workspace).executor
    created_client = None
    if client is not None:
        planner = NexusPlanner(client, max_steps=4, max_tokens=3200)
    elif live:
        env = dict(os.environ)
        env["NEBIUS_API_KEY"] = api_key or ""
        env["NEXUS_PLANNER_MAX_STEPS"] = "4"
        env["NEXUS_PLANNER_MAX_TOKENS"] = "3200"
        cfg = NexusConfig.from_env(env, require_api_key=True)
        created_client = cfg.build_client()
        planner = cfg.build_planner(client=created_client)
    else:
        planner = NexusPlanner(trusted_demo._FixedOfflinePlannerClient(), max_steps=4, max_tokens=3200)
    return NexusOrchestrator(planner, original_executor), created_client


def _trusted_checks(workspace: Path):
    return [
        IndependentCheck(
            id="fixed_fixture_hashes", step_id="s2",
            description="Host independently reads pinned fixture bytes and SHA-256",
            handler=lambda step, record: trusted_demo._verify_exact_fixture(workspace),
            evidence_ref="host-fixture-byte-check",
        ),
        IndependentCheck(
            id="static_test_rerun", step_id="s3",
            description="Host independently reruns the predefined six test cases",
            handler=lambda step, record: trusted_demo._run_static_tests(workspace),
            evidence_ref="host-independent-tests",
        ),
        IndependentCheck(
            id="final_cross_check", step_id="s4",
            description="Host repeats fixed fixture validation and test checks",
            handler=lambda step, record: (
                trusted_demo._verify_exact_fixture(workspace)
                and trusted_demo._run_static_tests(workspace)
            ),
            evidence_ref="host-final-crosscheck",
        ),
    ]


def _safe_usage(usage: Mapping[str, Any]) -> dict[str, int]:
    return {k: v for k, v in usage.items()
            if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
            and type(v) is int and v >= 0}


def _plan_hash(plan: Any) -> str:
    """Fingerprint the model proposal without storing private/raw plan contents."""
    body = json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _public_plan(plan: Any) -> dict[str, Any]:
    """Only bounded plan metadata and operator-review text, no credentials."""
    return {"model": plan.model,
            "steps": [{"id": x.id, "kind": x.kind, "tool": x.tool,
                       "title": x.title[:160], "description": x.description[:500],
                       "success_criteria": x.success_criteria[:300],
                       "depends_on": list(x.depends_on)}
                      for x in plan.steps]}


def run_workflow(*, live: bool = False, execute: bool = False,
                 approved: bool = False, save_evidence: bool = False,
                 client: Any = None, api_key: str | None = None,
                 evidence_root: Path | None = None,
                 review_callback: Any = None) -> dict[str, Any]:
    """One prepare, then optional approved execution of the SAME prepared plan.

    Client injection and approved=True exist for automated local tests only.
    CLI still asks separately before inference and before execution.
    """
    if save_evidence and not execute:
        raise ValueError("Saving verified execution evidence requires execute=True")
    if execute and not approved and review_callback is None:
        raise PermissionError("No operator approval: execution is not permitted")
    with tempfile.TemporaryDirectory(prefix="aegisforge-nexus-live-plan-") as root:
        workspace = Path(root)
        orchestrator, created_client = _build(live, workspace, client=client, api_key=api_key)
        try:
            # Exactly one model call in live mode. No retries and no tooling here.
            prepared = orchestrator.prepare(_TASK, constraints=_CONSTRAINTS)
            plan = prepared.plan
            safe_summary: dict[str, Any] = {
                "mode": ("live_nebius" if client is None and live else
                         "injected_test_client" if client is not None else "offline_simulated"),
                "live_inference": bool(live and client is None),
                "model": plan.model,
                "request_id": plan.request_id,
                "usage": _safe_usage(plan.usage),
                "plan_sha256": _plan_hash(plan),
                "plan_step_count": len(plan.steps),
                "plan_steps": _public_plan(plan)["steps"],
                "tools_invoked": 0,
                "independent_checks_run": 0,
                "verification_status": "not_run",
            }
            # REVIEW IS BEFORE PERMISSION TO EXECUTE. The plan may be rejected.
            _checked_plan(plan)
            if not execute:
                preview = orchestrator.preview(prepared)
                safe_summary.update({"status": "review_only", "preview_status": preview.status})
                return safe_summary
            if review_callback is not None:
                approved = review_callback(_public_plan(plan)) is True
            if not approved:
                safe_summary["status"] = "operator_declined"
                return safe_summary

            # Only static pre-reviewed source and tests. The model's text is NOT
            # handed to tools as arguments or executable instructions.
            attestations = {"s1": "Host reviewed static scope and sequence",
                            "s4": "Host approved independent fixed test verification"}
            preview = orchestrator.preview(prepared, manual_evidence=attestations)
            outcome = orchestrator.run(
                prepared, execute_tools=True, approved_mutations=frozenset({"s2"}),
                manual_evidence=attestations,
                expectations=[
                    EvidenceExpectation("s2", "source_sha256", trusted_demo._sha256(trusted_demo.SOURCE_CODE)),
                    EvidenceExpectation("s2", "test_sha256", trusted_demo._sha256(trusted_demo.TEST_CODE)),
                    EvidenceExpectation("s3", "exit_state", "pass"),
                ],
                independent_checks=_trusted_checks(workspace),
            )
            safe_summary.update({
                "status": "completed" if outcome.status == "verified" else "not_verified",
                "preview_status": preview.status,
                "execution_status": outcome.execution.status,
                "verification_status": outcome.status,
                "tools_invoked": outcome.execution.tools_invoked,
                "independent_checks_run": outcome.verification.independent_checks_run,
                "independent_checks_passed": outcome.verification.independent_checks_passed,
                "audit_events": len(outcome.evidence.events),
                "audit_sha256": outcome.evidence.final_sha256,
                "temporary_workspace_removed_on_exit": True,
                "fixed_fixture_only": True,
            })
            if save_evidence:
                folder = Path(evidence_root) if evidence_root else REPO_ROOT / "evidence" / "nexus"
                folder.mkdir(parents=True, exist_ok=True)
                stem = f"nemotron_verified_{'live' if live else 'offline'}_{outcome.evidence.run_id}"
                journal = folder / (stem + ".jsonl")
                metadata = folder / (stem + ".json")
                outcome.evidence.save_jsonl(journal)
                try:
                    with metadata.open("x", encoding="utf-8") as file:
                        json.dump({k: v for k, v in safe_summary.items() if k != "plan_steps"},
                                  file, ensure_ascii=False, indent=2)
                    verify_jsonl(journal)
                except Exception:
                    journal.unlink(missing_ok=True)
                    metadata.unlink(missing_ok=True)
                    raise
                safe_summary["summary_path"] = str(metadata)
                safe_summary["audit_path"] = str(journal)
            return safe_summary
        finally:
            if created_client is not None:
                created_client.close()


def _get_secret() -> str:
    key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if key:
        return key
    if not sys.stdin.isatty():
        raise ValueError("An authorized API key is required for live mode")
    key = getpass("Introduce tu NEBIUS_API_KEY (entrada oculta): ").strip()
    if not key:
        raise ValueError("No API key provided; canceled without inference")
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description="Review a REAL Nemotron plan, then optionally run trusted fixed tools")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--offline", action="store_true", help="No API calls; synthetic plan")
    selection.add_argument("--live", action="store_true", help="One authorized Nebius inference")
    parser.add_argument("--execute", action="store_true", help="Offer operator-reviewed execution after planning")
    parser.add_argument("--save-evidence", action="store_true", help="Save sanitized JSON + verified JSONL")
    args = parser.parse_args()
    if args.save_evidence and not args.execute:
        parser.error("--save-evidence requires --execute")
    try:
        if args.live:
            print("Una inferencia REAL puede consumir créditos; NO habrá reintentos automáticos.")
            if input("Para autorizar UNA inferencia escribe SI: ").strip().upper() != "SI":
                print("Cancelado; no se envió ninguna petición.")
                return 0
            key = _get_secret()
        else:
            key = None

        def ask_operator(plan: dict[str, Any]) -> bool:
            print("\nPlan propuesto (SIN herramientas ejecutadas):")
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            print("Se permite SOLO escribir la plantilla fija en carpeta temporal y ejecutar sus pruebas.")
            print("No se ejecutará código generado por Nemotron ni comandos arbitrarios.")
            answer = input("Tras revisar el plan, escribe EJECUTAR para autorizar: ").strip()
            return answer == "EJECUTAR"

        report = run_workflow(live=args.live, execute=args.execute,
                              save_evidence=args.save_evidence, api_key=key,
                              review_callback=ask_operator if args.execute else None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.execute and report["status"] not in {"completed", "operator_declined"}:
            return 1
        return 0
    except UnsafePlanError as error:
        # Static error, not model text; safe to display the reason.
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except NexusPlanningError as error:
        # Do not expose raw Nemotron output, prompt, API key or HTTP bodies.
        code, description = _safe_planning_diagnostic(error)
        print(f"ERROR [NexusPlanningError/{code}]: {description}", file=sys.stderr)
        print("No se ejecutaron herramientas y no habra reintentos automaticos.", file=sys.stderr)
        return 1
    except Exception as error:
        # Omit error body entirely: SDK failures may include tokens or task text.
        print(f"ERROR [{type(error).__name__}]: etapa detenida sin reintentos automáticos.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
