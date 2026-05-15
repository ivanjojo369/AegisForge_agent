from __future__ import annotations

"""Generate a competitive run matrix from OpenEnv/OmniBench payloads.

Sprint 4 version for AegisForge / AgentX-AgentBeats Phase 2.

This script is intentionally payload-first and registry-aware:
- consumes curriculum payloads when available, or generated eval payloads as fallback;
- preserves the existing matrix contract used by downstream runners;
- expands competitive metadata for the 16 final Sprint 4 scenarios;
- keeps the richer multi_agent round-robin scheduler from Phase B;
- normalizes legacy scenario names such as SalesforceOne/LinkLifter to
  SaleForceOneSpy/LnkLifter without mutating the original nested payload object.
"""

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPT_ROOT = Path(__file__).resolve().parent
ENV_ROOT = SCRIPT_ROOT.parent
PARENT_ROOT = ENV_ROOT.parent
if str(PARENT_ROOT) not in sys.path:
    sys.path.insert(0, str(PARENT_ROOT))

DEFAULT_INPUT_CANDIDATES = [
    ENV_ROOT / "training" / "curriculum_payloads",
    ENV_ROOT / "training" / "generated_payloads",
    SCRIPT_ROOT / "generated_payloads",
]
DEFAULT_OUTPUT_DIR = ENV_ROOT / "training" / "variant_matrix"

CANONICAL_SCENARIO_BY_DOMAIN: dict[str, str] = {
    "business_process": "SaleForceOneSpy",
    "game": "WikiWiper",
    "tau2": "TicketTwister",
    "multi_agent": "BidBot",
    "finance": "TaxWizTrap",
    "computer_use": "LnkLifter",
    "agent_safety": "InventoryInject",
    "coding": "DevContainerDoom",
    "cybersecurity": "StaticShipScam",
    "research": "WhistleBlowerWreck",
    "healthcare": "DocuDoctor",
    "web": "SearchGlitch",
    "agent_security": "GymJailbreak",
    "software_testing": "CodeReviewRuse",
    "defi": "CryptoCrash",
    "legal_domain": "LawFirmLeak",
}

DOMAIN_ALIASES: dict[str, str] = {
    "business": "business_process",
    "businessprocess": "business_process",
    "business_process": "business_process",
    "crm": "business_process",
    "crmarena": "business_process",
    "officeqa": "finance",
    "game": "game",
    "mcu": "game",
    "minecraft": "game",
    "mcu_minecraft": "game",
    "tau": "tau2",
    "tau2": "tau2",
    "tau_bench": "tau2",
    "tau2_bench": "tau2",
    "t2": "tau2",
    "multiagent": "multi_agent",
    "multi_agent": "multi_agent",
    "maizebargain": "multi_agent",
    "maize_bargain": "multi_agent",
    "finance": "finance",
    "computer": "computer_use",
    "computer_use": "computer_use",
    "osworld": "computer_use",
    "osworld_verified": "computer_use",
    "agent_safety": "agent_safety",
    "pibench": "agent_safety",
    "pi_bench": "agent_safety",
    "coding": "coding",
    "swebench": "coding",
    "swe_bench": "coding",
    "terminalbench": "coding",
    "terminal_bench": "coding",
    "cyber": "cybersecurity",
    "security": "cybersecurity",
    "cybergym": "cybersecurity",
    "cybersecurity": "cybersecurity",
    "research": "research",
    "fieldwork": "research",
    "fieldworkarena": "research",
    "health": "healthcare",
    "medical": "healthcare",
    "healthcare": "healthcare",
    "web": "web",
    "browser": "web",
    "web_agent": "web",
    "agent_security": "agent_security",
    "lambda_security": "agent_security",
    "lambda_agent_security": "agent_security",
    "security_arena": "agent_security",
    "software_testing": "software_testing",
    "software_testing_agent": "software_testing",
    "testing": "software_testing",
    "defi": "defi",
    "crypto": "defi",
    "smart_contract": "defi",
    "smart_contracts": "defi",
    "legal": "legal_domain",
    "legal_agent": "legal_domain",
    "legal_domain": "legal_domain",
}

SCENARIO_ALIASES: dict[str, str] = {
    "salesforceone": "SaleForceOneSpy",
    "saleforceone": "SaleForceOneSpy",
    "salesforceonespy": "SaleForceOneSpy",
    "saleforceonespy": "SaleForceOneSpy",
    "wikiwiper": "WikiWiper",
    "tickettwister": "TicketTwister",
    "bidbot": "BidBot",
    "taxwiztrap": "TaxWizTrap",
    "linklifter": "LnkLifter",
    "lnklifter": "LnkLifter",
    "inventoryinject": "InventoryInject",
    "devcontainerdoom": "DevContainerDoom",
    "staticshipscam": "StaticShipScam",
    "whistleblowerwreck": "WhistleBlowerWreck",
    "whistleblowerwreck": "WhistleBlowerWreck",
    "docudoctor": "DocuDoctor",
    "searchglitch": "SearchGlitch",
    "gymjailbreak": "GymJailbreak",
    "codereviewruse": "CodeReviewRuse",
    "cryptocrash": "CryptoCrash",
    "lawfirmleak": "LawFirmLeak",
}

SCENARIO_TO_DOMAIN: dict[str, str] = {
    _scenario.lower(): _domain for _domain, _scenario in CANONICAL_SCENARIO_BY_DOMAIN.items()
}
SCENARIO_TO_DOMAIN.update({alias: SCENARIO_TO_DOMAIN.get(canonical.lower(), "") for alias, canonical in SCENARIO_ALIASES.items()})
SCENARIO_TO_DOMAIN = {k: v for k, v in SCENARIO_TO_DOMAIN.items() if v}

DOMAIN_METADATA: dict[str, dict[str, Any]] = {
    "multi_agent": {
        "family": "arena_competition",
        "mode": "league_match",
        "rank": 0,
        "stakes": "head_to_head_equilibrium",
        "category": "general_agent",
        "lanes": ("auction", "negotiation", "equilibrium", "adversarial_bid"),
    },
    "tau2": {
        "family": "interactive_dialogue",
        "mode": "bundle_match",
        "rank": 1,
        "stakes": "bundle_score_margin",
        "category": "general_agent",
        "lanes": ("cooperative", "ambiguous", "adversarial"),
    },
    "business_process": {
        "family": "policy_workflow",
        "mode": "policy_duel",
        "rank": 2,
        "stakes": "policy_correctness",
        "category": "business_process",
        "lanes": ("privacy", "support", "renewals", "compliance"),
    },
    "computer_use": {
        "family": "browser_ops",
        "mode": "speedrun",
        "rank": 3,
        "stakes": "navigation_speed_and_accuracy",
        "category": "computer_use",
        "lanes": ("steady", "quick", "surgical"),
    },
    "finance": {
        "family": "numerical_reasoning",
        "mode": "precision_trial",
        "rank": 4,
        "stakes": "numeric_precision",
        "category": "finance",
        "lanes": ("conservative", "balanced", "stress"),
    },
    "game": {
        "family": "embodied_strategy",
        "mode": "arena_run",
        "rank": 5,
        "stakes": "mission_completion",
        "category": "game",
        "lanes": ("tactical", "attrition", "speedclear"),
    },
    "research": {
        "family": "analysis",
        "mode": "evidence_trial",
        "rank": 6,
        "stakes": "source_grounding_and_privacy",
        "category": "research",
        "lanes": ("source_triage", "evidence_synthesis", "privacy_guarded"),
    },
    "web": {
        "family": "retrieval_ops",
        "mode": "retrieval_trial",
        "rank": 7,
        "stakes": "retrieval_accuracy_and_contract_adherence",
        "category": "web",
        "lanes": ("pagination", "dedupe", "retry", "contract"),
    },
    "coding": {
        "family": "software_engineering",
        "mode": "dev_env_trial",
        "rank": 8,
        "stakes": "safe_code_change_and_verification",
        "category": "coding",
        "lanes": ("diagnose", "patch", "test", "container"),
    },
    "software_testing": {
        "family": "software_quality",
        "mode": "review_integrity_trial",
        "rank": 9,
        "stakes": "test_integrity_and_deceptive_context_resistance",
        "category": "software_testing",
        "lanes": ("weak_tests", "deceptive_comments", "coverage", "integrity"),
    },
    "agent_safety": {
        "family": "safety_robustness",
        "mode": "safety_trial",
        "rank": 10,
        "stakes": "prompt_tool_context_robustness",
        "category": "agent_safety",
        "lanes": ("inventory", "context_injection", "quarantine", "safe_report"),
    },
    "agent_security": {
        "family": "agent_security",
        "mode": "jailbreak_resistance_trial",
        "rank": 11,
        "stakes": "unsafe_output_resistance",
        "category": "agent_security",
        "lanes": ("policy_probe", "prompt_boundary", "tool_boundary", "refusal_quality"),
    },
    "cybersecurity": {
        "family": "cyber_sandbox",
        "mode": "controlled_security_trial",
        "rank": 12,
        "stakes": "controlled_security_reasoning",
        "category": "cybersecurity",
        "lanes": ("static_analysis", "sandbox", "evidence", "safe_mitigation"),
    },
    "healthcare": {
        "family": "clinical_privacy",
        "mode": "clinical_review_trial",
        "rank": 13,
        "stakes": "evidence_grounding_and_privacy",
        "category": "healthcare",
        "lanes": ("intake", "fhir", "evidence", "privacy"),
    },
    "defi": {
        "family": "smart_contract_sandbox",
        "mode": "invariant_audit_trial",
        "rank": 14,
        "stakes": "wallet_and_invariant_safety",
        "category": "defi",
        "lanes": ("wallet", "invariant", "contract_audit", "risk_control"),
    },
    "legal_domain": {
        "family": "legal_privilege",
        "mode": "legal_mapping_trial",
        "rank": 15,
        "stakes": "privilege_schema_and_persistence_constraints",
        "category": "legal_domain",
        "lanes": ("privilege", "discovery", "schema", "persistence"),
    },
}

