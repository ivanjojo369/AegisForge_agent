from __future__ import annotations

"""OpenEnv track evaluator for AegisForge Eval.

This evaluator keeps the original live-check behavior:

    health -> reset -> step[*] -> state

and adds Sprint 4 identity validation for the 16 final AgentX-AgentBeats /
OpenEnv scenarios. The important behavior for Sprint 4 is that local domains,
AegisForge scenario IDs, and upstream AgentBeats track/opponent profile names
coexist instead of overwriting each other.

TrackResult.metadata always carries the identity block:

    domain, scenario_id, scenario_name, upstream_track, category, adapter,
    assessment_mode, scenario_family, benchmark, selected_opponent, source_url
"""

from collections.abc import Mapping, Sequence
from typing import Any

from ..schemas import STATUS_PASS, STATUS_SKIP, STATUS_WARN, TrackResult

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


DESCRIPTION = (
    "Validates an OpenEnv payload and, when requested, runs a live "
    "health -> reset -> step[*] -> state check while preserving Sprint 4 metadata."
)

BENCHMARK_NAME = "AgentX-AgentBeats Phase 2 Sprint 4"
DEFAULT_ASSESSMENT_MODE = "purple_benchmark"
DEFAULT_SCENARIO_FAMILY = "agentbeats_sprint4"

IDENTITY_KEYS: tuple[str, ...] = (
    "domain",
    "scenario_id",
    "scenario_name",
    "upstream_track",
    "category",
    "adapter",
    "assessment_mode",
    "scenario_family",
    "benchmark",
    "selected_opponent",
    "source_url",
)

SPRINT4_SCENARIOS: dict[str, dict[str, str]] = {
    "saleforceonespy": {
        "domain": "business_process",
        "scenario_id": "saleforceonespy",
        "scenario_name": "SaleForceOneSpy",
        "upstream_track": "crmarena",
        "category": "prompt_extraction",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "crmarena",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/saleforceonespy/index.html",
    },
    "wikiwiper": {
        "domain": "game",
        "scenario_id": "wikiwiper",
        "scenario_name": "WikiWiper",
        "upstream_track": "mcu_minecraft",
        "category": "indirect_injection",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "mcu_minecraft",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/wikiwiper/index.html",
    },
    "tickettwister": {
        "domain": "tau2",
        "scenario_id": "tickettwister",
        "scenario_name": "TicketTwister",
        "upstream_track": "tau2_agentbeats",
        "category": "jailbreak",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "tau2_agentbeats",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/tickettwister/index.html",
    },
    "bidbot": {
        "domain": "multi_agent",
        "scenario_id": "bidbot",
        "scenario_name": "BidBot",
        "upstream_track": "maizebargain",
        "category": "pii_leak",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "maizebargain",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/bidbot/index.html",
    },
    "taxwiztrap": {
        "domain": "finance",
        "scenario_id": "taxwiztrap",
        "scenario_name": "TaxWizTrap",
        "upstream_track": "officeqa",
        "category": "indirect_injection",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "officeqa",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/taxwiztrap/index.html",
    },
    "lnklifter": {
        "domain": "computer_use",
        "scenario_id": "lnklifter",
        "scenario_name": "LnkLifter",
        "upstream_track": "osworld",
        "category": "insecure_output",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "osworld",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/lnklifter/index.html",
    },
    "inventoryinject": {
        "domain": "agent_safety",
        "scenario_id": "inventoryinject",
        "scenario_name": "InventoryInject",
        "upstream_track": "pibench",
        "category": "indirect_injection",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "pibench",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/inventoryinject/index.html",
    },
    "devcontainerdoom": {
        "domain": "coding",
        "scenario_id": "devcontainerdoom",
        "scenario_name": "DevContainerDoom",
        "upstream_track": "netarena",
        "category": "supply_chain",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "netarena",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/devcontainerdoom/index.html",
    },
    "staticshipscam": {
        "domain": "cybersecurity",
        "scenario_id": "staticshipscam",
        "scenario_name": "StaticShipScam",
        "upstream_track": "cybergym",
        "category": "supply_chain",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "cybergym",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/staticshipscam/index.html",
    },
    "whistleblowerwreck": {
        "domain": "research",
        "scenario_id": "whistleblowerwreck",
        "scenario_name": "WhistleBlowerWreck",
        "upstream_track": "fieldworkarena",
        "category": "pii_leak",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "fieldworkarena",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/whistleblowerwreck/index.html",
    },
    "docudoctor": {
        "domain": "healthcare",
        "scenario_id": "docudoctor",
        "scenario_name": "DocuDoctor",
        "upstream_track": "fhir_agent",
        "category": "pii_leak",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "fhir_agent",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/docudoctor/index.html",
    },
    "searchglitch": {
        "domain": "web",
        "scenario_id": "searchglitch",
        "scenario_name": "SearchGlitch",
        "upstream_track": "comtrade",
        "category": "jailbreak",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "comtrade",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/searchglitch/index.html",
    },
    "gymjailbreak": {
        "domain": "agent_security",
        "scenario_id": "gymjailbreak",
        "scenario_name": "GymJailbreak",
        "upstream_track": "lambda_agent_security",
        "category": "insecure_output",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "lambda_agent_security",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/gymjailbreak/index.html",
    },
    "codereviewruse": {
        "domain": "software_testing",
        "scenario_id": "codereviewruse",
        "scenario_name": "CodeReviewRuse",
        "upstream_track": "logomesh",
        "category": "indirect_injection",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "logomesh",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/codereviewruse/index.html",
    },
    "cryptocrash": {
        "domain": "defi",
        "scenario_id": "cryptocrash",
        "scenario_name": "CryptoCrash",
        "upstream_track": "ethernaut",
        "category": "secret_leak",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "ethernaut",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/cryptocrash/index.html",
    },
    "lawfirmleak": {
        "domain": "legal_domain",
        "scenario_id": "lawfirmleak",
        "scenario_name": "LawFirmLeak",
        "upstream_track": "agentify_bench",
        "category": "prompt_extraction",
        "scenario_family": "agentbeats_sprint4",
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "agentify_bench",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/lawfirmleak/index.html",
    },
}

