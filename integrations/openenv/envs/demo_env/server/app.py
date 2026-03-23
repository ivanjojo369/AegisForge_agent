from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException

from models import (
    HealthResponse,
    ObservationModel,
    ResetRequest,
    ResetResponse,
    StateModel,
    StepRequest,
    StepResponse,
)


app = FastAPI(
    title="AegisForge OpenEnv Demo Env",
    version="0.1.0",
    description="Entorno OpenEnv mínimo para pruebas locales de reset/step/state.",
)

_ENV_STATE: StateModel | None = None


def _new_state() -> StateModel:
    return StateModel(
        episode_id=str(uuid.uuid4()),
        score=0,
        step_count=0,
        max_steps=5,
        target_score=3,
        done=False,
        success=False,
        last_action=None,
        history=[],
    )


def _build_observation(state: StateModel) -> ObservationModel:
    remaining = max(state.max_steps - state.step_count, 0)
    return ObservationModel(
        message=(
            f"Episode {state.episode_id[:8]} | "
            f"score={state.score} | "
            f"step_count={state.step_count} | "
            f"remaining_steps={remaining}"
        ),
        score=state.score,
        step_count=state.step_count,
        remaining_steps=remaining,
    )


def _require_state() -> StateModel:
    if _ENV_STATE is None:
        raise HTTPException(
            status_code=409,
            detail="Environment not initialized. Call POST /reset first.",
        )
    return _ENV_STATE


@app.get("/", tags=["meta"])
def root() -> dict[str, Any]:
    return {
        "name": "AegisForge OpenEnv Demo Env",
        "version": "0.1.0",
        "endpoints": {
            "health": "/health",
            "reset": "POST /reset",
            "step": "POST /step",
            "state": "GET /state",
        },
    }


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        env="demo_env",
        initialized=_ENV_STATE is not None,
    )


@app.post("/reset", response_model=ResetResponse, tags=["env"])
def reset_env(payload: ResetRequest | None = None) -> ResetResponse:
    del payload  # reservado para uso futuro

    global _ENV_STATE
    _ENV_STATE = _new_state()

    return ResetResponse(
        observation=_build_observation(_ENV_STATE),
        state=_ENV_STATE,
        info={
            "env_name": "demo_env",
            "reset": True,
        },
    )


@app.post("/step", response_model=StepResponse, tags=["env"])
def step_env(payload: StepRequest) -> StepResponse:
    global _ENV_STATE
    state = _require_state()

    if state.done:
        raise HTTPException(
            status_code=409,
            detail="Episode already finished. Call POST /reset to start a new episode.",
        )

    reward = 0.0
    action_record: dict[str, Any] = {
        "action": payload.action,
        "value": payload.value,
    }

    state.step_count += 1
    state.last_action = payload.action

    if payload.action == "advance":
        state.score += payload.value
        reward = float(payload.value)

    elif payload.action == "hold":
        reward = 0.0

    elif payload.action == "finish":
        state.done = True
        if state.score >= state.target_score:
            state.success = True
            reward = 1.0
        else:
            state.success = False
            reward = -1.0

    if state.score >= state.target_score:
        state.done = True
        state.success = True

    truncated = False
    if state.step_count >= state.max_steps and not state.done:
        state.done = True
        truncated = True
        state.success = state.score >= state.target_score

    action_record.update(
        {
            "score_after": state.score,
            "step_count_after": state.step_count,
            "done": state.done,
            "success": state.success,
            "reward": reward,
        }
    )
    state.history.append(action_record)

    _ENV_STATE = state

    return StepResponse(
        observation=_build_observation(state),
        reward=reward,
        done=state.done,
        truncated=truncated,
        info={
            "target_score": state.target_score,
            "max_steps": state.max_steps,
        },
        state=state,
    )


@app.get("/state", response_model=StateModel, tags=["env"])
def get_state() -> StateModel:
    return _require_state()
