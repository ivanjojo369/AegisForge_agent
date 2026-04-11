from __future__ import annotations

"""Run lightweight rollout episodes against omnibench_aegis_env.

Aligned to the current BaseDomain-backed server contract where `/state`
returns an envelope with nested state, observation and info.

This version supports explicit file glob filters and ignores aggregate payload files
by default so rollout runs do not accidentally mix aggregate JSON artifacts.
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
DEFAULT_OUTPUT_DIR = PACKAGE_ROOT / "rollout_results"
DEFAULT_PAYLOAD_DIR = PACKAGE_ROOT / "generated_payloads"
DEFAULT_EXCLUDE_GLOBS = (
    "all_*.json",
    "*_summary.json",
    "index.json",
    "variant_matrix.json",
    "variant_matrix_summary.json",
    "league_index.json",
)


class RolloutError(RuntimeError):
    """Raised when rollout setup or execution fails."""


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


def _matches_any(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _iter_bundle_candidate_paths(payload_dir: Path) -> list[Path]:
    return sorted(path for path in payload_dir.glob("*.client_bundle.json") if path.is_file())


def _load_payload_bundles(
    payload_dir: Path,
    *,
    include_globs: Sequence[str] | None = None,
    exclude_globs: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    include_patterns = _normalize_globs(include_globs)
    exclude_patterns = list(DEFAULT_EXCLUDE_GLOBS) + _normalize_globs(exclude_globs)

    bundle_paths = _iter_bundle_candidate_paths(payload_dir)
    selected_paths: list[Path] = []
    for path in bundle_paths:
        name = path.name
        if _matches_any(name, exclude_patterns):
            continue
        if include_patterns and not _matches_any(name, include_patterns):
            continue
        selected_paths.append(path)

    if include_patterns and not selected_paths:
        raise RolloutError(
            f"no client bundle files matched include_glob under {payload_dir}: {', '.join(include_patterns)}"
        )

    if not selected_paths:
        index_path = payload_dir / "index.json"
        if index_path.exists():
            index_payload = load_json(index_path)
            if not isinstance(index_payload, Mapping):
                raise RolloutError("generated payload index must be a JSON object")
            files = index_payload.get("files")
            if not isinstance(files, list):
                raise RolloutError("generated payload index is missing the files list")
            for name in files:
                if not isinstance(name, str) or not name.endswith(".client_bundle.json"):
                    continue
                if _matches_any(name, exclude_patterns):
                    continue
                if include_patterns and not _matches_any(name, include_patterns):
                    continue
                path = payload_dir / name
                if path.exists():
                    selected_paths.append(path)

    if not selected_paths:
        raise RolloutError(
            f"no client bundle files found in {payload_dir}; use --include-glob to target files explicitly"
        )

    bundles: list[dict[str, Any]] = []
    for path in selected_paths:
        payload = load_json(path)
        if isinstance(payload, Mapping):
            bundle = dict(payload)
            bundle.setdefault("__source_file__", path.name)
            bundles.append(bundle)
    return bundles


def _select_bundles(
    bundles: Sequence[Mapping[str, Any]],
    *,
    only: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    only_set = _normalize_only(only)
    selected: list[dict[str, Any]] = []
    for bundle in bundles:
        domain = str(bundle.get("domain") or "general")
        scenario_id = str(bundle.get("scenario_id") or "UnknownScenario")
        source_file = str(bundle.get("__source_file__") or "")
        if only_set and domain not in only_set and scenario_id not in only_set and source_file not in only_set:
            continue
        selected.append(dict(bundle))
    return selected


def _unwrap_state_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    state = payload.get("state")
    if isinstance(state, Mapping):
        return dict(state)
    return dict(payload)


def _run_single_rollout(
    *,
    client: OpenEnvClient,
    bundle: Mapping[str, Any],
    rollout_index: int,
    require_success: bool = False,
) -> dict[str, Any]:
    domain = str(bundle.get("domain") or "general")
    scenario_id = str(bundle.get("scenario_id") or "UnknownScenario")
    reset_payload = dict(bundle.get("reset_payload") or {})
    action_plan = list(bundle.get("action_plan") or [])

    result: dict[str, Any] = {
        "kind": "rollout_result",
        "timestamp": _now_iso(),
        "rollout_index": rollout_index,
        "source_file": bundle.get("__source_file__"),
        "domain": domain,
        "scenario_id": scenario_id,
        "env_name": str(bundle.get("env_name") or "omnibench_aegis_env"),
        "base_url": client.base_url,
        "status": "pass",
        "error": None,
        "health": None,
        "reset": None,
        "steps": [],
        "final_state": None,
        "done": False,
        "success": False,
        "step_count": 0,
        "reward_total": 0.0,
        "require_success": bool(require_success),
    }

    try:
        health = client.health()
        result["health"] = health

        reset_response = client.reset(reset_payload)
        result["reset"] = reset_response

        total_reward = 0.0
        done = False
        truncated = False
        success = False

        for idx, step_spec in enumerate(action_plan, start=1):
            if done:
                break
            if not isinstance(step_spec, Mapping):
                continue

            step_response = client.step(step_spec)
            reward = float(step_response.get("reward") or 0.0)
            done = bool(step_response.get("done") or False)
            truncated = bool(step_response.get("truncated") or False)
            total_reward += reward

            state = step_response.get("state") or {}
            info = step_response.get("info") or {}
            if isinstance(state, Mapping) and bool(state.get("success") or False):
                success = True
            if isinstance(info, Mapping) and bool(info.get("success") or False):
                success = True

            result["steps"].append(
                {
                    "index": idx,
                    "request": dict(step_spec),
                    "response": dict(step_response),
                }
            )

        final_state_envelope = client.state()
        final_state = _unwrap_state_envelope(final_state_envelope)
        result["final_state"] = final_state_envelope
        result["step_count"] = int(final_state.get("step_count") or len(result["steps"]))
        result["reward_total"] = total_reward
        result["done"] = bool(final_state.get("done") or done or truncated)
        result["success"] = bool(final_state.get("success") or success)

        if require_success and not result["success"]:
            result["status"] = "warn"
            result["error"] = "rollout completed but success=false"

    except OpenEnvClientError as exc:
        result["status"] = "fail"
        result["error"] = str(exc)
    except Exception as exc:  # pragma: no cover - defensive runtime fallback
        result["status"] = "fail"
        result["error"] = f"{exc.__class__.__name__}: {exc}"

    return result


def run_rollouts(
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
    repeats: int = 1,
    require_success: bool = False,
) -> dict[str, Any]:
    if repeats < 1:
        raise RolloutError("repeats must be at least 1")

    if rebuild_payloads:
        build_payloads(
            base_url=base_url,
            timeout=timeout,
            output_dir=payload_dir,
            only=only,
            include_non_smoke=include_non_smoke,
        )

    bundles = _load_payload_bundles(payload_dir, include_globs=include_glob, exclude_globs=exclude_glob)
    bundles = _select_bundles(bundles, only=only)
    if not bundles:
        raise RolloutError("no client bundles matched the requested filters")

    output_dir.mkdir(parents=True, exist_ok=True)
    client = OpenEnvClient(base_url=base_url, timeout=timeout)

    results: list[dict[str, Any]] = []
    per_run_files: list[str] = []

    rollout_counter = 0
    for bundle in bundles:
        for _ in range(repeats):
            rollout_counter += 1
            result = _run_single_rollout(
                client=client,
                bundle=bundle,
                rollout_index=rollout_counter,
                require_success=require_success,
            )
            results.append(result)

            file_name = (
                f"{rollout_counter:03d}__{_slugify(result['domain'])}__"
                f"{_slugify(result['scenario_id'])}.rollout.json"
            )
            dump_json(output_dir / file_name, result)
            per_run_files.append(file_name)

    summary = {
        "ok": True,
        "timestamp": _now_iso(),
        "base_url": base_url.rstrip("/"),
        "timeout": timeout,
        "payload_dir": str(payload_dir),
        "output_dir": str(output_dir),
        "repeats": repeats,
        "require_success": bool(require_success),
        "include_glob": _normalize_globs(include_glob),
        "exclude_glob": list(DEFAULT_EXCLUDE_GLOBS) + _normalize_globs(exclude_glob),
        "count": len(results),
        "passes": sum(1 for item in results if item.get("status") == "pass"),
        "warnings": sum(1 for item in results if item.get("status") == "warn"),
        "fails": sum(1 for item in results if item.get("status") == "fail"),
        "results": results,
        "files": per_run_files,
    }

    dump_json(output_dir / "rollout_summary.json", summary)
    with (output_dir / "rollout_results.jsonl").open("w", encoding="utf-8") as fh:
        for item in results:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run lightweight rollout episodes for omnibench_aegis_env.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Environment server base URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument(
        "--payload-dir",
        default=str(DEFAULT_PAYLOAD_DIR),
        help="Directory containing generated client payload bundles",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where rollout result artifacts will be written",
    )
    parser.add_argument("--only", nargs="*", help="Restrict rollouts to one or more domains or scenario IDs")
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
        help="Regenerate payload bundles before running the rollouts",
    )
    parser.add_argument("--repeats", type=int, default=1, help="How many times to run each selected rollout")
    parser.add_argument(
        "--require-success",
        action="store_true",
        help="Mark a rollout as warn if it completes but success=false",
    )
    parser.add_argument("--json", action="store_true", help="Print the final summary as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        summary = run_rollouts(
            base_url=args.base_url,
            timeout=args.timeout,
            payload_dir=Path(args.payload_dir).resolve(),
            output_dir=Path(args.output_dir).resolve(),
            only=args.only,
            include_glob=args.include_glob,
            exclude_glob=args.exclude_glob,
            include_non_smoke=args.include_non_smoke,
            rebuild_payloads=args.rebuild_payloads,
            repeats=args.repeats,
            require_success=args.require_success,
        )
    except RolloutError as exc:
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
        print("[ok] rollout run completed")
        print(f"- output_dir: {summary['output_dir']}")
        print(f"- count: {summary['count']}")
        print(f"- passes: {summary['passes']}")
        print(f"- warnings: {summary['warnings']}")
        print(f"- fails: {summary['fails']}")
        for name in summary["files"]:
            print(f"- {name}")
        print("- rollout_summary.json")
        print("- rollout_results.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
