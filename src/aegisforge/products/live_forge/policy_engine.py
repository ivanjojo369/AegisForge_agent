"""Host-controlled limits for voluntary, reversible stream overlay mechanics."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from time import monotonic

from .mechanic_registry import ACTIVITIES
from .mechanic_composer import is_mechanic_id


@dataclass(frozen=True)
class StreamerPolicy:
    enabled: bool = False
    activity: str = "art"
    option_a: str = "rpg_quest"
    option_b: str = "creative_palette"
    cooldown_seconds: int = 30
    duration_seconds: int = 20

    @classmethod
    def from_payload(cls, data: object) -> "StreamerPolicy":
        if type(data) is not dict or set(data) != {
            "enabled", "activity", "option_a", "option_b", "cooldown_seconds", "duration_seconds"
        }:
            raise ValueError("invalid_policy_fields")
        if (type(data["enabled"]) is not bool or
                data["activity"] not in ACTIVITIES or
                not is_mechanic_id(data["option_a"]) or
                not is_mechanic_id(data["option_b"]) or
                data["option_a"] == data["option_b"] or
                type(data["cooldown_seconds"]) is not int or
                not 10 <= data["cooldown_seconds"] <= 300 or
                type(data["duration_seconds"]) is not int or
                not 5 <= data["duration_seconds"] <= 60):
            raise ValueError("policy_outside_allowlist")
        return cls(**data)

    def public(self) -> dict[str, object]:
        return {"enabled": self.enabled, "activity": self.activity,
                "option_a": self.option_a, "option_b": self.option_b,
                "cooldown_seconds": self.cooldown_seconds,
                "duration_seconds": self.duration_seconds}


class PolicyEngine:
    def __init__(self) -> None:
        self.active = StreamerPolicy()
        self._last_effect: float | None = None

    def configure(self, data: object) -> dict[str, object]:
        self.active = StreamerPolicy.from_payload(data)
        return self.active.public()

    def apply_check(self, pack_id: str) -> tuple[bool, str]:
        if not self.active.enabled:
            return False, "policy_paused"
        if pack_id not in (self.active.option_a, self.active.option_b):
            return False, "mechanic_not_allowed"
        if (self._last_effect is not None and
                monotonic() - self._last_effect < self.active.cooldown_seconds):
            return False, "mechanic_cooldown"
        return True, "allowed"

    def cooldown_remaining_seconds(self) -> int:
        """Operator-facing wait time, without changing enforcement."""
        if self._last_effect is None:
            return 0
        return max(0, ceil(self.active.cooldown_seconds - (monotonic() - self._last_effect)))

    def mark_effect(self) -> None:
        self._last_effect = monotonic()
