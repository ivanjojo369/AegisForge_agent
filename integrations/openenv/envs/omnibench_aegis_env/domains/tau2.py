from __future__ import annotations

"""Tau2 evaluation domain for omnibench_aegis_env.

Primary scenario:
- TicketTwister

Enhanced RL-style version:
- hidden task bundle and seed-driven variants
- partial observability over ticket tasks and scoring targets
- richer failure taxonomy and reward shaping
- grounded bundle scorecard contract
"""

from copy import deepcopy
from random import Random
from typing import Any, Mapping

from ..base import BaseDomain, StepOutcome


TICKETTWISTER_ENV_ID = "omnibench_aegis_env:tau2.tickettwister"
TICKETTWISTER_SCENARIO_ID = "TicketTwister"


_VARIANTS: list[dict[str, Any]] = [
    {
        "variant_id": "airline_reissue_a",
        "benchmark_domain": "airline",
        "visible_tasks": [
            {"task_id": "tt_air_1", "summary": "Customer requests a same-day itinerary twist.", "policy_hint": "fare_rules_apply"},
            {"task_id": "tt_air_2", "summary": "Refund request conflicts with prior reissue.", "policy_hint": "refund_consistency"},
        ],
        "hidden_expected_rewards": {"tt_air_1": 1.0, "tt_air_2": 1.0},
        "hidden_target_score": 2.0,
    },
    {
        "variant_id": "retail_bundle_b",
        "benchmark_domain": "retail",
        "visible_tasks": [
            {"task_id": "tt_ret_1", "summary": "Order replacement and review abuse overlap.", "policy_hint": "replacement_policy"},
            {"task_id": "tt_ret_2", "summary": "Address change collides with shipment lock.", "policy_hint": "shipment_lock"},
        ],
        "hidden_expected_rewards": {"tt_ret_1": 1.0, "tt_ret_2": 1.0},
        "hidden_target_score": 2.0,
    },
    {
        "variant_id": "telecom_hold_c",
        "benchmark_domain": "telecom",
        "visible_tasks": [
            {"task_id": "tt_tel_1", "summary": "Plan downgrade request during outage escalation.", "policy_hint": "service_credit_policy"},
            {"task_id": "tt_tel_2", "summary": "Account ownership and SIM swap risk interact.", "policy_hint": "identity_verification"},
        ],
        "hidden_expected_rewards": {"tt_tel_1": 1.0, "tt_tel_2": 1.0},
        "hidden_target_score": 2.0,
    },
]


