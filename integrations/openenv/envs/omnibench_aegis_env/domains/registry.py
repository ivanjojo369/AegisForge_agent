from __future__ import annotations

"""Domain registry for omnibench_aegis_env.

This registry is intentionally separate from any generic tools registry.

It maps OpenEnv/OmniBench domain keys and scenario IDs to BaseDomain
implementations under ``integrations/openenv/envs/omnibench_aegis_env/domains``.

Sprint 4 coverage target:
- 16 scenario-aware domains/tracks
- stable aliases for legacy names and scenario IDs
- canonical Sprint 4 scenario names:
  - SalesforceOne  -> SaleForceOneSpy
  - LinkLifter     -> LnkLifter
- category and source_url metadata for UI/API/export surfaces
- lazy imports so optional/in-progress domains do not break discovery
"""

import importlib
import inspect
from dataclasses import dataclass
from typing import Any, Mapping


_REPO_ROOT_URL = "https://github.com/ivanjojo369/AegisForge_agent/blob/main"
_DOMAIN_SOURCE_ROOT = (
    f"{_REPO_ROOT_URL}/integrations/openenv/envs/omnibench_aegis_env/domains"
)


def _domain_source_url(module: str) -> str:
    """Return the repo URL for a domain implementation module."""

    return f"{_DOMAIN_SOURCE_ROOT}/{module}.py"


@dataclass(frozen=True)
class DomainSpec:
    """Static metadata for one benchmark domain.

    The first six fields preserve the previous constructor shape. New metadata
    fields have defaults so older script-level tests that instantiate DomainSpec
    directly do not break.
    """

    key: str
    module: str
    scenario_id: str
    scenario_name: str
    track_label: str
    description: str
    category: str = ""
    source_url: str = ""
    aliases: tuple[str, ...] = ()
    class_candidates: tuple[str, ...] = ()


