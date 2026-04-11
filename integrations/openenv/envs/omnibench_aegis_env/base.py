from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepOutcome:
    reward: float = 0.0
    progress_delta: int = 0
    done: bool = False
    success: bool = False
    truncated: bool = False
    event: str = "progress"
    info: dict[str, Any] = field(default_factory=dict)
    state_updates: dict[str, Any] = field(default_factory=dict)


class BaseDomain(ABC):
    """
    Base reusable contract for OmniBench Aegis OpenEnv domains.

    Every domain should either:
    - implement `build_initial_state()` + `apply_action()` and use the generic
      `reset()` / `step()` from this base class, or
    - override `reset()` / `step()` if it needs a custom execution contract.

    Standard state keys maintained here:
    - domain
    - seed
    - mission
    - max_steps
    - step_count
    - progress
    - target_progress
    - score
    - done
    - success
    - last_action
    - history
    - metadata
    """

    domain_name = "base"
    env_name = "omnibench_aegis_env"
    default_max_steps = 20
    default_target_progress = 100

    def list_task_categories(self) -> list[str]:
        return []

    @abstractmethod
    def build_initial_state(self, **kwargs: Any) -> dict[str, Any]:
        """Return the raw initial state for the domain."""
        raise NotImplementedError

    @abstractmethod
    def apply_action(self, state: dict[str, Any], action: dict[str, Any]) -> StepOutcome:
        """Apply one environment action to the current state and return a StepOutcome."""
        raise NotImplementedError

    def normalize_action(self, action: dict[str, Any] | None) -> dict[str, Any]:
        return dict(action or {})

    def default_action(self) -> dict[str, Any]:
        return {}

    def get_observation(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "text": (
                f"Domain: {state.get('domain', self.domain_name)}. "
                f"Mission: {state.get('mission', 'N/A')} "
                f"Step {state.get('step_count', 0)}/{state.get('max_steps', self.default_max_steps)}. "
                f"Progress {state.get('progress', 0)}/{state.get('target_progress', self.default_target_progress)}."
            ),
            "domain": state.get("domain", self.domain_name),
            "mission": state.get("mission"),
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", self.default_max_steps),
            "progress": state.get("progress", 0),
            "target_progress": state.get("target_progress", self.default_target_progress),
            "last_event": state["history"][-1]["event"] if state.get("history") else "reset",
        }

    def reset(self, **kwargs: Any) -> dict[str, Any]:
        raw_state = self.build_initial_state(**kwargs)
        state = self._ensure_common_state(raw_state)

        return {
            "observation": self.get_observation(state),
            "state": state,
            "info": self._build_reset_info(state),
        }

    def step(self, state: dict[str, Any], action: dict[str, Any] | None) -> dict[str, Any]:
        state = deepcopy(state)

        if state.get("done", False):
            return {
                "observation": self.get_observation(state),
                "reward": 0.0,
                "done": True,
                "truncated": False,
                "info": {
                    "event": "episode_already_finished",
                    "success": state.get("success", False),
                    "domain": state.get("domain", self.domain_name),
                    "env_name": self.env_name,
                    "env_id": (state.get("metadata") or {}).get("env_id"),
                },
                "state": state,
            }

        normalized_action = self.normalize_action(action)
        outcome = self.apply_action(state, normalized_action)

        state["step_count"] = int(state.get("step_count", 0)) + 1
        state["progress"] = min(
            int(state.get("target_progress", self.default_target_progress)),
            int(state.get("progress", 0)) + int(outcome.progress_delta),
        )
        state["score"] = round(float(state.get("score", 0.0)) + float(outcome.reward), 6)
        state["last_action"] = normalized_action

        for key, value in outcome.state_updates.items():
            state[key] = value

        auto_success = state["progress"] >= int(state.get("target_progress", self.default_target_progress))
        state["success"] = bool(outcome.success or auto_success)

        reached_max_steps = state["step_count"] >= int(state.get("max_steps", self.default_max_steps))
        state["done"] = bool(outcome.done or state["success"] or reached_max_steps)

        truncated = bool(outcome.truncated or (reached_max_steps and not state["success"]))

        history_entry = {
            "step": state["step_count"],
            "event": outcome.event,
            "reward": float(outcome.reward),
            "progress": int(state["progress"]),
        }
        if outcome.info:
            history_entry["info"] = deepcopy(outcome.info)

        state.setdefault("history", []).append(history_entry)

        info = {
            "event": outcome.event,
            "success": state["success"],
            "domain": state.get("domain", self.domain_name),
            "env_name": self.env_name,
            "env_id": (state.get("metadata") or {}).get("env_id"),
            **deepcopy(outcome.info),
        }

        return {
            "observation": self.get_observation(state),
            "reward": float(outcome.reward),
            "done": bool(state["done"]),
            "truncated": truncated,
            "info": info,
            "state": state,
        }

    def clone_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(state)

    def _ensure_common_state(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(state)

        normalized.setdefault("domain", self.domain_name)
        normalized.setdefault("seed", 0)
        normalized.setdefault("mission", f"Complete the {self.domain_name} objective.")
        normalized.setdefault("max_steps", self.default_max_steps)
        normalized.setdefault("step_count", 0)
        normalized.setdefault("progress", 0)
        normalized.setdefault("target_progress", self.default_target_progress)
        normalized.setdefault("score", 0.0)
        normalized.setdefault("done", False)
        normalized.setdefault("success", False)
        normalized.setdefault("last_action", self.default_action())
        normalized.setdefault("history", [])
        normalized.setdefault("metadata", {})

        return normalized

    def _build_reset_info(self, state: dict[str, Any]) -> dict[str, Any]:
        metadata = state.get("metadata") or {}
        return {
            "domain": state.get("domain", self.domain_name),
            "env_name": self.env_name,
            "env_id": metadata.get("env_id"),
            "mission": state.get("mission"),
            "task_category": state.get("task_category"),
        }
