from __future__ import annotations

"""Configuration for the AegisForge OpenEnv adapter.

The adapter now targets the integrated OmniBench Aegis environment rather than
older demo/local ports.  This module keeps the original small config surface
(`base_url`, `timeout`, `env_name`) while adding convenience helpers used by
smoke tests, generated payloads, and local adapter wiring.
"""

import os
import re
from typing import Any, Mapping

from pydantic import BaseModel, Field


DEFAULT_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_TIMEOUT = 10.0
DEFAULT_ENV_NAME = "omnibench_aegis_env"
DEFAULT_ENV_ID = f"{DEFAULT_ENV_NAME}:demo"
DEFAULT_DOMAIN = "general"
DEFAULT_SCENARIO_ID = "SmokeLocal"
DEFAULT_SEED = 42
DEFAULT_MAX_STEPS = 5
DEFAULT_TARGET_SCORE = 1


def _env_first(*names: str, default: str | None = None) -> str | None:
    """Return the first non-empty environment variable from ``names``."""

    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _slugify(value: Any) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "default"


class OpenEnvAdapterConfig(BaseModel):
    """Runtime configuration for ``OpenEnvAdapter``.

    Environment variable precedence in ``from_env``:
    - ``AEGISFORGE_OPENENV_BASE_URL`` then ``OPENENV_BASE_URL``
    - ``AEGISFORGE_OPENENV_TIMEOUT`` then ``OPENENV_TIMEOUT``
    - ``AEGISFORGE_OPENENV_ENV_NAME`` then ``OPENENV_ENV_NAME``
    - ``AEGISFORGE_OPENENV_ENV_ID`` then ``OPENENV_ENV_ID``
    """

    base_url: str = Field(default=DEFAULT_BASE_URL)
    timeout: float = Field(default=DEFAULT_TIMEOUT, gt=0)
    env_name: str = Field(default=DEFAULT_ENV_NAME)
    default_env_id: str = Field(default=DEFAULT_ENV_ID)
    default_domain: str = Field(default=DEFAULT_DOMAIN)
    default_scenario_id: str = Field(default=DEFAULT_SCENARIO_ID)
    default_seed: int = Field(default=DEFAULT_SEED)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1)
    target_score: int | float = Field(default=DEFAULT_TARGET_SCORE)
    strict_contract: bool = Field(default=False)
    validate_registry_imports: bool = Field(default=False)

    class Config:
        extra = "allow"
        validate_assignment = True

    @classmethod
    def from_env(cls) -> "OpenEnvAdapterConfig":
        """Build config from environment variables with safe Sprint 4 defaults."""

        base_url = _env_first(
            "AEGISFORGE_OPENENV_BASE_URL",
            "OPENENV_BASE_URL",
            default=DEFAULT_BASE_URL,
        )
        timeout = _coerce_float(
            _env_first("AEGISFORGE_OPENENV_TIMEOUT", "OPENENV_TIMEOUT"),
            DEFAULT_TIMEOUT,
        )
        env_name = _env_first(
            "AEGISFORGE_OPENENV_ENV_NAME",
            "OPENENV_ENV_NAME",
            default=DEFAULT_ENV_NAME,
        ) or DEFAULT_ENV_NAME
        default_env_id = _env_first(
            "AEGISFORGE_OPENENV_ENV_ID",
            "OPENENV_ENV_ID",
            default=f"{env_name}:demo",
        ) or f"{env_name}:demo"
        default_domain = _env_first(
            "AEGISFORGE_OPENENV_DOMAIN",
            "OPENENV_DOMAIN",
            default=DEFAULT_DOMAIN,
        ) or DEFAULT_DOMAIN
        default_scenario_id = _env_first(
            "AEGISFORGE_OPENENV_SCENARIO_ID",
            "OPENENV_SCENARIO_ID",
            default=DEFAULT_SCENARIO_ID,
        ) or DEFAULT_SCENARIO_ID

        return cls(
            base_url=base_url or DEFAULT_BASE_URL,
            timeout=timeout,
            env_name=env_name,
            default_env_id=default_env_id,
            default_domain=default_domain,
            default_scenario_id=default_scenario_id,
            default_seed=_coerce_int(
                _env_first("AEGISFORGE_OPENENV_SEED", "OPENENV_SEED"),
                DEFAULT_SEED,
            ),
            max_steps=max(
                1,
                _coerce_int(
                    _env_first("AEGISFORGE_OPENENV_MAX_STEPS", "OPENENV_MAX_STEPS"),
                    DEFAULT_MAX_STEPS,
                ),
            ),
            target_score=_coerce_float(
                _env_first("AEGISFORGE_OPENENV_TARGET_SCORE", "OPENENV_TARGET_SCORE"),
                float(DEFAULT_TARGET_SCORE),
            ),
            strict_contract=_coerce_bool(
                _env_first("AEGISFORGE_OPENENV_STRICT_CONTRACT", "OPENENV_STRICT_CONTRACT"),
                False,
            ),
            validate_registry_imports=_coerce_bool(
                _env_first("AEGISFORGE_OPENENV_VALIDATE_REGISTRY_IMPORTS", "OPENENV_VALIDATE_REGISTRY_IMPORTS"),
                False,
            ),
        )

    @property
    def normalized_base_url(self) -> str:
        """Return ``base_url`` without a trailing slash."""

        return str(self.base_url).rstrip("/")

    @property
    def client_kwargs(self) -> dict[str, Any]:
        """Keyword arguments accepted by ``OpenEnvClient``."""

        return {
            "base_url": self.normalized_base_url,
            "timeout": float(self.timeout),
        }

    def env_id_for(self, domain: str | None = None, scenario_id: str | None = None) -> str:
        """Return a stable env_id for a domain/scenario pair.

        If no concrete domain/scenario is supplied, the configured demo env is
        returned.  For real Sprint 4 payloads the shape is:
        ``omnibench_aegis_env:<domain>.<scenario>``.
        """

        domain_name = str(domain or self.default_domain or "").strip()
        scenario_name = str(scenario_id or self.default_scenario_id or "").strip()
        if not domain_name or domain_name == "general" or not scenario_name:
            return str(self.default_env_id or f"{self.env_name}:demo")
        return f"{self.env_name}:{_slugify(domain_name)}.{_slugify(scenario_name)}"

    def build_reset_payload(
        self,
        *,
        seed: int | None = None,
        scenario_id: str | None = None,
        mission_id: str | None = None,
        domain: str | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a reset payload compatible with omnibench_aegis_env."""

        chosen_seed = self.default_seed if seed is None else int(seed)
        chosen_domain = str(domain or self.default_domain or DEFAULT_DOMAIN)
        chosen_scenario = str(scenario_id or self.default_scenario_id or DEFAULT_SCENARIO_ID)
        chosen_mission = str(
            mission_id
            or f"{_slugify(chosen_scenario)}_{_slugify(chosen_domain)}_adapter"
        )

        merged_options = dict(options or {})
        merged_options.setdefault("env_id", self.env_id_for(chosen_domain, chosen_scenario))
        merged_options.setdefault("max_steps", int(self.max_steps))
        merged_options.setdefault("target_score", self.target_score)
        merged_options["domain"] = chosen_domain

        return {
            "seed": chosen_seed,
            "scenario_id": chosen_scenario,
            "mission_id": chosen_mission,
            "options": merged_options,
        }

    def to_metadata(self) -> dict[str, Any]:
        """Return a serializable config summary for diagnostics."""

        return {
            "base_url": self.normalized_base_url,
            "timeout": float(self.timeout),
            "env_name": self.env_name,
            "default_env_id": self.default_env_id,
            "default_domain": self.default_domain,
            "default_scenario_id": self.default_scenario_id,
            "default_seed": int(self.default_seed),
            "max_steps": int(self.max_steps),
            "target_score": self.target_score,
            "strict_contract": bool(self.strict_contract),
            "validate_registry_imports": bool(self.validate_registry_imports),
        }
