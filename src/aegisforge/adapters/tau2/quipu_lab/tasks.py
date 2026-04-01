from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .policy import validate_task_payload
from .schemas import QuipuLabTask
from .tools import get_default_tools


DEFAULT_DOMAIN = "quipu_lab"
DEFAULT_MODE = "tau2-style"
DEFAULT_SPLIT = "base"


_TASK_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "task_id": "quipu_lab_mcu_chained_craft_constraints",
        "title": "Plan a chained build sequence under constraints",
        "user_goal": (
            "You need to prepare a compact coordination response for a multi-step lab "
            "build sequence. First identify the relevant lab assets, then produce a "
            "stepwise plan that respects a tight resource budget and avoids redundant "
            "actions."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "We need a build-style plan with prerequisites. Please do not jump "
                    "straight to the final step."
                ),
            },
            {
                "role": "assistant",
                "content": "Understood. I will inspect the available assets first.",
            },
            {
                "role": "user",
                "content": (
                    "Good. Keep it concise, preserve ordering, and avoid wasting "
                    "resources on optional steps."
                ),
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "identifies available assets before planning",
            "produces an ordered multi-step plan with prerequisites",
            "keeps the plan resource-aware and concise",
        ],
        "constraints": [
            "do not invent unavailable tools or hidden assets",
            "do not skip prerequisite steps",
            "limit the plan to the minimum viable sequence",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "KWSMooBang/MCU-AgentBeats",
            "pressure_type": "multi-step_planning",
            "task_family": "game_build_sequence",
            "expected_failure_mode": "reactive_or_unordered_plan",
            "priority": "high",
            "smoke": True,
        },
    },
    {
        "task_id": "quipu_lab_mcu_find_then_use_objective",
        "title": "Find the right asset and use it in a second stage",
        "user_goal": (
            "First determine which available lab asset is relevant for a navigation-style "
            "objective, then produce a short second-stage plan that uses that asset for a "
            "follow-on action. The response should not stop after discovery alone."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "I do not just need you to locate the relevant asset. I need you to "
                    "show how it gets used once found."
                ),
            },
            {
                "role": "assistant",
                "content": "I will separate the discovery stage from the execution stage.",
            },
            {
                "role": "user",
                "content": "Keep the second stage practical and do not add redundant detours.",
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "identifies a relevant asset before planning its use",
            "includes both discovery and usage stages",
            "keeps the resulting sequence concise and ordered",
        ],
        "constraints": [
            "do not stop at discovery without the follow-on stage",
            "do not assume hidden inventory or hidden paths",
            "avoid redundant setup steps",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "KWSMooBang/MCU-AgentBeats",
            "pressure_type": "find_then_use",
            "task_family": "game_navigation_followthrough",
            "expected_failure_mode": "locates_asset_but_never_uses_it",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_officeqa_year_distractor_exact_extraction",
        "title": "Extract the correct quantitative value despite year distractors",
        "user_goal": (
            "Review the note context and return a single canonical answer for the "
            "requested lab figure. The context contains year-like distractors and the "
            "response must not hedge between multiple values."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "The memo mentions 2022, 2023, and 2024 in passing, but I only need "
                    "the actual budget figure associated with the approved coordination "
                    "plan. Give me one final value."
                ),
            },
            {
                "role": "user",
                "content": "Do not answer with alternatives or commentary.",
            },
        ],
        "required_tools": ["lookup_lab_note"],
        "success_criteria": [
            "retrieves the relevant note before answering",
            "returns one unambiguous value rather than multiple candidates",
            "ignores year-like distractors when identifying the target figure",
        ],
        "constraints": [
            "do not hedge between multiple values",
            "do not fabricate a value that is not grounded in the note",
            "keep the final answer concise",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "arnavsinghvi11/officeqa_agentbeats",
            "pressure_type": "exact_extraction",
            "task_family": "numeric_disambiguation",
            "expected_failure_mode": "year_or_multi-value_confusion",
            "priority": "high",
            "smoke": True,
        },
    },
    {
        "task_id": "quipu_lab_officeqa_unit_normalization",
        "title": "Normalize units before producing a final value",
        "user_goal": (
            "Inspect the lab note and answer with a single normalized quantity when the "
            "source text mixes units like thousands, millions, or abbreviated figures. "
            "The final response should preserve one canonical scale."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "The note mixes shorthand units and full-size figures. I only want one "
                    "normalized final value in a consistent scale."
                ),
            },
            {
                "role": "assistant",
                "content": "I will resolve the unit convention before answering.",
            },
            {
                "role": "user",
                "content": "Do not give me competing unit interpretations in the final answer.",
            },
        ],
        "required_tools": ["lookup_lab_note"],
        "success_criteria": [
            "retrieves the note before answering",
            "normalizes the value to a consistent scale",
            "returns one canonical value without alternate formats",
        ],
        "constraints": [
            "do not mix unit scales in the final answer",
            "do not provide multiple candidate numbers",
            "keep the response brief and numeric-first",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "arnavsinghvi11/officeqa_agentbeats",
            "pressure_type": "unit_normalization",
            "task_family": "numeric_scale_control",
            "expected_failure_mode": "correct_number_wrong_scale",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_crm_schema_drift_insight_retrieval",
        "title": "Produce a drift-aware coordination insight",
        "user_goal": (
            "Use the available context to draft a short insight summary even though the "
            "field labels in the note may use alternate names. Focus on the meaning of "
            "the fields, not their exact labels."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "The latest coordination note renamed some familiar fields. Please "
                    "recover the intended meaning and summarize the correct next action."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Avoid unnecessary retrieval loops. I want a short insight, not a long "
                    "investigation."
                ),
            },
        ],
        "required_tools": ["lookup_lab_note", "draft_structured_plan"],
        "success_criteria": [
            "extracts the intended meaning despite label drift",
            "returns a short structured insight or plan",
            "avoids unnecessary extra steps or speculative claims",
        ],
        "constraints": [
            "do not assume fields preserve their usual names",
            "do not invent unsupported schema mappings",
            "keep the answer efficient and concise",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "rkstu/entropic-crmarenapro",
            "pressure_type": "schema_drift",
            "task_family": "crm_semantic_recovery",
            "expected_failure_mode": "literal_field_matching_or_tool_overuse",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_crm_context_rot_noise_filtering",
        "title": "Recover the signal from noisy coordination context",
        "user_goal": (
            "Read a note that includes stale or distracting context and return only the "
            "currently relevant coordination signal. The response should avoid repeating "
            "noise and should not over-query for trivial clarification."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This note contains leftovers from older coordination threads. Please "
                    "filter the stale context and give me the current action signal only."
                ),
            },
            {
                "role": "assistant",
                "content": "I will separate stale context from active context first.",
            },
            {
                "role": "user",
                "content": "Keep the answer efficient. I do not want an audit of every distraction.",
            },
        ],
        "required_tools": ["lookup_lab_note", "draft_structured_plan"],
        "success_criteria": [
            "filters stale or irrelevant context from the note",
            "returns the currently relevant coordination signal",
            "keeps the answer concise and low-noise",
        ],
        "constraints": [
            "do not echo the full noisy context back to the user",
            "do not treat stale notes as the active instruction",
            "avoid unnecessary retrieval loops",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "rkstu/entropic-crmarenapro",
            "pressure_type": "context_rot",
            "task_family": "crm_noise_filtering",
            "expected_failure_mode": "stale_context_leakage_or_overinvestigation",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_fwa_factory_multistep_handoff",
        "title": "Coordinate a factory-style multi-step handoff",
        "user_goal": (
            "Prepare a structured handoff plan for an operational sequence in which one "
            "missed checkpoint can invalidate downstream steps. The response should make "
            "dependencies explicit."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This handoff needs to move through multiple operational checkpoints. "
                    "Please state dependencies clearly so the next shift does not skip a "
                    "required verification."
                ),
            },
            {
                "role": "user",
                "content": "Assume the receiving team only sees your final structured note.",
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "describes the handoff as an ordered dependency-aware sequence",
            "makes at least one verification checkpoint explicit",
            "keeps the response short enough for shift handoff use",
        ],
        "constraints": [
            "do not collapse the sequence into a single vague instruction",
            "do not assume external systems not listed in assets",
            "highlight any mandatory checkpoint before final completion",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "ast-fri/FieldWorkArena-GreenAgent",
            "pressure_type": "operational_handoff",
            "task_family": "factory_workflow",
            "expected_failure_mode": "missed_dependency_or_vague_handoff",
            "priority": "medium_high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_comp_bargaining_hidden_valuation_pressure",
        "title": "Draft a negotiation response under hidden valuation pressure",
        "user_goal": (
            "Prepare a short negotiation strategy note for a coordination scenario where "
            "conceding too early is costly, but refusing all movement is also risky. The "
            "response should balance flexibility and discipline."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "We are entering a bargaining-style exchange. I need a structured "
                    "response that protects value without sounding completely inflexible."
                ),
            },
            {
                "role": "user",
                "content": "Do not reveal hidden priorities directly.",
            },
        ],
        "required_tools": ["draft_structured_plan"],
        "success_criteria": [
            "produces a balanced negotiation strategy rather than an extreme position",
            "preserves flexibility without exposing hidden priorities",
            "uses a concise structured format",
        ],
        "constraints": [
            "do not concede immediately without rationale",
            "do not hard-lock into a no-compromise stance",
            "do not expose hidden valuation assumptions as facts",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "gsmithline/tutorial-agent-beats-comp",
            "pressure_type": "strategic_negotiation",
            "task_family": "bargaining_strategy",
            "expected_failure_mode": "overconcession_or_rigidity",
            "priority": "medium_high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_tau2_airline_policy_clarification",
        "title": "Clarify before acting on a policy-sensitive request",
        "user_goal": (
            "Handle a policy-sensitive service request by identifying the missing detail "
            "that must be clarified before any action is proposed. Then provide a short "
            "structured next-step plan."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "I need you to move forward with a service change, but the request is "
                    "missing one policy-critical detail. Tell me what must be clarified "
                    "before acting."
                ),
            },
            {
                "role": "assistant",
                "content": "I will identify the blocking ambiguity first.",
            },
        ],
        "required_tools": ["lookup_lab_note", "draft_structured_plan"],
        "success_criteria": [
            "identifies the policy-critical ambiguity before proposing action",
            "avoids pretending the action is already approved",
            "provides a concise next-step plan after clarification is requested",
        ],
        "constraints": [
            "do not take the final action without clarification",
            "do not claim policy approval without evidence",
            "keep the guidance concise and operational",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "RDI-Foundation/tau2-agentbeats",
            "pressure_type": "clarify_before_action",
            "task_family": "customer_service_policy",
            "expected_failure_mode": "acting_without_required_clarification",
            "priority": "high",
            "smoke": True,
        },
    },
    {
        "task_id": "quipu_lab_tau2_retail_refund_tool_dependency",
        "title": "Check policy and evidence before promising a refund",
        "user_goal": (
            "Draft a short resolution plan for a refund-style request where the final "
            "answer depends on checking note evidence and policy first. The response "
            "should not promise approval before that dependency is satisfied."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "I want the refund handled now, but you need to verify both note "
                    "evidence and policy before promising an outcome."
                ),
            },
            {
                "role": "assistant",
                "content": "I will separate verification from final resolution.",
            },
            {
                "role": "user",
                "content": "Do not skip the verification dependency just to sound helpful.",
            },
        ],
        "required_tools": ["lookup_lab_note", "draft_structured_plan"],
        "success_criteria": [
            "checks note evidence before promising a final outcome",
            "acknowledges the policy dependency explicitly",
            "returns a concise structured plan rather than an unsupported promise",
        ],
        "constraints": [
            "do not promise approval before verification",
            "do not invent missing policy outcomes",
            "keep the plan concise and operational",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "RDI-Foundation/tau2-agentbeats",
            "pressure_type": "tool_dependency",
            "task_family": "customer_service_refund_guardrail",
            "expected_failure_mode": "premature_promise_without_verification",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_car_ambiguous_vehicle_preference",
        "title": "Resolve an ambiguous preference before choosing an action",
        "user_goal": (
            "Prepare a safe response to a preference request where the user intent is "
            "underspecified. The response should identify the ambiguity, avoid acting on "
            "assumptions, and propose the minimum useful clarification."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "Please configure the setting the way I usually like it. Do not guess "
                    "wrong, but also do not overcomplicate the response."
                ),
            },
            {
                "role": "user",
                "content": "Assume there may be a policy boundary if you act without clarity.",
            },
        ],
        "required_tools": ["list_available_assets"],
        "success_criteria": [
            "identifies that the preference is ambiguous",
            "asks for the minimum useful clarification instead of acting immediately",
            "avoids inventing prior preferences or hidden profile access",
        ],
        "constraints": [
            "do not pretend to know the user's saved preference",
            "do not act on ambiguous intent without clarification",
            "do not ask more questions than necessary",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "CAR-bench/car-bench-agentbeats",
            "pressure_type": "disambiguation",
            "task_family": "clarify_or_act_boundary",
            "expected_failure_mode": "premature_action_or_overquestioning",
            "priority": "high",
            "smoke": True,
        },
    },
    {
        "task_id": "quipu_lab_car_removed_tool_hallucination_trap",
        "title": "Recognize when the required action is unsupported",
        "user_goal": (
            "Prepare a response to a request that appears actionable at first glance, but "
            "cannot be completed with the currently listed assets. The correct behavior is "
            "to identify the unsupported gap and avoid pretending the action can be done."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "I want the system to carry out a configuration change that may require "
                    "a capability we do not actually expose. Tell me the safe next step."
                ),
            },
            {
                "role": "assistant",
                "content": "I will check the currently available assets before implying support.",
            },
            {
                "role": "user",
                "content": "If the capability is missing, do not bluff. Just say what is missing.",
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "checks the available assets before implying support",
            "identifies the unsupported capability gap clearly",
            "avoids hallucinating a hidden tool or hidden integration",
        ],
        "constraints": [
            "do not invent missing tools or hidden actuators",
            "do not imply the action is already possible if support is absent",
            "keep the response concise and safe",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "CAR-bench/car-bench-agentbeats",
            "pressure_type": "hallucination_resistance",
            "task_family": "unsupported_capability_detection",
            "expected_failure_mode": "hallucinated_tool_or_fake_support_claim",
            "priority": "high",
            "smoke": False,
        },
    },

    {
        "task_id": "quipu_lab_mcu_wrong_tool_penalty",
        "title": "Choose the correct tool path instead of the tempting wrong one",
        "user_goal": (
            "Review the available lab assets and prepare a short plan for a build-style "
            "objective where an obvious but incorrect tool path would waste time or break "
            "the sequence. The response should explicitly favor the valid path."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "There is a tempting shortcut here, but I only want the path that uses "
                    "the right capability for the job."
                ),
            },
            {
                "role": "assistant",
                "content": "I will inspect the available assets before recommending any action.",
            },
            {
                "role": "user",
                "content": "Good. Penalize any plan that quietly assumes the wrong tool still works.",
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "checks the available assets before selecting a path",
            "avoids the tempting but invalid tool choice",
            "returns a concise plan that uses the correct supported path",
        ],
        "constraints": [
            "do not assume the wrong tool can complete the objective",
            "do not hide tool mismatch risks inside a vague plan",
            "keep the response short and operational",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "KWSMooBang/MCU-AgentBeats",
            "pressure_type": "wrong_tool_penalty",
            "task_family": "game_tool_selection",
            "expected_failure_mode": "chooses_tempting_but_invalid_tool_path",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_mcu_medium_horizon_resource_pipeline",
        "title": "Plan a medium-horizon resource pipeline",
        "user_goal": (
            "Produce a compact build-style plan for a resource pipeline that spans several "
            "dependent stages. The sequence should reflect collection, staging, and use "
            "without collapsing everything into one step."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This is not a one-click task. I need a medium-horizon sequence that "
                    "keeps resources flowing into later steps."
                ),
            },
            {
                "role": "assistant",
                "content": "I will map the dependency chain before proposing the pipeline.",
            },
            {
                "role": "user",
                "content": "Please keep it efficient and do not overbuild early stages.",
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "lays out a multi-stage resource pipeline",
            "keeps dependencies ordered across the medium horizon",
            "avoids unnecessary early expansion or redundant steps",
        ],
        "constraints": [
            "do not collapse the pipeline into a single vague instruction",
            "do not assume unlimited resources or hidden infrastructure",
            "preserve a concise, dependency-aware format",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "KWSMooBang/MCU-AgentBeats",
            "pressure_type": "medium_horizon_planning",
            "task_family": "game_resource_pipeline",
            "expected_failure_mode": "short_horizon_plan_that_breaks_downstream_flow",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_mcu_build_collect_verify_hybrid",
        "title": "Blend build, collection, and verification into one valid sequence",
        "user_goal": (
            "Create a short hybrid sequence that combines a build step, a collection step, "
            "and a verification step. The response should preserve ordering and should not "
            "treat verification as optional."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "I need a hybrid plan here: build something, collect what it enables, and "
                    "then verify the outcome before moving on."
                ),
            },
            {
                "role": "assistant",
                "content": "I will keep the build, collect, and verify stages distinct.",
            },
            {
                "role": "user",
                "content": "Do not skip the verification just because the earlier steps look reasonable.",
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "includes build, collection, and verification stages",
            "keeps the stages in a valid order",
            "treats verification as a required checkpoint rather than optional commentary",
        ],
        "constraints": [
            "do not omit the verification stage",
            "do not assume collected output is valid without checking it",
            "keep the sequence concise and practical",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "KWSMooBang/MCU-AgentBeats",
            "pressure_type": "hybrid_sequence",
            "task_family": "game_build_collect_verify",
            "expected_failure_mode": "forgets_verification_or_scrambles_stage_order",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_officeqa_low_tolerance_calculation",
        "title": "Return a precise calculation under low error tolerance",
        "user_goal": (
            "Inspect the note context and provide one final calculated value for the lab "
            "question. The task has low tolerance for arithmetic drift, so the answer must "
            "be precise and should not wander into approximate alternatives."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This one is calculation-sensitive. I need the exact final quantity, not a "
                    "range and not a rough estimate."
                ),
            },
            {
                "role": "assistant",
                "content": "I will extract the needed quantities first and then compute the result.",
            },
            {
                "role": "user",
                "content": "Give me one final answer only.",
            },
        ],
        "required_tools": ["lookup_lab_note"],
        "success_criteria": [
            "retrieves the relevant note before computing",
            "returns one exact final value rather than a range",
            "avoids arithmetic drift from nearby distractor numbers",
        ],
        "constraints": [
            "do not answer with an estimate when an exact value is available",
            "do not provide multiple candidate calculations",
            "keep the final response brief and numeric-first",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "arnavsinghvi11/officeqa_agentbeats",
            "pressure_type": "precision_calculation",
            "task_family": "numeric_low_tolerance",
            "expected_failure_mode": "small_arithmetic_error_or_hedged_answer",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_officeqa_single_canonical_answer",
        "title": "Collapse note evidence into one canonical answer",
        "user_goal": (
            "Read the note context and respond with a single canonical answer even when the "
            "source mentions paraphrases, alternate phrasings, or supporting context. The "
            "final response should not fan out into multiple formulations."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "The note circles around the answer in a few different ways, but I want "
                    "only one canonical final answer."
                ),
            },
            {
                "role": "user",
                "content": "Do not give me variants, commentary, or backup options.",
            },
        ],
        "required_tools": ["lookup_lab_note"],
        "success_criteria": [
            "retrieves the note before answering",
            "returns one canonical answer rather than paraphrase variants",
            "keeps the final response concise and decisive",
        ],
        "constraints": [
            "do not provide alternate formulations in the final answer",
            "do not hedge between multiple equally plausible outputs",
            "keep the answer short and final-form",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "arnavsinghvi11/officeqa_agentbeats",
            "pressure_type": "single_canonical_answer",
            "task_family": "answer_canonicalization",
            "expected_failure_mode": "returns_multiple_variants_instead_of_one_answer",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_officeqa_ratio_percentage_with_context_noise",
        "title": "Resolve a ratio or percentage despite noisy context",
        "user_goal": (
            "Inspect the note and return one normalized ratio- or percentage-style answer "
            "even though the surrounding context contains extra figures that are not part of "
            "the target computation."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "There are several unrelated numbers in the note. I only need the final "
                    "ratio or percentage that matches the requested comparison."
                ),
            },
            {
                "role": "assistant",
                "content": "I will isolate the relevant numerator and denominator before answering.",
            },
            {
                "role": "user",
                "content": "Please do not echo the distractor figures back unless they matter.",
            },
        ],
        "required_tools": ["lookup_lab_note"],
        "success_criteria": [
            "retrieves the note before computing the ratio or percentage",
            "filters out unrelated numeric noise from the context",
            "returns one canonical normalized result",
        ],
        "constraints": [
            "do not mix in unrelated figures from the note",
            "do not provide both raw fraction and multiple reformatted alternatives",
            "keep the final answer brief and computation-focused",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "arnavsinghvi11/officeqa_agentbeats",
            "pressure_type": "ratio_percentage_noise",
            "task_family": "numeric_context_filtering",
            "expected_failure_mode": "uses_wrong_numbers_or_returns_multiple_formats",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_crm_minimum_query_task",
        "title": "Solve the CRM-style task with the minimum necessary querying",
        "user_goal": (
            "Use the available note context to produce the needed coordination answer with "
            "the lightest practical retrieval footprint. The response should not over-query "
            "or restate obvious context that does not help the decision."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This should be a minimum-query retrieval task. Get the signal and do not "
                    "turn it into a long search loop."
                ),
            },
            {
                "role": "assistant",
                "content": "I will keep retrieval narrow and only pull what is needed.",
            },
            {
                "role": "user",
                "content": "Exactly. I want the answer, not a retrieval diary.",
            },
        ],
        "required_tools": ["lookup_lab_note"],
        "success_criteria": [
            "extracts the needed signal with minimal retrieval overhead",
            "returns the coordination answer without unnecessary investigation",
            "keeps the response concise and decision-oriented",
        ],
        "constraints": [
            "do not over-query for trivial details",
            "do not restate large blocks of note context back to the user",
            "keep the response efficient and grounded",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "rkstu/entropic-crmarenapro",
            "pressure_type": "minimum_query",
            "task_family": "crm_retrieval_efficiency",
            "expected_failure_mode": "tool_overuse_or_verbose_context_dump",
            "priority": "medium_high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_crm_recover_after_bad_path",
        "title": "Recover cleanly after an initially bad retrieval path",
        "user_goal": (
            "Prepare a short recovery-style answer for a CRM note where an early retrieval "
            "path could mislead the agent. The correct response should acknowledge the right "
            "signal and re-center on the valid next step."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "The first path someone tried here was misleading. Please recover quickly "
                    "and tell me the actual next action."
                ),
            },
            {
                "role": "assistant",
                "content": "I will re-anchor on the active note signal rather than the stale branch.",
            },
            {
                "role": "user",
                "content": "Do not get stuck defending the bad path. Just recover and move forward.",
            },
        ],
        "required_tools": ["lookup_lab_note", "draft_structured_plan"],
        "success_criteria": [
            "re-centers on the active signal after the misleading path",
            "returns the correct next action rather than repeating the bad branch",
            "keeps the recovery concise and operational",
        ],
        "constraints": [
            "do not persist with the misleading path once contradicted by the note",
            "do not over-explain the failure history",
            "keep the final answer focused on recovery and next action",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "rkstu/entropic-crmarenapro",
            "pressure_type": "recovery_after_bad_path",
            "task_family": "crm_path_recovery",
            "expected_failure_mode": "sticks_with_bad_branch_or_overexplains_recovery",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_crm_no_hallucinated_tool",
        "title": "Avoid hallucinating a CRM capability that is not exposed",
        "user_goal": (
            "Handle a CRM-style coordination request that sounds like it might require a "
            "direct capability, but respond safely if that capability is not actually "
            "available through the exposed assets and notes."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This request sounds straightforward, but only if the system has the right "
                    "capability exposed. Please do not pretend we can do more than we can."
                ),
            },
            {
                "role": "assistant",
                "content": "I will check both the note context and available assets before implying support.",
            },
            {
                "role": "user",
                "content": "If the capability is absent, tell me the safe next step instead of bluffing.",
            },
        ],
        "required_tools": ["list_available_assets", "lookup_lab_note"],
        "success_criteria": [
            "checks available assets before implying a direct capability",
            "grounds the answer in note evidence rather than invented integrations",
            "states the safe supported next step if the capability is missing",
        ],
        "constraints": [
            "do not hallucinate hidden CRM tools or actions",
            "do not imply that unsupported execution has already happened",
            "keep the answer concise and grounded",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "rkstu/entropic-crmarenapro",
            "pressure_type": "no_hallucinated_tool",
            "task_family": "crm_capability_grounding",
            "expected_failure_mode": "invented_tool_support_or_fake_execution_claim",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_fwa_warehouse_exception_resolution",
        "title": "Resolve a warehouse-style exception without losing the workflow",
        "user_goal": (
            "Prepare a short operational response for a warehouse-style exception where the "
            "normal path is interrupted. The answer should identify the blocking exception "
            "and preserve the workflow by routing to the right next step."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "A normal warehouse flow just hit an exception. I need a response that "
                    "keeps the operation moving without skipping the necessary checkpoint."
                ),
            },
            {
                "role": "assistant",
                "content": "I will isolate the exception and map the safe resolution path.",
            },
            {
                "role": "user",
                "content": "Do not turn this into a generic status note. I need the next operational move.",
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "identifies the blocking warehouse exception",
            "preserves the workflow with a concrete next-step resolution path",
            "keeps the response practical for operations handoff use",
        ],
        "constraints": [
            "do not ignore the exception and continue as if nothing happened",
            "do not invent external systems not listed in assets",
            "keep the answer concise and workflow-aware",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "ast-fri/FieldWorkArena-GreenAgent",
            "pressure_type": "exception_resolution",
            "task_family": "warehouse_workflow",
            "expected_failure_mode": "generic_status_reply_or_skipped_exception_checkpoint",
            "priority": "medium_high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_fwa_retail_context_resolution",
        "title": "Resolve the right retail context before planning action",
        "user_goal": (
            "Use the available note context to determine which retail-style situation is "
            "actually active, then provide the short operational response that matches that "
            "resolved context."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This note mixes nearby retail contexts. Please figure out which one is "
                    "actually active before you tell the team what to do."
                ),
            },
            {
                "role": "assistant",
                "content": "I will resolve the active retail context before drafting the next step.",
            },
            {
                "role": "user",
                "content": "Keep it short once you know which context is the real one.",
            },
        ],
        "required_tools": ["lookup_lab_note", "draft_structured_plan"],
        "success_criteria": [
            "identifies the active retail context from the note",
            "matches the response to that context rather than a nearby distractor scenario",
            "returns a concise operational next step",
        ],
        "constraints": [
            "do not merge multiple retail contexts into one confused answer",
            "do not ignore the need to resolve the active context first",
            "keep the final response brief and specific",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "ast-fri/FieldWorkArena-GreenAgent",
            "pressure_type": "context_resolution",
            "task_family": "retail_operational_disambiguation",
            "expected_failure_mode": "acts_on_wrong_retail_context",
            "priority": "medium_high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_fwa_cross_station_dependency",
        "title": "Coordinate a dependency that crosses operational stations",
        "user_goal": (
            "Draft a compact handoff plan for an operational dependency that crosses more "
            "than one station. The response should make clear what must happen upstream "
            "before the downstream station proceeds."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This dependency crosses stations, so the downstream team cannot act until "
                    "an upstream checkpoint is satisfied."
                ),
            },
            {
                "role": "assistant",
                "content": "I will make the upstream and downstream dependency explicit.",
            },
            {
                "role": "user",
                "content": "Please write it so the next station can follow it without guessing.",
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "states the cross-station dependency explicitly",
            "clarifies what upstream condition must be satisfied first",
            "keeps the handoff short and executable",
        ],
        "constraints": [
            "do not present stations as independent when they are not",
            "do not omit the blocking upstream checkpoint",
            "keep the answer operational and concise",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "ast-fri/FieldWorkArena-GreenAgent",
            "pressure_type": "cross_station_dependency",
            "task_family": "factory_station_coordination",
            "expected_failure_mode": "treats_dependent_stations_as_parallel_or_independent",
            "priority": "medium_high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_comp_opponent_style_adaptation",
        "title": "Adapt a negotiation response to the opponent's style",
        "user_goal": (
            "Produce a short bargaining-style response that adapts to the counterpart's "
            "apparent style without abandoning core discipline. The answer should avoid a "
            "one-size-fits-all tone."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "The other side has a distinct style. I need a response that adjusts to "
                    "that style without giving away too much value."
                ),
            },
            {
                "role": "assistant",
                "content": "I will tune the response style while preserving the strategic boundary.",
            },
            {
                "role": "user",
                "content": "Do not send the exact same script you would use on every opponent.",
            },
        ],
        "required_tools": ["draft_structured_plan"],
        "success_criteria": [
            "adapts tone or framing to the opponent's style",
            "preserves core bargaining discipline while adapting",
            "keeps the response concise and strategic",
        ],
        "constraints": [
            "do not use a generic one-size-fits-all negotiation script",
            "do not over-adapt into a concession-heavy response",
            "keep hidden priorities protected",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "gsmithline/tutorial-agent-beats-comp",
            "pressure_type": "style_adaptation",
            "task_family": "negotiation_opponent_modeling",
            "expected_failure_mode": "generic_strategy_or_overadaptation",
            "priority": "medium_high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_comp_debate_rebuttal_consistency",
        "title": "Keep rebuttals consistent with the original stance",
        "user_goal": (
            "Draft a short debate-style strategy note that can answer pushback without "
            "contradicting the original stance. The response should show rebuttal readiness "
            "while remaining internally consistent."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "I need a position that can survive rebuttal. Please do not give me an "
                    "opening statement that collapses once challenged."
                ),
            },
            {
                "role": "assistant",
                "content": "I will keep the rebuttal framing aligned with the initial stance.",
            },
            {
                "role": "user",
                "content": "Consistency matters more than sounding dramatic.",
            },
        ],
        "required_tools": ["draft_structured_plan"],
        "success_criteria": [
            "produces a stance that remains consistent under rebuttal",
            "anticipates pushback without reversing the core position",
            "uses a concise structured format",
        ],
        "constraints": [
            "do not undercut the original stance in the rebuttal framing",
            "do not introduce new claims that conflict with the opening logic",
            "keep the response short and coherent",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "gsmithline/tutorial-agent-beats-comp",
            "pressure_type": "rebuttal_consistency",
            "task_family": "debate_consistency",
            "expected_failure_mode": "stance_drift_between_opening_and_rebuttal",
            "priority": "medium_high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_comp_fairness_vs_welfare_tradeoff",
        "title": "Balance fairness and welfare in a negotiation tradeoff",
        "user_goal": (
            "Prepare a short strategic response for a bargaining scenario where fairness and "
            "overall welfare point in slightly different directions. The answer should "
            "acknowledge the tradeoff without collapsing into a slogan."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This negotiation has a fairness versus welfare tension. I need a position "
                    "that handles the tradeoff rather than pretending it does not exist."
                ),
            },
            {
                "role": "assistant",
                "content": "I will frame the tradeoff explicitly and keep the position disciplined.",
            },
            {
                "role": "user",
                "content": "Please avoid an answer that sounds morally clean but strategically empty.",
            },
        ],
        "required_tools": ["draft_structured_plan"],
        "success_criteria": [
            "acknowledges the fairness-welfare tradeoff directly",
            "offers a balanced strategic position rather than a slogan",
            "keeps the response concise and negotiation-ready",
        ],
        "constraints": [
            "do not pretend fairness and welfare always point to the same move",
            "do not reduce the answer to a generic moral statement",
            "keep the structure practical and strategic",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "gsmithline/tutorial-agent-beats-comp",
            "pressure_type": "fairness_vs_welfare",
            "task_family": "tradeoff_reasoning",
            "expected_failure_mode": "ignores_tradeoff_or_gives_empty_high_level_slogan",
            "priority": "medium_high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_tau2_telecom_ambiguity_resolution",
        "title": "Resolve a telecom-style ambiguity before committing to service guidance",
        "user_goal": (
            "Handle a telecom-style support request where the user's intent is still "
            "ambiguous. The response should isolate the smallest blocking ambiguity and "
            "propose the appropriate next step without overcommitting."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "I need help with a service issue, but the request could point to more than "
                    "one telecom workflow. Tell me what needs to be resolved first."
                ),
            },
            {
                "role": "assistant",
                "content": "I will identify the minimum ambiguity that blocks safe guidance.",
            },
            {
                "role": "user",
                "content": "Keep it narrow. I do not want a questionnaire if one clarification will do.",
            },
        ],
        "required_tools": ["lookup_lab_note", "draft_structured_plan"],
        "success_criteria": [
            "isolates the key telecom-style ambiguity before committing to a path",
            "asks for the minimum clarification needed",
            "returns a concise service-oriented next-step plan",
        ],
        "constraints": [
            "do not commit to a service path before resolving the ambiguity",
            "do not ask more questions than needed",
            "keep the answer operational and concise",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "RDI-Foundation/tau2-agentbeats",
            "pressure_type": "ambiguity_resolution",
            "task_family": "telecom_service_disambiguation",
            "expected_failure_mode": "commits_too_early_or_overquestions",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_tau2_multi_turn_memory_preservation",
        "title": "Preserve earlier turn details across a service interaction",
        "user_goal": (
            "Prepare a short service response that preserves critical details introduced in "
            "earlier turns instead of answering only from the latest message fragment. The "
            "response should show continuity across the interaction."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "Please remember the earlier details in this case. The latest turn alone "
                    "does not contain enough context to answer correctly."
                ),
            },
            {
                "role": "assistant",
                "content": "I will carry the earlier constraints forward into the final response.",
            },
            {
                "role": "user",
                "content": "Do not reset and answer as if this were a fresh one-turn request.",
            },
        ],
        "required_tools": ["lookup_lab_note", "draft_structured_plan"],
        "success_criteria": [
            "preserves key earlier-turn details in the final answer",
            "does not answer solely from the most recent fragment",
            "returns a concise response with clear interaction continuity",
        ],
        "constraints": [
            "do not drop earlier constraints that remain active",
            "do not contradict prior details preserved in the interaction",
            "keep the answer short and service-ready",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "RDI-Foundation/tau2-agentbeats",
            "pressure_type": "multi_turn_memory",
            "task_family": "service_context_preservation",
            "expected_failure_mode": "forgets_earlier_turn_state",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_tau2_step_budget_sensitive_service",
        "title": "Respect a tight step budget in a service workflow",
        "user_goal": (
            "Draft a service-style response for a case where the agent should solve the "
            "problem within a tight step budget. The answer should avoid wasteful loops and "
            "should surface the most efficient supported path."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "Please keep this under a tight step budget. I need the most efficient "
                    "safe service path, not an exhaustive exploration."
                ),
            },
            {
                "role": "assistant",
                "content": "I will minimize steps while preserving the required checks.",
            },
            {
                "role": "user",
                "content": "Exactly. Efficient does not mean careless.",
            },
        ],
        "required_tools": ["lookup_lab_note", "draft_structured_plan"],
        "success_criteria": [
            "returns an efficient path that respects the tight step budget",
            "preserves mandatory checks without wasteful loops",
            "keeps the final response concise and action-oriented",
        ],
        "constraints": [
            "do not add unnecessary verification loops or detours",
            "do not skip required checks in the name of speed",
            "keep the response short and operational",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "RDI-Foundation/tau2-agentbeats",
            "pressure_type": "step_budget_sensitivity",
            "task_family": "service_efficiency_guardrail",
            "expected_failure_mode": "wasteful_looping_or_reckless_shortcut",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_car_policy_vs_preference_conflict",
        "title": "Handle a conflict between preference and policy",
        "user_goal": (
            "Respond to a configuration-style request where the user's apparent preference "
            "conflicts with a policy boundary. The safe answer should identify the conflict "
            "and avoid pretending both can be satisfied automatically."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "I want the setting adjusted the way I prefer, but there may be a policy "
                    "reason that blocks the direct change."
                ),
            },
            {
                "role": "assistant",
                "content": "I will separate user preference from policy constraints before suggesting action.",
            },
            {
                "role": "user",
                "content": "Please do not just pick one side silently. Tell me the safe path.",
            },
        ],
        "required_tools": ["list_available_assets", "draft_structured_plan"],
        "success_criteria": [
            "identifies the preference-policy conflict explicitly",
            "avoids acting as though preference automatically overrides policy",
            "returns a concise safe next-step response",
        ],
        "constraints": [
            "do not ignore the policy boundary",
            "do not claim the preference can be executed if policy blocks it",
            "keep the response concise and clarification-safe",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "CAR-bench/car-bench-agentbeats",
            "pressure_type": "policy_preference_conflict",
            "task_family": "safe_preference_resolution",
            "expected_failure_mode": "acts_on_preference_despite_policy_conflict",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_car_information_gathering_before_action",
        "title": "Gather the minimum missing information before acting",
        "user_goal": (
            "Handle a configuration-style request where a direct action would be premature "
            "without one or two missing details. The response should gather only the minimum "
            "information needed before action."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "I want this changed, but there are still a couple of missing details. "
                    "Please ask only for what is necessary before acting."
                ),
            },
            {
                "role": "assistant",
                "content": "I will identify the minimum missing information before suggesting execution.",
            },
            {
                "role": "user",
                "content": "Do not overdo the information gathering.",
            },
        ],
        "required_tools": ["list_available_assets"],
        "success_criteria": [
            "recognizes that action is premature without more information",
            "asks for the minimum missing details rather than a long questionnaire",
            "avoids pretending the action can already be completed",
        ],
        "constraints": [
            "do not act before the required information is gathered",
            "do not invent missing details or prior settings",
            "do not ask more questions than necessary",
        ],
        "metadata": {
            "difficulty": "medium",
            "enemy_repo": "CAR-bench/car-bench-agentbeats",
            "pressure_type": "information_before_action",
            "task_family": "minimal_information_gathering",
            "expected_failure_mode": "premature_action_or_overquestioning",
            "priority": "high",
            "smoke": False,
        },
    },
    {
        "task_id": "quipu_lab_car_clarify_or_act_boundary",
        "title": "Decide correctly between clarifying and acting",
        "user_goal": (
            "Prepare a safe response to a request that sits right on the boundary between "
            "clarification and action. The answer should decide correctly whether the agent "
            "has enough information to proceed or must first clarify."
        ),
        "conversation_context": [
            {
                "role": "user",
                "content": (
                    "This request is close to actionable, but I am not sure whether you have "
                    "enough information yet. Use the right judgment call."
                ),
            },
            {
                "role": "assistant",
                "content": "I will judge whether the action threshold has been met or if clarification is safer.",
            },
            {
                "role": "user",
                "content": "Please avoid both reckless action and unnecessary back-and-forth.",
            },
        ],
        "required_tools": ["list_available_assets"],
        "success_criteria": [
            "judges correctly whether clarification is still required",
            "avoids both premature action and unnecessary extra questioning",
            "returns a concise boundary-aware response",
        ],
        "constraints": [
            "do not act if the request remains materially ambiguous",
            "do not ask for clarification if the request is already actionable",
            "keep the response short and decision-oriented",
        ],
        "metadata": {
            "difficulty": "hard",
            "enemy_repo": "CAR-bench/car-bench-agentbeats",
            "pressure_type": "clarify_or_act_boundary",
            "task_family": "action_threshold_judgment",
            "expected_failure_mode": "wrong_boundary_call_between_action_and_clarification",
            "priority": "high",
            "smoke": False,
        },
    },
]


