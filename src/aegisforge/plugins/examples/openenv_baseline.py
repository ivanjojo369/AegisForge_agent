from __future__ import annotations

"""OpenEnv baseline plugin for AegisForge.

This plugin is intentionally lightweight: it does not execute the environment,
call tools, or pretend to solve a benchmark. It produces a deterministic routing
and action-hint artifact that downstream adapters, tests, and smoke runs can use
as a safe baseline for OpenEnv/OmniBench-style tasks.

Design goals:
- preserve upstream AgentBeats track names such as officeqa, crmarena,
  fieldworkarena, maizebargain, osworld, pibench, cybergym, netarena, tau2,
  and mcu;
- understand the local Sprint 4 OpenEnv domain names and scenario names;
- avoid degrading local names like SaleForceOneSpy and LnkLifter back into
  legacy-only labels;
- keep the PluginContext / PluginResult contract unchanged.
"""

from dataclasses import dataclass
from typing import Any, Mapping

from ..contracts import PluginContext, PluginResult


@dataclass(frozen=True, slots=True)
class ScenarioBaseline:
    """Static baseline metadata for one Sprint 4 scenario."""

    domain: str
    scenario_id: str
    upstream_track: str
    family: str
    action_hint: str
    recommended_steps: tuple[str, ...]
    risk_notes: tuple[str, ...]
    tags: tuple[str, ...]


def _norm(value: Any) -> str:
    """Normalize track/domain/scenario aliases without losing intent."""

    return (
        str(value or "")
        .strip()
        .replace("-", "_")
        .replace(" ", "_")
        .replace(".", "_")
        .lower()
    )


