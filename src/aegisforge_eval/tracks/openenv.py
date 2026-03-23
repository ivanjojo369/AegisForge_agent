from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..schemas import TrackResult

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


DESCRIPTION = (
    "Validates an OpenEnv environment by running a live "
    "health -> reset -> step[*] -> state check."
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


def _as_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
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


def _normalize_action_plan(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_plan = payload.get("action_plan")

    if isinstance(raw_plan, Sequence) and not isinstance(raw_plan, (str, bytes)):
        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_plan, start=1):
            if not isinstance(item, Mapping):
                raise ValueError(f"action_plan[{idx}] must be a JSON object")

            action = str(item.get("action") or "advance")
            value = _as_int(item.get("value"), default=1)

            normalized.append(
                {
                    "action": action,
                    "value": value,
                }
            )

        if normalized:
            return normalized

    return [
        {
            "action": str(payload.get("action") or "advance"),
            "value": _as_int(payload.get("value"), default=1),
        }
    ]


def evaluate(payload: Mapping[str, Any] | None = None) -> TrackResult:
    payload = dict(payload or {})

    adapter = str(payload.get("adapter", "openenv"))
    environment_url = str(payload.get("environment_url") or payload.get("base_url") or "").rstrip("/")
    timeout = _as_float(payload.get("timeout"), default=10.0)
    live_check = _as_bool(payload.get("live_check"), default=True)
    require_success = _as_bool(payload.get("require_success"), default=False)
    seed = payload.get("seed")
    episode_id = payload.get("episode_id")
    expected_env_name = payload.get("env_name")

    details: dict[str, Any] = {
        "description": DESCRIPTION,
        "adapter": adapter,
        "environment_url": environment_url or None,
        "episode_id": episode_id,
        "timeout": timeout,
        "live_check": live_check,
        "require_success": require_success,
        "expected_env_name": expected_env_name,
        "checks": {
            "health": False,
            "reset": False,
            "step_count": 0,
            "state": False,
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

            # 2) reset
            reset_payload: dict[str, Any] = {}
            if seed is not None:
                reset_payload["seed"] = seed

            reset_response = client.post(
                f"{environment_url}/reset",
                json=reset_payload,
            )
            reset_response.raise_for_status()
            reset_json = reset_response.json()

            if not isinstance(reset_json, Mapping):
                raise ValueError("/reset did not return a JSON object")

            missing_reset = _require_keys(
                reset_json,
                ("observation", "state", "info"),
            )
            if missing_reset:
                raise ValueError(f"/reset missing keys: {', '.join(missing_reset)}")

            reset_state = reset_json.get("state")
            reset_info = reset_json.get("info")

            if not isinstance(reset_state, Mapping):
                raise ValueError("/reset.state is not a JSON object")
            if not isinstance(reset_info, Mapping):
                raise ValueError("/reset.info is not a JSON object")

            if expected_env_name and str(reset_info.get("env_name")) != str(expected_env_name):
                raise ValueError(
                    f"/reset info env_name mismatch: expected '{expected_env_name}' "
                    f"got '{reset_info.get('env_name')}'"
                )

            details["reset"] = dict(reset_json)
            details["checks"]["reset"] = True

            # 3) step plan
            step_results: list[dict[str, Any]] = []
            last_step_state: Mapping[str, Any] | None = None

            for idx, step_spec in enumerate(action_plan, start=1):
                step_response = client.post(
                    f"{environment_url}/step",
                    json={
                        "action": step_spec["action"],
                        "value": step_spec["value"],
                    },
                )
                step_response.raise_for_status()
                step_json = step_response.json()

                if not isinstance(step_json, Mapping):
                    raise ValueError(f"/step #{idx} did not return a JSON object")

                missing_step = _require_keys(
                    step_json,
                    ("observation", "reward", "done", "truncated", "info", "state"),
                )
                if missing_step:
                    raise ValueError(f"/step #{idx} missing keys: {', '.join(missing_step)}")

                step_state = step_json.get("state")
                if not isinstance(step_state, Mapping):
                    raise ValueError(f"/step #{idx}.state is not a JSON object")

                if step_state.get("last_action") != step_spec["action"]:
                    raise ValueError(
                        f"/step #{idx} last_action mismatch: expected "
                        f"'{step_spec['action']}' got '{step_state.get('last_action')}'"
                    )

                step_results.append(dict(step_json))
                last_step_state = step_state
                details["checks"]["step_count"] = idx

            details["steps"] = step_results

            # 4) state
            state_response = client.get(f"{environment_url}/state")
            state_response.raise_for_status()
            state_json = state_response.json()

            if not isinstance(state_json, Mapping):
                raise ValueError("/state did not return a JSON object")

            missing_state = _require_keys(
                state_json,
                (
                    "episode_id",
                    "score",
                    "step_count",
                    "max_steps",
                    "target_score",
                    "done",
                    "success",
                    "last_action",
                    "history",
                ),
            )
            if missing_state:
                raise ValueError(f"/state missing keys: {', '.join(missing_state)}")

            if episode_id and state_json.get("episode_id") != episode_id:
                raise ValueError(
                    f"/state episode_id mismatch: expected '{episode_id}' "
                    f"got '{state_json.get('episode_id')}'"
                )

            if last_step_state is not None:
                if state_json.get("last_action") != last_step_state.get("last_action"):
                    raise ValueError("/state last_action does not match the last step result")

            details["state"] = dict(state_json)
            details["checks"]["state"] = True

            if require_success:
                done = bool(state_json.get("done"))
                success = bool(state_json.get("success"))
                score = _as_int(state_json.get("score"), default=0)
                target_score = _as_int(state_json.get("target_score"), default=0)

                if not done:
                    raise ValueError("require_success=True but final state.done is false")
                if not success:
                    raise ValueError("require_success=True but final state.success is false")
                if target_score > 0 and score < target_score:
                    raise ValueError(
                        f"require_success=True but final score {score} "
                        f"is below target_score {target_score}"
                    )

                details["checks"]["success_path"] = True

            return _result(
                status="pass",
                summary="OpenEnv live check passed: health/reset/step/state are operational",
                score=1.0,
                details=details,
            )

    except Exception as exc:
        completed_stages = 0
        if details["checks"]["health"]:
            completed_stages += 1
        if details["checks"]["reset"]:
            completed_stages += 1
        if details["checks"]["step_count"] > 0:
            completed_stages += 1
        if details["checks"]["state"]:
            completed_stages += 1
        if details["checks"]["success_path"]:
            completed_stages += 1

        denominator = 5 if require_success else 4
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
    