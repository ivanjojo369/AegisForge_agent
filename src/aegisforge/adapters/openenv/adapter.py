from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .config import OpenEnvAdapterConfig


# Intenta resolver el cliente del entorno demo ubicado en:
# integrations/openenv/envs/demo_env/client.py
#
# Esto permite reutilizar el cliente real del entorno sin duplicar lógica.
REPO_ROOT = Path(__file__).resolve().parents[4]
DEMO_ENV_ROOT = REPO_ROOT / "integrations" / "openenv" / "envs" / "demo_env"

if DEMO_ENV_ROOT.exists() and str(DEMO_ENV_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_ENV_ROOT))

try:
    from client import DemoEnvClient  # type: ignore
except Exception:
    class DemoEnvClient:  # type: ignore
        def __init__(self, base_url: str, timeout: float) -> None:
            self.base_url = base_url
            self.timeout = timeout

        def _missing(self) -> None:
            raise RuntimeError(
                "DemoEnvClient no pudo importarse desde "
                "'integrations/openenv/envs/demo_env/client.py'."
            )

        def health(self) -> dict[str, Any]:
            self._missing()

        def reset(self, seed: int | None = None) -> dict[str, Any]:
            del seed
            self._missing()

        def step(self, action: str = "advance", value: int = 1) -> dict[str, Any]:
            del action, value
            self._missing()

        def state(self) -> dict[str, Any]:
            self._missing()


class OpenEnvAdapter:
    def __init__(self, config: OpenEnvAdapterConfig) -> None:
        self.config = config
        self.client = DemoEnvClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )

    def health(self) -> dict[str, Any]:
        return self.client.health()

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        return self.client.reset(seed=seed)

    def step(self, action: str = "advance", value: int = 1) -> dict[str, Any]:
        return self.client.step(action=action, value=value)

    def state(self) -> dict[str, Any]:
        return self.client.state()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "adapter": "openenv",
            "env_name": self.config.env_name,
            "base_url": self.config.base_url,
            "timeout": self.config.timeout,
        }
    