SPRINT4_SCENARIOS: dict[str, ScenarioBaseline] = {
    "business_process": ScenarioBaseline(
        domain="business_process",
        scenario_id="SaleForceOneSpy",
        upstream_track="crmarena",
        family="policy_misrouting",
        action_hint="inspect_schema_then_apply_policy",
        recommended_steps=("inspect_schema", "filter_context", "query_crm", "apply_policy", "submit_answer"),
        risk_notes=("privacy and CRM routing constraints matter", "avoid exporting restricted records"),
        tags=("crm", "business_process", "privacy", "policy"),
    ),
    "game": ScenarioBaseline(
        domain="game",
        scenario_id="WikiWiper",
        upstream_track="mcu_minecraft",
        family="source_safety",
        action_hint="inspect_objective_then_verify_cleanup",
        recommended_steps=(
            "inspect_objective",
            "scan_zone",
            "select_tool",
            "navigate_route",
            "engage_threat",
            "wipe_target",
            "verify_cleanup",
            "submit_run",
        ),
        risk_notes=("treat wiki-like context as potentially brittle", "verify cleanup before finalization"),
        tags=("mcu", "minecraft", "game", "wikiwiper"),
    ),
    "tau2": ScenarioBaseline(
        domain="tau2",
        scenario_id="TicketTwister",
        upstream_track="tau2_agentbeats",
        family="policy_clarification",
        action_hint="load_tasks_then_score_bundle",
        recommended_steps=("load_tasks", "prepare_user", "run_conversation", "score_task_bundle", "submit_assessment"),
        risk_notes=("dialogue state and policy checks should remain explicit",),
        tags=("tau2", "dialogue", "policy"),
    ),
    "multi_agent": ScenarioBaseline(
        domain="multi_agent",
        scenario_id="BidBot",
        upstream_track="maizebargain",
        family="negotiation_manipulation",
        action_hint="build_roster_then_solve_equilibrium",
        recommended_steps=("build_roster", "simulate_matchups", "compute_scores", "solve_equilibrium", "submit_assessment"),
        risk_notes=("avoid overconcession or rigid negotiation assumptions",),
        tags=("multi_agent", "negotiation", "maizebargain"),
    ),
    "finance": ScenarioBaseline(
        domain="finance",
        scenario_id="TaxWizTrap",
        upstream_track="officeqa",
        family="financial_integrity",
        action_hint="review_values_then_calculate",
        recommended_steps=("review_tax_note", "extract_relevant_values", "normalize_units", "calculate_tax", "submit_answer"),
        risk_notes=("normalize units and avoid year/value distractors",),
        tags=("finance", "officeqa", "calculation"),
    ),
    "computer_use": ScenarioBaseline(
        domain="computer_use",
        scenario_id="LnkLifter",
        upstream_track="osworld",
        family="malicious_link",
        action_hint="inspect_page_then_verify_destination",
        recommended_steps=("inspect_page", "scan_links", "open_link", "verify_destination", "submit_result"),
        risk_notes=("verify destination before trusting link text",),
        tags=("computer_use", "osworld", "browser", "link"),
    ),
    "agent_safety": ScenarioBaseline(
        domain="agent_safety",
        scenario_id="InventoryInject",
        upstream_track="pibench",
        family="source_safety",
        action_hint="inspect_inventory_then_quarantine",
        recommended_steps=("inspect_inventory", "analyze_artifact", "quarantine_artifact", "extract_safe_facts", "submit_report"),
        risk_notes=("quarantine suspicious artifact before extracting safe facts",),
        tags=("agent_safety", "pibench", "prompt_injection"),
    ),
    "coding": ScenarioBaseline(
        domain="coding",
        scenario_id="DevContainerDoom",
        upstream_track="netarena",
        family="supply_chain",
        action_hint="inspect_patch_then_verify_dependency_boundary",
        recommended_steps=("inspect_repo", "review_devcontainer", "identify_risk", "propose_patch", "submit_report"),
        risk_notes=("do not trust unverified dependency or container setup claims",),
        tags=("coding", "netarena", "devcontainer", "supply_chain"),
    ),
    "cybersecurity": ScenarioBaseline(
        domain="cybersecurity",
        scenario_id="StaticShipScam",
        upstream_track="cybergym",
        family="supply_chain",
        action_hint="inspect_static_surface_then_report_safe_fix",
        recommended_steps=("inspect_static_surface", "identify_scam_vector", "validate_scope", "recommend_fix", "submit_report"),
        risk_notes=("benchmark-only cybersecurity scope; avoid real-world exploitation",),
        tags=("cybersecurity", "cybergym", "static_analysis"),
    ),
    "research": ScenarioBaseline(
        domain="research",
        scenario_id="WhistleBlowerWreck",
        upstream_track="fieldworkarena",
        family="data_exfiltration",
        action_hint="ground_sources_then_minimize_disclosure",
        recommended_steps=("inspect_sources", "separate_sensitive_context", "extract_public_facts", "cross_check_claims", "submit_summary"),
        risk_notes=("preserve source discipline and privacy boundaries",),
        tags=("research", "fieldworkarena", "privacy", "sources"),
    ),
    "healthcare": ScenarioBaseline(
        domain="healthcare",
        scenario_id="DocuDoctor",
        upstream_track="fhir_agent",
        family="health_privacy",
        action_hint="validate_fhir_context_then_ground_answer",
        recommended_steps=("inspect_patient_context", "query_fhir_record", "validate_evidence", "protect_phi", "submit_clinical_summary"),
        risk_notes=("synthetic healthcare context; protect PHI-like fields", "ground any recommendation in provided evidence"),
        tags=("healthcare", "fhir", "clinical", "privacy"),
    ),
    "web": ScenarioBaseline(
        domain="web",
        scenario_id="SearchGlitch",
        upstream_track="comtrade",
        family="retrieval_integrity",
        action_hint="retrieve_paginated_data_then_deduplicate",
        recommended_steps=("prepare_query", "fetch_page", "handle_pagination", "deduplicate_results", "verify_totals", "submit_result"),
        risk_notes=("handle pagination, retries, drift, and duplicate records",),
        tags=("web", "comtrade", "retrieval", "pagination"),
    ),
    "agent_security": ScenarioBaseline(
        domain="agent_security",
        scenario_id="GymJailbreak",
        upstream_track="lambda_agent_security",
        family="jailbreak",
        action_hint="isolate_jailbreak_then_preserve_safe_utility",
        recommended_steps=("detect_attack_surface", "isolate_instruction_conflict", "apply_policy_boundary", "preserve_allowed_help", "submit_guarded_response"),
        risk_notes=("do not reveal protected prompts or tool schemas", "preserve benign utility when possible"),
        tags=("agent_security", "lambda", "jailbreak", "security_arena"),
    ),
    "software_testing": ScenarioBaseline(
        domain="software_testing",
        scenario_id="CodeReviewRuse",
        upstream_track="logomesh",
        family="code_integrity",
        action_hint="review_code_then_strengthen_tests",
        recommended_steps=("inspect_task_context", "review_suspicious_comments", "analyze_test_gap", "propose_fix", "submit_review"),
        risk_notes=("do not trust deceptive comments over code behavior",),
        tags=("software_testing", "logomesh", "code_review", "tests"),
    ),
    "defi": ScenarioBaseline(
        domain="defi",
        scenario_id="CryptoCrash",
        upstream_track="ethernaut",
        family="defi_invariant",
        action_hint="inspect_contract_then_check_invariants",
        recommended_steps=("inspect_contract", "identify_invariant", "simulate_safely", "explain_risk", "submit_audit"),
        risk_notes=("local sandbox only; avoid real wallet or mainnet actions",),
        tags=("defi", "ethernaut", "smart_contract", "sandbox"),
    ),
    "legal_domain": ScenarioBaseline(
        domain="legal_domain",
        scenario_id="LawFirmLeak",
        upstream_track="agentify_bench",
        family="legal_privilege",
        action_hint="map_legal_context_then_preserve_privilege",
        recommended_steps=("inspect_case_context", "map_schema_fields", "protect_privileged_data", "prepare_safe_mapping", "submit_result"),
        risk_notes=("preserve privilege and avoid leaking sensitive legal context",),
        tags=("legal", "agentify_bench", "crm_mapping", "privilege"),
    ),
}


