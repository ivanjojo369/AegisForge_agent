from __future__ import annotations

from typing import Any

from config import AegisArenaEnvConfig, DEFAULT_CONFIG
from reward import summarize_reward


BASE_ACTION_COSTS: dict[str, int] = {
    "inspect_context": 1,
    "query_tool": 2,
    "propose_plan": 1,
    "take_action": 3,
    "submit_final": 2,
}

DEFAULT_AVAILABLE_TOOLS: dict[str, list[str]] = {
    "game_ops": [
        "map_probe",
        "inventory_check",
        "simulate_move",
    ],
    "finance_ops": [
        "table_lookup",
        "calc_metric",
        "consistency_check",
    ],
    "business_ops": [
        "ticket_lookup",
        "policy_lookup",
        "route_case",
        "update_stage",
    ],
    "cyber_ops": ["ioc_lookup"],
    "agent_safety": ["policy_lookup"],
    "research_ops": ["paper_lookup"],
    "coding_ops": ["repo_lookup"],
    "general_ops": ["context_lookup"],
}


def action_cost(action: str) -> int:
    return BASE_ACTION_COSTS.get(action, 2)


def count_query_actions(history: list[dict[str, Any]]) -> int:
    return sum(1 for item in history if item.get("action") == "query_tool")


def evaluate_final_answer(
    *,
    mission_type: str,
    hidden_truth: dict[str, Any],
    answer: str | None,
) -> tuple[bool, dict[str, Any]]:
    normalized_answer = (answer or "").strip()
    expected_answer = str(hidden_truth.get("expected_answer", "")).strip()

    if mission_type == "game_ops":
        success = normalized_answer.lower() == (expected_answer or "objective_reached").lower()
        return success, {
            "expected_answer": expected_answer or "objective_reached",
            "normalized_answer": normalized_answer,
        }

    if mission_type == "finance_ops":
        success = normalized_answer.upper() == expected_answer.upper()
        return success, {
            "expected_answer": expected_answer,
            "normalized_answer": normalized_answer,
        }

    if mission_type == "business_ops":
        success = normalized_answer == expected_answer
        return success, {
            "expected_answer": expected_answer,
            "normalized_answer": normalized_answer,
        }

    success = normalized_answer == expected_answer
    return success, {
        "expected_answer": expected_answer,
        "normalized_answer": normalized_answer,
    }


def initial_engine_meta(
    *,
    initial_budget: int,
) -> dict[str, Any]:
    return {
        "initial_budget": initial_budget,
        "query_count": 0,
        "free_queries": 2,
        "invalid_action_count": 0,
        "invalid_tool_count": 0,
    }


def normalize_available_tools(
    *,
    mission_type: str,
    state: dict[str, Any],
) -> list[str]:
    raw_tools = state.get("available_tools")

    if isinstance(raw_tools, (list, tuple, set)):
        normalized = [str(item).strip() for item in raw_tools if str(item).strip()]
        if normalized:
            return normalized

    return list(DEFAULT_AVAILABLE_TOOLS.get(str(mission_type), []))


def mark_invalid_action(
    *,
    meta: dict[str, Any],
    info: dict[str, Any],
    reason: str,
) -> None:
    info["invalid_action"] = True
    info["reason"] = reason
    meta["invalid_action_count"] = int(meta.get("invalid_action_count", 0)) + 1
    info["invalid_action_count_so_far"] = int(meta["invalid_action_count"])


def build_tool_result(
    *,
    mission_type: str,
    tool_name: str,
    payload: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "ok",
        "tool_name": tool_name,
        "mission_type": mission_type,
        "payload_echo": payload,
    }

    if mission_type == "finance_ops":
        if tool_name == "table_lookup":
            result["lookup_kind"] = "finance_table"
            result["requested_row"] = payload.get("row")
        elif tool_name == "calc_metric":
            result["lookup_kind"] = "finance_metric"
            result["metric"] = payload.get("metric")
        elif tool_name == "consistency_check":
            result["lookup_kind"] = "finance_consistency"
            result["checked_scope"] = payload.get("scope", "visible_context")
    elif mission_type == "business_ops":
        if tool_name == "ticket_lookup":
            result["lookup_kind"] = "business_ticket"
            result["requested_field"] = payload.get("field")
        elif tool_name == "policy_lookup":
            result["lookup_kind"] = "business_policy"
            result["policy"] = payload.get("policy", "routing")
        elif tool_name == "route_case":
            result["lookup_kind"] = "business_route"
            result["candidate_route"] = payload.get("route")
        elif tool_name == "update_stage":
            result["lookup_kind"] = "business_stage"
            result["next_stage"] = payload.get("stage")
    elif mission_type == "game_ops":
        if tool_name == "map_probe":
            result["lookup_kind"] = "game_map"
            result["requested_region"] = payload.get("region")
        elif tool_name == "inventory_check":
            result["lookup_kind"] = "game_inventory"
            result["requested_field"] = payload.get("field", "all")
        elif tool_name == "simulate_move":
            result["lookup_kind"] = "game_simulation"
            result["candidate_move"] = payload.get("candidate_move")
    else:
        result["lookup_kind"] = "generic"

    return result


