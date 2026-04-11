from __future__ import annotations

"""OpenEnv adapter for AegisForge.

This adapter now targets the real omnibench_aegis_env client instead of the
legacy demo_env client. It keeps a compatibility surface similar to the older
adapter while exposing the richer current contract used by the integrated
OpenEnv environment in AegisForge_agent.
"""

import sys
from pathlib import Path
from typing import Any, Mapping

from .config import OpenEnvAdapterConfig


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _MissingOpenEnvClient:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def _missing(self) -> None:
        raise RuntimeError(
            "OpenEnvClient could not be imported from "
            "'integrations/openenv/envs/omnibench_aegis_env/client.py'."
        )

    def health(self) -> dict[str, Any]:
        self._missing()

    def contract(self) -> dict[str, Any]:
        self._missing()

    def reset(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        self._missing()

    def step(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        self._missing()

    def state(self) -> dict[str, Any]:
        self._missing()

    def actions(self) -> dict[str, Any]:
        self._missing()

    def validate_min_contract(self) -> dict[str, Any]:
        self._missing()


try:
    from integrations.openenv.envs.omnibench_aegis_env.client import OpenEnvClient  # type: ignore
except Exception:  # pragma: no cover
    OpenEnvClient = _MissingOpenEnvClient  # type: ignore[assignment]


class OpenEnvAdapter:
    """Thin adapter around the real omnibench_aegis_env HTTP client.

    Goals:
    - align AegisForge's adapter layer with the actual OpenEnv environment now
      hosted in AegisForge_agent;
    - preserve backward-friendly `reset(seed=...)` and `step(action=..., value=...)`
      calls when older code paths still use them;
    - expose the richer endpoints used by the current contract.
    """

    def __init__(self, config: OpenEnvAdapterConfig) -> None:
        self.config = config
        self.client = OpenEnvClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )

    def health(self) -> dict[str, Any]:
        return self.client.health()

    def contract(self) -> dict[str, Any]:
        return self.client.contract()

    def reset(
        self,
        seed: int | None = None,
        *,
        request: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if request is not None:
            merged = dict(request)
            if seed is not None and "seed" not in merged:
                merged["seed"] = seed
            if kwargs:
                options = dict(merged.get("options") or {})
                for key, value in kwargs.items():
                    if key in {"seed", "scenario_id", "mission_id", "options"}:
                        merged[key] = value
                    else:
                        options[key] = value
                merged["options"] = options
            return self.client.reset(request=merged)

        if seed is not None:
            kwargs.setdefault("seed", seed)
        return self.client.reset(**kwargs)

    def step(
        self,
        action: str | None = None,
        value: Any = None,
        *,
        request: Mapping[str, Any] | None = None,
        use_shorthand: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if request is not None:
            merged = dict(request)
            if kwargs:
                if use_shorthand:
                    merged.update(kwargs)
                else:
                    merged_args = dict(merged.get("args") or {})
                    merged_args.update(kwargs)
                    merged["args"] = merged_args
            return self.client.step(request=merged, use_shorthand=use_shorthand)

        if action is None:
            raise ValueError("step requires either `request` or `action`")

        # Backward-friendly shorthand path.
        return self.client.step(
            action=action,
            value=value,
            use_shorthand=use_shorthand,
            **kwargs,
        )

    def state(self) -> dict[str, Any]:
        return self.client.state()

    def actions(self) -> dict[str, Any]:
        return self.client.actions()

    def validate_min_contract(self) -> dict[str, Any]:
        return self.client.validate_min_contract()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "adapter": "openenv",
            "client_kind": "omnibench_aegis_env",
            "env_name": self.config.env_name,
            "base_url": self.config.base_url,
            "environment_url": self.config.base_url,
            "timeout": self.config.timeout,
        }
