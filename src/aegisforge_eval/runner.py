from __future__ import annotations

"""Local evaluation runner for AegisForge tracks.

The original runner already handled:

- running named tracks;
- selecting a subset of tracks;
- merging inline/file payloads and metadata;
- building an EvaluationReport;
- optional held-out degradation/generality metadata.

Sprint 4 adds one critical requirement: do not lose benchmark identity when
TrackResult objects flow into the final report. This runner therefore preserves
and summarizes per-track metadata while keeping the existing public API intact.
"""

import argparse
from dataclasses import asdict, is_dataclass
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .report import build_report, report_to_json
from .schemas import EvaluationReport, TrackResult
from .tracks import get_evaluator, get_track_names

try:  # Optional held-out enrichments
    from .heldouts.degradation import DegradationAnalyzer
    from .heldouts.generality import GeneralityAnalyzer

    _HAS_HELDOUTS = True
except Exception:  # pragma: no cover
    DegradationAnalyzer = None  # type: ignore[assignment]
    GeneralityAnalyzer = None  # type: ignore[assignment]
    _HAS_HELDOUTS = False


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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
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
    "lnk_lifter": "lnklifter",
    "linklifter": "lnklifter",
    "link_lifter": "lnklifter",
    "whistle_blower_wreck": "whistleblowerwreck",
    "code_review_ruse": "codereviewruse",
    "crypto_crash": "cryptocrash",
    "law_firm_leak": "lawfirmleak",
}


def run_named_track(name: str, payload: Mapping[str, Any] | None = None) -> TrackResult:
    evaluator = get_evaluator(name)
    return evaluator(payload)


def _resolve_track_selection(selected: Sequence[str] | None) -> list[str]:
    available = get_track_names()
    if not selected:
        return available

    requested = [name for name in selected if name and name != "all"]
    if not requested:
        return available

    unknown = [name for name in requested if name not in available]
    if unknown:
        raise ValueError(
            f"Unknown tracks requested: {', '.join(unknown)}. Available: {', '.join(available)}"
        )
    return list(requested)


def _augment_metadata(
    payload: Mapping[str, Any],
    metadata: dict[str, Any] | None,
    selected_names: list[str],
    results: Sequence[TrackResult] | None = None,
) -> dict[str, Any]:
    merged = dict(metadata or {})
    payload_identity = _extract_identity(payload)

    merged.setdefault("selected_tracks", list(selected_names))
    merged.setdefault("payload_keys", sorted(payload.keys()))
    merged.setdefault("benchmark", payload_identity.get("benchmark") or BENCHMARK_NAME)
    merged.setdefault("assessment_mode", payload_identity.get("assessment_mode") or DEFAULT_ASSESSMENT_MODE)
    merged.setdefault("scenario_family", payload_identity.get("scenario_family") or DEFAULT_SCENARIO_FAMILY)

    if any(payload_identity.get(key) for key in IDENTITY_KEYS):
        merged.setdefault("sprint4_identity", payload_identity)
        merged.setdefault("sprint4_validation", _identity_validation(payload_identity))

    if results is not None:
        result_metadata = _collect_result_metadata(results)
        result_identities = {
            track: _identity_from_mapping(meta)
            for track, meta in result_metadata.items()
        }
        merged["track_result_metadata"] = result_metadata
        merged["track_result_identities"] = result_identities
        merged["track_statuses"] = {
            _get_result_track(result): _get_result_status(result)
            for result in results
        }
        merged["track_scores"] = {
            _get_result_track(result): _get_result_score(result)
            for result in results
        }

        if result_identities:
            merged["sprint4_identity_coverage"] = _identity_coverage(result_identities)

    if _HAS_HELDOUTS:
        if "baseline_results" in payload and "heldout_results" in payload:
            report = DegradationAnalyzer().analyze(
                payload.get("baseline_results", []),
                payload.get("heldout_results", []),
            )
            merged["heldout_degradation"] = report.as_dict()

        if "suite_success_rates" in payload:
            generality = GeneralityAnalyzer().analyze(payload["suite_success_rates"])
            merged["generality_hint"] = generality.as_dict()

    return merged


