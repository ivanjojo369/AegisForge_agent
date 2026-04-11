from __future__ import annotations

"""Run lightweight evaluation checks for omnibench_aegis_env.

Aligned to the current BaseDomain-backed HTTP contract:
    health -> reset -> step[*] -> state

Current `/state` returns an *envelope*:
    {
      "env_id": ...,
      "scenario_id": ...,
      "state": {...},
      "last_observation": {...},
      "last_info": {...}
    }

This version supports explicit file glob filters and ignores aggregate payload files
by default so rollout/eval runs do not accidentally mix aggregate JSON artifacts.
"""

import argparse
import fnmatch
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parent
PARENT_ROOT = PACKAGE_ROOT.parent
if str(PARENT_ROOT) not in sys.path:
    sys.path.insert(0, str(PARENT_ROOT))

try:
    from scripts.build_sample_payloads import build_payloads
    from omnibench_aegis_env.client import OpenEnvClient, OpenEnvClientError
except ImportError:  # pragma: no cover - script execution fallback
    from omnibench_aegis_env.scripts.build_sample_payloads import build_payloads
    from omnibench_aegis_env.client import OpenEnvClient, OpenEnvClientError


DEFAULT_BASE_URL = os.getenv("OPENENV_BASE_URL", "http://127.0.0.1:8001")
DEFAULT_TIMEOUT = float(os.getenv("OPENENV_TIMEOUT", "10"))
DEFAULT_PAYLOAD_DIR = PACKAGE_ROOT / "generated_payloads"
DEFAULT_OUTPUT_DIR = PACKAGE_ROOT / "eval_results"
DEFAULT_ENV_NAME = "omnibench_aegis_env"
DEFAULT_EXCLUDE_GLOBS = (
    "all_*.json",
    "*_summary.json",
    "index.json",
    "variant_matrix.json",
    "variant_matrix_summary.json",
    "league_index.json",
)


class EvalError(RuntimeError):
    """Raised when evaluation setup or execution fails."""


EVAL_REQUIRED_HEALTH_KEYS = ("status", "env", "initialized")
EVAL_REQUIRED_RESET_KEYS = ("observation", "state", "info")
EVAL_REQUIRED_STEP_KEYS = ("observation", "reward", "done", "truncated", "info", "state")
EVAL_REQUIRED_STATE_ENVELOPE_KEYS = ("env_id", "scenario_id", "state", "last_observation", "last_info")
EVAL_REQUIRED_NESTED_STATE_KEYS = (
    "step_count",
    "done",
    "success",
    "scenario_id",
)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_only(values: Sequence[str] | None) -> set[str]:
    return {str(value).strip() for value in (values or []) if str(value).strip()}


def _normalize_globs(values: Sequence[str] | None) -> list[str]:
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    return slug or "item"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _missing_keys(payload: Mapping[str, Any], required: Sequence[str]) -> list[str]:
    return [key for key in required if key not in payload]


def _unwrap_state_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    state = payload.get("state")
    if isinstance(state, Mapping):
        return dict(state)
    return dict(payload)


def _extract_success(value: Mapping[str, Any]) -> bool:
    return bool(value.get("success") or False)


def _last_action_name(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("name") or value.get("action") or "").strip()
    return str(value or "").strip()


def _expected_action_name(step_spec: Mapping[str, Any]) -> str:
    return str(step_spec.get("action") or step_spec.get("name") or "").strip()


