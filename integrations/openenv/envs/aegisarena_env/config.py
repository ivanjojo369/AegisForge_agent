from __future__ import annotations

from pydantic import BaseModel, Field


class RewardWeights(BaseModel):
    correctness_weight: float = Field(default=1.0)
    efficiency_penalty_weight: float = Field(default=0.2)
    tool_overuse_penalty_weight: float = Field(default=0.15)
    invalid_action_penalty_weight: float = Field(default=0.3)
    robustness_bonus_weight: float = Field(default=0.2)


class MissionMix(BaseModel):
    game_ops: float = Field(default=0.34, ge=0.0)
    finance_ops: float = Field(default=0.33, ge=0.0)
    business_ops: float = Field(default=0.33, ge=0.0)


class AegisArenaEnvConfig(BaseModel):
    env_name: str = Field(default="aegisarena_env")
    display_name: str = Field(default="AegisArena Env")
    version: str = Field(default="0.1.0")

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8012, ge=1, le=65535)
    base_url: str = Field(default="http://127.0.0.1:8012")

    sprint_focus: int = Field(default=1, ge=1)
    deterministic: bool = Field(default=True)
    heldout_mode: bool = Field(default=False)

    max_steps_default: int = Field(default=8, ge=1)
    budget_default: int = Field(default=100, ge=1)

    allowed_mission_types: list[str] = Field(
        default_factory=lambda: ["game_ops", "finance_ops", "business_ops"]
    )
    mission_mix: MissionMix = Field(default_factory=MissionMix)
    reward_weights: RewardWeights = Field(default_factory=RewardWeights)

    def normalized_mission_mix(self) -> dict[str, float]:
        raw = {
            "game_ops": self.mission_mix.game_ops,
            "finance_ops": self.mission_mix.finance_ops,
            "business_ops": self.mission_mix.business_ops,
        }

        allowed = {k: v for k, v in raw.items() if k in self.allowed_mission_types}
        total = sum(allowed.values())

        if total <= 0:
            even = 1.0 / max(len(self.allowed_mission_types), 1)
            return {name: even for name in self.allowed_mission_types}

        return {name: value / total for name, value in allowed.items()}

    def to_public_dict(self) -> dict[str, object]:
        return {
            "env_name": self.env_name,
            "display_name": self.display_name,
            "version": self.version,
            "host": self.host,
            "port": self.port,
            "base_url": self.base_url,
            "sprint_focus": self.sprint_focus,
            "deterministic": self.deterministic,
            "heldout_mode": self.heldout_mode,
            "max_steps_default": self.max_steps_default,
            "budget_default": self.budget_default,
            "allowed_mission_types": list(self.allowed_mission_types),
            "mission_mix": self.normalized_mission_mix(),
            "reward_weights": self.reward_weights.model_dump(),
        }


DEFAULT_CONFIG = AegisArenaEnvConfig()
DEFAULT_IGNORES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    ".ruff_cache",
}