def evaluate_tracks(
    payload: Mapping[str, Any] | None = None,
    *,
    selected: Sequence[str] | None = None,
    agent_name: str = "AegisForge",
    metadata: dict[str, Any] | None = None,
) -> EvaluationReport:
    payload = dict(payload or {})
    selected_names = _resolve_track_selection(selected)

    # Build a report-level identity and pass it through payload metadata too.
    # Track evaluators can use flat fields directly, while generic tracks still
    # receive the original payload shape.
    payload_identity = _extract_identity(payload)
    enriched_payload = dict(payload)
    if any(payload_identity.get(key) for key in IDENTITY_KEYS):
        enriched_payload.setdefault("identity", payload_identity)
        enriched_payload.setdefault("metadata", {})
        if isinstance(enriched_payload["metadata"], Mapping):
            enriched_payload["metadata"] = {**payload_identity, **dict(enriched_payload["metadata"])}

    raw_results = [run_named_track(name, enriched_payload) for name in selected_names]
    normalized_results = [
        _ensure_result_metadata(result, payload_identity=payload_identity, selected_track=name)
        for name, result in zip(selected_names, raw_results, strict=False)
    ]

    enriched_metadata = _augment_metadata(
        enriched_payload,
        metadata,
        selected_names,
        results=normalized_results,
    )
    return build_report(normalized_results, agent_name=agent_name, metadata=enriched_metadata)


def _load_json_arg(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid inline JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc

    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def _load_json_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}

    file_path = Path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {file_path} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in file: {path}")
    return payload


def _write_text(path: str | Path, text: str) -> Path:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding="utf-8")
    return file_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local evaluation helpers for AegisForge tracks.")
    parser.add_argument("--payload", default=None, help="Inline JSON object with input fields for the selected tracks")
    parser.add_argument("--payload-file", default=None, help="Path to a JSON file with the evaluation payload")
    parser.add_argument("--metadata", default=None, help="Inline JSON metadata object to attach to the report")
    parser.add_argument("--metadata-file", default=None, help="Path to a JSON file with extra report metadata")
    parser.add_argument(
        "--tracks",
        nargs="*",
        default=None,
        help=f"Optional subset of tracks. Use 'all' or omit for all tracks. Available: {', '.join(get_track_names())}",
    )
    parser.add_argument("--agent-name", default="AegisForge")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the resulting report JSON")
    parser.add_argument("--output", default=None, help="Optional path where the report JSON should be written")
    args = parser.parse_args()

    payload: dict[str, Any] = {}
    if args.payload_file:
        payload.update(_load_json_file(args.payload_file))
    if args.payload:
        payload.update(_load_json_arg(args.payload))

    metadata: dict[str, Any] = {}
    if args.metadata_file:
        metadata.update(_load_json_file(args.metadata_file))
    if args.metadata:
        metadata.update(_load_json_arg(args.metadata))

    report = evaluate_tracks(
        payload,
        selected=args.tracks,
        agent_name=args.agent_name,
        metadata=metadata or None,
    )
    rendered = report_to_json(report)
    if args.pretty:
        rendered = json.dumps(json.loads(rendered), indent=2, ensure_ascii=False)

    if args.output:
        _write_text(args.output, rendered + ("\n" if not rendered.endswith("\n") else ""))
    else:
        print(rendered)

    return 0


def _ensure_result_metadata(
    result: TrackResult,
    *,
    payload_identity: Mapping[str, Any],
    selected_track: str,
) -> TrackResult:
    """Return a TrackResult that carries complete metadata when possible."""

    current_metadata = _get_result_metadata(result)
    merged_metadata = dict(payload_identity)
    merged_metadata.update(current_metadata)
    merged_metadata.setdefault("track", _get_result_track(result) or selected_track)

    # Generic tracks may not know Sprint 4 identity. Do not penalize them, but
    # attach report-level identity so downstream reports can group consistently.
    identity = _identity_from_mapping(merged_metadata)
    if any(identity.values()):
        merged_metadata.update({key: value for key, value in identity.items() if value})

    if merged_metadata == current_metadata:
        return result

    return TrackResult(
        track=_get_result_track(result) or selected_track,
        status=_get_result_status(result),
        summary=_get_result_summary(result),
        score=_get_result_score(result),
        details=_get_result_details(result),
        metadata=merged_metadata,
    )


