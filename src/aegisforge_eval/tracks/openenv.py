
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..schemas import TrackResult

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


DESCRIPTION = (
    "Validates an OpenEnv environment against the current AegisForge "
    "BaseDomain-backed contract by running health -> contract -> reset -> "
    "step[*] -> state -> actions."
)


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def _as_float(value: Any, default: float = 10.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _require_keys(obj: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    return [key for key in keys if key not in obj]


def _result(
    *,
    status: str,
    summary: str,
    score: float,
    details: dict[str, Any],
) -> TrackResult:
    return TrackResult(
        track="openenv",
        status=status,
        summary=summary,
        score=score,
        details=details,
    )


def _normalize_reset_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_reset = payload.get("reset_payload")
    if isinstance(raw_reset, Mapping):
        return dict(raw_reset)

    seed = payload.get("seed")
    scenario_id = payload.get("scenario_id")
    mission_id = payload.get("mission_id")
    env_id = payload.get("env_id")
    domain = payload.get("domain")
    max_steps = payload.get("max_steps")
    target_score = payload.get("target_score")

    reset_payload: dict[str, Any] = {}
    if seed is not None:
        reset_payload["seed"] = seed
    if scenario_id is not None:
        reset_payload["scenario_id"] = str(scenario_id)
    if mission_id is not None:
        reset_payload["mission_id"] = str(mission_id)

    options: dict[str, Any] = {}
    if env_id is not None:
        options["env_id"] = str(env_id)
    if domain is not None:
        options["domain"] = str(domain)
    if max_steps is not None:
        options["max_steps"] = max_steps
    if target_score is not None:
        options["target_score"] = target_score

    if options:
        reset_payload["options"] = options

    return reset_payload


def _normalize_action_plan(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_plan = payload.get("action_plan")
    if not isinstance(raw_plan, Sequence) or isinstance(raw_plan, (str, bytes)):
        raw_plan = None

    if raw_plan:
        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_plan, start=1):
            if not isinstance(item, Mapping):
                raise ValueError(f"action_plan[{idx}] must be a JSON object")

            if "name" in item:
                normalized.append(
                    {
                        "name": str(item.get("name") or ""),
                        "args": dict(item.get("args") or {}),
                    }
                )
                continue

            if "action" in item:
                action_name = str(item.get("action") or "")
                if isinstance(item.get("args"), Mapping):
                    normalized.append(
                        {
                            "name": action_name,
                            "args": dict(item.get("args") or {}),
                        }
                    )
                else:
                    extras = {
                        str(k): v
                        for k, v in item.items()
                        if k not in {"action"}
                    }
                    if "args" in extras and not isinstance(extras["args"], Mapping):
                        extras.pop("args", None)
                    normalized.append(
                        {
                            "name": action_name,
                            "args": extras.get("args", {}) if isinstance(extras.get("args"), Mapping) else {
                                k: v for k, v in extras.items() if k != "args"
                            },
                        }
                    )
                continue

            raise ValueError(f"action_plan[{idx}] must include either 'name' or 'action'")

        if normalized:
            return normalized

    # Shallow compatibility fallback
    if isinstance(payload.get("args"), Mapping):
        return [{"name": str(payload.get("action") or payload.get("name") or ""), "args": dict(payload.get("args") or {})}]

    action_name = str(payload.get("action") or payload.get("name") or "")
    if action_name:
        extras = {
            str(k): v
            for k, v in payload.items()
            if k not in {
                "adapter",
                "environment_url",
                "base_url",
                "timeout",
                "live_check",
                "require_success",
                "seed",
                "episode_id",
                "env_name",
                "expected_env_name",
                "expected_env_id",
                "expected_scenario_id",
                "scenario_id",
                "mission_id",
                "env_id",
                "domain",
                "max_steps",
                "target_score",
                "reset_payload",
                "action_plan",
            }
        }
        if "value" in extras and "args" not in extras:
            extras = {"value": extras["value"]}
        return [{"name": action_name, "args": dict(extras)}]

    return []


def _extract_action_name(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    if "name" in payload:
        return str(payload.get("name") or "")
    if "action" in payload:
        return str(payload.get("action") or "")
    return ""


def evaluate(payload: Mapping[str, Any] | None = None) -> TrackResult:
    payload = dict(payload or {})

    adapter = str(payload.get("adapter", "openenv"))
    environment_url = str(payload.get("environment_url") or payload.get("base_url") or "").rstrip("/")
    timeout = _as_float(payload.get("timeout"), default=10.0)
    live_check = _as_bool(payload.get("live_check"), default=True)
    require_success = _as_bool(payload.get("require_success"), default=False)

    expected_env_name = payload.get("expected_env_name") or payload.get("env_name")
    expected_env_id = payload.get("expected_env_id") or payload.get("env_id")
    expected_scenario_id = payload.get("expected_scenario_id") or payload.get("scenario_id")

    details: dict[str, Any] = {
        "description": DESCRIPTION,
        "adapter": adapter,
        "environment_url": environment_url or None,
        "timeout": timeout,
        "live_check": live_check,
        "require_success": require_success,
        "expected_env_name": expected_env_name,
        "expected_env_id": expected_env_id,
        "expected_scenario_id": expected_scenario_id,
        "checks": {
            "health": False,
            "contract": False,
            "reset": False,
            "step_count": 0,
            "state": False,
            "actions": False,
            "success_path": False,
        },
    }

    if adapter != "openenv":
        return _result(
            status="skip",
            summary="payload targets a different adapter; OpenEnv track skipped",
            score=0.0,
            details=details,
        )

    if not environment_url:
        details["missing"] = ["environment_url"]
        return _result(
            status="warn",
            summary="OpenEnv payload is incomplete: missing environment_url/base_url",
            score=0.15,
            details=details,
        )

    reset_payload = _normalize_reset_payload(payload)
    details["reset_payload"] = reset_payload

    try:
        action_plan = _normalize_action_plan(payload)
    except Exception as exc:
        details["error"] = str(exc)
        return _result(
            status="warn",
            summary="OpenEnv payload has an invalid action_plan",
            score=0.15,
            details=details,
        )

    details["action_plan"] = action_plan

    if not live_check:
        return _result(
            status="pass",
            summary="OpenEnv payload is documented and live check was intentionally disabled",
            score=0.60,
            details=details,
        )

    if httpx is None:
        details["error"] = "httpx is not available in the current environment"
        return _result(
            status="warn",
            summary="OpenEnv live check could not run because httpx is unavailable",
            score=0.10,
            details=details,
        )

    try:
        with httpx.Client(timeout=timeout) as client:
            # 1) health
            health_response = client.get(f"{environment_url}/health")
            health_response.raise_for_status()
            health_json = health_response.json()

            if not isinstance(health_json, Mapping):
                raise ValueError("/health did not return a JSON object")

            missing_health = _require_keys(
                health_json,
                ("status", "env", "initialized"),
            )
            if missing_health:
                raise ValueError(f"/health missing keys: {', '.join(missing_health)}")

            if str(health_json.get("status")) != "ok":
                raise ValueError("/health returned status different from 'ok'")

            if expected_env_name and str(health_json.get("env")) != str(expected_env_name):
                raise ValueError(
                    f"/health env mismatch: expected '{expected_env_name}' "
                    f"got '{health_json.get('env')}'"
                )

            details["health"] = dict(health_json)
            details["checks"]["health"] = True

            # 2) contract
            contract_response = client.get(f"{environment_url}/contract")
            contract_response.raise_for_status()
            contract_json = contract_response.json()

            if not isinstance(contract_json, Mapping):
                raise ValueError("/contract did not return a JSON object")

            missing_contract = _require_keys(
                contract_json,
                ("env_id", "name", "version", "primary_scenarios", "supported_domains", "supported_env_ids"),
            )
            if missing_contract:
                raise ValueError(f"/contract missing keys: {', '.join(missing_contract)}")

            if expected_env_name and str(contract_json.get("name")) != str(expected_env_name):
                raise ValueError(
                    f"/contract name mismatch: expected '{expected_env_name}' "
                    f"got '{contract_json.get('name')}'"
                )

            if expected_env_id:
                supported_env_ids = list(contract_json.get("supported_env_ids") or [])
                if str(expected_env_id) not in [str(item) for item in supported_env_ids]:
                    raise ValueError(
                        f"/contract does not advertise expected env_id '{expected_env_id}'"
                    )

            if expected_scenario_id:
                primary_scenarios = [str(item) for item in contract_json.get("primary_scenarios") or []]
                if str(expected_scenario_id) not in primary_scenarios:
                    raise ValueError(
                        f"/contract does not advertise expected scenario_id '{expected_scenario_id}'"
                    )

            details["contract"] = dict(contract_json)
            details["checks"]["contract"] = True

            # 3) reset
            reset_response = client.post(f"{environment_url}/reset", json=reset_payload)
            reset_response.raise_for_status()
            reset_json = reset_response.json()

            if not isinstance(reset_json, Mapping):
                raise ValueError("/reset did not return a JSON object")

            missing_reset = _require_keys(
                reset_json,
                ("env_id", "scenario_id", "observation", "state", "info", "actions"),
            )
            if missing_reset:
                raise ValueError(f"/reset missing keys: {', '.join(missing_reset)}")

            reset_state = reset_json.get("state")
            reset_info = reset_json.get("info")
            reset_actions = reset_json.get("actions")

            if not isinstance(reset_state, Mapping):
                raise ValueError("/reset.state is not a JSON object")
            if not isinstance(reset_info, Mapping):
                raise ValueError("/reset.info is not a JSON object")
            if not isinstance(reset_actions, Sequence) or isinstance(reset_actions, (str, bytes)):
                raise ValueError("/reset.actions is not a JSON array")

            if expected_env_id and str(reset_json.get("env_id")) != str(expected_env_id):
                raise ValueError(
                    f"/reset env_id mismatch: expected '{expected_env_id}' "
                    f"got '{reset_json.get('env_id')}'"
                )
            if expected_scenario_id and str(reset_json.get("scenario_id")) != str(expected_scenario_id):
                raise ValueError(
                    f"/reset scenario_id mismatch: expected '{expected_scenario_id}' "
                    f"got '{reset_json.get('scenario_id')}'"
                )
            if expected_env_name and str(reset_info.get("env_name")) != str(expected_env_name):
                raise ValueError(
                    f"/reset info env_name mismatch: expected '{expected_env_name}' "
                    f"got '{reset_info.get('env_name')}'"
                )

            details["reset"] = dict(reset_json)
            details["checks"]["reset"] = True

            # If no action plan provided, use first advertised action.
            if not action_plan:
                advertised = [str(item) for item in reset_actions if isinstance(item, (str, int, float))]
                fallback_name = advertised[0] if advertised else "inspect_inventory"
                action_plan = [{"name": fallback_name, "args": {}}]
                details["action_plan"] = action_plan

            # 4) step plan
            step_results: list[dict[str, Any]] = []
            last_step_state: Mapping[str, Any] | None = None

            for idx, step_spec in enumerate(action_plan, start=1):
                if not isinstance(step_spec, Mapping):
                    raise ValueError(f"action_plan[{idx}] normalized into a non-mapping payload")

                step_response = client.post(
                    f"{environment_url}/step",
                    json={
                        "name": str(step_spec.get("name") or ""),
                        "args": dict(step_spec.get("args") or {}),
                    },
                )
                step_response.raise_for_status()
                step_json = step_response.json()

                if not isinstance(step_json, Mapping):
                    raise ValueError(f"/step #{idx} did not return a JSON object")

                missing_step = _require_keys(
                    step_json,
                    ("env_id", "scenario_id", "observation", "reward", "done", "truncated", "info", "state", "actions"),
                )
                if missing_step:
                    raise ValueError(f"/step #{idx} missing keys: {', '.join(missing_step)}")

                step_state = step_json.get("state")
                step_info = step_json.get("info")
                if not isinstance(step_state, Mapping):
                    raise ValueError(f"/step #{idx}.state is not a JSON object")
                if not isinstance(step_info, Mapping):
                    raise ValueError(f"/step #{idx}.info is not a JSON object")

                expected_action_name = str(step_spec.get("name") or "")
                actual_action_name = _extract_action_name(step_state.get("last_action"))  # type: ignore[arg-type]
                if actual_action_name and actual_action_name != expected_action_name:
                    raise ValueError(
                        f"/step #{idx} last_action mismatch: expected "
                        f"'{expected_action_name}' got '{actual_action_name}'"
                    )

                step_results.append(dict(step_json))
                last_step_state = step_state
                details["checks"]["step_count"] = idx

            details["steps"] = step_results

            # 5) state
            state_response = client.get(f"{environment_url}/state")
            state_response.raise_for_status()
            state_json = state_response.json()

            if not isinstance(state_json, Mapping):
                raise ValueError("/state did not return a JSON object")

            missing_state = _require_keys(
                state_json,
                ("env_id", "scenario_id", "state", "last_observation", "last_info"),
            )
            if missing_state:
                raise ValueError(f"/state missing keys: {', '.join(missing_state)}")

            envelope_state = state_json.get("state")
            if not isinstance(envelope_state, Mapping):
                raise ValueError("/state.state is not a JSON object")

            if expected_env_id and str(state_json.get("env_id")) != str(expected_env_id):
                raise ValueError(
                    f"/state env_id mismatch: expected '{expected_env_id}' "
                    f"got '{state_json.get('env_id')}'"
                )
            if expected_scenario_id and str(state_json.get("scenario_id")) != str(expected_scenario_id):
                raise ValueError(
                    f"/state scenario_id mismatch: expected '{expected_scenario_id}' "
                    f"got '{state_json.get('scenario_id')}'"
                )

            if last_step_state is not None:
                final_last_action = _extract_action_name(envelope_state.get("last_action"))  # type: ignore[arg-type]
                previous_last_action = _extract_action_name(last_step_state.get("last_action"))  # type: ignore[arg-type]
                if previous_last_action and final_last_action != previous_last_action:
                    raise ValueError("/state.state.last_action does not match the last step result")

            details["state"] = dict(state_json)
            details["checks"]["state"] = True

            # 6) actions
            actions_response = client.get(f"{environment_url}/actions")
            actions_response.raise_for_status()
            actions_json = actions_response.json()

            if not isinstance(actions_json, Mapping):
                raise ValueError("/actions did not return a JSON object")

            missing_actions = _require_keys(actions_json, ("env_id", "scenario_id", "actions"))
            if missing_actions:
                raise ValueError(f"/actions missing keys: {', '.join(missing_actions)}")

            if not isinstance(actions_json.get("actions"), Sequence) or isinstance(actions_json.get("actions"), (str, bytes)):
                raise ValueError("/actions.actions is not a JSON array")

            details["actions"] = dict(actions_json)
            details["checks"]["actions"] = True

            if require_success:
                done = bool(envelope_state.get("done"))
                success = bool(envelope_state.get("success"))
                progress = int(envelope_state.get("progress") or 0)
                target_progress = int(
                    envelope_state.get("target_score")
                    or envelope_state.get("target_progress")
                    or 0
                )

                if not done:
                    raise ValueError("require_success=True but final state.done is false")
                if not success:
                    raise ValueError("require_success=True but final state.success is false")
                if target_progress > 0 and progress < target_progress:
                    raise ValueError(
                        f"require_success=True but final progress {progress} "
                        f"is below target {target_progress}"
                    )

                details["checks"]["success_path"] = True

            return _result(
                status="pass",
                summary="OpenEnv live check passed: health/contract/reset/step/state/actions are operational",
                score=1.0,
                details=details,
            )

    except Exception as exc:
        completed_stages = 0
        for key in ("health", "contract", "reset", "state", "actions"):
            if details["checks"][key]:
                completed_stages += 1
        if details["checks"]["step_count"] > 0:
            completed_stages += 1
        if details["checks"]["success_path"]:
            completed_stages += 1

        denominator = 7 if require_success else 6
        score = round(completed_stages / denominator, 2)

        details["error"] = str(exc)

        return _result(
            status="warn",
            summary=(
                f"OpenEnv live check failed after {completed_stages}/{denominator} "
                f"completed stages"
            ),
            score=score,
            details=details,
        )
