from __future__ import annotations

import random
from typing import Any

from config import AegisArenaEnvConfig
from missions.common import (
    build_base_mission,
    build_context_item,
    build_hidden_truth,
    default_tools,
    heldout_suffix,
    make_mission_id,
    make_step_budget,
    merge_tools,
    mission_metadata_block,
)


def _sample_game_layout(rng: random.Random, heldout_mode: bool) -> dict[str, Any]:
    layouts = [
        {
            "layout_name": "corridor_key_exit",
            "map_hint": "The exit is not adjacent to the starting tile.",
            "inventory": {"energy": 3, "key": False},
            "goal": "Reach the exit with the key.",
            "optimal_plan": ["map_probe", "simulate_move", "take_action", "submit_final"],
            "expected_answer": "objective_reached",
            "difficulty": "medium",
        },
        {
            "layout_name": "resource_tradeoff",
            "map_hint": "A shorter path may consume more energy than the safer route.",
            "inventory": {"energy": 4, "key": True},
            "goal": "Reach the objective without exhausting energy.",
            "optimal_plan": ["inventory_check", "map_probe", "simulate_move", "submit_final"],
            "expected_answer": "objective_reached",
            "difficulty": "medium",
        },
        {
            "layout_name": "signal_and_decoy",
            "map_hint": "One visible route is a decoy; inspect before committing.",
            "inventory": {"energy": 5, "key": False},
            "goal": "Avoid the decoy path and reach the objective.",
            "optimal_plan": ["inspect_context", "map_probe", "simulate_move", "submit_final"],
            "expected_answer": "objective_reached",
            "difficulty": "hard" if heldout_mode else "medium",
        },
    ]

    return rng.choice(layouts)


def build_game_ops_mission(
    *,
    rng: random.Random,
    config: AegisArenaEnvConfig,
    heldout_mode: bool,
) -> dict[str, Any]:
    sample = _sample_game_layout(rng, heldout_mode)
    max_steps, budget_default = make_step_budget(
        config,
        max_steps=9 if heldout_mode else 8,
        budget_default=110 if heldout_mode else 100,
    )

    mission_id = make_mission_id("game")
    visible_context = [
        build_context_item("mode", "game_ops", tag=heldout_suffix(heldout_mode)),
        build_context_item("map_hint", sample["map_hint"]),
        build_context_item("inventory", sample["inventory"]),
        build_context_item("goal", sample["goal"]),
        build_context_item(
            "mission_metadata",
            mission_metadata_block(
                sprint_focus=config.sprint_focus,
                mission_family="game_ops",
                difficulty=sample["difficulty"],
                heldout_mode=heldout_mode,
            ),
        ),
    ]

    hidden_truth = build_hidden_truth(
        success_label="objective_reached",
        expected_answer=sample["expected_answer"],
        extra={
            "layout_name": sample["layout_name"],
            "optimal_plan": sample["optimal_plan"],
            "energy_budget": sample["inventory"]["energy"],
            "has_key_initially": sample["inventory"]["key"],
        },
    )

    available_tools = merge_tools(
        default_tools(),
        ["map_probe", "inventory_check", "simulate_move"],
    )

    return build_base_mission(
        mission_id=mission_id,
        mission_type="game_ops",
        mission_summary=(
            "Solve a sequential decision-making task with partial information, "
            "limited resources, and a final objective submission."
        ),
        visible_context=visible_context,
        hidden_truth=hidden_truth,
        available_tools=available_tools,
        max_steps=max_steps,
        budget_default=budget_default,
        heldout_mode=heldout_mode,
    )
