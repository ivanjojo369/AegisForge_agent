from __future__ import annotations

"""Fast local smoke checker for omnibench_aegis_env.

This script is intentionally small and local-server focused. It validates the
minimal OpenEnv server contract for one selected domain/scenario:
- GET  /health
- POST /reset
- POST /step
- GET  /state

Sprint 4 alignment:
- defaults to port 8001, not 8000
- can resolve domains/scenarios through domains/registry.py
- knows the 16 final Sprint 4 domains/scenarios
- can use generated payload bundles when available
- can fall back to sample_actions_*.json fixtures without requiring every new
  domain fixture to exist yet

Usage:
    python smoke_local.py --verbose
    python smoke_local.py --base-url http://127.0.0.1:8001 --domain web --json
    python smoke_local.py --scenario LnkLifter --strict-fixture --json
    python smoke_local.py --list-fixtures
"""

import argparse
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parent
ENV_ROOT = SCRIPT_ROOT.parent if SCRIPT_ROOT.name == "scripts" else SCRIPT_ROOT
REPO_ROOT = ENV_ROOT.parents[3] if len(ENV_ROOT.parents) >= 4 else ENV_ROOT.parent

# Make the script resilient whether it is run from:
# - omnibench_aegis_env/smoke_local.py
# - omnibench_aegis_env/scripts/smoke_local.py
# - repo root with a direct path
for candidate in (ENV_ROOT.parent, ENV_ROOT, SCRIPT_ROOT.parent, SCRIPT_ROOT, REPO_ROOT):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)


DEFAULT_BASE_URL = os.getenv("OPENENV_BASE_URL", "http://127.0.0.1:8001")
DEFAULT_TIMEOUT = float(os.getenv("OPENENV_TIMEOUT", "10"))
DEFAULT_ENV_NAME = "omnibench_aegis_env"
DEFAULT_DOMAIN = os.getenv("OPENENV_SMOKE_DOMAIN", "research")


class SmokeFailure(RuntimeError):
    """Raised when the local smoke contract fails."""