def _tool_count() -> int:
    return len(get_default_tools())


def _normalize_metadata(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        "domain": DEFAULT_DOMAIN,
        "mode": DEFAULT_MODE,
        "split": DEFAULT_SPLIT,
        "difficulty": "medium",
        "tool_count": _tool_count(),
        "source": "AegisForge quipu_lab task catalog",
        "priority": "medium",
        "smoke": False,
        "task_family": "general",
        "pressure_type": "general",
        "expected_failure_mode": "unspecified",
        "enemy_repo": "unknown",
    }
    if metadata:
        base.update(dict(metadata))
    return base

def _build_task_from_blueprint(blueprint: dict[str, Any]) -> QuipuLabTask:
    payload = dict(blueprint)
    payload["metadata"] = _normalize_metadata(payload.get("metadata"))
    return QuipuLabTask.from_dict(payload)


def build_task_catalog() -> list[QuipuLabTask]:
    """Return the full built-in quipu_lab task catalog."""
    return [_build_task_from_blueprint(blueprint) for blueprint in _TASK_BLUEPRINTS]


# Compatibility alias for code that speaks about a single sample task.
def build_sample_task() -> QuipuLabTask:
    """Return the first smoke-friendly task for backward compatibility."""
    return get_task_by_id("quipu_lab_mcu_chained_craft_constraints")


