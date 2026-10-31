"""Immutable virtual-activity contracts shared by seven Live Forge categories.

The public contract is descriptive: it grants NO permissions. Any mutation must
pass the existing streamer policy, operator token and Colossus evidence path.
Nothing here accesses Twitch, other applications, code or external systems.
"""
from __future__ import annotations

from .mechanic_composer import render_mechanic
from .mechanic_registry import ACTIVITIES, PACKS

CONTRACT_SCHEMA = "aegisforge.live_forge.activity_contract.v1"
BRIDGE_ACTIVITIES = ("gaming", "coding", "science", "music", "sports", "talk")
STUDIO_PARTS = frozenset({
    "rpg_quest", "creative_palette", "progress_goal", "story_branch",
    "creative_constraint", "timed_challenge", "community_roles",
})
BRIDGE_PARTS = STUDIO_PARTS | frozenset({
    "science_hypothesis", "code_review", "stat_spotlight",
})

# Names and descriptions label virtual counters, NOT external effects.
CATEGORY_LABELS = {
    "gaming": ("Videojuegos", "Objetivos virtuales", "Hitos", "Datos"),
    "art": ("Arte", "Figuras", "Objetivos", "Escenas"),
    "coding": ("Programación", "Tareas", "Hitos", "Datos"),
    "science": ("Ciencia", "Hipótesis", "Hitos", "Datos"),
    "music": ("Música autorizada", "Ideas visuales", "Hitos", "Datos"),
    "sports": ("Deportes", "Análisis virtuales", "Hitos", "Datos"),
    "talk": ("Charlas", "Temas virtuales", "Hitos", "Datos"),
}
assert tuple(CATEGORY_LABELS) == ACTIVITIES


def _parts(mechanic_id: str, activity: str) -> tuple[str, ...]:
    """Parse only the existing, fixed catalog; arbitrary text is never a mechanic."""
    rendered = render_mechanic(mechanic_id, activity)
    if mechanic_id == "quest_palette_mix":
        parts = ("rpg_quest", "creative_palette")
    elif rendered.get("composed"):
        parts = tuple(item["id"] for item in rendered["components"])
    else:
        parts = (mechanic_id,)
    if not 1 <= len(parts) <= 2 or any(part not in PACKS for part in parts):
        raise ValueError("unknown_contract_mechanic")
    return parts


def virtual_compatible(activity: str, mechanic_id: str) -> bool:
    if activity not in ACTIVITIES:
        return False
    try:
        return all(part in (STUDIO_PARTS if activity == "art" else BRIDGE_PARTS)
                   for part in _parts(mechanic_id, activity))
    except (ValueError, TypeError, KeyError):
        return False


def public_contracts() -> list[dict[str, object]]:
    """Return a static capability matrix; no permission or execution tokens."""
    from .mechanic_composer import public_compositions
    ids = [*PACKS, *(item["id"] for item in public_compositions())]
    result = []
    for activity in ACTIVITIES:
        title, tasks, milestones, insights = CATEGORY_LABELS[activity]
        result.append({
            "schema": CONTRACT_SCHEMA,
            "activity": activity,
            "label": title,
            "target": "studio" if activity == "art" else "bridge",
            "state_scope": "local_virtual_only",
            "writes_external_apps": False,
            "requires_operator": True,
            "requires_policy": True,
            "max_counters": 24,
            "metric_labels": {"tasks": tasks, "milestones": milestones,
                              "insights": insights},
            "virtual_compatible": [mid for mid in ids if virtual_compatible(activity, mid)],
            "overlay_only": [mid for mid in ids if not virtual_compatible(activity, mid)],
        })
    return result