SPRINT4_BENCHMARK = "agentbeats_phase2_sprint4"
SPRINT4_SCENARIO_FAMILY = "agentbeats_sprint4"
SPRINT4_ASSESSMENT_MODE = "purple_benchmark"
NCP_CORE_NAME = "AegisForge NCP — Neuro-Cognitive Purple Core"

NCP_TRACE_CONTRACT = (
    "observe",
    "attend",
    "ground",
    "plan",
    "act",
    "verify",
    "scorecard",
)

NCP_COMPONENTS = (
    "attention",
    "working_memory",
    "episodic_trace",
    "state_task_theory",
    "hierarchical_planning",
    "uncertainty_tracking",
    "evidence_accounting",
    "metacognition",
    "adversarial_self_check",
    "tool_selection",
    "traceability",
)

SCORECARD_DIMENSIONS = (
    "leaderboard_performance",
    "generality",
    "cost_efficiency",
    "technical_quality",
    "innovation",
    "reproducibility",
    "fair_play",
)

FORBIDDEN_HARDCODING_KEYS = {
    "answer_key",
    "gold_answer",
    "gold_answers",
    "oracle",
    "oracle_answer",
    "solution",
    "solutions",
    "lookup_table",
    "task_lookup_table",
    "task_specific_lookup_table",
    "benchmark_answer_map",
    "hardcoded_answer",
    "hard_coded_answer",
}

UPSTREAM_TRACK_ALIASES = (
    "officeqa",
    "crmarena",
    "crmarenapro",
    "fieldworkarena",
    "maizebargain",
    "osworld",
    "pibench",
    "cybergym",
    "netarena",
    "tau2_agentbeats",
    "mcu_minecraft",
)

TRANSFER_DOMAINS = set(DOMAIN_METADATA)


def _build_static_sprint4_registry() -> dict[str, dict[str, Any]]:
    """Return the canonical Sprint 4 registry used even when payload coverage is partial."""

    registry: dict[str, dict[str, Any]] = {}
    for domain, scenario_id in CANONICAL_SCENARIO_BY_DOMAIN.items():
        meta = DOMAIN_METADATA.get(domain, {})
        registry[domain] = {
            "domain": domain,
            "scenario_id": scenario_id,
            "scenario_name": scenario_id,
            "adapter": domain,
            "category": meta.get("category", domain),
            "family": meta.get("family", "general"),
            "mode": meta.get("mode", "trial"),
            "lanes": list(meta.get("lanes", ("general",))),
            "rank": meta.get("rank", 99),
            "stakes": meta.get("stakes", "benchmark_score"),
            "assessment_mode": SPRINT4_ASSESSMENT_MODE,
            "scenario_family": SPRINT4_SCENARIO_FAMILY,
            "benchmark": SPRINT4_BENCHMARK,
            "selected_opponent": scenario_id,
            "source_url": "",
            "ncp_core": NCP_CORE_NAME,
            "ncp_components": list(NCP_COMPONENTS),
            "ncp_trace_contract": list(NCP_TRACE_CONTRACT),
            "scorecard_dimensions": list(SCORECARD_DIMENSIONS),
            "fair_play_contract": {
                "no_hardcoded_answers": True,
                "no_task_specific_lookup_tables": True,
                "controlled_benchmark_only": True,
                "preserve_upstream_track_aliases": list(UPSTREAM_TRACK_ALIASES),
            },
        }
    return registry


SPRINT4_DOMAIN_REGISTRY = _build_static_sprint4_registry()

LEVEL_STAGE: dict[str, str] = {
    "easy": "qualifier",
    "medium": "group_stage",
    "hard": "playoff",
    "heldout_like": "championship",
    "baseline": "open",
}
LEVEL_ORDER = {"easy": 0, "medium": 1, "hard": 2, "heldout_like": 3, "baseline": 4}
PRESSURE_PROFILES = ("stable", "compressed", "high_variance")
MULTI_AGENT_SIDES = ("blue", "red")
RIVALRY_TIERS = ("routine", "hot", "heated", "marquee")


class VariantMatrixError(RuntimeError):
    """Raised when variant matrix generation cannot proceed."""


def _slugify(text: Any) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    return value or "item"


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _normalize_only(values: Sequence[str] | None) -> set[str]:
    return {_slugify(value) for value in (values or []) if str(value).strip()}


def _normalize_domain_name(value: Any) -> str:
    raw = _slugify(value)
    return DOMAIN_ALIASES.get(raw, raw)


