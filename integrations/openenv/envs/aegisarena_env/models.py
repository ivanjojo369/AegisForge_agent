from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


MissionType = Literal["game_ops", "finance_ops", "business_ops"]
BaseActionName = Literal[
    "inspect_context",
    "query_tool",
    "propose_plan",
    "take_action",
    "submit_final",
]


class HealthResponse(BaseModel):
    status: str
    env: str
    initialized: bool
    active_mission_type: MissionType | None = None


class MissionToolSpec(BaseModel):
    name: str
    description: str


class MissionMetadata(BaseModel):
    mission_id: str
    mission_type: MissionType
    mission_summary: str
    available_tools: list[str] = Field(default_factory=list)


class ObservationModel(BaseModel):
    mission_id: str
    mission_type: MissionType
    mission_summary: str
    available_tools: list[str] = Field(default_factory=list)
    observed_context: list[dict[str, Any]] = Field(default_factory=list)
    recent_actions: list[dict[str, Any]] = Field(default_factory=list)
    step_count: int
    max_steps: int
    budget_remaining: int
    cost_so_far: int
    done: bool


class StateModel(BaseModel):
    episode_id: str
    mission_id: str
    mission_type: MissionType
    hidden_truth: dict[str, Any] = Field(default_factory=dict)
    step_count: int
    max_steps: int
    budget_remaining: int
    cost_so_far: int
    score: float
    success: bool
    done: bool
    failure_mode: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class ResetRequest(BaseModel):
    seed: int | None = None
    mission_type: MissionType | None = None
    heldout_mode: bool = False


class ResetResponse(BaseModel):
    observation: ObservationModel
    state: StateModel
    info: dict[str, Any]


class StepRequest(BaseModel):
    action: BaseActionName
    target: str | None = None
    tool_name: str | None = None
    answer: str | None = None
    plan_text: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StepResponse(BaseModel):
    observation: ObservationModel
    reward: float
    done: bool
    truncated: bool
    info: dict[str, Any]
    state: StateModel


class RootResponse(BaseModel):
    name: str
    version: str
    sprint_focus: int
    supported_mission_types: list[MissionType] = Field(default_factory=list)
    endpoints: dict[str, str]


class MissionSample(BaseModel):
    mission_id: str
    mission_type: MissionType
    mission_summary: str
    visible_context: list[dict[str, Any]] = Field(default_factory=list)
    hidden_truth: dict[str, Any] = Field(default_factory=dict)
    available_tools: list[str] = Field(default_factory=list)
    max_steps: int = 8
    budget_default: int = 100


class RewardBreakdown(BaseModel):
    correctness: float = 0.0
    efficiency_penalty: float = 0.0
    tool_overuse_penalty: float = 0.0
    invalid_action_penalty: float = 0.0
    robustness_bonus: float = 0.0
    total: float = 0.0
