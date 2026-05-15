from __future__ import annotations

"""Registry-aware smoke validation for omnibench_aegis_env.

Sprint 4 / AgentX-AgentBeats Phase 2 goals:
- Treat ``domains/registry.py`` as the canonical source for the 16 final domains.
- Keep a mission_mix fallback for older local setups.
- Validate health/reset/step/state without requiring every real domain backend to be
  fully implemented yet.
- Prefer domain-specific ``sample_actions_*.json`` fixtures, but keep safe fallbacks
  so incremental fixture work does not break discovery.

Usage:
    python smoke_test_all_domains.py
    python smoke_test_all_domains.py --base-url http://127.0.0.1:8001 --verbose
    python smoke_test_all_domains.py --only healthcare web defi --json
    python smoke_test_all_domains.py --source mission_mix --include-non-smoke
    python smoke_test_all_domains.py --strict-fixtures --fail-fast
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPT_ROOT = Path(__file__).resolve().parent
ENV_ROOT = SCRIPT_ROOT.parent
PACKAGE_PARENT = ENV_ROOT.parent

for path in (PACKAGE_PARENT, ENV_ROOT, SCRIPT_ROOT):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from omnibench_aegis_env.client import OpenEnvClient, OpenEnvClientError  # noqa: E402

try:  # noqa: SIM105 - explicit diagnostic state is useful here.
    from omnibench_aegis_env.domains.registry import (  # noqa: E402
        get_domain_spec,
        list_domain_specs,
        list_domains,
        normalize_domain_name,
        resolve_domain_name,
        validate_registry,
    )

    REGISTRY_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - diagnostic fallback path
    get_domain_spec = None  # type: ignore[assignment]
    list_domain_specs = None  # type: ignore[assignment]
    list_domains = None  # type: ignore[assignment]
    normalize_domain_name = None  # type: ignore[assignment]
    resolve_domain_name = None  # type: ignore[assignment]
    validate_registry = None  # type: ignore[assignment]
    REGISTRY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


DEFAULT_BASE_URL = os.getenv("OPENENV_BASE_URL", "http://127.0.0.1:8001")
DEFAULT_TIMEOUT = float(os.getenv("OPENENV_TIMEOUT", "10"))
DEFAULT_ENV_ID = os.getenv("OPENENV_ENV_ID", "omnibench_aegis_env:demo")
DEFAULT_ENV_NAME = os.getenv("OPENENV_ENV_NAME", "omnibench_aegis_env")
EXPECTED_DOMAIN_COUNT = 16


class SmokeFailure(RuntimeError):
    """Raised for smoke-contract failures."""


# Preferred fixture first; later entries are compatibility fallbacks.
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

# Backward-compatible aliases for old mission_mix entries.
LEGACY_DOMAIN_ALIASES: dict[str, str] = {
    "crm": "business_process",
    "crmarena": "business_process",
    "officeqa": "finance",
    "fieldwork": "research",
    "fieldworkarena": "research",
    "osworld": "computer_use",
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

DEFAULT_ACTION_BY_DOMAIN: dict[str, Mapping[str, Any]] = {
    "business_process": {"name": "inspect_schema", "args": {}},
    "game": {"name": "inspect_objective", "args": {}},
    "tau2": {"name": "load_tasks", "args": {}},
    "multi_agent": {"name": "build_roster", "args": {}},
    "finance": {"name": "review_tax_note", "args": {}},
    "computer_use": {"name": "inspect_page", "args": {}},
    "agent_safety": {"name": "inspect_inventory", "args": {}},
    "coding": {"name": "inspect_repository", "args": {}},
    "cybersecurity": {"name": "inspect_target", "args": {}},
    "research": {"name": "inspect_sources", "args": {}},
    "healthcare": {"name": "inspect_record", "args": {}},
    "web": {"name": "inspect_query", "args": {}},
    "agent_security": {"name": "inspect_prompt", "args": {}},
    "software_testing": {"name": "inspect_tests", "args": {}},
    "defi": {"name": "inspect_contract", "args": {}},
    "legal_domain": {"name": "inspect_case", "args": {}},
}


# Canonical scenario names are kept here as a fallback for mission_mix-only mode.
SCENARIO_NAME_BY_DOMAIN: dict[str, str] = {
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

def _slugify(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    return value or "item"


def _id_from_name(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "", str(text or "").strip()).lower()


SCENARIO_ID_BY_DOMAIN = {
    key: _id_from_name(value)
    for key, value in SCENARIO_NAME_BY_DOMAIN.items()
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


def _candidate_paths(name: str | Path) -> list[Path]:
    path = Path(name)
    if path.is_absolute():
        return [path]
    return [
        SCRIPT_ROOT / path,
        ENV_ROOT / path,
        ENV_ROOT / "scripts" / path,
        ENV_ROOT / "training" / path,
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
            tried = ", ".join(str(p) for p in _candidate_paths(name))
            raise SmokeFailure(f"missing JSON fixture '{name}'. Tried: {tried}")
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def is_subset(expected: Any, actual: Any, path: str = "$") -> list[str]:
    errors: list[str] = []

    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        for key, value in expected.items():
            if key not in actual:
                errors.append(f"{path}.{key}: missing key")
                continue
            errors.extend(is_subset(value, actual[key], f"{path}.{key}"))
        return errors

    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes, bytearray)):
        if not isinstance(actual, Sequence) or isinstance(actual, (str, bytes, bytearray)):
            return [f"{path}: expected array, got {type(actual).__name__}"]
        if len(expected) > len(actual):
            errors.append(f"{path}: expected at least {len(expected)} items, got {len(actual)}")
        for idx, value in enumerate(expected):
            if idx >= len(actual):
                break
            errors.extend(is_subset(value, actual[idx], f"{path}[{idx}]"))
        return errors

    if expected != actual:
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    return errors


def summarize_payload(payload: Any, max_chars: int = 180) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = repr(payload)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


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


def _spec_dict_for_domain(domain: str) -> dict[str, Any]:
    canonical = _canonical_domain(domain)
    if get_domain_spec is not None:
        try:
            spec = get_domain_spec(canonical)
            return {
                "key": spec.key,
                "module": spec.module,
                "scenario_id": spec.scenario_id,
                "scenario_name": spec.scenario_name,
                "track_label": spec.track_label,
                "category": getattr(spec, "category", spec.key),
                "source_url": getattr(spec, "source_url", ""),
                "aliases": list(getattr(spec, "aliases", ()) or ()),
                "class_candidates": list(getattr(spec, "class_candidates", ()) or ()),
            }
        except Exception:
            pass

    return {
        "key": canonical,
        "module": canonical,
        "scenario_id": SCENARIO_ID_BY_DOMAIN.get(canonical, _id_from_name(canonical)),
        "scenario_name": SCENARIO_NAME_BY_DOMAIN.get(canonical, canonical),
        "track_label": canonical.replace("_", " ").title(),
        "category": canonical,
        "source_url": "",
        "aliases": [],
        "class_candidates": [],
    }


def _registry_report(*, import_all: bool = False) -> dict[str, Any]:
    if validate_registry is None:
        return {
            "ok": False,
            "error": REGISTRY_IMPORT_ERROR or "registry import unavailable",
            "domain_count": 0,
        }
    try:
        return dict(validate_registry(import_all=import_all))
    except Exception as exc:  # pragma: no cover - diagnostic path
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "domain_count": 0,
        }


def _entries_from_registry() -> list[dict[str, Any]]:
    if list_domain_specs is None:
        raise SmokeFailure(REGISTRY_IMPORT_ERROR or "domains.registry could not be imported")

    specs = list_domain_specs()
    if not isinstance(specs, Mapping):
        raise SmokeFailure("registry.list_domain_specs() must return a mapping")

    entries: list[dict[str, Any]] = []
    for key, raw_spec in sorted(specs.items()):
        if not isinstance(raw_spec, Mapping):
            continue
        domain = _canonical_domain(str(raw_spec.get("key") or key))
        spec = _spec_dict_for_domain(domain)
        entries.append(
            {
                "domain": domain,
                "scenario_id": str(spec.get("scenario_id") or _id_from_name(str(spec.get("scenario_name") or domain))),
                "scenario_name": str(spec.get("scenario_name") or SCENARIO_NAME_BY_DOMAIN.get(domain, domain)),
                "track_label": str(spec.get("track_label") or domain),
                "category": str(spec.get("category") or domain),
                "source_url": str(spec.get("source_url") or ""),
                "smoke": True,
                "weight": 1.0,
                "source": "registry",
            }
        )
    return entries


def _load_mission_mix() -> Mapping[str, Any]:
    data = load_json("mission_mix.json", required=True)
    if not isinstance(data, Mapping):
        raise SmokeFailure("mission_mix.json must be a JSON object")
    return data


def _entries_from_mission_mix(*, include_non_smoke: bool) -> tuple[list[dict[str, Any]], str]:
    mission_mix = _load_mission_mix()
    entries = mission_mix.get("primary_mix")
    if not isinstance(entries, list):
        raise SmokeFailure("mission_mix.json is missing primary_mix")

    output: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        smoke_flag = bool(entry.get("smoke", False))
        if not include_non_smoke and not smoke_flag:
            continue

        domain = _canonical_domain(str(entry.get("domain") or "general"))
        spec = _spec_dict_for_domain(domain)
        scenario_id = str(entry.get("scenario_id") or spec.get("scenario_id") or _id_from_name(domain))
        scenario_name = str(entry.get("scenario_name") or spec.get("scenario_name") or scenario_id)

        output.append(
            {
                **dict(entry),
                "domain": domain,
                "scenario_id": scenario_id,
                "scenario_name": scenario_name,
                "track_label": str(entry.get("track_label") or spec.get("track_label") or domain),
                "category": str(entry.get("category") or spec.get("category") or domain),
                "source_url": str(entry.get("source_url") or spec.get("source_url") or ""),
                "smoke": smoke_flag,
                "source": "mission_mix",
            }
        )

    default_env_id = str(mission_mix.get("default_env_id") or DEFAULT_ENV_ID)
    return output, default_env_id


def _resolve_entries(source: str, *, include_non_smoke: bool) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    registry_info = _registry_report(import_all=False)

    if source == "registry":
        return _entries_from_registry(), DEFAULT_ENV_ID, registry_info

    if source == "mission_mix":
        entries, default_env_id = _entries_from_mission_mix(include_non_smoke=include_non_smoke)
        return entries, default_env_id, registry_info

    # auto: prefer registry because it is the Sprint 4 canonical surface.
    if registry_info.get("ok") and int(registry_info.get("domain_count") or 0) >= EXPECTED_DOMAIN_COUNT:
        return _entries_from_registry(), DEFAULT_ENV_ID, registry_info

    entries, default_env_id = _entries_from_mission_mix(include_non_smoke=include_non_smoke)
    return entries, default_env_id, registry_info


def _only_tokens(values: Sequence[str] | None) -> set[str]:
    output: set[str] = set()
    for value in values or []:
        raw = str(value).strip()
        if not raw:
            continue
        output.add(_normalize_name(raw))
        output.add(_id_from_name(raw))
        try:
            output.add(_canonical_domain(raw))
        except Exception:
            pass
    return {item for item in output if item}


def _entry_tokens(entry: Mapping[str, Any]) -> set[str]:
    domain = _canonical_domain(str(entry.get("domain") or ""))
    spec = _spec_dict_for_domain(domain)
    raw_values = {
        domain,
        str(entry.get("domain") or ""),
        str(entry.get("scenario_id") or ""),
        str(entry.get("scenario_name") or ""),
        str(entry.get("category") or ""),
        str(spec.get("scenario_id") or ""),
        str(spec.get("scenario_name") or ""),
        str(spec.get("category") or ""),
        *[str(alias) for alias in spec.get("aliases") or []],
    }
    tokens: set[str] = set()
    for value in raw_values:
        tokens.add(_normalize_name(value))
        tokens.add(_id_from_name(value))
    return {item for item in tokens if item}


def _select_entries(entries: Sequence[Mapping[str, Any]], only: Sequence[str] | None) -> list[dict[str, Any]]:
    only_set = _only_tokens(only)
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        candidate = dict(entry)
        domain = _canonical_domain(str(candidate.get("domain") or "general"))
        candidate["domain"] = domain
        candidate.setdefault("scenario_id", _spec_dict_for_domain(domain).get("scenario_id") or _id_from_name(domain))
        candidate.setdefault("scenario_name", _spec_dict_for_domain(domain).get("scenario_name") or domain)
        if only_set and not (_entry_tokens(candidate) & only_set):
            continue
        key = (str(candidate.get("domain") or ""), str(candidate.get("scenario_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate)
    return selected


def _fixture_candidates_for_domain(domain: str) -> tuple[str, ...]:
    canonical = _canonical_domain(domain)
    return FIXTURE_CANDIDATES_BY_DOMAIN.get(canonical, (f"sample_actions_{canonical}.json", "sample_actions_research.json"))


def _load_domain_fixture(domain: str, *, strict_fixtures: bool = False) -> tuple[str | None, dict[str, Any], list[str]]:
    candidates = _fixture_candidates_for_domain(domain)
    warnings: list[str] = []

    if strict_fixtures and candidates:
        preferred = candidates[0]
        if _first_existing_path(preferred) is None:
            raise SmokeFailure(f"preferred fixture missing for domain '{domain}': {preferred}")

    for candidate in candidates:
        path = _first_existing_path(candidate)
        if path is None:
            continue
        data = load_json(candidate, required=True)
        if isinstance(data, Mapping):
            return candidate, dict(data), warnings
        if isinstance(data, list):
            return candidate, {"action_plan": data}, warnings
        raise SmokeFailure(f"fixture for domain '{domain}' must be an object or list: {candidate}")

    fallback_action = dict(DEFAULT_ACTION_BY_DOMAIN.get(_canonical_domain(domain), {"action": "advance", "value": 1}))
    warning = f"no fixture found for domain '{domain}'; using synthetic fallback action"
    if strict_fixtures:
        raise SmokeFailure(warning)
    warnings.append(warning)
    return None, {"action_plan": [fallback_action]}, warnings


def _normalize_action_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    if "name" in item:
        return {
            "name": str(item.get("name") or ""),
            "args": dict(item.get("args") or {}),
        }
    if "action" in item:
        normalized = dict(item)
        normalized["action"] = str(item.get("action") or "")
        return normalized
    return dict(item)


def _first_action_from_fixture(fixture: Mapping[str, Any], *, domain: str, scenario_id: str) -> Mapping[str, Any]:
    examples = fixture.get("action_examples")
    if isinstance(examples, Mapping):
        for key in ("shorthand", "canonical"):
            values = examples.get(key)
            if isinstance(values, list) and values and isinstance(values[0], Mapping):
                return _normalize_action_entry(values[0])

    action_plan = fixture.get("action_plan")
    if isinstance(action_plan, list) and action_plan and isinstance(action_plan[0], Mapping):
        return _normalize_action_entry(action_plan[0])

    episodes = fixture.get("episodes")
    if isinstance(episodes, list):
        for episode in episodes:
            if not isinstance(episode, Mapping):
                continue
            episode_plan = episode.get("action_plan")
            if isinstance(episode_plan, list) and episode_plan and isinstance(episode_plan[0], Mapping):
                return _normalize_action_entry(episode_plan[0])

    if "action" in fixture or "name" in fixture:
        return _normalize_action_entry(fixture)

    default_action = DEFAULT_ACTION_BY_DOMAIN.get(_canonical_domain(domain))
    if isinstance(default_action, Mapping):
        action = dict(default_action)
        action.setdefault("args", {})
        return action

    return {"action": "advance", "value": 1, "domain": domain, "scenario_id": scenario_id}


def _build_reset_payload(
    mission_entry: Mapping[str, Any],
    fixture: Mapping[str, Any],
    *,
    default_env_id: str,
) -> dict[str, Any]:
    domain = _canonical_domain(str(mission_entry.get("domain") or fixture.get("domain") or "general"))
    spec = _spec_dict_for_domain(domain)
    scenario_id = str(mission_entry.get("scenario_id") or fixture.get("scenario_id") or spec.get("scenario_id") or "unknown")
    scenario_name = str(mission_entry.get("scenario_name") or fixture.get("scenario_name") or spec.get("scenario_name") or scenario_id)

    reset_payload = fixture.get("reset_payload")
    payload = dict(reset_payload) if isinstance(reset_payload, Mapping) else {}

    payload["seed"] = int(payload.get("seed", mission_entry.get("seed", 42)) or 42)
    payload["scenario_id"] = scenario_id
    payload["scenario_name"] = scenario_name
    payload.setdefault("mission_id", f"{_slugify(scenario_id)}_{_slugify(domain)}_smoke")

    options = dict(payload.get("options") or {})
    options.setdefault("env_id", default_env_id)
    options.setdefault("max_steps", int(mission_entry.get("max_steps", 5) or 5))
    options.setdefault("target_score", int(mission_entry.get("target_score", 1) or 1))
    options["domain"] = domain
    options["scenario_id"] = scenario_id
    options["scenario_name"] = scenario_name
    options["category"] = str(mission_entry.get("category") or spec.get("category") or domain)
    source_url = str(mission_entry.get("source_url") or spec.get("source_url") or "")
    if source_url:
        options["source_url"] = source_url
    payload["options"] = options

    return payload


def _health_ok(payload: Mapping[str, Any]) -> bool:
    return all(key in payload for key in ("status", "env", "initialized")) and payload.get("status") == "ok"


def _record_contract_check(
    report: dict[str, Any],
    name: str,
    *,
    ok: bool,
    payload: Any,
    errors: Sequence[str] | None = None,
    request: Mapping[str, Any] | None = None,
) -> None:
    section: dict[str, Any] = {
        "ok": bool(ok),
        "summary": summarize_payload(payload),
    }
    if errors:
        section["errors"] = list(errors)
    if request is not None:
        section["request"] = dict(request)
    report.setdefault("checks", {})[name] = section


def run_smoke_for_entry(
    client: OpenEnvClient,
    mission_entry: Mapping[str, Any],
    *,
    default_env_id: str,
    expected_reset: Any,
    expected_step: Any,
    expected_state: Any,
    strict_fixtures: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    domain = _canonical_domain(str(mission_entry.get("domain") or "general"))
    spec = _spec_dict_for_domain(domain)
    scenario_id = str(mission_entry.get("scenario_id") or spec.get("scenario_id") or "unknown")
    scenario_name = str(mission_entry.get("scenario_name") or spec.get("scenario_name") or scenario_id)

    fixture_name, fixture, fixture_warnings = _load_domain_fixture(domain, strict_fixtures=strict_fixtures)
    reset_payload = _build_reset_payload(mission_entry, fixture, default_env_id=default_env_id)
    action_payload = _first_action_from_fixture(fixture, domain=domain, scenario_id=scenario_id)

    entry_report: dict[str, Any] = {
        "ok": True,
        "domain": domain,
        "scenario_id": scenario_id,
        "scenario_name": scenario_name,
        "category": str(mission_entry.get("category") or spec.get("category") or domain),
        "source_url": str(mission_entry.get("source_url") or spec.get("source_url") or ""),
        "fixture": fixture_name,
        "fixture_candidates": list(_fixture_candidates_for_domain(domain)),
        "warnings": fixture_warnings,
        "checks": {},
    }

    health = client.health()
    health_ok = _health_ok(health)
    _record_contract_check(entry_report, "health", ok=health_ok, payload=health)
    if not health_ok:
        entry_report["ok"] = False
        entry_report["error"] = "/health did not satisfy the minimal contract"
        return entry_report
    if verbose:
        print(f"[ok] {domain}: health")

    reset = client.reset(reset_payload)
    reset_errors = is_subset(expected_reset, reset)
    _record_contract_check(entry_report, "reset", ok=not reset_errors, payload=reset, errors=reset_errors, request=reset_payload)
    if reset_errors:
        entry_report["ok"] = False
        entry_report["error"] = "/reset did not match expected_reset_min.json"
        return entry_report
    if verbose:
        print(f"[ok] {domain}: reset ({scenario_name})")

    step = client.step(action_payload)
    step_errors = is_subset(expected_step, step)
    _record_contract_check(entry_report, "step", ok=not step_errors, payload=step, errors=step_errors, request=action_payload)
    if step_errors:
        entry_report["ok"] = False
        entry_report["error"] = "/step did not match expected_step_min.json"
        return entry_report
    if verbose:
        print(f"[ok] {domain}: step")

    state = client.state()
    state_errors = is_subset(expected_state, state)
    _record_contract_check(entry_report, "state", ok=not state_errors, payload=state, errors=state_errors)
    if state_errors:
        entry_report["ok"] = False
        entry_report["error"] = "/state did not match expected_state_min.json"
        return entry_report
    if verbose:
        print(f"[ok] {domain}: state")

    entry_report["ok"] = all(section.get("ok") for section in entry_report["checks"].values())
    return entry_report


def run_all_domain_smokes(
    *,
    base_url: str,
    timeout: float,
    source: str = "auto",
    only: Sequence[str] | None = None,
    include_non_smoke: bool = False,
    strict_fixtures: bool = False,
    validate_domain_imports: bool = False,
    fail_fast: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    entries, default_env_id, registry_info = _resolve_entries(source, include_non_smoke=include_non_smoke)
    selected_entries = _select_entries(entries, only)
    if not selected_entries:
        raise SmokeFailure("no domain/scenario entries matched the requested filters")

    if validate_domain_imports:
        registry_info = _registry_report(import_all=True)

    expected_reset = load_json("expected_reset_min.json")
    expected_step = load_json("expected_step_min.json")
    expected_state = load_json("expected_state_min.json")

    client = OpenEnvClient(base_url=base_url, timeout=timeout)

    report: dict[str, Any] = {
        "ok": True,
        "base_url": base_url,
        "timeout": timeout,
        "source": source,
        "default_env_id": default_env_id,
        "registry": registry_info,
        "selected": [
            {
                "domain": str(entry.get("domain") or "general"),
                "scenario_id": str(entry.get("scenario_id") or "unknown"),
                "scenario_name": str(entry.get("scenario_name") or "unknown"),
            }
            for entry in selected_entries
        ],
        "domains": [],
        "warnings": [],
    }

    if source in {"auto", "registry"} and not registry_info.get("ok"):
        report["warnings"].append("registry validation is not clean; see registry.errors")

    if len(selected_entries) != EXPECTED_DOMAIN_COUNT and not only:
        report["warnings"].append(
            f"selected {len(selected_entries)} entries; expected {EXPECTED_DOMAIN_COUNT} for Sprint 4 full coverage"
        )

    for entry in selected_entries:
        domain = _canonical_domain(str(entry.get("domain") or "general"))
        try:
            entry_report = run_smoke_for_entry(
                client,
                entry,
                default_env_id=default_env_id,
                expected_reset=expected_reset,
                expected_step=expected_step,
                expected_state=expected_state,
                strict_fixtures=strict_fixtures,
                verbose=verbose,
            )
        except (OpenEnvClientError, SmokeFailure, OSError, ValueError, TypeError) as exc:
            entry_report = {
                "ok": False,
                "domain": domain,
                "scenario_id": str(entry.get("scenario_id") or "unknown"),
                "scenario_name": str(entry.get("scenario_name") or "unknown"),
                "error": str(exc),
                "type": type(exc).__name__,
                "checks": {},
            }
            if fail_fast:
                report["domains"].append(entry_report)
                report["ok"] = False
                raise SmokeFailure(f"[{domain}] {type(exc).__name__}: {exc}") from exc

        report["domains"].append(entry_report)
        if not entry_report.get("ok") and fail_fast:
            report["ok"] = False
            raise SmokeFailure(f"[{domain}] {entry_report.get('error') or 'smoke failed'}")

    report["passed"] = sum(1 for item in report["domains"] if item.get("ok"))
    report["failed"] = sum(1 for item in report["domains"] if not item.get("ok"))
    report["total"] = len(report["domains"])
    report["fixture_warnings"] = [
        warning
        for item in report["domains"]
        for warning in item.get("warnings", [])
    ]
    report["ok"] = report["failed"] == 0
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run registry-aware smoke checks across OmniBench domains.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Environment server base URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument(
        "--source",
        choices=("auto", "registry", "mission_mix"),
        default="auto",
        help="Entry source. auto prefers registry when it has the full Sprint 4 surface.",
    )
    parser.add_argument("--only", nargs="*", help="Restrict to one or more domains, aliases, scenario IDs, or scenario names")
    parser.add_argument(
        "--include-non-smoke",
        action="store_true",
        help="When --source mission_mix is used, include entries even if smoke=false.",
    )
    parser.add_argument(
        "--strict-fixtures",
        action="store_true",
        help="Require each domain's preferred sample_actions_*.json fixture to exist.",
    )
    parser.add_argument(
        "--validate-domain-imports",
        action="store_true",
        help="Also call validate_registry(import_all=True). Useful once every domain implementation exists.",
    )
    parser.add_argument("--fail-fast", action="store_true", help="Stop on the first failing domain")
    parser.add_argument("--verbose", action="store_true", help="Print live step-by-step output")
    parser.add_argument("--json", action="store_true", help="Print the final report as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        report = run_all_domain_smokes(
            base_url=args.base_url,
            timeout=args.timeout,
            source=args.source,
            only=args.only,
            include_non_smoke=args.include_non_smoke,
            strict_fixtures=args.strict_fixtures,
            validate_domain_imports=args.validate_domain_imports,
            fail_fast=args.fail_fast,
            verbose=args.verbose,
        )
    except SmokeFailure as exc:
        report = {
            "ok": False,
            "base_url": args.base_url,
            "error": str(exc),
            "type": "smoke_failure",
        }
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
        else:
            print(f"[fail] {report['error']}")
        return 1
    except OpenEnvClientError as exc:
        report = {
            "ok": False,
            "base_url": args.base_url,
            "error": str(exc),
            "type": "client_error",
        }
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
        else:
            print(f"[fail] {report['error']}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        status = "ok" if report.get("ok") else "fail"
        print(f"[{status}] selected domain smokes completed")
        print(f"- base_url: {report['base_url']}")
        print(f"- source: {report['source']}")
        print(f"- passed: {report['passed']}/{report['total']}")
        if report.get("warnings"):
            for warning in report["warnings"]:
                print(f"- warning: {warning}")
        if report.get("fixture_warnings"):
            for warning in report["fixture_warnings"]:
                print(f"- fixture warning: {warning}")
        for item in report["domains"]:
            marker = "PASS" if item.get("ok") else "FAIL"
            scenario = item.get("scenario_name") or item.get("scenario_id")
            suffix = f" - {item.get('error')}" if not item.get("ok") and item.get("error") else ""
            print(f"- {item['domain']} ({scenario}): {marker}{suffix}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