class Tau2Domain(BaseDomain):
    domain_name = "tau2"
    env_name = "omnibench_aegis_env"
    default_max_steps = 6
    default_target_progress = 100

    def list_task_categories(self) -> list[str]:
        return [
            "airline_customer_support",
            "retail_customer_support",
            "telecom_customer_support",
            "multi_turn_evaluation",
        ]

    def normalize_action(self, action: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict(action or {})
        if "name" not in raw and "action" in raw:
            args = dict(raw.get("args") or {})
            for key, value in raw.items():
                if key not in {"action", "args"}:
                    args[key] = value
            raw = {"name": raw.get("action"), "args": args}

        name = str(raw.get("name") or "").strip()
        args = dict(raw.get("args") or {})

        aliases = {
            "load_bundle": "load_tasks",
            "prepare_sim_user": "prepare_user",
            "run_bundle": "run_conversation",
            "score_bundle": "score_task_bundle",
            "finalize_report": "submit_assessment",
        }
        name = aliases.get(name, name)
        return {"name": name, "args": args}

    def default_action(self) -> dict[str, Any]:
        return {"name": "", "args": {}}

    def scripted_good_path(self) -> list[str]:
        return [
            "load_tasks",
            "prepare_user",
            "run_conversation",
            "score_task_bundle",
            "submit_assessment",
        ]

    def _variant_for_seed(self, seed: int) -> dict[str, Any]:
        return deepcopy(_VARIANTS[seed % len(_VARIANTS)])

    def build_initial_state(self, **kwargs: Any) -> dict[str, Any]:
        seed = int(kwargs.get("seed", 0) or 0)
        max_steps = int(kwargs.get("max_steps", self.default_max_steps) or self.default_max_steps)
        scenario_id = str(kwargs.get("scenario_id") or TICKETTWISTER_SCENARIO_ID)
        mission = str(
            kwargs.get("mission")
            or "Load the ticket bundle, prepare the simulated user, orchestrate the multi-turn evaluation, "
            "score the tasks, and submit one grounded assessment for TicketTwister."
        )

        rng = Random(seed)
        variant = self._variant_for_seed(seed)
        visible_tasks = list(variant["visible_tasks"])
        rng.shuffle(visible_tasks)

        benchmark_domain = str(kwargs.get("benchmark_domain") or variant["benchmark_domain"])

        return {
            "domain": self.domain_name,
            "seed": seed,
            "scenario_id": scenario_id,
            "mission": mission,
            "task_category": f"{benchmark_domain}_customer_support",
            "benchmark_domain": benchmark_domain,
            "max_steps": max_steps,
            "progress": 0,
            "target_progress": self.default_target_progress,
            "score": 0.0,
            "done": False,
            "success": False,
            "final_outcome": "in_progress",
            "failure_mode": "none",
            "terminal_reason": "in_progress",
            "result_quality": "none",
            "coverage_ratio": 0.0,
            "tasks_loaded": False,
            "simulated_user_ready": False,
            "turns_orchestrated": False,
            "bundle_scored": False,
            "assessment_submitted": False,
            "premature_submission": False,
            "optimistic_scoring": False,
            "task_rewards": {},
            "evidence_count": 0,
            "public_scorecard": {},
            "conversation_trace_public": [],
            "visible_ticket_bundle": visible_tasks,
            "visible_notes": [
                "Do not score tasks that were not actually covered.",
                "The final report must use the visible bundle only.",
                "Complete the user-simulator setup before judging the run.",
            ],
            "history": [],
            "last_action": self.default_action(),
            "hidden_variant_id": variant["variant_id"],
            "hidden_expected_rewards": deepcopy(variant["hidden_expected_rewards"]),
            "hidden_target_score": float(variant["hidden_target_score"]),
            "metadata": {"env_id": TICKETTWISTER_ENV_ID, "scenario_id": scenario_id},
        }

    def get_observation(self, state: dict[str, Any]) -> dict[str, Any]:
        alerts: list[str] = []
        if state["tasks_loaded"]:
            alerts.append("The visible ticket bundle is loaded.")
        if state["simulated_user_ready"]:
            alerts.append("The user simulator is ready.")
        if state["turns_orchestrated"]:
            alerts.append("Conversation turns were orchestrated across the bundle.")
        if state["bundle_scored"]:
            alerts.append("A grounded bundle scorecard is available.")
        if state["optimistic_scoring"]:
            alerts.append("The reported score exceeded grounded bundle coverage.")
        if state["premature_submission"]:
            alerts.append("The report was submitted before the bundle was fully evaluated.")

        notes = list(state["visible_notes"])
        if state["assessment_submitted"]:
            notes.append("An assessment has already been submitted.")

        progress = int(state.get("progress", 0))
        target = int(state.get("target_progress", self.default_target_progress))

        return {
            "text": (
                "Evaluate the TicketTwister task bundle with grounded multi-turn orchestration "
                "and submit one clean report."
            ),
            "domain": state.get("domain", self.domain_name),
            "mission": state.get("mission"),
            "scenario_id": state.get("scenario_id", TICKETTWISTER_SCENARIO_ID),
            "benchmark_domain": state.get("benchmark_domain", "mock"),
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", self.default_max_steps),
            "progress": progress,
            "target_progress": target,
            "progress_view": {"score": progress, "target_score": target, "remaining": max(0, target - progress)},
            "visible_ticket_bundle": deepcopy(state["visible_ticket_bundle"]),
            "public_scorecard": deepcopy(state.get("public_scorecard", {})),
            "conversation_trace_public": deepcopy(state.get("conversation_trace_public", [])),
            "alerts": alerts,
            "notes": notes,
            "available_actions": self.scripted_good_path(),
            "status_flags": {
                "tasks_loaded": bool(state["tasks_loaded"]),
                "simulated_user_ready": bool(state["simulated_user_ready"]),
                "turns_orchestrated": bool(state["turns_orchestrated"]),
                "bundle_scored": bool(state["bundle_scored"]),
                "assessment_submitted": bool(state["assessment_submitted"]),
                "premature_submission": bool(state["premature_submission"]),
                "optimistic_scoring": bool(state["optimistic_scoring"]),
            },
            "coverage_ratio": round(float(state.get("coverage_ratio", 0.0)), 2),
            "evidence_count": int(state.get("evidence_count", 0)),
            "last_event": state["history"][-1]["event"] if state.get("history") else "reset",
        }

    @staticmethod
    def _normalize_reward_bundle(payload: Any) -> dict[str, float]:
        if not isinstance(payload, Mapping):
            return {}
        return {str(key): float(value) for key, value in payload.items()}

    def apply_action(self, state: dict[str, Any], action: dict[str, Any]) -> StepOutcome:
        name = str(action.get("name") or "").strip()
        args = dict(action.get("args") or {})
        updates: dict[str, Any] = {}
        reward = 0.0
        progress_delta = 0
        event = "minor_tau2_progress"
        done = False
        success = False
        info: dict[str, Any] = {
            "scenario_id": state["scenario_id"],
            "result_quality": state["result_quality"],
            "failure_mode": state["failure_mode"],
        }

        if name == "load_tasks":
            updates["tasks_loaded"] = True
            updates["evidence_count"] = int(state["evidence_count"]) + 1
            updates["coverage_ratio"] = round(max(float(state["coverage_ratio"]), 0.18), 2)
            reward = 0.17
            progress_delta = 16
            event = "tau2_bundle_loaded"

        elif name == "prepare_user":
            updates["simulated_user_ready"] = True
            updates["evidence_count"] = int(state["evidence_count"]) + 1
            updates["coverage_ratio"] = round(max(float(state["coverage_ratio"]), 0.34), 2)
            reward = 0.18 if state["tasks_loaded"] else 0.08
            progress_delta = 16 if state["tasks_loaded"] else 8
            event = "tau2_user_simulator_ready"

        elif name == "run_conversation":
            trace = [
                {"task_id": item["task_id"], "coverage": "observed"}
                for item in state["visible_ticket_bundle"]
                if isinstance(item, Mapping) and item.get("task_id")
            ]
            updates["turns_orchestrated"] = True
            updates["conversation_trace_public"] = trace
            updates["evidence_count"] = int(state["evidence_count"]) + (2 if state["simulated_user_ready"] else 1)
            updates["coverage_ratio"] = round(max(float(state["coverage_ratio"]), 0.62), 2)
            reward = 0.23 if state["simulated_user_ready"] else 0.11
            progress_delta = 22 if state["simulated_user_ready"] else 10
            event = "tau2_conversation_orchestrated"

        elif name == "score_task_bundle":
            normalized_rewards = self._normalize_reward_bundle(args.get("task_rewards") or {})
            expected = dict(state["hidden_expected_rewards"])
            expected_keys = set(expected.keys())
            provided_keys = set(normalized_rewards.keys())
            total = float(sum(normalized_rewards.values()))
            optimistic = bool(provided_keys - expected_keys) or total > float(state["hidden_target_score"]) + 1e-9

            updates["bundle_scored"] = True
            updates["task_rewards"] = deepcopy(normalized_rewards)
            updates["public_scorecard"] = {
                "num_tasks_scored": len(normalized_rewards),
                "observed_total_reward": round(total, 2),
                "coverage_basis": "visible_bundle_only",
            }
            updates["evidence_count"] = int(state["evidence_count"]) + (2 if state["turns_orchestrated"] else 1)
            updates["coverage_ratio"] = round(max(float(state["coverage_ratio"]), 0.86), 2)

            if optimistic:
                updates["optimistic_scoring"] = True
                reward = -0.42
                progress_delta = 4
                event = "optimistic_bundle_scoring_detected"
            else:
                reward = 0.24 if state["turns_orchestrated"] else 0.10
                progress_delta = 24 if state["turns_orchestrated"] else 10
                event = "tau2_bundle_scored"

            info["observed_total_reward"] = round(total, 2)

        elif name == "submit_assessment":
            ready = bool(
                state["tasks_loaded"]
                and state["simulated_user_ready"]
                and state["turns_orchestrated"]
                and state["bundle_scored"]
            )
            expected = dict(state["hidden_expected_rewards"])
            observed = dict(state.get("task_rewards") or {})
            clean = ready and observed == expected and not state["optimistic_scoring"]

            updates["assessment_submitted"] = True
            if state["optimistic_scoring"]:
                updates["result_quality"] = "optimistic"
                updates["failure_mode"] = "optimistic_scoring"
                updates["terminal_reason"] = "report_exceeded_grounded_bundle_score"
                updates["final_outcome"] = "optimistic_report"
                reward = -0.56
                progress_delta = 0
                event = "optimistic_tau2_report"
                done = True
            elif not ready:
                updates["premature_submission"] = True
                updates["result_quality"] = "premature"
                updates["failure_mode"] = "premature_submission"
                updates["terminal_reason"] = "submitted_before_bundle_evaluation"
                updates["final_outcome"] = "premature_report"
                reward = -0.40
                progress_delta = 6
                event = "premature_tau2_report"
                done = True
            elif not clean:
                updates["result_quality"] = "incorrect"
                updates["failure_mode"] = "wrong_task_reward_bundle"
                updates["terminal_reason"] = "submitted_incorrect_task_scorecard"
                updates["final_outcome"] = "incorrect_report"
                reward = -0.28
                progress_delta = 12
                event = "incorrect_tau2_report"
                done = True
            else:
                updates["result_quality"] = "clean"
                updates["failure_mode"] = "none"
                updates["terminal_reason"] = "clean_tickettwister_submission"
                updates["final_outcome"] = "clean_report"
                updates["coverage_ratio"] = 1.0
                reward = 0.58
                progress_delta = 22
                event = "clean_tau2_report"
                done = True
                success = True

        else:
            reward = 0.03
            progress_delta = 2
            event = "minor_tau2_progress"

        if done:
            info["result_quality"] = updates.get("result_quality", state["result_quality"])
            info["failure_mode"] = updates.get("failure_mode", state["failure_mode"])
            info["task_rewards"] = deepcopy(updates.get("task_rewards", state.get("task_rewards", {})))

        return StepOutcome(
            reward=reward,
            progress_delta=progress_delta,
            done=done,
            success=success,
            event=event,
            state_updates=updates,
            info=info,
        )

    def build_result_artifact(self, state: dict[str, Any]) -> dict[str, Any]:
        task_rewards = dict(state.get("task_rewards") or {})
        total_reward = float(sum(task_rewards.values()))
        return {
            "domain": state.get("benchmark_domain"),
            "score": total_reward,
            "task_rewards": task_rewards,
            "result_quality": state.get("result_quality", "none"),
            "scenario_id": state.get("scenario_id", TICKETTWISTER_SCENARIO_ID),
        }
