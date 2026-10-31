"""Reversible in-memory overlay effect dispatcher. No external adapters yet."""
from __future__ import annotations

from time import monotonic
from typing import Any

from .mechanic_composer import render_mechanic
from .overlay_visual_adapter import decorate_overlay


class OverlayEffectDispatcher:
    def __init__(self) -> None:
        self._effect: dict[str, Any] | None = None
        self._expires_at: float = 0.0
        self._sequence = 0

    def activate(self, pack_id: str, activity: str, duration: int, *,
                 preview: bool = False) -> dict[str, Any]:
        prepared = self.prepare(pack_id, activity, duration, preview=preview)
        return self.commit(prepared, duration)

    def prepare(self, pack_id: str, activity: str, duration: int, *,
                preview: bool = False) -> dict[str, Any]:
        """Render without mutation so evidence can be validated first."""
        pack = render_mechanic(pack_id, activity)
        return {**pack, "effect_number": self._sequence + 1,
                        "preview_only": preview,
                        "visual": decorate_overlay(pack_id, activity, duration)}

    def commit(self, prepared: dict[str, Any], duration: int) -> dict[str, Any]:
        self._sequence += 1
        self._effect = prepared
        self._expires_at = monotonic() + duration
        return self.snapshot()

    def clear(self) -> None:
        self._effect = None
        self._expires_at = 0.0

    def snapshot(self) -> dict[str, Any] | None:
        if self._effect is None:
            return None
        remaining = max(0, self._expires_at - monotonic())
        if remaining <= 0:
            self.clear()
            return None
        return {**self._effect, "seconds_remaining": round(remaining, 1)}