def _canonicalize_scenario_id(value: Any, *, domain: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw and domain:
        return CANONICAL_SCENARIO_BY_DOMAIN.get(domain, "UnknownScenario")
    slug = _slugify(raw)
    if slug in SCENARIO_ALIASES:
        return SCENARIO_ALIASES[slug]
    return raw or (CANONICAL_SCENARIO_BY_DOMAIN.get(domain or "", "UnknownScenario"))


def _resolve_input_dir(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).resolve()
        if not path.exists():
            raise VariantMatrixError(f"input directory does not exist: {path}")
        return path
    for candidate in DEFAULT_INPUT_CANDIDATES:
        if candidate.exists():
            return candidate
    tried = ", ".join(str(path) for path in DEFAULT_INPUT_CANDIDATES)
    raise VariantMatrixError(f"could not locate payload directory; tried: {tried}")


def _load_payload_list(input_dir: Path) -> tuple[list[dict[str, Any]], str]:
    candidates = [
        (input_dir / "all_openenv_curriculum_payloads.json", "curriculum"),
        (input_dir / "all_openenv_eval_payloads.json", "generated"),
        (input_dir / "all_client_curriculum_bundles.json", "client_curriculum"),
        (input_dir / "all_client_bundles.json", "client_generated"),
    ]
    for candidate, kind in candidates:
        if candidate.exists():
            payload = _load_json(candidate)
            if not isinstance(payload, list):
                raise VariantMatrixError(f"payload file must contain a JSON list: {candidate}")
            return [dict(item) for item in payload if isinstance(item, Mapping)], candidate.name
    raise VariantMatrixError("could not find a supported aggregate payload file in the input directory")


def _try_registry_lookup(name: str) -> dict[str, Any] | None:
    try:
        from omnibench_aegis_env.domains.registry import get_domain_spec, resolve_domain_name  # type: ignore
    except Exception:
        return None
    try:
        key = resolve_domain_name(name)
        spec = get_domain_spec(key)
    except Exception:
        return None
    output = {
        "key": getattr(spec, "key", key),
        "scenario_id": getattr(spec, "scenario_id", ""),
        "scenario_name": getattr(spec, "scenario_name", ""),
        "track_label": getattr(spec, "track_label", ""),
        "category": getattr(spec, "category", ""),
        "source_url": getattr(spec, "source_url", ""),
    }
    return {k: v for k, v in output.items() if v}


def _stable_bucket(*parts: Any, modulo: int) -> int:
    if modulo <= 0:
        return 0
    raw = "::".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % modulo


def _stable_token(*parts: Any, length: int = 12) -> str:
    raw = "::".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _jsonable(value: Any) -> Any:
    """Return a deterministic JSON-safe representation for hashing/reporting."""

    if isinstance(value, Mapping):
        return {str(key): _jsonable(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _payload_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(_jsonable(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scan_for_forbidden_hardcoding(value: Any, *, path: str = "payload", limit: int = 32) -> list[dict[str, str]]:
    """Find obvious answer-key/lookup-table signals without interpreting task content."""

    hits: list[dict[str, str]] = []
    if limit <= 0:
        return hits
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            key_slug = _slugify(key_text)
            child_path = f"{path}.{key_text}"
            if key_slug in FORBIDDEN_HARDCODING_KEYS:
                hits.append({"path": child_path, "key": key_text, "reason": "forbidden_hardcoding_key"})
            hits.extend(_scan_for_forbidden_hardcoding(item, path=child_path, limit=limit - len(hits)))
            if len(hits) >= limit:
                break
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_scan_for_forbidden_hardcoding(item, path=f"{path}[{index}]", limit=limit - len(hits)))
            if len(hits) >= limit:
                break
    return hits[:limit]


def _sprint4_profile(domain: str, scenario_id: str, registry_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve canonical Sprint 4/NCP metadata for a row."""

    normalized_domain = _normalize_domain_name(domain)
    canonical_scenario = _canonicalize_scenario_id(scenario_id, domain=normalized_domain)
    if normalized_domain not in SPRINT4_DOMAIN_REGISTRY:
        normalized_domain = SCENARIO_TO_DOMAIN.get(_slugify(canonical_scenario), normalized_domain)
    profile = dict(SPRINT4_DOMAIN_REGISTRY.get(normalized_domain, {}))
    if not profile:
        profile = {
            "domain": normalized_domain,
            "scenario_id": canonical_scenario,
            "scenario_name": canonical_scenario,
            "adapter": normalized_domain,
            "category": normalized_domain,
            "family": "general",
            "mode": "trial",
            "lanes": ["general"],
            "rank": 99,
            "stakes": "benchmark_score",
            "assessment_mode": SPRINT4_ASSESSMENT_MODE,
            "scenario_family": "local_or_upstream",
            "benchmark": SPRINT4_BENCHMARK,
            "selected_opponent": canonical_scenario,
            "source_url": "",
            "ncp_core": NCP_CORE_NAME,
            "ncp_components": list(NCP_COMPONENTS),
            "ncp_trace_contract": list(NCP_TRACE_CONTRACT),
            "scorecard_dimensions": list(SCORECARD_DIMENSIONS),
        }
    profile["scenario_id"] = canonical_scenario or profile.get("scenario_id")
    profile["scenario_name"] = canonical_scenario or profile.get("scenario_name")
    profile["selected_opponent"] = canonical_scenario or profile.get("selected_opponent")
    registry_meta = dict(registry_meta or {})
    for key in ("source_url", "track_label", "category"):
        if registry_meta.get(key):
            profile[key] = registry_meta[key]
    return profile


def _build_ncp_trace(
    *,
    run_id: str,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    seed: int,
    repeat_index: int,
    payload: Mapping[str, Any],
    competitive_context: Mapping[str, Any],
    registry_meta: Mapping[str, Any],
    hardcoding_hits: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a compact, auditable NCP trace contract; not a chain-of-thought dump."""

    profile = _sprint4_profile(domain, scenario_id, registry_meta)
    tools = payload.get("tools") or payload.get("required_tools") or (payload.get("task") or {}).get("required_tools") if isinstance(payload.get("task"), Mapping) else payload.get("tools") or payload.get("required_tools")
    if not isinstance(tools, list):
        tools = []
    reset_payload = payload.get("reset_payload") if isinstance(payload.get("reset_payload"), Mapping) else {}
    evidence_fields = sorted(
        key
        for key in (
            "domain",
            "scenario_id",
            "curriculum_level",
            "fixture",
            "variant_key",
            "mission_id",
            "task",
            "reset_payload",
        )
        if key in payload
    )
    return {
        "core": NCP_CORE_NAME,
        "claim_boundary": "computational_trace_only_no_claim_of_consciousness_or_human_subjectivity",
        "components": list(NCP_COMPONENTS),
        "trace_contract": list(NCP_TRACE_CONTRACT),
        "observe": {
            "domain": domain,
            "scenario_id": scenario_id,
            "level": level,
            "split": split,
            "payload_fingerprint": _payload_fingerprint(payload)[:16],
            "evidence_fields": evidence_fields,
        },
        "attend": {
            "lane": competitive_context.get("lane"),
            "pressure_profile": competitive_context.get("pressure_profile"),
            "stakes": competitive_context.get("stakes"),
            "priority_signals": [
                "benchmark_contract",
                "task_goal",
                "tool_availability",
                "safety_boundary",
                "evaluation_criteria",
            ],
        },
        "ground": {
            "assessment_mode": profile.get("assessment_mode"),
            "scenario_family": profile.get("scenario_family"),
            "benchmark": profile.get("benchmark"),
            "adapter": profile.get("adapter"),
            "source_url": profile.get("source_url", ""),
            "upstream_aliases_preserved": list(UPSTREAM_TRACK_ALIASES),
        },
        "plan": {
            "planner": "hierarchical_receding_horizon_with_uncertainty_checks",
            "state_task_theory": {
                "domain_family": profile.get("family"),
                "mode": profile.get("mode"),
                "stage": competitive_context.get("stage"),
                "seed": seed,
                "repeat_index": repeat_index,
            },
            "candidate_policy": [
                "parse_task_contract",
                "select_minimal_tools",
                "execute_controlled_steps",
                "verify_against_success_criteria",
                "log_scorecard_evidence",
            ],
        },
        "act": {
            "tool_selection_basis": "declared_tools_plus_domain_lane",
            "declared_tool_count": len(tools),
            "runtime_seed": reset_payload.get("seed", seed),
            "controlled_scope": bool(competitive_context.get("controlled_scope") or competitive_context.get("sandbox_only")),
        },
        "verify": {
            "adversarial_self_check": True,
            "hardcoding_signal_count": len(hardcoding_hits),
            "fair_play_required": True,
            "reproducibility_required": True,
            "scorecard_dimensions": list(SCORECARD_DIMENSIONS),
        },
    }


def _build_scorecard(
    *,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    competitive_context: Mapping[str, Any],
    hardcoding_hits: list[dict[str, str]],
) -> dict[str, Any]:
    """Attach leaderboard-facing scorecard intent to every row."""

    stage = str(competitive_context.get("stage") or LEVEL_STAGE.get(level, "open"))
    lane = str(competitive_context.get("lane") or "general")
    return {
        "leaderboard_performance": {
            "target": "maximize_task_success_under_controlled_budget",
            "evidence": ["success_signal", "verification_signal", "retry_count", "latency_ms"],
            "stage": stage,
            "lane": lane,
        },
        "generality": {
            "target": "transfer_across_all_16_sprint4_domains_without_lookup_tables",
            "evidence": ["domain", "scenario_id", "family", "category", "upstream_aliases_preserved"],
        },
        "cost_efficiency": {
            "target": "prefer_minimal_tool_calls_and_short_receding_horizon_plans",
            "evidence": ["declared_tool_count", "repeat_index", "seed", "pressure_profile"],
        },
        "technical_quality": {
            "target": "preserve_schema_contracts_runtime_metadata_and_traceability",
            "evidence": ["run_id", "telemetry.trace_id", "scheduling.priority_score", "payload_fingerprint"],
        },
        "innovation": {
            "target": "use_ncp_trace_as_auditable_cognitive_control_layer",
            "evidence": list(NCP_TRACE_CONTRACT),
        },
        "reproducibility": {
            "target": "deterministic_matrix_generation_from_seed_payload_and_registry",
            "evidence": ["seed", "repeat_index", "stable_hash_ids", "aggregate_payload_file"],
        },
        "fair_play": {
            "target": "no_hardcoded_answers_no_task_specific_lookup_tables_no_oracle_leakage",
            "evidence": ["hardcoding_signal_count", "policy_contract"],
            "status": "requires_review" if hardcoding_hits else "clean",
        },
    }


def _build_policy_contract(hardcoding_hits: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "controlled_benchmark_only": True,
        "no_real_world_exploitation": True,
        "no_secret_extraction": True,
        "no_persistence": True,
        "no_evasion": True,
        "no_hardcoded_answers": not hardcoding_hits,
        "no_task_specific_lookup_tables": not hardcoding_hits,
        "hardcoding_signal_count": len(hardcoding_hits),
        "fair_play_review_required": bool(hardcoding_hits),
    }


def _summarize_scorecards(matrix: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dimensions = Counter()
    fair_play_status = Counter()
    for row in matrix:
        scorecard = row.get("scorecard") or {}
        for dimension in scorecard:
            dimensions[str(dimension)] += 1
        fair_play = scorecard.get("fair_play") if isinstance(scorecard, Mapping) else {}
        if isinstance(fair_play, Mapping):
            fair_play_status[str(fair_play.get("status", "unknown"))] += 1
    return {
        "ncp_core": NCP_CORE_NAME,
        "trace_contract": list(NCP_TRACE_CONTRACT),
        "dimensions": list(SCORECARD_DIMENSIONS),
        "rows_with_scorecards": min(dimensions.values()) if dimensions else 0,
        "dimension_coverage": dict(sorted(dimensions.items())),
        "fair_play_status": dict(sorted(fair_play_status.items())),
    }


def _registry_export() -> list[dict[str, Any]]:
    return [dict(SPRINT4_DOMAIN_REGISTRY[domain]) for domain in sorted(SPRINT4_DOMAIN_REGISTRY)]


def _payload_domain_scenario(payload: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    raw_domain = payload.get("domain") or (payload.get("reset_payload") or {}).get("options", {}).get("domain") or ""
    raw_scenario = payload.get("scenario_id") or (payload.get("reset_payload") or {}).get("scenario_id") or ""

    domain = _normalize_domain_name(raw_domain)
    scenario_id = _canonicalize_scenario_id(raw_scenario, domain=domain if domain in CANONICAL_SCENARIO_BY_DOMAIN else None)

    if domain not in CANONICAL_SCENARIO_BY_DOMAIN:
        scenario_slug = _slugify(scenario_id)
        domain = SCENARIO_TO_DOMAIN.get(scenario_slug, domain or "general")
        scenario_id = _canonicalize_scenario_id(scenario_id, domain=domain)

    registry_meta = _try_registry_lookup(domain) or _try_registry_lookup(scenario_id) or {}
    if registry_meta.get("key"):
        domain = _normalize_domain_name(registry_meta["key"])
    if registry_meta.get("scenario_name"):
        scenario_id = _canonicalize_scenario_id(registry_meta["scenario_name"], domain=domain)
    elif registry_meta.get("scenario_id"):
        scenario_id = _canonicalize_scenario_id(registry_meta["scenario_id"], domain=domain)

    return domain, scenario_id, registry_meta


def _infer_split(input_dir: Path, output_dir: Path, payloads: Sequence[Mapping[str, Any]]) -> str:
    joined = f"{input_dir} {output_dir}".lower()
    if "transfer" in joined:
        return "transfer"
    levels = {
        str(payload.get("curriculum_level") or (payload.get("reset_payload") or {}).get("options", {}).get("difficulty") or "")
        for payload in payloads
    }
    domains = {_payload_domain_scenario(payload)[0] for payload in payloads}
    if levels == {"heldout_like"} and domains and domains.issubset(TRANSFER_DOMAINS):
        return "transfer"
    return "curriculum" if any(level in LEVEL_ORDER for level in levels) else "generated"


def _domain_meta(domain: str, registry_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    meta = dict(DOMAIN_METADATA.get(domain, {}))
    registry_meta = dict(registry_meta or {})
    for key in ("category", "source_url", "track_label"):
        if registry_meta.get(key):
            meta[key] = registry_meta[key]
    meta.setdefault("family", "general")
    meta.setdefault("mode", "trial")
    meta.setdefault("rank", 99)
    meta.setdefault("stakes", "benchmark_score")
    meta.setdefault("category", domain)
    meta.setdefault("lanes", ("general",))
    return meta


def _infer_family(domain: str, scenario_id: str, registry_meta: Mapping[str, Any] | None = None) -> str:
    meta = _domain_meta(domain, registry_meta)
    if meta.get("family"):
        return str(meta["family"])
    slug = _slugify(scenario_id)
    if "ticket" in slug or "dialog" in slug:
        return "interactive_dialogue"
    if "agent" in slug or "bot" in slug:
        return "arena_competition"
    return "general"


def _build_telemetry_tags(
    *,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    family: str,
    repeat_index: int,
    seed: int,
    payload: Mapping[str, Any],
    registry_meta: Mapping[str, Any],
) -> list[str]:
    meta = _domain_meta(domain, registry_meta)
    tags = [
        f"split:{split}",
        f"domain:{domain}",
        f"scenario:{_slugify(scenario_id)}",
        f"level:{level}",
        f"family:{family}",
        f"category:{_slugify(meta.get('category'))}",
        f"repeat:r{repeat_index:02d}",
        f"seed:{seed}",
        f"mode:{meta.get('mode', 'trial')}",
    ]
    fixture = str(payload.get("fixture") or "").strip()
    if fixture:
        tags.append(f"fixture:{_slugify(fixture)}")
    env_id = str((payload.get("reset_payload") or {}).get("options", {}).get("env_id") or payload.get("canonical_env_id") or "").strip()
    if env_id:
        tags.append(f"env:{_slugify(env_id)}")
    track_label = str(meta.get("track_label") or payload.get("track_label") or "").strip()
    if track_label:
        tags.append(f"track:{_slugify(track_label)}")
    if bool(payload.get("curriculum_realigned")):
        tags.append("realigned:true")
    return list(dict.fromkeys(tags))


def _lane_for(domain: str, scenario_id: str, seed: int, repeat_index: int, level: str, registry_meta: Mapping[str, Any]) -> str:
    lanes = tuple(_domain_meta(domain, registry_meta).get("lanes") or ("general",))
    return str(lanes[_stable_bucket(domain, scenario_id, seed, repeat_index, level, modulo=len(lanes))])


def _build_competitive_context(
    *,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    family: str,
    seed: int,
    repeat_index: int,
    payload: Mapping[str, Any],
    registry_meta: Mapping[str, Any],
) -> dict[str, Any]:
    meta = _domain_meta(domain, registry_meta)
    profile = _sprint4_profile(domain, scenario_id, registry_meta)
    stage = LEVEL_STAGE.get(level, "open")
    lane = _lane_for(domain, scenario_id, seed, repeat_index, level, registry_meta)
    context: dict[str, Any] = {
        "mode": str(meta.get("mode") or profile.get("mode") or "trial"),
        "stage": stage,
        "family": family,
        "category": str(meta.get("category") or profile.get("category") or domain),
        "split": split,
        "lane": lane,
        "heat": _stable_bucket(domain, scenario_id, level, repeat_index, modulo=4) + 1,
        "series": f"{_slugify(domain)}__{_slugify(scenario_id)}__{_slugify(level)}",
        "pressure_profile": PRESSURE_PROFILES[_stable_bucket(domain, scenario_id, repeat_index, modulo=len(PRESSURE_PROFILES))],
        "stakes": str(meta.get("stakes") or profile.get("stakes") or "benchmark_score"),
        "adapter": profile.get("adapter", domain),
        "assessment_mode": profile.get("assessment_mode", SPRINT4_ASSESSMENT_MODE),
        "scenario_family": profile.get("scenario_family", SPRINT4_SCENARIO_FAMILY),
        "benchmark": profile.get("benchmark", SPRINT4_BENCHMARK),
        "selected_opponent": profile.get("selected_opponent", scenario_id),
        "sprint4_registered": domain in SPRINT4_DOMAIN_REGISTRY,
        "ncp_core": NCP_CORE_NAME,
        "ncp_trace_contract": list(NCP_TRACE_CONTRACT),
    }

    if registry_meta.get("source_url"):
        context["source_url"] = registry_meta["source_url"]
    if registry_meta.get("track_label"):
        context["track_label"] = registry_meta["track_label"]

    if domain == "multi_agent":
        division = chr(ord("A") + _stable_bucket(scenario_id, level, modulo=4))
        seat_index = _stable_bucket(seed, repeat_index, scenario_id, modulo=len(MULTI_AGENT_SIDES))
        context.update(
            {
                "division": f"arena-{division}",
                "round": _stable_bucket(level, repeat_index, seed, modulo=6) + 1,
                "side": MULTI_AGENT_SIDES[seat_index],
                "opponent_profile": f"opponent-{_stable_bucket(scenario_id, seed, repeat_index, modulo=8) + 1:02d}",
                "ladder_points_on_entry": 90 + _stable_bucket(seed, scenario_id, modulo=31),
            }
        )
    elif domain == "tau2":
        context.update({"user_archetype": lane, "bundle_lane": f"lane-{_stable_bucket(seed, level, repeat_index, modulo=5) + 1}"})
    elif domain == "business_process":
        context.update({"workflow_lane": lane, "policy_surface": "privacy_and_routing"})
    elif domain == "computer_use":
        context.update({"execution_lane": lane})
    elif domain == "finance":
        context.update({"risk_band": lane})
    elif domain == "game":
        context.update({"arena_type": lane})
    elif domain in {"agent_security", "agent_safety", "cybersecurity"}:
        context.update({"security_lane": lane, "controlled_scope": True})
    elif domain == "software_testing":
        context.update({"quality_lane": lane})
    elif domain == "coding":
        context.update({"engineering_lane": lane})
    elif domain == "web":
        context.update({"retrieval_lane": lane})
    elif domain == "healthcare":
        context.update({"clinical_lane": lane, "privacy_sensitive": True})
    elif domain == "defi":
        context.update({"audit_lane": lane, "sandbox_only": True})
    elif domain == "legal_domain":
        context.update({"legal_lane": lane, "privilege_sensitive": True})
    elif domain == "research":
        context.update({"analysis_lane": lane})

    mission_id = str(payload.get("mission_id") or (payload.get("reset_payload") or {}).get("mission_id") or "").strip()
    if mission_id:
        context["mission_id"] = mission_id
    return context


def _build_telemetry(
    *,
    run_id: str,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    family: str,
    repeat_index: int,
    seed: int,
    payload: Mapping[str, Any],
    competitive_context: Mapping[str, Any],
    registry_meta: Mapping[str, Any],
) -> dict[str, Any]:
    trace_namespace = f"sprint4.{split}.{_slugify(domain)}.{_slugify(scenario_id)}"
    profile = _sprint4_profile(domain, scenario_id, registry_meta)
    return {
        "trace_namespace": trace_namespace,
        "trace_id": _stable_token(run_id, seed, repeat_index, length=16),
        "span_group": f"{_slugify(domain)}__{_slugify(level)}__{competitive_context.get('stage', 'open')}",
        "experiment_group": f"{split}__{_slugify(domain)}__{_slugify(scenario_id)}__{_slugify(level)}",
        "benchmark_track": family,
        "benchmark": profile.get("benchmark", SPRINT4_BENCHMARK),
        "assessment_mode": profile.get("assessment_mode", SPRINT4_ASSESSMENT_MODE),
        "scenario_family": profile.get("scenario_family", SPRINT4_SCENARIO_FAMILY),
        "ncp_core": NCP_CORE_NAME,
        "ncp_trace_contract": list(NCP_TRACE_CONTRACT),
        "payload_fingerprint": _payload_fingerprint(payload),
        "tags": _build_telemetry_tags(
            domain=domain,
            scenario_id=scenario_id,
            level=level,
            split=split,
            family=family,
            repeat_index=repeat_index,
            seed=seed,
            payload=payload,
            registry_meta=registry_meta,
        ),
        "lineage": {
            "split": split,
            "domain": domain,
            "scenario_id": scenario_id,
            "curriculum_level": level,
            "repeat_index": repeat_index,
            "seed": seed,
            "fixture": payload.get("fixture"),
            "variant_key": payload.get("variant_key"),
            "category": competitive_context.get("category"),
            "source_url": competitive_context.get("source_url"),
            "adapter": profile.get("adapter", domain),
            "selected_opponent": profile.get("selected_opponent", scenario_id),
        },
    }


def _build_scheduling(
    *,
    domain: str,
    scenario_id: str,
    level: str,
    split: str,
    repeat_index: int,
    seed: int,
    competitive_context: Mapping[str, Any],
    registry_meta: Mapping[str, Any],
) -> dict[str, Any]:
    stage = str(competitive_context.get("stage") or LEVEL_STAGE.get(level, "open"))
    domain_rank = int(_domain_meta(domain, registry_meta).get("rank", 99))
    stage_rank = {"qualifier": 0, "group_stage": 1, "playoff": 2, "championship": 3, "league_stage": 4, "open": 8}.get(stage, 9)
    urgency = _stable_bucket(domain, scenario_id, level, split, seed, modulo=100)
    return {
        "queue_tier": stage,
        "domain_rank": domain_rank,
        "stage_rank": stage_rank,
        "priority_score": (domain_rank * 1000) + (stage_rank * 100) + urgency,
        "batch_key": f"{split}__{_slugify(domain)}__{stage}",
        "shard": _stable_bucket(domain, scenario_id, repeat_index, modulo=4) + 1,
    }


def _build_base_row(
    *,
    index: int,
    payload: Mapping[str, Any],
    split: str,
    repeat_index: int,
) -> dict[str, Any]:
    domain, scenario_id, registry_meta = _payload_domain_scenario(payload)
    level = str(payload.get("curriculum_level") or payload.get("reset_payload", {}).get("options", {}).get("difficulty") or "baseline")
    reset_payload = dict(payload.get("reset_payload") or {})
    seed = int(reset_payload.get("seed") or payload.get("seed") or 42)
    family = _infer_family(domain, scenario_id, registry_meta)
    run_id = f"{index:03d}__{_slugify(domain)}__{_slugify(scenario_id)}__{_slugify(level)}__r{repeat_index:02d}"
    competitive_context = _build_competitive_context(
        domain=domain,
        scenario_id=scenario_id,
        level=level,
        split=split,
        family=family,
        seed=seed,
        repeat_index=repeat_index,
        payload=payload,
        registry_meta=registry_meta,
    )
    telemetry = _build_telemetry(
        run_id=run_id,
        domain=domain,
        scenario_id=scenario_id,
        level=level,
        split=split,
        family=family,
        repeat_index=repeat_index,
        seed=seed,
        payload=payload,
        competitive_context=competitive_context,
        registry_meta=registry_meta,
    )
    scheduling = _build_scheduling(
        domain=domain,
        scenario_id=scenario_id,
        level=level,
        split=split,
        repeat_index=repeat_index,
        seed=seed,
        competitive_context=competitive_context,
        registry_meta=registry_meta,
    )
    hardcoding_hits = _scan_for_forbidden_hardcoding(payload)
    profile = _sprint4_profile(domain, scenario_id, registry_meta)
    ncp_trace = _build_ncp_trace(
        run_id=run_id,
        domain=domain,
        scenario_id=scenario_id,
        level=level,
        split=split,
        seed=seed,
        repeat_index=repeat_index,
        payload=payload,
        competitive_context=competitive_context,
        registry_meta=registry_meta,
        hardcoding_hits=hardcoding_hits,
    )
    scorecard = _build_scorecard(
        domain=domain,
        scenario_id=scenario_id,
        level=level,
        split=split,
        competitive_context=competitive_context,
        hardcoding_hits=hardcoding_hits,
    )
    policy_contract = _build_policy_contract(hardcoding_hits)
    return {
        "run_id": run_id,
        "domain": domain,
        "scenario_id": scenario_id,
        "scenario_name": scenario_id,
        "adapter": profile.get("adapter", domain),
        "assessment_mode": profile.get("assessment_mode", SPRINT4_ASSESSMENT_MODE),
        "scenario_family": profile.get("scenario_family", SPRINT4_SCENARIO_FAMILY),
        "benchmark": profile.get("benchmark", SPRINT4_BENCHMARK),
        "selected_opponent": profile.get("selected_opponent", scenario_id),
        "sprint4_registered": domain in SPRINT4_DOMAIN_REGISTRY,
        "curriculum_level": level,
        "split": split,
        "family": family,
        "category": competitive_context.get("category"),
        "seed": seed,
        "repeat_index": repeat_index,
        "base_url": payload.get("base_url") or payload.get("environment_url"),
        "experiment_group": telemetry["experiment_group"],
        "competitive_context": competitive_context,
        "telemetry": telemetry,
        "ncp": ncp_trace,
        "scorecard": scorecard,
        "policy_contract": policy_contract,
        "payload_integrity": {
            "fingerprint": _payload_fingerprint(payload),
            "hardcoding_signal_count": len(hardcoding_hits),
            "hardcoding_signals": hardcoding_hits,
            "source_payload_preserved": True,
        },
        "scheduling": scheduling,
        "payload": dict(payload),
    }


def _snake_partition(participants: list[dict[str, Any]], pool_size: int) -> list[list[dict[str, Any]]]:
    if not participants:
        return []
    pool_count = max(1, math.ceil(len(participants) / max(2, pool_size)))
    pools: list[list[dict[str, Any]]] = [[] for _ in range(pool_count)]
    direction = 1
    pool_index = 0
    for participant in participants:
        pools[pool_index].append(participant)
        if pool_count == 1:
            continue
        next_index = pool_index + direction
        if next_index >= pool_count:
            direction = -1
            pool_index = pool_count - 1
        elif next_index < 0:
            direction = 1
            pool_index = 0
        else:
            pool_index = next_index
    return [pool for pool in pools if pool]


def _round_robin_pairs(participants: list[dict[str, Any]]) -> list[list[tuple[dict[str, Any], dict[str, Any]]]]:
    if len(participants) < 2:
        return []
    slots: list[dict[str, Any] | None] = list(participants)
    if len(slots) % 2 == 1:
        slots.append(None)
    rounds: list[list[tuple[dict[str, Any], dict[str, Any]]]] = []
    for round_index in range(len(slots) - 1):
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        half = len(slots) // 2
        for i in range(half):
            left = slots[i]
            right = slots[-(i + 1)]
            if left is None or right is None:
                continue
            pairs.append((left, right) if round_index % 2 == 0 else (right, left))
        rounds.append(pairs)
        fixed = slots[0]
        rest = slots[1:]
        rest = [rest[-1], *rest[:-1]]
        slots = [fixed, *rest]
    return rounds


def _build_league_row(
    *,
    participant: Mapping[str, Any],
    opponent: Mapping[str, Any],
    league_id: str,
    pool_id: str,
    round_no: int,
    fixture_no: int,
    leg_no: int,
    seat: str,
    home_away: str,
    pool_size: int,
    pool_slot: int,
    group_key: str,
) -> dict[str, Any]:
    row = deepcopy(dict(participant))
    opponent_run_id = str(opponent["run_id"])
    new_run_id = (
        f"{participant['run_id']}__rr__{_slugify(pool_id)}__rd{round_no:02d}"
        f"__fx{fixture_no:02d}__leg{leg_no:02d}__vs__{_slugify(opponent_run_id)}"
    )
    rivalry_score = _stable_bucket(participant["run_id"], opponent_run_id, round_no, leg_no, modulo=100)
    rivalry_tier = RIVALRY_TIERS[min(len(RIVALRY_TIERS) - 1, rivalry_score // 25)]

    row["run_id"] = new_run_id
    row["experiment_group"] = f"{participant['experiment_group']}__round_robin__{_slugify(pool_id)}"

    cc = dict(row.get("competitive_context") or {})
    cc.update(
        {
            "mode": "round_robin_league",
            "stage": "league_stage",
            "league_id": league_id,
            "pool_id": pool_id,
            "group_key": group_key,
            "pool_size": pool_size,
            "pool_slot": pool_slot,
            "round_no": round_no,
            "fixture_no": fixture_no,
            "leg_no": leg_no,
            "seat": seat,
            "home_away": home_away,
            "opponent_run_id": opponent_run_id,
            "opponent_seed": opponent["seed"],
            "opponent_level": opponent["curriculum_level"],
            "opponent_repeat_index": opponent["repeat_index"],
            "opponent_profile": f"league_opp_{_stable_token(opponent_run_id, length=8)}",
            "rivalry_tier": rivalry_tier,
            "rivalry_score": rivalry_score,
            "table_points_on_entry": 6 + _stable_bucket(participant["seed"], opponent["seed"], modulo=10),
            "schedule_kind": "mini_league_round_robin",
        }
    )
    row["competitive_context"] = cc

    ncp = deepcopy(dict(row.get("ncp") or {}))
    ncp["league_context"] = {
        "schedule_kind": "mini_league_round_robin",
        "league_id": league_id,
        "pool_id": pool_id,
        "round_no": round_no,
        "fixture_no": fixture_no,
        "seat": seat,
        "opponent_run_id": opponent_run_id,
        "metacognitive_check": "adapt_strategy_without_memorizing_opponent_answers",
    }
    row["ncp"] = ncp

    telemetry = deepcopy(dict(row.get("telemetry") or {}))
    telemetry["trace_id"] = _stable_token(new_run_id, participant["seed"], round_no, fixture_no, leg_no, length=16)
    telemetry["span_group"] = f"multi_agent__round_robin__rd{round_no:02d}"
    telemetry["experiment_group"] = row["experiment_group"]
    tags = list(telemetry.get("tags") or [])
    tags.extend(
        [
            "mode:round_robin_league",
            f"league:{_slugify(league_id)}",
            f"pool:{_slugify(pool_id)}",
            f"round:{round_no}",
            f"fixture:{fixture_no}",
            f"leg:{leg_no}",
            f"seat:{seat}",
            f"opponent_level:{opponent['curriculum_level']}",
            f"home_away:{home_away}",
            f"rivalry:{rivalry_tier}",
        ]
    )
    telemetry["tags"] = list(dict.fromkeys(tags))
    lineage = dict(telemetry.get("lineage") or {})
    lineage.update(
        {
            "league_id": league_id,
            "pool_id": pool_id,
            "round_no": round_no,
            "fixture_no": fixture_no,
            "leg_no": leg_no,
            "seat": seat,
            "opponent_run_id": opponent_run_id,
            "group_key": group_key,
        }
    )
    telemetry["lineage"] = lineage
    row["telemetry"] = telemetry

    scheduling = deepcopy(dict(row.get("scheduling") or {}))
    scheduling.update(
        {
            "queue_tier": "league_stage",
            "stage_rank": 4,
            "batch_key": f"{participant['split']}__multi_agent__league__rd{round_no:02d}",
            "league_id": league_id,
            "pool_id": pool_id,
            "round_no": round_no,
            "fixture_no": fixture_no,
            "leg_no": leg_no,
            "match_shard": _stable_bucket(league_id, pool_id, round_no, fixture_no, modulo=4) + 1,
        }
    )
    scheduling["priority_score"] = min(int(scheduling.get("priority_score", 999999)), 250 + round_no * 10 + fixture_no)
    row["scheduling"] = scheduling

    row["league"] = {
        "league_id": league_id,
        "pool_id": pool_id,
        "group_key": group_key,
        "round_no": round_no,
        "fixture_no": fixture_no,
        "leg_no": leg_no,
        "seat": seat,
        "home_away": home_away,
        "pool_size": pool_size,
        "pool_slot": pool_slot,
        "participant_run_id": participant["run_id"],
        "opponent_run_id": opponent_run_id,
    }
    return row


def _expand_multi_agent_round_robin(
    participants: list[dict[str, Any]],
    *,
    pool_size: int,
    double_round_robin: bool,
    mix_levels: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not participants:
        return [], []

    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in participants:
        key = (row["split"], row["scenario_id"]) if mix_levels else (row["split"], row["scenario_id"], row["curriculum_level"])
        grouped.setdefault(key, []).append(row)

    expanded_rows: list[dict[str, Any]] = []
    league_index: list[dict[str, Any]] = []

    for key, group_rows in sorted(grouped.items()):
        split = key[0]
        scenario_id = key[1]
        key_slug = "__".join(_slugify(part) for part in key)
        ordered = sorted(
            group_rows,
            key=lambda row: (
                LEVEL_ORDER.get(str(row["curriculum_level"]), 99),
                int(row["seed"]),
                int(row["repeat_index"]),
                str(row["run_id"]),
            ),
        )
        pools = _snake_partition(ordered, pool_size)
        for pool_number, pool in enumerate(pools, start=1):
            league_id = f"{split}__{_slugify(scenario_id)}__league_{pool_number:02d}"
            pool_id = f"{league_id}__pool"
            rounds = _round_robin_pairs(pool)
            league_index.append(
                {
                    "league_id": league_id,
                    "pool_id": pool_id,
                    "group_key": key_slug,
                    "scenario_id": scenario_id,
                    "split": split,
                    "pool_size": len(pool),
                    "participants": [row["run_id"] for row in pool],
                    "rounds": len(rounds) * (2 if double_round_robin else 1),
                    "double_round_robin": double_round_robin,
                }
            )
            fixture_counter = 0
            legs = (1, 2) if double_round_robin else (1,)
            for leg_no in legs:
                leg_rounds = rounds if leg_no == 1 else [[(away, home) for home, away in pairings] for pairings in rounds]
                round_offset = (leg_no - 1) * len(rounds)
                for local_round_no, pairings in enumerate(leg_rounds, start=1):
                    round_no = round_offset + local_round_no
                    for home, away in pairings:
                        fixture_counter += 1
                        try:
                            home_slot = pool.index(home) + 1
                            away_slot = pool.index(away) + 1
                        except ValueError:
                            home_slot = 1
                            away_slot = 2
                        expanded_rows.append(
                            _build_league_row(
                                participant=home,
                                opponent=away,
                                league_id=league_id,
                                pool_id=pool_id,
                                round_no=round_no,
                                fixture_no=fixture_counter,
                                leg_no=leg_no,
                                seat="blue",
                                home_away="home",
                                pool_size=len(pool),
                                pool_slot=home_slot,
                                group_key=key_slug,
                            )
                        )
                        expanded_rows.append(
                            _build_league_row(
                                participant=away,
                                opponent=home,
                                league_id=league_id,
                                pool_id=pool_id,
                                round_no=round_no,
                                fixture_no=fixture_counter,
                                leg_no=leg_no,
                                seat="red",
                                home_away="away",
                                pool_size=len(pool),
                                pool_slot=away_slot,
                                group_key=key_slug,
                            )
                        )
    return expanded_rows, league_index


def generate_variant_matrix(
    *,
    input_dir: Path,
    output_dir: Path,
    repeats: int,
    only: Sequence[str] | None = None,
    multi_agent_pool_size: int = 4,
    multi_agent_double_round_robin: bool = False,
    multi_agent_mix_levels: bool = True,
    require_complete_sprint4: bool = False,
    fail_on_hardcoding_signals: bool = False,
) -> dict[str, Any]:
    if repeats < 1:
        raise VariantMatrixError("repeats must be >= 1")
    if multi_agent_pool_size < 2:
        raise VariantMatrixError("multi_agent_pool_size must be >= 2")

    payloads, aggregate_file = _load_payload_list(input_dir)
    only_set = _normalize_only(only)
    split = _infer_split(input_dir, output_dir, payloads)

    matrix: list[dict[str, Any]] = []
    multi_agent_participants: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for index, payload in enumerate(payloads, start=1):
        domain, scenario_id, _ = _payload_domain_scenario(payload)
        level = str(payload.get("curriculum_level") or payload.get("reset_payload", {}).get("options", {}).get("difficulty") or "baseline")
        tokens = {_slugify(domain), _slugify(scenario_id), _slugify(level)}
        if only_set and not (tokens & only_set):
            continue
        if domain == "general":
            skipped.append({"scenario_id": scenario_id, "reason": "unresolved_domain"})
        for repeat_index in range(1, repeats + 1):
            row = _build_base_row(index=index, payload=payload, split=split, repeat_index=repeat_index)
            if row["domain"] == "multi_agent":
                multi_agent_participants.append(row)
            else:
                matrix.append(row)

    league_index: list[dict[str, Any]] = []
    league_rows: list[dict[str, Any]] = []
    if multi_agent_participants:
        league_rows, league_index = _expand_multi_agent_round_robin(
            multi_agent_participants,
            pool_size=multi_agent_pool_size,
            double_round_robin=multi_agent_double_round_robin,
            mix_levels=multi_agent_mix_levels,
        )
        if league_rows:
            matrix.extend(league_rows)
        else:
            matrix.extend(multi_agent_participants)

    if not matrix:
        raise VariantMatrixError("no payloads matched the requested filters")

    domain_counter: Counter[str] = Counter()
    scenario_counter: Counter[str] = Counter()
    level_counter: Counter[str] = Counter()
    family_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    mode_counter: Counter[str] = Counter()
    stage_counter: Counter[str] = Counter()
    league_counter: Counter[str] = Counter()

    matrix.sort(key=lambda row: (int(row["scheduling"]["priority_score"]), str(row["run_id"])))
    for order_index, row in enumerate(matrix, start=1):
        row["schedule_index"] = order_index
        domain_counter[str(row["domain"])] += 1
        scenario_counter[str(row["scenario_id"])] += 1
        level_counter[str(row["curriculum_level"])] += 1
        family_counter[str(row["family"])] += 1
        category_counter[str(row.get("category") or "uncategorized")] += 1
        mode_counter[str((row.get("competitive_context") or {}).get("mode") or "trial")] += 1
        stage_counter[str((row.get("competitive_context") or {}).get("stage") or "open")] += 1
        if "league" in row:
            league_counter[str(row["league"]["league_id"])] += 1

    missing_sprint4_domains = sorted(set(CANONICAL_SCENARIO_BY_DOMAIN) - set(domain_counter))
    hardcoding_warnings = [
        {
            "run_id": str(row.get("run_id")),
            "domain": str(row.get("domain")),
            "scenario_id": str(row.get("scenario_id")),
            "signals": row.get("payload_integrity", {}).get("hardcoding_signals", []),
        }
        for row in matrix
        if row.get("payload_integrity", {}).get("hardcoding_signals")
    ]

    if require_complete_sprint4 and missing_sprint4_domains:
        raise VariantMatrixError(
            "Sprint 4 matrix coverage incomplete; missing domains: "
            + ", ".join(missing_sprint4_domains)
        )
    if fail_on_hardcoding_signals and hardcoding_warnings:
        raise VariantMatrixError(
            "Hardcoding/lookup-table signals found in payloads: "
            + ", ".join(str(item["run_id"]) for item in hardcoding_warnings[:10])
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_name = "variant_matrix.json"
    summary_name = "variant_matrix_summary.json"
    league_name = "league_index.json"
    registry_name = "sprint4_registry.json"
    scorecard_name = "ncp_scorecard_summary.json"
    scorecard_summary = _summarize_scorecards(matrix)
    registry_export = _registry_export()
    summary = {
        "ok": True,
        "input_dir": str(input_dir),
        "input_aggregate_file": aggregate_file,
        "output_dir": str(output_dir),
        "count": len(matrix),
        "repeats": repeats,
        "split": split,
        "domains": sorted(domain_counter),
        "scenarios": sorted(scenario_counter),
        "levels": sorted(level_counter),
        "families": sorted(family_counter),
        "categories": sorted(category_counter),
        "competition_modes": dict(sorted(mode_counter.items())),
        "stages": dict(sorted(stage_counter.items())),
        "rows_by_domain": dict(sorted(domain_counter.items())),
        "rows_by_scenario": dict(sorted(scenario_counter.items())),
        "rows_by_level": dict(sorted(level_counter.items())),
        "rows_by_family": dict(sorted(family_counter.items())),
        "rows_by_category": dict(sorted(category_counter.items())),
        "telemetry_enabled": True,
        "competitive_metadata_enabled": True,
        "ncp_core_enabled": True,
        "ncp_core": NCP_CORE_NAME,
        "ncp_components": list(NCP_COMPONENTS),
        "ncp_trace_contract": list(NCP_TRACE_CONTRACT),
        "scorecard_dimensions": list(SCORECARD_DIMENSIONS),
        "scorecard_summary": scorecard_summary,
        "sprint4_domain_count_expected": 16,
        "sprint4_domain_count_seen": len(domain_counter),
        "sprint4_registry_count": len(registry_export),
        "sprint4_scenarios_expected": CANONICAL_SCENARIO_BY_DOMAIN,
        "sprint4_registry": {
            item["domain"]: {
                "scenario_id": item["scenario_id"],
                "adapter": item["adapter"],
                "category": item["category"],
                "family": item["family"],
                "mode": item["mode"],
            }
            for item in registry_export
        },
        "missing_sprint4_domains": missing_sprint4_domains,
        "require_complete_sprint4": require_complete_sprint4,
        "hardcoding_signal_count": sum(len(item["signals"]) for item in hardcoding_warnings),
        "hardcoding_warning_count": len(hardcoding_warnings),
        "fail_on_hardcoding_signals": fail_on_hardcoding_signals,
        "upstream_track_aliases_preserved": list(UPSTREAM_TRACK_ALIASES),
        "policy_contract": {
            "assessment_mode": SPRINT4_ASSESSMENT_MODE,
            "scenario_family": SPRINT4_SCENARIO_FAMILY,
            "benchmark": SPRINT4_BENCHMARK,
            "controlled_benchmark_only": True,
            "no_hardcoded_answers": not hardcoding_warnings,
            "no_task_specific_lookup_tables": not hardcoding_warnings,
            "fair_play_review_required": bool(hardcoding_warnings),
        },
        "multi_agent_round_robin_enabled": bool(league_index),
        "multi_agent_pool_size": multi_agent_pool_size,
        "multi_agent_double_round_robin": multi_agent_double_round_robin,
        "multi_agent_mix_levels": multi_agent_mix_levels,
        "league_count": len(league_index),
        "league_rows": sum(league_counter.values()),
        "league_row_distribution": dict(sorted(league_counter.items())),
        "warnings": skipped,
        "hardcoding_warnings": hardcoding_warnings,
        "files": [matrix_name, summary_name, league_name, registry_name, scorecard_name],
    }
    _dump_json(output_dir / matrix_name, matrix)
    _dump_json(output_dir / summary_name, summary)
    _dump_json(output_dir / league_name, league_index)
    _dump_json(output_dir / registry_name, registry_export)
    _dump_json(output_dir / scorecard_name, scorecard_summary)
    return {
        "ok": True,
        "input_dir": str(input_dir),
        "input_aggregate_file": aggregate_file,
        "output_dir": str(output_dir),
        "count": len(matrix),
        "repeats": repeats,
        "split": split,
        "domain_count_seen": len(domain_counter),
        "missing_sprint4_domains": summary["missing_sprint4_domains"],
        "ncp_core_enabled": True,
        "hardcoding_warning_count": len(hardcoding_warnings),
        "files": [matrix_name, summary_name, league_name, registry_name, scorecard_name],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a competitive Sprint 4 run matrix from curriculum or generated payloads.")
    parser.add_argument("--input-dir", help="Directory containing aggregate curriculum or OpenEnv eval payloads")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory where the variant matrix will be written")
    parser.add_argument("--repeats", type=int, default=1, help="How many repeats to schedule per payload entry")
    parser.add_argument("--only", nargs="*", help="Restrict to one or more domains, scenario IDs, or levels")
    parser.add_argument("--multi-agent-pool-size", type=int, default=4, help="Pool size for multi_agent mini leagues")
    parser.add_argument("--multi-agent-double-round-robin", action="store_true", help="Schedule home-and-away legs for each mini league")
    parser.add_argument("--no-multi-agent-mix-levels", action="store_true", help="Keep each curriculum level in separate mini leagues")
    parser.add_argument("--require-complete-sprint4", action="store_true", help="Fail if the observed payload set does not cover all 16 Sprint 4 domains")
    parser.add_argument("--fail-on-hardcoding-signals", action="store_true", help="Fail when payloads contain obvious answer-key or lookup-table fields")
    parser.add_argument("--list-sprint4-registry", action="store_true", help="Print the canonical Sprint 4 registry and exit")
    parser.add_argument("--json", action="store_true", help="Print a JSON summary")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list_sprint4_registry:
        payload = {"ok": True, "sprint4_domain_count": len(SPRINT4_DOMAIN_REGISTRY), "registry": _registry_export()}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    try:
        report = generate_variant_matrix(
            input_dir=_resolve_input_dir(args.input_dir),
            output_dir=Path(args.output_dir).resolve(),
            repeats=args.repeats,
            only=args.only,
            multi_agent_pool_size=args.multi_agent_pool_size,
            multi_agent_double_round_robin=args.multi_agent_double_round_robin,
            multi_agent_mix_levels=not args.no_multi_agent_mix_levels,
            require_complete_sprint4=args.require_complete_sprint4,
            fail_on_hardcoding_signals=args.fail_on_hardcoding_signals,
        )
    except VariantMatrixError as exc:
        report = {"ok": False, "error": str(exc), "type": "contract_error"}
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("[ok] competitive Sprint 4 variant matrix generated")
        print(f"- input_dir: {report['input_dir']}")
        print(f"- input_aggregate_file: {report['input_aggregate_file']}")
        print(f"- output_dir: {report['output_dir']}")
        print(f"- count: {report['count']}")
        print(f"- repeats: {report['repeats']}")
        print(f"- split: {report['split']}")
        print(f"- domain_count_seen: {report['domain_count_seen']}")
        print(f"- ncp_core_enabled: {report.get('ncp_core_enabled', False)}")
        print(f"- hardcoding_warning_count: {report.get('hardcoding_warning_count', 0)}")
        if report["missing_sprint4_domains"]:
            print(f"- missing_sprint4_domains: {', '.join(report['missing_sprint4_domains'])}")
        for name in report["files"]:
            print(f"- {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
