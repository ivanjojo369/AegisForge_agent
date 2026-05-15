from __future__ import annotations

"""NCP-aware τ²-style track evaluation for AegisForge.

This module is intentionally local and lightweight. It does not claim score
comparability with the upstream τ² benchmark. Its job is to validate that a
τ²-shaped payload has enough structure to survive AegisForge routing, adapter
selection, trace capture, and Purple benchmark review.

AegisForge NCP -- Neuro-Cognitive Purple Core alignment:
- observe: read payload/task/domain/turns/tools without relying on hidden state;
- attend: focus on scenario, adapter, task, context, constraints, and evidence;
- ground: normalize identifiers while preserving upstream and local names;
- plan: verify that the task is actionable and multi-turn;
- act: return a deterministic TrackResult suitable for smoke tests and CI;
- verify: expose missing fields, scorecard values, and fair-play flags.

The file is deliberately deterministic. It should never contain hardcoded task
answers, benchmark-specific lookup tables, exploit payloads, secret extraction
logic, persistence, evasion, or real-world targeting. It only checks structural
readiness for authorized benchmark payloads.
"""

from collections.abc import Mapping
from typing import Any

from ..schemas import TrackResult

DESCRIPTION = (
    "Performs NCP-aware lightweight structural validation for τ²-style payloads "
    "inside the AegisForge evaluation stack, checking domain framing, task "
    "shape, multi-turn context, declared tools, constraints, evidence, "
    "traceability, and fair-play metadata."
)

ASSESSMENT_MODE = "purple_benchmark"
SPRINT4_SCENARIO_FAMILY = "agentbeats_sprint4"
SPRINT4_REGISTRY_VERSION = "2026-05-ncp-tau2-v2"

TAU2_ADAPTER_ALIASES = {
    "tau2",
    "tau",
    "tau_bench",
    "tau2_bench",
    "tau2_agentbeats",
    "tickettwister",
    "ticket_twister",
    "ticketing",
}

UPSTREAM_COMPATIBILITY_ALIASES = {
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
    "mcu",
    "mcu_minecraft",
}

TICKETTWISTER_SCENARIO_ID = "tickettwister"