def apply_step_transition(
    *,
    state: dict[str, Any],
    step_request: dict[str, Any],
    config: AegisArenaEnvConfig | None = None,
    engine_meta: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cfg = config or DEFAULT_CONFIG
    meta = dict(
        engine_meta
        or initial_engine_meta(initial_budget=int(state["budget_remaining"]))
    )

    action = str(step_request["action"])
    target = step_request.get("target")
    tool_name = step_request.get("tool_name")
    answer = step_request.get("answer")
    plan_text = step_request.get("plan_text")
    payload = step_request.get("payload") or {}
    mission_type = str(state["mission_type"])

    info: dict[str, Any] = {
        "mission_type": mission_type,
        "invalid_action": False,
        "action": action,
    }

    cost = action_cost(action)
    state["cost_so_far"] += cost
    state["budget_remaining"] = max(int(state["budget_remaining"]) - cost, 0)
    info["action_cost"] = cost

    if action == "query_tool":
        meta["query_count"] = int(meta.get("query_count", 0)) + 1
        info["tool_name"] = tool_name

    if action == "inspect_context":
        info["context_inspected"] = True

    elif action == "query_tool":
        available_tools = normalize_available_tools(
            mission_type=mission_type,
            state=state,
        )
        info["available_tools"] = available_tools

        if not tool_name:
            mark_invalid_action(
                meta=meta,
                info=info,
                reason="missing_tool_name",
            )
        elif tool_name not in available_tools:
            meta["invalid_tool_count"] = int(meta.get("invalid_tool_count", 0)) + 1
            info["invalid_tool_count_so_far"] = int(meta["invalid_tool_count"])
            mark_invalid_action(
                meta=meta,
                info=info,
                reason="tool_not_available_for_mission",
            )
            info["tool_name"] = tool_name
        else:
            info["tool_invoked"] = tool_name
            info["tool_name"] = tool_name
            info["tool_result"] = build_tool_result(
                mission_type=mission_type,
                tool_name=str(tool_name),
                payload=dict(payload),
                state=state,
            )

    elif action == "propose_plan":
        if not plan_text:
            mark_invalid_action(
                meta=meta,
                info=info,
                reason="missing_plan_text",
            )
        else:
            info["plan_received"] = True

    elif action == "take_action":
        if not payload:
            mark_invalid_action(
                meta=meta,
                info=info,
                reason="missing_payload",
            )
        else:
            info["domain_action"] = payload

    elif action == "submit_final":
        raw_success, final_meta = evaluate_final_answer(
            mission_type=mission_type,
            hidden_truth=dict(state.get("hidden_truth", {})),
            answer=answer,
        )

        invalid_action_count = int(meta.get("invalid_action_count", 0))
        success_path_clean = invalid_action_count == 0
        accepted_success = raw_success and success_path_clean

        state["success"] = accepted_success
        state["done"] = True

        info["submitted_answer"] = answer
        info["final_evaluation"] = final_meta
        info["final_submission_correct"] = raw_success
        info["success_path_clean"] = success_path_clean
        info["invalid_action_count_so_far"] = invalid_action_count
        info["final_submission_accepted"] = accepted_success

        if raw_success and not success_path_clean:
            info["reason"] = "final_answer_correct_but_path_not_clean"

    else:
        mark_invalid_action(
            meta=meta,
            info=info,
            reason="unknown_action",
        )

    state["step_count"] += 1

    if int(state["budget_remaining"]) <= 0 and not bool(state["done"]):
        state["done"] = True
        state["failure_mode"] = "budget_exhausted"
        info["truncated"] = True
    elif int(state["step_count"]) >= int(state["max_steps"]) and not bool(state["done"]):
        state["done"] = True
        state["failure_mode"] = "max_steps_exhausted"
        info["truncated"] = True
    else:
        info["truncated"] = False

    reward_breakdown = summarize_reward(
        mission_type=mission_type,
        success=bool(state["success"]),
        step_count=int(state["step_count"]),
        max_steps=int(state["max_steps"]),
        query_count=int(meta.get("query_count", 0)),
        free_queries=int(meta.get("free_queries", 2)),
        invalid_action=bool(info["invalid_action"]),
        budget_remaining=int(state["budget_remaining"]),
        initial_budget=int(meta.get("initial_budget", state["budget_remaining"])),
        config=cfg,
    )

    reward = float(reward_breakdown["total"])
    state["score"] = round(float(state["score"]) + reward, 4)

    history_item = {
        "action": action,
        "target": target,
        "tool_name": tool_name,
        "answer": answer,
        "plan_text": plan_text,
        "payload": payload,
        "invalid_action": info["invalid_action"],
        "reason": info.get("reason"),
        "reward": reward,
        "reward_breakdown": reward_breakdown,
        "step_count_after": state["step_count"],
        "budget_remaining_after": state["budget_remaining"],
        "done": state["done"],
        "success": state["success"],
    }
    state["history"].append(history_item)

    info["reward_breakdown"] = reward_breakdown

    return (
        {
            "state": state,
            "reward": reward,
            "done": bool(state["done"]),
            "truncated": bool(info["truncated"]),
            "info": info,
        },
        meta,
    )
