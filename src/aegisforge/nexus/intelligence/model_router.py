"""Deterministic planning-only selection from a host-provided model catalog.

Never probes Nebius, estimates unpublished prices or makes network calls.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    name: str
    max_context_tokens: int
    cost_per_million_input_tokens: float
    available: bool = True
    capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        import math
        if (not isinstance(self.name, str) or not self.name.strip() or
                not isinstance(self.max_context_tokens, int) or
                isinstance(self.max_context_tokens, bool) or self.max_context_tokens <= 0 or
                not isinstance(self.cost_per_million_input_tokens, (int, float)) or
                not math.isfinite(self.cost_per_million_input_tokens) or
                self.cost_per_million_input_tokens < 0 or
                type(self.available) is not bool):
            raise ValueError("invalid catalog candidate")


def select_model(catalog: list[ModelCandidate], *, context_tokens: int,
                 required_capabilities: frozenset[str] = frozenset(),
                 max_cost_per_million: float | None = None) -> ModelCandidate | None:
    """Choose cheapest feasible declared model; None means host must decide."""
    if type(context_tokens) is not int or context_tokens < 0:
        raise ValueError("invalid context size")
    if len(catalog) > 100 or any(type(m) is not ModelCandidate for m in catalog):
        raise ValueError("invalid catalog")
    if max_cost_per_million is not None and max_cost_per_million < 0:
        raise ValueError("invalid ceiling")
    choices = [m for m in catalog if m.available and
               m.max_context_tokens >= context_tokens and
               m.capabilities.issuperset(required_capabilities) and
               (max_cost_per_million is None or
                m.cost_per_million_input_tokens <= max_cost_per_million)]
    return min(choices, key=lambda m: (m.cost_per_million_input_tokens, m.name), default=None)


__all__ = ["ModelCandidate", "select_model"]