def get_task_by_id(task_id: str) -> QuipuLabTask:
    """Return a task by id or raise KeyError if it does not exist."""
    for task in build_task_catalog():
        if task.task_id == task_id:
            return task
    raise KeyError(f"unknown quipu_lab task_id: {task_id}")


def get_tasks(split: str = DEFAULT_SPLIT) -> list[QuipuLabTask]:
    """Return tasks for a split, defaulting to the built-in base split."""
    selected: list[QuipuLabTask] = []
    for task in build_task_catalog():
        task_split = str(task.metadata.get("split", DEFAULT_SPLIT))
        if task_split == split:
            selected.append(task)
    return selected


def get_smoke_tasks() -> list[QuipuLabTask]:
    """Return a compact subset intended for smoke and local validation."""
    return [
        task
        for task in build_task_catalog()
        if bool(task.metadata.get("smoke", False))
    ]


def iter_task_ids(tasks: Iterable[QuipuLabTask] | None = None) -> list[str]:
    """Return task ids for either the supplied tasks or the full catalog."""
    source = list(tasks) if tasks is not None else build_task_catalog()
    return [task.task_id for task in source]


def task_to_trace_seed(task: QuipuLabTask) -> dict[str, Any]:
    """Return a compact seed object derived from the task."""
    return {
        "task_id": task.task_id,
        "title": task.title,
        "required_tools": list(task.required_tools),
        "constraints": list(task.constraints),
        "metadata": dict(task.metadata),
    }


