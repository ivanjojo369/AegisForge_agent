from __future__ import annotations

"""Executor that binds A2A tasks to AegisForgeAgent.

Pi-Bench-specific revision:
- preserves the bounded one-agent-per-context model
- keeps non-Pi-Bench metadata-aware cache keys
- stabilizes Pi-Bench cache keys so bootstrap/context is not lost between turns
- injects Pi-Bench policy-bootstrap metadata into the incoming A2A message when
  the request/card indicates the pibench/agent-safety track
- does not touch API keys, secrets, authentication, or external network settings
"""

from collections import OrderedDict
import hashlib
import json
import os
from typing import Any, Mapping

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import InvalidRequestError, TaskState, UnsupportedOperationError
from a2a.utils import get_message_text, new_agent_text_message, new_task
from a2a.utils.errors import ServerError

from .agent import AegisForgeAgent


SELECTED_OPPONENT_TRACKS = (
    "mcu",
    "officeqa",
    "crmarena",
    "fieldworkarena",
    "maizebargain",
    "tau2",
    "osworld",
    "pibench",
    "cybergym",
    "netarena",
)

TRACK_ALIASES = {
    "mcu-minecraft": "mcu",
    "mcu_minecraft": "mcu",
    "minecraft": "mcu",
    "minecraft benchmark": "mcu",
    "minecraft-benchmark": "mcu",
    "mcu-agentbeats": "mcu",
    "mcu_agentbeats": "mcu",
    "office-qa": "officeqa",
    "office_qa": "officeqa",
    "finance": "officeqa",
    "crmarenapro": "crmarena",
    "entropic-crmarenapro": "crmarena",
    "business-process": "crmarena",
    "business_process": "crmarena",
    "fieldworkarena-greenagent": "fieldworkarena",
    "fieldworkarena_greenagent": "fieldworkarena",
    "research": "fieldworkarena",
    "maize-bargain": "maizebargain",
    "maize_bargain": "maizebargain",
    "tutorial-agent-beats-comp": "maizebargain",
    "multi-agent": "maizebargain",
    "multi_agent": "maizebargain",
    "tau2-agentbeats": "tau2",
    "tau2_agentbeats": "tau2",
    "tau²": "tau2",
    "osworld-green": "osworld",
    "computer-use": "osworld",
    "computer_use": "osworld",
    "pi-bench": "pibench",
    "pi_bench": "pibench",
    "pibench": "pibench",
    "agent-safety": "pibench",
    "agent_safety": "pibench",
    "agent safety": "pibench",
    "policy-bootstrap": "pibench",
    "record-decision": "pibench",
    "record_decision": "pibench",
    "cybergym-green": "cybergym",
    "cybersecurity": "cybergym",
    "cybersecurity-agent": "cybergym",
    "cybersecurity_agent": "cybergym",
    "net-arena": "netarena",
    "net_arena": "netarena",
    "coding-agent": "netarena",
    "coding_agent": "netarena",
}

PI_BENCH_POLICY_BOOTSTRAP_URN = "urn:pi-bench:policy-bootstrap:v1"
PI_BENCH_CACHE_SUFFIX = "pibench::policy-bootstrap-v1"
PI_BENCH_DECISIONS = ("ALLOW", "ALLOW-CONDITIONAL", "DENY", "ESCALATE")
PI_BENCH_METADATA_MARKERS = (
    "pibench",
    "pi-bench",
    "pi_bench",
    "agent-safety",
    "agent_safety",
    "agent safety",
    "policy-bootstrap",
    "policy_bootstrap",
    "record_decision",
    "record-decision",
    PI_BENCH_POLICY_BOOTSTRAP_URN,
)

PI_BENCH_BOOTSTRAP_METADATA: dict[str, Any] = {
    "track_hint": "pibench",
    "track": "pibench",
    "benchmark": "Pi-Bench",
    "agentbeats_category": "agent_safety",
    "pi_bench_policy_bootstrap": {
        "uri": PI_BENCH_POLICY_BOOTSTRAP_URN,
        "decision_channel": "assistant.tool_calls",
        "required_tool_calls": ["record_decision"],
        "valid_decisions": list(PI_BENCH_DECISIONS),
        "record_decision_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "decision": {"type": "string", "enum": list(PI_BENCH_DECISIONS)},
                "rationale": {"type": "string", "minLength": 1},
            },
            "required": ["decision", "rationale"],
        },
    },
    "a2a_decision_contract": {
        "canonical_decision_source": "assistant.tool_calls.record_decision.arguments",
        "textpart_is_not_decision": True,
        "datapart_is_not_decision": True,
        "missing_decision_error_to_avoid": "MISSING_DECISION",
    },
}


