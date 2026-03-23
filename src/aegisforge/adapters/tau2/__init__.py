from __future__ import annotations
from .adapter import Tau2Adapter
from .config import Tau2AdapterConfig

__all__ = ["Tau2Adapter", "Tau2AdapterConfig"]

def tau2_handle(text: str) -> str:
    return (
        "[τ² adapter]\n"
        "Placeholder implementation. Next steps:\n"
        "  - wire a small public demo task that exercises quipu_lab tools\n"
        "  - keep outputs deterministic and lightweight\n"
    )