SPRINT4_SCENARIO_REGISTRY: dict[str, dict[str, str]] = {
    "saleforceonespy": {
        "scenario_id": "SaleForceOneSpy",
        "scenario_name": "SaleForceOneSpy",
        "domain": "business_process",
        "category": "business_process",
        "adapter": "business_process",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "SaleForceOneSpy",
        "source_url": "",
    },
    "wikiwiper": {
        "scenario_id": "WikiWiper",
        "scenario_name": "WikiWiper",
        "domain": "game",
        "category": "game",
        "adapter": "game",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "WikiWiper",
        "source_url": "",
    },
    TICKETTWISTER_SCENARIO_ID: {
        "scenario_id": "TicketTwister",
        "scenario_name": "TicketTwister",
        "domain": "tau2",
        "category": "tau2",
        "adapter": "tau2",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "TicketTwister",
        "source_url": "",
    },
    "bidbot": {
        "scenario_id": "BidBot",
        "scenario_name": "BidBot",
        "domain": "multi_agent",
        "category": "multi_agent",
        "adapter": "multi_agent",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "BidBot",
        "source_url": "",
    },
    "taxwiztrap": {
        "scenario_id": "TaxWizTrap",
        "scenario_name": "TaxWizTrap",
        "domain": "finance",
        "category": "finance",
        "adapter": "finance",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "TaxWizTrap",
        "source_url": "",
    },
    "lnklifter": {
        "scenario_id": "LnkLifter",
        "scenario_name": "LnkLifter",
        "domain": "computer_use",
        "category": "computer_use",
        "adapter": "computer_use",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "LnkLifter",
        "source_url": "",
    },
    "inventoryinject": {
        "scenario_id": "InventoryInject",
        "scenario_name": "InventoryInject",
        "domain": "agent_safety",
        "category": "agent_safety",
        "adapter": "agent_safety",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "InventoryInject",
        "source_url": "",
    },
    "devcontainerdoom": {
        "scenario_id": "DevContainerDoom",
        "scenario_name": "DevContainerDoom",
        "domain": "coding",
        "category": "coding",
        "adapter": "coding",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "DevContainerDoom",
        "source_url": "",
    },
    "staticshipscam": {
        "scenario_id": "StaticShipScam",
        "scenario_name": "StaticShipScam",
        "domain": "cybersecurity",
        "category": "cybersecurity",
        "adapter": "cybersecurity",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "StaticShipScam",
        "source_url": "",
    },
    "whistleblowerwreck": {
        "scenario_id": "WhistleBlowerWreck",
        "scenario_name": "WhistleBlowerWreck",
        "domain": "research",
        "category": "research",
        "adapter": "research",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "WhistleBlowerWreck",
        "source_url": "",
    },
    "docudoctor": {
        "scenario_id": "DocuDoctor",
        "scenario_name": "DocuDoctor",
        "domain": "healthcare",
        "category": "healthcare",
        "adapter": "healthcare",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "DocuDoctor",
        "source_url": "",
    },
    "searchglitch": {
        "scenario_id": "SearchGlitch",
        "scenario_name": "SearchGlitch",
        "domain": "web",
        "category": "web",
        "adapter": "web",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "SearchGlitch",
        "source_url": "",
    },
    "gymjailbreak": {
        "scenario_id": "GymJailbreak",
        "scenario_name": "GymJailbreak",
        "domain": "agent_security",
        "category": "agent_security",
        "adapter": "agent_security",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "GymJailbreak",
        "source_url": "",
    },
    "codereviewruse": {
        "scenario_id": "CodeReviewRuse",
        "scenario_name": "CodeReviewRuse",
        "domain": "software_testing",
        "category": "software_testing",
        "adapter": "software_testing",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "CodeReviewRuse",
        "source_url": "",
    },
    "cryptocrash": {
        "scenario_id": "CryptoCrash",
        "scenario_name": "CryptoCrash",
        "domain": "defi",
        "category": "defi",
        "adapter": "defi",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "CryptoCrash",
        "source_url": "",
    },
    "lawfirmleak": {
        "scenario_id": "LawFirmLeak",
        "scenario_name": "LawFirmLeak",
        "domain": "legal_domain",
        "category": "legal_domain",
        "adapter": "legal_domain",
        "benchmark": "agentbeats_phase2_sprint4",
        "selected_opponent": "LawFirmLeak",
        "source_url": "",
    },
}


SUSPICIOUS_LOOKUP_KEYS = {
    "answer",
    "answer_key",
    "answers",
    "gold",
    "gold_answer",
    "golden",
    "hardcoded_answer",
    "lookup",
    "lookup_table",
    "oracle",
    "secret",
    "solution",
}


def _normalize_identifier(value: Any) -> str:
    """Normalize identifiers while preserving enough signal for alias matching."""

    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _compact_identifier(value: Any) -> str:
    """Return a compact scenario key for Sprint 4 registry lookups."""

    return _normalize_identifier(value).replace("_", "")


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _metadata_from(payload: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}

    payload_metadata = payload.get("metadata")
    if isinstance(payload_metadata, Mapping):
        metadata.update(dict(payload_metadata))

    task_metadata = task.get("metadata")
    if isinstance(task_metadata, Mapping):
        metadata.update(dict(task_metadata))

    scenario = payload.get("scenario")
    if isinstance(scenario, Mapping):
        scenario_metadata = scenario.get("metadata")
        if isinstance(scenario_metadata, Mapping):
            metadata.update(dict(scenario_metadata))

    return metadata


