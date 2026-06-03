from __future__ import annotations

"""Operational runtime entrypoint for AegisForge.

This runner is intentionally separate from ``src/aegisforge_eval/runner.py``.
It is the submission-facing entrypoint that:
- boots the A2A server
- wires the real runtime app (Agent -> Executor -> A2A)
- can print the advertised Agent Card
- can run a lightweight runtime doctor check before submission

Recommended location in the repo:
    src/aegisforge/runner.py
"""

import argparse
import json
import os
from dataclasses import asdict, is_dataclass
from typing import Any

import uvicorn

from .a2a_server import build_agent_card, build_app
from .agent import AegisForgeAgent
from .executor import Executor


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8001
RUNNER_VERSION = "aegisforge_runner_v0_2_skillsbench_runtime_snapshot_2026_06_03"

SELECTED_OPPONENT_TRACKS = (
    "mcu",
    "officeqa",
    "crmarena",
    "fieldworkarena",
    "maizebargain",
    "tau2",
    "osworld",
    "pibench",
    "cybergym",
    "netarena",
    "skillsbench",
)

ADDITIONAL_GENERALIST_TRACKS = (
    "healthcare",
    "web",
    "lambda_agent_security",
    "software_testing",
    "defi",
    "legal_domain",
)

FINAL_SCENARIOS = (
    "SaleForceOneSpy",
    "WikiWiper",
    "TicketTwister",
    "BidBot",
    "TaxWizTrap",
    "LnkLifter",
    "InventoryInject",
    "DevContainerDoom",
    "StaticShipScam",
    "WhistleBlowerWreck",
    "DocuDoctor",
    "SearchGlitch",
    "GymJailbreak",
    "CodeReviewRuse",
    "CryptoCrash",
    "LawFirmLeak",
)

FINAL_SCENARIO_IDS = (
    "saleforceonespy",
    "wikiwiper",
    "tickettwister",
    "bidbot",
    "taxwiztrap",
    "lnklifter",
    "inventoryinject",
    "devcontainerdoom",
    "staticshipscam",
    "whistleblowerwreck",
    "docudoctor",
    "searchglitch",
    "gymjailbreak",
    "codereviewruse",
    "cryptocrash",
    "lawfirmleak",
)

TRACK_TAGS = (
    "mcu",
    "minecraft-benchmark",
    "officeqa",
    "finance",
    "crmarena",
    "business-process",
    "fieldworkarena",
    "research",
    "maizebargain",
    "multi-agent",
    "tau2",
    "osworld",
    "computer-use",
    "pibench",
    "agent-safety",
    "cybergym",
    "cybersecurity",
    "netarena",
    "skillsbench",
    "skillsbench-leaderboard",
    "benchflow-ai",
    "standard-v1",
    "with-skills",
    "general-purpose-agent",
    "filesystem-first",
    "filesystem-output",
    "sandbox-file-output",
    "coding-agent",
    "healthcare",
    "healthcare-agent",
    "web",
    "web-agent",
    "lambda-agent-security",
    "software-testing",
    "defi",
    "legal-domain",
    "docudoctor",
    "searchglitch",
    "gymjailbreak",
    "codereviewruse",
    "cryptocrash",
    "lawfirmleak",
    "saleforceonespy",
    "lnklifter",
    "openenv",
    "security",
)

SUPPORTED_MODES = (
    "attacker",
    "defender",
)


SKILLSBENCH_OUTPUT_ROOTS = (
    "/root",
    "/root/output",
    "/app/workspace",
    "/app/output",
    "/output",
    "/workspace",
    "/home/github/build/failed",
    "/logs/verifier",
)

