"""Configuration for AegisForge Nexus (offline and side-effect free).

Repository path: src/aegisforge/nexus/config.py

Reads variables on explicit ``NexusConfig.from_env()`` calls. Importing this
module and constructing a config never contacts Nebius, runs tools, or writes
files. ``build_client()`` constructs a client but does not send a request.

Environment variables:
    NEBIUS_API_KEY                  required for real Nebius requests
    NEBIUS_BASE_URL                 optional; official Token Factory API only
    NEBIUS_MODEL                    optional; default from nebius_client.py
    NEBIUS_TIMEOUT_SECONDS          optional; 1..120 (default 60)
    NEXUS_PLANNER_MAX_STEPS         optional; 2..12 (default 6)
    NEXUS_PLANNER_MAX_TOKENS        optional; 128..8192 (default 2500)

The key is deliberately omitted from repr() and public_summary(). Avoid
logging config.__dict__, dataclasses.asdict(), or dumping the environment.
The model is a proposal engine only: config does NOT authorize tool execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
import re
from typing import TYPE_CHECKING, Any, Mapping

from .nebius_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    NebiusClient,
    NebiusConfigurationError,
)

if TYPE_CHECKING:
    import requests
    from .executor import NexusExecutor
    from .orchestrator import NexusOrchestrator
    from .planner import ChatClient, NexusPlanner
    from .verifier import NexusVerifier


_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")


class NexusConfigError(ValueError):
    """Configuration rejected before any model or tool operation."""


def _read_env(environ: Mapping[str, str], key: str, fallback: str | None) -> str | None:
    value = environ.get(key, fallback)
    if value is None:
        return None
    if not isinstance(value, str):
        raise NexusConfigError(f"{key} must be a string.")
    return value.strip()


def _read_int(environ: Mapping[str, str], key: str, default: int) -> int:
    raw = _read_env(environ, key, str(default))
    if raw is None or not re.fullmatch(r"[0-9]+", raw):
        raise NexusConfigError(f"{key} must be a positive integer.")
    # Cap input length before int() to avoid huge untrusted environment values.
    if len(raw) > 8:
        raise NexusConfigError(f"{key} is outside the supported range.")
    return int(raw)


def _read_float(environ: Mapping[str, str], key: str, default: float) -> float:
    raw = _read_env(environ, key, str(default))
    try:
        result = float(raw) if raw is not None else float("nan")
    except ValueError as exc:
        raise NexusConfigError(f"{key} must be a finite number.") from exc
    if not math.isfinite(result):
        raise NexusConfigError(f"{key} must be a finite number.")
    return result


@dataclass(frozen=True, slots=True)
class NexusConfig:
    """Validated local settings for Nebius and the Nexus planning layer.

    This object never grants permission to run tools. The host must still
    review plans and pass explicit authorization to NexusOrchestrator.run().
    """

    api_key: str | None = field(default=None, repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 60.0
    planner_max_steps: int = 6
    planner_max_tokens: int = 2500

    def __post_init__(self) -> None:
        if self.api_key is not None:
            if not isinstance(self.api_key, str):
                raise NexusConfigError("NEBIUS_API_KEY must be a string.")
            key = self.api_key.strip()
            if any(ord(character) < 32 or ord(character) == 127 for character in key):
                raise NexusConfigError("NEBIUS_API_KEY has invalid characters.")
            if len(key) > 4096:
                raise NexusConfigError("NEBIUS_API_KEY is too long.")
            object.__setattr__(self, "api_key", key or None)

        # Credentials must NEVER be forwarded to an arbitrary URL supplied by
        # task data or a modified environment. For custom gateways, introduce
        # a separately reviewed explicit trust policy rather than bypassing
        # this host restriction.
        if not isinstance(self.base_url, str) or self.base_url.rstrip("/") != DEFAULT_BASE_URL:
            raise NexusConfigError("NEBIUS_BASE_URL must be the official Nebius Token Factory /v1 endpoint.")
        object.__setattr__(self, "base_url", DEFAULT_BASE_URL)

        if not isinstance(self.model, str) or not _MODEL_ID.fullmatch(self.model):
            raise NexusConfigError("NEBIUS_MODEL must be a supported nonempty model ID.")

        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 1 <= self.timeout_seconds <= 120
        ):
            raise NexusConfigError("NEBIUS_TIMEOUT_SECONDS must be between 1 and 120.")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        if (
            type(self.planner_max_steps) is not int
            or not 2 <= self.planner_max_steps <= 12
        ):
            raise NexusConfigError("NEXUS_PLANNER_MAX_STEPS must be between 2 and 12.")
        if (
            type(self.planner_max_tokens) is not int
            or not 128 <= self.planner_max_tokens <= 8192
        ):
            raise NexusConfigError("NEXUS_PLANNER_MAX_TOKENS must be between 128 and 8192.")

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        require_api_key: bool = False,
    ) -> NexusConfig:
        """Validate a mapping or the process environment, without side effects.

        ``require_api_key=False`` supports local tests with no Nebius account.
        Real client creation always requires a key.
        """
        if type(require_api_key) is not bool:
            raise NexusConfigError("require_api_key must be a boolean.")
        source = os.environ if environ is None else environ
        if not isinstance(source, Mapping):
            raise NexusConfigError("environ must be a mapping.")
        settings = cls(
            api_key=_read_env(source, "NEBIUS_API_KEY", None),
            base_url=_read_env(source, "NEBIUS_BASE_URL", DEFAULT_BASE_URL) or "",
            model=_read_env(source, "NEBIUS_MODEL", DEFAULT_MODEL) or "",
            timeout_seconds=_read_float(source, "NEBIUS_TIMEOUT_SECONDS", 60.0),
            planner_max_steps=_read_int(source, "NEXUS_PLANNER_MAX_STEPS", 6),
            planner_max_tokens=_read_int(source, "NEXUS_PLANNER_MAX_TOKENS", 2500),
        )
        if require_api_key and not settings.api_key:
            raise NexusConfigError("NEBIUS_API_KEY is required for live inference.")
        return settings

    @property
    def has_api_key(self) -> bool:
        """True when a nonempty API key has been supplied; never returns it."""
        return bool(self.api_key)

    def public_summary(self) -> dict[str, Any]:
        """Safe operational metadata; excludes secrets and task contents."""
        return {
            "provider": "Nebius Token Factory",
            "base_url": self.base_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "planner_max_steps": self.planner_max_steps,
            "planner_max_tokens": self.planner_max_tokens,
            "api_key_configured": self.has_api_key,
            "auto_execute_tools": False,
        }

    def build_client(self, *, session: requests.Session | None = None) -> NebiusClient:
        """Construct a client; NO HTTP call is performed by this method."""
        if not self.api_key:
            raise NexusConfigError("NEBIUS_API_KEY is required to create the live Nebius client.")
        try:
            return NebiusClient(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                timeout_seconds=self.timeout_seconds,
                session=session,
            )
        except NebiusConfigurationError as exc:
            raise NexusConfigError("Nebius client configuration was rejected.") from exc

    def build_planner(self, *, client: ChatClient | None = None) -> NexusPlanner:
        """Create a Planner from a supplied mock/client or a real Nebius client.

        When ``client=None`` a live client instance is CREATED, but no model
        request is sent until ``planner.plan()`` is explicitly invoked.
        """
        from .planner import NexusPlanner

        selected = client if client is not None else self.build_client()
        return NexusPlanner(
            selected,
            max_steps=self.planner_max_steps,
            max_tokens=self.planner_max_tokens,
        )

    def build_orchestrator(
        self,
        executor: NexusExecutor,
        *,
        client: ChatClient | None = None,
        verifier: NexusVerifier | None = None,
    ) -> NexusOrchestrator:
        """Wire existing Nexus modules; does not plan, call APIs, or run tools."""
        from .orchestrator import NexusOrchestrator

        return NexusOrchestrator(
            planner=self.build_planner(client=client),
            executor=executor,
            verifier=verifier,
        )


NexusSettings = NexusConfig


__all__ = ["NexusConfig", "NexusSettings", "NexusConfigError"]