# NOTE:
# Existing scenario/domain associations are kept stable and conservative.
# The six Sprint 4 additions are:
# - DocuDoctor        -> healthcare.py
# - SearchGlitch      -> web.py
# - GymJailbreak      -> agent_security.py
# - CodeReviewRuse    -> software_testing.py
# - CryptoCrash       -> defi.py
# - LawFirmLeak       -> legal_domain.py
_DOMAIN_SPECS: dict[str, DomainSpec] = {
    "business_process": DomainSpec(
        key="business_process",
        module="business_process",
        scenario_id="saleforceonespy",
        scenario_name="SaleForceOneSpy",
        track_label="Business Process Agent",
        category="business_process",
        source_url=_domain_source_url("business_process"),
        description="CRM/business-process workflow benchmark scenario.",
        aliases=(
            "crm",
            "crmarena",
            "sale_force_one",
            "sale_force_one_spy",
            "saleforceone",
            "saleforceonespy",
            "salesforce_one",
            "salesforce_one_spy",
            "salesforceone",
            "salesforceonespy",
        ),
        class_candidates=(
            "BusinessProcessDomain",
            "BusinessProcessSaleForceOneSpyEnv",
            "BusinessProcessSalesforceOneSpyEnv",
            "BusinessProcessSalesforceOneEnv",
            "BusinessProcessEnv",
        ),
    ),
    "game": DomainSpec(
        key="game",
        module="game",
        scenario_id="wikiwiper",
        scenario_name="WikiWiper",
        track_label="Game Agent",
        category="game",
        source_url=_domain_source_url("game"),
        description="Game-style navigation, tool-use, cleanup, and verification scenario.",
        aliases=("mcu", "minecraft", "mcu_minecraft", "wiki_wiper", "wikiwiper"),
        class_candidates=("GameDomain", "GameWikiWiperEnv", "GameWikiwiperEnv"),
    ),
    "tau2": DomainSpec(
        key="tau2",
        module="tau2",
        scenario_id="tickettwister",
        scenario_name="TicketTwister",
        track_label="τ²-Bench",
        category="tau2",
        source_url=_domain_source_url("tau2"),
        description="Dual-control conversational task with policy, tools, and state assertions.",
        aliases=("tau", "tau_bench", "tau2_bench", "t2", "ticket_twister", "tickettwister"),
        class_candidates=("Tau2Domain", "Tau2TicketTwisterEnv", "Tau2Env"),
    ),
    "multi_agent": DomainSpec(
        key="multi_agent",
        module="multi_agent",
        scenario_id="bidbot",
        scenario_name="BidBot",
        track_label="Multi-agent Evaluation",
        category="multi_agent",
        source_url=_domain_source_url("multi_agent"),
        description="Negotiation/multi-agent decision scenario.",
        aliases=("maizebargain", "maize_bargain", "bid_bot", "bidbot"),
        class_candidates=("MultiAgentDomain", "MultiAgentBidBotEnv", "MultiAgentEnv"),
    ),
    "finance": DomainSpec(
        key="finance",
        module="finance",
        scenario_id="taxwiztrap",
        scenario_name="TaxWizTrap",
        track_label="Finance Agent",
        category="finance",
        source_url=_domain_source_url("finance"),
        description="Finance-style reasoning with evidence and policy constraints.",
        aliases=("officeqa", "tax_wiz_trap", "taxwiztrap"),
        class_candidates=("FinanceDomain", "FinanceTaxWizTrapEnv", "FinanceEnv"),
    ),
    "computer_use": DomainSpec(
        key="computer_use",
        module="computer_use",
        scenario_id="lnklifter",
        scenario_name="LnkLifter",
        track_label="Computer Use Agent",
        category="computer_use",
        source_url=_domain_source_url("computer_use"),
        description="Computer-use/web-navigation style benchmark scenario.",
        aliases=(
            "computer",
            "link_lifter",
            "linklifter",
            "lnk_lifter",
            "lnklifter",
            "osworld",
            "osworld_verified",
        ),
        class_candidates=(
            "ComputerUseDomain",
            "ComputerUseLnkLifterEnv",
            "ComputerUseLinkLifterEnv",
            "ComputerUseEnv",
        ),
    ),
    "agent_safety": DomainSpec(
        key="agent_safety",
        module="agent_safety",
        scenario_id="inventoryinject",
        scenario_name="InventoryInject",
        track_label="Agent Safety",
        category="agent_safety",
        source_url=_domain_source_url("agent_safety"),
        description="Safety benchmark scenario focused on prompt/tool/context robustness.",
        aliases=("pibench", "pi_bench", "inventory_inject", "inventoryinject"),
        class_candidates=("AgentSafetyDomain", "AgentSafetyInventoryInjectEnv", "AgentSafetyEnv"),
    ),
    "coding": DomainSpec(
        key="coding",
        module="coding",
        scenario_id="devcontainerdoom",
        scenario_name="DevContainerDoom",
        track_label="Coding Agent",
        category="coding",
        source_url=_domain_source_url("coding"),
        description="Coding/dev-environment benchmark scenario.",
        aliases=(
            "dev_container_doom",
            "devcontainerdoom",
            "swebench",
            "swe_bench",
            "terminalbench",
            "terminal_bench",
        ),
        class_candidates=("CodingDomain", "CodingDevContainerDoomEnv", "CodingEnv"),
    ),
    "cybersecurity": DomainSpec(
        key="cybersecurity",
        module="Cybersecurity",
        scenario_id="staticshipscam",
        scenario_name="StaticShipScam",
        track_label="Cybersecurity Agent",
        category="cybersecurity",
        source_url=_domain_source_url("Cybersecurity"),
        description="Controlled cybersecurity benchmark scenario.",
        aliases=("cybergym", "cyber", "security", "static_ship_scam", "staticshipscam"),
        class_candidates=(
            "CybersecurityDomain",
            "CyberSecurityDomain",
            "CybersecurityStaticShipScamEnv",
            "CyberSecurityStaticShipScamEnv",
            "CybersecurityEnv",
        ),
    ),
    "research": DomainSpec(
        key="research",
        module="research",
        scenario_id="whistleblowerwreck",
        scenario_name="WhistleBlowerWreck",
        track_label="Research Agent",
        category="research",
        source_url=_domain_source_url("research"),
        description="Research/evidence synthesis scenario with privacy and source discipline.",
        aliases=("fieldworkarena", "fieldwork", "whistle_blower_wreck", "whistleblowerwreck"),
        class_candidates=("ResearchDomain", "ResearchWhistleBlowerWreckEnv", "ResearchEnv"),
    ),

    # Sprint 4 additions.
    "healthcare": DomainSpec(
        key="healthcare",
        module="healthcare",
        scenario_id="docudoctor",
        scenario_name="DocuDoctor",
        track_label="Healthcare Agent",
        category="healthcare",
        source_url=_domain_source_url("healthcare"),
        description="Synthetic healthcare/FHIR-like intake, evidence-grounding, and privacy scenario.",
        aliases=("docu_doctor", "docudoctor", "fhir", "medical", "health"),
        class_candidates=("HealthcareDomain", "HealthcareDocuDoctorEnv", "HealthcareFhirClinicalReviewEnv"),
    ),
    "web": DomainSpec(
        key="web",
        module="web",
        scenario_id="searchglitch",
        scenario_name="SearchGlitch",
        track_label="Web Agent",
        category="web",
        source_url=_domain_source_url("web"),
        description="Web/API retrieval scenario with pagination, deduplication, retry, and output-contract robustness.",
        aliases=("search_glitch", "searchglitch", "comtrade", "web_agent", "browser"),
        class_candidates=("WebDomain", "WebSearchGlitchEnv", "WebComtradeRetrievalEnv"),
    ),
    "agent_security": DomainSpec(
        key="agent_security",
        module="agent_security",
        scenario_id="gymjailbreak",
        scenario_name="GymJailbreak",
        track_label="Lambda Agent Security",
        category="agent_security",
        source_url=_domain_source_url("agent_security"),
        description="Benchmark-safe agent security scenario for jailbreak/unsafe-output resistance.",
        aliases=(
            "lambda_agent_security",
            "lambda_security",
            "gym_jailbreak",
            "gymjailbreak",
            "security_arena",
        ),
        class_candidates=(
            "AgentSecurityDomain",
            "LambdaAgentSecurityDomain",
            "AgentSecurityGymJailbreakEnv",
            "AgentSecurityLambdaArenaEnv",
        ),
    ),
    "software_testing": DomainSpec(
        key="software_testing",
        module="software_testing",
        scenario_id="codereviewruse",
        scenario_name="CodeReviewRuse",
        track_label="Software Testing Agent",
        category="software_testing",
        source_url=_domain_source_url("software_testing"),
        description="Software testing/code review scenario with deceptive comments, weak tests, and integrity checks.",
        aliases=("code_review_ruse", "codereviewruse", "software_testing_agent", "testing", "logomesh"),
        class_candidates=(
            "SoftwareTestingDomain",
            "SoftwareTestingCodeReviewRuseEnv",
            "SoftwareTestingContextualIntegrityEnv",
        ),
    ),
    "defi": DomainSpec(
        key="defi",
        module="defi",
        scenario_id="cryptocrash",
        scenario_name="CryptoCrash",
        track_label="DeFi Agent",
        category="defi",
        source_url=_domain_source_url("defi"),
        description="Local DeFi/smart-contract sandbox audit scenario with wallet and invariant safety constraints.",
        aliases=("crypto_crash", "cryptocrash", "ethernaut", "smart_contract", "smart_contracts", "crypto"),
        class_candidates=("DeFiDomain", "DefiDomain", "DeFiCryptoCrashEnv", "DeFiSandboxSmartContractAuditEnv"),
    ),
    "legal_domain": DomainSpec(
        key="legal_domain",
        module="legal_domain",
        scenario_id="lawfirmleak",
        scenario_name="LawFirmLeak",
        track_label="Legal Domain Agent",
        category="legal_domain",
        source_url=_domain_source_url("legal_domain"),
        description="Synthetic legal-discovery/CRM mapping scenario with privilege, schema, and persistence constraints.",
        aliases=("law_firm_leak", "lawfirmleak", "legal", "legal_agent", "agentify_bench"),
        class_candidates=("LegalDomain", "LegalDomainLawFirmLeakEnv", "LegalDomainSemanticMappingEnv"),
    ),
}