SKILLSBENCH_SOLVER_ALIGNED_FAMILIES = (
    "json_output",
    "csv_output",
    "code_solution",
    "office_xlsx",
    "office_docx",
    "pdf_document",
    "lean_solution",
    "security_config",
    "general_file_output",
)

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _dedupe_strings(items: tuple[str, ...] | list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _normalize_base_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    return url if url.endswith("/") else f"{url}/"


def _resolve_host(value: str | None) -> str:
    value = (value or "").strip()
    return value or DEFAULT_HOST


def _resolve_port(value: int | None) -> int:
    if value is None:
        return DEFAULT_PORT
    port = int(value)
    return port if port != 8000 else DEFAULT_PORT


def _resolve_card_url(host: str, port: int, explicit: str | None) -> str:
    explicit = _normalize_base_url(explicit)
    if explicit:
        return explicit

    public = _normalize_base_url(os.getenv("AEGISFORGE_PUBLIC_URL") or os.getenv("PUBLIC_URL"))
    if public:
        return public

    safe_host = "localhost" if host in {"0.0.0.0", "::", ""} else host
    return f"http://{safe_host}:{port}/"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()  # type: ignore[no-any-return, attr-defined]
    if hasattr(value, "dict"):
        return value.dict()  # type: ignore[no-any-return, attr-defined]
    if hasattr(value, "as_dict"):
        return value.as_dict()  # type: ignore[no-any-return]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def _skillsbench_runtime_metadata() -> dict[str, Any]:
    return {
        "enabled": True,
        "benchmark": "SkillsBench",
        "task_set": "standard-v1",
        "condition": "with_skills",
        "route": "general_purpose_filesystem_first",
        "filesystem_output_primary": True,
        "artifact_refs_diagnostic_only": True,
        "a2a_file_part_optional_diagnostic": True,
        "known_output_roots": list(SKILLSBENCH_OUTPUT_ROOTS),
        "solver_aligned_families": list(SKILLSBENCH_SOLVER_ALIGNED_FAMILIES),
        "expected_log_markers": [
            "AEGISFORGE_SKILLSBENCH_SOLVER_DISPATCH",
            "AEGISFORGE_SKILLSBENCH_WORKSPACE_EXECUTOR",
        ],
        "non_regression_note": (
            "SkillsBench scoring should be treated as real sandbox filesystem outputs; "
            "artifact_refs/FilePart metadata is diagnostic compatibility only."
        ),
    }


def _build_runtime_snapshot(*, host: str, port: int, card_url: str, mode: str) -> dict[str, Any]:
    return {
        "runner_version": RUNNER_VERSION,
        "mode": mode,
        "host": host,
        "port": port,
        "advertised_card_url": card_url,
        "debug_artifacts": _env_bool("AEGISFORGE_DEBUG_ARTIFACTS", False),
        "trace_artifacts": _env_bool("AEGISFORGE_TRACE_ARTIFACTS", False),
        "max_context_agents": os.getenv("AEGISFORGE_MAX_CONTEXT_AGENTS", "128"),
        "default_assessment_mode": os.getenv("AEGISFORGE_DEFAULT_ASSESSMENT_MODE", "defender"),
        "default_track": os.getenv("AEGISFORGE_TRACK", "purple"),
        "officeqa_enabled": _env_bool("AEGISFORGE_ENABLE_OFFICEQA", False),
        "crmarena_enabled": _env_bool("AEGISFORGE_ENABLE_CRMARENA", False),
        "skillsbench_enabled": _env_bool("AEGISFORGE_ENABLE_SKILLSBENCH", True),
        "tags": _dedupe_strings(list(TRACK_TAGS)),
        "selected_opponent_tracks": _dedupe_strings(list(SELECTED_OPPONENT_TRACKS)),
        "additional_generalist_tracks": list(ADDITIONAL_GENERALIST_TRACKS),
        "selected_scenarios": list(FINAL_SCENARIOS),
        "selected_scenario_ids": list(FINAL_SCENARIO_IDS),
        "scenario_count": len(FINAL_SCENARIOS),
        "track_alias_note": "mcu and mcu-minecraft are the same selected Game Agent opponent; skillsbench is the standard-v1 general-purpose filesystem-first route.",
        "scenario_alignment": "AgentX-AgentBeats Phase 2 Sprint 4 generalist 16-scenario set plus SkillsBench standard-v1 general-purpose evaluator.",
        "supported_assessment_modes": list(SUPPORTED_MODES),
        "skillsbench": _skillsbench_runtime_metadata(),
    }


def _render_json(payload: dict[str, Any], *, pretty: bool) -> str:
    if pretty:
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    return json.dumps(payload, ensure_ascii=False)


def _build_doctor_report(*, host: str, port: int, card_url: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "ok",
        "runtime": _build_runtime_snapshot(host=host, port=port, card_url=card_url, mode="doctor"),
        "checks": {},
    }

    checks = report["checks"]

    try:
        agent = AegisForgeAgent()
        checks["agent_init"] = {
            "ok": True,
            "prompt_loader": agent.prompt_loader.__class__.__name__,
            "context_mapper": agent.context_mapper.__class__.__name__,
            "policy_bridge": agent.policy_bridge.__class__.__name__,
            "mcu_adapter": agent.mcu_adapter.__class__.__name__ if getattr(agent, "mcu_adapter", None) else None,
            "officeqa_adapter": agent.officeqa_adapter.__class__.__name__ if getattr(agent, "officeqa_adapter", None) else None,
            "crmarena_adapter": agent.crmarena_adapter.__class__.__name__ if getattr(agent, "crmarena_adapter", None) else None,
            "classifier": agent.classifier.__class__.__name__,
            "router": agent.router.__class__.__name__,
            "planner": agent.planner.__class__.__name__,
            "self_check": agent.self_check.__class__.__name__,
        }
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        checks["agent_init"] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        report["status"] = "degraded"

    try:
        executor = Executor()
        checks["executor_init"] = {
            "ok": True,
            **executor.snapshot(),
        }
    except Exception as exc:  # pragma: no cover
        checks["executor_init"] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        report["status"] = "degraded"

    try:
        card = build_agent_card(url=card_url)
        checks["agent_card"] = {
            "ok": True,
            "name": getattr(card, "name", "AegisForge"),
            "version": getattr(card, "version", None),
            "skills": [getattr(skill, "id", None) for skill in getattr(card, "skills", [])],
        }
    except Exception as exc:  # pragma: no cover
        checks["agent_card"] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        report["status"] = "degraded"

    try:
        app = build_app(host=host, port=port, card_url=card_url)
        checks["app_build"] = {
            "ok": True,
            "class": app.__class__.__name__,
            "routes": [getattr(route, "path", str(route)) for route in getattr(app, "routes", [])],
        }
    except Exception as exc:  # pragma: no cover
        checks["app_build"] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        report["status"] = "degraded"

    try:
        from .adapters.skillsbench.task_catalog import validate_task_catalog_selftest
        from .adapters.skillsbench.task_environment import validate_task_identity_selftest

        catalog_check = validate_task_catalog_selftest()
        identity_check = validate_task_identity_selftest()
        checks["skillsbench_routing"] = {
            "ok": bool(catalog_check.get("ok")) and bool(identity_check.get("ok")),
            "catalog": catalog_check,
            "identity": identity_check,
            "runtime": _skillsbench_runtime_metadata(),
        }
        if not checks["skillsbench_routing"]["ok"]:
            report["status"] = "degraded"
    except Exception as exc:  # pragma: no cover
        checks["skillsbench_routing"] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        report["status"] = "degraded"

    try:
        from .artifact_policy import validate_artifact_policy_selftest

        artifact_policy_check = validate_artifact_policy_selftest()
        checks["artifact_policy"] = artifact_policy_check
        if not artifact_policy_check.get("ok"):
            report["status"] = "degraded"
    except Exception as exc:  # pragma: no cover
        checks["artifact_policy"] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        report["status"] = "degraded"

    return report


def _emit_card(*, card_url: str, pretty: bool) -> int:
    card = build_agent_card(url=card_url)
    payload = _jsonable(card)
    if not isinstance(payload, dict):
        payload = {"card": payload}
    print(_render_json(payload, pretty=pretty))
    return 0


def _run_doctor(*, host: str, port: int, card_url: str, pretty: bool) -> int:
    report = _build_doctor_report(host=host, port=port, card_url=card_url)
    print(_render_json(report, pretty=pretty))
    return 0 if report.get("status") == "ok" else 1


def _run_server(*, host: str, port: int, card_url: str, log_level: str, access_log: bool) -> int:
    app = build_app(host=host, port=port, card_url=card_url)

    startup_payload = {
        "status": "starting",
        "runtime": _build_runtime_snapshot(host=host, port=port, card_url=card_url, mode="serve"),
    }
    print(_render_json(startup_payload, pretty=True))

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=access_log,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the AegisForge operational runtime.")
    parser.add_argument(
        "--mode",
        choices=("serve", "card", "doctor"),
        default=os.getenv("AEGISFORGE_RUNNER_MODE", "serve"),
        help="serve: start the A2A server; card: print the Agent Card JSON; doctor: validate runtime wiring.",
    )
    parser.add_argument("--host", default=os.getenv("AEGISFORGE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("AEGISFORGE_PORT", str(DEFAULT_PORT))))
    parser.add_argument(
        "--card-url",
        default=os.getenv("AEGISFORGE_CARD_URL") or None,
        help="Public base URL advertised in the Agent Card. Falls back to AEGISFORGE_PUBLIC_URL/PUBLIC_URL or localhost.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output for card/doctor modes.")
    parser.add_argument("--log-level", default=os.getenv("AEGISFORGE_LOG_LEVEL", "info"))
    parser.add_argument(
        "--access-log",
        dest="access_log",
        action="store_true",
        default=_env_bool("AEGISFORGE_ACCESS_LOG", True),
        help="Enable uvicorn access logs in serve mode.",
    )
    parser.add_argument(
        "--no-access-log",
        dest="access_log",
        action="store_false",
        help="Disable uvicorn access logs in serve mode.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    host = _resolve_host(args.host)
    port = _resolve_port(args.port)
    card_url = _resolve_card_url(host, port, args.card_url)

    if args.mode == "card":
        return _emit_card(card_url=card_url, pretty=args.pretty)
    if args.mode == "doctor":
        return _run_doctor(host=host, port=port, card_url=card_url, pretty=args.pretty)
    return _run_server(
        host=host,
        port=port,
        card_url=card_url,
        log_level=args.log_level,
        access_log=args.access_log,
    )


def validate_runner_selftest() -> dict[str, Any]:
    """Validate runner metadata without starting uvicorn."""

    snapshot = _build_runtime_snapshot(
        host="127.0.0.1",
        port=8001,
        card_url="http://127.0.0.1:8001/",
        mode="doctor",
    )
    errors: list[str] = []
    selected = snapshot.get("selected_opponent_tracks", [])
    tags = snapshot.get("tags", [])
    skillsbench = snapshot.get("skillsbench", {})

    if "skillsbench" not in selected:
        errors.append("skillsbench missing from selected_opponent_tracks")
    if "skillsbench" not in tags:
        errors.append("skillsbench missing from TRACK_TAGS/runtime tags")
    if not isinstance(skillsbench, dict) or not skillsbench.get("filesystem_output_primary"):
        errors.append("skillsbench runtime metadata missing filesystem_output_primary")
    if "AEGISFORGE_SKILLSBENCH_SOLVER_DISPATCH" not in skillsbench.get("expected_log_markers", []):
        errors.append("missing solver dispatch marker expectation")
    if 8000 == DEFAULT_PORT:
        errors.append("DEFAULT_PORT must not be 8000")

    return {
        "ok": not errors,
        "errors": errors,
        "version": RUNNER_VERSION,
        "snapshot": snapshot,
    }


__all__ = [
    "RUNNER_VERSION",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "SELECTED_OPPONENT_TRACKS",
    "ADDITIONAL_GENERALIST_TRACKS",
    "TRACK_TAGS",
    "SKILLSBENCH_OUTPUT_ROOTS",
    "SKILLSBENCH_SOLVER_ALIGNED_FAMILIES",
    "build_parser",
    "main",
    "validate_runner_selftest",
]

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
