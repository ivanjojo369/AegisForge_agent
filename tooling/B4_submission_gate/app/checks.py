from __future__ import annotations

"""Checks for the B4 Submission Gate.

The original module performed three checks:

- /health
- /.well-known/agent-card.json
- a small set of repository artifacts

This version keeps those functions and response shapes, then adds Sprint 4
readiness checks for scenario coverage, telemetry identity preservation,
templates, evaluation helpers, and controlled benchmark safety scope.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .validators import (
    BENCHMARK_NAME,
    DEFAULT_ASSESSMENT_MODE,
    DEFAULT_SCENARIO_FAMILY,
    EXPECTED_CATEGORIES,
    EXPECTED_DOMAINS,
    EXPECTED_SCENARIOS,
    EXPECTED_SCENARIO_IDS,
    EXPECTED_UPSTREAM_TRACKS,
    validate_agent_card_payload,
    validate_health_payload,
    validate_safety_scope,
    validate_scenario_coverage,
    validate_sprint4_check_payload,
    validate_telemetry_payload,
)


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

CORE_REPO_ARTIFACTS: tuple[str, ...] = (
    "README.md",
    "Dockerfile",
    "run.sh",
    "src/aegisforge/a2a_server.py",
)

EVAL_ARTIFACTS: tuple[str, ...] = (
    "aegisforge_eval/registry.py",
    "aegisforge_eval/runner.py",
    "aegisforge_eval/scorer.py",
    "aegisforge_eval/report.py",
    "aegisforge_eval/schemas.py",
    "aegisforge_eval/tracks/openenv.py",
    "aegisforge_eval/tracks/security_arena.py",
    "aegisforge_eval/tracks/tau2.py",
)

TELEMETRY_ARTIFACTS: tuple[str, ...] = (
    "src/aegisforge/telemetry/trace_schema.py",
    "src/aegisforge/telemetry/episode_summary.py",
    "src/aegisforge/telemetry/failure_taxonomy.py",
    "src/aegisforge/telemetry/scorecard.py",
)

OPTIONAL_TELEMETRY_ARTIFACTS: tuple[str, ...] = (
    "src/aegisforge/telemetry/emitter.py",
    "src/aegisforge/telemetry/events.py",
)

TEMPLATE_ARTIFACTS: tuple[str, ...] = (
    "templates/openenv_adapter/adapter_config.template.toml",
    "templates/security_adapter/adapter_config.template.toml",
    "templates/security_adapter/scenario_attacker.template.toml",
    "templates/security_adapter/scenario_defender.template.toml",
    "templates/tau2_adapter/adapter_config.template.toml",
)

OPENENV_ARTIFACTS: tuple[str, ...] = (
    "integrations/openenv/envs/omnibench_aegis_env/domains/registry.py",
    "scripts/smoke_test_all_domains.py",
    "scripts/smoke_local.py",
    "scripts/build_sample_payloads.py",
    "scripts/build_curriculum_payloads.py",
    "scripts/generate_variant_matrix.py",
)

UTILITY_ARTIFACTS: tuple[str, ...] = (
    "src/aegisforge/utils/json_io.py",
    "src/aegisforge/utils/paths.py",
    "src/aegisforge/utils/prompt_loader.py",
    "src/aegisforge/utils/validation.py",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _join_url(base_url: str, suffix: str) -> str:
    return base_url.rstrip("/") + suffix


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _slug(value: str) -> str:
    return str(value or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _scenario_id_from_text(value: str) -> str:
    aliases = {
        "salesforceonespy": "saleforceonespy",
        "salesforceone": "saleforceonespy",
        "saleforceone": "saleforceonespy",
        "lnklifter": "lnklifter",
        "linklifter": "lnklifter",
        "whistleblowerwreck": "whistleblowerwreck",
        "codereviewruse": "codereviewruse",
        "cryptocrash": "cryptocrash",
        "lawfirmleak": "lawfirmleak",
    }
    key = _slug(value)
    return aliases.get(key, key)


def _artifact_group(root: Path, artifacts: tuple[str, ...]) -> dict[str, Any]:
    found = {name: (root / name).exists() for name in artifacts}
    missing = [name for name, exists in found.items() if not exists]
    return {
        "ok": not missing,
        "artifacts": found,
        "missing": missing,
    }


def _coverage(expected: tuple[str, ...], present: set[str]) -> dict[str, Any]:
    expected_set = set(expected)
    present_set = set(present)
    missing = sorted(expected_set - present_set)
    extra = sorted(present_set - expected_set)
    return {
        "ok": not missing,
        "expected": sorted(expected_set),
        "present": sorted(present_set),
        "missing": missing,
        "extra": extra,
    }


def fetch_json(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object from {url}")
        return payload


def check_health(base_url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    url = _join_url(base_url, "/health")
    try:
        payload = fetch_json(url, timeout=timeout)
        errors = validate_health_payload(payload)
        return {
            "url": url,
            "ok": not errors,
            "status": payload.get("status"),
            "errors": errors,
            "warnings": [],
            "payload": payload,
        }
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return {"url": url, "ok": False, "errors": [str(exc)], "warnings": [], "payload": None}


def check_agent_card(
    base_url: str,
    *,
    card_url: str | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    url = card_url or _join_url(base_url, "/.well-known/agent-card.json")
    try:
        payload = fetch_json(url, timeout=timeout)
        errors = validate_agent_card_payload(payload)
        return {
            "url": url,
            "ok": not errors,
            "errors": errors,
            "warnings": [],
            "payload": payload,
        }
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        return {"url": url, "ok": False, "errors": [str(exc)], "warnings": [], "payload": None}


def check_repo_artifacts(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root)
    groups = {
        "core": _artifact_group(root, CORE_REPO_ARTIFACTS),
        "evaluation": _artifact_group(root, EVAL_ARTIFACTS),
        "telemetry": _artifact_group(root, TELEMETRY_ARTIFACTS),
        "templates": _artifact_group(root, TEMPLATE_ARTIFACTS),
        "openenv": _artifact_group(root, OPENENV_ARTIFACTS),
        "utils": _artifact_group(root, UTILITY_ARTIFACTS),
    }

    optional_groups = {
        "optional_telemetry": _artifact_group(root, OPTIONAL_TELEMETRY_ARTIFACTS),
    }

    # Preserve old top-level artifacts field using core artifacts.
    artifacts = {name: (root / name).exists() for name in CORE_REPO_ARTIFACTS}
    missing = [name for name, exists in artifacts.items() if not exists]
    required_missing = [
        f"{group_name}:{item}"
        for group_name, group in groups.items()
        for item in group["missing"]
    ]

    return {
        "root": str(root.resolve()),
        "ok": not required_missing,
        "artifacts": artifacts,
        "missing": missing,
        "required_groups": groups,
        "optional_groups": optional_groups,
        "required_missing": required_missing,
        "warnings": [
            f"optional:{group_name}:{item}"
            for group_name, group in optional_groups.items()
            for item in group["missing"]
        ],
    }


def discover_sprint4_scenarios(repo_root: str | Path = ".") -> dict[str, dict[str, Any]]:
    """Best-effort repository scan for Sprint 4 scenario identity.

    This is intentionally lightweight: it scans known config/template/script
    surfaces for scenario IDs and then fills expected metadata from validators.
    """

    root = Path(repo_root)
    search_roots = [
        root / "templates",
        root / "integrations",
        root / "scripts",
        root / "src",
        root / "aegisforge_eval",
    ]
    candidate_files: list[Path] = []
    for search_root in search_roots:
        if not search_root.exists():
            continue
        for suffix in ("*.py", "*.toml", "*.json", "*.md", "*.yaml", "*.yml"):
            candidate_files.extend(search_root.rglob(suffix))

    found: dict[str, dict[str, Any]] = {}
    for file_path in candidate_files:
        text = _read_text(file_path).lower()
        if not text:
            continue
        for scenario_id, expected in EXPECTED_SCENARIOS.items():
            name_key = _scenario_id_from_text(expected["scenario_name"])
            if scenario_id in text or name_key in text:
                found.setdefault(
                    scenario_id,
                    {
                        "ok": True,
                        "scenario_id": scenario_id,
                        "scenario_name": expected["scenario_name"],
                        "domain": expected["domain"],
                        "upstream_track": expected["upstream_track"],
                        "category": expected["category"],
                        "adapter": "openenv" if expected["domain"] not in {"agent_security"} else "security",
                        "assessment_mode": DEFAULT_ASSESSMENT_MODE,
                        "scenario_family": DEFAULT_SCENARIO_FAMILY,
                        "benchmark": BENCHMARK_NAME,
                        "selected_opponent": expected["selected_opponent"],
                        "source_url": expected["source_url"],
                        "files": [],
                        "errors": [],
                        "warnings": [],
                    },
                )
                rel = str(file_path.relative_to(root)) if file_path.is_relative_to(root) else str(file_path)
                found[scenario_id]["files"].append(rel)

    for record in found.values():
        record["files"] = sorted(set(record.get("files", [])))

    return found


def check_sprint4_readiness(repo_root: str | Path = ".") -> dict[str, Any]:
    scenarios = discover_sprint4_scenarios(repo_root)
    present_scenarios = set(scenarios)
    missing_scenarios = sorted(set(EXPECTED_SCENARIO_IDS) - present_scenarios)

    present_domains = {record["domain"] for record in scenarios.values() if record.get("domain")}
    present_upstream = {record["upstream_track"] for record in scenarios.values() if record.get("upstream_track")}
    present_categories = {record["category"] for record in scenarios.values() if record.get("category")}

    scenario_coverage = {
        "ok": not missing_scenarios,
        "expected_count": len(EXPECTED_SCENARIO_IDS),
        "present_count": len(present_scenarios),
        "missing_count": len(missing_scenarios),
        "expected_scenarios": list(EXPECTED_SCENARIO_IDS),
        "present_scenarios": sorted(present_scenarios),
        "missing_scenarios": missing_scenarios,
        "scenarios": scenarios,
    }
    upstream_track_coverage = _coverage(EXPECTED_UPSTREAM_TRACKS, present_upstream)
    domain_coverage = _coverage(EXPECTED_DOMAINS, present_domains)
    category_coverage = _coverage(EXPECTED_CATEGORIES, present_categories)

    errors = []
    errors.extend(validate_scenario_coverage(scenario_coverage))
    errors.extend(
        [
            f"missing scenario: {scenario_id}"
            for scenario_id in missing_scenarios
        ]
    )

    ok = (
        not errors
        and scenario_coverage["ok"]
        and upstream_track_coverage["ok"]
        and domain_coverage["ok"]
        and category_coverage["ok"]
    )

    check = {
        "ok": ok,
        "errors": errors,
        "warnings": [],
        "identity": {
            "assessment_mode": DEFAULT_ASSESSMENT_MODE,
            "scenario_family": DEFAULT_SCENARIO_FAMILY,
            "benchmark": BENCHMARK_NAME,
        },
        "scenario_coverage": scenario_coverage,
        "upstream_track_coverage": upstream_track_coverage,
        "domain_coverage": domain_coverage,
        "category_coverage": category_coverage,
    }
    check["errors"].extend(validate_sprint4_check_payload(check))
    return check


def check_telemetry(repo_root: str | Path = ".") -> dict[str, Any]:
    root = Path(repo_root)
    files = {
        "trace_schema": root / "src/aegisforge/telemetry/trace_schema.py",
        "episode_summary": root / "src/aegisforge/telemetry/episode_summary.py",
        "failure_taxonomy": root / "src/aegisforge/telemetry/failure_taxonomy.py",
        "scorecard": root / "src/aegisforge/telemetry/scorecard.py",
        "emitter": root / "src/aegisforge/telemetry/emitter.py",
        "events": root / "src/aegisforge/telemetry/events.py",
    }

    presence = {name: path.exists() for name, path in files.items()}
    combined_text = "\n".join(_read_text(path) for path in files.values() if path.exists())

    missing_identity_fields = [
        field for field in IDENTITY_FIELDS if field not in combined_text
    ]
    telemetry = {
        **presence,
        "preserves_identity": not missing_identity_fields,
        "required_fields": list(IDENTITY_FIELDS),
        "missing_fields": missing_identity_fields,
    }
    errors = validate_telemetry_payload(telemetry)
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": [],
        "payload": telemetry,
        "extra": telemetry,
    }


def check_safety_scope() -> dict[str, Any]:
    safety_scope = {
        "benchmark_only": True,
        "controlled_only": True,
        "no_real_world_targeting": True,
        "no_secret_extraction_from_real_systems": True,
        "no_persistence_or_evasion": True,
        "notes": "B4 gate validates benchmark/submission readiness only.",
    }
    errors = validate_safety_scope(safety_scope)
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": [],
        "payload": safety_scope,
        "extra": safety_scope,
    }


def _summarize_checks(checks: dict[str, Any]) -> dict[str, int]:
    total = len(checks)
    passed = sum(1 for check in checks.values() if isinstance(check, dict) and check.get("ok") is True)
    failed = total - passed
    warnings = sum(
        len(check.get("warnings") or [])
        for check in checks.values()
        if isinstance(check, dict)
    )
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "warnings": warnings,
    }


def _submission_readiness(checks: dict[str, Any]) -> dict[str, Any]:
    blocking = [
        name
        for name, check in checks.items()
        if isinstance(check, dict) and check.get("ok") is not True
    ]
    warnings = [
        f"{name}: {warning}"
        for name, check in checks.items()
        if isinstance(check, dict)
        for warning in (check.get("warnings") or [])
    ]
    return {
        "ok": not blocking,
        "status": "ready" if not blocking else "blocked",
        "blocking_issues": blocking,
        "warnings": warnings,
        "next_steps": [
            f"Fix check: {name}" for name in blocking
        ],
    }


def run_submission_gate(
    *,
    base_url: str,
    card_url: str | None = None,
    repo_root: str | Path = ".",
    timeout: float = 8.0,
    strict_sprint4: bool = True,
) -> dict[str, Any]:
    health = check_health(base_url, timeout=timeout)
    agent_card = check_agent_card(base_url, card_url=card_url, timeout=timeout)
    repo = check_repo_artifacts(repo_root)
    telemetry = check_telemetry(repo_root)
    safety = check_safety_scope()

    checks: dict[str, Any] = {
        "health": health,
        "agent_card": agent_card,
        "repo": repo,
        "telemetry": telemetry,
        "safety": safety,
    }

    sprint4 = check_sprint4_readiness(repo_root)
    if strict_sprint4:
        checks["sprint4"] = sprint4
    else:
        sprint4 = {**sprint4, "ok": True, "warnings": [*sprint4.get("warnings", []), "strict_sprint4 disabled"]}
        checks["sprint4"] = sprint4

    readiness = _submission_readiness(checks)
    summary = _summarize_checks(checks)
    ok = readiness["ok"]

    return {
        "title": "B4 Submission Gate Report",
        "schema_version": "2.0",
        "base_url": base_url,
        "timestamp": _now(),
        "ok": ok,
        "benchmark": {
            "name": BENCHMARK_NAME,
            "phase": "AgentX-AgentBeats Phase 2",
            "sprint": "Sprint 4",
            "assessment_mode": DEFAULT_ASSESSMENT_MODE,
            "scenario_family": DEFAULT_SCENARIO_FAMILY,
            "benchmark_scope": "controlled_only",
            "leaderboard_ready": False,
        },
        "safety_scope": safety["payload"],
        "checks": checks,
        "summary": summary,
        "scenario_coverage": sprint4["scenario_coverage"],
        "upstream_track_coverage": sprint4["upstream_track_coverage"],
        "domain_coverage": sprint4["domain_coverage"],
        "category_coverage": sprint4["category_coverage"],
        "telemetry": telemetry["payload"],
        "submission_readiness": readiness,
        "metadata": {
            "repo_root": str(Path(repo_root).resolve()),
            "strict_sprint4": strict_sprint4,
            "expected_scenarios": list(EXPECTED_SCENARIO_IDS),
            "expected_domains": list(EXPECTED_DOMAINS),
            "expected_upstream_tracks": list(EXPECTED_UPSTREAM_TRACKS),
            "expected_categories": list(EXPECTED_CATEGORIES),
        },
    }