def normalize_domain_name(name: str | None) -> str:
    """Normalize user-facing domain/track/scenario names for lookup."""

    if not name:
        return ""
    return (
        str(name)
        .strip()
        .replace("-", "_")
        .replace(" ", "_")
        .replace(".", "_")
        .lower()
    )


def _lookup_names(spec: DomainSpec) -> set[str]:
    """Return all raw names that should resolve to this spec."""

    return {
        spec.key,
        spec.module,
        spec.category,
        spec.scenario_id,
        spec.scenario_name,
        spec.scenario_name.lower(),
        spec.scenario_name.replace("_", "").lower(),
        spec.scenario_name.replace("-", "").lower(),
        *spec.aliases,
    }


def _build_alias_index(specs: Mapping[str, DomainSpec]) -> dict[str, str]:
    index: dict[str, str] = {}
    for key, spec in specs.items():
        for name in _lookup_names(spec):
            normalized = normalize_domain_name(name)
            if not normalized:
                continue
            # Keep the first owner deterministic. validate_registry() reports
            # ambiguous aliases as errors instead of failing at import time.
            index.setdefault(normalized, key)
    return index


_ALIAS_TO_KEY = _build_alias_index(_DOMAIN_SPECS)


def resolve_domain_name(name: str) -> str:
    """Resolve a domain key, alias, module name, category, or scenario ID."""

    normalized = normalize_domain_name(name)
    if normalized in _DOMAIN_SPECS:
        return normalized
    key = _ALIAS_TO_KEY.get(normalized)
    if key:
        return key
    raise KeyError(f"unknown domain/scenario '{name}'")


