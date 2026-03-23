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


def _sample_business_case(rng: random.Random, heldout_mode: bool) -> dict[str, Any]:
    priority = rng.choice(["low", "medium", "high"])
    stage = rng.choice(["new", "triage", "pending_review"])
    sla_hours = rng.choice([4, 8, 24])

    if priority == "high":
        expected_route = "escalate_ops"
    elif priority == "medium":
        expected_route = "priority_queue"
    else:
        expected_route = "standard_queue"

    customer_type = rng.choice(["standard", "premium", "enterprise"])
    blockers = rng.sample(
        ["missing_owner", "missing_approval", "billing_flag", "compliance_note"],
        k=rng.randint(1, 2),
    )

    return {
        "priority": priority,
        "stage": stage,
        "sla_hours": sla_hours,
        "customer_type": customer_type,
        "blockers": blockers,
        "expected_route": expected_route,
        "difficulty": "hard" if heldout_mode else "medium",
    }


def build_business_ops_mission(
    *,
    rng: random.Random,
    config: AegisArenaEnvConfig,
    heldout_mode: bool,
) -> dict[str, Any]:
    sample = _sample_business_case(rng, heldout_mode)
    max_steps, budget_default = make_step_budget(
        config,
        max_steps=9 if heldout_mode else 8,
        budget_default=105 if heldout_mode else 100,
    )

    mission_id = make_mission_id("business")

    visible_context: list[dict[str, Any]] = [
        build_context_item("mode", "business_ops", tag=heldout_suffix(heldout_mode)),
        build_context_item(
            "ticket",
            {
                "id": mission_id,
                "priority": sample["priority"],
                "stage": sample["stage"],
                "sla_hours": sample["sla_hours"],
                "customer_type": sample["customer_type"],
            },
        ),
        build_context_item("blockers", sample["blockers"]),
        build_context_item(
            "policy_hint",
            (
                "High priority cases go to Ops, medium priority to Priority Queue, "
                "and low priority to Standard Queue."
            ),
        ),
        build_context_item(
            "mission_metadata",
            mission_metadata_block(
                sprint_focus=config.sprint_focus,
                mission_family="business_ops",
                difficulty=sample["difficulty"],
                heldout_mode=heldout_mode,
            ),
        ),
    ]

    hidden_truth = build_hidden_truth(
        success_label=sample["expected_route"],
        expected_answer=sample["expected_route"],
        extra={
            "expected_route": sample["expected_route"],
            "priority": sample["priority"],
            "stage": sample["stage"],
            "sla_hours": sample["sla_hours"],
            "customer_type": sample["customer_type"],
            "blockers": sample["blockers"],
        },
    )

    available_tools = merge_tools(
        default_tools(),
        ["ticket_lookup", "policy_lookup", "route_case", "update_stage"],
    )

    return build_base_mission(
        mission_id=mission_id,
        mission_type="business_ops",
        mission_summary=(
            "Review the business case, respect process priority, and send the case "
            "to the correct route with minimal wasted actions."
        ),
        visible_context=visible_context,
        hidden_truth=hidden_truth,
        available_tools=available_tools,
        max_steps=max_steps,
        budget_default=budget_default,
        heldout_mode=heldout_mode,
    )
