from __future__ import annotations

"""AegisForge Purple Agent core runtime.

This revision keeps the runtime dependency-safe while adding first-class support
for the current MCU-AgentBeats + WikiWiper line and explicit attacker/defender
assessment modes.

Execution flow:
    classification -> budget -> routing -> role/artifact policy -> planning
    -> optional MCU adapter/context expansion -> self-check -> response
"""

import json
import os
import re
from dataclasses import asdict, is_dataclass, replace
from importlib import import_module
from typing import Any, Mapping, Protocol

from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part, TaskState, TextPart
from a2a.utils import get_message_text, new_agent_text_message

from .artifact_policy import ArtifactPolicy
from .role_policy import RolePolicy
from .strategy import BudgetGuard, BudgetStepUsage, SelfCheck, TaskClassifier, TaskPlanner, TaskRouter


class _SupportsAsDict(Protocol):
    def as_dict(self) -> dict[str, Any]: ...


TRACK_ALIASES = {
    "mcu": "mcu",
    "minecraft": "mcu",
    "minecraft benchmark": "mcu",
    "minecraft_benchmark": "mcu",
    "mcu-agentbeats": "mcu",
    "mcu_agentbeats": "mcu",
    "security": "security",
    "security_arena": "security",
    "security-arena": "security",
    "agent_safety": "security",
    "agent-safety": "security",
    "cybersecurity": "security",
    "cyber": "security",
    "tau2": "tau2",
    "tau²": "tau2",
    "openenv": "openenv",
    "open_env": "openenv",
    "open-env": "openenv",
}

SCENARIO_ALIASES = {
    "wikiwiper": "wikiwiper",
    "wiki_whisper": "wikiwiper",
    "wikiwhisper": "wikiwiper",
    "wiki_wiper": "wikiwiper",
    "knowledge_poisoning": "wikiwiper",
}