def get_domain_spec(name: str) -> DomainSpec:
    """Return static metadata for a domain or scenario alias."""

    return _DOMAIN_SPECS[resolve_domain_name(name)]


def list_domain_specs() -> dict[str, dict[str, Any]]:
    """Return serializable metadata for all registered domains."""

    return {
        key: {
            "key": spec.key,
            "module": spec.module,
            "scenario_id": spec.scenario_id,
            "scenario_name": spec.scenario_name,
            "track_label": spec.track_label,
            "category": spec.category,
            "source_url": spec.source_url,
            "description": spec.description,
            "aliases": list(spec.aliases),
            "class_candidates": list(spec.class_candidates),
        }
        for key, spec in sorted(_DOMAIN_SPECS.items())
    }


def list_domains() -> list[str]:
    """Return canonical domain keys."""

    return sorted(_DOMAIN_SPECS)


def list_scenarios() -> dict[str, str]:
    """Return scenario_id -> domain_key mapping."""

    return {
        spec.scenario_id: key
        for key, spec in sorted(_DOMAIN_SPECS.items(), key=lambda item: item[1].scenario_id)
    }


def list_scenario_names() -> list[str]:
    """Return canonical Sprint 4 scenario names in registry insertion order."""

    return [spec.scenario_name for spec in _DOMAIN_SPECS.values()]


def list_source_urls() -> dict[str, str]:
    """Return domain_key -> source_url mapping."""

    return {
        key: spec.source_url
        for key, spec in sorted(_DOMAIN_SPECS.items())
    }


def _import_domain_module(spec: DomainSpec):
    if __package__:
        return importlib.import_module(f".{spec.module}", __package__)
    return importlib.import_module(spec.module)


def _find_domain_class(module: Any, spec: DomainSpec):
    for candidate in spec.class_candidates:
        obj = getattr(module, candidate, None)
        if inspect.isclass(obj):
            return obj

    # Prefer explicit aliases ending with "Domain".
    for name in getattr(module, "__all__", ()):
        obj = getattr(module, name, None)
        if inspect.isclass(obj) and name.endswith("Domain"):
            return obj

    # Fallback: any class that looks like a BaseDomain subclass/implementation
    # without importing BaseDomain here. This keeps the registry resilient during
    # packaging and local script execution.
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        if hasattr(obj, "build_initial_state") and hasattr(obj, "apply_action") and hasattr(obj, "get_observation"):
            return obj

    raise LookupError(
        f"could not find a domain class in module '{module.__name__}' for domain '{spec.key}'"
    )