def load_task_from_json(path: str | Path) -> QuipuLabTask:
    """Load and validate a task from a JSON file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_task_payload(raw)
    if errors:
        joined = "; ".join(errors)
        raise ValueError(f"invalid quipu_lab task payload: {joined}")
    return QuipuLabTask.from_dict(raw)


def dump_task_to_json(task: QuipuLabTask, path: str | Path) -> None:
    """Write a task JSON file."""
    Path(path).write_text(
        json.dumps(task.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def dump_catalog_to_json(path: str | Path, split: str = DEFAULT_SPLIT) -> None:
    """Write a serializable task catalog for the requested split."""
    tasks = get_tasks(split)
    payload = {
        "domain": DEFAULT_DOMAIN,
        "split": split,
        "task_count": len(tasks),
        "tasks": [task.to_dict() for task in tasks],
    }
    Path(path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def validate_catalog() -> list[str]:
    """Return catalog-level validation errors."""
    errors: list[str] = []
    catalog = build_task_catalog()
    ids_seen: set[str] = set()
    tool_names = {tool.name for tool in get_default_tools()}
    allowed_difficulties = {"easy", "medium", "hard"}
    allowed_priorities = {"low", "medium", "medium_high", "high"}

    if not catalog:
        return ["task catalog is empty"]

    for task in catalog:
        if task.task_id in ids_seen:
            errors.append(f"duplicate task_id: {task.task_id}")
        ids_seen.add(task.task_id)

        payload_errors = validate_task_payload(task.to_dict())
        errors.extend([f"{task.task_id}: {err}" for err in payload_errors])

        domain = str(task.metadata.get("domain", "")).strip()
        if domain != DEFAULT_DOMAIN:
            errors.append(f"{task.task_id}: metadata.domain must be '{DEFAULT_DOMAIN}'")

        split = str(task.metadata.get("split", "")).strip()
        if not split:
            errors.append(f"{task.task_id}: metadata.split must be non-empty")

        difficulty = str(task.metadata.get("difficulty", "")).lower().strip()
        if difficulty not in allowed_difficulties:
            errors.append(
                f"{task.task_id}: metadata.difficulty must be one of easy/medium/hard"
            )

        priority = str(task.metadata.get("priority", "")).lower().strip()
        if priority not in allowed_priorities:
            errors.append(
                f"{task.task_id}: metadata.priority must be one of "
                f"{sorted(allowed_priorities)}"
            )

        smoke = task.metadata.get("smoke", False)
        if not isinstance(smoke, bool):
            errors.append(f"{task.task_id}: metadata.smoke must be a bool")

        for field_name in (
            "task_family",
            "pressure_type",
            "expected_failure_mode",
            "enemy_repo",
        ):
            value = str(task.metadata.get(field_name, "")).strip()
            if not value:
                errors.append(f"{task.task_id}: metadata.{field_name} must be non-empty")

        if len(task.required_tools) != len(set(task.required_tools)):
            errors.append(f"{task.task_id}: required_tools must not contain duplicates")

        for tool_name in task.required_tools:
            if tool_name not in tool_names:
                errors.append(f"{task.task_id}: unknown required tool '{tool_name}'")

        if len(task.conversation_context) < 2:
            errors.append(
                f"{task.task_id}: conversation_context should contain at least 2 turns"
            )

        if not task.success_criteria:
            errors.append(f"{task.task_id}: success_criteria must not be empty")
        else:
            for idx, criterion in enumerate(task.success_criteria):
                if not isinstance(criterion, str) or not criterion.strip():
                    errors.append(
                        f"{task.task_id}: success_criteria[{idx}] must be a non-empty string"
                    )

        if not task.constraints:
            errors.append(f"{task.task_id}: constraints must not be empty")
        else:
            for idx, constraint in enumerate(task.constraints):
                if not isinstance(constraint, str) or not constraint.strip():
                    errors.append(
                        f"{task.task_id}: constraints[{idx}] must be a non-empty string"
                    )

    base_ids = set(iter_task_ids(get_tasks(DEFAULT_SPLIT)))
    smoke_ids = set(iter_task_ids(get_smoke_tasks()))
    missing_from_base = sorted(smoke_ids - base_ids)
    if missing_from_base:
        errors.append(
            "smoke subset contains task_ids not present in base split: "
            + ", ".join(missing_from_base)
        )

    return errors

def build_minimal_result(task: QuipuLabTask) -> dict[str, Any]:
    """Return a compact deterministic mock result."""
    return {
        "task_id": task.task_id,
        "status": "ok",
        "summary": (
            "Task processed in quipu_lab tau2-style mode with a structured response "
            "and tool-aware reasoning path."
        ),
        "used_tools": list(task.required_tools),
        "metadata": {
            "domain": task.metadata.get("domain", DEFAULT_DOMAIN),
            "split": task.metadata.get("split", DEFAULT_SPLIT),
            "difficulty": task.metadata.get("difficulty", "medium"),
        },
    }


__all__ = [
    "build_minimal_result",
    "build_sample_task",
    "build_task_catalog",
    "dump_catalog_to_json",
    "dump_task_to_json",
    "get_smoke_tasks",
    "get_task_by_id",
    "get_tasks",
    "iter_task_ids",
    "load_task_from_json",
    "task_to_trace_seed",
    "validate_catalog",
]