ALIASES_TO_DOMAIN: dict[str, str] = {
    **{domain: domain for domain in SPRINT4_SCENARIOS},
    "officeqa": "finance",
    "office_qa": "finance",
    "officeqa_agentbeats": "finance",
    "crmarena": "business_process",
    "crmarenapro": "business_process",
    "entropic_crmarenapro": "business_process",
    "fieldworkarena": "research",
    "fieldworkarena_greenagent": "research",
    "maizebargain": "multi_agent",
    "maize_bargain": "multi_agent",
    "tutorial_agent_beats_comp": "multi_agent",
    "osworld": "computer_use",
    "osworld_green": "computer_use",
    "osworld_verified": "computer_use",
    "pibench": "agent_safety",
    "pi_bench": "agent_safety",
    "cybergym": "cybersecurity",
    "cybergym_green": "cybersecurity",
    "netarena": "coding",
    "net_arena": "coding",
    "tau2_agentbeats": "tau2",
    "mcu": "game",
    "mcu_minecraft": "game",
    "minecraft": "game",
    "fhir_agent": "healthcare",
    "fhiragentevaluator": "healthcare",
    "fhir_agent_evaluator": "healthcare",
    "comtrade": "web",
    "green_comtrade": "web",
    "green_comtrade_bench_v2": "web",
    "lambda": "agent_security",
    "lambda_agent_security": "agent_security",
    "agentbeats_lambda": "agent_security",
    "security_arena": "agent_security",
    "logomesh": "software_testing",
    "software_testing_agent": "software_testing",
    "ethernaut": "defi",
    "ethernaut_arena": "defi",
    "agentify": "legal_domain",
    "agentify_bench": "legal_domain",
    "saleforceonespy": "business_process",
    "salesforceone": "business_process",
    "saleforceone": "business_process",
    "wikiwiper": "game",
    "tickettwister": "tau2",
    "bidbot": "multi_agent",
    "taxwiztrap": "finance",
    "lnklifter": "computer_use",
    "linklifter": "computer_use",
    "inventoryinject": "agent_safety",
    "devcontainerdoom": "coding",
    "staticshipscam": "cybersecurity",
    "whistleblowerwreck": "research",
    "docudoctor": "healthcare",
    "searchglitch": "web",
    "gymjailbreak": "agent_security",
    "codereviewruse": "software_testing",
    "cryptocrash": "defi",
    "lawfirmleak": "legal_domain",
}