def _collect_result_metadata(results: Sequence[TrackResult]) -> dict[str, dict[str, Any]]:
    collected: dict[str, dict[str, Any]] = {}
    for result in results:
        track = _get_result_track(result)
        collected[track] = _get_result_metadata(result)
    return collected


def _identity_coverage(track_identities: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    for key in IDENTITY_KEYS:
        tracks_with_key = [
            track
            for track, identity in track_identities.items()
            if _text(identity.get(key))
        ]
        coverage[key] = {
            "count": len(tracks_with_key),
            "tracks": tracks_with_key,
        }
    return coverage


def _extract_identity(payload: Mapping[str, Any]) -> dict[str, str]:
    metadata = _to_dict(payload.get("metadata"))
    identity_payload = _to_dict(payload.get("identity"))
    scenario = _to_dict(payload.get("scenario"))
    source = _to_dict(payload.get("source"))
    route = _to_dict(payload.get("route"))

    scenario_id = _find_known_scenario(payload, scenario, metadata, identity_payload)
    known = dict(SPRINT4_SCENARIOS.get(scenario_id, {}))

    identity = {
        "domain": _first_text(
            payload.get("domain"),
            metadata.get("domain"),
            identity_payload.get("domain"),
            payload.get("domain_key"),
            payload.get("scenario_domain"),
            scenario.get("domain"),
            known.get("domain"),
        ),
        "scenario_id": _first_text(
            payload.get("scenario_id"),
            metadata.get("scenario_id"),
            identity_payload.get("scenario_id"),
            scenario.get("scenario_id"),
            scenario.get("id"),
            known.get("scenario_id"),
        ),
        "scenario_name": _first_text(
            payload.get("scenario_name"),
            metadata.get("scenario_name"),
            identity_payload.get("scenario_name"),
            scenario.get("scenario_name"),
            scenario.get("name"),
            known.get("scenario_name"),
        ),
        "upstream_track": _first_text(
            payload.get("upstream_track"),
            payload.get("benchmark_track"),
            payload.get("opponent_profile"),
            metadata.get("upstream_track"),
            metadata.get("benchmark_track"),
            identity_payload.get("upstream_track"),
            route.get("upstream_track"),
            route.get("opponent_profile"),
            known.get("upstream_track"),
        ),
        "category": _first_text(
            payload.get("category"),
            payload.get("attack_category"),
            metadata.get("category"),
            identity_payload.get("category"),
            scenario.get("category"),
            scenario.get("attack_category"),
            known.get("category"),
        ),
        "adapter": _first_text(
            payload.get("adapter"),
            payload.get("adapter_name"),
            metadata.get("adapter"),
            identity_payload.get("adapter"),
            route.get("adapter"),
            route.get("adapter_name"),
            known.get("adapter"),
        ),
        "assessment_mode": _first_text(
            payload.get("assessment_mode"),
            metadata.get("assessment_mode"),
            identity_payload.get("assessment_mode"),
            scenario.get("assessment_mode"),
            route.get("assessment_mode"),
            known.get("assessment_mode"),
            default=DEFAULT_ASSESSMENT_MODE if scenario_id else "",
        ),
        "scenario_family": _first_text(
            payload.get("scenario_family"),
            metadata.get("scenario_family"),
            identity_payload.get("scenario_family"),
            scenario.get("scenario_family"),
            route.get("scenario_family"),
            known.get("scenario_family"),
            default=DEFAULT_SCENARIO_FAMILY if scenario_id else "",
        ),
        "benchmark": _first_text(
            payload.get("benchmark"),
            metadata.get("benchmark"),
            identity_payload.get("benchmark"),
            source.get("benchmark"),
            known.get("benchmark"),
            default=BENCHMARK_NAME if scenario_id else "",
        ),
        "selected_opponent": _first_text(
            payload.get("selected_opponent"),
            metadata.get("selected_opponent"),
            identity_payload.get("selected_opponent"),
            route.get("selected_opponent"),
            route.get("opponent"),
            source.get("selected_opponent"),
            known.get("selected_opponent"),
        ),
        "source_url": _first_text(
            payload.get("source_url"),
            metadata.get("source_url"),
            identity_payload.get("source_url"),
            source.get("source_url"),
            source.get("url"),
            source.get("repo"),
            known.get("source_url"),
        ),
    }

    if not identity["selected_opponent"]:
        identity["selected_opponent"] = identity["upstream_track"]

    normalized_id = _scenario_id_from_any(identity["scenario_id"] or identity["scenario_name"])
    if normalized_id in SPRINT4_SCENARIOS:
        identity["scenario_id"] = normalized_id
        identity["scenario_name"] = identity["scenario_name"] or SPRINT4_SCENARIOS[normalized_id]["scenario_name"]

    return identity


def _identity_from_mapping(value: Mapping[str, Any]) -> dict[str, str]:
    return {key: _text(value.get(key)) for key in IDENTITY_KEYS}


def _identity_validation(identity: Mapping[str, Any]) -> dict[str, Any]:
    domain = _text(identity.get("domain"))
    scenario_id = _text(identity.get("scenario_id"))
    expected_scenario_for_domain = DOMAIN_TO_SCENARIO_ID.get(domain, "")

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
        if not _text(identity.get(key))
    ]

    return {
        "domain_known": domain in DOMAIN_TO_SCENARIO_ID,
        "scenario_known": scenario_id in SPRINT4_SCENARIOS,
        "domain_scenario_match": (
            not domain
            or not scenario_id
            or not expected_scenario_for_domain
            or expected_scenario_for_domain == scenario_id
        ),
        "expected_scenario_for_domain": expected_scenario_for_domain or None,
        "missing_identity": missing_identity,
        "supported_domains": sorted(DOMAIN_TO_SCENARIO_ID),
        "supported_scenarios": sorted(SPRINT4_SCENARIOS),
    }