def _load_client_classes():
    """Import OpenEnvClient with fallbacks for direct script execution."""

    errors: list[str] = []
    for module_name in (
        "omnibench_aegis_env.client",
        "client",
    ):
        try:
            module = importlib.import_module(module_name)
            return module.OpenEnvClient, module.OpenEnvClientError
        except Exception as exc:  # pragma: no cover - import diagnostic path
            errors.append(f"{module_name}: {type(exc).__name__}: {exc}")

    client_path = ENV_ROOT / "client.py"
    if client_path.exists():
        try:
            spec = importlib.util.spec_from_file_location("_omnibench_aegis_env_client", client_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module.OpenEnvClient, module.OpenEnvClientError
        except Exception as exc:  # pragma: no cover - import diagnostic path
            errors.append(f"{client_path}: {type(exc).__name__}: {exc}")

    joined = "; ".join(errors)
    raise SmokeFailure(f"could not import OpenEnvClient ({joined})")


OpenEnvClient, OpenEnvClientError = _load_client_classes()


SPRINT4_FALLBACK_SPECS: dict[str, dict[str, Any]] = {
    "business_process": {
        "scenario_id": "saleforceonespy",
        "scenario_name": "SaleForceOneSpy",
        "category": "business_process",
        "track_label": "Business Process Agent",
        "fixture_candidates": ["sample_actions_business_process.json", "sample_actions_finance.json"],
        "default_action": {"name": "inspect_schema", "args": {}},
        "aliases": ["crm", "crmarena", "salesforceone", "saleforceone", "saleforceonespy", "SaleForceOneSpy"],
    },
    "game": {
        "scenario_id": "wikiwiper",
        "scenario_name": "WikiWiper",
        "category": "game",
        "track_label": "Game Agent",
        "fixture_candidates": ["sample_actions_game.json", "sample_actions_game_real.json"],
        "default_action": {"name": "inspect_objective", "args": {}},
        "aliases": ["mcu", "minecraft", "mcu_minecraft", "wikiwiper", "WikiWiper"],
    },
    "tau2": {
        "scenario_id": "tickettwister",
        "scenario_name": "TicketTwister",
        "category": "tau2",
        "track_label": "τ²-Bench",
        "fixture_candidates": ["sample_actions_tau2.json", "sample_actions_research.json"],
        "default_action": {"name": "load_tasks", "args": {}},
        "aliases": ["tau", "tau_bench", "tau2_bench", "t2", "tickettwister", "TicketTwister"],
    },
    "multi_agent": {
        "scenario_id": "bidbot",
        "scenario_name": "BidBot",
        "category": "multi_agent",
        "track_label": "Multi-agent Evaluation",
        "fixture_candidates": ["sample_actions_multi_agent.json", "sample_actions_research.json"],
        "default_action": {"name": "build_roster", "args": {}},
        "aliases": ["maizebargain", "maize_bargain", "bidbot", "BidBot"],
    },
    "finance": {
        "scenario_id": "taxwiztrap",
        "scenario_name": "TaxWizTrap",
        "category": "finance",
        "track_label": "Finance Agent",
        "fixture_candidates": ["sample_actions_finance.json", "sample_actions_finance_real.json"],
        "default_action": {"name": "review_tax_note", "args": {}},
        "aliases": ["officeqa", "taxwiztrap", "TaxWizTrap"],
    },
    "computer_use": {
        "scenario_id": "lnklifter",
        "scenario_name": "LnkLifter",
        "category": "computer_use",
        "track_label": "Computer Use Agent",
        "fixture_candidates": ["sample_actions_computer_use.json", "sample_actions_web.json"],
        "default_action": {"name": "inspect_page", "args": {}},
        "aliases": ["osworld", "osworld_verified", "computer", "linklifter", "lnklifter", "LnkLifter", "LinkLifter"],
    },
    "agent_safety": {
        "scenario_id": "inventoryinject",
        "scenario_name": "InventoryInject",
        "category": "agent_safety",
        "track_label": "Agent Safety",
        "fixture_candidates": ["sample_actions_agent_safety.json", "sample_actions_research.json"],
        "default_action": {"name": "inspect_context", "args": {}},
        "aliases": ["pibench", "pi_bench", "inventoryinject", "InventoryInject"],
    },
    "coding": {
        "scenario_id": "devcontainerdoom",
        "scenario_name": "DevContainerDoom",
        "category": "coding",
        "track_label": "Coding Agent",
        "fixture_candidates": ["sample_actions_coding.json"],
        "default_action": {"name": "inspect_repo", "args": {}},
        "aliases": ["swebench", "swe_bench", "terminalbench", "terminal_bench", "devcontainerdoom", "DevContainerDoom"],
    },
    "cybersecurity": {
        "scenario_id": "staticshipscam",
        "scenario_name": "StaticShipScam",
        "category": "cybersecurity",
        "track_label": "Cybersecurity Agent",
        "fixture_candidates": ["sample_actions_cybersecurity.json", "sample_actions_agent_security.json", "sample_actions_research.json"],
        "default_action": {"name": "inspect_surface", "args": {}},
        "aliases": ["cybergym", "cyber", "security", "staticshipscam", "StaticShipScam"],
    },
    "research": {
        "scenario_id": "whistleblowerwreck",
        "scenario_name": "WhistleBlowerWreck",
        "category": "research",
        "track_label": "Research Agent",
        "fixture_candidates": ["sample_actions_research.json"],
        "default_action": {"name": "inspect_sources", "args": {}},
        "aliases": ["fieldworkarena", "fieldwork", "whistleblowerwreck", "WhistleBlowerWreck"],
    },
    "healthcare": {
        "scenario_id": "docudoctor",
        "scenario_name": "DocuDoctor",
        "category": "healthcare",
        "track_label": "Healthcare Agent",
        "fixture_candidates": ["sample_actions_healthcare.json", "sample_actions_finance.json"],
        "default_action": {"name": "inspect_patient_bundle", "args": {}},
        "aliases": ["docudoctor", "DocuDoctor", "fhir", "medical", "health"],
    },
    "web": {
        "scenario_id": "searchglitch",
        "scenario_name": "SearchGlitch",
        "category": "web",
        "track_label": "Web Agent",
        "fixture_candidates": ["sample_actions_web.json"],
        "default_action": {"name": "inspect_query", "args": {}},
        "aliases": ["searchglitch", "SearchGlitch", "comtrade", "web_agent", "browser"],
    },
    "agent_security": {
        "scenario_id": "gymjailbreak",
        "scenario_name": "GymJailbreak",
        "category": "agent_security",
        "track_label": "Lambda Agent Security",
        "fixture_candidates": ["sample_actions_agent_security.json", "sample_actions_agent_safety.json"],
        "default_action": {"name": "inspect_prompt", "args": {}},
        "aliases": ["lambda_agent_security", "lambda_security", "gymjailbreak", "GymJailbreak", "security_arena"],
    },
    "software_testing": {
        "scenario_id": "codereviewruse",
        "scenario_name": "CodeReviewRuse",
        "category": "software_testing",
        "track_label": "Software Testing Agent",
        "fixture_candidates": ["sample_actions_software_testing.json", "sample_actions_coding.json"],
        "default_action": {"name": "inspect_patch", "args": {}},
        "aliases": ["codereviewruse", "CodeReviewRuse", "software_testing_agent", "testing", "logomesh"],
    },
    "defi": {
        "scenario_id": "cryptocrash",
        "scenario_name": "CryptoCrash",
        "category": "defi",
        "track_label": "DeFi Agent",
        "fixture_candidates": ["sample_actions_defi.json", "sample_actions_finance.json"],
        "default_action": {"name": "inspect_contract", "args": {}},
        "aliases": ["cryptocrash", "CryptoCrash", "ethernaut", "smart_contract", "smart_contracts", "crypto"],
    },
    "legal_domain": {
        "scenario_id": "lawfirmleak",
        "scenario_name": "LawFirmLeak",
        "category": "legal_domain",
        "track_label": "Legal Domain Agent",
        "fixture_candidates": ["sample_actions_legal_domain.json", "sample_actions_business_process.json", "sample_actions_finance.json"],
        "default_action": {"name": "inspect_matter", "args": {}},
        "aliases": ["lawfirmleak", "LawFirmLeak", "legal", "legal_agent", "agentify_bench"],
    },
}


def _norm(value: Any) -> str:
    return str(value or "").strip().replace("-", "_").replace(" ", "_").replace(".", "_").lower()


def _registry_module():
    for name in (
        "omnibench_aegis_env.domains.registry",
        "domains.registry",
    ):
        try:
            return importlib.import_module(name)
        except Exception:
            pass

    registry_path = ENV_ROOT / "domains" / "registry.py"
    if registry_path.exists():
        spec = importlib.util.spec_from_file_location("_omnibench_domains_registry", registry_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def _registry_specs(validate_imports: bool = False) -> dict[str, dict[str, Any]]:
    registry = _registry_module()
    if registry is None:
        return {}

    if validate_imports and hasattr(registry, "validate_registry"):
        report = registry.validate_registry(import_all=True)
        if isinstance(report, Mapping) and not report.get("ok", False):
            raise SmokeFailure(f"registry validation failed: {json.dumps(report.get('errors', {}), ensure_ascii=False)}")

    if hasattr(registry, "list_domain_specs"):
        raw = registry.list_domain_specs()
        if isinstance(raw, Mapping):
            specs: dict[str, dict[str, Any]] = {}
            for key, value in raw.items():
                if not isinstance(value, Mapping):
                    continue
                scenario_id = str(value.get("scenario_id") or _norm(value.get("scenario_name") or key))
                scenario_name = str(value.get("scenario_name") or scenario_id)
                specs[str(key)] = {
                    "scenario_id": scenario_id,
                    "scenario_name": scenario_name,
                    "category": str(value.get("category") or value.get("key") or key),
                    "source_url": str(value.get("source_url") or ""),
                    "track_label": str(value.get("track_label") or value.get("category") or key),
                    "fixture_candidates": _fixture_candidates_for_domain(str(key), scenario_name),
                    "default_action": SPRINT4_FALLBACK_SPECS.get(str(key), {}).get("default_action", {"name": "advance", "args": {"value": 1}}),
                    "aliases": list(value.get("aliases") or []),
                }
            if specs:
                return specs
    return {}


def _all_specs(validate_imports: bool = False) -> dict[str, dict[str, Any]]:
    specs = {key: dict(value) for key, value in SPRINT4_FALLBACK_SPECS.items()}
    registry_specs = _registry_specs(validate_imports=validate_imports)
    for key, value in registry_specs.items():
        merged = dict(specs.get(key, {}))
        merged.update(value)
        # Preserve the richer fixture/default-action fallback unless registry explicitly adds it.
        if not value.get("fixture_candidates") and key in specs:
            merged["fixture_candidates"] = specs[key]["fixture_candidates"]
        if not value.get("default_action") and key in specs:
            merged["default_action"] = specs[key]["default_action"]
        specs[key] = merged
    return specs


def _fixture_candidates_for_domain(domain: str, scenario_name: str | None = None) -> list[str]:
    fallback = SPRINT4_FALLBACK_SPECS.get(domain)
    if fallback:
        return list(fallback.get("fixture_candidates") or [])

    slug = _norm(domain)
    names = [f"sample_actions_{slug}.json"]
    if scenario_name:
        names.append(f"sample_actions_{_norm(scenario_name)}.json")
    names.append("sample_actions_research.json")
    return list(dict.fromkeys(names))


def _build_alias_index(specs: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for key, spec in specs.items():
        names = {
            key,
            spec.get("category"),
            spec.get("scenario_id"),
            spec.get("scenario_name"),
            _norm(spec.get("scenario_name")),
            *list(spec.get("aliases") or []),
        }
        for name in names:
            normalized = _norm(name)
            if normalized:
                index[normalized] = key
    return index


def _resolve_domain(name: str | None, specs: Mapping[str, Mapping[str, Any]]) -> str:
    if not name:
        return DEFAULT_DOMAIN if DEFAULT_DOMAIN in specs else sorted(specs)[0]
    normalized = _norm(name)
    if normalized in specs:
        return normalized
    alias_index = _build_alias_index(specs)
    if normalized in alias_index:
        return alias_index[normalized]
    raise SmokeFailure(f"unknown domain/scenario '{name}'")


def _candidate_roots() -> list[Path]:
    roots = [
        SCRIPT_ROOT,
        ENV_ROOT,
        ENV_ROOT / "scripts",
        ENV_ROOT / "training",
        ENV_ROOT / "training" / "generated_payloads",
        ENV_ROOT / "training" / "curriculum_payloads",
        ENV_ROOT / "scripts" / "generated_payloads",
        SCRIPT_ROOT / "generated_payloads",
    ]
    output: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        resolved = root.resolve()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            output.append(resolved)
    return output


def _find_file(name: str, *, required: bool = True) -> Path | None:
    path = Path(name)
    if path.is_absolute() and path.exists():
        return path
    for root in _candidate_roots():
        candidate = root / name
        if candidate.exists():
            return candidate
    if required:
        tried = ", ".join(str(root / name) for root in _candidate_roots())
        raise SmokeFailure(f"could not find {name}; tried: {tried}")
    return None


def load_json(name_or_path: str | Path, *, required: bool = True) -> Any:
    path = Path(name_or_path)
    if not path.is_absolute():
        found = _find_file(str(path), required=required)
        if found is None:
            return None
        path = found
    if not path.exists():
        if required:
            raise SmokeFailure(f"missing JSON file: {path}")
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


def summarize_payload(payload: Any, max_chars: int = 220) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _coerce_action(item: Mapping[str, Any]) -> dict[str, Any]:
    if "name" in item:
        return {"name": str(item.get("name") or ""), "args": dict(item.get("args") or {})}
    if "action" in item:
        payload = dict(item)
        payload["action"] = str(item.get("action") or "")
        return payload
    return dict(item)


def _first_action_from_fixture(fixture: Mapping[str, Any], default_action: Mapping[str, Any]) -> dict[str, Any]:
    examples = fixture.get("action_examples")
    if isinstance(examples, Mapping):
        for key in ("shorthand", "canonical"):
            items = examples.get(key)
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
                for item in items:
                    if isinstance(item, Mapping):
                        return _coerce_action(item)

    action_plan = fixture.get("action_plan")
    if isinstance(action_plan, Sequence) and not isinstance(action_plan, (str, bytes, bytearray)):
        for item in action_plan:
            if isinstance(item, Mapping):
                return _coerce_action(item)

    episodes = fixture.get("episodes")
    if isinstance(episodes, Sequence) and not isinstance(episodes, (str, bytes, bytearray)):
        for episode in episodes:
            if not isinstance(episode, Mapping):
                continue
            episode_plan = episode.get("action_plan")
            if isinstance(episode_plan, Sequence) and not isinstance(episode_plan, (str, bytes, bytearray)):
                for item in episode_plan:
                    if isinstance(item, Mapping):
                        return _coerce_action(item)

    if "action" in fixture or "name" in fixture:
        return _coerce_action(fixture)

    return dict(default_action)


def _load_fixture(
    *,
    domain: str,
    spec: Mapping[str, Any],
    explicit_fixture: str | None,
    strict_fixture: bool,
) -> tuple[str | None, dict[str, Any]]:
    candidates = [explicit_fixture] if explicit_fixture else list(spec.get("fixture_candidates") or [])
    candidates = [str(name) for name in candidates if str(name or "").strip()]

    for name in candidates:
        path = _find_file(name, required=False)
        if path is None:
            continue
        data = load_json(path)
        if not isinstance(data, Mapping):
            raise SmokeFailure(f"fixture for domain '{domain}' must be a JSON object: {path}")
        return path.name, dict(data)

    if strict_fixture:
        raise SmokeFailure(f"no fixture found for domain '{domain}'; candidates: {candidates}")

    return None, {}


def _load_generated_payload(domain: str, scenario_id: str) -> dict[str, Any] | None:
    aggregate_candidates = [
        "all_client_curriculum_bundles.json",
        "all_openenv_curriculum_payloads.json",
        "all_client_bundles.json",
        "all_openenv_eval_payloads.json",
    ]
    wanted_domain = _norm(domain)
    wanted_scenario = _norm(scenario_id)
    for name in aggregate_candidates:
        path = _find_file(name, required=False)
        if path is None:
            continue
        data = load_json(path, required=False)
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, Mapping):
                continue
            if _norm(item.get("domain")) == wanted_domain and _norm(item.get("scenario_id")) == wanted_scenario:
                return dict(item)
    return None


def _build_reset_payload(
    *,
    domain: str,
    spec: Mapping[str, Any],
    fixture: Mapping[str, Any],
    generated_payload: Mapping[str, Any] | None,
    seed: int | None,
    env_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    env_seed = load_json("env_seed.json", required=False)
    if isinstance(env_seed, Mapping):
        payload.update(dict(env_seed))

    if generated_payload and isinstance(generated_payload.get("reset_payload"), Mapping):
        payload.update(dict(generated_payload["reset_payload"]))

    fixture_reset = fixture.get("reset_payload") if isinstance(fixture, Mapping) else None
    if isinstance(fixture_reset, Mapping):
        payload.update(dict(fixture_reset))

    scenario_id = str(spec.get("scenario_id") or "unknown")
    scenario_name = str(spec.get("scenario_name") or scenario_id)
    payload["seed"] = int(seed if seed is not None else payload.get("seed", 42))
    payload["scenario_id"] = scenario_id
    payload.setdefault("mission_id", f"{_norm(scenario_name)}_{_norm(domain)}_local_smoke")

    options = dict(payload.get("options") or {})
    options["domain"] = domain
    options.setdefault("max_steps", 5)
    options.setdefault("target_score", 1)
    options["env_id"] = str(env_id or options.get("env_id") or f"{DEFAULT_ENV_NAME}:{domain}.{scenario_id}")
    payload["options"] = options

    return payload


def _health_ok(payload: Mapping[str, Any]) -> bool:
    return all(key in payload for key in ("status", "env", "initialized")) and payload.get("status") == "ok"


def _expected_json(name: str) -> Any:
    data = load_json(name, required=False)
    return data if data is not None else {}


def run_smoke(
    *,
    base_url: str,
    timeout: float,
    domain_or_scenario: str | None = None,
    fixture_name: str | None = None,
    strict_fixture: bool = False,
    validate_registry_imports: bool = False,
    seed: int | None = None,
    env_id: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    specs = _all_specs(validate_imports=validate_registry_imports)
    domain = _resolve_domain(domain_or_scenario, specs)
    spec = specs[domain]

    fixture_used, fixture = _load_fixture(
        domain=domain,
        spec=spec,
        explicit_fixture=fixture_name,
        strict_fixture=strict_fixture,
    )
    generated_payload = _load_generated_payload(domain, str(spec.get("scenario_id") or ""))
    reset_payload = _build_reset_payload(
        domain=domain,
        spec=spec,
        fixture=fixture,
        generated_payload=generated_payload,
        seed=seed,
        env_id=env_id,
    )

    if generated_payload and isinstance(generated_payload.get("action_plan"), Sequence):
        first = next((item for item in generated_payload["action_plan"] if isinstance(item, Mapping)), None)
        action_payload = _coerce_action(first) if isinstance(first, Mapping) else None
    else:
        action_payload = None
    if action_payload is None:
        action_payload = _first_action_from_fixture(fixture, dict(spec.get("default_action") or {"name": "advance", "args": {"value": 1}}))

    expected_reset = _expected_json("expected_reset_min.json")
    expected_step = _expected_json("expected_step_min.json")
    expected_state = _expected_json("expected_state_min.json")

    client = OpenEnvClient(base_url=base_url, timeout=timeout)
    report: dict[str, Any] = {
        "base_url": base_url,
        "timeout": timeout,
        "domain": domain,
        "scenario_id": spec.get("scenario_id"),
        "scenario_name": spec.get("scenario_name"),
        "category": spec.get("category"),
        "track_label": spec.get("track_label"),
        "source_url": spec.get("source_url"),
        "fixture": fixture_used,
        "generated_payload_used": bool(generated_payload),
        "checks": {},
        "warnings": [],
    }
    if fixture_used is None:
        report["warnings"].append("no fixture file found; using domain default action")
    if not generated_payload:
        report["warnings"].append("no generated payload bundle found; building reset payload locally")

    health = client.health()
    health_ok = _health_ok(health)
    report["checks"]["health"] = {"ok": health_ok, "summary": summarize_payload(health)}
    if not health_ok:
        raise SmokeFailure("/health did not satisfy the minimal contract")
    if verbose:
        print("[ok] health", summarize_payload(health))

    reset = client.reset(reset_payload)
    reset_errors = is_subset(expected_reset, reset) if expected_reset else []
    report["checks"]["reset"] = {
        "ok": not reset_errors,
        "errors": reset_errors,
        "request": reset_payload,
        "summary": summarize_payload(reset),
    }
    if reset_errors:
        raise SmokeFailure("/reset did not match expected_reset_min.json")
    if verbose:
        print("[ok] reset", summarize_payload(reset))

    step = client.step(action_payload)
    step_errors = is_subset(expected_step, step) if expected_step else []
    report["checks"]["step"] = {
        "ok": not step_errors,
        "errors": step_errors,
        "request": dict(action_payload),
        "summary": summarize_payload(step),
    }
    if step_errors:
        raise SmokeFailure("/step did not match expected_step_min.json")
    if verbose:
        print("[ok] step", summarize_payload(step))

    state = client.state()
    state_errors = is_subset(expected_state, state) if expected_state else []
    report["checks"]["state"] = {
        "ok": not state_errors,
        "errors": state_errors,
        "summary": summarize_payload(state),
    }
    if state_errors:
        raise SmokeFailure("/state did not match expected_state_min.json")
    if verbose:
        print("[ok] state", summarize_payload(state))

    report["ok"] = all(section.get("ok") for section in report["checks"].values())
    return report


def list_fixture_status(validate_registry_imports: bool = False) -> dict[str, Any]:
    specs = _all_specs(validate_imports=validate_registry_imports)
    rows: list[dict[str, Any]] = []
    for domain, spec in sorted(specs.items()):
        candidates = list(spec.get("fixture_candidates") or [])
        found = None
        for name in candidates:
            path = _find_file(str(name), required=False)
            if path is not None:
                found = path.name
                break
        rows.append(
            {
                "domain": domain,
                "scenario_id": spec.get("scenario_id"),
                "scenario_name": spec.get("scenario_name"),
                "fixture_found": found,
                "fixture_candidates": candidates,
            }
        )
    return {"ok": True, "domain_count": len(rows), "fixtures": rows}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a fast local smoke check against omnibench_aegis_env.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Environment server base URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument("--domain", help="Domain key, alias, or scenario name to smoke")
    parser.add_argument("--scenario", help="Alias for --domain when selecting by scenario name/id")
    parser.add_argument("--fixture", help="Explicit sample_actions_*.json fixture to use")
    parser.add_argument("--strict-fixture", action="store_true", help="Fail if no fixture file exists for the selected domain")
    parser.add_argument("--validate-registry-imports", action="store_true", help="Run registry validate_registry(import_all=True) before smoking")
    parser.add_argument("--seed", type=int, help="Override reset seed")
    parser.add_argument("--env-id", help="Override reset options.env_id")
    parser.add_argument("--list-fixtures", action="store_true", help="Print fixture availability for all known domains")
    parser.add_argument("--verbose", action="store_true", help="Print live step-by-step output")
    parser.add_argument("--json", action="store_true", help="Print the final report as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    selector = args.scenario or args.domain

    try:
        if args.list_fixtures:
            report = list_fixture_status(validate_registry_imports=args.validate_registry_imports)
        else:
            report = run_smoke(
                base_url=args.base_url,
                timeout=args.timeout,
                domain_or_scenario=selector,
                fixture_name=args.fixture,
                strict_fixture=args.strict_fixture,
                validate_registry_imports=args.validate_registry_imports,
                seed=args.seed,
                env_id=args.env_id,
                verbose=args.verbose,
            )
    except OpenEnvClientError as exc:
        report = {
            "ok": False,
            "base_url": args.base_url,
            "error": str(exc),
            "type": "client_error",
        }
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1
    except SmokeFailure as exc:
        report = {
            "ok": False,
            "base_url": args.base_url,
            "error": str(exc),
            "type": "contract_error",
        }
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.list_fixtures:
        print("[ok] fixture status")
        print(f"- domains: {report['domain_count']}")
        for item in report["fixtures"]:
            found = item["fixture_found"] or "MISSING"
            print(f"- {item['domain']} ({item['scenario_name']}): {found}")
    else:
        print("[ok] local smoke passed")
        print(f"- base_url: {report['base_url']}")
        print(f"- domain: {report['domain']}")
        print(f"- scenario: {report['scenario_name']} ({report['scenario_id']})")
        print(f"- fixture: {report['fixture'] or 'default action fallback'}")
        print(f"- generated_payload_used: {report['generated_payload_used']}")
        for warning in report.get("warnings") or []:
            print(f"- warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
