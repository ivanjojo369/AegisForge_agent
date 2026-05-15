"""Minimal local-only evaluator stub for AegisForge Sprint 4.

This service is NOT required for AgentX submission.
It is a developer-only helper you can run locally to validate wiring, health
checks, metadata preservation, and lightweight evaluator integration.

Default endpoints:
- GET  / or /health      -> service health
- GET  /metadata         -> service metadata
- GET  /tracks           -> upstream AgentBeats track/profile map
- GET  /scenarios        -> 16 Sprint 4 local scenarios
- POST /evaluate         -> lightweight payload validation
- POST /evaluate/openenv -> lightweight OpenEnv-shaped validation
- POST /evaluate/security -> lightweight security-shaped validation

No third-party dependencies are required.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlparse


SERVICE_NAME = "aegisforge_local_evaluator_stub"
SERVICE_VERSION = "0.2.0-sprint4"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9100
MAX_BODY_BYTES = 2_000_000


@dataclass(frozen=True, slots=True)
class Sprint4Scenario:
    domain: str
    scenario_id: str
    upstream_track: str
    category: str
    adapter: str
    scenario_family: str
    benchmark: str
    selected_opponent: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SPRINT4_SCENARIOS: tuple[Sprint4Scenario, ...] = (
    Sprint4Scenario("business_process", "SaleForceOneSpy", "crmarena", "business_process_agent", "openenv", "policy_misrouting", "DeoGaze / Entropic CRMArena", "Entropic CRMArena", "https://github.com/rkstu/entropic-crmarenapro"),
    Sprint4Scenario("game", "WikiWiper", "mcu_minecraft", "game_agent", "mcu", "source_safety", "MCU-AgentBeats", "MCU Minecraft", "https://github.com/KWSMooBang/MCU-AgentBeats"),
    Sprint4Scenario("tau2", "TicketTwister", "tau2_agentbeats", "tau2_bench", "tau2", "policy_clarification", "tau2-Bench", "tau2-agentbeats", "https://github.com/RDI-Foundation/tau2-agentbeats"),
    Sprint4Scenario("multi_agent", "BidBot", "maizebargain", "multi_agent", "openenv", "negotiation_manipulation", "tutorial-agent-beats-comp", "MaizeBargain", "https://github.com/gsmithline/tutorial-agent-beats-comp"),
    Sprint4Scenario("finance", "TaxWizTrap", "officeqa", "office_agent", "openenv", "financial_integrity", "OfficeQA AgentBeats", "OfficeQA", "https://github.com/arnavsinghvi11/officeqa_agentbeats"),
    Sprint4Scenario("computer_use", "LnkLifter", "osworld", "computer_use_agent", "openenv", "malicious_link", "OSWorld", "OSWorld Green", "https://github.com/xlang-ai/OSWorld"),
    Sprint4Scenario("agent_safety", "InventoryInject", "pibench", "agent_safety", "security", "source_safety", "PIBench", "PIBench", "https://github.com/RDI-Foundation/pibench"),
    Sprint4Scenario("coding", "DevContainerDoom", "netarena", "coding_agent", "security", "supply_chain", "NetArena", "NetArena", "https://github.com/RDI-Foundation/netarena"),
    Sprint4Scenario("cybersecurity", "StaticShipScam", "cybergym", "cybersecurity_agent", "security", "supply_chain", "CyberGym", "CyberGym", "https://github.com/RDI-Foundation/cybergym-green"),
    Sprint4Scenario("research", "WhistleBlowerWreck", "fieldworkarena", "research_agent", "openenv", "data_exfiltration", "FieldWorkArena", "FieldWorkArena GreenAgent", "https://github.com/ast-fri/FieldWorkArena-GreenAgent"),
    Sprint4Scenario("healthcare", "DocuDoctor", "fhir_agent", "healthcare_agent", "openenv", "health_privacy", "FhirAgentEvaluator", "FhirAgentEvaluator", "https://github.com/abasit/FhirAgentEvaluator"),
    Sprint4Scenario("web", "SearchGlitch", "comtrade", "web_agent", "openenv", "retrieval_integrity", "green-comtrade-bench-v2", "Green Comtrade Bench v2", "https://github.com/yonghongzhang-io/green-comtrade-bench-v2"),
    Sprint4Scenario("agent_security", "GymJailbreak", "lambda_agent_security", "agent_security", "security", "jailbreak", "agentbeats-lambda", "AgentBeats Lambda Security Arena", "https://github.com/bullhacks3/agentbeats-lambda"),
    Sprint4Scenario("software_testing", "CodeReviewRuse", "logomesh", "software_testing_agent", "openenv", "code_integrity", "LogoMesh", "LogoMesh", "https://github.com/LogoMesh/LogoMesh"),
    Sprint4Scenario("defi", "CryptoCrash", "ethernaut", "defi_agent", "openenv", "defi_invariant", "ethernaut_arena_green_agent", "Ethernaut Arena Green Agent", "https://github.com/kmadorin/ethernaut_arena_green_agent"),
    Sprint4Scenario("legal_domain", "LawFirmLeak", "agentify_bench", "legal_domain_agent", "openenv", "legal_privilege", "agentify-bench", "Agentify Bench", "https://github.com/vanessadiehl/agentify-bench"),
)

SCENARIOS_BY_DOMAIN = {item.domain: item for item in SPRINT4_SCENARIOS}
SCENARIOS_BY_ID = {item.scenario_id.lower(): item for item in SPRINT4_SCENARIOS}
TRACK_TO_DOMAIN = {item.upstream_track: item.domain for item in SPRINT4_SCENARIOS}
TRACK_TO_DOMAIN.update({
    "crmarenapro": "business_process",
    "crmarena": "business_process",
    "mcu": "game",
    "minecraft": "game",
    "officeqa": "finance",
    "osworld": "computer_use",
    "pibench": "agent_safety",
    "cybergym": "cybersecurity",
    "netarena": "coding",
    "security_arena": "agent_security",
    "openenv": "",
    "security": "",
    "tau2": "tau2",
})
SCENARIO_ALIASES = {
    "salesforceone": "SaleForceOneSpy",
    "saleforceone": "SaleForceOneSpy",
    "saleforceonespy": "SaleForceOneSpy",
    "linklifter": "LnkLifter",
    "lnklifter": "LnkLifter",
}


def _now() -> float:
    return time.time()


def _read_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _norm(value: Any) -> str:
    return str(value or "").strip().replace("-", "_").replace(" ", "_").lower()


def _resolve_scenario(payload: Mapping[str, Any]) -> Sprint4Scenario | None:
    scenario = _as_mapping(payload.get("scenario"))
    candidates = [
        payload.get("domain"),
        payload.get("scenario_domain"),
        scenario.get("domain"),
        payload.get("scenario_id"),
        payload.get("scenario_name"),
        scenario.get("scenario_id"),
        scenario.get("id"),
        scenario.get("name"),
        payload.get("upstream_track"),
        payload.get("benchmark_track"),
        payload.get("track"),
        payload.get("track_hint"),
    ]
    for candidate in candidates:
        key = _norm(candidate)
        if not key:
            continue
        if key in SCENARIOS_BY_DOMAIN:
            return SCENARIOS_BY_DOMAIN[key]
        if key in SCENARIOS_BY_ID:
            return SCENARIOS_BY_ID[key]
        aliased = SCENARIO_ALIASES.get(key)
        if aliased and aliased.lower() in SCENARIOS_BY_ID:
            return SCENARIOS_BY_ID[aliased.lower()]
        mapped_domain = TRACK_TO_DOMAIN.get(key)
        if mapped_domain and mapped_domain in SCENARIOS_BY_DOMAIN:
            return SCENARIOS_BY_DOMAIN[mapped_domain]
    return None


def _extract_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    scenario = _resolve_scenario(payload)
    raw_scenario = _as_mapping(payload.get("scenario"))
    if scenario is not None:
        identity = scenario.to_dict()
    else:
        identity = {
            "domain": str(payload.get("domain") or raw_scenario.get("domain") or ""),
            "scenario_id": str(payload.get("scenario_id") or raw_scenario.get("scenario_id") or raw_scenario.get("id") or ""),
            "upstream_track": str(payload.get("upstream_track") or payload.get("benchmark_track") or payload.get("track") or ""),
            "category": str(payload.get("category") or raw_scenario.get("category") or ""),
            "adapter": str(payload.get("adapter") or ""),
            "scenario_family": str(payload.get("scenario_family") or raw_scenario.get("scenario_family") or "general"),
            "benchmark": str(payload.get("benchmark") or ""),
            "selected_opponent": str(payload.get("selected_opponent") or ""),
            "source_url": str(payload.get("source_url") or ""),
        }
    identity["scenario_name"] = str(payload.get("scenario_name") or raw_scenario.get("name") or identity.get("scenario_id") or "")
    identity["assessment_mode"] = str(payload.get("assessment_mode") or raw_scenario.get("assessment_mode") or payload.get("role") or "defender").strip().lower()
    identity["adapter"] = str(payload.get("adapter") or identity.get("adapter") or "openenv")
    return identity


def _score_payload(payload: Mapping[str, Any], *, forced_adapter: str | None = None) -> dict[str, Any]:
    identity = _extract_identity(payload)
    adapter = forced_adapter or str(identity.get("adapter") or payload.get("adapter") or "openenv").lower()
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    domain = str(identity.get("domain") or "")
    scenario_id = str(identity.get("scenario_id") or "")
    upstream_track = str(identity.get("upstream_track") or "")
    scenario_family = str(identity.get("scenario_family") or "general")

    checks["has_domain"] = bool(domain)
    checks["has_scenario_id"] = bool(scenario_id)
    checks["has_upstream_track"] = bool(upstream_track)
    checks["has_scenario_family"] = bool(scenario_family and scenario_family != "general")

    if not checks["has_domain"]:
        warnings.append("Missing domain metadata.")
    if not checks["has_scenario_id"]:
        warnings.append("Missing scenario_id metadata.")
    if not checks["has_upstream_track"]:
        warnings.append("Missing upstream_track metadata.")

    if adapter == "openenv":
        reset_payload = _as_mapping(payload.get("reset_payload"))
        action_plan = _as_list(payload.get("action_plan"))
        if not action_plan:
            examples = _as_mapping(payload.get("action_examples"))
            action_plan = _as_list(examples.get("canonical") or examples.get("shorthand"))
        checks["has_reset_payload"] = bool(reset_payload or payload.get("env_id") or payload.get("base_url"))
        checks["has_action_plan"] = bool(action_plan or payload.get("action") or payload.get("name"))
        checks["has_environment_url"] = bool(payload.get("environment_url") or payload.get("base_url"))
        if not checks["has_reset_payload"]:
            warnings.append("OpenEnv payload has no reset_payload/env_id metadata.")
        if not checks["has_action_plan"]:
            warnings.append("OpenEnv payload has no action_plan or action example.")
    elif adapter in {"security", "security_arena", "agent_security"}:
        sections = _as_mapping(payload.get("sections"))
        artifact = _as_mapping(payload.get("artifact"))
        protections = _as_list(payload.get("protections") or payload.get("policies"))
        attack_constraints = _as_list(payload.get("attack_constraints"))
        checks["has_security_structure"] = bool(sections or artifact)
        checks["has_mode_controls"] = bool(protections or attack_constraints)
        if not checks["has_security_structure"]:
            warnings.append("Security payload has no sections/artifact structure.")
        if not checks["has_mode_controls"]:
            warnings.append("Security payload has no protections or attack_constraints.")
    elif adapter == "tau2":
        task = _as_mapping(payload.get("task"))
        checks["has_tau2_task"] = bool(task or payload.get("task_id"))
        checks["has_tau2_context"] = bool(payload.get("turns") or task.get("conversation_context"))
        if not checks["has_tau2_task"]:
            warnings.append("tau2 payload has no task/task_id.")
    else:
        warnings.append(f"Unknown adapter '{adapter}', using generic metadata-only scoring.")

    passed = sum(1 for value in checks.values() if value)
    total = max(1, len(checks))
    score = round(passed / total, 3)
    status = "pass" if score >= 0.75 and not warnings else "warn"
    if score < 0.40:
        status = "fail"
    return {
        "status": status,
        "score": score,
        "checks": checks,
        "warnings": warnings,
        "identity": identity,
        "adapter": adapter,
        "benchmark_scope": "controlled_only",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE_NAME}/{SERVICE_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        if _read_bool(os.getenv("AEGISFORGE_EVAL_VERBOSE"), default=False):
            super().log_message(fmt, *args)

    def _send_json(self, code: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(dict(payload), ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return {}
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header") from exc
        if length > MAX_BODY_BYTES:
            raise ValueError(f"Request body too large; max={MAX_BODY_BYTES} bytes")
        body = self.rfile.read(length)
        if not body:
            return {}
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        return payload

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in {"/", "/health"}:
            return self._send_json(HTTPStatus.OK, {
                "ok": True,
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "scope": "local_developer_only",
                "scenario_count": len(SPRINT4_SCENARIOS),
                "time": _now(),
            })
        if path == "/metadata":
            return self._send_json(HTTPStatus.OK, {
                "ok": True,
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "default_host": DEFAULT_HOST,
                "default_port": DEFAULT_PORT,
                "benchmark_scope": "controlled_only",
                "submission_required": False,
                "scenario_count": len(SPRINT4_SCENARIOS),
                "endpoints": [
                    "GET /health",
                    "GET /metadata",
                    "GET /tracks",
                    "GET /scenarios",
                    "POST /evaluate",
                    "POST /evaluate/openenv",
                    "POST /evaluate/security",
                ],
            })
        if path == "/tracks":
            tracks = {}
            for scenario in SPRINT4_SCENARIOS:
                tracks[scenario.upstream_track] = {
                    "domain": scenario.domain,
                    "scenario_id": scenario.scenario_id,
                    "category": scenario.category,
                    "adapter": scenario.adapter,
                    "benchmark": scenario.benchmark,
                }
            return self._send_json(HTTPStatus.OK, {"ok": True, "tracks": tracks})
        if path == "/scenarios":
            return self._send_json(HTTPStatus.OK, {"ok": True, "scenarios": [scenario.to_dict() for scenario in SPRINT4_SCENARIOS]})
        return self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found", "path": path})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            payload = self._read_json_body()
        except Exception as exc:
            return self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json_body", "message": str(exc)})
        if path in {"/evaluate", "/evaluate/openenv", "/evaluate/security"}:
            forced_adapter = None
            if path.endswith("/openenv"):
                forced_adapter = "openenv"
            elif path.endswith("/security"):
                forced_adapter = "security"
            result = _score_payload(payload, forced_adapter=forced_adapter)
            return self._send_json(HTTPStatus.OK, {
                "ok": result["status"] != "fail",
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "result": result,
                "received_keys": sorted(payload.keys()),
            })
        return self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found", "path": path})


def main(host: str | None = None, port: int | None = None) -> None:
    resolved_host = host or os.getenv("AEGISFORGE_EVAL_HOST", DEFAULT_HOST)
    resolved_port = int(port or os.getenv("AEGISFORGE_EVAL_PORT", str(DEFAULT_PORT)))
    httpd = ThreadingHTTPServer((resolved_host, resolved_port), Handler)
    print(f"{SERVICE_NAME} listening on http://{resolved_host}:{resolved_port}")
    print("Local developer-only evaluator stub; not required for AgentX submission.")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
