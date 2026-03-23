from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import SecurityAdapterConfig


@dataclass(slots=True)
class SecurityAdapterResult:
    ok: bool
    provider: str
    role: str
    scenario_name: str
    payload: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "ok": self.ok,
            "provider": self.provider,
            "role": self.role,
            "scenario_name": self.scenario_name,
            "payload": self.payload,
        }
        if self.error:
            data["error"] = self.error
        return data


class SecurityAdapter:
    provider_name = "security_arena"

    def __init__(self, config: SecurityAdapterConfig | None = None) -> None:
        self.config = config or SecurityAdapterConfig.from_env()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "enabled": self.config.enabled,
            "role": self.config.role,
            "scenario_name": self.config.scenario_name,
            "timeout_seconds": self.config.timeout_seconds,
            "strict_mode": self.config.strict_mode,
        }

    def validate_request(self, request_data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request_data, dict):
            raise ValueError("Security adapter request must be a dict.")
        normalized = dict(request_data)
        if not normalized.get("role"):
            normalized["role"] = self.config.role
        if not normalized.get("scenario_name"):
            normalized["scenario_name"] = self.config.scenario_name
        if "provider" not in normalized:
            normalized["provider"] = self.provider_name
        return normalized

    def translate_request(self, request_data: dict[str, Any]) -> dict[str, Any]:
        normalized = self.validate_request(request_data)
        return {
            "provider": self.provider_name,
            "role": normalized["role"],
            "scenario_name": normalized["scenario_name"],
            "timeout_seconds": self.config.timeout_seconds,
            "input": normalized,
        }

    def translate_response(self, response_data: dict[str, Any]) -> SecurityAdapterResult:
        if not isinstance(response_data, dict):
            raise ValueError("Security adapter response must be a dict.")
        ok = bool(response_data.get("ok", True))
        error = response_data.get("error")
        if error is not None and not isinstance(error, str):
            error = str(error)
        return SecurityAdapterResult(
            ok=ok,
            provider=self.provider_name,
            role=self.config.role,
            scenario_name=self.config.scenario_name,
            payload=dict(response_data),
            error=error,
        )

    def execute(self, request_data: dict[str, Any]) -> SecurityAdapterResult:
        translated = self.translate_request(request_data)
        mock_response = {
            "ok": True,
            "provider": self.provider_name,
            "role": translated["role"],
            "scenario_name": translated["scenario_name"],
            "echo": translated["input"],
        }
        return self.translate_response(mock_response)
    