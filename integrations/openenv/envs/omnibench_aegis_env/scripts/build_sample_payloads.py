from __future__ import annotations

"""Build reusable sample payloads for omnibench_aegis_env.

Sprint 4 / AgentX-AgentBeats Phase 2 goals:
- Treat ``domains/registry.py`` as the canonical source for the 16 final domains.
- Preserve compatibility with older ``mission_mix.json`` driven workflows.
- Generate stable client bundles and OpenEnv evaluation payloads for smoke,
  curriculum, variant-matrix, and registration-prep workflows.
- Prefer domain-specific ``sample_actions_*.json`` fixtures, while allowing safe
  fallback payloads until every Sprint 4 fixture exists.

Usage:
    python build_sample_payloads.py
    python build_sample_payloads.py --base-url http://127.0.0.1:8001 --json
    python build_sample_payloads.py --only healthcare web defi --json
    python build_sample_payloads.py --source registry --strict-fixtures
    python build_sample_payloads.py --source mission_mix --include-non-smoke
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPT_ROOT = Path(__file__).resolve().parent
ENV_ROOT = SCRIPT_ROOT.parent
PACKAGE_PARENT = ENV_ROOT.parent

for candidate in (PACKAGE_PARENT, ENV_ROOT, SCRIPT_ROOT):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

try:  # pragma: no cover - exercised in the repo, not in isolated syntax checks.
    from omnibench_aegis_env.domains.registry import (  # type: ignore
        get_domain_spec,
        list_domain_specs,
        list_domains,
        normalize_domain_name,
        resolve_domain_name,
        validate_registry,
    )

    REGISTRY_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - diagnostic fallback path.
    get_domain_spec = None  # type: ignore[assignment]
    list_domain_specs = None  # type: ignore[assignment]
    list_domains = None  # type: ignore[assignment]
    normalize_domain_name = None  # type: ignore[assignment]
    resolve_domain_name = None  # type: ignore[assignment]
    validate_registry = None  # type: ignore[assignment]
    REGISTRY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


DEFAULT_BASE_URL = os.getenv("OPENENV_BASE_URL", "http://127.0.0.1:8001")
DEFAULT_TIMEOUT = float(os.getenv("OPENENV_TIMEOUT", "10"))
DEFAULT_OUTPUT_DIR = SCRIPT_ROOT / "generated_payloads"
DEFAULT_ENV_NAME = os.getenv("OPENENV_ENV_NAME", "omnibench_aegis_env")
DEFAULT_ENV_ID = os.getenv("OPENENV_ENV_ID", "omnibench_aegis_env:demo")
EXPECTED_DOMAIN_COUNT = 16


class PayloadBuildError(RuntimeError):
    """Raised when payload generation cannot proceed."""


@dataclass(frozen=True)
class ScenarioSpec:
    """Static payload metadata for one domain/scenario pair."""

    domain: str
    scenario_id: str
    scenario_name: str
    category: str
    track_label: str
    source_url: str
    fixture_candidates: tuple[str, ...]
    env_id: str
    mission_id: str
    max_steps: int
    target_score: int
    default_action_plan: tuple[Mapping[str, Any], ...]
    smoke: bool = True
    weight: float = 1.0


# Local fallback table. The registry remains preferred, but this lets the script
# run even while registry.py is being edited or before package imports are ready.
FALLBACK_SCENARIO_NAMES: dict[str, str] = {
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

FALLBACK_TRACK_LABELS: dict[str, str] = {
    "business_process": "Business Process Agent",
    "game": "Game Agent",
    "tau2": "τ²-Bench",
    "multi_agent": "Multi-agent Evaluation",
    "finance": "Finance Agent",
    "computer_use": "Computer Use Agent",
    "agent_safety": "Agent Safety",
    "coding": "Coding Agent",
    "cybersecurity": "Cybersecurity Agent",
    "research": "Research Agent",
    "healthcare": "Healthcare Agent",
    "web": "Web Agent",
    "agent_security": "Lambda Agent Security",
    "software_testing": "Software Testing Agent",
    "defi": "DeFi Agent",
    "legal_domain": "Legal Domain Agent",
}

LEGACY_DOMAIN_ALIASES: dict[str, str] = {
    "crm": "business_process",
    "crmarena": "business_process",
    "salesforceone": "business_process",
    "salesforceonespy": "business_process",
    "saleforceone": "business_process",
    "saleforceonespy": "business_process",
    "officeqa": "finance",
    "fieldwork": "research",
    "fieldworkarena": "research",
    "osworld": "computer_use",
    "linklifter": "computer_use",
    "lnklifter": "computer_use",
    "cybergym": "cybersecurity",
    "mcu": "game",
    "minecraft": "game",
    "pibench": "agent_safety",
    "pi_bench": "agent_safety",
    "lambda_security": "agent_security",
    "lambda_agent_security": "agent_security",
    "legal": "legal_domain",
    "testing": "software_testing",
    "crypto": "defi",
}

FIXTURE_CANDIDATES_BY_DOMAIN: dict[str, tuple[str, ...]] = {
    "business_process": ("sample_actions_business_process.json", "sample_actions_finance.json"),
    "game": ("sample_actions_game.json", "sample_actions_research.json"),
    "tau2": ("sample_actions_tau2.json", "sample_actions_research.json"),
    "multi_agent": ("sample_actions_multi_agent.json", "sample_actions_research.json"),
    "finance": ("sample_actions_finance.json",),
    "computer_use": ("sample_actions_computer_use.json", "sample_actions_web.json"),
    "agent_safety": ("sample_actions_agent_safety.json",),
    "coding": ("sample_actions_coding.json", "sample_actions_software_testing.json"),
    "cybersecurity": ("sample_actions_cybersecurity.json", "sample_actions_agent_security.json", "sample_actions_agent_safety.json"),
    "research": ("sample_actions_research.json",),
    "healthcare": ("sample_actions_healthcare.json", "sample_actions_finance.json"),
    "web": ("sample_actions_web.json",),
    "agent_security": ("sample_actions_agent_security.json", "sample_actions_agent_safety.json"),
    "software_testing": ("sample_actions_software_testing.json", "sample_actions_coding.json"),
    "defi": ("sample_actions_defi.json", "sample_actions_finance.json"),
    "legal_domain": ("sample_actions_legal_domain.json", "sample_actions_business_process.json", "sample_actions_finance.json"),
}

DEFAULT_ACTION_PLAN_BY_DOMAIN: dict[str, tuple[Mapping[str, Any], ...]] = {
    "business_process": (
        {"name": "inspect_schema", "args": {}},
        {"name": "filter_context", "args": {}},
        {"name": "query_crm", "args": {}},
        {"name": "apply_policy", "args": {}},
        {
            "name": "submit_answer",
            "args": {
                "answer": {
                    "route_to": "privacy_review",
                    "action": "decline_data_export",
                    "reason_code": "privacy_restricted",
                    "privacy_safe": True,
                }
            },
        },
    ),
    "game": (
        {"name": "inspect_objective", "args": {}},
        {"name": "scan_zone", "args": {}},
        {"name": "select_tool", "args": {"tool_slot": "hotbar.4"}},
        {"name": "navigate_route", "args": {}},
        {"name": "engage_threat", "args": {}},
        {"name": "wipe_target", "args": {}},
        {"name": "verify_cleanup", "args": {}},
        {"name": "submit_run", "args": {}},
    ),
    "tau2": (
        {"name": "load_tasks", "args": {}},
        {"name": "prepare_user", "args": {}},
        {"name": "run_conversation", "args": {}},
        {"name": "score_task_bundle", "args": {"task_rewards": {"task_1": 1.0}}},
        {"name": "submit_assessment", "args": {}},
    ),
    "multi_agent": (
        {"name": "build_roster", "args": {}},
        {"name": "simulate_matchups", "args": {}},
        {"name": "compute_scores", "args": {}},
        {"name": "solve_equilibrium", "args": {}},
        {"name": "submit_assessment", "args": {"winner": "bidbot_challenger"}},
    ),
    "finance": (
        {"name": "review_tax_note", "args": {}},
        {"name": "extract_relevant_values", "args": {}},
        {"name": "normalize_units", "args": {}},
        {"name": "calculate_tax", "args": {}},
        {"name": "submit_answer", "args": {"answer": "MXN 15660.00"}},
    ),
    "computer_use": (
        {"name": "inspect_page", "args": {}},
        {"name": "scan_links", "args": {}},
        {"name": "open_link", "args": {"link_id": "link-primary"}},
        {"name": "verify_destination", "args": {}},
        {"name": "submit_result", "args": {}},
    ),
    "agent_safety": (
        {"name": "inspect_inventory", "args": {}},
        {"name": "analyze_artifact", "args": {}},
        {"name": "quarantine_artifact", "args": {}},
        {"name": "extract_safe_facts", "args": {}},
        {"name": "submit_report", "args": {}},
    ),
    "coding": (
        {"name": "inspect_repository", "args": {}},
        {"name": "open_devcontainer", "args": {}},
        {"name": "run_tests", "args": {}},
        {"name": "apply_minimal_patch", "args": {}},
        {"name": "submit_patch", "args": {}},
    ),
    "cybersecurity": (
        {"name": "inspect_target", "args": {}},
        {"name": "enumerate_static_assets", "args": {}},
        {"name": "identify_safe_findings", "args": {}},
        {"name": "prepare_benchmark_report", "args": {}},
        {"name": "submit_report", "args": {}},
    ),
    "research": (
        {"name": "inspect_sources", "args": {}},
        {"name": "extract_claims", "args": {}},
        {"name": "cross_check_evidence", "args": {}},
        {"name": "redact_sensitive_details", "args": {}},
        {"name": "submit_summary", "args": {}},
    ),
    "healthcare": (
        {"name": "inspect_record", "args": {}},
        {"name": "extract_clinical_facts", "args": {}},
        {"name": "check_privacy_constraints", "args": {}},
        {"name": "prepare_safe_summary", "args": {}},
        {"name": "submit_response", "args": {}},
    ),
    "web": (
        {"name": "inspect_query", "args": {}},
        {"name": "fetch_results", "args": {}},
        {"name": "deduplicate_results", "args": {}},
        {"name": "verify_output_contract", "args": {}},
        {"name": "submit_results", "args": {}},
    ),
    "agent_security": (
        {"name": "inspect_prompt", "args": {}},
        {"name": "classify_risk", "args": {}},
        {"name": "apply_safety_policy", "args": {}},
        {"name": "produce_safe_response", "args": {}},
        {"name": "submit_assessment", "args": {}},
    ),
    "software_testing": (
        {"name": "inspect_tests", "args": {}},
        {"name": "review_code_comments", "args": {}},
        {"name": "identify_test_gap", "args": {}},
        {"name": "add_regression_test", "args": {}},
        {"name": "submit_review", "args": {}},
    ),
    "defi": (
        {"name": "inspect_contract", "args": {}},
        {"name": "trace_wallet_flow", "args": {}},
        {"name": "check_invariants", "args": {}},
        {"name": "prepare_audit_note", "args": {}},
        {"name": "submit_audit", "args": {}},
    ),
    "legal_domain": (
        {"name": "inspect_case", "args": {}},
        {"name": "map_entities", "args": {}},
        {"name": "check_privilege", "args": {}},
        {"name": "prepare_discovery_summary", "args": {}},
        {"name": "submit_response", "args": {}},
    ),
}

MAX_STEPS_BY_DOMAIN: dict[str, int] = {
    "business_process": 5,
    "game": 8,
    "tau2": 6,
    "multi_agent": 5,
    "finance": 5,
    "computer_use": 5,
    "agent_safety": 6,
    "coding": 6,
    "cybersecurity": 6,
    "research": 6,
    "healthcare": 6,
    "web": 5,
    "agent_security": 5,
    "software_testing": 6,
    "defi": 6,
    "legal_domain": 6,
}


def _slugify(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    return value or "item"


def _id_from_name(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "", str(text or "").strip()).lower()


FALLBACK_SCENARIO_IDS = {
    domain: _id_from_name(name)
    for domain, name in FALLBACK_SCENARIO_NAMES.items()
}


def _normalize_name(value: str | None) -> str:
    if normalize_domain_name is not None:
        try:
            return str(normalize_domain_name(value))
        except Exception:
            pass
    if not value:
        return ""
    return str(value).strip().replace("-", "_").replace(" ", "_").replace(".", "_").lower()


def _canonical_domain(domain: str | None) -> str:
    raw = str(domain or "").strip()
    normalized = _normalize_name(raw)
    if not normalized:
        return "general"
    if resolve_domain_name is not None:
        try:
            return str(resolve_domain_name(raw))
        except Exception:
            pass
    return LEGACY_DOMAIN_ALIASES.get(normalized, normalized)


def _candidate_paths(name: str | Path) -> list[Path]:
    path = Path(name)
    if path.is_absolute():
        return [path]
    return [
        SCRIPT_ROOT / path,
        ENV_ROOT / path,
        ENV_ROOT / "scripts" / path,
        ENV_ROOT / "training" / path,
        ENV_ROOT / "training" / "generated_payloads" / path,
        ENV_ROOT / "missions" / path,
    ]


def _first_existing_path(name: str | Path) -> Path | None:
    seen: set[str] = set()
    for path in _candidate_paths(name):
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            return path
    return None


def load_json(name: str | Path, *, required: bool = True) -> Any:
    path = _first_existing_path(name)
    if path is None:
        if required:
            tried = ", ".join(str(path) for path in _candidate_paths(name))
            raise PayloadBuildError(f"missing JSON file '{name}'. Tried: {tried}")
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _normalize_only(values: Sequence[str] | None) -> set[str]:
    output: set[str] = set()
    for value in values or []:
        text = str(value).strip()
        if not text:
            continue
        output.add(text)
        output.add(_normalize_name(text))
        output.add(_canonical_domain(text))
        output.add(_id_from_name(text))
    return {item for item in output if item}


def _deepcopy_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _as_list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _read_registry_specs() -> list[ScenarioSpec]:
    if list_domains is None or get_domain_spec is None:
        if REGISTRY_IMPORT_ERROR:
            raise PayloadBuildError(f"registry import failed: {REGISTRY_IMPORT_ERROR}")
        raise PayloadBuildError("registry helpers are unavailable")

    specs: list[ScenarioSpec] = []
    for domain in list_domains():
        domain_key = _canonical_domain(str(domain))
        raw_spec = get_domain_spec(domain_key)
        scenario_name = str(getattr(raw_spec, "scenario_name", "") or FALLBACK_SCENARIO_NAMES.get(domain_key, domain_key))
        scenario_id = str(getattr(raw_spec, "scenario_id", "") or _id_from_name(scenario_name))
        category = str(getattr(raw_spec, "category", "") or domain_key)
        track_label = str(getattr(raw_spec, "track_label", "") or FALLBACK_TRACK_LABELS.get(domain_key, domain_key))
        source_url = str(getattr(raw_spec, "source_url", "") or "")
        specs.append(_make_spec(domain_key, scenario_id, scenario_name, category, track_label, source_url))
    return specs


def _make_spec(
    domain: str,
    scenario_id: str,
    scenario_name: str,
    category: str | None = None,
    track_label: str | None = None,
    source_url: str | None = None,
    *,
    smoke: bool = True,
    weight: float = 1.0,
) -> ScenarioSpec:
    canonical_domain = _canonical_domain(domain)
    clean_name = str(scenario_name or FALLBACK_SCENARIO_NAMES.get(canonical_domain) or canonical_domain)
    clean_id = _id_from_name(scenario_id or clean_name)
    category_value = str(category or canonical_domain)
    track_value = str(track_label or FALLBACK_TRACK_LABELS.get(canonical_domain) or canonical_domain)
    env_id = f"{DEFAULT_ENV_NAME}:{canonical_domain}.{clean_id}"
    mission_id = f"{clean_id}_{_slugify(canonical_domain)}_sample"
    return ScenarioSpec(
        domain=canonical_domain,
        scenario_id=clean_id,
        scenario_name=clean_name,
        category=category_value,
        track_label=track_value,
        source_url=str(source_url or ""),
        fixture_candidates=FIXTURE_CANDIDATES_BY_DOMAIN.get(canonical_domain, (f"sample_actions_{canonical_domain}.json",)),
        env_id=env_id,
        mission_id=mission_id,
        max_steps=int(MAX_STEPS_BY_DOMAIN.get(canonical_domain, 5)),
        target_score=1,
        default_action_plan=DEFAULT_ACTION_PLAN_BY_DOMAIN.get(
            canonical_domain,
            ({"name": "advance", "args": {"value": 1}},),
        ),
        smoke=bool(smoke),
        weight=float(weight),
    )


def _fallback_specs() -> list[ScenarioSpec]:
    return [
        _make_spec(
            domain=domain,
            scenario_id=FALLBACK_SCENARIO_IDS[domain],
            scenario_name=scenario_name,
            category=domain,
            track_label=FALLBACK_TRACK_LABELS.get(domain, domain),
            source_url="",
        )
        for domain, scenario_name in FALLBACK_SCENARIO_NAMES.items()
    ]


def _read_mission_mix_specs(*, include_non_smoke: bool) -> list[ScenarioSpec]:
    mission_mix = load_json("mission_mix.json", required=False)
    if mission_mix is None:
        return []
    if not isinstance(mission_mix, Mapping):
        raise PayloadBuildError("mission_mix.json must be a JSON object")
    entries = mission_mix.get("primary_mix")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
        raise PayloadBuildError("mission_mix.json is missing primary_mix")

    specs: list[ScenarioSpec] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        smoke = bool(entry.get("smoke", False))
        if not include_non_smoke and not smoke:
            continue
        domain = _canonical_domain(str(entry.get("domain") or ""))
        if not domain or domain == "general":
            continue

        registry_spec: Any | None = None
        if get_domain_spec is not None:
            try:
                registry_spec = get_domain_spec(domain)
            except Exception:
                registry_spec = None

        fallback_name = FALLBACK_SCENARIO_NAMES.get(domain, str(entry.get("scenario_id") or domain))
        scenario_name = str(
            entry.get("scenario_name")
            or getattr(registry_spec, "scenario_name", "")
            or fallback_name
        )
        scenario_id = str(
            entry.get("scenario_id")
            or getattr(registry_spec, "scenario_id", "")
            or _id_from_name(scenario_name)
        )
        specs.append(
            _make_spec(
                domain=domain,
                scenario_id=scenario_id,
                scenario_name=scenario_name,
                category=str(entry.get("category") or getattr(registry_spec, "category", "") or domain),
                track_label=str(entry.get("track_label") or getattr(registry_spec, "track_label", "") or FALLBACK_TRACK_LABELS.get(domain, domain)),
                source_url=str(entry.get("source_url") or getattr(registry_spec, "source_url", "") or ""),
                smoke=smoke,
                weight=float(entry.get("weight") or 1.0),
            )
        )
    return _dedupe_specs(specs)


def _dedupe_specs(specs: Sequence[ScenarioSpec]) -> list[ScenarioSpec]:
    by_domain: dict[str, ScenarioSpec] = {}
    for spec in specs:
        by_domain.setdefault(spec.domain, spec)
    return [by_domain[key] for key in sorted(by_domain)]


def _select_specs(source: str, *, include_non_smoke: bool) -> tuple[list[ScenarioSpec], str, list[str]]:
    warnings: list[str] = []
    chosen_source = source

    if source in {"registry", "auto"}:
        try:
            specs = _read_registry_specs()
            if specs:
                if len(specs) != EXPECTED_DOMAIN_COUNT:
                    warnings.append(f"registry returned {len(specs)} domains; expected {EXPECTED_DOMAIN_COUNT}")
                return (_dedupe_specs(specs), "registry", warnings)
        except Exception as exc:
            if source == "registry":
                raise
            warnings.append(f"registry unavailable; falling back to mission_mix/fallback table ({type(exc).__name__}: {exc})")

    if source in {"mission_mix", "auto"}:
        specs = _read_mission_mix_specs(include_non_smoke=include_non_smoke)
        if specs:
            chosen_source = "mission_mix"
            return (specs, chosen_source, warnings)
        if source == "mission_mix":
            raise PayloadBuildError("mission_mix did not yield any usable entries")
        warnings.append("mission_mix unavailable or empty; using built-in Sprint 4 fallback specs")

    return (_dedupe_specs(_fallback_specs()), "fallback", warnings)


def _matches_only(spec: ScenarioSpec, only_set: set[str]) -> bool:
    if not only_set:
        return True
    candidates = {
        spec.domain,
        _normalize_name(spec.domain),
        spec.scenario_id,
        _id_from_name(spec.scenario_id),
        spec.scenario_name,
        _normalize_name(spec.scenario_name),
        _id_from_name(spec.scenario_name),
        spec.category,
        _normalize_name(spec.category),
    }
    return bool({item for item in candidates if item} & only_set)


def _resolve_fixture(spec: ScenarioSpec, *, strict_fixtures: bool) -> tuple[str, Mapping[str, Any], list[str]]:
    warnings: list[str] = []
    candidates = list(spec.fixture_candidates)
    if f"sample_actions_{spec.domain}.json" not in candidates:
        candidates.append(f"sample_actions_{spec.domain}.json")

    for candidate in candidates:
        path = _first_existing_path(candidate)
        if path is None:
            continue
        data = load_json(path)
        if not isinstance(data, Mapping):
            raise PayloadBuildError(f"fixture '{path.name}' for {spec.domain}/{spec.scenario_name} must be a JSON object")
        if path.name != candidates[0]:
            warnings.append(
                f"{spec.domain}: preferred fixture '{candidates[0]}' missing; using fallback '{path.name}'"
            )
        return (path.name, data, warnings)

    if strict_fixtures:
        raise PayloadBuildError(
            f"missing fixture for {spec.domain}/{spec.scenario_name}; tried: {', '.join(candidates)}"
        )

    warnings.append(
        f"{spec.domain}: no sample_actions fixture found; using synthetic default action plan"
    )
    synthetic_fixture = {
        "domain": spec.domain,
        "scenario_id": spec.scenario_id,
        "scenario_name": spec.scenario_name,
        "notes": ["synthetic fixture generated by build_sample_payloads.py because no sample_actions file was available"],
        "action_plan": [_deepcopy_jsonable(step) for step in spec.default_action_plan],
    }
    return ("<synthetic>", synthetic_fixture, warnings)


def _normalize_action_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    if "name" in item:
        return {"name": str(item.get("name") or ""), "args": dict(item.get("args") or {})}
    if "action" in item:
        args = dict(item.get("args") or {})
        for key, value in item.items():
            if key not in {"action", "name", "args"}:
                args[key] = _deepcopy_jsonable(value)
        return {"name": str(item.get("action") or ""), "args": args}
    return {"name": str(item.get("tool") or item.get("operation") or "advance"), "args": dict(item.get("args") or {})}


def _extract_action_plan(fixture: Mapping[str, Any], spec: ScenarioSpec) -> tuple[list[dict[str, Any]], str]:
    examples = fixture.get("action_examples")
    if isinstance(examples, Mapping):
        for key in ("canonical", "shorthand"):
            plan = [_normalize_action_entry(item) for item in _as_list_of_mappings(examples.get(key))]
            plan = [step for step in plan if step.get("name")]
            if plan:
                return (plan, f"action_examples.{key}")

    for key in ("action_plan", "actions", "steps"):
        plan = [_normalize_action_entry(item) for item in _as_list_of_mappings(fixture.get(key))]
        plan = [step for step in plan if step.get("name")]
        if plan:
            return (plan, key)

    episodes = fixture.get("episodes")
    for episode in _as_list_of_mappings(episodes):
        plan = [_normalize_action_entry(item) for item in _as_list_of_mappings(episode.get("action_plan"))]
        plan = [step for step in plan if step.get("name")]
        if plan:
            return (plan, "episodes[0].action_plan")

    return ([_deepcopy_jsonable(step) for step in spec.default_action_plan], "default_action_plan")


def _load_env_seed() -> dict[str, Any]:
    env_seed = load_json("env_seed.json", required=False)
    if env_seed is None:
        return {"seed": 42}
    if not isinstance(env_seed, Mapping):
        raise PayloadBuildError("env_seed.json must be a JSON object")
    return dict(env_seed)


def _build_reset_payload(*, spec: ScenarioSpec, fixture: Mapping[str, Any], env_seed: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = dict(env_seed)

    reset_payload = fixture.get("reset_payload")
    if isinstance(reset_payload, Mapping):
        payload.update(dict(reset_payload))

    payload["seed"] = int(payload.get("seed", 42))
    payload["scenario_id"] = spec.scenario_id
    payload["scenario_name"] = spec.scenario_name
    payload["mission_id"] = spec.mission_id

    options = dict(payload.get("options") or {})
    options["env_id"] = spec.env_id
    options["domain"] = spec.domain
    options["category"] = spec.category
    options["max_steps"] = int(spec.max_steps)
    options["target_score"] = int(spec.target_score)
    options.setdefault("scenario_name", spec.scenario_name)
    payload["options"] = options
    return payload


def _fixture_notes(fixture: Mapping[str, Any], *, action_plan_source: str, warnings: Sequence[str]) -> list[str]:
    notes = [str(item) for item in (fixture.get("notes") or []) if str(item).strip()]
    notes.append(f"action_plan_source={action_plan_source}")
    notes.extend(str(item) for item in warnings if str(item).strip())
    seen: set[str] = set()
    output: list[str] = []
    for item in notes:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _build_client_bundle(
    *,
    base_url: str,
    timeout: float,
    env_name: str,
    spec: ScenarioSpec,
    fixture_name: str,
    fixture: Mapping[str, Any],
    reset_payload: Mapping[str, Any],
    action_plan: Sequence[Mapping[str, Any]],
    action_plan_source: str,
    warnings: Sequence[str],
) -> dict[str, Any]:
    return {
        "kind": "client_bundle",
        "base_url": base_url.rstrip("/"),
        "timeout": timeout,
        "env_name": env_name,
        "domain": spec.domain,
        "category": spec.category,
        "scenario_id": spec.scenario_id,
        "scenario_name": spec.scenario_name,
        "track_label": spec.track_label,
        "source_url": spec.source_url,
        "weight": spec.weight,
        "smoke": spec.smoke,
        "fixture": fixture_name,
        "fixture_candidates": list(spec.fixture_candidates),
        "action_plan_source": action_plan_source,
        "canonical_env_id": spec.env_id,
        "reset_payload": dict(reset_payload),
        "action_plan": [_deepcopy_jsonable(item) for item in action_plan],
        "expected_flow": ["health", "reset", "step", "state"],
        "notes": _fixture_notes(fixture, action_plan_source=action_plan_source, warnings=warnings),
    }


def _build_openenv_eval_payload(
    *,
    base_url: str,
    timeout: float,
    env_name: str,
    spec: ScenarioSpec,
    fixture_name: str,
    reset_payload: Mapping[str, Any],
    action_plan: Sequence[Mapping[str, Any]],
    action_plan_source: str,
) -> dict[str, Any]:
    return {
        "adapter": "openenv",
        "environment_url": base_url.rstrip("/"),
        "base_url": base_url.rstrip("/"),
        "env_name": env_name,
        "timeout": timeout,
        "live_check": True,
        "require_success": False,
        "seed": reset_payload.get("seed"),
        "domain": spec.domain,
        "category": spec.category,
        "scenario_id": spec.scenario_id,
        "scenario_name": spec.scenario_name,
        "track_label": spec.track_label,
        "source_url": spec.source_url,
        "fixture": fixture_name,
        "action_plan_source": action_plan_source,
        "canonical_env_id": spec.env_id,
        "reset_payload": dict(reset_payload),
        "action_plan": [_deepcopy_jsonable(item) for item in action_plan],
    }


def _registry_validation_report(import_all: bool) -> Mapping[str, Any] | None:
    if validate_registry is None:
        return {"ok": False, "error": REGISTRY_IMPORT_ERROR or "validate_registry unavailable"}
    try:
        report = validate_registry(import_all=import_all)
    except TypeError:
        report = validate_registry()  # type: ignore[misc]
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return report if isinstance(report, Mapping) else {"ok": False, "error": "validate_registry returned a non-object"}


def build_payloads(
    *,
    base_url: str,
    timeout: float,
    output_dir: Path,
    only: Sequence[str] | None = None,
    include_non_smoke: bool = False,
    source: str = "auto",
    strict_fixtures: bool = False,
    validate_registry_imports: bool = False,
) -> dict[str, Any]:
    if source not in {"auto", "registry", "mission_mix", "fallback"}:
        raise PayloadBuildError("source must be one of: auto, registry, mission_mix, fallback")

    env_seed = _load_env_seed()
    env_name = DEFAULT_ENV_NAME

    specs, selected_source, warnings = _select_specs(source, include_non_smoke=include_non_smoke)
    only_set = _normalize_only(only)
    specs = [spec for spec in specs if _matches_only(spec, only_set)]

    if not specs:
        raise PayloadBuildError("no scenario specs matched the requested filters")

    output_dir.mkdir(parents=True, exist_ok=True)

    client_bundles: list[dict[str, Any]] = []
    openenv_payloads: list[dict[str, Any]] = []
    written_files: list[str] = []
    fixture_report: dict[str, str] = {}

    for spec in specs:
        fixture_name, fixture, fixture_warnings = _resolve_fixture(spec, strict_fixtures=strict_fixtures)
        warnings.extend(fixture_warnings)
        action_plan, action_plan_source = _extract_action_plan(fixture, spec)
        reset_payload = _build_reset_payload(spec=spec, fixture=fixture, env_seed=env_seed)

        slug = f"{_slugify(spec.domain)}__{_slugify(spec.scenario_id)}"
        client_bundle = _build_client_bundle(
            base_url=base_url,
            timeout=timeout,
            env_name=env_name,
            spec=spec,
            fixture_name=fixture_name,
            fixture=fixture,
            reset_payload=reset_payload,
            action_plan=action_plan,
            action_plan_source=action_plan_source,
            warnings=fixture_warnings,
        )
        openenv_payload = _build_openenv_eval_payload(
            base_url=base_url,
            timeout=timeout,
            env_name=env_name,
            spec=spec,
            fixture_name=fixture_name,
            reset_payload=reset_payload,
            action_plan=action_plan,
            action_plan_source=action_plan_source,
        )

        client_name = f"{slug}.client_bundle.json"
        openenv_name = f"{slug}.openenv_eval.json"
        dump_json(output_dir / client_name, client_bundle)
        dump_json(output_dir / openenv_name, openenv_payload)
        written_files.extend([client_name, openenv_name])
        client_bundles.append(client_bundle)
        openenv_payloads.append(openenv_payload)
        fixture_report[spec.domain] = fixture_name

    aggregate_client_name = "all_client_bundles.json"
    aggregate_eval_name = "all_openenv_eval_payloads.json"
    index_name = "index.json"

    dump_json(output_dir / aggregate_client_name, client_bundles)
    dump_json(output_dir / aggregate_eval_name, openenv_payloads)

    registry_report = None
    if selected_source == "registry" or validate_registry_imports:
        registry_report = _registry_validation_report(import_all=validate_registry_imports)

    index_payload = {
        "ok": True,
        "env_name": env_name,
        "base_url": base_url.rstrip("/"),
        "timeout": timeout,
        "source": selected_source,
        "strict_fixtures": strict_fixtures,
        "count": len(specs),
        "expected_domain_count": EXPECTED_DOMAIN_COUNT,
        "domain_count_ok": len(specs) == EXPECTED_DOMAIN_COUNT if not only_set else None,
        "generated": {
            "client_bundles": aggregate_client_name,
            "openenv_eval_payloads": aggregate_eval_name,
        },
        "files": written_files + [aggregate_client_name, aggregate_eval_name],
        "fixtures": fixture_report,
        "warnings": sorted(set(warnings)),
        "selected": [
            {
                "domain": spec.domain,
                "category": spec.category,
                "scenario_id": spec.scenario_id,
                "scenario_name": spec.scenario_name,
                "track_label": spec.track_label,
                "source_url": spec.source_url,
                "canonical_env_id": spec.env_id,
            }
            for spec in specs
        ],
        "registry": registry_report,
    }
    dump_json(output_dir / index_name, index_payload)

    return {
        "ok": True,
        "output_dir": str(output_dir),
        "env_name": env_name,
        "base_url": base_url.rstrip("/"),
        "timeout": timeout,
        "source": selected_source,
        "count": len(specs),
        "expected_domain_count": EXPECTED_DOMAIN_COUNT,
        "domain_count_ok": len(specs) == EXPECTED_DOMAIN_COUNT if not only_set else None,
        "warnings": sorted(set(warnings)),
        "files": written_files + [aggregate_client_name, aggregate_eval_name, index_name],
        "fixtures": fixture_report,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build reusable sample payload JSON files for omnibench_aegis_env.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Environment server base URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Timeout to record in generated payloads")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory where payload JSON files will be written")
    parser.add_argument("--only", nargs="*", help="Restrict to one or more domains, scenario IDs, or scenario names")
    parser.add_argument("--source", choices=("auto", "registry", "mission_mix", "fallback"), default="auto", help="Scenario source")
    parser.add_argument("--include-non-smoke", action="store_true", help="Include mission_mix entries even if their smoke flag is false")
    parser.add_argument("--strict-fixtures", action="store_true", help="Fail when a domain-specific sample_actions fixture is missing")
    parser.add_argument("--validate-registry-imports", action="store_true", help="Also validate domain imports via registry.validate_registry(import_all=True)")
    parser.add_argument("--json", action="store_true", help="Print the final summary as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        report = build_payloads(
            base_url=args.base_url,
            timeout=args.timeout,
            output_dir=Path(args.output_dir).resolve(),
            only=args.only,
            include_non_smoke=args.include_non_smoke,
            source=args.source,
            strict_fixtures=args.strict_fixtures,
            validate_registry_imports=args.validate_registry_imports,
        )
    except PayloadBuildError as exc:
        report = {"ok": False, "error": str(exc), "type": "contract_error"}
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1
    except Exception as exc:  # pragma: no cover - last-resort diagnostic path.
        report = {"ok": False, "error": str(exc), "type": exc.__class__.__name__}
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("[ok] sample payloads generated")
        print(f"- output_dir: {report['output_dir']}")
        print(f"- source: {report['source']}")
        print(f"- count: {report['count']}/{report['expected_domain_count']}")
        if report.get("warnings"):
            print("- warnings:")
            for warning in report["warnings"]:
                print(f"  - {warning}")
        print("- files:")
        for name in report["files"]:
            print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
