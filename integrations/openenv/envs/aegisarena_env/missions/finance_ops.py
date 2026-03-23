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


def _sample_finance_case(rng: random.Random, heldout_mode: bool) -> dict[str, Any]:
    revenue = rng.randint(120, 220)
    cost = rng.randint(60, 180)
    profit = revenue - cost
    margin = round((profit / revenue) * 100, 2) if revenue else 0.0

    question_templates = [
        "Is the business unit profitable? Respond with PROFITABLE or NOT_PROFITABLE.",
        "Based on the snapshot, determine whether profit is positive. Respond with PROFITABLE or NOT_PROFITABLE.",
        "Review the row and classify the unit as PROFITABLE or NOT_PROFITABLE.",
    ]

    anomaly_flag = heldout_mode and rng.choice([True, False])

    visible_context = [
        {
            "quarter": rng.choice(["Q1", "Q2", "Q3", "Q4"]),
            "revenue": revenue,
            "cost": cost,
        }
    ]

    if anomaly_flag:
        visible_context.append(
            {
                "note": "One source suggests an adjustment may have been applied after the draft export.",
                "draft_adjustment": rng.choice([-8, -5, 5, 7]),
            }
        )

    expected_label = "PROFITABLE" if profit > 0 else "NOT_PROFITABLE"

    return {
        "question": rng.choice(question_templates),
        "table_rows": visible_context,
        "profit": profit,
        "margin": margin,
        "expected_label": expected_label,
        "anomaly_flag": anomaly_flag,
        "difficulty": "hard" if heldout_mode else "medium",
    }


def build_finance_ops_mission(
    *,
    rng: random.Random,
    config: AegisArenaEnvConfig,
    heldout_mode: bool,
) -> dict[str, Any]:
    sample = _sample_finance_case(rng, heldout_mode)
    max_steps, budget_default = make_step_budget(
        config,
        max_steps=9 if heldout_mode else 8,
        budget_default=105 if heldout_mode else 100,
    )

    mission_id = make_mission_id("finance")

    visible_context: list[dict[str, Any]] = [
        build_context_item("mode", "finance_ops", tag=heldout_suffix(heldout_mode)),
        build_context_item("finance_table", sample["table_rows"]),
        build_context_item("question", sample["question"]),
        build_context_item(
            "mission_metadata",
            mission_metadata_block(
                sprint_focus=config.sprint_focus,
                mission_family="finance_ops",
                difficulty=sample["difficulty"],
                heldout_mode=heldout_mode,
            ),
        ),
    ]

    hidden_truth = build_hidden_truth(
        success_label=sample["expected_label"],
        expected_answer=sample["expected_label"],
        extra={
            "profit": sample["profit"],
            "margin_percent": sample["margin"],
            "anomaly_flag": sample["anomaly_flag"],
        },
    )

    available_tools = merge_tools(
        default_tools(),
        ["table_lookup", "calc_metric", "consistency_check"],
    )

    return build_base_mission(
        mission_id=mission_id,
        mission_type="finance_ops",
        mission_summary=(
            "Analyze a compact financial snapshot, reason about profitability, "
            "and submit the correct classification with minimal wasted actions."
        ),
        visible_context=visible_context,
        hidden_truth=hidden_truth,
        available_tools=available_tools,
        max_steps=max_steps,
        budget_default=budget_default,
        heldout_mode=heldout_mode,
    )