def _find_known_scenario(
    payload: Mapping[str, Any],
    scenario: Mapping[str, Any],
    metadata: Mapping[str, Any],
    identity_payload: Mapping[str, Any],
) -> str:
    candidates = (
        payload.get("scenario_id"),
        payload.get("scenario_name"),
        payload.get("scenario"),
        payload.get("env_name"),
        metadata.get("scenario_id"),
        metadata.get("scenario_name"),
        identity_payload.get("scenario_id"),
        identity_payload.get("scenario_name"),
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
        metadata.get("domain"),
        identity_payload.get("domain"),
        payload.get("domain_key"),
        payload.get("scenario_domain"),
        scenario.get("domain"),
    )
    return DOMAIN_TO_SCENARIO_ID.get(domain, "")


def _scenario_id_from_any(value: Any) -> str:
    key = _compact_key(value)
    if not key:
        return ""
    return SCENARIO_ALIASES.get(key, key)


def _compact_key(value: Any) -> str:
    return _slug_key(value).replace("_", "")


def _slug_key(value: Any) -> str:
    text = _text(value).lower()
    if not text:
        return ""
    cleaned: list[str] = []
    previous_was_sep = False
    for char in text:
        if char.isalnum():
            cleaned.append(char)
            previous_was_sep = False
        elif not previous_was_sep:
            cleaned.append("_")
            previous_was_sep = True
    return "".join(cleaned).strip("_")


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return dict(asdict(value))

    for method_name in ("as_dict", "to_dict", "model_dump", "dict"):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            dumped = method()
        except Exception:
            continue
        if isinstance(dumped, Mapping):
            return dict(dumped)

    return {}


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


def _get_result_track(result: TrackResult) -> str:
    return _text(getattr(result, "track", ""), default="unknown")


def _get_result_status(result: TrackResult) -> str:
    return _text(getattr(result, "status", ""), default="unknown")


def _get_result_summary(result: TrackResult) -> str:
    return _text(getattr(result, "summary", ""))


def _get_result_score(result: TrackResult) -> float:
    try:
        return float(getattr(result, "score", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _get_result_details(result: TrackResult) -> dict[str, Any]:
    return _to_dict(getattr(result, "details", None))


def _get_result_metadata(result: TrackResult) -> dict[str, Any]:
    metadata = _to_dict(getattr(result, "metadata", None))
    if metadata:
        return metadata

    as_dict = _to_dict(result)
    return _to_dict(as_dict.get("metadata"))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
