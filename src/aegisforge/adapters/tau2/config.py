from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from ...utils.validation import (
    require_int_in_range,
    require_non_empty_string,
    validate_http_url,
)


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Tau2AdapterConfig:
    enabled: bool
    base_url: str
    domain_name: str
    timeout_seconds: int
    strict_mode: bool

    @classmethod
    def from_env(cls) -> "Tau2AdapterConfig":
        enabled = _read_bool_env("AEGISFORGE_ENABLE_TAU2", False)

        base_url = validate_http_url(
            os.environ.get("TAU2_BASE_URL", "http://127.0.0.1:8020"),
            "TAU2_BASE_URL",
        )

        domain_name = require_non_empty_string(
            os.environ.get("TAU2_DOMAIN_NAME", "quipu_lab"),
            "TAU2_DOMAIN_NAME",
        )

        timeout_seconds = require_int_in_range(
            int(os.environ.get("TAU2_TIMEOUT_SECONDS", "30")),
            "TAU2_TIMEOUT_SECONDS",
            minimum=1,
            maximum=300,
        )

        strict_mode = _read_bool_env("TAU2_STRICT_MODE", False)

        return cls(
            enabled=enabled,
            base_url=base_url,
            domain_name=domain_name,
            timeout_seconds=timeout_seconds,
            strict_mode=strict_mode,
        )

    @property
    def provider_name(self) -> str:
        return "tau2"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["provider_name"] = self.provider_name
        return data
    