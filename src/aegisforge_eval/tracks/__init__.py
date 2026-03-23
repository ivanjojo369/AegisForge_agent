from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .openenv import evaluate as evaluate_openenv
from .security_arena import evaluate as evaluate_security_arena
from .tau2 import evaluate as evaluate_tau2

TrackEvaluator = Callable[[Mapping[str, Any] | None], Any]

TRACK_REGISTRY: dict[str, TrackEvaluator] = {
    "openenv": evaluate_openenv,
    "security_arena": evaluate_security_arena,
    "tau2": evaluate_tau2,
}


def get_track_names() -> list[str]:
    return sorted(TRACK_REGISTRY)


def get_evaluator(name: str) -> TrackEvaluator:
    try:
        return TRACK_REGISTRY[name]
    except KeyError as exc:
        available = ", ".join(get_track_names())
        raise KeyError(f"unknown track {name!r}; available: {available}") from exc