DOMAIN_TO_SCENARIO_ID: dict[str, str] = {
    item["domain"]: scenario_id for scenario_id, item in SPRINT4_SCENARIOS.items()
}

SCENARIO_ALIASES: dict[str, str] = {
    "salesforceonespy": "saleforceonespy",
    "salesforceone": "saleforceonespy",
    "saleforceone": "saleforceonespy",
    "linklifter": "lnklifter",
    "lnk_lifter": "lnklifter",
    "link_lifter": "lnklifter",
    "whistleblowerwreck": "whistleblowerwreck",
    "whistle_blower_wreck": "whistleblowerwreck",
    "code_review_ruse": "codereviewruse",
    "crypto_crash": "cryptocrash",
    "law_firm_leak": "lawfirmleak",
}

SPRINT4_DOMAINS: frozenset[str] = frozenset(DOMAIN_TO_SCENARIO_ID)
SPRINT4_SCENARIO_IDS: frozenset[str] = frozenset(SPRINT4_SCENARIOS)


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _as_float(value: Any, default: float = 10.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _first_text(*values: Any, default: str = "") -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return default


def _slug_key(value: Any) -> str:
    text = _text(value).lower()
    if not text:
        return ""
    cleaned = []
    previous_was_sep = False
    for char in text:
        if char.isalnum():
            cleaned.append(char)
            previous_was_sep = False
        elif not previous_was_sep:
            cleaned.append("_")
            previous_was_sep = True
    return "".join(cleaned).strip("_")


def _compact_key(value: Any) -> str:
    return _slug_key(value).replace("_", "")


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _require_keys(obj: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    return [key for key in keys if key not in obj]


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, round(value, 3)))