def get_domain_class(name: str):
    """Import and return the domain class for a domain key, alias, or scenario ID."""

    spec = get_domain_spec(name)
    module = _import_domain_module(spec)
    return _find_domain_class(module, spec)


def make_domain(name: str, *args: Any, **kwargs: Any):
    """Instantiate a domain by key, alias, category, or scenario ID."""

    cls = get_domain_class(name)
    return cls(*args, **kwargs)


def _find_alias_conflicts(specs: Mapping[str, DomainSpec]) -> dict[str, list[str]]:
    owners_by_alias: dict[str, list[str]] = {}
    for key, spec in specs.items():
        for name in _lookup_names(spec):
            normalized = normalize_domain_name(name)
            if normalized:
                owners_by_alias.setdefault(normalized, []).append(key)

    return {
        alias: sorted(set(owners))
        for alias, owners in sorted(owners_by_alias.items())
        if len(set(owners)) > 1
    }


def validate_registry(import_all: bool = True) -> dict[str, Any]:
    """Validate registry metadata and, optionally, import all domain classes.

    Returns a report instead of raising on per-domain import failures so CI and
    smoke scripts can display actionable diagnostics.
    """

    expected_domain_count = 16
    seen_scenarios: set[str] = set()
    seen_scenario_names: set[str] = set()
    errors: dict[str, str] = {}
    loaded: dict[str, str] = {}

    alias_conflicts = _find_alias_conflicts(_DOMAIN_SPECS)
    for alias, owners in alias_conflicts.items():
        errors[f"alias:{alias}"] = f"ambiguous alias maps to {owners}"

    for key, spec in sorted(_DOMAIN_SPECS.items()):
        if key != spec.key:
            errors[key] = f"spec.key '{spec.key}' does not match registry key '{key}'"
            continue

        if not spec.module:
            errors[key] = "missing module"
            continue

        if not spec.category:
            errors[key] = "missing category"
            continue

        if not spec.source_url:
            errors[key] = "missing source_url"
            continue

        if not spec.source_url.startswith(("https://", "http://")):
            errors[key] = f"source_url is not an HTTP(S) URL: {spec.source_url}"
            continue

        if spec.scenario_id in seen_scenarios:
            errors[key] = f"duplicate scenario_id '{spec.scenario_id}'"
            continue
        seen_scenarios.add(spec.scenario_id)

        if spec.scenario_name in seen_scenario_names:
            errors[key] = f"duplicate scenario_name '{spec.scenario_name}'"
            continue
        seen_scenario_names.add(spec.scenario_name)

        if import_all:
            try:
                cls = get_domain_class(key)
                loaded[key] = f"{cls.__module__}.{cls.__name__}"
            except Exception as exc:  # pragma: no cover - diagnostic path
                errors[key] = f"{type(exc).__name__}: {exc}"

    if len(_DOMAIN_SPECS) != expected_domain_count:
        errors["domain_count"] = f"expected {expected_domain_count}, got {len(_DOMAIN_SPECS)}"

    return {
        "ok": not errors,
        "domain_count": len(_DOMAIN_SPECS),
        "expected_domain_count": expected_domain_count,
        "scenario_count": len(seen_scenarios),
        "loaded": loaded,
        "errors": errors,
        "domains": list_domains(),
        "scenarios": list_scenarios(),
        "scenario_names": list_scenario_names(),
        "source_urls": list_source_urls(),
    }


# Backward-compatible names some scripts may expect.
DOMAIN_SPECS = _DOMAIN_SPECS
DOMAIN_REGISTRY = _DOMAIN_SPECS
REGISTERED_DOMAINS = _DOMAIN_SPECS
resolve_domain = resolve_domain_name
get_domain = make_domain
create_domain = make_domain


__all__ = [
    "DomainSpec",
    "DOMAIN_SPECS",
    "DOMAIN_REGISTRY",
    "REGISTERED_DOMAINS",
    "normalize_domain_name",
    "resolve_domain_name",
    "resolve_domain",
    "get_domain_spec",
    "list_domain_specs",
    "list_domains",
    "list_scenarios",
    "list_scenario_names",
    "list_source_urls",
    "get_domain_class",
    "make_domain",
    "get_domain",
    "create_domain",
    "validate_registry",
]
