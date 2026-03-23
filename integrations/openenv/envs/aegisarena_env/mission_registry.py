from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from config import AegisArenaEnvConfig, DEFAULT_CONFIG
from missions.business_ops import build_business_ops_mission
from missions.finance_ops import build_finance_ops_mission
from missions.game_ops import build_game_ops_mission


MissionBuilder = Callable[[random.Random, AegisArenaEnvConfig, bool], dict[str, Any]]


class MissionRegistry:
    def __init__(self, config: AegisArenaEnvConfig | None = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self._builders: dict[str, MissionBuilder] = {
            "game_ops": build_game_ops_mission,
            "finance_ops": build_finance_ops_mission,
            "business_ops": build_business_ops_mission,
        }

    def supported_mission_types(self) -> list[str]:
        return [
            mission_type
            for mission_type in self.config.allowed_mission_types
            if mission_type in self._builders
        ]

    def has_mission_type(self, mission_type: str) -> bool:
        return mission_type in self.supported_mission_types()

    def choose_mission_type(
        self,
        rng: random.Random,
        requested_mission_type: str | None = None,
    ) -> str:
        if requested_mission_type and self.has_mission_type(requested_mission_type):
            return requested_mission_type

        supported = self.supported_mission_types()
        if not supported:
            raise ValueError("No supported mission types are configured for aegisarena_env.")

        normalized_mix = self.config.normalized_mission_mix()
        weights = [normalized_mix.get(name, 0.0) for name in supported]

        if sum(weights) <= 0:
            return rng.choice(supported)

        return rng.choices(supported, weights=weights, k=1)[0]

    def build_mission(
        self,
        *,
        seed: int | None = None,
        requested_mission_type: str | None = None,
        heldout_mode: bool = False,
    ) -> dict[str, Any]:
        rng = random.Random(seed)
        mission_type = self.choose_mission_type(
            rng=rng,
            requested_mission_type=requested_mission_type,
        )

        builder = self._builders.get(mission_type)
        if builder is None:
            raise ValueError(f"Mission builder not found for mission_type='{mission_type}'.")

        mission = builder(
            rng=rng,
            config=self.config,
            heldout_mode=heldout_mode,
        )

        if mission.get("mission_type") != mission_type:
            mission["mission_type"] = mission_type

        mission.setdefault("max_steps", self.config.max_steps_default)
        mission.setdefault("budget_default", self.config.budget_default)
        mission.setdefault("heldout_mode", heldout_mode)

        return mission


DEFAULT_MISSION_REGISTRY = MissionRegistry()
