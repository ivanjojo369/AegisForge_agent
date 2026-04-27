from __future__ import annotations

"""Executor that binds A2A tasks to AegisForgeAgent.

This revision keeps the bounded one-agent-per-context model and improves:
- metadata-aware context caching for dual-mode attacker/defender flows
- bounded cache eviction for long-lived runtimes
- lightweight executor snapshotting for doctor/debug modes
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
from a2a.utils import new_agent_text_message, new_task
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
    "agent-safety": "pibench",
    "agent_safety": "pibench",
    "cybergym-green": "cybergym",
    "cybersecurity": "cybergym",
    "cybersecurity-agent": "cybergym",
    "cybersecurity_agent": "cybergym",
    "net-arena": "netarena",
    "net_arena": "netarena",
    "coding-agent": "netarena",
    "coding_agent": "netarena",
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

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = self._require_message(context)
        task = await self._get_or_create_task(context, message, event_queue)
        context_id = task.context_id
        cache_key = self._build_cache_key(context_id, getattr(message, "metadata", None))

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
            "selected_opponent_tracks": list(SELECTED_OPPONENT_TRACKS),
            "track_alias_note": "mcu-minecraft is normalized to mcu.",
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

    def _build_cache_key(self, context_id: str, metadata: Any) -> str:
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        track = self._normalize_track(metadata.get("track_hint") or metadata.get("track") or metadata.get("arena"))
        mode = self._normalize(metadata.get("assessment_mode") or metadata.get("mode") or metadata.get("role"))
        family = self._normalize(metadata.get("scenario_family") or metadata.get("scenario") or metadata.get("family"))
        payload = metadata.get("mcu_payload") or metadata.get("payload") or metadata.get("scenario_payload")

        digest_source = {
            "track": track,
            "mode": mode,
            "family": family,
        }
        if isinstance(payload, Mapping):
            task = payload.get("task") if isinstance(payload.get("task"), Mapping) else {}
            digest_source["task_id"] = task.get("id")
            digest_source["goal"] = task.get("goal")

        digest = hashlib.sha1(json.dumps(digest_source, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
        return f"{context_id}::{digest}"

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
