from __future__ import annotations

"""Lightweight HTTP client for omnibench_aegis_env.

This version is aligned to the current BaseDomain-backed server contract used by
AegisForge_agent:
- GET  /health
- GET  /contract
- POST /reset
- POST /step
- GET  /state
- GET  /actions

It intentionally avoids any dependency on the old BaseOpenEnv / ResetResult /
StepResult stack.
"""

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, MutableMapping, Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from .models import (
    ActionListResponse,
    ContractPayload,
    HealthPayload,
    JsonDict,
    ResetRequest,
    ResetResponse,
    StateEnvelope,
    StepRequest,
    StepResponse,
)


DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_USER_AGENT = "omnibench-aegis-env-client/0.2.0"


class OpenEnvClientError(RuntimeError):
    """Raised when the environment server returns an invalid or failed response."""


@dataclass(slots=True)
class HttpResult:
    status_code: int
    payload: JsonDict
    headers: JsonDict


def _normalize_base_url(base_url: str) -> str:
    text = str(base_url or "").strip()
    if not text:
        raise ValueError("base_url must be a non-empty string")
    return text.rstrip("/")


def _join_url(base_url: str, path: str) -> str:
    clean_base = _normalize_base_url(base_url)
    clean_path = path if path.startswith("/") else f"/{path}"
    return urllib_parse.urljoin(clean_base + "/", clean_path.lstrip("/"))


def _json_bytes(payload: Optional[Mapping[str, Any]]) -> bytes:
    return json.dumps(dict(payload or {}), ensure_ascii=False).encode("utf-8")


