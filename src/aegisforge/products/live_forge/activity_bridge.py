"""Small, deterministic virtual bridge for six non-art stream activities.

Only allows reviewed mechanics to change counters and labels in isolated local
in-memory worlds. Never executes code, instruments, equipment, chat or APIs.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from .mechanic_composer import render_mechanic

SCHEMA = "aegisforge.live_forge.activity_bridge.v1"
from .activity_contracts import BRIDGE_ACTIVITIES, virtual_compatible

ACTIVITIES = BRIDGE_ACTIVITIES
CAP = 24
PARTS = frozenset({
    "rpg_quest", "creative_palette", "progress_goal", "story_branch",
    "science_hypothesis", "code_review", "stat_spotlight", "timed_challenge",
    "community_roles", "creative_constraint",
})

def _digest(obj: Any) -> str:
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()

def initial_activity(activity: str) -> dict[str, Any]:
    if activity not in ACTIVITIES:
        raise ValueError("bridge_activity_not_supported")
    return {"schema": SCHEMA, "activity": activity, "revision": 0,
            "tasks": 0, "milestones": 0, "insights": 0,
            "mode": "baseline", "theme": "teal", "challenge": False,
            "roles": False, "constraint": False}

def validate_state(value: Any) -> dict[str, Any]:
    if type(value) is not dict or value.get("activity") not in ACTIVITIES or             set(value) != set(initial_activity(value["activity"])) or value.get("schema") != SCHEMA:
        raise ValueError("invalid_activity_state")
    if (any(type(value[k]) is not int or not 0 <= value[k] <= CAP
            for k in ("tasks", "milestones", "insights")) or
        type(value["revision"]) is not int or not 0 <= value["revision"] <= 100000 or
        value["mode"] not in ("baseline", "alternative") or
        value["theme"] not in ("teal", "violet", "amber", "blue") or
        any(type(value[k]) is not bool for k in ("challenge", "roles", "constraint"))):
        raise ValueError("invalid_activity_values")
    return deepcopy(value)

def _mechanic_parts(pack_id: str, activity: str) -> tuple[str, ...]:
    rendered = render_mechanic(pack_id, activity)
    if pack_id == "quest_palette_mix":
        ids = ("rpg_quest", "creative_palette")
    elif rendered.get("composed"):
        ids = tuple(component["id"] for component in rendered["components"])
    else:
        ids = (pack_id,)
    if not 1 <= len(ids) <= 2 or any(x not in PARTS for x in ids) or not virtual_compatible(activity, pack_id):
        raise ValueError("bridge_mechanic_not_supported")
    return ids

def plan_transition(before: dict[str, Any], pack_id: str) -> dict[str, Any]:
    state = validate_state(before)
    if pack_id == "bridge.reset":
        state = initial_activity(state["activity"])
        parts = ("bridge.reset",)
    else:
        parts = _mechanic_parts(pack_id, state["activity"])
    for part in parts:
        if part in ("rpg_quest", "science_hypothesis", "code_review"):
            if state["tasks"] >= CAP:
                raise ValueError("bridge_task_limit")
            state["tasks"] += 1
        elif part == "progress_goal":
            if state["milestones"] >= CAP:
                raise ValueError("bridge_milestone_limit")
            state["milestones"] += 1
        elif part == "stat_spotlight":
            if state["insights"] >= CAP:
                raise ValueError("bridge_insight_limit")
            state["insights"] += 1
        elif part == "story_branch":
            state["mode"] = "alternative" if state["mode"] == "baseline" else "baseline"
        elif part == "creative_palette":
            palette = ("teal", "violet", "amber", "blue")
            state["theme"] = palette[(palette.index(state["theme"]) + 1) % len(palette)]
        elif part == "timed_challenge":
            state["challenge"] = not state["challenge"]
        elif part == "community_roles":
            state["roles"] = not state["roles"]
        elif part == "creative_constraint":
            state["constraint"] = not state["constraint"]
    state["revision"] = before["revision"] + 1
    return {"pack_id": pack_id, "parts": list(parts), "before": validate_state(before),
            "after": state, "before_sha256": _digest(before),
            "after_sha256": _digest(state), "scope": "local_virtual_activity_only",
            "operator_confirmed": True}

def verify_transition(transition: Any) -> bool:
    try:
        return (type(transition) is dict and
                set(transition) == {"pack_id", "parts", "before", "after",
                                    "before_sha256", "after_sha256", "scope", "operator_confirmed"} and
                transition["scope"] == "local_virtual_activity_only" and
                transition["operator_confirmed"] is True and
                transition == plan_transition(transition["before"], transition["pack_id"]))
    except (ValueError, TypeError, KeyError, OverflowError):
        return False

class ActivityBridge:
    def __init__(self) -> None:
        self._states = {activity: initial_activity(activity) for activity in ACTIVITIES}

    def snapshot(self, activity: str | None = None) -> dict[str, Any]:
        if activity is None:
            return deepcopy(self._states)
        if activity not in self._states:
            raise ValueError("bridge_activity_not_supported")
        return deepcopy(self._states[activity])

    def preview(self, pack_id: str, activity: str) -> dict[str, Any]:
        return plan_transition(self.snapshot(activity), pack_id)

    def preview_reset(self, activity: str) -> dict[str, Any]:
        return plan_transition(self.snapshot(activity), "bridge.reset")

    def commit(self, transition: dict[str, Any]) -> dict[str, Any]:
        if not verify_transition(transition):
            raise ValueError("bridge_transition_invalid")
        activity = transition["before"]["activity"]
        if self._states[activity] != transition["before"]:
            raise ValueError("bridge_changed_before_commit")
        self._states[activity] = deepcopy(transition["after"])
        return self.snapshot(activity)
