from __future__ import annotations

"""Run smoke validation across the configured OmniBench mission mix.

This script intentionally validates *domain entries* rather than requiring each
real domain environment to be fully implemented already. Each entry uses a
fixture-specific reset payload plus one sample action, while the current server
may still route those requests to the demo fallback environment.

Usage:
    python smoke_test_all_domains.py
    python smoke_test_all_domains.py --base-url http://127.0.0.1:8000 --verbose
    python smoke_test_all_domains.py --only research web --json
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PACKAGE_ROOT = Path(__file__).resolve().parent
PARENT_ROOT = PACKAGE_ROOT.parent
if str(PARENT_ROOT) not in sys.path:
    sys.path.insert(0, str(PARENT_ROOT))

from omnibench_aegis_env.client import OpenEnvClient, OpenEnvClientError  # noqa: E402

DEFAULT_BASE_URL = os.getenv("OPENENV_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_TIMEOUT = float(os.getenv("OPENENV_TIMEOUT", "10"))


class SmokeFailure(RuntimeError):
    pass


FIXTURE_BY_DOMAIN = {
    "research": "sample_actions_research.json",
    "web": "sample_actions_web.json",
    "computer_use": "sample_actions_web.json",
    "coding": "sample_actions_coding.json",
    "finance": "sample_actions_finance.json",
    "healthcare": "sample_actions_finance.json",
    "agent_safety": "sample_actions_agent_safety.json",
    "multi_agent": "sample_actions_research.json",
    "tau2": "sample_actions_research.json",
    "game": "sample_actions_research.json",
    "officeqa": "sample_actions_finance.json",
    "business_process": "sample_actions_finance.json",
    "crmarena": "sample_actions_finance.json",
    "fieldwork": "sample_actions_research.json",
    "osworld": "sample_actions_web.json",
}


def load_json(name: str) -> Any:
    path = PACKAGE_ROOT / name
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
            errors.extend(is_subset(value, actual[idx], f"{path}[{idx}]") )
        return errors

    if expected != actual:
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    return errors


def summarize_payload(payload: Any, max_chars: int = 180) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _fixture_name_for_domain(domain: str) -> str:
    return FIXTURE_BY_DOMAIN.get(domain, "sample_actions_research.json")


def _normalize_only(values: Sequence[str] | None) -> set[str]:
    return {str(value).strip() for value in (values or []) if str(value).strip()}


def _load_domain_fixture(domain: str) -> Mapping[str, Any]:
    data = load_json(_fixture_name_for_domain(domain))
    if not isinstance(data, Mapping):
        raise SmokeFailure(f"fixture for domain '{domain}' must be a JSON object")
    return data


def _first_action_from_fixture(fixture: Mapping[str, Any]) -> Mapping[str, Any]:
    examples = fixture.get("action_examples")
    if isinstance(examples, Mapping):
        shorthand = examples.get("shorthand")
        if isinstance(shorthand, list) and shorthand and isinstance(shorthand[0], Mapping):
            return dict(shorthand[0])
        canonical = examples.get("canonical")
        if isinstance(canonical, list) and canonical and isinstance(canonical[0], Mapping):
            return dict(canonical[0])

    action_plan = fixture.get("action_plan")
    if isinstance(action_plan, list) and action_plan and isinstance(action_plan[0], Mapping):
        return dict(action_plan[0])

    if "action" in fixture or "name" in fixture:
        return dict(fixture)

    return {"action": "advance", "value": 1}


def _build_reset_payload(mission_entry: Mapping[str, Any], fixture: Mapping[str, Any], default_env_id: str) -> dict[str, Any]:
    reset_payload = fixture.get("reset_payload")
    payload = dict(reset_payload) if isinstance(reset_payload, Mapping) else {}

    payload.setdefault("seed", 42)
    payload["scenario_id"] = str(mission_entry.get("scenario_id") or payload.get("scenario_id") or "UnknownScenario")

    domain = str(mission_entry.get("domain") or fixture.get("domain") or "general")
    payload.setdefault("mission_id", f"{payload['scenario_id'].lower()}_{domain}_smoke")

    options = dict(payload.get("options") or {})
    options.setdefault("env_id", default_env_id)
    options.setdefault("max_steps", 5)
    options.setdefault("target_score", 1)
    options["domain"] = domain
    payload["options"] = options

    return payload


def _health_ok(payload: Mapping[str, Any]) -> bool:
    return all(key in payload for key in ("status", "env", "initialized")) and payload.get("status") == "ok"


def run_smoke_for_entry(
    client: OpenEnvClient,
    mission_entry: Mapping[str, Any],
    *,
    default_env_id: str,
    expected_reset: Any,
    expected_step: Any,
    expected_state: Any,
    verbose: bool = False,
) -> dict[str, Any]:
    domain = str(mission_entry.get("domain") or "general")
    scenario_id = str(mission_entry.get("scenario_id") or "unknown")
    fixture = _load_domain_fixture(domain)
    reset_payload = _build_reset_payload(mission_entry, fixture, default_env_id=default_env_id)
    action_payload = _first_action_from_fixture(fixture)

    entry_report: dict[str, Any] = {
        "domain": domain,
        "scenario_id": scenario_id,
        "fixture": _fixture_name_for_domain(domain),
        "checks": {},
    }

    health = client.health()
    health_ok = _health_ok(health)
    entry_report["checks"]["health"] = {
        "ok": health_ok,
        "summary": summarize_payload(health),
    }
    if not health_ok:
        raise SmokeFailure(f"[{domain}] /health did not satisfy the minimal contract")
    if verbose:
        print(f"[ok] {domain}: health")

    reset = client.reset(reset_payload)
    reset_errors = is_subset(expected_reset, reset)
    entry_report["checks"]["reset"] = {
        "ok": not reset_errors,
        "errors": reset_errors,
        "request": reset_payload,
        "summary": summarize_payload(reset),
    }
    if reset_errors:
        raise SmokeFailure(f"[{domain}] /reset did not match expected_reset_min.json")
    if verbose:
        print(f"[ok] {domain}: reset ({scenario_id})")

    step = client.step(action_payload)
    step_errors = is_subset(expected_step, step)
    entry_report["checks"]["step"] = {
        "ok": not step_errors,
        "errors": step_errors,
        "request": dict(action_payload),
        "summary": summarize_payload(step),
    }
    if step_errors:
        raise SmokeFailure(f"[{domain}] /step did not match expected_step_min.json")
    if verbose:
        print(f"[ok] {domain}: step")

    state = client.state()
    state_errors = is_subset(expected_state, state)
    entry_report["checks"]["state"] = {
        "ok": not state_errors,
        "errors": state_errors,
        "summary": summarize_payload(state),
    }
    if state_errors:
        raise SmokeFailure(f"[{domain}] /state did not match expected_state_min.json")
    if verbose:
        print(f"[ok] {domain}: state")

    entry_report["ok"] = all(section.get("ok") for section in entry_report["checks"].values())
    return entry_report


def run_all_domain_smokes(
    *,
    base_url: str,
    timeout: float,
    only: Sequence[str] | None = None,
    include_non_smoke: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    mission_mix = load_json("mission_mix.json")
    if not isinstance(mission_mix, Mapping):
        raise SmokeFailure("mission_mix.json must be a JSON object")

    entries = mission_mix.get("primary_mix")
    if not isinstance(entries, list):
        raise SmokeFailure("mission_mix.json is missing primary_mix")

    only_set = _normalize_only(only)
    default_env_id = str(mission_mix.get("default_env_id") or "omnibench_aegis_env:demo")

    expected_reset = load_json("expected_reset_min.json")
    expected_step = load_json("expected_step_min.json")
    expected_state = load_json("expected_state_min.json")

    client = OpenEnvClient(base_url=base_url, timeout=timeout)

    selected_entries: list[Mapping[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        domain = str(entry.get("domain") or "")
        scenario_id = str(entry.get("scenario_id") or "")
        smoke_flag = bool(entry.get("smoke", False))
        if only_set and domain not in only_set and scenario_id not in only_set:
            continue
        if not include_non_smoke and not smoke_flag:
            continue
        selected_entries.append(entry)

    if not selected_entries:
        raise SmokeFailure("no mission entries matched the requested filters")

    report: dict[str, Any] = {
        "ok": True,
        "base_url": base_url,
        "timeout": timeout,
        "default_env_id": default_env_id,
        "selected": [
            {"domain": str(entry.get("domain") or "general"), "scenario_id": str(entry.get("scenario_id") or "unknown")}
            for entry in selected_entries
        ],
        "domains": [],
    }

    for entry in selected_entries:
        entry_report = run_smoke_for_entry(
            client,
            entry,
            default_env_id=default_env_id,
            expected_reset=expected_reset,
            expected_step=expected_step,
            expected_state=expected_state,
            verbose=verbose,
        )
        report["domains"].append(entry_report)

    report["ok"] = all(item.get("ok") for item in report["domains"])
    report["passed"] = sum(1 for item in report["domains"] if item.get("ok"))
    report["total"] = len(report["domains"])
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run smoke checks across the OmniBench mission mix.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Environment server base URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument("--only", nargs="*", help="Restrict to one or more domains or scenario IDs")
    parser.add_argument(
        "--include-non-smoke",
        action="store_true",
        help="Include mission entries even if their smoke flag is false",
    )
    parser.add_argument("--verbose", action="store_true", help="Print live step-by-step output")
    parser.add_argument("--json", action="store_true", help="Print the final report as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        report = run_all_domain_smokes(
            base_url=args.base_url,
            timeout=args.timeout,
            only=args.only,
            include_non_smoke=args.include_non_smoke,
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
    else:
        print("[ok] all selected domain smokes passed")
        print(f"- base_url: {report['base_url']}")
        print(f"- passed: {report['passed']}/{report['total']}")
        for item in report["domains"]:
            print(f"- {item['domain']} ({item['scenario_id']}): PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