def _read_json_response(response: Any) -> JsonDict:
    raw = response.read()
    if not raw:
        return {}
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise OpenEnvClientError(f"response body is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise OpenEnvClientError("response body must be a JSON object")
    return decoded


def _headers_to_dict(headers: Any) -> JsonDict:
    try:
        return {str(key): str(value) for key, value in headers.items()}
    except Exception:
        return {}


class OpenEnvClient:
    """Small synchronous client for the local OpenEnv server."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.timeout = float(timeout)
        self.user_agent = str(user_agent)

    def endpoint(self, path: str) -> str:
        return _join_url(self.base_url, path)

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> HttpResult:
        url = self.endpoint(path)
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }

        normalized_method = method.upper().strip()
        if normalized_method in {"POST", "PUT", "PATCH"}:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = _json_bytes(payload)

        req = urllib_request.Request(url=url, data=data, headers=headers, method=normalized_method)

        try:
            with urllib_request.urlopen(req, timeout=self.timeout) as response:
                return HttpResult(
                    status_code=int(getattr(response, "status", 200)),
                    payload=_read_json_response(response),
                    headers=_headers_to_dict(getattr(response, "headers", {})),
                )
        except urllib_error.HTTPError as exc:
            try:
                error_payload = _read_json_response(exc)
            except Exception:
                raw = exc.read().decode("utf-8", errors="replace")
                error_payload = {"detail": raw or exc.reason}
            raise OpenEnvClientError(
                f"{normalized_method} {path} failed with HTTP {exc.code}: {error_payload}"
            ) from exc
        except urllib_error.URLError as exc:
            raise OpenEnvClientError(f"could not reach environment server at {url}: {exc.reason}") from exc

    def health(self) -> JsonDict:
        return self.request("GET", "/health").payload

    def health_model(self) -> HealthPayload:
        return HealthPayload.from_mapping(self.health())

    def contract(self) -> JsonDict:
        return self.request("GET", "/contract").payload

    def contract_model(self) -> ContractPayload:
        return ContractPayload.from_mapping(self.contract())

    def reset(
        self,
        request: ResetRequest | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> JsonDict:
        payload = self._normalize_reset_payload(request=request, **kwargs)
        return self.request("POST", "/reset", payload=payload).payload

    def reset_model(
        self,
        request: ResetRequest | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> ResetResponse:
        return ResetResponse.from_mapping(self.reset(request=request, **kwargs))

    def step(
        self,
        request: StepRequest | Mapping[str, Any] | None = None,
        *,
        action: Optional[str] = None,
        value: Any = None,
        use_shorthand: bool = False,
        **kwargs: Any,
    ) -> JsonDict:
        payload = self._normalize_step_payload(
            request=request,
            action=action,
            value=value,
            use_shorthand=use_shorthand,
            **kwargs,
        )
        return self.request("POST", "/step", payload=payload).payload

    def step_model(
        self,
        request: StepRequest | Mapping[str, Any] | None = None,
        *,
        action: Optional[str] = None,
        value: Any = None,
        use_shorthand: bool = False,
        **kwargs: Any,
    ) -> StepResponse:
        return StepResponse.from_mapping(
            self.step(
                request=request,
                action=action,
                value=value,
                use_shorthand=use_shorthand,
                **kwargs,
            )
        )

    def state(self) -> JsonDict:
        return self.request("GET", "/state").payload

    def state_model(self) -> StateEnvelope:
        return StateEnvelope.from_mapping(self.state())

    def actions(self) -> JsonDict:
        return self.request("GET", "/actions").payload

    def actions_model(self) -> ActionListResponse:
        return ActionListResponse.from_mapping(self.actions())

    def run_action_plan(
        self,
        action_plan: Iterable[Mapping[str, Any] | StepRequest],
        *,
        use_shorthand: bool = False,
    ) -> list[JsonDict]:
        results: list[JsonDict] = []
        for item in action_plan:
            results.append(self.step(request=item, use_shorthand=use_shorthand))
        return results

    def validate_min_contract(self) -> JsonDict:
        """Run a minimal local contract check against the live environment."""
        health = self.health()
        contract = self.contract()
        reset = self.reset()

        advertised_actions = []
        if isinstance(reset.get("actions"), list):
            advertised_actions = [str(item) for item in reset.get("actions") or []]
        elif isinstance(reset.get("observation"), Mapping):
            advertised_actions = [
                str(item)
                for item in (reset.get("observation") or {}).get("available_actions") or []
            ]

        first_action = advertised_actions[0] if advertised_actions else "inspect_inventory"
        step = self.step(request={"name": first_action, "args": {}}, use_shorthand=False)
        state = self.state()
        actions = self.actions()

        checks = {
            "health": all(key in health for key in ("status", "env", "initialized")),
            "contract": all(key in contract for key in ("env_id", "name", "version")),
            "reset": all(key in reset for key in ("observation", "state", "info")),
            "step": all(key in step for key in ("observation", "reward", "done", "truncated", "info", "state")),
            "state": all(key in state for key in ("env_id", "scenario_id", "state", "last_observation", "last_info")),
            "actions": "actions" in actions,
        }
        checks["all_pass"] = all(checks.values())
        return {
            "checks": checks,
            "health": health,
            "contract": contract,
            "reset": reset,
            "step": step,
            "state": state,
            "actions": actions,
        }

    def _normalize_reset_payload(
        self,
        *,
        request: ResetRequest | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> JsonDict:
        if isinstance(request, ResetRequest):
            payload = request.to_dict()
        elif isinstance(request, Mapping):
            payload = ResetRequest.from_mapping(request).to_dict()
        else:
            payload = ResetRequest.from_mapping(kwargs).to_dict()

        if kwargs:
            options = dict(payload.get("options") or {})
            for key, value in kwargs.items():
                if key in {"seed", "scenario_id", "mission_id", "options"}:
                    payload[key] = value
                else:
                    options[key] = value
            payload["options"] = options
        return payload

    def _normalize_step_payload(
        self,
        *,
        request: StepRequest | Mapping[str, Any] | None = None,
        action: Optional[str] = None,
        value: Any = None,
        use_shorthand: bool = False,
        **kwargs: Any,
    ) -> JsonDict:
        step_request: StepRequest

        if isinstance(request, StepRequest):
            step_request = request
        elif isinstance(request, Mapping):
            step_request = StepRequest.from_mapping(request)
        elif isinstance(action, str) and action.strip():
            raw: MutableMapping[str, Any] = {"action": action}
            if value is not None:
                raw["value"] = value
            raw.update(kwargs)
            step_request = StepRequest.from_mapping(raw)
        else:
            raise ValueError("step requires either a StepRequest, a mapping payload, or the `action` argument")

        if use_shorthand:
            payload = step_request.to_shorthand_dict()
        else:
            payload = step_request.to_action_dict()

        if kwargs and request is not None:
            if use_shorthand:
                payload.update(kwargs)
            else:
                payload.setdefault("args", {}).update(kwargs)
        return payload


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_USER_AGENT",
    "HttpResult",
    "OpenEnvClient",
    "OpenEnvClientError",
]