TERMINAL_STATES = {
    TaskState.completed,
    TaskState.canceled,
    TaskState.failed,
    TaskState.rejected,
}


def _max_cached_agents() -> int:
    raw = os.getenv("AEGISFORGE_MAX_CONTEXT_AGENTS", "128")
    try:
        return max(int(raw), 8)
    except ValueError:
        return 128


class Executor(AgentExecutor):
    def __init__(self) -> None:
        self._agents: OrderedDict[str, AegisForgeAgent] = OrderedDict()
        self._max_cached_agents = _max_cached_agents()
        self._executions = 0
        self._pibench_requests = 0

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = self._require_message(context)
        task = await self._get_or_create_task(context, message, event_queue)
        context_id = task.context_id

        metadata = self._extract_message_metadata(message)
        text = self._safe_message_text(message)
        is_pibench = self._is_pi_bench_request(metadata, text)

        if is_pibench:
            self._pibench_requests += 1
            metadata = self._attach_pi_bench_bootstrap_metadata(
                message,
                metadata=metadata,
                context_id=context_id,
                task_id=getattr(task, "id", ""),
            )

        cache_key = self._build_cache_key(context_id, metadata, text=text, is_pibench=is_pibench)

        agent = self._get_or_create_agent(cache_key)
        updater = TaskUpdater(event_queue, task.id, context_id)

        await updater.start_work()

        try:
            self._executions += 1
            await agent.run(message, updater)
            if not self._terminal_reached(updater):
                await updater.complete()
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            if not self._terminal_reached(updater):
                await updater.failed(
                    new_agent_text_message(
                        self._safe_error_message(exc),
                        context_id=context_id,
                        task_id=task.id,
                    )
                )
        finally:
            self._touch_agent(cache_key)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())

    def snapshot(self) -> dict[str, Any]:
        return {
            "class": self.__class__.__name__,
            "max_cached_agents": self._max_cached_agents,
            "cached_agents": len(self._agents),
            "executions": self._executions,
            "pibench_requests": self._pibench_requests,
            "pibench_cache_suffix": PI_BENCH_CACHE_SUFFIX,
            "selected_opponent_tracks": list(SELECTED_OPPONENT_TRACKS),
            "track_alias_note": "mcu-minecraft is normalized to mcu; pi-bench/agent-safety is normalized to pibench.",
            "cache_keys": list(self._agents.keys())[:8],
        }

    def _require_message(self, context: RequestContext):
        message = getattr(context, "message", None)
        if not message:
            raise ServerError(error=InvalidRequestError(message="Missing message in request."))
        return message

    async def _get_or_create_task(self, context: RequestContext, message: Any, event_queue: EventQueue):
        task = self._ensure_active_task(context)
        if task is None:
            task = new_task(message)
            await event_queue.enqueue_event(task)
        return task

    def _ensure_active_task(self, context: RequestContext):
        task = getattr(context, "current_task", None)
        if task and task.status.state in TERMINAL_STATES:
            raise ServerError(
                error=InvalidRequestError(
                    message=f"Task {task.id} already processed (state: {task.status.state})."
                )
            )
        return task

    def _get_or_create_agent(self, cache_key: str) -> AegisForgeAgent:
        agent = self._agents.get(cache_key)
        if agent is not None:
            self._touch_agent(cache_key)
            return agent

        agent = AegisForgeAgent()
        self._agents[cache_key] = agent
        self._evict_if_needed()
        return agent

    def _touch_agent(self, cache_key: str) -> None:
        if cache_key in self._agents:
            self._agents.move_to_end(cache_key)

    def _evict_if_needed(self) -> None:
        while len(self._agents) > self._max_cached_agents:
            self._agents.popitem(last=False)

    def _build_cache_key(
        self,
        context_id: str,
        metadata: Any,
        *,
        text: str = "",
        is_pibench: bool = False,
    ) -> str:
        """Build an agent cache key.

        Non-Pi-Bench keeps the prior metadata-aware digest because several tracks
        can multiplex attacker/defender or scenario modes. Pi-Bench is deliberately
        stable per context so the record_decision bootstrap/session state cannot be
        lost when turn metadata changes between bootstrap and decision turns.
        """
        safe_context_id = str(context_id or "default-context")

        if is_pibench or self._is_pi_bench_request(metadata, text):
            return f"{safe_context_id}::{PI_BENCH_CACHE_SUFFIX}"

        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        track = self._normalize_track(metadata.get("track_hint") or metadata.get("track") or metadata.get("arena"))
        mode = self._normalize(metadata.get("assessment_mode") or metadata.get("mode") or metadata.get("role"))
        family = self._normalize(metadata.get("scenario_family") or metadata.get("scenario") or metadata.get("family"))
        payload = metadata.get("mcu_payload") or metadata.get("payload") or metadata.get("scenario_payload")

        digest_source: dict[str, Any] = {
            "track": track,
            "mode": mode,
            "family": family,
        }
        if isinstance(payload, Mapping):
            task = payload.get("task") if isinstance(payload.get("task"), Mapping) else {}
            digest_source["task_id"] = task.get("id")
            digest_source["goal"] = task.get("goal")

        digest = hashlib.sha1(
            json.dumps(digest_source, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:12]
        return f"{safe_context_id}::{digest}"

    def _extract_message_metadata(self, message: Any) -> dict[str, Any]:
        metadata = getattr(message, "metadata", None)
        if isinstance(metadata, Mapping):
            return dict(metadata)
        return {}

    def _attach_pi_bench_bootstrap_metadata(
        self,
        message: Any,
        *,
        metadata: Mapping[str, Any],
        context_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        merged = self._deep_merge_dicts(dict(PI_BENCH_BOOTSTRAP_METADATA), dict(metadata))
        merged.setdefault("track_hint", "pibench")
        merged.setdefault("track", "pibench")
        merged.setdefault("context_id", context_id)
        merged.setdefault("task_id", task_id)
        merged.setdefault(
            "aegisforge_executor",
            {
                "cache_policy": "stable_per_context_for_pibench",
                "cache_suffix": PI_BENCH_CACHE_SUFFIX,
                "pi_bench_policy_bootstrap": PI_BENCH_POLICY_BOOTSTRAP_URN,
            },
        )

        # Best effort: several A2A SDK message classes are pydantic models, but in
        # current runtimes metadata is normally mutable. If assignment is blocked,
        # the cache still stays stable; only metadata injection is skipped.
        try:
            setattr(message, "metadata", merged)
        except Exception:
            try:
                vars(message)["metadata"] = merged
            except Exception:
                pass
        return merged

    def _is_pi_bench_request(self, metadata: Any, text: str = "") -> bool:
        haystacks: list[str] = []

        if isinstance(metadata, Mapping):
            for key, value in metadata.items():
                haystacks.append(str(key))
                if isinstance(value, (str, int, float, bool)):
                    haystacks.append(str(value))
                elif isinstance(value, Mapping):
                    haystacks.extend(str(k) for k in value.keys())
                    for nested_value in value.values():
                        if isinstance(nested_value, (str, int, float, bool)):
                            haystacks.append(str(nested_value))
                elif isinstance(value, (list, tuple, set)):
                    haystacks.extend(str(item) for item in list(value)[:20] if not isinstance(item, Mapping))

        if text:
            haystacks.append(text[:5000])

        combined = " ".join(haystacks).lower().replace("_", "-")
        if any(marker.replace("_", "-").lower() in combined for marker in PI_BENCH_METADATA_MARKERS):
            return True

        track = ""
        if isinstance(metadata, Mapping):
            track = self._normalize_track(
                metadata.get("track_hint")
                or metadata.get("track")
                or metadata.get("arena")
                or metadata.get("benchmark")
                or metadata.get("category")
            )
        return track == "pibench"

    @staticmethod
    def _safe_message_text(message: Any) -> str:
        try:
            return str(get_message_text(message) or "")
        except Exception:
            return ""

    @classmethod
    def _deep_merge_dicts(cls, base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in overlay.items():
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, Mapping):
                merged[key] = cls._deep_merge_dicts(existing, dict(value))
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _terminal_reached(updater: TaskUpdater) -> bool:
        return bool(getattr(updater, "_terminal_state_reached", False))

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        return f"Agent error: {text[:500]}"

    @staticmethod
    def _normalize_track(value: Any) -> str:
        if value is None:
            return ""
        raw = str(value).strip().lower().replace("_", "-")
        return TRACK_ALIASES.get(raw, raw)

    @staticmethod
    def _normalize(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().lower()