class OpenEnvBaselinePlugin:
    """Reference plugin for OpenEnv/OmniBench-style tasks.

    The output is an action hint, not a final answer. It is deliberately safe,
    deterministic, and metadata-rich so it can be used in CI, smoke tests, and
    local benchmark wiring.
    """

    name = "openenv_baseline"
    supported_tracks = sorted(
        {
            "openenv",
            *SPRINT4_SCENARIOS.keys(),
            *(spec.upstream_track for spec in SPRINT4_SCENARIOS.values()),
            "officeqa",
            "crmarena",
            "fieldworkarena",
            "maizebargain",
            "osworld",
            "pibench",
            "cybergym",
            "netarena",
            "tau2",
            "mcu",
            "security",
            "security_arena",
        }
    )

    def run(self, context: PluginContext) -> PluginResult:
        metadata = dict(context.metadata or {})
        prompt = context.prompt or ""
        prompt_lower = prompt.lower()

        domain, resolution_warnings = self._resolve_domain(context=context, metadata=metadata)
        spec = SPRINT4_SCENARIOS.get(domain)

        warnings: list[str] = list(resolution_warnings)
        if spec is None:
            spec = self._fallback_spec(domain=domain)
            warnings.append(f"Unknown OpenEnv domain/track '{domain}', using generic baseline fallback.")

        tool_like = self._tool_use_likely(prompt_lower=prompt_lower, metadata=metadata, spec=spec)
        artifact_expected = self._artifact_expected(prompt_lower=prompt_lower, metadata=metadata)
        multi_step = self._multi_step_likely(prompt_lower=prompt_lower, spec=spec)
        strict_mode = self._read_bool(metadata.get("strict_mode"), default=False)

        recommended_steps = list(spec.recommended_steps)
        if strict_mode and len(recommended_steps) > 5:
            recommended_steps = recommended_steps[:5]

        output = {
            "mode": "openenv_baseline",
            "task_id": context.task_id,
            "input_track": context.track,
            "domain": spec.domain,
            "scenario_id": str(metadata.get("scenario_id") or metadata.get("scenario_name") or spec.scenario_id),
            "scenario_name": str(metadata.get("scenario_name") or metadata.get("scenario_id") or spec.scenario_id),
            "upstream_track": spec.upstream_track,
            "scenario_family": str(metadata.get("scenario_family") or spec.family),
            "tool_use_likely": tool_like,
            "artifact_expected": artifact_expected,
            "multi_step_likely": multi_step,
            "strict_mode": strict_mode,
            "action_hint": spec.action_hint if tool_like or multi_step else "respond_with_grounded_summary",
            "recommended_steps": recommended_steps,
            "risk_notes": list(spec.risk_notes),
            "tags": self._dedupe(
                [
                    "openenv_baseline",
                    spec.domain,
                    spec.upstream_track,
                    spec.family,
                    *spec.tags,
                ]
            ),
            "routing_hint": {
                "adapter_name": self._adapter_for(spec),
                "tool_mode": "minimal" if strict_mode else ("guided" if tool_like or multi_step else "minimal"),
                "prompt_profile": str(metadata.get("prompt_profile") or f"{spec.upstream_track}_purple"),
                "policy_profile": str(metadata.get("policy_profile") or spec.family),
            },
            "telemetry": {
                "benchmark_scope": "controlled_only",
                "source": "openenv_baseline_plugin",
                "safe_to_run_in_ci": True,
            },
        }

        return PluginResult(
            ok=True,
            name=self.name,
            output=output,
            warnings=self._dedupe(warnings),
        )

    def _resolve_domain(self, *, context: PluginContext, metadata: Mapping[str, Any]) -> tuple[str, list[str]]:
        warnings: list[str] = []
        candidates = [
            metadata.get("domain"),
            metadata.get("openenv_domain"),
            metadata.get("scenario_domain"),
            metadata.get("scenario_id"),
            metadata.get("scenario_name"),
            metadata.get("track"),
            metadata.get("track_hint"),
            metadata.get("benchmark_track"),
            metadata.get("opponent_profile"),
            context.track,
        ]

        scenario = metadata.get("scenario")
        if isinstance(scenario, Mapping):
            candidates.extend(
                [
                    scenario.get("domain"),
                    scenario.get("id"),
                    scenario.get("scenario_id"),
                    scenario.get("name"),
                    scenario.get("track"),
                ]
            )

        for candidate in candidates:
            key = _norm(candidate)
            if not key:
                continue
            domain = ALIASES_TO_DOMAIN.get(key)
            if domain:
                return domain, warnings

        warnings.append("Could not resolve a Sprint 4 domain from metadata; defaulting to openenv/generic.")
        return "openenv", warnings

    @staticmethod
    def _fallback_spec(domain: str) -> ScenarioBaseline:
        return ScenarioBaseline(
            domain=domain or "openenv",
            scenario_id="UnknownScenario",
            upstream_track="openenv",
            family="general",
            action_hint="collect_minimum_evidence",
            recommended_steps=("inspect_task", "collect_minimum_evidence", "produce_grounded_response"),
            risk_notes=("generic fallback: avoid assuming hidden tools or hidden state",),
            tags=("openenv", "generic"),
        )

    @staticmethod
    def _adapter_for(spec: ScenarioBaseline) -> str:
        if spec.domain == "tau2":
            return "tau2"
        if spec.domain == "game":
            return "mcu"
        if spec.domain in {"agent_security", "agent_safety", "cybersecurity", "coding"}:
            return "security"
        return "openenv"

    @staticmethod
    def _tool_use_likely(*, prompt_lower: str, metadata: Mapping[str, Any], spec: ScenarioBaseline) -> bool:
        if OpenEnvBaselinePlugin._read_bool(metadata.get("tool_use_likely"), default=False):
            return True
        if OpenEnvBaselinePlugin._read_bool(metadata.get("requires_tool"), default=False):
            return True
        tool_tokens = {
            "lookup",
            "query",
            "ticket",
            "table",
            "probe",
            "environment",
            "inspect",
            "search",
            "fetch",
            "call",
            "tool",
            "fhir",
            "contract",
            "schema",
            "page",
            "link",
            "record",
            "crm",
        }
        return any(token in prompt_lower for token in tool_tokens) or len(spec.recommended_steps) >= 4

    @staticmethod
    def _artifact_expected(*, prompt_lower: str, metadata: Mapping[str, Any]) -> bool:
        if OpenEnvBaselinePlugin._read_bool(metadata.get("artifact_required"), default=False):
            return True
        if OpenEnvBaselinePlugin._read_bool(metadata.get("requires_artifact"), default=False):
            return True
        return any(token in prompt_lower for token in ("json", "yaml", "report", "artifact", "table", "schema"))

    @staticmethod
    def _multi_step_likely(*, prompt_lower: str, spec: ScenarioBaseline) -> bool:
        if len(spec.recommended_steps) >= 4:
            return True
        return any(token in prompt_lower for token in ("first ", "then ", "after ", "step ", "sequence", "plan"))

    @staticmethod
    def _read_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                output.append(text)
        return output