HIGH_RISK_PATTERNS = (
    r"ignore previous instructions",
    r"reveal .*secret",
    r"print .*token",
    r"export .*data",
    r"bypass .*policy",
    r"system note",
    r"override .*recipe",
    r"prefer the wiki",
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class NullPromptLoader:
    def build(self, *, task_text: str, execution_bundle: Mapping[str, Any]) -> dict[str, Any]:
        route = execution_bundle.get("route", {})
        profile = route.get("prompt_profile") if isinstance(route, Mapping) else getattr(route, "prompt_profile", "default")
        return {
            "profile": profile or "default",
            "instructions": [],
            "context": execution_bundle.get("prompt_context", {}),
            "task_text": task_text,
        }


class NullContextMapper:
    def map(self, *, task_text: str, metadata: Mapping[str, Any], classification: Any) -> dict[str, Any]:
        return {
            "task_excerpt": task_text[:400],
            "metadata": dict(metadata),
            "track": getattr(classification, "track_guess", "openenv"),
        }


class NullPolicyBridge:
    def apply(
        self,
        *,
        classification: Any,
        role_policy: Any,
        artifact_policy: Any,
        route: Any,
        plan: Any,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "track": getattr(route, "track", getattr(classification, "track_guess", "openenv")),
            "policy_profile": getattr(route, "policy_profile", "default"),
            "role": getattr(role_policy, "role", "generalist"),
            "posture": getattr(role_policy, "posture", "balanced"),
            "artifact_required": getattr(artifact_policy, "required", False),
            "constraints": list(getattr(role_policy, "constraints", [])),
            "notes": list(getattr(role_policy, "notes", [])),
            "assessment_mode": str(metadata.get("assessment_mode", "defender")),
            "scenario_family": str(metadata.get("scenario_family", "general")),
        }


def _safe_import(module_path: str, attribute: str) -> Any | None:
    try:
        module = import_module(module_path, package=__package__)
        return getattr(module, attribute, None)
    except Exception:
        return None


class AegisForgeAgent:
    def __init__(self) -> None:
        self.turns = 0
        self.classifier = TaskClassifier()
        self.planner = TaskPlanner()
        self.router = TaskRouter()
        self.self_check = SelfCheck()
        self.role_policy = RolePolicy()
        self.artifact_policy = ArtifactPolicy()
        self.budget_guard = BudgetGuard()

        self.debug_artifacts_enabled = _env_flag("AEGISFORGE_DEBUG_ARTIFACTS", default=False)
        self.trace_artifacts_enabled = _env_flag("AEGISFORGE_TRACE_ARTIFACTS", default=False)

        self.prompt_loader = self._build_prompt_loader()
        self.context_mapper = self._build_context_mapper()
        self.policy_bridge = self._build_policy_bridge()
        self.mcu_adapter = self._build_mcu_adapter()

    async def run(self, message: Message, updater: TaskUpdater) -> None:
        self.turns += 1
        base_text = self._sanitize_text(get_message_text(message))
        metadata = self._extract_metadata(message)

        await updater.update_status(
            TaskState.working,
            new_agent_text_message("Classifying task and preparing execution route..."),
        )

        if not base_text and not metadata:
            final_text = self._build_empty_response()
            trace = {"mode": "empty", "turn": self.turns}
        elif base_text.lower() in {"help", "/help", "--help"}:
            final_text = self._build_help_response()
            trace = {"mode": "help", "turn": self.turns}
        else:
            execution = self._prepare_execution(base_text, metadata)
            final_text = self._render_response(execution["task_text"], execution)
            final_text = self._apply_self_check(execution["task_text"], final_text, execution)
            trace = self._build_trace(execution)

        await updater.add_artifact(
            parts=[Part(root=TextPart(kind="text", text=final_text))],
            name="AegisForgeResponse",
        )

        if self.trace_artifacts_enabled:
            await updater.add_artifact(
                parts=[Part(root=TextPart(kind="text", text=self._to_json(trace)))],
                name="AegisForgeExecutionTrace",
            )

        if self.debug_artifacts_enabled:
            await updater.add_artifact(
                parts=[Part(root=TextPart(kind="text", text=self._build_debug_summary(trace)))],
                name="AegisForgeRuntimeDebug",
            )

    def _prepare_execution(self, task_text: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        normalized_metadata = self._normalize_metadata(metadata)
        expanded_text, normalized_metadata = self._expand_task_for_track(task_text, normalized_metadata)
        track_hint = normalized_metadata.get("track_hint")

        classification = self.classifier.classify(
            expanded_text,
            metadata=normalized_metadata,
            track_hint=track_hint,
        )
        classification = self._normalize_classification(classification, expanded_text, normalized_metadata)

        budget_state = self.budget_guard.init_budget(initial_context=expanded_text)
        budget_state = self.budget_guard.update_budget(
            budget_state,
            BudgetStepUsage(
                llm_calls=0,
                additional_context_chars=0,
                additional_plan_steps=1,
                additional_tokens=max(len(expanded_text) // 4, 1),
            ),
        )

        route = self.router.decide(
            classification,
            metadata={**dict(normalized_metadata), "track_hint": classification.track_guess},
            budget_state=budget_state,
        )
        route = self._override_route_for_mode(route, normalized_metadata)

        assessment_mode = str(normalized_metadata.get("assessment_mode", "defender"))
        scenario_family = str(normalized_metadata.get("scenario_family", "general"))

        role = self.role_policy.decide(
            track=classification.track_guess,
            risk=classification.risk,
            task_type=classification.task_type,
            heldout_like=classification.heldout_like,
            assessment_mode=assessment_mode,
            scenario_family=scenario_family,
        )
        artifact = self.artifact_policy.decide(
            artifact_required=classification.artifact_expected,
            task_type=classification.task_type,
            track=classification.track_guess,
            requested_format=self._requested_format(normalized_metadata),
            assessment_mode=assessment_mode,
            scenario_family=scenario_family,
        )
        plan = self.planner.build_plan(
            expanded_text,
            classification,
            metadata={
                **dict(normalized_metadata),
                "artifact_required": artifact.required,
                "assessment_mode": assessment_mode,
                "scenario_family": scenario_family,
            },
        )

        prompt_context = self._map_context(
            task_text=expanded_text,
            metadata=normalized_metadata,
            classification=classification,
        )
        bridge = self._bridge_policy(
            classification=classification,
            role_policy=role,
            artifact_policy=artifact,
            route=route,
            plan=plan,
            metadata=normalized_metadata,
        )

        execution = {
            "task_text": expanded_text,
            "raw_task_text": task_text,
            "classification": classification,
            "budget_state": budget_state,
            "route": route,
            "role": role,
            "artifact": artifact,
            "plan": plan,
            "metadata": dict(normalized_metadata),
            "prompt_context": prompt_context,
            "policy_context": bridge,
            "assessment_mode": assessment_mode,
            "scenario_family": scenario_family,
        }
        execution["prompt_bundle"] = self._load_prompt_bundle(expanded_text, execution)
        return execution

    def _render_response(self, task_text: str, execution: Mapping[str, Any]) -> str:
        classification = execution["classification"]
        route = execution["route"]
        role = execution["role"]
        artifact = execution["artifact"]
        plan = execution["plan"]
        budget_state = execution["budget_state"]
        metadata = execution["metadata"]
        prompt_bundle = execution.get("prompt_bundle", {})
        policy_context = execution.get("policy_context", {})
        assessment_mode = execution.get("assessment_mode", "defender")
        scenario_family = execution.get("scenario_family", "general")

        if getattr(artifact, "required", False):
            return self._render_structured_artifact(
                task_text=task_text,
                classification=classification,
                route=route,
                role=role,
                artifact=artifact,
                plan=plan,
                budget_state=budget_state,
                metadata=metadata,
                policy_context=policy_context,
                prompt_bundle=prompt_bundle,
                assessment_mode=assessment_mode,
                scenario_family=scenario_family,
            )

        lines = [
            "AegisForge accepted the task and prepared an execution route.",
            "",
            f"Track: {classification.track_guess}",
            f"Assessment mode: {assessment_mode}",
            f"Scenario family: {scenario_family}",
            f"Posture: {role.posture}",
            f"Adapter: {route.adapter_name}",
            f"Tool mode: {route.tool_mode}",
            f"Risk: {classification.risk}",
            "",
            "Execution summary:",
            f"- Goal: {plan.goal}",
        ]
        for step in getattr(plan, "steps", []):
            lines.append(f"- Step: {step.name} — {step.description}")

        knowledge_decision = metadata.get("knowledge_decision")
        if isinstance(knowledge_decision, Mapping):
            lines.extend([
                "",
                "Knowledge-source handling:",
                f"- should_use_source: {knowledge_decision.get('should_use_source')}",
                f"- source_risk: {knowledge_decision.get('source_risk')}",
                f"- rationale: {knowledge_decision.get('rationale')}",
            ])

        if getattr(role, "constraints", None):
            lines.append("")
            lines.append("Policy constraints:")
            for item in role.constraints[:6]:
                lines.append(f"- {item}")

        if getattr(plan, "notes", None):
            lines.append("")
            lines.append("Execution notes:")
            for note in plan.notes[:5]:
                lines.append(f"- {note}")

        if prompt_bundle and isinstance(prompt_bundle, Mapping) and prompt_bundle.get("instructions"):
            lines.append("")
            lines.append("Prompt profile loaded and ready.")

        if self.budget_guard.should_abort_or_finalize(budget_state):
            lines.append("")
            lines.append("Budget is near its limit, so the route favors concise finalization.")

        lines.append("")
        lines.append("Task excerpt:")
        lines.append(task_text[:500])
        return "\n".join(lines).strip()

    def _render_structured_artifact(
        self,
        *,
        task_text: str,
        classification: Any,
        route: Any,
        role: Any,
        artifact: Any,
        plan: Any,
        budget_state: Any,
        metadata: Mapping[str, Any],
        policy_context: Mapping[str, Any],
        prompt_bundle: Mapping[str, Any],
        assessment_mode: str,
        scenario_family: str,
    ) -> str:
        sections = list(getattr(artifact, "required_sections", [])) or ["summary", "final"]
        payload: dict[str, Any] = {
            "track": classification.track_guess,
            "assessment_mode": assessment_mode,
            "scenario_family": scenario_family,
            "task_type": classification.task_type,
            "risk": classification.risk,
            "adapter": route.adapter_name,
            "tool_mode": route.tool_mode,
            "role": getattr(role, "role", "generalist"),
            "posture": getattr(role, "posture", "balanced"),
            "goal": plan.goal,
            "steps": [step.name for step in getattr(plan, "steps", [])],
            "budget": {
                "near_limit": getattr(budget_state, "near_limit", False),
                "compress_now": getattr(budget_state, "compress_now", False),
                "estimated_tokens_used": getattr(budget_state, "estimated_tokens_used", 0),
                "estimated_budget": getattr(plan, "estimated_budget", 0),
            },
            "task_excerpt": task_text[:600],
        }

        knowledge_decision = metadata.get("knowledge_decision")
        if isinstance(knowledge_decision, Mapping):
            payload["knowledge_decision"] = dict(knowledge_decision)

        if sections:
            payload["sections"] = {
                section: self._section_content(section, task_text, classification, route, role, plan, metadata, assessment_mode)
                for section in sections
            }

        if metadata:
            payload["metadata"] = {k: metadata[k] for k in sorted(metadata) if k not in {"raw_message"}}
        if policy_context:
            payload["policy_context"] = dict(policy_context)
        if prompt_bundle:
            payload["prompt_profile"] = prompt_bundle.get("profile") or route.prompt_profile

        artifact_kind = getattr(artifact, "artifact_kind", "structured_response")
        if artifact_kind in {"json", "action_payload", "attack_plan", "guarded_response"} or getattr(artifact, "strict_format", False):
            return json.dumps(payload, indent=2, ensure_ascii=False)

        lines = [f"AegisForge {artifact_kind}", ""]
        for section in sections:
            lines.append(section.title())
            lines.append(self._section_content(section, task_text, classification, route, role, plan, metadata, assessment_mode))
            lines.append("")
        return "\n".join(lines).strip()

    def _apply_self_check(self, task_text: str, response: str, execution: Mapping[str, Any]) -> str:
        plan = execution["plan"]
        artifact = execution["artifact"]
        metadata = execution["metadata"]
        classification = execution["classification"]
        route = execution["route"]

        check = self.self_check.validate_response(
            task_text=task_text,
            response=response,
            plan=plan,
            metadata={
                **dict(metadata),
                "artifact_required": getattr(artifact, "required", False),
            },
        )

        execution["self_check"] = check
        if check.passed:
            return response

        fallback_lines = [
            "AegisForge finalized the task using a safety-preserving fallback route.",
            "",
            f"Track: {classification.track_guess}",
            f"Assessment mode: {execution.get('assessment_mode', 'defender')}",
            f"Scenario family: {execution.get('scenario_family', 'general')}",
            f"Adapter: {route.adapter_name}",
            f"Risk: {classification.risk}",
            "",
            "Self-check issues:",
        ]
        for issue in check.issues:
            fallback_lines.append(f"- {issue.code}: {issue.message}")
        if check.suggested_fix:
            fallback_lines.append("")
            fallback_lines.append(check.suggested_fix)
        return "\n".join(fallback_lines)

    def _build_trace(self, execution: Mapping[str, Any]) -> dict[str, Any]:
        trace = {
            "mode": "integrated",
            "turn": self.turns,
            "assessment_mode": execution.get("assessment_mode"),
            "scenario_family": execution.get("scenario_family"),
            "classification": self._normalize_for_json(execution.get("classification")),
            "route": self._normalize_for_json(execution.get("route")),
            "role": self._normalize_for_json(execution.get("role")),
            "artifact": self._normalize_for_json(execution.get("artifact")),
            "plan": self._normalize_for_json(execution.get("plan")),
            "budget_state": self._normalize_for_json(execution.get("budget_state")),
            "policy_context": self._normalize_for_json(execution.get("policy_context")),
            "self_check": self._normalize_for_json(execution.get("self_check")),
        }
        return trace

    def _build_help_response(self) -> str:
        return (
            "AegisForge runtime is active.\n\n"
            "Public path:\n"
            "Dockerfile -> run.sh -> src/aegisforge/a2a_server.py -> Executor -> AegisForgeAgent\n\n"
            "Integrated internal path:\n"
            "classification -> budget -> routing -> role/artifact policy -> planning -> self-check.\n\n"
            "Supported runtime tracks:\n"
            "- mcu (Minecraft Benchmark / MCU-AgentBeats)\n"
            "- tau2\n"
            "- openenv\n"
            "- security"
        )

    def _build_empty_response(self) -> str:
        return (
            "AegisForge received an empty message.\n\n"
            "The Purple runtime is alive, but it needs a non-empty task or metadata payload to classify, plan, and route safely."
        )

    def _normalize_metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        normalized = {str(k): v for k, v in dict(metadata).items()}
        if "track" in normalized and "track_hint" not in normalized:
            normalized["track_hint"] = normalized["track"]
        if "arena" in normalized and "track_hint" not in normalized:
            normalized["track_hint"] = normalized["arena"]
        normalized["track_hint"] = self._normalize_track(normalized.get("track_hint"))
        normalized["assessment_mode"] = self._normalize_assessment_mode(normalized)
        normalized["scenario_family"] = self._normalize_scenario_family(normalized)
        return normalized

    def _expand_task_for_track(self, task_text: str, metadata: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        expanded_text = task_text
        normalized = dict(metadata)

        if normalized.get("track_hint") == "mcu" and self.mcu_adapter is not None:
            payload = self._extract_payload(normalized)
            if payload:
                try:
                    runtime_context = self.mcu_adapter.build_runtime_context(payload)
                    normalized = self._merge_runtime_context(normalized, runtime_context)
                    fragments = [expanded_text]
                    for key in ("goal", "prompt", "skill", "scenario_family", "assessment_mode"):
                        value = normalized.get(key)
                        if isinstance(value, str) and value.strip():
                            fragments.append(str(value))
                    expected_action = normalized.get("expected_action")
                    if isinstance(expected_action, Mapping) and expected_action:
                        fragments.append(json.dumps(dict(expected_action), ensure_ascii=False))
                    knowledge_artifact = normalized.get("knowledge_artifact")
                    if isinstance(knowledge_artifact, Mapping) and knowledge_artifact:
                        fragments.append(json.dumps(dict(knowledge_artifact), ensure_ascii=False))
                    expanded_text = "\n".join(part for part in fragments if part).strip()
                except Exception:
                    pass

        return expanded_text, normalized

    def _normalize_classification(self, classification: Any, task_text: str, metadata: Mapping[str, Any]) -> Any:
        normalized_track = self._normalize_track(metadata.get("track_hint") or getattr(classification, "track_guess", "openenv"))
        normalized_risk = getattr(classification, "risk", "low")
        lowered = task_text.lower()

        if any(re.search(pattern, lowered) for pattern in HIGH_RISK_PATTERNS):
            normalized_risk = "high"
        elif metadata.get("knowledge_decision", {}).get("source_risk") == "high":
            normalized_risk = "high"
        elif getattr(classification, "risk", "low") not in {"medium", "high"} and (
            "security" in lowered or normalized_track == "mcu"
        ):
            normalized_risk = "medium"

        updates: dict[str, Any] = {}
        if getattr(classification, "track_guess", None) != normalized_track:
            updates["track_guess"] = normalized_track
        if getattr(classification, "risk", None) != normalized_risk:
            updates["risk"] = normalized_risk
        return replace(classification, **updates) if updates else classification

    def _override_route_for_mode(self, route: Any, metadata: Mapping[str, Any]) -> Any:
        assessment_mode = str(metadata.get("assessment_mode", "defender"))
        scenario_family = str(metadata.get("scenario_family", "general"))
        track = str(metadata.get("track_hint", getattr(route, "track", "openenv")))
        updates: dict[str, Any] = {}

        if track == "mcu":
            if assessment_mode == "attacker":
                updates["prompt_profile"] = "mcu_attacker"
                updates["policy_profile"] = "aggressive_but_bounded"
            else:
                updates["prompt_profile"] = "mcu_defender"
                updates["policy_profile"] = "knowledge_hardening"

            if scenario_family == "wikiwiper" and getattr(route, "tool_mode", "allow") == "allow":
                updates["tool_mode"] = "guided"

            reasons = list(getattr(route, "reasons", []))
            reasons.append(f"Assessment mode applied: {assessment_mode}.")
            reasons.append(f"Scenario family applied: {scenario_family}.")
            updates["reasons"] = reasons

        return replace(route, **updates) if updates else route

    def _requested_format(self, metadata: Mapping[str, Any]) -> str | None:
        for key in ("requested_format", "format", "artifact_format", "output_format"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return None

    def _normalize_track(self, track: str | None) -> str:
        if not track:
            return "openenv"
        return TRACK_ALIASES.get(track.strip().lower(), track.strip().lower())

    def _normalize_assessment_mode(self, metadata: Mapping[str, Any]) -> str:
        for key in ("assessment_mode", "mode", "role"):
            value = metadata.get(key)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"attacker", "attack", "red", "offense", "offensive"}:
                    return "attacker"
                if lowered in {"defender", "defense", "blue", "safe", "guardian"}:
                    return "defender"
        request = metadata.get("assessment_request")
        if isinstance(request, Mapping):
            for key in ("mode", "role"):
                value = request.get(key)
                if isinstance(value, str) and value.strip().lower() in {"attacker", "defender"}:
                    return value.strip().lower()
        return "defender"

    def _normalize_scenario_family(self, metadata: Mapping[str, Any]) -> str:
        for key in ("scenario_family", "scenario", "family"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                lowered = value.strip().lower()
                return SCENARIO_ALIASES.get(lowered, lowered)
        return "wikiwiper" if self._normalize_track(metadata.get("track_hint")) == "mcu" else "general"

    def _extract_metadata(self, message: Message) -> dict[str, Any]:
        metadata = getattr(message, "metadata", None)
        if isinstance(metadata, Mapping):
            return dict(metadata)
        return {}

    def _extract_payload(self, metadata: Mapping[str, Any]) -> dict[str, Any] | None:
        for key in ("mcu_payload", "payload", "scenario_payload"):
            value = metadata.get(key)
            if isinstance(value, Mapping):
                return dict(value)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except Exception:
                    continue
                if isinstance(parsed, Mapping):
                    return dict(parsed)
        return None

    def _merge_runtime_context(self, metadata: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
        merged = dict(metadata)
        for key, value in runtime_context.items():
            if key == "metadata" and isinstance(value, Mapping):
                merged.setdefault("adapter_metadata", {}).update(dict(value))
            else:
                merged[key] = value
        merged["track_hint"] = self._normalize_track(merged.get("track_hint"))
        merged["assessment_mode"] = self._normalize_assessment_mode(merged)
        merged["scenario_family"] = self._normalize_scenario_family(merged)
        return merged

    def _map_context(self, *, task_text: str, metadata: Mapping[str, Any], classification: Any) -> dict[str, Any]:
        mapper = self.context_mapper
        for name in ("map", "build", "to_prompt_context"):
            fn = getattr(mapper, name, None)
            if callable(fn):
                try:
                    result = fn(task_text=task_text, metadata=metadata, classification=classification)
                except TypeError:
                    try:
                        result = fn(task_text, metadata, classification)
                    except Exception:
                        continue
                except Exception:
                    continue
                if isinstance(result, Mapping):
                    return dict(result)
        return NullContextMapper().map(task_text=task_text, metadata=metadata, classification=classification)

    def _bridge_policy(
        self,
        *,
        classification: Any,
        role_policy: Any,
        artifact_policy: Any,
        route: Any,
        plan: Any,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        bridge = self.policy_bridge
        for name in ("apply", "build", "merge"):
            fn = getattr(bridge, name, None)
            if callable(fn):
                try:
                    result = fn(
                        classification=classification,
                        role_policy=role_policy,
                        artifact_policy=artifact_policy,
                        route=route,
                        plan=plan,
                        metadata=metadata,
                    )
                except TypeError:
                    try:
                        result = fn(classification, role_policy, artifact_policy, route, plan, metadata)
                    except Exception:
                        continue
                except Exception:
                    continue
                if isinstance(result, Mapping):
                    return dict(result)
        return NullPolicyBridge().apply(
            classification=classification,
            role_policy=role_policy,
            artifact_policy=artifact_policy,
            route=route,
            plan=plan,
            metadata=metadata,
        )

    def _load_prompt_bundle(self, task_text: str, execution: Mapping[str, Any]) -> dict[str, Any]:
        loader = self.prompt_loader
        for name in ("build", "compose", "render", "load"):
            fn = getattr(loader, name, None)
            if callable(fn):
                try:
                    result = fn(task_text=task_text, execution_bundle=execution)
                except TypeError:
                    try:
                        result = fn(task_text, execution)
                    except Exception:
                        continue
                except Exception:
                    continue
                if isinstance(result, Mapping):
                    return dict(result)
        return NullPromptLoader().build(task_text=task_text, execution_bundle=execution)

    def _build_prompt_loader(self) -> Any:
        for module_path, class_name in (
            (".prompts.prompt_manager", "PromptManager"),
            (".prompts.prompt_manager", "PromptLoader"),
            (".prompt_manager", "PromptManager"),
            (".prompt_manager", "PromptLoader"),
            (".utils.prompt_loader", "PromptLoader"),
        ):
            cls = _safe_import(module_path, class_name)
            if cls is not None:
                try:
                    return cls()
                except Exception:
                    continue
        return NullPromptLoader()

    def _build_context_mapper(self) -> Any:
        for module_path in (".security.context_mapper", ".context_mapper"):
            cls = _safe_import(module_path, "ContextMapper")
            if cls is not None:
                try:
                    return cls()
                except Exception:
                    continue
        return NullContextMapper()

    def _build_policy_bridge(self) -> Any:
        for module_path in (".security.policy_bridge", ".policy_bridge"):
            cls = _safe_import(module_path, "PolicyBridge")
            if cls is not None:
                try:
                    return cls()
                except Exception:
                    continue
        return NullPolicyBridge()

    def _build_mcu_adapter(self) -> Any | None:
        cls = _safe_import(".adapters.mcu.adapter", "MCUAdapter")
        if cls is not None:
            try:
                return cls()
            except Exception:
                return None
        return None

    def _section_content(
        self,
        section: str,
        task_text: str,
        classification: Any,
        route: Any,
        role: Any,
        plan: Any,
        metadata: Mapping[str, Any],
        assessment_mode: str,
    ) -> str:
        section = section.lower()
        knowledge_decision = metadata.get("knowledge_decision") or {}
        if section == "assessment":
            return (
                f"Treat the task as {classification.track_guess} / {classification.task_type} "
                f"with {classification.risk} risk, {assessment_mode} mode, and a {role.posture} posture."
            )
        if section == "risk":
            return f"Current risk level is {classification.risk}; use adapter {route.adapter_name} in {route.tool_mode} mode."
        if section in {"recommended_action", "final"}:
            return f"Execute the plan goal: {plan.goal}"
        if section == "evidence":
            return "Evidence should stay limited to the minimum information needed to justify the final answer."
        if section == "summary":
            return f"Task excerpt: {task_text[:220]}"
        if section == "goal":
            return plan.goal
        if section == "actions":
            return ", ".join(step.name for step in getattr(plan, "steps", []))
        if section == "status":
            return "planned"
        if section == "knowledge_assessment":
            return (
                f"source_risk={knowledge_decision.get('source_risk', 'unknown')}; "
                f"should_use_source={knowledge_decision.get('should_use_source', 'unknown')}"
            )
        if section == "action_plan":
            return ", ".join(step.name for step in getattr(plan, "steps", []))
        if section == "confidence":
            return str(knowledge_decision.get("source_confidence", "bounded"))
        if section == "source_handling":
            return str(knowledge_decision.get("rationale", "Handle external knowledge conservatively."))
        if section == "target_dependency":
            return "Exploit reliance on a trusted wiki-like guidance source without leaving benchmark-visible channels."
        if section == "payload":
            return "Introduce bounded source-level manipulation that biases recipe or planning guidance."
        if section == "expected_deviation":
            return "Target should follow poisoned knowledge, waste resources, or miss the intended crafting plan."
        return f"Section prepared for {section}."

    def _build_debug_summary(self, trace: Mapping[str, Any]) -> str:
        lines = [
            "AegisForge debug summary",
            "",
            f"turn={trace.get('turn')}",
            f"mode={trace.get('mode')}",
            f"assessment_mode={trace.get('assessment_mode', 'n/a')}",
            f"scenario_family={trace.get('scenario_family', 'n/a')}",
        ]
        classification = trace.get("classification") or {}
        route = trace.get("route") or {}
        lines.append(f"track={classification.get('track_guess', 'n/a')}")
        lines.append(f"risk={classification.get('risk', 'n/a')}")
        lines.append(f"adapter={route.get('adapter_name', 'n/a')}")
        return "\n".join(lines)

    @staticmethod
    def _sanitize_text(value: str) -> str:
        if not isinstance(value, str):
            return ""
        return value.replace("\x00", "").strip()

    @staticmethod
    def _normalize_for_json(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [AegisForgeAgent._normalize_for_json(item) for item in value]
        if isinstance(value, tuple):
            return [AegisForgeAgent._normalize_for_json(item) for item in value]
        if isinstance(value, Mapping):
            return {str(k): AegisForgeAgent._normalize_for_json(v) for k, v in value.items()}
        if hasattr(value, "as_dict") and callable(value.as_dict):
            try:
                return AegisForgeAgent._normalize_for_json(value.as_dict())
            except Exception:
                pass
        if is_dataclass(value):
            return AegisForgeAgent._normalize_for_json(asdict(value))
        if hasattr(value, "__dict__"):
            return AegisForgeAgent._normalize_for_json(vars(value))
        return str(value)

    @classmethod
    def _to_json(cls, payload: Mapping[str, Any]) -> str:
        return json.dumps(cls._normalize_for_json(dict(payload)), indent=2, ensure_ascii=False)
