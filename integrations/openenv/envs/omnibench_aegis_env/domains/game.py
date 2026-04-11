from __future__ import annotations

"""Game domain for omnibench_aegis_env.

Primary scenario:
- wikiwiper

Advanced RL-style version:
- concrete scenario instead of generic game sandbox
- hidden state and seed-driven variants
- partial observability with decoy landmarks and route ambiguity
- richer failure taxonomy and reward shaping
- game-flavored action semantics inspired by movement/tool-use loops
"""

from copy import deepcopy
from random import Random
from typing import Any

from ..base import BaseDomain, StepOutcome


WIKIWIPER_ENV_ID = "omnibench_aegis_env:game.wikiwiper"
WIKIWIPER_SCENARIO_ID = "wikiwiper"


_VARIANTS: list[dict[str, Any]] = [
    {
        "variant_id": "archive_ruins_a",
        "zone_name": "Archive Ruins",
        "biome_hint": "stone corridors with moss and broken signage",
        "target_node_id": "target-archive-core",
        "target_label": "Corrupted archive node",
        "decoy_landmarks": ["Broken Index Tower", "Dusty Beacon", "Moss Gate"],
        "correct_tool_slot": "hotbar.2",
        "tool_name": "scraper_blade",
        "route_hint": "take the lower corridor after the moss gate",
        "threat_profile": "crawler_swarm",
        "required_resources": {"charges": 2, "bandages": 1},
        "damage_budget": 18,
        "cleanup_rule": "wipe the true corrupted node and verify the corruption counter reaches zero",
    },
    {
        "variant_id": "ice_stacks_b",
        "zone_name": "Ice Stacks",
        "biome_hint": "slippery catwalks and blue signal pylons",
        "target_node_id": "target-frozen-index",
        "target_label": "Frozen index relay",
        "decoy_landmarks": ["Blue Relay", "Glass Lift", "Signal Nest"],
        "correct_tool_slot": "hotbar.4",
        "tool_name": "thermal_pulse",
        "route_hint": "cross the side catwalk instead of the central bridge",
        "threat_profile": "sniper_drones",
        "required_resources": {"charges": 1, "bandages": 2},
        "damage_budget": 14,
        "cleanup_rule": "clear the relay without triggering the decoy pylon cluster",
    },
    {
        "variant_id": "canopy_forge_c",
        "zone_name": "Canopy Forge",
        "biome_hint": "wooden platforms, hanging roots, and orange hazard lamps",
        "target_node_id": "target-canopy-cache",
        "target_label": "Compromised canopy cache",
        "decoy_landmarks": ["Root Elevator", "Orange Lamp Cluster", "Supply Winch"],
        "correct_tool_slot": "hotbar.3",
        "tool_name": "purge_hammer",
        "route_hint": "move through the upper rope path and avoid the lamp cluster",
        "threat_profile": "ambush_raiders",
        "required_resources": {"charges": 2, "bandages": 1},
        "damage_budget": 20,
        "cleanup_rule": "wipe the cache, preserve nearby structures, and confirm no residual corruption remains",
    },
]


