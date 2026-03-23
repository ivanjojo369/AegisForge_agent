from __future__ import annotations

import hashlib
import random
from typing import Any


def normalize_seed(seed: int | None = None, fallback: int = 12345) -> int:
    if seed is None:
        return fallback
    return int(seed)


def derive_child_seed(parent_seed: int, label: str) -> int:
    raw = f"{parent_seed}:{label}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:8], 16)


def make_rng(seed: int | None = None, fallback: int = 12345) -> random.Random:
    normalized = normalize_seed(seed=seed, fallback=fallback)
    return random.Random(normalized)


def choose_weighted(
    *,
    rng: random.Random,
    items: list[str],
    weights: list[float],
) -> str:
    if not items:
        raise ValueError("choose_weighted requires at least one item.")
    if len(items) != len(weights):
        raise ValueError("items and weights must have the same length.")

    total = sum(weights)
    if total <= 0:
        return rng.choice(items)

    return rng.choices(items, weights=weights, k=1)[0]


def build_episode_seed_bundle(
    *,
    seed: int | None,
    mission_type: str,
) -> dict[str, Any]:
    root_seed = normalize_seed(seed)
    mission_seed = derive_child_seed(root_seed, mission_type)
    context_seed = derive_child_seed(root_seed, f"{mission_type}:context")
    reward_seed = derive_child_seed(root_seed, f"{mission_type}:reward")

    return {
        "root_seed": root_seed,
        "mission_seed": mission_seed,
        "context_seed": context_seed,
        "reward_seed": reward_seed,
        "mission_type": mission_type,
    }


def reproducibility_metadata(
    *,
    seed_bundle: dict[str, Any],
    heldout_mode: bool,
) -> dict[str, Any]:
    return {
        "seed_bundle": dict(seed_bundle),
        "heldout_mode": heldout_mode,
        "reproducible": True,
    }
