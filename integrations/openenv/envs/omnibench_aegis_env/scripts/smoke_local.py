from __future__ import annotations

"""Local smoke checker for omnibench_aegis_env.

Usage:
    python smoke_local.py
    python smoke_local.py --base-url http://127.0.0.1:8000
    python -m omnibench_aegis_env.smoke_local --verbose

What it validates:
- GET  /health
- POST /reset using env_seed.json
- POST /step using the first sample action available
- GET  /state
- minimal subset matching against expected_* fixtures
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parent
PARENT_ROOT = PACKAGE_ROOT.parent
if str(PARENT_ROOT) not in sys.path:
    sys.path.insert(0, str(PARENT_ROOT))

from omnibench_aegis_env.client import OpenEnvClient, OpenEnvClientError  # noqa: E402


DEFAULT_BASE_URL = os.getenv("OPENENV_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_TIMEOUT = float(os.getenv("OPENENV_TIMEOUT", "10"))


class SmokeFailure(RuntimeError):
    pass


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
            errors.extend(is_subset(value, actual[idx], f"{path}[{idx}]"))
        return errors

    if expected != actual:
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    return errors


def choose_step_payload() -> Mapping[str, Any]:
    for candidate in (
        "sample_actions_research.json",
        "sample_actions_web.json",
        "sample_actions_coding.json",
        "sample_actions_finance.json",
        "sample_actions_agent_safety.json",
    ):
        data = load_json(candidate)
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, Mapping):
                return dict(first)
        if isinstance(data, Mapping):
            plan = data.get("action_plan")
            if isinstance(plan, list) and plan and isinstance(plan[0], Mapping):
                return dict(plan[0])
            if "action" in data or "name" in data:
                return dict(data)
    return {"action": "advance", "value": 1}


def summarize_payload(payload: Any, max_chars: int = 180) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def run_smoke(base_url: str, timeout: float, verbose: bool = False) -> dict[str, Any]:
    client = OpenEnvClient(base_url=base_url, timeout=timeout)

    env_seed = load_json("env_seed.json")
    expected_reset = load_json("expected_reset_min.json")
    expected_step = load_json("expected_step_min.json")
    expected_state = load_json("expected_state_min.json")
    step_payload = choose_step_payload()

    report: dict[str, Any] = {
        "base_url": base_url,
        "timeout": timeout,
        "checks": {},
    }

    health = client.health()
    health_ok = all(key in health for key in ("status", "env", "initialized")) and health.get("status") == "ok"
    report["checks"]["health"] = {
        "ok": health_ok,
        "summary": summarize_payload(health),
    }
    if not health_ok:
        raise SmokeFailure("/health did not satisfy the minimal contract")
    if verbose:
        print("[ok] health", summarize_payload(health))

    reset = client.reset(env_seed if isinstance(env_seed, Mapping) else None)
    reset_errors = is_subset(expected_reset, reset)
    report["checks"]["reset"] = {
        "ok": not reset_errors,
        "errors": reset_errors,
        "summary": summarize_payload(reset),
    }
    if reset_errors:
        raise SmokeFailure("/reset did not match expected_reset_min.json")
    if verbose:
        print("[ok] reset", summarize_payload(reset))

    step = client.step(step_payload)
    step_errors = is_subset(expected_step, step)
    report["checks"]["step"] = {
        "ok": not step_errors,
        "errors": step_errors,
        "request": dict(step_payload),
        "summary": summarize_payload(step),
    }
    if step_errors:
        raise SmokeFailure("/step did not match expected_step_min.json")
    if verbose:
        print("[ok] step", summarize_payload(step))

    state = client.state()
    state_errors = is_subset(expected_state, state)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a local smoke check against omnibench_aegis_env.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Environment server base URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="Print live step-by-step output")
    parser.add_argument("--json", action="store_true", help="Print the final report as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        report = run_smoke(base_url=args.base_url, timeout=args.timeout, verbose=args.verbose)
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
        print("[ok] local smoke passed")
        print(f"- base_url: {report['base_url']}")
        for name, section in report["checks"].items():
            print(f"- {name}: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
