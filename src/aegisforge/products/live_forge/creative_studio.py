"""Local deterministic creative scene, modified only by operator-authorized actions.

No external game, Twitch, OBS, filesystem, executable plugins, or raw chat access.
State lives in this server process; the evidence archive records the transition.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from .mechanic_composer import render_mechanic

LEGACY_SCHEMA = "aegisforge.live_forge.creative_studio.v1"
V2_SCHEMA = "aegisforge.live_forge.creative_studio.v2"
SCHEMA = "aegisforge.live_forge.creative_studio.v3"
_PALETTES = ("teal", "violet", "amber", "blue")
_SCENES = ("garden", "night_sky")
_LEGACY_SHAPES = ("star", "circle", "diamond", "leaf", "spark")
# Extend shapes only for v2. Keep v1 semantics for archived event replay.
_SHAPES = _LEGACY_SHAPES + ("moon", "sun", "heart")
_SUPPORTED = frozenset({
    "rpg_quest", "creative_palette", "progress_goal", "story_branch",
    "creative_constraint", "timed_challenge", "community_roles",
})
_MAX_ELEMENTS = 24
_MAX_PROGRESS = 24
_LEGACY_MAX_ELEMENTS = 8
_LEGACY_MAX_PROGRESS = 6


def _hash(value: dict[str, Any]) -> str:
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def initial_scene(*, schema: str = SCHEMA) -> dict[str, Any]:
    if schema not in (LEGACY_SCHEMA, V2_SCHEMA, SCHEMA):
        raise ValueError("invalid_studio_schema")
    return {"schema": schema, "revision": 0, "palette": "teal",
            "setting": "garden", "elements": [], "progress": 0,
            "constraint": False, "challenge": False, "roles": False}


def _validate_scene(scene: object) -> dict[str, Any]:
    if type(scene) is not dict or set(scene) != set(initial_scene()) or scene.get("schema") not in (LEGACY_SCHEMA, V2_SCHEMA, SCHEMA):
        raise ValueError("invalid_studio_snapshot")
    if (type(scene["revision"]) is not int or not 0 <= scene["revision"] <= 100000 or
            scene["palette"] not in _PALETTES or scene["setting"] not in _SCENES or
            type(scene["elements"]) is not list or len(scene["elements"]) > (_MAX_ELEMENTS if scene["schema"] == SCHEMA else _LEGACY_MAX_ELEMENTS) or
            type(scene["progress"]) is not int or not 0 <= scene["progress"] <= (
                _MAX_PROGRESS if scene["schema"] == SCHEMA else _LEGACY_MAX_PROGRESS) or
            any(type(scene[k]) is not bool for k in ("constraint", "challenge", "roles"))):
        raise ValueError("invalid_studio_values")
    for index, el in enumerate(scene["elements"]):
        if (type(el) is not dict or set(el) != {"id", "shape"} or
                type(el["id"]) is not int or el["id"] != index + 1 or
                type(el["shape"]) is not str or
                el["shape"] not in (_LEGACY_SHAPES if scene["schema"] == LEGACY_SCHEMA else _SHAPES)):
            raise ValueError("invalid_studio_element")
    return deepcopy(scene)


def _parts(pack_id: str) -> tuple[str, ...]:
    # Catalog validation is mandatory. 'art' is the only writable virtual adapter.
    pack = render_mechanic(pack_id, "art")
    if pack_id == "quest_palette_mix":
        ids = ("rpg_quest", "creative_palette")
    elif pack.get("composed"):
        ids = tuple(item["id"] for item in pack["components"])
    else:
        ids = (pack_id,)
    if not 1 <= len(ids) <= 2 or any(p not in _SUPPORTED for p in ids):
        raise ValueError("studio_mechanic_not_supported")
    return ids


def plan_transition(before: dict[str, Any], pack_id: str) -> dict[str, Any]:
    """Pure replayable transition. Does not mutate the live studio."""
    state = _validate_scene(before)
    if pack_id == "studio.reset":
        state = initial_scene(schema=before["schema"])
        parts = ("studio.reset",)
    else:
        parts = _parts(pack_id)
    for part in parts:
        if part == "rpg_quest":
            if len(state["elements"]) >= (
                _MAX_ELEMENTS if state["schema"] == SCHEMA else _LEGACY_MAX_ELEMENTS):
                raise ValueError("studio_element_limit")
            count = len(state["elements"])
            shapes = _LEGACY_SHAPES if state["schema"] == LEGACY_SCHEMA else _SHAPES
            state["elements"].append({"id": count + 1, "shape": shapes[count % len(shapes)]})
        elif part == "creative_palette":
            i = _PALETTES.index(state["palette"])
            state["palette"] = _PALETTES[(i + 1) % len(_PALETTES)]
        elif part == "progress_goal":
            if state["progress"] >= (_MAX_PROGRESS if state["schema"] == SCHEMA else _LEGACY_MAX_PROGRESS):
                raise ValueError("studio_progress_limit")
            state["progress"] += 1
        elif part == "story_branch":
            state["setting"] = _SCENES[1 - _SCENES.index(state["setting"])]
        elif part == "creative_constraint":
            state["constraint"] = not state["constraint"]
        elif part == "timed_challenge":
            state["challenge"] = not state["challenge"]
        elif part == "community_roles":
            state["roles"] = not state["roles"]
    state["revision"] = before["revision"] + 1
    return {"pack_id": pack_id, "parts": list(parts), "before": _validate_scene(before),
            "after": state, "before_sha256": _hash(before), "after_sha256": _hash(state),
            "scope": "live_forge_local_studio_only", "operator_confirmed": True}


def verify_transition(transition: object) -> bool:
    try:
        if type(transition) is not dict or set(transition) != {
            "pack_id", "parts", "before", "after", "before_sha256",
            "after_sha256", "scope", "operator_confirmed"
        } or transition["scope"] != "live_forge_local_studio_only" or transition["operator_confirmed"] is not True:
            return False
        expected = plan_transition(transition["before"], transition["pack_id"])
        return transition == expected
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


class CreativeStudio:
    def __init__(self) -> None:
        self._scene = initial_scene()

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._scene)

    def preview(self, pack_id: str, activity: str) -> dict[str, Any]:
        if activity != "art":
            raise ValueError("studio_activity_not_supported")
        return plan_transition(self._scene, pack_id)

    def preview_reset(self) -> dict[str, Any]:
        return plan_transition(self._scene, "studio.reset")

    def commit(self, transition: dict[str, Any]) -> dict[str, Any]:
        if not verify_transition(transition) or transition["before"] != self._scene:
            raise ValueError("studio_changed_before_commit")
        self._scene = deepcopy(transition["after"])
        return self.snapshot()
