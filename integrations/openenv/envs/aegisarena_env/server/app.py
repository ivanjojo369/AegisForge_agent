from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from config import DEFAULT_CONFIG
from mission_registry import DEFAULT_MISSION_REGISTRY
from models import (
    HealthResponse,
    ObservationModel,
    ResetRequest,
    ResetResponse,
    RootResponse,
    StateModel,
    StepRequest,
    StepResponse,
)
from seeds import build_episode_seed_bundle, make_rng, reproducibility_metadata
from state_engine import apply_step_transition, initial_engine_meta


app = FastAPI(
    title="AegisArena Env",
    version=DEFAULT_CONFIG.version,
    description=(
        "OpenEnv environment for AegisForge_agent focused on "
        "AgentX–AgentBeats Phase 2 Purple Sprint 1."
    ),
)

_ENV_STATE: StateModel | None = None
_ENV_META: dict[str, Any] | None = None
_ENGINE_META: dict[str, Any] | None = None


def _require_state() -> StateModel:
    if _ENV_STATE is None:
        raise HTTPException(
            status_code=409,
            detail="Environment not initialized. Call POST /reset first.",
        )
    return _ENV_STATE


def _build_observation(state: StateModel) -> ObservationModel:
    if _ENV_META is None:
        raise RuntimeError("Environment metadata is missing.")

    return ObservationModel(
        mission_id=state.mission_id,
        mission_type=state.mission_type,
        mission_summary=str(_ENV_META["mission_summary"]),
        available_tools=list(_ENV_META["available_tools"]),
        observed_context=list(_ENV_META["visible_context"]),
        recent_actions=state.history[-3:],
        step_count=state.step_count,
        max_steps=state.max_steps,
        budget_remaining=state.budget_remaining,
        cost_so_far=state.cost_so_far,
        done=state.done,
    )


def _build_state_model(mission: dict[str, Any], episode_id: str) -> StateModel:
    return StateModel(
        episode_id=episode_id,
        mission_id=mission["mission_id"],
        mission_type=mission["mission_type"],
        hidden_truth=mission["hidden_truth"],
        step_count=0,
        max_steps=mission["max_steps"],
        budget_remaining=mission["budget_default"],
        cost_so_far=0,
        score=0.0,
        success=False,
        done=False,
        failure_mode=None,
        history=[],
    )


@app.get("/", response_model=RootResponse, tags=["meta"])
def root() -> RootResponse:
    return RootResponse(
        name=DEFAULT_CONFIG.display_name,
        version=DEFAULT_CONFIG.version,
        sprint_focus=DEFAULT_CONFIG.sprint_focus,
        supported_mission_types=DEFAULT_MISSION_REGISTRY.supported_mission_types(),
        endpoints={
            "health": "/health",
            "reset": "POST /reset",
            "step": "POST /step",
            "state": "GET /state",
        },
    )


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    active_mission_type = None
    if _ENV_STATE is not None:
        active_mission_type = _ENV_STATE.mission_type

    return HealthResponse(
        status="ok",
        env=DEFAULT_CONFIG.env_name,
        initialized=_ENV_STATE is not None,
        active_mission_type=active_mission_type,
    )


@app.post("/reset", response_model=ResetResponse, tags=["env"])
def reset_env(payload: ResetRequest | None = None) -> ResetResponse:
    global _ENV_STATE, _ENV_META, _ENGINE_META

    payload = payload or ResetRequest()

    chooser_rng = make_rng(seed=payload.seed)
    mission_type = DEFAULT_MISSION_REGISTRY.choose_mission_type(
        rng=chooser_rng,
        requested_mission_type=payload.mission_type,
    )

    seed_bundle = build_episode_seed_bundle(
        seed=payload.seed,
        mission_type=mission_type,
    )

    mission = DEFAULT_MISSION_REGISTRY.build_mission(
        seed=seed_bundle["mission_seed"],
        requested_mission_type=mission_type,
        heldout_mode=payload.heldout_mode,
    )

    episode_id = f'{seed_bundle["root_seed"]}-{mission["mission_id"]}'

    _ENV_META = {
        "mission_summary": mission["mission_summary"],
        "available_tools": mission["available_tools"],
        "visible_context": mission["visible_context"],
        "heldout_mode": mission["heldout_mode"],
        "seed_metadata": reproducibility_metadata(
            seed_bundle=seed_bundle,
            heldout_mode=payload.heldout_mode,
        ),
    }

    _ENV_STATE = _build_state_model(mission, episode_id=episode_id)
    _ENGINE_META = initial_engine_meta(initial_budget=_ENV_STATE.budget_remaining)

    return ResetResponse(
        observation=_build_observation(_ENV_STATE),
        state=_ENV_STATE,
        info={
            "env_name": DEFAULT_CONFIG.env_name,
            "reset": True,
            "mission_type": mission_type,
            "heldout_mode": payload.heldout_mode,
            "seed_metadata": _ENV_META["seed_metadata"],
        },
    )


@app.post("/step", response_model=StepResponse, tags=["env"])
def step_env(payload: StepRequest) -> StepResponse:
    global _ENV_STATE, _ENGINE_META

    state = _require_state()

    if state.done:
        raise HTTPException(
            status_code=409,
            detail="Episode already finished. Call POST /reset to start a new episode.",
        )

    transition_result, updated_engine_meta = apply_step_transition(
        state=state.model_dump(),
        step_request=payload.model_dump(),
        config=DEFAULT_CONFIG,
        engine_meta=_ENGINE_META,
    )

    _ENV_STATE = StateModel(**transition_result["state"])
    _ENGINE_META = updated_engine_meta

    return StepResponse(
        observation=_build_observation(_ENV_STATE),
        reward=float(transition_result["reward"]),
        done=bool(transition_result["done"]),
        truncated=bool(transition_result["truncated"]),
        info=dict(transition_result["info"]),
        state=_ENV_STATE,
    )


@app.get("/state", response_model=StateModel, tags=["env"])
def get_state() -> StateModel:
    return _require_state()