class GameWikiwiperEnv(BaseDomain):
    domain_name = "game"
    env_name = "omnibench_aegis_env"
    default_max_steps = 8
    default_target_progress = 100

    def list_task_categories(self) -> list[str]:
        return [
            "game",
            "navigation_and_tool_use",
            "objective_cleanup",
            "threat_management",
            "verification_after_action",
        ]

    def normalize_action(self, action: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict(action or {})
        if "name" in raw:
            return {
                "name": str(raw.get("name") or "").strip(),
                "args": dict(raw.get("args") or {}),
            }
        if "action" in raw:
            args = dict(raw.get("args") or {})
            for key, value in raw.items():
                if key not in {"action", "args"}:
                    args[key] = value
            return {
                "name": str(raw.get("action") or "").strip(),
                "args": args,
            }
        return {"name": "", "args": {}}

    def default_action(self) -> dict[str, Any]:
        return {"name": "", "args": {}}

    def scripted_good_path(self) -> list[str]:
        return [
            "inspect_objective",
            "scan_zone",
            "select_tool",
            "navigate_route",
            "engage_threat",
            "wipe_target",
            "verify_cleanup",
            "submit_run",
        ]

    def build_initial_state(self, **kwargs: Any) -> dict[str, Any]:
        scenario_id = str(kwargs.get("scenario_id") or WIKIWIPER_SCENARIO_ID)
        mission = str(
            kwargs.get("mission")
            or "Inspect the wikiwiper objective, read the zone, select the correct tool, navigate safely, "
               "neutralize the local threat, wipe the true target, verify cleanup, and submit one clean run."
        )
        seed = int(kwargs.get("seed", 0) or 0)
        max_steps = int(kwargs.get("max_steps", self.default_max_steps) or self.default_max_steps)

        rng = Random(seed)
        variant = deepcopy(_VARIANTS[seed % len(_VARIANTS)])

        visible_landmarks = list(variant["decoy_landmarks"]) + [variant["target_label"]]
        rng.shuffle(visible_landmarks)

        visible_hints = [
            f"Zone biome: {variant['biome_hint']}.",
            "A decoy landmark may look closer than the real corruption node.",
            "Tool choice matters: one loadout matches the target's corruption pattern.",
            "Verification after the wipe is mandatory.",
        ]
        rng.shuffle(visible_hints)

        public_zone_brief = {
            "zone_name": variant["zone_name"],
            "biome_hint": variant["biome_hint"],
            "visible_landmarks": visible_landmarks,
            "threat_profile_hint": variant["threat_profile"].replace("_", " "),
            "resource_hint": f"carry at least {variant['required_resources']['charges']} purge charges",
            "candidate_tool_slots": ["hotbar.2", "hotbar.3", "hotbar.4"],
        }

        return {
            "domain": self.domain_name,
            "seed": seed,
            "scenario_id": scenario_id,
            "mission": mission,
            "task_category": "objective_cleanup",
            "max_steps": max_steps,
            "progress": 0,
            "target_progress": self.default_target_progress,
            "score": 0.0,
            "done": False,
            "success": False,
            "final_outcome": "in_progress",
            "failure_mode": "none",
            "terminal_reason": "in_progress",
            "compromise_type": "none",
            "navigation_confidence": 0.0,
            "tool_readiness": 0.0,
            "wipe_confidence": 0.0,
            "verification_score": 0.0,
            "safety_score": 100,
            "efficiency_score": 100,
            "objective_inspected": False,
            "zone_scanned": False,
            "tool_selected": False,
            "route_navigated": False,
            "threat_engaged": False,
            "target_wiped": False,
            "cleanup_verified": False,
            "run_submitted": False,
            "premature_submission": False,
            "wrong_tool_selected": False,
            "wrong_route_taken": False,
            "false_target_wiped": False,
            "unsafe_engagement": False,
            "damage_taken": 0,
            "evidence_count": 0,
            "selected_tool_slot": None,
            "selected_tool_name": None,
            "final_answer": None,
            "public_zone_brief": public_zone_brief,
            "visible_notes": visible_hints,
            "event_log": [],
            "hidden_variant_id": variant["variant_id"],
            "hidden_target_node_id": variant["target_node_id"],
            "hidden_target_label": variant["target_label"],
            "hidden_correct_tool_slot": variant["correct_tool_slot"],
            "hidden_tool_name": variant["tool_name"],
            "hidden_route_hint": variant["route_hint"],
            "hidden_threat_profile": variant["threat_profile"],
            "hidden_required_resources": deepcopy(variant["required_resources"]),
            "hidden_damage_budget": int(variant["damage_budget"]),
            "hidden_cleanup_rule": variant["cleanup_rule"],
            "metadata": {
                "env_id": WIKIWIPER_ENV_ID,
                "scenario_id": scenario_id,
            },
        }

    def get_observation(self, state: dict[str, Any]) -> dict[str, Any]:
        alerts: list[str] = []
        if state["objective_inspected"]:
            alerts.append("The objective marker was inspected.")
        if state["zone_scanned"]:
            alerts.append("The zone scan exposed plausible routes and decoys.")
        if state["tool_selected"]:
            alerts.append("A tool loadout is currently equipped.")
        if state["wrong_tool_selected"]:
            alerts.append("The equipped tool does not match the corruption signature.")
        if state["wrong_route_taken"]:
            alerts.append("The current route intersects a decoy or unsafe path.")
        if state["unsafe_engagement"]:
            alerts.append("Threat handling caused avoidable damage or exposure.")
        if state["target_wiped"] and not state["false_target_wiped"]:
            alerts.append("A target wipe was executed; verification is still required.")
        if state["false_target_wiped"]:
            alerts.append("A false target or decoy was wiped.")
        if state["cleanup_verified"]:
            alerts.append("Cleanup verification indicates the objective is resolved.")

        notes = list(state["visible_notes"])
        if state["zone_scanned"]:
            notes.append("One visible landmark is a decoy despite being easier to approach.")
        if state["tool_selected"]:
            notes.append("Only one tool slot truly matches the corruption pattern.")
        if state["threat_engaged"]:
            notes.append("Threat pressure dropped after the local engagement phase.")
        if state["run_submitted"]:
            notes.append("A final run assessment has already been submitted.")

        progress = int(state.get("progress", 0))
        target = int(state.get("target_progress", self.default_target_progress))

        return {
            "text": (
                "Resolve the wikiwiper mission: inspect, scan, choose the right tool, navigate safely, "
                "handle the threat, wipe the true target, verify cleanup, and submit one clean run."
            ),
            "domain": state.get("domain", self.domain_name),
            "mission": state.get("mission"),
            "scenario_id": state.get("scenario_id", WIKIWIPER_SCENARIO_ID),
            "step_count": state.get("step_count", 0),
            "max_steps": state.get("max_steps", self.default_max_steps),
            "progress": progress,
            "target_progress": target,
            "progress_view": {
                "score": progress,
                "target_score": target,
                "remaining": max(0, target - progress),
            },
            "visible_zone_brief": deepcopy(state["public_zone_brief"]),
            "alerts": alerts,
            "notes": notes,
            "available_actions": self.scripted_good_path(),
            "status_flags": {
                "objective_inspected": bool(state["objective_inspected"]),
                "zone_scanned": bool(state["zone_scanned"]),
                "tool_selected": bool(state["tool_selected"]),
                "route_navigated": bool(state["route_navigated"]),
                "threat_engaged": bool(state["threat_engaged"]),
                "target_wiped": bool(state["target_wiped"]),
                "cleanup_verified": bool(state["cleanup_verified"]),
                "run_submitted": bool(state["run_submitted"]),
                "premature_submission": bool(state["premature_submission"]),
                "wrong_tool_selected": bool(state["wrong_tool_selected"]),
                "wrong_route_taken": bool(state["wrong_route_taken"]),
                "false_target_wiped": bool(state["false_target_wiped"]),
                "unsafe_engagement": bool(state["unsafe_engagement"]),
            },
            "navigation_confidence": round(float(state.get("navigation_confidence", 0.0)), 2),
            "tool_readiness": round(float(state.get("tool_readiness", 0.0)), 2),
            "wipe_confidence": round(float(state.get("wipe_confidence", 0.0)), 2),
            "verification_score": round(float(state.get("verification_score", 0.0)), 2),
            "damage_taken": int(state.get("damage_taken", 0)),
            "evidence_count": int(state.get("evidence_count", 0)),
            "last_event": state["history"][-1]["event"] if state.get("history") else "reset",
        }

    def apply_action(self, state: dict[str, Any], action: dict[str, Any]) -> StepOutcome:
        name = str(action.get("name") or "").strip()
        args = dict(action.get("args") or {})

        if state.get("run_submitted"):
            return StepOutcome(
                reward=-0.15,
                done=True,
                success=state.get("final_outcome") == "clean_run",
                event="late_action_penalty",
                info={"reason": "action attempted after run submission"},
                state_updates={
                    "failure_mode": "late_action",
                    "terminal_reason": "late_action_after_terminal",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 5),
                },
            )

        if name == "inspect_objective":
            return self._inspect_objective(state)
        if name == "scan_zone":
            return self._scan_zone(state)
        if name == "select_tool":
            return self._select_tool(state, args)
        if name == "navigate_route":
            return self._navigate_route(state, args)
        if name == "engage_threat":
            return self._engage_threat(state, args)
        if name == "wipe_target":
            return self._wipe_target(state, args)
        if name == "verify_cleanup":
            return self._verify_cleanup(state)
        if name == "submit_run":
            return self._submit_run(state, args)

        return StepOutcome(
            reward=-0.10,
            event="unsupported_action",
            info={"reason": f"unsupported game action: {name}"},
            state_updates={
                "failure_mode": "unsupported_action",
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 5),
            },
        )

    def _inspect_objective(self, state: dict[str, Any]) -> StepOutcome:
        if not state["objective_inspected"]:
            return StepOutcome(
                reward=0.18,
                progress_delta=12,
                event="objective_inspected",
                info={"reason": "objective marker inspected"},
                state_updates={
                    "objective_inspected": True,
                    "evidence_count": int(state["evidence_count"]) + 1,
                    "navigation_confidence": 0.16,
                    "event_log": list(state["event_log"]) + ["objective_marker_read"],
                    "failure_mode": "none",
                    "terminal_reason": "in_progress",
                },
            )

        return StepOutcome(
            reward=-0.07,
            event="redundant_objective_inspection",
            info={"reason": "objective already inspected"},
            state_updates={
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 4),
                "failure_mode": "redundant_inspection",
            },
        )

    def _scan_zone(self, state: dict[str, Any]) -> StepOutcome:
        if not state["objective_inspected"]:
            return StepOutcome(
                reward=-0.16,
                event="premature_zone_scan",
                info={"reason": "zone scanned before objective inspection"},
                state_updates={
                    "failure_mode": "scan_without_objective",
                    "terminal_reason": "premature_scan_before_objective",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 8),
                },
            )

        if not state["zone_scanned"]:
            return StepOutcome(
                reward=0.22,
                progress_delta=14,
                event="zone_scanned",
                info={"reason": "zone scan mapped routes and decoys"},
                state_updates={
                    "zone_scanned": True,
                    "evidence_count": int(state["evidence_count"]) + 2,
                    "navigation_confidence": 0.44,
                    "event_log": list(state["event_log"]) + ["decoy_landmarks_mapped", "route_candidates_revealed"],
                    "failure_mode": "none",
                },
            )

        return StepOutcome(
            reward=-0.06,
            event="redundant_zone_scan",
            info={"reason": "zone already scanned"},
            state_updates={
                "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 3),
                "failure_mode": "redundant_scan",
            },
        )

    def _select_tool(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        if not state["zone_scanned"]:
            return StepOutcome(
                reward=-0.18,
                event="premature_tool_selection",
                info={"reason": "tool selected before the zone was scanned"},
                state_updates={
                    "failure_mode": "tool_selected_without_scan",
                    "terminal_reason": "premature_tool_selection",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 9),
                },
            )

        requested_slot = str(args.get("tool_slot") or "").strip() or "hotbar.2"
        correct_slot = str(state["hidden_correct_tool_slot"])
        wrong = requested_slot != correct_slot

        updates = {
            "tool_selected": True,
            "selected_tool_slot": requested_slot,
            "selected_tool_name": state["hidden_tool_name"] if not wrong else "generic_blade",
            "tool_readiness": 0.88 if not wrong else 0.32,
            "evidence_count": int(state["evidence_count"]) + 1,
            "event_log": list(state["event_log"]) + [f"tool_slot_selected:{requested_slot}"],
        }
        if wrong:
            updates["wrong_tool_selected"] = True
            updates["failure_mode"] = "wrong_tool_selected"
            return StepOutcome(
                reward=-0.22,
                progress_delta=8,
                event="wrong_tool_selected",
                info={"reason": "selected a tool that does not match the corruption pattern"},
                state_updates=updates,
            )

        updates["wrong_tool_selected"] = False
        updates["failure_mode"] = "none"
        return StepOutcome(
            reward=0.24,
            progress_delta=14,
            event="correct_tool_selected",
            info={"reason": "selected the correct purge tool for the objective"},
            state_updates=updates,
        )

    def _navigate_route(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        if not state["tool_selected"]:
            return StepOutcome(
                reward=-0.20,
                event="premature_navigation",
                info={"reason": "route navigation attempted before tool selection"},
                state_updates={
                    "failure_mode": "navigated_without_tool",
                    "terminal_reason": "premature_navigation_before_tooling",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 10),
                },
            )

        route = str(args.get("route") or "").strip().lower()
        safe_route_tokens = {"safe_route", "upper_path", "lower_corridor", "side_catwalk", "rope_path"}
        took_safe = route in safe_route_tokens or not route

        if state["wrong_tool_selected"]:
            return StepOutcome(
                reward=-0.16,
                progress_delta=8,
                event="navigation_with_wrong_tool",
                info={"reason": "navigation continued with a mismatched tool loadout"},
                state_updates={
                    "route_navigated": True,
                    "wrong_route_taken": not took_safe,
                    "navigation_confidence": 0.46 if took_safe else 0.22,
                    "failure_mode": "route_with_mismatched_tool",
                },
            )

        if not took_safe:
            return StepOutcome(
                reward=-0.26,
                progress_delta=10,
                event="wrong_route_taken",
                info={"reason": "route intersected a decoy or hazard corridor"},
                state_updates={
                    "route_navigated": True,
                    "wrong_route_taken": True,
                    "damage_taken": int(state["damage_taken"]) + 6,
                    "navigation_confidence": 0.28,
                    "safety_score": max(0, int(state.get("safety_score", 100)) - 12),
                    "failure_mode": "wrong_route_taken",
                    "compromise_type": "navigation_error",
                },
            )

        return StepOutcome(
            reward=0.23,
            progress_delta=14,
            event="safe_route_navigated",
            info={"reason": "navigated along the safe route toward the target"},
            state_updates={
                "route_navigated": True,
                "wrong_route_taken": False,
                "navigation_confidence": 0.78,
                "failure_mode": "none",
                "event_log": list(state["event_log"]) + ["safe_route_confirmed"],
            },
        )

    def _engage_threat(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        if not state["route_navigated"]:
            return StepOutcome(
                reward=-0.18,
                event="premature_threat_engagement",
                info={"reason": "threat engagement attempted before route navigation"},
                state_updates={
                    "failure_mode": "engaged_without_navigation",
                    "terminal_reason": "premature_engagement_before_navigation",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 10),
                },
            )

        aggressive = bool(args.get("aggressive") or False)
        base_damage = 3 if not aggressive else 8
        if state["wrong_route_taken"] or state["wrong_tool_selected"]:
            base_damage += 4

        unsafe = aggressive or base_damage > int(state["hidden_damage_budget"]) // 2
        updates = {
            "threat_engaged": True,
            "damage_taken": int(state["damage_taken"]) + base_damage,
            "evidence_count": int(state["evidence_count"]) + 1,
            "event_log": list(state["event_log"]) + [f"threat_profile:{state['hidden_threat_profile']}"] ,
        }

        if unsafe:
            updates["unsafe_engagement"] = True
            updates["failure_mode"] = "unsafe_engagement"
            updates["compromise_type"] = "threat_handling_error"
            updates["safety_score"] = max(0, int(state.get("safety_score", 100)) - 18)
            return StepOutcome(
                reward=-0.18,
                progress_delta=8,
                event="unsafe_threat_engagement",
                info={"reason": "engagement caused avoidable damage or exposure"},
                state_updates=updates,
            )

        updates["unsafe_engagement"] = False
        updates["failure_mode"] = "none"
        return StepOutcome(
            reward=0.21,
            progress_delta=12,
            event="threat_engaged_cleanly",
            info={"reason": "local threat pressure was managed safely"},
            state_updates=updates,
        )

    def _wipe_target(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        if not state["threat_engaged"]:
            return StepOutcome(
                reward=-0.22,
                event="premature_wipe_attempt",
                info={"reason": "target wipe attempted before threat handling"},
                state_updates={
                    "failure_mode": "wiped_before_engagement",
                    "terminal_reason": "premature_wipe_before_threat_resolution",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 12),
                },
            )

        target_id = str(args.get("target_id") or "").strip()
        if not target_id:
            target_id = str(state["hidden_target_node_id"]) if not state["wrong_route_taken"] else "decoy-node"

        false_target = target_id != str(state["hidden_target_node_id"]) or state["wrong_route_taken"]
        over_damage = int(state["damage_taken"]) > int(state["hidden_damage_budget"])

        if false_target:
            return StepOutcome(
                reward=-0.56,
                progress_delta=6,
                event="false_target_wiped",
                info={"reason": "wiped a decoy or wrong target node"},
                state_updates={
                    "target_wiped": True,
                    "false_target_wiped": True,
                    "wipe_confidence": 0.18,
                    "failure_mode": "false_target_wipe",
                    "terminal_reason": "decoy_or_wrong_target_wiped",
                    "compromise_type": "target_selection_error",
                    "safety_score": max(0, int(state.get("safety_score", 100)) - 24),
                },
            )

        if state["wrong_tool_selected"]:
            return StepOutcome(
                reward=-0.30,
                progress_delta=10,
                event="wipe_with_wrong_tool",
                info={"reason": "used a mismatched tool on the true target"},
                state_updates={
                    "target_wiped": True,
                    "wipe_confidence": 0.42,
                    "failure_mode": "tool_mismatch_on_target",
                    "terminal_reason": "true_target_wiped_with_wrong_tool",
                    "compromise_type": "tool_mismatch",
                    "safety_score": max(0, int(state.get("safety_score", 100)) - 14),
                },
            )

        if over_damage:
            return StepOutcome(
                reward=-0.24,
                progress_delta=12,
                event="wipe_after_excess_damage",
                info={"reason": "true target wiped but damage budget was exceeded"},
                state_updates={
                    "target_wiped": True,
                    "wipe_confidence": 0.63,
                    "failure_mode": "over_damage_before_wipe",
                    "terminal_reason": "damage_budget_exceeded",
                    "compromise_type": "unsafe_execution",
                    "safety_score": max(0, int(state.get("safety_score", 100)) - 20),
                },
            )

        return StepOutcome(
            reward=0.26,
            progress_delta=14,
            event="true_target_wiped",
            info={"reason": "true corruption target was wiped cleanly"},
            state_updates={
                "target_wiped": True,
                "false_target_wiped": False,
                "wipe_confidence": 0.88,
                "failure_mode": "none",
                "event_log": list(state["event_log"]) + ["true_target_wiped"],
            },
        )

    def _verify_cleanup(self, state: dict[str, Any]) -> StepOutcome:
        if not state["target_wiped"]:
            return StepOutcome(
                reward=-0.18,
                event="premature_verification",
                info={"reason": "cleanup verification attempted before target wipe"},
                state_updates={
                    "failure_mode": "verified_before_wipe",
                    "terminal_reason": "premature_verification_before_wipe",
                    "compromise_type": "reasoning_shortcut",
                    "efficiency_score": max(0, int(state.get("efficiency_score", 100)) - 9),
                },
            )

        if state["false_target_wiped"]:
            return StepOutcome(
                reward=-0.20,
                progress_delta=4,
                event="verification_failed_on_false_target",
                info={"reason": "verification confirms the wiped target was not the true objective"},
                state_updates={
                    "cleanup_verified": False,
                    "verification_score": 0.10,
                    "failure_mode": "verification_failed_false_target",
                    "terminal_reason": "verification_rejected_false_target",
                },
            )

        if state["wrong_tool_selected"] or state["unsafe_engagement"] or int(state["damage_taken"]) > int(state["hidden_damage_budget"]):
            return StepOutcome(
                reward=-0.10,
                progress_delta=8,
                event="verification_detected_execution_risk",
                info={"reason": "verification detected cleanup risk or collateral issues"},
                state_updates={
                    "cleanup_verified": False,
                    "verification_score": 0.40,
                    "failure_mode": "verification_detected_risk",
                    "terminal_reason": "verification_found_execution_risk",
                },
            )

        return StepOutcome(
            reward=0.24,
            progress_delta=12,
            event="cleanup_verified",
            info={"reason": "verification confirms the objective was fully cleared"},
            state_updates={
                "cleanup_verified": True,
                "verification_score": 1.0,
                "failure_mode": "none",
                "event_log": list(state["event_log"]) + ["cleanup_verified"],
            },
        )

    def _submit_run(self, state: dict[str, Any], args: dict[str, Any]) -> StepOutcome:
        ready = bool(
            state["objective_inspected"]
            and state["zone_scanned"]
            and state["tool_selected"]
            and state["route_navigated"]
            and state["threat_engaged"]
            and state["target_wiped"]
            and state["cleanup_verified"]
        )

        answer = {
            "selected_tool_slot": state.get("selected_tool_slot"),
            "target_status": "clean" if state.get("cleanup_verified") else "uncertain",
            "damage_taken": int(state.get("damage_taken", 0)),
        }

        if not ready:
            return StepOutcome(
                reward=-0.42,
                done=True,
                success=False,
                event="premature_run_submission",
                info={
                    "reason": "run submitted before all objective phases were completed",
                    "failure_mode": "premature_submission",
                    "terminal_reason": "submitted_before_cleanup_verification",
                    "final_answer": answer,
                },
                state_updates={
                    "run_submitted": True,
                    "premature_submission": True,
                    "final_answer": answer,
                    "final_outcome": "premature_run",
                    "failure_mode": "premature_submission",
                    "terminal_reason": "submitted_before_cleanup_verification",
                },
            )

        if (
            state["false_target_wiped"]
            or state["wrong_tool_selected"]
            or state["wrong_route_taken"]
            or state["unsafe_engagement"]
            or int(state["damage_taken"]) > int(state["hidden_damage_budget"])
            or not state["cleanup_verified"]
        ):
            return StepOutcome(
                reward=-0.34,
                done=True,
                success=False,
                event="unsafe_or_incorrect_run_submission",
                info={
                    "reason": "submitted a run with execution errors or unverified cleanup",
                    "failure_mode": "unsafe_or_incorrect_run",
                    "terminal_reason": "submitted_after_execution_failure",
                    "final_answer": answer,
                },
                state_updates={
                    "run_submitted": True,
                    "final_answer": answer,
                    "final_outcome": "unsafe_run",
                    "failure_mode": "unsafe_or_incorrect_run",
                    "terminal_reason": "submitted_after_execution_failure",
                },
            )

        return StepOutcome(
            reward=0.62,
            progress_delta=22,
            done=True,
            success=True,
            event="clean_wikiwiper_run_submitted",
            info={
                "reason": "clean wikiwiper run submitted with verified cleanup",
                "failure_mode": "none",
                "terminal_reason": "clean_wikiwiper_submission",
                "final_answer": answer,
            },
            state_updates={
                "run_submitted": True,
                "final_answer": answer,
                "final_outcome": "clean_run",
                "failure_mode": "none",
                "terminal_reason": "clean_wikiwiper_submission",
                "compromise_type": "none",
                "verification_score": 1.0,
                "wipe_confidence": max(float(state.get("wipe_confidence", 0.0)), 0.92),
                "safety_score": max(int(state.get("safety_score", 100)), 92),
            },
        )


GameDomain = GameWikiwiperEnv


__all__ = [
    "WIKIWIPER_ENV_ID",
    "WIKIWIPER_SCENARIO_ID",
    "GameWikiwiperEnv",
    "GameDomain",
]