def _extract_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized task mapping from nested or flat payloads.

    τ²-style fixtures may arrive as a nested ``task`` object, a flatter request
    shape, or an adapter-transformed payload. This helper keeps the validator
    tolerant without assuming benchmark answers.
    """

    task = payload.get("task")
    if isinstance(task, Mapping):
        return dict(task)

    direct_task_keys = {
        "task_id",
        "title",
        "user_goal",
        "goal",
        "objective",
        "conversation_context",
        "turns",
        "messages",
        "required_tools",
        "tools",
        "success_criteria",
        "constraints",
        "metadata",
    }
    if any(key in payload for key in direct_task_keys):
        return {
            "task_id": payload.get("task_id"),
            "title": payload.get("title"),
            "user_goal": payload.get("user_goal")
            or payload.get("goal")
            or payload.get("objective"),
            "conversation_context": payload.get("conversation_context")
            or payload.get("turns")
            or payload.get("messages")
            or [],
            "required_tools": payload.get("required_tools")
            or payload.get("tools")
            or [],
            "success_criteria": payload.get("success_criteria", []),
            "constraints": payload.get("constraints", []),
            "metadata": payload.get("metadata", {}),
        }

    return {}


def _extract_scenario_id(payload: dict[str, Any], task: dict[str, Any]) -> str | None:
    metadata = _metadata_from(payload, task)

    scenario = payload.get("scenario")
    scenario_id = None
    if isinstance(scenario, Mapping):
        scenario_id = (
            scenario.get("scenario_id")
            or scenario.get("scenario_name")
            or scenario.get("id")
            or scenario.get("slug")
            or scenario.get("name")
        )
    elif scenario is not None:
        scenario_id = scenario

    value = (
        payload.get("scenario_id")
        or payload.get("scenario_name")
        or payload.get("scenario_slug")
        or scenario_id
        or metadata.get("scenario_id")
        or metadata.get("scenario_name")
        or metadata.get("scenario")
        or metadata.get("slug")
    )
    if value is None or isinstance(value, Mapping):
        return None

    normalized = _compact_identifier(value)
    return normalized or None


def _extract_domain(payload: dict[str, Any], task: dict[str, Any], scenario_id: str | None) -> str | None:
    """Resolve the domain name from payload-level, task-level, or registry metadata."""

    metadata = _metadata_from(payload, task)
    scenario_profile = SPRINT4_SCENARIO_REGISTRY.get(scenario_id or "")

    domain = (
        payload.get("domain")
        or payload.get("domain_name")
        or payload.get("category")
        or metadata.get("domain")
        or metadata.get("domain_name")
        or metadata.get("category")
        or (scenario_profile or {}).get("domain")
        or payload.get("track")
    )
    if domain is None:
        return None
    return str(domain)


def _extract_turns(payload: dict[str, Any], task: dict[str, Any]) -> list[Any]:
    """Return multi-turn context from explicit turns, messages, or task context."""

    for key in ("turns", "messages", "conversation", "conversation_context"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return value

    for key in ("conversation_context", "turns", "messages", "history"):
        value = task.get(key)
        if isinstance(value, list) and value:
            return value

    return []


def _extract_tools(payload: dict[str, Any], task: dict[str, Any]) -> list[Any]:
    """Return declared tools from payload or task definition."""

    for key in ("tools", "tool_specs", "available_tools", "required_tools"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return value

    for key in ("required_tools", "tools", "tool_specs", "available_tools"):
        value = task.get(key)
        if isinstance(value, list) and value:
            return value

    return []


def _extract_list_field(
    payload: dict[str, Any],
    task: dict[str, Any],
    metadata: dict[str, Any],
    *keys: str,
) -> list[Any]:
    for source in (task, payload, metadata):
        for key in keys:
            value = source.get(key)
            if isinstance(value, list):
                return value
            if value not in (None, ""):
                return [value]
    return []


def _contains_suspicious_lookup(value: Any, path: str = "") -> list[str]:
    """Find fields that look like answer keys rather than structural metadata.

    This is a fair-play signal only. It does not inspect hidden benchmark state
    or attempt to infer a task answer.
    """

    findings: list[str] = []

    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            key_norm = _normalize_identifier(key_text)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_norm in SUSPICIOUS_LOOKUP_KEYS:
                findings.append(child_path)
            findings.extend(_contains_suspicious_lookup(nested, child_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            findings.extend(_contains_suspicious_lookup(nested, f"{path}[{index}]"))

    return findings


def _scenario_profile_for(scenario_id: str | None) -> dict[str, str] | None:
    if not scenario_id:
        return None
    return SPRINT4_SCENARIO_REGISTRY.get(scenario_id)


def _build_ncp_trace(
    *,
    adapter: str,
    adapter_ok: bool,
    scenario_id: str | None,
    scenario_profile: dict[str, str] | None,
    domain: str | None,
    task: dict[str, Any],
    turns: list[Any],
    tools: list[Any],
    success_criteria: list[Any],
    constraints: list[Any],
    missing: list[str],
    suspicious_lookup_paths: list[str],
) -> dict[str, Any]:
    """Build a compact, deterministic NCP trace for local evaluator output."""

    return {
        "observe": {
            "adapter": adapter,
            "scenario_id": scenario_id,
            "domain": domain,
            "task_present": bool(task),
            "turn_count": len(turns),
            "tool_count": len(tools),
        },
        "attend": {
            "focus": [
                "adapter_alias",
                "scenario_identity",
                "domain_frame",
                "task_goal",
                "multi_turn_context",
                "tool_expectations",
                "constraints",
                "fair_play",
            ],
            "ticket_twister_focus": scenario_id == TICKETTWISTER_SCENARIO_ID,
        },
        "ground": {
            "adapter_ok": adapter_ok,
            "assessment_mode": ASSESSMENT_MODE,
            "scenario_family": SPRINT4_SCENARIO_FAMILY,
            "registry_version": SPRINT4_REGISTRY_VERSION,
            "registry_count": len(SPRINT4_SCENARIO_REGISTRY),
            "scenario_profile": scenario_profile,
        },
        "plan": {
            "local_validation_goal": (
                "verify structural readiness for authorized τ²/Purple benchmark "
                "evaluation without hardcoded answers"
            ),
            "required_fields": [
                "domain",
                "task",
                "user_goal",
                "turns_or_conversation_context",
            ],
            "missing": missing,
        },
        "act": {
            "decision_rule": "deterministic structural TrackResult",
            "score_cap": 0.95,
        },
        "verify": {
            "fair_play_lookup_fields": suspicious_lookup_paths,
            "fair_play_ok": not suspicious_lookup_paths,
            "ready": adapter_ok and not missing and not suspicious_lookup_paths,
        },
    }


def _build_scorecard(
    *,
    adapter_ok: bool,
    scenario_id: str | None,
    task: dict[str, Any],
    turns: list[Any],
    tools: list[Any],
    success_criteria: list[Any],
    constraints: list[Any],
    evidence: list[Any],
    suspicious_lookup_paths: list[str],
) -> dict[str, float]:
    """Return leaderboard-facing diagnostic scores without replacing benchmark scoring."""

    has_goal = bool(task.get("user_goal") or task.get("goal") or task.get("objective"))
    multi_turn = len(turns) >= 2
    has_ticket_twister_identity = scenario_id == TICKETTWISTER_SCENARIO_ID

    return {
        "leaderboard_performance_readiness": round(
            0.35
            + (0.15 if adapter_ok else 0.0)
            + (0.15 if has_goal else 0.0)
            + (0.15 if multi_turn else 0.0)
            + (0.10 if tools else 0.0)
            + (0.10 if success_criteria else 0.0),
            3,
        ),
        "generality": round(
            0.50
            + (0.15 if adapter_ok else 0.0)
            + (0.10 if tools else 0.0)
            + (0.10 if constraints else 0.0)
            + (0.15 if has_ticket_twister_identity else 0.0),
            3,
        ),
        "cost_efficiency": 0.90,
        "technical_quality": round(
            0.55
            + (0.10 if adapter_ok else 0.0)
            + (0.10 if task else 0.0)
            + (0.10 if multi_turn else 0.0)
            + (0.10 if success_criteria else 0.0)
            + (0.05 if evidence else 0.0),
            3,
        ),
        "innovation": 0.86,
        "reproducibility": round(
            0.65
            + (0.10 if scenario_id else 0.0)
            + (0.10 if success_criteria else 0.0)
            + (0.10 if constraints else 0.0)
            + (0.05 if tools else 0.0),
            3,
        ),
        "fair_play": 0.50 if suspicious_lookup_paths else 1.0,
    }


def sprint4_scenarios() -> dict[str, dict[str, str]]:
    """Return a shallow copy of the canonical Sprint 4 scenario registry."""

    return {key: dict(value) for key, value in SPRINT4_SCENARIO_REGISTRY.items()}


def evaluate(payload: Mapping[str, Any] | None = None) -> TrackResult:
    """Evaluate whether a payload is structurally ready for local τ² validation.

    The evaluator checks structure, not correctness. It preserves upstream and
    local aliases, recognizes TicketTwister as the τ² Sprint 4 scenario, and
    emits NCP traces/scorecards that make CI failures easier to diagnose.
    """

    payload = dict(payload or {})
    adapter = str(payload.get("adapter", "tau2"))
    task = _extract_task(payload)
    scenario_id = _extract_scenario_id(payload, task)
    scenario_profile = _scenario_profile_for(scenario_id)
    domain = _extract_domain(payload, task, scenario_id)

    if not domain and scenario_id == TICKETTWISTER_SCENARIO_ID:
        domain = "ticketing"

    turns = _extract_turns(payload, task)
    tools = _extract_tools(payload, task)
    metadata = _metadata_from(payload, task)

    success_criteria = _extract_list_field(
        payload,
        task,
        metadata,
        "success_criteria",
        "evaluation_criteria",
        "acceptance_criteria",
    )
    constraints = _extract_list_field(
        payload,
        task,
        metadata,
        "constraints",
        "safety_constraints",
        "policy_constraints",
    )
    evidence = _extract_list_field(
        payload,
        task,
        metadata,
        "evidence",
        "observations",
        "supporting_evidence",
    )

    adapter_key = _normalize_identifier(adapter)
    track_key = _normalize_identifier(payload.get("track") or payload.get("track_hint"))
    domain_key = _normalize_identifier(domain)
    profile_adapter_key = _normalize_identifier((scenario_profile or {}).get("adapter"))

    adapter_ok = (
        adapter_key in TAU2_ADAPTER_ALIASES
        or track_key in TAU2_ADAPTER_ALIASES
        or domain_key in TAU2_ADAPTER_ALIASES
        or profile_adapter_key in TAU2_ADAPTER_ALIASES
        or scenario_id == TICKETTWISTER_SCENARIO_ID
    )

    upstream_compatibility_match = (
        adapter_key in UPSTREAM_COMPATIBILITY_ALIASES
        or track_key in UPSTREAM_COMPATIBILITY_ALIASES
        or domain_key in UPSTREAM_COMPATIBILITY_ALIASES
    )

    suspicious_lookup_paths = _contains_suspicious_lookup(payload)

    missing: list[str] = []
    if not domain:
        missing.append("domain")
    if not task:
        missing.append("task")
    if task and not (task.get("user_goal") or task.get("goal") or task.get("objective")):
        missing.append("user_goal")
    if not turns:
        missing.append("turns_or_conversation_context")

    ncp_trace = _build_ncp_trace(
        adapter=adapter,
        adapter_ok=adapter_ok,
        scenario_id=scenario_id,
        scenario_profile=scenario_profile,
        domain=domain,
        task=task,
        turns=turns,
        tools=tools,
        success_criteria=success_criteria,
        constraints=constraints,
        missing=missing,
        suspicious_lookup_paths=suspicious_lookup_paths,
    )

    scorecard = _build_scorecard(
        adapter_ok=adapter_ok,
        scenario_id=scenario_id,
        task=task,
        turns=turns,
        tools=tools,
        success_criteria=success_criteria,
        constraints=constraints,
        evidence=evidence,
        suspicious_lookup_paths=suspicious_lookup_paths,
    )

    details = {
        "description": DESCRIPTION,
        "adapter": adapter,
        "adapter_ok": adapter_ok,
        "upstream_compatibility_match": upstream_compatibility_match,
        "scenario_id": scenario_id,
        "scenario_name": (scenario_profile or {}).get("scenario_name"),
        "domain": domain,
        "category": (scenario_profile or {}).get("category") or domain,
        "assessment_mode": ASSESSMENT_MODE,
        "scenario_family": SPRINT4_SCENARIO_FAMILY,
        "benchmark": (scenario_profile or {}).get("benchmark")
        or metadata.get("benchmark")
        or "local_structural_validation",
        "selected_opponent": (scenario_profile or {}).get("selected_opponent")
        or metadata.get("selected_opponent"),
        "source_url": (scenario_profile or {}).get("source_url") or metadata.get("source_url"),
        "turn_count": len(turns),
        "tool_count": len(tools),
        "has_task": bool(task),
        "has_user_goal": bool(task.get("user_goal") or task.get("goal") or task.get("objective")),
        "success_criteria_count": len(success_criteria),
        "constraint_count": len(constraints),
        "evidence_count": len(evidence),
        "sprint4_registry_count": len(SPRINT4_SCENARIO_REGISTRY),
        "sprint4_registry_version": SPRINT4_REGISTRY_VERSION,
        "ncp_trace": ncp_trace,
        "scorecard": scorecard,
        "fair_play": {
            "no_hardcoded_answers": not suspicious_lookup_paths,
            "suspicious_lookup_paths": suspicious_lookup_paths,
        },
    }

    if not adapter_ok:
        return TrackResult(
            track="tau2",
            status="skip",
            summary="payload targets a different adapter; τ² structural validation skipped",
            score=0.0,
            details=details,
        )

    if missing:
        details["missing"] = missing
        return TrackResult(
            track="tau2",
            status="warn",
            summary=f"τ² payload is incomplete for local validation: missing {', '.join(missing)}",
            score=0.5,
            details=details,
        )

    score = 0.70
    if tools:
        score += 0.10
    if len(turns) >= 2:
        score += 0.05
    if success_criteria:
        score += 0.05
    if constraints:
        score += 0.05
    if scenario_id == TICKETTWISTER_SCENARIO_ID:
        score += 0.05

    if suspicious_lookup_paths:
        details["warning"] = (
            "payload contains fields that look like answer keys or lookup tables; "
            "local validation remains structural, but fair-play score is capped"
        )
        return TrackResult(
            track="tau2",
            status="warn",
            summary=(
                "τ² payload is structurally usable but contains fair-play lookup "
                "fields that should be removed before benchmark submission"
            ),
            score=min(score, 0.72),
            details=details,
        )

    if scenario_id == TICKETTWISTER_SCENARIO_ID:
        summary = (
            "τ² payload includes TicketTwister-compatible task structure suitable "
            "for local Purple validation inside AegisForge"
        )
    elif domain == "quipu_lab":
        summary = (
            "τ² payload includes a quipu_lab-compatible domain/task structure "
            "suitable for local Purple validation inside AegisForge"
        )
    else:
        summary = (
            "τ² payload includes a domain and multi-turn task structure suitable "
            "for local validation inside AegisForge"
        )

    return TrackResult(
        track="tau2",
        status="pass",
        summary=summary,
        score=min(score, 0.95),
        details=details,
    )
