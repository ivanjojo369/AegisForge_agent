from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    env: str
    initialized: bool


class ObservationModel(BaseModel):
    message: str
    score: int
    step_count: int
    remaining_steps: int


class StateModel(BaseModel):
    episode_id: str
    score: int
    step_count: int
    max_steps: int
    target_score: int
    done: bool
    success: bool
    last_action: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class ResetRequest(BaseModel):
    seed: int | None = None


class ResetResponse(BaseModel):
    observation: ObservationModel
    state: StateModel
    info: dict[str, Any]


class StepRequest(BaseModel):
    action: Literal["advance", "hold", "finish"] = "advance"
    value: int = Field(default=1, ge=0, le=5)


class StepResponse(BaseModel):
    observation: ObservationModel
    reward: float
    done: bool
    truncated: bool
    info: dict[str, Any]
    state: StateModel
    