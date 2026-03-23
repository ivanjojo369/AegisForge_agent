from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from ...utils.validation import require_int_in_range, require_non_empty_string


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class SecurityAdapterConfig:
    enabled: bool
    role: str
    scenario_name: str
    timeout_seconds: int
    strict_mode: bool

    @classmethod
    def from_env(cls) -> "SecurityAdapterConfig":
        enabled = _read_bool_env("AEGISFORGE_ENABLE_SECURITY", False)
        role = require_non_empty_string(os.environ.get("SECURITY_ROLE", "defender"), "SECURITY_ROLE").lower()
        scenario_name = require_non_empty_string(
            os.environ.get("SECURITY_SCENARIO_NAME", "security-default"),
            "SECURITY_SCENARIO_NAME",
        )
        timeout_seconds = require_int_in_range(
            int(os.environ.get("SECURITY_TIMEOUT_SECONDS", "30")),
            "SECURITY_TIMEOUT_SECONDS",
            minimum=1,
            maximum=300,
        )
        strict_mode = _read_bool_env("SECURITY_STRICT_MODE", False)
        return cls(
            enabled=enabled,
            role=role,
            scenario_name=scenario_name,
            timeout_seconds=timeout_seconds,
            strict_mode=strict_mode,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
    