def _scenario_id_from_any(value: Any) -> str:
    key = _compact_key(value)
    if not key:
        return ""
    return SCENARIO_ALIASES.get(key, key)


def _find_known_scenario(payload: Mapping[str, Any], scenario: Mapping[str, Any]) -> str:
    candidates = (
        payload.get("scenario_id"),
        payload.get("scenario_name"),
        payload.get("scenario"),
        payload.get("env_name"),
        scenario.get("scenario_id"),
        scenario.get("id"),
        scenario.get("scenario_name"),
        scenario.get("name"),
    )

    for candidate in candidates:
        scenario_id = _scenario_id_from_any(candidate)
        if scenario_id in SPRINT4_SCENARIOS:
            return scenario_id

    domain = _first_text(
        payload.get("domain"),
        payload.get("domain_key"),
        payload.get("scenario_domain"),
        scenario.get("domain"),
    )
    return DOMAIN_TO_SCENARIO_ID.get(domain, "")


def _extract_identity(payload: Mapping[str, Any]) -> dict[str, str]:
    scenario = _to_dict(payload.get("scenario"))
    source = _to_dict(payload.get("source"))
    route = _to_dict(payload.get("route"))
    identity_payload = _to_dict(payload.get("identity") or payload.get("metadata"))

    scenario_id = _find_known_scenario(payload, scenario)
    known = dict(SPRINT4_SCENARIOS.get(scenario_id, {}))

    # Preserve explicit payload values first, then known Sprint 4 registry values.
    identity = {
        "domain": _first_text(
            payload.get("domain"),
            payload.get("domain_key"),
            payload.get("scenario_domain"),
            identity_payload.get("domain"),
            scenario.get("domain"),
            known.get("domain"),
        ),
        "scenario_id": _first_text(
            payload.get("scenario_id"),
            identity_payload.get("scenario_id"),
            scenario.get("scenario_id"),
            scenario.get("id"),
            known.get("scenario_id"),
        ),
        "scenario_name": _first_text(
            payload.get("scenario_name"),
            identity_payload.get("scenario_name"),
            scenario.get("scenario_name"),
            scenario.get("name"),
            known.get("scenario_name"),
        ),
        "upstream_track": _first_text(
            payload.get("upstream_track"),
            payload.get("benchmark_track"),
            payload.get("opponent_profile"),
            identity_payload.get("upstream_track"),
            identity_payload.get("benchmark_track"),
            route.get("upstream_track"),
            route.get("opponent_profile"),
            known.get("upstream_track"),
        ),
        "category": _first_text(
            payload.get("category"),
            payload.get("attack_category"),
            identity_payload.get("category"),
            scenario.get("category"),
            scenario.get("attack_category"),
            known.get("category"),
        ),
        "adapter": _first_text(
            payload.get("adapter"),
            payload.get("adapter_name"),
            identity_payload.get("adapter"),
            route.get("adapter"),
            route.get("adapter_name"),
            default="openenv",
        ),
        "assessment_mode": _first_text(
            payload.get("assessment_mode"),
            identity_payload.get("assessment_mode"),
            scenario.get("assessment_mode"),
            route.get("assessment_mode"),
            known.get("assessment_mode"),
            default=DEFAULT_ASSESSMENT_MODE,
        ),
        "scenario_family": _first_text(
            payload.get("scenario_family"),
            identity_payload.get("scenario_family"),
            scenario.get("scenario_family"),
            route.get("scenario_family"),
            known.get("scenario_family"),
            default=DEFAULT_SCENARIO_FAMILY,
        ),
        "benchmark": _first_text(
            payload.get("benchmark"),
            identity_payload.get("benchmark"),
            source.get("benchmark"),
            known.get("benchmark"),
            default=BENCHMARK_NAME,
        ),
        "selected_opponent": _first_text(
            payload.get("selected_opponent"),
            identity_payload.get("selected_opponent"),
            route.get("selected_opponent"),
            route.get("opponent"),
            source.get("selected_opponent"),
            known.get("selected_opponent"),
        ),
        "source_url": _first_text(
            payload.get("source_url"),
            identity_payload.get("source_url"),
            source.get("source_url"),
            source.get("url"),
            source.get("repo"),
            known.get("source_url"),
        ),
    }

    if not identity["selected_opponent"]:
        identity["selected_opponent"] = identity["upstream_track"]

    # If a caller provided SalesForceOneSpy spelling, normalize the ID but keep
    # scenario_name human-readable.
    normalized_id = _scenario_id_from_any(identity["scenario_id"] or identity["scenario_name"])
    if normalized_id in SPRINT4_SCENARIOS:
        identity["scenario_id"] = normalized_id
        identity["scenario_name"] = identity["scenario_name"] or SPRINT4_SCENARIOS[normalized_id]["scenario_name"]

    return identity


