from __future__ import annotations

"""Validators for the B4 Submission Gate.

The original module intentionally exposed four small validators:

    validate_health_payload(payload)
    validate_agent_card_payload(payload)
    validate_endpoint_result(payload)
    validate_submission_report(payload)

Those functions remain compatible. Sprint 4 adds optional deeper validation for
AgentX-AgentBeats Purple Benchmark metadata, scenario coverage, upstream track
preservation, telemetry readiness, and submission safety scope.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse


BENCHMARK_NAME = "AgentX-AgentBeats Phase 2 Sprint 4"
DEFAULT_ASSESSMENT_MODE = "purple_benchmark"
DEFAULT_SCENARIO_FAMILY = "agentbeats_sprint4"

IDENTITY_FIELDS: tuple[str, ...] = (
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

CORE_IDENTITY_FIELDS: tuple[str, ...] = (
    "domain",
    "scenario_id",
    "scenario_name",
    "upstream_track",
    "category",
    "adapter",
    "assessment_mode",
    "scenario_family",
)

EXPECTED_SCENARIOS: dict[str, dict[str, str]] = {
    "saleforceonespy": {
        "domain": "business_process",
        "scenario_name": "SaleForceOneSpy",
        "upstream_track": "crmarena",
        "category": "prompt_extraction",
        "selected_opponent": "crmarena",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/saleforceonespy/index.html",
    },
    "wikiwiper": {
        "domain": "game",
        "scenario_name": "WikiWiper",
        "upstream_track": "mcu_minecraft",
        "category": "indirect_injection",
        "selected_opponent": "mcu_minecraft",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/wikiwiper/index.html",
    },
    "tickettwister": {
        "domain": "tau2",
        "scenario_name": "TicketTwister",
        "upstream_track": "tau2_agentbeats",
        "category": "jailbreak",
        "selected_opponent": "tau2_agentbeats",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/tickettwister/index.html",
    },
    "bidbot": {
        "domain": "multi_agent",
        "scenario_name": "BidBot",
        "upstream_track": "maizebargain",
        "category": "pii_leak",
        "selected_opponent": "maizebargain",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/bidbot/index.html",
    },
    "taxwiztrap": {
        "domain": "finance",
        "scenario_name": "TaxWizTrap",
        "upstream_track": "officeqa",
        "category": "indirect_injection",
        "selected_opponent": "officeqa",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/taxwiztrap/index.html",
    },
    "lnklifter": {
        "domain": "computer_use",
        "scenario_name": "LnkLifter",
        "upstream_track": "osworld",
        "category": "insecure_output",
        "selected_opponent": "osworld",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/lnklifter/index.html",
    },
    "inventoryinject": {
        "domain": "agent_safety",
        "scenario_name": "InventoryInject",
        "upstream_track": "pibench",
        "category": "indirect_injection",
        "selected_opponent": "pibench",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/inventoryinject/index.html",
    },
    "devcontainerdoom": {
        "domain": "coding",
        "scenario_name": "DevContainerDoom",
        "upstream_track": "netarena",
        "category": "supply_chain",
        "selected_opponent": "netarena",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/devcontainerdoom/index.html",
    },
    "staticshipscam": {
        "domain": "cybersecurity",
        "scenario_name": "StaticShipScam",
        "upstream_track": "cybergym",
        "category": "supply_chain",
        "selected_opponent": "cybergym",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/staticshipscam/index.html",
    },
    "whistleblowerwreck": {
        "domain": "research",
        "scenario_name": "WhistleBlowerWreck",
        "upstream_track": "fieldworkarena",
        "category": "pii_leak",
        "selected_opponent": "fieldworkarena",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/whistleblowerwreck/index.html",
    },
    "docudoctor": {
        "domain": "healthcare",
        "scenario_name": "DocuDoctor",
        "upstream_track": "fhir_agent",
        "category": "pii_leak",
        "selected_opponent": "fhir_agent",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/docudoctor/index.html",
    },
    "searchglitch": {
        "domain": "web",
        "scenario_name": "SearchGlitch",
        "upstream_track": "comtrade",
        "category": "jailbreak",
        "selected_opponent": "comtrade",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/searchglitch/index.html",
    },
    "gymjailbreak": {
        "domain": "agent_security",
        "scenario_name": "GymJailbreak",
        "upstream_track": "lambda_agent_security",
        "category": "insecure_output",
        "selected_opponent": "lambda_agent_security",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/gymjailbreak/index.html",
    },
    "codereviewruse": {
        "domain": "software_testing",
        "scenario_name": "CodeReviewRuse",
        "upstream_track": "logomesh",
        "category": "indirect_injection",
        "selected_opponent": "logomesh",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/codereviewruse/index.html",
    },
    "cryptocrash": {
        "domain": "defi",
        "scenario_name": "CryptoCrash",
        "upstream_track": "ethernaut",
        "category": "secret_leak",
        "selected_opponent": "ethernaut",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/cryptocrash/index.html",
    },
    "lawfirmleak": {
        "domain": "legal_domain",
        "scenario_name": "LawFirmLeak",
        "upstream_track": "agentify_bench",
        "category": "prompt_extraction",
        "selected_opponent": "agentify_bench",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/lawfirmleak/index.html",
    },
}

EXPECTED_SCENARIO_IDS: tuple[str, ...] = tuple(EXPECTED_SCENARIOS)
EXPECTED_DOMAINS: tuple[str, ...] = tuple(item["domain"] for item in EXPECTED_SCENARIOS.values())
EXPECTED_UPSTREAM_TRACKS: tuple[str, ...] = tuple(item["upstream_track"] for item in EXPECTED_SCENARIOS.values())
EXPECTED_CATEGORIES: tuple[str, ...] = tuple(sorted({item["category"] for item in EXPECTED_SCENARIOS.values()}))

UPSTREAM_ALIASES: dict[str, str] = {
    "crmarenapro": "crmarena",
}

SCENARIO_ALIASES: dict[str, str] = {
    "salesforceonespy": "saleforceonespy",
    "salesforceone": "saleforceonespy",
    "saleforceone": "saleforceonespy",
    "lnk_lifter": "lnklifter",
    "linklifter": "lnklifter",
    "link_lifter": "lnklifter",
    "whistle_blower_wreck": "whistleblowerwreck",
    "code_review_ruse": "codereviewruse",
    "crypto_crash": "cryptocrash",
    "law_firm_leak": "lawfirmleak",
}


def _expect_mapping(payload: Any, label: str) -> list[str]:
    if isinstance(payload, Mapping):
        return []
    return [f"{label} must be an object"]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _slug(value: Any) -> str:
    text = _text(value).lower()
    cleaned: list[str] = []
    previous_sep = False
    for char in text:
        if char.isalnum():
            cleaned.append(char)
            previous_sep = False
        elif not previous_sep:
            cleaned.append("_")
            previous_sep = True
    return "".join(cleaned).strip("_")


def _compact(value: Any) -> str:
    return _slug(value).replace("_", "")


def _scenario_key(value: Any) -> str:
    compact = _compact(value)
    return SCENARIO_ALIASES.get(compact, compact)


def _upstream_key(value: Any) -> str:
    key = _text(value).lower()
    return UPSTREAM_ALIASES.get(key, key)


def _validate_url(value: Any, field_name: str, *, allow_empty: bool = False) -> list[str]:
    text = _text(value)
    if not text:
        return [] if allow_empty else [f"{field_name} must be a non-empty URL"]
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return [f"{field_name} must be a valid http/https URL"]
    return []


def _validate_required_fields(data: Mapping[str, Any], fields: Sequence[str], label: str) -> list[str]:
    missing = [field for field in fields if not _text(data.get(field))]
    if missing:
        return [f"{label} missing required fields: {', '.join(missing)}"]
    return []


def _validate_check_payload(payload: Any, label: str) -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    if "ok" not in data:
        errors.append(f"{label} missing field: ok")
    elif not isinstance(data.get("ok"), bool):
        errors.append(f"{label}.ok must be a boolean")

    if "errors" not in data:
        errors.append(f"{label} missing field: errors")
    elif not isinstance(data.get("errors"), list):
        errors.append(f"{label}.errors must be a list")

    if "warnings" in data and not isinstance(data.get("warnings"), list):
        errors.append(f"{label}.warnings must be a list")

    return errors


def validate_health_payload(payload: Any) -> list[str]:
    errors = _expect_mapping(payload, "health payload")
    if errors:
        return errors

    data = dict(payload)
    if data.get("status") != "ok":
        errors.append("health payload must include status='ok'")

    return errors


def validate_agent_card_payload(payload: Any) -> list[str]:
    errors = _expect_mapping(payload, "agent card")
    if errors:
        return errors

    data = dict(payload)
    required = ["name", "description"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        errors.append(f"agent card missing required fields: {', '.join(missing)}")

    if not (data.get("url") or data.get("endpoint") or data.get("base_url")):
        errors.append("agent card should declare url, endpoint, or base_url")

    # Optional AgentBeats / A2A useful fields. Warn as errors only when fields
    # are present but malformed.
    for field in ("url", "endpoint", "base_url"):
        if data.get(field):
            errors.extend(_validate_url(data.get(field), f"agent card {field}"))

    return errors


def validate_sprint4_identity(payload: Any, *, label: str = "identity", require_all: bool = True) -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    if require_all:
        errors.extend(_validate_required_fields(data, CORE_IDENTITY_FIELDS, label))

    scenario_id = _scenario_key(data.get("scenario_id"))
    domain = _text(data.get("domain"))
    upstream_track = _upstream_key(data.get("upstream_track"))
    category = _text(data.get("category"))
    assessment_mode = _text(data.get("assessment_mode"))
    scenario_family = _text(data.get("scenario_family"))
    source_url = _text(data.get("source_url"))

    if scenario_id and scenario_id not in EXPECTED_SCENARIOS:
        errors.append(f"{label}.scenario_id is not a known Sprint 4 scenario: {data.get('scenario_id')}")

    expected = EXPECTED_SCENARIOS.get(scenario_id)
    if expected:
        if domain and domain != expected["domain"]:
            errors.append(
                f"{label}.domain mismatch for {scenario_id}: expected {expected['domain']}, got {domain}"
            )
        if upstream_track and upstream_track != expected["upstream_track"]:
            errors.append(
                f"{label}.upstream_track mismatch for {scenario_id}: expected {expected['upstream_track']}, got {upstream_track}"
            )
        if category and category != expected["category"]:
            errors.append(
                f"{label}.category mismatch for {scenario_id}: expected {expected['category']}, got {category}"
            )

    if domain and domain not in EXPECTED_DOMAINS:
        errors.append(f"{label}.domain is not one of the 16 Sprint 4 domains: {domain}")

    if upstream_track and upstream_track not in EXPECTED_UPSTREAM_TRACKS:
        errors.append(f"{label}.upstream_track is not a known Sprint 4 upstream track: {upstream_track}")

    if category and category not in EXPECTED_CATEGORIES:
        errors.append(f"{label}.category is not a known Sprint 4 category: {category}")

    if assessment_mode and assessment_mode not in {
        "purple_benchmark",
        "purple_benchmark_preview",
        "green_defensive",
        "smoke",
        "local_smoke",
        "evaluation",
    }:
        errors.append(f"{label}.assessment_mode is not recognized: {assessment_mode}")

    if scenario_family and scenario_family != DEFAULT_SCENARIO_FAMILY:
        errors.append(f"{label}.scenario_family should be {DEFAULT_SCENARIO_FAMILY!r} for Sprint 4")

    if source_url:
        errors.extend(_validate_url(source_url, f"{label}.source_url"))

    return errors


def validate_safety_scope(payload: Any, *, label: str = "safety_scope") -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    required_true = (
        "benchmark_only",
        "controlled_only",
        "no_real_world_targeting",
        "no_secret_extraction_from_real_systems",
        "no_persistence_or_evasion",
    )
    for field in required_true:
        if field in data and not isinstance(data[field], bool):
            errors.append(f"{label}.{field} must be a boolean")
        elif field in data and data[field] is not True:
            errors.append(f"{label}.{field} must be true for Sprint 4 submission readiness")

    return errors


def validate_scenario_coverage(payload: Any, *, label: str = "scenario_coverage") -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    expected = set(_as_string_list(data.get("expected_scenarios")) or EXPECTED_SCENARIO_IDS)
    present = set(_scenario_key(item) for item in _as_string_list(data.get("present_scenarios")))
    missing = set(_scenario_key(item) for item in _as_string_list(data.get("missing_scenarios")))

    if data.get("expected_count") is not None and data.get("expected_count") != len(expected):
        errors.append(f"{label}.expected_count should be {len(expected)}")

    if data.get("present_count") is not None and present and data.get("present_count") != len(present):
        errors.append(f"{label}.present_count does not match present_scenarios length")

    if data.get("missing_count") is not None and missing and data.get("missing_count") != len(missing):
        errors.append(f"{label}.missing_count does not match missing_scenarios length")

    if present:
        unknown_present = sorted(item for item in present if item not in EXPECTED_SCENARIOS)
        if unknown_present:
            errors.append(f"{label}.present_scenarios contains unknown scenarios: {', '.join(unknown_present)}")

        actual_missing = sorted(expected - present)
        if actual_missing and data.get("ok") is True:
            errors.append(f"{label}.ok is true but scenarios are missing: {', '.join(actual_missing)}")

    scenarios = data.get("scenarios")
    if isinstance(scenarios, Mapping):
        for key, record in scenarios.items():
            record_errors = validate_sprint4_identity(record, label=f"{label}.scenarios.{key}", require_all=False)
            errors.extend(record_errors)

    return errors


def validate_string_coverage(
    payload: Any,
    *,
    label: str,
    expected_values: Sequence[str],
    normalize_aliases: bool = False,
) -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    expected = set(expected_values)
    present = set(_as_string_list(data.get("present")))
    if normalize_aliases:
        present = {_upstream_key(item) for item in present}

    unknown = sorted(item for item in present if item not in expected)
    if unknown:
        errors.append(f"{label}.present contains unexpected values: {', '.join(unknown)}")

    missing = sorted(expected - present) if present else []
    if missing and data.get("ok") is True:
        errors.append(f"{label}.ok is true but values are missing: {', '.join(missing)}")

    return errors


def validate_telemetry_payload(payload: Any, *, label: str = "telemetry") -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    bool_fields = (
        "trace_schema",
        "episode_summary",
        "failure_taxonomy",
        "scorecard",
        "emitter",
        "events",
        "preserves_identity",
    )
    for field in bool_fields:
        if field in data and not isinstance(data[field], bool):
            errors.append(f"{label}.{field} must be a boolean")

    if data.get("preserves_identity") is False:
        errors.append(f"{label}.preserves_identity must be true for Sprint 4 readiness")

    missing_fields = _as_string_list(data.get("missing_fields") or data.get("missing_identity_fields"))
    if missing_fields:
        errors.append(f"{label} missing identity fields: {', '.join(missing_fields)}")

    return errors


def validate_submission_readiness(payload: Any, *, label: str = "submission_readiness") -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    if "ok" in data and not isinstance(data.get("ok"), bool):
        errors.append(f"{label}.ok must be a boolean")

    blocking = _as_string_list(data.get("blocking_issues"))
    if blocking and data.get("ok") is True:
        errors.append(f"{label}.ok is true but blocking_issues are present")

    return errors


def validate_endpoint_result(payload: Any) -> list[str]:
    errors = _expect_mapping(payload, "endpoint result")
    if errors:
        return errors

    data = dict(payload)
    for field in ("base_url", "timestamp", "checks"):
        if field not in data:
            errors.append(f"endpoint result missing field: {field}")

    if "base_url" in data:
        errors.extend(_validate_url(data.get("base_url"), "endpoint result base_url"))

    checks = data.get("checks")
    if "checks" in data and not isinstance(checks, Mapping):
        errors.append("endpoint result checks must be an object")
        return errors

    if isinstance(checks, Mapping):
        checks_data = dict(checks)
        for required_check in ("health", "agent_card", "repo"):
            if required_check not in checks_data:
                errors.append(f"endpoint result checks missing required check: {required_check}")

        for name, check_payload in checks_data.items():
            if name == "repo":
                errors.extend(validate_repo_check_payload(check_payload, label="checks.repo"))
            elif name == "sprint4":
                errors.extend(validate_sprint4_check_payload(check_payload, label="checks.sprint4"))
            else:
                errors.extend(_validate_check_payload(check_payload, f"checks.{name}"))

    if data.get("identity"):
        errors.extend(validate_sprint4_identity(data["identity"], label="identity", require_all=False))

    if data.get("benchmark"):
        errors.extend(validate_benchmark_payload(data["benchmark"], label="benchmark"))

    if data.get("safety_scope"):
        errors.extend(validate_safety_scope(data["safety_scope"]))

    if data.get("scenario_coverage"):
        errors.extend(validate_scenario_coverage(data["scenario_coverage"]))

    if data.get("upstream_track_coverage"):
        errors.extend(
            validate_string_coverage(
                data["upstream_track_coverage"],
                label="upstream_track_coverage",
                expected_values=EXPECTED_UPSTREAM_TRACKS,
                normalize_aliases=True,
            )
        )

    if data.get("domain_coverage"):
        errors.extend(
            validate_string_coverage(
                data["domain_coverage"],
                label="domain_coverage",
                expected_values=EXPECTED_DOMAINS,
            )
        )

    if data.get("category_coverage"):
        errors.extend(
            validate_string_coverage(
                data["category_coverage"],
                label="category_coverage",
                expected_values=EXPECTED_CATEGORIES,
            )
        )

    if data.get("telemetry"):
        errors.extend(validate_telemetry_payload(data["telemetry"]))

    if data.get("submission_readiness"):
        errors.extend(validate_submission_readiness(data["submission_readiness"]))

    return errors


def validate_repo_check_payload(payload: Any, *, label: str = "repo") -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    if "ok" not in data or not isinstance(data.get("ok"), bool):
        errors.append(f"{label}.ok must be present and boolean")
    if "artifacts" not in data or not isinstance(data.get("artifacts"), Mapping):
        errors.append(f"{label}.artifacts must be an object")
    if "missing" not in data or not isinstance(data.get("missing"), list):
        errors.append(f"{label}.missing must be a list")

    required_groups = data.get("required_groups")
    if isinstance(required_groups, Mapping):
        for name, group in required_groups.items():
            group_errors = validate_repo_check_payload(group, label=f"{label}.required_groups.{name}")
            errors.extend(group_errors)

    return errors


def validate_sprint4_check_payload(payload: Any, *, label: str = "sprint4") -> list[str]:
    errors = _validate_check_payload(payload, label)
    if errors:
        return errors

    data = dict(payload)
    if data.get("identity"):
        errors.extend(validate_sprint4_identity(data["identity"], label=f"{label}.identity", require_all=False))
    if data.get("scenario_coverage"):
        errors.extend(validate_scenario_coverage(data["scenario_coverage"], label=f"{label}.scenario_coverage"))
    if data.get("upstream_track_coverage"):
        errors.extend(
            validate_string_coverage(
                data["upstream_track_coverage"],
                label=f"{label}.upstream_track_coverage",
                expected_values=EXPECTED_UPSTREAM_TRACKS,
                normalize_aliases=True,
            )
        )
    if data.get("domain_coverage"):
        errors.extend(
            validate_string_coverage(
                data["domain_coverage"],
                label=f"{label}.domain_coverage",
                expected_values=EXPECTED_DOMAINS,
            )
        )
    if data.get("category_coverage"):
        errors.extend(
            validate_string_coverage(
                data["category_coverage"],
                label=f"{label}.category_coverage",
                expected_values=EXPECTED_CATEGORIES,
            )
        )

    return errors


def validate_benchmark_payload(payload: Any, *, label: str = "benchmark") -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    assessment_mode = _text(data.get("assessment_mode"))
    scenario_family = _text(data.get("scenario_family"))
    scope = _text(data.get("benchmark_scope"))

    if assessment_mode and assessment_mode != DEFAULT_ASSESSMENT_MODE:
        errors.append(f"{label}.assessment_mode should be {DEFAULT_ASSESSMENT_MODE!r}")
    if scenario_family and scenario_family != DEFAULT_SCENARIO_FAMILY:
        errors.append(f"{label}.scenario_family should be {DEFAULT_SCENARIO_FAMILY!r}")
    if scope and scope not in {"controlled_only", "benchmark_only", "preview_only"}:
        errors.append(f"{label}.benchmark_scope is not recognized: {scope}")

    return errors


def validate_submission_report(payload: Any) -> list[str]:
    errors = _expect_mapping(payload, "submission report")
    if errors:
        return errors

    data = dict(payload)
    for field in ("repo", "endpoints", "summary"):
        if field not in data:
            errors.append(f"submission report missing field: {field}")

    repo = data.get("repo")
    if isinstance(repo, Mapping):
        for field in ("name", "public_url"):
            if not _text(repo.get(field)):
                errors.append(f"submission report repo missing field: {field}")
        if repo.get("public_url"):
            errors.extend(_validate_url(repo.get("public_url"), "submission report repo.public_url"))
    elif "repo" in data:
        errors.append("submission report repo must be an object")

    endpoints = data.get("endpoints")
    if isinstance(endpoints, Mapping):
        for field in ("base_url", "health", "agent_card", "a2a_agent_card"):
            if endpoints.get(field):
                errors.extend(_validate_url(endpoints.get(field), f"submission report endpoints.{field}"))
    elif "endpoints" in data:
        errors.append("submission report endpoints must be an object")

    summary = data.get("summary")
    if isinstance(summary, Mapping):
        for field in ("abstract", "track_strategy"):
            if not _text(summary.get(field)):
                errors.append(f"submission report summary missing field: {field}")
    elif "summary" in data:
        errors.append("submission report summary must be an object")

    if data.get("benchmark"):
        errors.extend(validate_benchmark_payload(data["benchmark"], label="submission report benchmark"))

    if data.get("safety_scope"):
        errors.extend(validate_safety_scope(data["safety_scope"], label="submission report safety_scope"))

    if data.get("scenario_matrix"):
        errors.extend(validate_scenario_matrix(data["scenario_matrix"]))

    if data.get("telemetry"):
        errors.extend(validate_telemetry_payload(data["telemetry"], label="submission report telemetry"))

    if data.get("evaluation"):
        errors.extend(validate_evaluation_payload(data["evaluation"]))

    if data.get("submission_readiness"):
        errors.extend(
            validate_submission_readiness(
                data["submission_readiness"],
                label="submission report submission_readiness",
            )
        )

    return errors


def validate_scenario_matrix(payload: Any, *, label: str = "scenario_matrix") -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    scenarios = _as_list(data.get("scenarios"))
    if scenarios:
        seen: set[str] = set()
        for index, scenario in enumerate(scenarios):
            scenario_label = f"{label}.scenarios[{index}]"
            scenario_errors = validate_sprint4_identity(scenario, label=scenario_label, require_all=False)
            errors.extend(scenario_errors)

            scenario_id = _scenario_key(_as_dict(scenario).get("scenario_id"))
            if scenario_id:
                seen.add(scenario_id)

        missing = sorted(set(EXPECTED_SCENARIO_IDS) - seen)
        if missing and data.get("coverage", {}).get("ok") is True:
            errors.append(f"{label}.coverage.ok is true but scenarios are missing: {', '.join(missing)}")

    if data.get("expected_count") is not None and data.get("expected_count") != len(EXPECTED_SCENARIO_IDS):
        errors.append(f"{label}.expected_count should be {len(EXPECTED_SCENARIO_IDS)}")

    coverage = data.get("coverage")
    if coverage:
        errors.extend(
            validate_string_coverage(
                coverage,
                label=f"{label}.coverage",
                expected_values=EXPECTED_SCENARIO_IDS,
            )
        )

    return errors


def validate_evaluation_payload(payload: Any, *, label: str = "evaluation") -> list[str]:
    errors = _expect_mapping(payload, label)
    if errors:
        return errors

    data = dict(payload)
    tracks = data.get("tracks")
    if isinstance(tracks, Mapping):
        for track_name, result in tracks.items():
            result_errors = _expect_mapping(result, f"{label}.tracks.{track_name}")
            if result_errors:
                errors.extend(result_errors)
                continue

            result_data = dict(result)
            score = result_data.get("score")
            if score is not None and not isinstance(score, (int, float)):
                errors.append(f"{label}.tracks.{track_name}.score must be a number")
            elif isinstance(score, (int, float)) and not 0 <= float(score) <= 1:
                errors.append(f"{label}.tracks.{track_name}.score must be between 0 and 1")

            metadata = result_data.get("metadata")
            if metadata:
                errors.extend(
                    validate_sprint4_identity(
                        metadata,
                        label=f"{label}.tracks.{track_name}.metadata",
                        require_all=False,
                    )
                )
    elif tracks is not None:
        errors.append(f"{label}.tracks must be an object")

    overall = data.get("overall_score")
    if overall is not None:
        if not isinstance(overall, (int, float)):
            errors.append(f"{label}.overall_score must be a number")
        elif not 0 <= float(overall) <= 1:
            errors.append(f"{label}.overall_score must be between 0 and 1")

    return errors


__all__ = [
    "BENCHMARK_NAME",
    "DEFAULT_ASSESSMENT_MODE",
    "DEFAULT_SCENARIO_FAMILY",
    "EXPECTED_SCENARIOS",
    "EXPECTED_SCENARIO_IDS",
    "EXPECTED_DOMAINS",
    "EXPECTED_UPSTREAM_TRACKS",
    "EXPECTED_CATEGORIES",
    "validate_health_payload",
    "validate_agent_card_payload",
    "validate_endpoint_result",
    "validate_submission_report",
    "validate_sprint4_identity",
    "validate_safety_scope",
    "validate_scenario_coverage",
    "validate_string_coverage",
    "validate_telemetry_payload",
    "validate_submission_readiness",
    "validate_repo_check_payload",
    "validate_sprint4_check_payload",
    "validate_benchmark_payload",
    "validate_scenario_matrix",
    "validate_evaluation_payload",
]
