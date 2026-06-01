from __future__ import annotations

"""SkillsBench / General-Purpose Agent track profile.

This profile is intentionally artifact-first.  SkillsBench tasks are not a
single-domain benchmark: they span software engineering, office automation,
spreadsheets, slides, document processing, media conversion, scientific work,
finance/economics, formal reasoning, and cybersecurity audits.

The profile does not execute tools by itself.  It gives router/planner layers a
stable identity for the track so SkillsBench traffic does not silently fall back
to OpenEnv, Security, Tau2, or legacy AgentBeats profiles.
"""

from inspect import signature
from typing import Any

from ..budget_guard import BudgetLimits
from .profile import TrackProfile


def _budget_limits(**preferred: int) -> BudgetLimits:
    """Build BudgetLimits while tolerating older/newer constructor schemas.

    The repo has evolved across several benchmark tracks.  Some branches use
    different BudgetLimits field names, so this helper keeps the SkillsBench
    profile compatible without forcing a synchronized budget_guard.py edit.
    """

    fallback_values: dict[str, int] = {
        # Common step / planning limits.
        "max_steps": 10,
        "max_plan_steps": 10,
        "max_reasoning_steps": 10,
        "step_budget": 10,

        # LLM / model-call limits.
        "max_llm_calls": 4,
        "llm_call_cap": 4,
        "max_model_calls": 4,

        # Tool / artifact-oriented limits.
        "max_tool_calls": 12,
        "tool_call_cap": 12,
        "max_artifacts": 6,
        "artifact_cap": 6,

        # Token / character style limits.
        "max_tokens": 6000,
        "token_budget": 6000,
        "max_prompt_chars": 48000,
        "max_context_chars": 90000,
        "context_char_budget": 90000,

        # Generic estimated budget used by older planners.
        "estimated_budget": 12,
        "budget": 12,
    }
    fallback_values.update(preferred)

    try:
        params = signature(BudgetLimits).parameters
    except Exception:
        return BudgetLimits()  # type: ignore[call-arg]

    kwargs: dict[str, Any] = {}
    for name, param in params.items():
        if name == "self":
            continue
        if param.kind not in {
            param.POSITIONAL_OR_KEYWORD,
            param.KEYWORD_ONLY,
        }:
            continue
        if name in fallback_values:
            kwargs[name] = fallback_values[name]
        elif param.default is param.empty:
            # Conservative integer fallback for required numeric fields.
            kwargs[name] = 8

    try:
        return BudgetLimits(**kwargs)
    except TypeError:
        return BudgetLimits()  # type: ignore[call-arg]


SKILLSBENCH_PROFILE = TrackProfile(
    name="skillsbench",
    default_prompt="skillsbench_generalist_artifact_first",
    budget_limits=_budget_limits(
        max_steps=10,
        max_plan_steps=10,
        max_llm_calls=4,
        max_tool_calls=12,
        max_artifacts=6,
        max_tokens=6000,
        max_prompt_chars=48000,
        max_context_chars=90000,
        context_char_budget=90000,
    ),
    self_check_policy="artifact_first_strict",
    routing_bias=(
        "skillsbench_general_purpose_artifact_first "
        "classify_task_id_category_tags_then_emit_file_deliverables "
        "prefer_minimal_valid_artifact_over_prose_only "
        "preserve_cybergym_single_poc_contract_and_maizebargain_baseline"
    ),
)