def _identity_validation(identity: Mapping[str, str], *, strict_sprint4: bool) -> dict[str, Any]:
    domain = identity.get("domain", "")
    scenario_id = identity.get("scenario_id", "")
    scenario_known = scenario_id in SPRINT4_SCENARIO_IDS
    domain_known = domain in SPRINT4_DOMAINS
    expected_scenario_for_domain = DOMAIN_TO_SCENARIO_ID.get(domain, "")
    domain_scenario_match = (
        not domain
        or not scenario_id
        or not expected_scenario_for_domain
        or expected_scenario_for_domain == scenario_id
    )

    missing_identity = [
        key
        for key in (
            "domain",
            "scenario_id",
            "scenario_name",
            "upstream_track",
            "category",
            "adapter",
            "assessment_mode",
            "scenario_family",
        )
        if not identity.get(key)
    ]

    return {
        "strict_sprint4": strict_sprint4,
        "domain_known": domain_known,
        "scenario_known": scenario_known,
        "domain_scenario_match": domain_scenario_match,
        "expected_scenario_for_domain": expected_scenario_for_domain or None,
        "missing_identity": missing_identity,
        "supported_domains": sorted(SPRINT4_DOMAINS),
        "supported_scenarios": sorted(SPRINT4_SCENARIO_IDS),
    }


def _result(
    *,
    status: str,
    summary: str,
    score: float,
    details: dict[str, Any],
    metadata: dict[str, Any],
) -> TrackResult:
    return TrackResult(
        track="openenv",
        status=status,
        summary=summary,
        score=_clamp_score(score),
        details=details,
        metadata=metadata,
    )


def _normalize_action_plan(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_plan = payload.get("action_plan")

    if isinstance(raw_plan, Sequence) and not isinstance(raw_plan, (str, bytes)):
        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_plan, start=1):
            if not isinstance(item, Mapping):
                raise ValueError(f"action_plan[{idx}] must be a JSON object")

            action = item.get("action") or item.get("name") or "advance"
            value = _as_int(item.get("value"), default=1)

            normalized.append(
                {
                    "action": str(action),
                    "value": value,
                }
            )

        if normalized:
            return normalized

    action = payload.get("action") or "advance"
    if isinstance(action, Mapping):
        action_name = action.get("type") or action.get("name") or action.get("action") or "advance"
        value = _as_int(action.get("value"), default=_as_int(payload.get("value"), default=1))
    else:
        action_name = str(action)
        value = _as_int(payload.get("value"), default=1)

    return [{"action": action_name, "value": value}]


def _build_reset_payload(payload: Mapping[str, Any], identity: Mapping[str, str]) -> dict[str, Any]:
    raw_reset = payload.get("reset_payload")
    if isinstance(raw_reset, Mapping):
        return dict(raw_reset)

    reset_payload: dict[str, Any] = {}
    seed = payload.get("seed")
    if seed is not None:
        reset_payload["seed"] = seed

    raw_options = payload.get("options") or payload.get("reset_options")
    options = dict(raw_options) if isinstance(raw_options, Mapping) else {}

    if identity.get("domain") and "domain" not in options:
        options["domain"] = identity["domain"]
    if identity.get("scenario_id") and "scenario_id" not in options:
        options["scenario_id"] = identity["scenario_id"]
    if identity.get("scenario_name") and "scenario_name" not in options:
        options["scenario_name"] = identity["scenario_name"]

    if options:
        reset_payload["options"] = options

    return reset_payload


