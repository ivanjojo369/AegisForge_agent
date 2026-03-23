from __future__ import annotations
import os
from .adapter import OpenEnvAdapter
from .config import OpenEnvAdapterConfig
from .solver import AegisArenaSolver, AegisArenaSolverConfig

__all__ = [
    "OpenEnvAdapter",
    "OpenEnvAdapterConfig",
    "AegisArenaSolver",
    "AegisArenaSolverConfig",
]

__all__ = ["OpenEnvAdapter", "OpenEnvAdapterConfig"]

def openenv_handle(text: str) -> str:
    base_url = os.getenv("OPENENV_BASE_URL", "http://127.0.0.1:8080")
    return (
        "[OpenEnv adapter]\n"
        f"Configured env server: {base_url}\n"
        "Placeholder implementation. Next steps:\n"
        "  1) choose one env (e.g., websearch_env)\n"
        "  2) call its HTTP API\n"
        "  3) return a short episode trace.\n"
    )