def _matches_any(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _iter_eval_candidate_paths(payload_dir: Path) -> list[Path]:
    return sorted(path for path in payload_dir.glob("*.openenv_eval.json") if path.is_file())


def _load_eval_payloads(
    payload_dir: Path,
    *,
    include_globs: Sequence[str] | None = None,
    exclude_globs: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    include_patterns = _normalize_globs(include_globs)
    exclude_patterns = list(DEFAULT_EXCLUDE_GLOBS) + _normalize_globs(exclude_globs)

    payload_paths = _iter_eval_candidate_paths(payload_dir)
    selected_paths: list[Path] = []
    for path in payload_paths:
        name = path.name
        if _matches_any(name, exclude_patterns):
            continue
        if include_patterns and not _matches_any(name, include_patterns):
            continue
        selected_paths.append(path)

    if include_patterns and not selected_paths:
        raise EvalError(
            f"no eval payload files matched include_glob under {payload_dir}: {', '.join(include_patterns)}"
        )

    if not selected_paths:
        index_path = payload_dir / "index.json"
        if index_path.exists():
            index_payload = load_json(index_path)
            if not isinstance(index_payload, Mapping):
                raise EvalError("generated payload index must be a JSON object")
            files = index_payload.get("files")
            if not isinstance(files, list):
                raise EvalError("generated payload index is missing the files list")
            for name in files:
                if not isinstance(name, str) or not name.endswith(".openenv_eval.json"):
                    continue
                if _matches_any(name, exclude_patterns):
                    continue
                if include_patterns and not _matches_any(name, include_patterns):
                    continue
                path = payload_dir / name
                if path.exists():
                    selected_paths.append(path)

    if not selected_paths:
        raise EvalError(
            f"no openenv eval payload files found in {payload_dir}; use --include-glob to target files explicitly"
        )

    payloads: list[dict[str, Any]] = []
    for path in selected_paths:
        item = load_json(path)
        if isinstance(item, Mapping):
            payload = dict(item)
            payload.setdefault("__source_file__", path.name)
            payloads.append(payload)
    return payloads


def _select_payloads(
    payloads: Sequence[Mapping[str, Any]],
    *,
    only: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    only_set = _normalize_only(only)
    selected: list[dict[str, Any]] = []
    for payload in payloads:
        domain = str(payload.get("domain") or "general")
        scenario_id = str(payload.get("scenario_id") or "UnknownScenario")
        source_file = str(payload.get("__source_file__") or "")
        if only_set and domain not in only_set and scenario_id not in only_set and source_file not in only_set:
            continue
        selected.append(dict(payload))
    return selected


def _score_from_checks(checks: Mapping[str, Any]) -> float:
    score = 0.0
    if checks.get("health"):
        score += 0.2
    if checks.get("reset"):
        score += 0.2
    if int(checks.get("step_count") or 0) > 0:
        score += 0.2
    if checks.get("state"):
        score += 0.2
    if checks.get("success_path"):
        score += 0.2
    return round(score, 4)


def _evaluate_single_payload(
    *,
    client: OpenEnvClient,
    payload: Mapping[str, Any],
    eval_index: int,
    require_success: bool = False,
) -> dict[str, Any]:
    domain = str(payload.get("domain") or "general")
    scenario_id = str(payload.get("scenario_id") or "UnknownScenario")
    env_name = str(payload.get("env_name") or DEFAULT_ENV_NAME)
    reset_payload = dict(payload.get("reset_payload") or {})
    action_plan = list(payload.get("action_plan") or [])

    result: dict[str, Any] = {
        "kind": "openenv_eval_result",
        "timestamp": _now_iso(),
        "eval_index": eval_index,
        "source_file": payload.get("__source_file__"),
        "domain": domain,
        "scenario_id": scenario_id,
        "env_name": env_name,
        "base_url": client.base_url,
        "status": "pass",
        "summary": "evaluation completed",
        "score": 0.0,
        "error": None,
        "require_success": bool(require_success),
        "checks": {
            "health": False,
            "reset": False,
            "step_count": 0,
            "state": False,
            "success_path": False,
        },
        "health": None,
        "reset": None,
        "steps": [],
        "final_state": None,
        "missing": {},
    }

    try:
        health = client.health()
        result["health"] = health
        missing_health = _missing_keys(health, EVAL_REQUIRED_HEALTH_KEYS)
        if missing_health:
            raise EvalError(f"/health missing keys: {', '.join(missing_health)}")
        if str(health.get("status")) != "ok":
            raise EvalError("/health returned status different from 'ok'")
        if str(health.get("env") or env_name) != env_name:
            raise EvalError(f"/health env mismatch: expected '{env_name}' got '{health.get('env')}'")
        result["checks"]["health"] = True

        reset_response = client.reset(reset_payload)
        result["reset"] = reset_response
        missing_reset = _missing_keys(reset_response, EVAL_REQUIRED_RESET_KEYS)
        if missing_reset:
            raise EvalError(f"/reset missing keys: {', '.join(missing_reset)}")
        reset_state = reset_response.get("state")
        if not isinstance(reset_state, Mapping):
            raise EvalError("/reset.state is not a JSON object")
        if str(reset_response.get("scenario_id") or scenario_id) != scenario_id:
            raise EvalError(
                f"/reset scenario_id mismatch: expected '{scenario_id}' got '{reset_response.get('scenario_id')}'"
            )
        if str(reset_response.get("env_id") or "").strip() == "":
            raise EvalError("/reset env_id is empty")
        result["checks"]["reset"] = True

        for idx, step_spec in enumerate(action_plan, start=1):
            if not isinstance(step_spec, Mapping):
                raise EvalError(f"action_plan[{idx}] must be a JSON object")

            step_response = client.step(step_spec)
            missing_step = _missing_keys(step_response, EVAL_REQUIRED_STEP_KEYS)
            if missing_step:
                raise EvalError(f"/step #{idx} missing keys: {', '.join(missing_step)}")

            state = step_response.get("state") or {}
            if not isinstance(state, Mapping):
                raise EvalError(f"/step #{idx}.state is not a JSON object")

            expected_action = _expected_action_name(step_spec)
            got_last_action = _last_action_name(state.get("last_action"))
            if expected_action and got_last_action and got_last_action != expected_action:
                raise EvalError(
                    f"/step #{idx} last_action mismatch: expected '{expected_action}' got '{got_last_action}'"
                )

            step_record = {
                "index": idx,
                "request": dict(step_spec),
                "response": dict(step_response),
            }
            result["steps"].append(step_record)
            result["checks"]["step_count"] = idx

            step_info = step_response.get("info") or {}
            if isinstance(step_info, Mapping) and _extract_success(step_info):
                result["checks"]["success_path"] = True
            if _extract_success(state):
                result["checks"]["success_path"] = True

            if bool(step_response.get("done")) or bool(step_response.get("truncated")):
                break

        final_state_envelope = client.state()
        result["final_state"] = final_state_envelope
        missing_state_env = _missing_keys(final_state_envelope, EVAL_REQUIRED_STATE_ENVELOPE_KEYS)
        if missing_state_env:
            raise EvalError(f"/state missing envelope keys: {', '.join(missing_state_env)}")

        final_state = _unwrap_state_payload(final_state_envelope)
        missing_nested_state = _missing_keys(final_state, EVAL_REQUIRED_NESTED_STATE_KEYS)
        if missing_nested_state:
            raise EvalError(f"/state.state missing keys: {', '.join(missing_nested_state)}")
        result["checks"]["state"] = True

        if _extract_success(final_state):
            result["checks"]["success_path"] = True

        result["score"] = _score_from_checks(result["checks"])

        if require_success and not result["checks"]["success_path"]:
            result["status"] = "warn"
            result["summary"] = "contract validated but success path was not reached"
        elif result["score"] < 0.8:
            result["status"] = "warn"
            result["summary"] = "evaluation completed with partial contract coverage"
        elif result["checks"]["success_path"]:
            result["summary"] = "contract and success path validated"
        else:
            result["summary"] = "contract validated; success path not required"

    except (OpenEnvClientError, EvalError) as exc:
        result["status"] = "fail"
        result["error"] = str(exc)
        result["summary"] = "evaluation failed"
        result["score"] = _score_from_checks(result["checks"])
    except Exception as exc:  # pragma: no cover - defensive CLI fallback
        result["status"] = "fail"
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        result["summary"] = "evaluation failed"
        result["score"] = _score_from_checks(result["checks"])

    return result


def run_eval(
    *,
    base_url: str,
    timeout: float,
    payload_dir: Path,
    output_dir: Path,
    only: Sequence[str] | None = None,
    include_glob: Sequence[str] | None = None,
    exclude_glob: Sequence[str] | None = None,
    include_non_smoke: bool = False,
    rebuild_payloads: bool = False,
    require_success: bool = False,
) -> dict[str, Any]:
    if rebuild_payloads:
        build_payloads(
            base_url=base_url,
            timeout=timeout,
            output_dir=payload_dir,
            only=only,
            include_non_smoke=include_non_smoke,
        )

    payloads = _load_eval_payloads(payload_dir, include_globs=include_glob, exclude_globs=exclude_glob)
    payloads = _select_payloads(payloads, only=only)
    if not payloads:
        raise EvalError("no openenv eval payloads matched the requested filters")

    output_dir.mkdir(parents=True, exist_ok=True)
    client = OpenEnvClient(base_url=base_url, timeout=timeout)

    results: list[dict[str, Any]] = []
    per_run_files: list[str] = []

    for idx, payload in enumerate(payloads, start=1):
        result = _evaluate_single_payload(
            client=client,
            payload=payload,
            eval_index=idx,
            require_success=require_success,
        )
        results.append(result)

        file_name = f"{idx:03d}__{_slugify(result['domain'])}__{_slugify(result['scenario_id'])}.eval.json"
        dump_json(output_dir / file_name, result)
        per_run_files.append(file_name)

    scores = [float(item.get("score") or 0.0) for item in results]
    summary = {
        "ok": True,
        "timestamp": _now_iso(),
        "base_url": base_url.rstrip("/"),
        "timeout": timeout,
        "payload_dir": str(payload_dir),
        "output_dir": str(output_dir),
        "require_success": bool(require_success),
        "include_glob": _normalize_globs(include_glob),
        "exclude_glob": list(DEFAULT_EXCLUDE_GLOBS) + _normalize_globs(exclude_glob),
        "count": len(results),
        "passes": sum(1 for item in results if item.get("status") == "pass"),
        "warnings": sum(1 for item in results if item.get("status") == "warn"),
        "fails": sum(1 for item in results if item.get("status") == "fail"),
        "score_mean": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "score_min": round(min(scores), 4) if scores else 0.0,
        "score_max": round(max(scores), 4) if scores else 0.0,
        "results": results,
        "files": per_run_files,
    }

    dump_json(output_dir / "eval_summary.json", summary)
    with (output_dir / "eval_results.jsonl").open("w", encoding="utf-8") as fh:
        for item in results:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run lightweight evaluation checks for omnibench_aegis_env.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Environment server base URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument(
        "--payload-dir",
        default=str(DEFAULT_PAYLOAD_DIR),
        help="Directory containing generated OpenEnv eval payloads",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where evaluation result artifacts will be written",
    )
    parser.add_argument("--only", nargs="*", help="Restrict evaluation to one or more domains or scenario IDs")
    parser.add_argument(
        "--include-glob",
        action="append",
        help="Only include payload files whose basename matches this glob. May be repeated.",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        help="Additional payload filename glob to exclude. May be repeated.",
    )
    parser.add_argument(
        "--include-non-smoke",
        action="store_true",
        help="When rebuilding payloads, include mission entries whose smoke flag is false",
    )
    parser.add_argument(
        "--rebuild-payloads",
        action="store_true",
        help="Regenerate payload bundles before running the evaluation",
    )
    parser.add_argument(
        "--require-success",
        action="store_true",
        help="Mark an evaluation as warn if the contract passes but success=false",
    )
    parser.add_argument("--json", action="store_true", help="Print the final summary as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        summary = run_eval(
            base_url=args.base_url,
            timeout=args.timeout,
            payload_dir=Path(args.payload_dir).resolve(),
            output_dir=Path(args.output_dir).resolve(),
            only=args.only,
            include_glob=args.include_glob,
            exclude_glob=args.exclude_glob,
            include_non_smoke=args.include_non_smoke,
            rebuild_payloads=args.rebuild_payloads,
            require_success=args.require_success,
        )
    except EvalError as exc:
        report = {"ok": False, "error": str(exc), "type": "contract_error"}
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1
    except Exception as exc:  # pragma: no cover - defensive CLI fallback
        report = {"ok": False, "error": str(exc), "type": exc.__class__.__name__}
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {report['error']}")
        return 1

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print("[ok] evaluation run completed")
        print(f"- output_dir: {summary['output_dir']}")
        print(f"- count: {summary['count']}")
        print(f"- passes: {summary['passes']}")
        print(f"- warnings: {summary['warnings']}")
        print(f"- fails: {summary['fails']}")
        print(f"- score_mean: {summary['score_mean']}")
        print(f"- score_min: {summary['score_min']}")
        print(f"- score_max: {summary['score_max']}")
        for name in summary["files"]:
            print(f"- {name}")
        print("- eval_summary.json")
        print("- eval_results.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
