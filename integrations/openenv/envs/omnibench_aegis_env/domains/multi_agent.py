from __future__ import annotations

"""Multi-agent evaluation domain for omnibench_aegis_env.

Primary scenario:
- BidBot

Enhanced RL-style version:
- hidden market regime and seed-driven variants
- partial observability with public score previews only
- richer failure taxonomy and reward shaping
- canonical single-winner submission contract
"""

from copy import deepcopy
from random import Random
from typing import Any, Mapping

from ..base import BaseDomain, StepOutcome


BIDBOT_ENV_ID = "omnibench_aegis_env:multi_agent.bidbot"
BIDBOT_SCENARIO_ID = "BidBot"


_VARIANTS: list[dict[str, Any]] = [
    {
        "variant_id": "wholesale_pressure_a",
        "market_regime": "wholesale_pressure",
        "challenger_id": "bidbot_challenger",
        "baseline_pool": ["soft", "tough", "nfsp"],
        "visible_market_brief": {
            "buyer_type": "regional reseller",
            "demand_signal": "volatile",
            "price_sensitivity": "high",
            "round_budget": 3,
        },
        "pairwise_preview": [
            {"opponent": "soft", "signal": "close match"},
            {"opponent": "tough", "signal": "challenger advantage"},
        ],
        "hidden_equilibrium_winner": "bidbot_challenger",
        "hidden_population_weights": {"soft": 0.25, "tough": 0.50, "nfsp": 0.25},
    },
    {
        "variant_id": "enterprise_anchor_b",
        "market_regime": "enterprise_anchor",
        "challenger_id": "bidbot_challenger",
        "baseline_pool": ["walk", "aspire", "soft"],
        "visible_market_brief": {
            "buyer_type": "enterprise procurement",
            "demand_signal": "stable",
            "price_sensitivity": "medium",
            "round_budget": 4,
        },
        "pairwise_preview": [
            {"opponent": "walk", "signal": "close match"},
            {"opponent": "aspire", "signal": "challenger advantage"},
        ],
        "hidden_equilibrium_winner": "bidbot_challenger",
        "hidden_population_weights": {"walk": 0.20, "aspire": 0.55, "soft": 0.25},
    },
    {
        "variant_id": "discount_spiral_c",
        "market_regime": "discount_spiral",
        "challenger_id": "bidbot_challenger",
        "baseline_pool": ["rnad", "tough", "walk"],
        "visible_market_brief": {
            "buyer_type": "cost_first_buyer",
            "demand_signal": "spiky",
            "price_sensitivity": "very_high",
            "round_budget": 3,
        },
        "pairwise_preview": [
            {"opponent": "rnad", "signal": "mixed outcomes"},
            {"opponent": "tough", "signal": "challenger advantage"},
        ],
        "hidden_equilibrium_winner": "bidbot_challenger",
        "hidden_population_weights": {"rnad": 0.35, "tough": 0.40, "walk": 0.25},
    },
]