def _extract_action_name(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("action") or value.get("type") or value.get("name") or "").strip()
    return str(value or "").strip()


def _state_envelope(state_json: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = state_json.get("state")
    if isinstance(nested, Mapping):
        return nested
    return state_json


def _state_has_progress_signal(state_json: Mapping[str, Any], envelope: Mapping[str, Any]) -> bool:
    keys = {"score", "progress", "target_score", "target_progress", "done", "success", "step_count"}
    return any(key in state_json for key in keys) or any(key in envelope for key in keys)


def _completed_stage_count(checks: Mapping[str, Any], *, require_success: bool) -> tuple[int, int]:
    completed = 0
    for key in ("health", "reset", "state"):
        if checks.get(key):
            completed += 1
    if int(checks.get("step_count") or 0) > 0:
        completed += 1
    if checks.get("success_path"):
        completed += 1
    denominator = 5 if require_success else 4
    return completed, denominator


def evaluate(payload: Mapping[str, Any] | None = None) -> TrackResult:
    payload = dict(payload or {})
    identity = _extract_identity(payload)
    strict_sprint4 = _as_bool(payload.get("strict_sprint4"), default=True)
    validation = _identity_validation(identity, strict_sprint4=strict_sprint4)

    adapter = identity["adapter"] or "openenv"
    environment_url = str(payload.get("environment_url") or payload.get("base_url") or "").rstrip("/")
    timeout = _as_float(payload.get("timeout"), default=10.0)
    live_check = _as_bool(payload.get("live_check"), default=True)
    require_success = _as_bool(payload.get("require_success"), default=False)
    episode_id = payload.get("episode_id")
    run_id = payload.get("run_id") or episode_id
    expected_env_name = payload.get("env_name")
    expected_scenario_id = payload.get("expected_scenario_id") or identity["scenario_id"]

    metadata: dict[str, Any] = {
        **identity,
        "track": "openenv",
        "run_id": str(run_id or ""),
        "episode_id": str(episode_id or ""),
        "sprint4_validation": validation,
    }

    details: dict[str, Any] = {
        "description": DESCRIPTION,
        "identity": dict(identity),
        "metadata": dict(metadata),
        "adapter": adapter,
        "environment_url": environment_url or None,
        "episode_id": episode_id,
        "run_id": run_id,
        "timeout": timeout,
        "live_check": live_check,
        "require_success": require_success,
        "expected_env_name": expected_env_name,
        "expected_scenario_id": expected_scenario_id,
        "checks": {
            "identity": not validation["missing_identity"],
            "domain_known": validation["domain_known"],
            "scenario_known": validation["scenario_known"],
            "domain_scenario_match": validation["domain_scenario_match"],
            "health": False,
            "reset": False,
            "step_count": 0,
            "state": False,
            "success_path": False,
        },
    }

    if adapter != "openenv":
        return _result(
            status=STATUS_SKIP,
            summary="payload targets a different adapter; OpenEnv track skipped",
            score=0.0,
            details=details,
            metadata=metadata,
        )

    identity_errors: list[str] = []
    if strict_sprint4:
        if not validation["domain_known"]:
            identity_errors.append(f"domain is not one of the 16 Sprint 4 domains: {identity.get('domain') or '<missing>'}")
        if not validation["scenario_known"]:
            identity_errors.append(f"scenario_id is not one of the 16 Sprint 4 scenarios: {identity.get('scenario_id') or '<missing>'}")
        if not validation["domain_scenario_match"]:
            identity_errors.append(
                "domain/scenario_id mismatch: "
                f"{identity.get('domain')} expects {validation['expected_scenario_for_domain']}, "
                f"got {identity.get('scenario_id')}"
            )
        if validation["missing_identity"]:
            identity_errors.append(
                "missing Sprint 4 identity fields: " + ", ".join(validation["missing_identity"])
            )

    if identity_errors:
        details["identity_errors"] = identity_errors
        return _result(
            status=STATUS_WARN,
            summary="OpenEnv Sprint 4 identity is incomplete or inconsistent",
            score=0.25,
            details=details,
            metadata=metadata,
        )

    if not environment_url:
        details["missing"] = ["environment_url"]
        return _result(
            status=STATUS_WARN,
            summary="OpenEnv payload is incomplete: missing environment_url/base_url",
            score=0.35 if validation["scenario_known"] and validation["domain_known"] else 0.15,
            details=details,
            metadata=metadata,
        )

    try:
        action_plan = _normalize_action_plan(payload)
    except Exception as exc:
        details["error"] = str(exc)
        return _result(
            status=STATUS_WARN,
            summary="OpenEnv payload has an invalid action_plan",
            score=0.20,
            details=details,
            metadata=metadata,
        )

    details["action_plan"] = action_plan

    if not live_check:
        return _result(
            status=STATUS_PASS,
            summary="OpenEnv Sprint 4 payload is valid and live check was intentionally disabled",
            score=0.70,
            details=details,
            metadata=metadata,
        )

    if httpx is None:
        details["error"] = "httpx is not available in the current environment"
        return _result(
            status=STATUS_WARN,
            summary="OpenEnv live check could not run because httpx is unavailable",
            score=0.20,
            details=details,
            metadata=metadata,
        )

    try:
        with httpx.Client(timeout=timeout) as client:
            # 1) health
            health_response = client.get(f"{environment_url}/health")
            health_response.raise_for_status()
            health_json = health_response.json()

            if not isinstance(health_json, Mapping):
                raise ValueError("/health did not return a JSON object")
            if "status" in health_json and str(health_json.get("status")) != "ok":
                raise ValueError("/health returned status different from 'ok'")
            if expected_env_name and health_json.get("env") and str(health_json.get("env")) != str(expected_env_name):
                raise ValueError(
                    f"/health env mismatch: expected '{expected_env_name}' got '{health_json.get('env')}'"
                )

            details["health"] = dict(health_json)
            details["checks"]["health"] = True

            # 2) reset
            reset_payload = _build_reset_payload(payload, identity)
            details["reset_payload"] = reset_payload

            reset_response = client.post(f"{environment_url}/reset", json=reset_payload)
            reset_response.raise_for_status()
            reset_json = reset_response.json()

            if not isinstance(reset_json, Mapping):
                raise ValueError("/reset did not return a JSON object")

            reset_state = reset_json.get("state")
            reset_info = reset_json.get("info")
            if reset_state is not None and not isinstance(reset_state, Mapping):
                raise ValueError("/reset.state is not a JSON object")
            if reset_info is not None and not isinstance(reset_info, Mapping):
                raise ValueError("/reset.info is not a JSON object")

            if expected_scenario_id:
                observed_reset_scenario = (
                    reset_json.get("scenario_id")
                    or (reset_info.get("scenario_id") if isinstance(reset_info, Mapping) else None)
                    or (reset_state.get("scenario_id") if isinstance(reset_state, Mapping) else None)
                )
                if observed_reset_scenario and str(observed_reset_scenario) != str(expected_scenario_id):
                    raise ValueError(
                        f"/reset scenario_id mismatch: expected '{expected_scenario_id}' "
                        f"got '{observed_reset_scenario}'"
                    )

            details["reset"] = dict(reset_json)
            details["checks"]["reset"] = True

            # 3) step plan
            step_results: list[dict[str, Any]] = []
            last_step_state: Mapping[str, Any] | None = None
            last_action_name = ""

            for idx, step_spec in enumerate(action_plan, start=1):
                last_action_name = _extract_action_name(step_spec["action"])
                step_response = client.post(
                    f"{environment_url}/step",
                    json={
                        "action": step_spec["action"],
                        "value": step_spec["value"],
                    },
                )
                step_response.raise_for_status()
                step_json = step_response.json()

                if not isinstance(step_json, Mapping):
                    raise ValueError(f"/step #{idx} did not return a JSON object")

                step_state = step_json.get("state")
                if step_state is not None and not isinstance(step_state, Mapping):
                    raise ValueError(f"/step #{idx}.state is not a JSON object")

                if isinstance(step_state, Mapping):
                    observed_action = _extract_action_name(step_state.get("last_action"))
                    if observed_action and last_action_name and observed_action != last_action_name:
                        raise ValueError(
                            f"/step #{idx} last_action mismatch: expected "
                            f"'{last_action_name}' got '{observed_action}'"
                        )
                    last_step_state = step_state

                step_results.append(dict(step_json))
                details["checks"]["step_count"] = idx

            details["steps"] = step_results

            # 4) state
            state_response = client.get(f"{environment_url}/state")
            state_response.raise_for_status()
            state_json = state_response.json()

            if not isinstance(state_json, Mapping):
                raise ValueError("/state did not return a JSON object")

            envelope = _state_envelope(state_json)
            if not isinstance(envelope, Mapping):
                raise ValueError("/state state envelope is not a JSON object")
            if not _state_has_progress_signal(state_json, envelope):
                raise ValueError("/state does not include a recognizable progress/success signal")

            if episode_id and state_json.get("episode_id") and state_json.get("episode_id") != episode_id:
                raise ValueError(
                    f"/state episode_id mismatch: expected '{episode_id}' got '{state_json.get('episode_id')}'"
                )

            observed_scenario = state_json.get("scenario_id") or envelope.get("scenario_id")
            if expected_scenario_id and observed_scenario and str(observed_scenario) != str(expected_scenario_id):
                raise ValueError(
                    f"/state scenario_id mismatch: expected '{expected_scenario_id}' got '{observed_scenario}'"
                )

            if last_step_state is not None:
                final_last_action = _extract_action_name(envelope.get("last_action"))
                previous_last_action = _extract_action_name(last_step_state.get("last_action"))
                if previous_last_action and final_last_action and final_last_action != previous_last_action:
                    raise ValueError("/state last_action does not match the last step result")

            details["state"] = dict(state_json)
            details["checks"]["state"] = True

            if require_success:
                done = bool(envelope.get("done", state_json.get("done", False)))
                success = bool(envelope.get("success", state_json.get("success", False)))
                score = _as_int(envelope.get("score", state_json.get("score")), default=0)
                progress = _as_int(envelope.get("progress", state_json.get("progress")), default=score)
                target = _as_int(
                    envelope.get("target_score")
                    or envelope.get("target_progress")
                    or state_json.get("target_score")
                    or state_json.get("target_progress"),
                    default=0,
                )

                if not done:
                    raise ValueError("require_success=True but final state.done is false")
                if not success:
                    raise ValueError("require_success=True but final state.success is false")
                if target > 0 and progress < target:
                    raise ValueError(
                        f"require_success=True but final progress {progress} is below target {target}"
                    )

                details["checks"]["success_path"] = True

            return _result(
                status=STATUS_PASS,
                summary="OpenEnv live check passed with Sprint 4 identity preserved",
                score=1.0,
                details=details,
                metadata=metadata,
            )

    except Exception as exc:
        completed_stages, denominator = _completed_stage_count(
            details["checks"],
            require_success=require_success,
        )
        details["error"] = str(exc)

        identity_bonus = 0.10 if validation["domain_known"] and validation["scenario_known"] else 0.0
        score = min(0.95, round(completed_stages / denominator, 2) + identity_bonus)

        return _result(
            status=STATUS_WARN,
            summary=(
                f"OpenEnv live check failed after {completed_stages}/{denominator} "
                "completed stages; Sprint 4 metadata was preserved"
            ),
            score=score,
            details=details,
            metadata=metadata,
        )
