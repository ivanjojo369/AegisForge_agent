"""Deterministic, allowlisted two-mechanic compositions for local overlays only.

No user-authored text, code, arbitrary plugins, external apps or Twitch writes.
"""
from __future__ import annotations

from typing import Any

from .mechanic_registry import ACTIVITIES, PACKS, render_pack

# The existing prebuilt quest_palette_mix remains a standalone preset;
# nesting mixes, repeating a mechanic, or combining more than two is prohibited.
# Only selected pairs of these pre-reviewed overlay mechanics may be combined.
COMPOSABLE = (
    "rpg_quest", "creative_palette", "progress_goal", "story_branch",
    "timed_challenge", "science_hypothesis", "code_review", "rhythm_overlay",
    "community_roles", "stat_spotlight", "creative_constraint",
)
_PREFIX = "mix:"
# A curated allowlist keeps the console manageable and avoids arbitrary nesting.
_PAIRS = (
    ("rpg_quest", "creative_palette"),
    ("rpg_quest", "progress_goal"),
    ("creative_palette", "progress_goal"),
    ("rpg_quest", "story_branch"),
    ("creative_palette", "story_branch"),
    ("story_branch", "community_roles"),
    ("timed_challenge", "progress_goal"),
    ("rpg_quest", "timed_challenge"),
    ("timed_challenge", "stat_spotlight"),
    ("science_hypothesis", "stat_spotlight"),
    ("science_hypothesis", "community_roles"),
    ("science_hypothesis", "progress_goal"),
    ("code_review", "community_roles"),
    ("code_review", "progress_goal"),
    ("rhythm_overlay", "creative_palette"),
    ("rhythm_overlay", "progress_goal"),
    ("creative_constraint", "creative_palette"),
    ("creative_constraint", "rpg_quest"),
    ("creative_constraint", "story_branch"),
)
assert all(a in COMPOSABLE and b in COMPOSABLE and a != b for a, b in _PAIRS)
assert len({frozenset(pair) for pair in _PAIRS}) == len(_PAIRS)
_IDS = {_PREFIX + "+".join(pair): pair for pair in _PAIRS}


def composition_id(first: str, second: str) -> str:
    """Return canonical identifier only for an approved, distinct pair."""
    if type(first) is not str or type(second) is not str:
        raise ValueError("invalid_composition")
    for ref, pair in _IDS.items():
        if {first, second} == set(pair):
            return ref
    raise ValueError("composition_not_allowed")


def is_mechanic_id(ref: object) -> bool:
    return type(ref) is str and (ref in PACKS or ref in _IDS)


def public_compositions() -> list[dict[str, Any]]:
    """List bounded combinations; no generation from chat or user strings."""
    return [{"id": ref, "name": " + ".join(PACKS[p]["name"] for p in pair),
             "components": list(pair), "effect_type": "overlay_only"}
            for ref, pair in _IDS.items()]


def render_mechanic(ref: str, activity: str) -> dict[str, Any]:
    """Render a single preset or a two-pack combination for a target activity."""
    if type(ref) is not str or activity not in ACTIVITIES:
        raise ValueError("unknown_mechanic_or_activity")
    if ref in PACKS:
        return render_pack(ref, activity)
    if ref not in _IDS:
        raise ValueError("composition_not_allowed")
    pair = _IDS[ref]
    rendered = [render_pack(part, activity) for part in pair]
    return {
        "id": ref,
        "name": " + ".join(part["name"] for part in rendered),
        "origin": " + ".join(part["origin"] for part in rendered),
        "activity": activity,
        "message": " | ".join(part["message"] for part in rendered),
        "effect_type": "overlay_only",
        "composed": True,
        "components": [{"id": item["id"], "name": item["name"],
                        "message": item["message"]} for item in rendered],
    }


def preview_composition(first: str, second: str, activity: str) -> dict[str, Any]:
    """Pure preview with zero mutation of streamer policy, votes or effects."""
    result = render_mechanic(composition_id(first, second), activity)
    return {**result, "preview_only": True, "activates_effect": False,
            "approved_for_round": False}