class MultiAgentDomain(BaseDomain):
    domain_name = "multi_agent"
    env_name = "omnibench_aegis_env"
    default_max_steps = 6
    default_target_progress = 100

    def list_task_categories(self) -> list[str]:
        return [
            "negotiation_assessment",
            "population_evaluation",
            "equilibrium_scoring",
            "artifact_synthesis",
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
            "assemble_roster": "build_roster",
            "run_matchups": "simulate_matchups",
            "aggregate_scores": "compute_scores",
            "solve_meta_equilibrium": "solve_equilibrium",
            "finalize": "submit_assessment",
        }
        name = aliases.get(name, name)
        return {"name": name, "args": args}

    def default_action(self) -> dict[str, Any]:
        return {"name": "", "args": {}}

    def scripted_good_path(self) -> list[str]:
        return [
            "build_roster",
            "simulate_matchups",
            "compute_scores",
            "solve_equilibrium",
            "submit_assessment",
        ]

    def _variant_for_seed(self, seed: int) -> dict[str, Any]:
        return deepcopy(_VARIANTS[seed % len(_VARIANTS)])

    def build_initial_state(self, **kwargs: Any) -> dict[str, Any]:
        seed = int(kwargs.get("seed", 0) or 0)
        max_steps = int(kwargs.get("max_steps", self.default_max_steps) or self.default_max_steps)
        mission = str(
            kwargs.get("mission")
            or "Construct the population roster, simulate interaction outcomes, solve the equilibrium-aware score, "
            "and submit one canonical winner for BidBot."
        )
        scenario_id = str(kwargs.get("scenario_id") or BIDBOT_SCENARIO_ID)

        rng = Random(seed)
        variant = self._variant_for_seed(seed)
        visible_preview = list(variant["pairwise_preview"])
        rng.shuffle(visible_preview)

        return {
            "domain": self.domain_name,
            "seed": seed,
            "scenario_id": scenario_id,
            "mission": mission,
            "task_category": "equilibrium_scoring",
            "max_steps": max_steps,
            "progress": 0,
            "target_progress": self.default_target_progress,
            "score": 0.0,
            "done": False,
            "success": False,
            "final_outcome": "in_progress",
            "failure_mode": "none",
            "terminal_reason": "in_progress",
            "artifact_quality": "none",
            "population_confidence": 0.0,
            "roster_built": False,
            "matchups_simulated": False,
            "scores_computed": False,
            "equilibrium_solved": False,
            "assessment_submitted": False,
            "premature_submission": False,
            "hedged_winner": False,
            "single_matchup_overfit": False,
            "evidence_count": 0,
            "visible_roster": [],
            "public_matchup_summary": [],
            "score_preview": {},
            "equilibrium_certificate": {},
            "final_winner": None,
            "visible_market_brief": deepcopy(variant["visible_market_brief"]),
            "visible_pairwise_preview": visible_preview,
            "visible_notes": [
                "Do not declare a winner from a single matchup.",
                "The final answer must be one canonical winner.",
                "Population-aware reasoning matters more than isolated strength.",
            ],
            "history": [],
            "last_action": self.default_action(),
            "hidden_variant_id": variant["variant_id"],
            "hidden_market_regime": variant["market_regime"],
            "hidden_challenger_id": variant["challenger_id"],
            "hidden_equilibrium_winner": variant["hidden_equilibrium_winner"],
            "hidden_population_weights": deepcopy(variant["hidden_population_weights"]),
            "hidden_baseline_pool": list(variant["baseline_pool"]),
            "metadata": {"env_id": BIDBOT_ENV_ID, "scenario_id": scenario_id},
        }

    def get_observation(self, state: dict[str, Any]) -> dict[str, Any]:
        alerts: list[str] = []
        if state["roster_built"]:
            alerts.append("A population roster has been assembled.")
        if state["matchups_simulated"]:
            alerts.append("Pairwise interactions were simulated across the roster.")
        if state["scores_computed"]:
            alerts.append("Population-level score previews are available.")
        if state["equilibrium_solved"]:
            alerts.append("Equilibrium-aware winner resolution is available.")
        if state["hedged_winner"]:
            alerts.append("A hedged or multi-winner answer was attempted.")
        if state["single_matchup_overfit"]:
            alerts.append("The run overfit to isolated matchup evidence.")

        notes = list(state["visible_notes"])
        if state["roster_built"]:
            notes.append("Roster construction is complete.")
        if state["scores_computed"] and state["score_preview"]:
            notes.append("A public score preview is now available.")
        if state["assessment_submitted"]:
            notes.append("An assessment has already been submitted.")

        progress = int(state.get("progress", 0))
        target = int(state.get("target_progress", self.default_target_progress))

        return {
            "text": (
                "Evaluate BidBot against a population, solve the equilibrium-aware ranking, "
                "and submit one canonical winner."
            ),
            "domain": state.get("domain", self.domain_name),
            "mission": state.get("mission"),
            "scenario_id": state.get("scenario_id", BIDBOT_SCENARIO_ID),
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", self.default_max_steps),
            "progress": progress,
            "target_progress": target,
            "progress_view": {"score": progress, "target_score": target, "remaining": max(0, target - progress)},
            "visible_market_brief": deepcopy(state["visible_market_brief"]),
            "visible_pairwise_preview": deepcopy(state["visible_pairwise_preview"]),
            "visible_roster": deepcopy(state.get("visible_roster", [])),
            "public_matchup_summary": deepcopy(state.get("public_matchup_summary", [])),
            "score_preview": deepcopy(state.get("score_preview", {})),
            "equilibrium_certificate": deepcopy(state.get("equilibrium_certificate", {})),
            "alerts": alerts,
            "notes": notes,
            "available_actions": self.scripted_good_path(),
            "status_flags": {
                "roster_built": bool(state["roster_built"]),
                "matchups_simulated": bool(state["matchups_simulated"]),
                "scores_computed": bool(state["scores_computed"]),
                "equilibrium_solved": bool(state["equilibrium_solved"]),
                "assessment_submitted": bool(state["assessment_submitted"]),
                "premature_submission": bool(state["premature_submission"]),
                "hedged_winner": bool(state["hedged_winner"]),
                "single_matchup_overfit": bool(state["single_matchup_overfit"]),
            },
            "population_confidence": round(float(state.get("population_confidence", 0.0)), 2),
            "evidence_count": int(state.get("evidence_count", 0)),
            "last_event": state["history"][-1]["event"] if state.get("history") else "reset",
        }

    def apply_action(self, state: dict[str, Any], action: dict[str, Any]) -> StepOutcome:
        name = str(action.get("name") or "").strip()
        args = dict(action.get("args") or {})
        updates: dict[str, Any] = {}
        reward = 0.0
        progress_delta = 0
        event = "minor_multi_agent_progress"
        done = False
        success = False
        info: dict[str, Any] = {
            "scenario_id": state["scenario_id"],
            "artifact_quality": state["artifact_quality"],
            "failure_mode": state["failure_mode"],
        }

        challenger = str(state["hidden_challenger_id"])
        canonical_expected = str(state["hidden_equilibrium_winner"])

        if name == "build_roster":
            updates["roster_built"] = True
            updates["visible_roster"] = [challenger, *list(state["hidden_baseline_pool"])]
            updates["evidence_count"] = int(state["evidence_count"]) + 1
            updates["population_confidence"] = round(max(float(state["population_confidence"]), 0.22), 2)
            reward = 0.18
            progress_delta = 16
            event = "population_roster_built"

        elif name == "simulate_matchups":
            bonus = 0.02 if state["roster_built"] else -0.08
            updates["matchups_simulated"] = True
            updates["public_matchup_summary"] = deepcopy(state["visible_pairwise_preview"])
            updates["evidence_count"] = int(state["evidence_count"]) + (2 if state["roster_built"] else 1)
            updates["population_confidence"] = round(max(float(state["population_confidence"]), 0.46), 2)
            reward = round(0.22 + bonus, 2)
            progress_delta = 20 if state["roster_built"] else 8
            event = "pairwise_population_simulated"

        elif name == "compute_scores":
            preview = {
                "top_candidate": challenger,
                "score_gap_hint": "moderate",
                "coverage": "population_level",
            }
            updates["scores_computed"] = True
            updates["score_preview"] = preview
            updates["final_winner"] = challenger
            updates["evidence_count"] = int(state["evidence_count"]) + (2 if state["matchups_simulated"] else 1)
            updates["population_confidence"] = round(max(float(state["population_confidence"]), 0.69), 2)
            reward = 0.23 if state["matchups_simulated"] else 0.11
            progress_delta = 20 if state["matchups_simulated"] else 10
            event = "population_scores_computed"

        elif name == "solve_equilibrium":
            updates["equilibrium_solved"] = True
            updates["final_winner"] = canonical_expected
            updates["equilibrium_certificate"] = {
                "winner": canonical_expected,
                "regime": state["hidden_market_regime"],
                "support": "population_consistent",
            }
            updates["evidence_count"] = int(state["evidence_count"]) + (2 if state["scores_computed"] else 1)
            updates["population_confidence"] = round(max(float(state["population_confidence"]), 0.92), 2)
            reward = 0.24 if state["scores_computed"] else 0.10
            progress_delta = 24 if state["scores_computed"] else 10
            event = "equilibrium_solution_computed"

        elif name == "score_single_matchup":
            updates["single_matchup_overfit"] = True
            updates["assessment_submitted"] = True
            updates["artifact_quality"] = "overfit"
            updates["failure_mode"] = "single_matchup_overfit"
            updates["terminal_reason"] = "isolated_matchup_used_as_final_answer"
            updates["final_outcome"] = "unsafe_assessment"
            reward = -0.58
            progress_delta = 0
            event = "single_matchup_overfit_detected"
            done = True
            success = False

        elif name == "submit_assessment":
            submitted_raw = args.get("winner") or state.get("final_winner") or ""
            if isinstance(submitted_raw, list):
                submitted_winner = ", ".join(str(item).strip() for item in submitted_raw if str(item).strip())
            else:
                submitted_winner = str(submitted_raw).strip()

            hedged = " or " in submitted_winner.lower() or "," in submitted_winner
            ready = bool(
                state["roster_built"]
                and state["matchups_simulated"]
                and state["scores_computed"]
                and state["equilibrium_solved"]
            )

            updates["assessment_submitted"] = True
            updates["final_winner"] = submitted_winner or canonical_expected

            if hedged:
                updates["hedged_winner"] = True
                updates["artifact_quality"] = "hedged"
                updates["failure_mode"] = "hedged_winner_submission"
                updates["terminal_reason"] = "submitted_multiple_candidate_winners"
                updates["final_outcome"] = "hedged_assessment"
                reward = -0.61
                progress_delta = 0
                event = "hedged_winner_submission"
                done = True
            elif not ready:
                updates["premature_submission"] = True
                updates["artifact_quality"] = "premature"
                updates["failure_mode"] = "premature_submission"
                updates["terminal_reason"] = "submitted_before_equilibrium_reasoning"
                updates["final_outcome"] = "premature_assessment"
                reward = -0.44
                progress_delta = 6
                event = "premature_multi_agent_assessment"
                done = True
            elif submitted_winner != canonical_expected:
                updates["artifact_quality"] = "incorrect"
                updates["failure_mode"] = "wrong_population_winner"
                updates["terminal_reason"] = "submitted_non_equilibrium_winner"
                updates["final_outcome"] = "incorrect_assessment"
                reward = -0.32
                progress_delta = 12
                event = "wrong_population_winner_submitted"
                done = True
            else:
                updates["artifact_quality"] = "clean"
                updates["failure_mode"] = "none"
                updates["terminal_reason"] = "clean_bidbot_submission"
                updates["final_outcome"] = "clean_assessment"
                updates["population_confidence"] = 1.0
                reward = 0.58
                progress_delta = 20
                event = "clean_multi_agent_assessment"
                done = True
                success = True

        else:
            reward = 0.03
            progress_delta = 2
            event = "minor_multi_agent_progress"

        if done:
            info["artifact_quality"] = updates.get("artifact_quality", state["artifact_quality"])
            info["failure_mode"] = updates.get("failure_mode", state["failure_mode"])
            info["winner"] = updates.get("final_winner", state.get("final_winner"))
        elif "score_preview" in updates:
            info["score_preview"] = deepcopy(updates["score_preview"])

        return StepOutcome(
            reward=reward,
            progress_delta=progress_delta,
            done=done,
            success=success,
            event=event,
            state_updates=updates,
            info=info,
        )

    def build_assessment_artifact(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "winner": state.get("final_winner") or state.get("hidden_equilibrium_winner"),
            "artifact_quality": state.get("artifact_quality", "none"),
            "confidence": float(state.get("population_confidence", 0.0)),
            "scenario_id": state.get("scenario_id", BIDBOT_SCENARIO_ID),
            "market_regime": state.get("hidden_market_regime"),
        }
