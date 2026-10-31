"""Allowlisted visual presentation for stream mods; no external effects.

This adapter only adds display hints for the existing local browser overlay.
It never receives raw Twitch chat, emits CSS, invokes a model, or runs code.
"""
from __future__ import annotations

from typing import Any

from .mechanic_composer import render_mechanic

# Preapproved semantic display themes. Only these values reach the browser.
_THEMES = frozenset({"teal", "violet", "amber", "blue"})


def decorate_overlay(pack_id: str, activity: str, duration_seconds: int) -> dict[str, Any]:
    """Return a bounded display-only descriptor for a catalog mechanic."""
    if type(duration_seconds) is not int or not 5 <= duration_seconds <= 60:
        raise ValueError("invalid_effect_duration")
    pack = render_mechanic(pack_id, activity)  # fails closed for unknown IDs
    ids = ([item["id"] for item in pack["components"]]
           if pack.get("composed") else [pack["id"]])
    theme = ("violet" if "creative_palette" in ids else
             "amber" if "timed_challenge" in ids else
             "blue" if "science_hypothesis" in ids or "stat_spotlight" in ids else
             "teal")
    assert theme in _THEMES
    return {
        "scope": "local_overlay_only",
        "theme": theme,
        "duration_seconds": duration_seconds,
        "countdown_enabled": True,
        # This is elapsed display time, never a fabricated task progress metric.
        "timer_label": "Tiempo restante del efecto",
        "tags": ids[:2],
    }
