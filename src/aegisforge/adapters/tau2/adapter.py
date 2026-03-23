from __future__ import annotations

"""τ² adapter for integrating tau2-style domains into AegisForge.

This module provides a lightweight, reusable translation layer between
AegisForge's runtime/evaluation flow and domain payloads modeled after τ².
Within the current Phase 2 · Purple layout, the adapter is intentionally
implemented as a Purple-native capability layer rather than as a copy of the
upstream benchmark framework.

Today the adapter includes first-class handling for the ``quipu_lab`` domain,
while preserving a generic execution path for future tau2-style domains. The
core responsibilities are to:

- normalize incoming adapter requests,
- inject provider/domain defaults from configuration,
- validate domain payloads when strict structure is available,
- translate inputs into a stable adapter-facing envelope, and
- return structured execution results that can be consumed by runtime,
  tests, and local evaluation tracks.
"""

from dataclasses import dataclass
from typing import Any

from .config import Tau2AdapterConfig
from .quipu_lab import (
    build_minimal_result,
    build_sample_task,
    execute_tool,
    get_policy_excerpt,
)
from .quipu_lab.policy import validate_task_payload
from .quipu_lab.schemas import QuipuLabTask


@dataclass(slots=True)
class Tau2AdapterResult:
    """Normalized result envelope returned by ``Tau2Adapter``.

    The adapter always returns a stable structure with provider/domain identity
    and the raw payload produced during translation or execution.
    """

    ok: bool
    provider: str
    domain_name: str
    payload: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the adapter result into a plain dictionary."""
        data = {
            "ok": self.ok,
            "provider": self.provider,
            "domain_name": self.domain_name,
            "payload": self.payload,
        }
        if self.error:
            data["error"] = self.error
        return data


class Tau2Adapter:
    """Reusable integration layer for τ²-style domains inside AegisForge.

    The adapter exposes a small contract used by tests, local evaluation, and
    future runtime wiring:

    - ``validate_request`` ensures a structurally valid domain payload,
    - ``translate_request`` builds a stable adapter envelope,
    - ``execute`` performs a lightweight local execution path, and
    - ``translate_response`` converts raw execution data into a stable result.

    Although ``quipu_lab`` is the first fully integrated domain, the adapter is
    intentionally shaped as a general pattern for additional tau2-style domains.
    """

    provider_name = "tau2"

    def __init__(self, config: Tau2AdapterConfig | None = None) -> None:
        self.config = config or Tau2AdapterConfig.from_env()

    @property
    def enabled(self) -> bool:
        """Expose whether the adapter is enabled by configuration."""
        return self.config.enabled

    def status(self) -> dict[str, Any]:
        """Return a compact status payload for diagnostics and tests."""
        return {
            "provider": self.provider_name,
            "enabled": self.config.enabled,
            "base_url": self.config.base_url,
            "domain_name": self.config.domain_name,
            "timeout_seconds": self.config.timeout_seconds,
            "strict_mode": self.config.strict_mode,
        }

    def _extract_task_payload(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Extract the task payload from either ``request_data['task']`` or the root.

        This keeps the adapter tolerant of both task-centric and flatter request
        shapes during local integration.
        """
        task = request_data.get("task")
        if isinstance(task, dict):
            return dict(task)
        return dict(request_data)

    def _default_tool_args(self, tool_name: str, task: QuipuLabTask) -> dict[str, Any]:
        """Provide small deterministic defaults for local tool execution.

        These defaults are intentionally lightweight: they exist to make local
        adapter execution and smoke tests reproducible without pretending to be a
        full external tool runtime.
        """
        if tool_name == "list_available_assets":
            return {"kind": "general"}
        if tool_name == "draft_structured_plan":
            return {"goal": task.user_goal, "max_steps": 3}
        if tool_name == "lookup_lab_note":
            return {"query": task.title}
        return {}

    def validate_request(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Normalize and validate a tau2-style adapter request.

        The method injects provider/domain defaults and, for ``quipu_lab``,
        ensures a complete task payload plus default ``turns`` and ``tools``
        fields required by downstream evaluation helpers.
        """
        if not isinstance(request_data, dict):
            raise ValueError("Tau2 adapter request must be a dict.")

        normalized = dict(request_data)

        if not normalized.get("domain_name"):
            normalized["domain_name"] = self.config.domain_name

        if "provider" not in normalized:
            normalized["provider"] = self.provider_name

        if normalized["domain_name"] == "quipu_lab":
            task_payload = self._extract_task_payload(normalized)

            if not task_payload.get("task_id"):
                sample_task = build_sample_task().to_dict()
                task_payload = {**sample_task, **task_payload}

            errors = validate_task_payload(task_payload)
            if errors:
                raise ValueError(
                    "Tau2 quipu_lab request is invalid: " + "; ".join(errors)
                )

            normalized["task"] = task_payload
            normalized.setdefault(
                "turns",
                list(task_payload.get("conversation_context", [])),
            )
            normalized.setdefault(
                "tools",
                list(task_payload.get("required_tools", [])),
            )

        return normalized

    def translate_request(self, request_data: dict[str, Any]) -> dict[str, Any]:
        """Translate a validated request into the adapter execution envelope."""
        normalized = self.validate_request(request_data)

        translated = {
            "provider": self.provider_name,
            "base_url": self.config.base_url,
            "domain_name": normalized["domain_name"],
            "timeout_seconds": self.config.timeout_seconds,
            "strict_mode": self.config.strict_mode,
            "input": normalized,
        }

        if normalized["domain_name"] == "quipu_lab":
            translated["task"] = dict(normalized["task"])
            translated["policy_excerpt"] = get_policy_excerpt()

        return translated

    def translate_response(self, response_data: dict[str, Any]) -> Tau2AdapterResult:
        """Convert raw execution output into a stable adapter result object."""
        if not isinstance(response_data, dict):
            raise ValueError("Tau2 adapter response must be a dict.")

        ok = bool(response_data.get("ok", True))
        error = response_data.get("error")
        if error is not None and not isinstance(error, str):
            error = str(error)

        domain_name = str(response_data.get("domain_name") or self.config.domain_name)

        return Tau2AdapterResult(
            ok=ok,
            provider=self.provider_name,
            domain_name=domain_name,
            payload=dict(response_data),
            error=error,
        )

    def execute(self, request_data: dict[str, Any]) -> Tau2AdapterResult:
        """Execute a lightweight local adapter flow for a tau2-style request.

        For ``quipu_lab`` this method builds a structured local result with tool
        outputs and a short execution trace. For other domains, it returns a
        generic echo-style response so the adapter remains extensible without
        hard-coding full execution logic for every future domain up front.
        """
        if not self.enabled:
            return self.translate_response(
                {
                    "ok": False,
                    "provider": self.provider_name,
                    "domain_name": self.config.domain_name,
                    "error": "Tau2 adapter is disabled via configuration.",
                }
            )

        translated = self.translate_request(request_data)
        domain_name = translated["domain_name"]

        # Keep a generic path available so the adapter can scale beyond the
        # first fully integrated quipu_lab domain.
        if domain_name != "quipu_lab":
            structured_response = {
                "ok": True,
                "provider": self.provider_name,
                "domain_name": domain_name,
                "echo": translated["input"],
            }
            return self.translate_response(structured_response)

        task = QuipuLabTask.from_dict(translated["task"])
        tool_results: list[dict[str, Any]] = []

        for tool_name in task.required_tools:
            tool_results.append(
                execute_tool(tool_name, self._default_tool_args(tool_name, task))
            )

        result = build_minimal_result(task)

        execution_response = {
            "ok": True,
            "provider": self.provider_name,
            "domain_name": domain_name,
            "task": task.to_dict(),
            "result": result,
            "tool_results": tool_results,
            "policy_excerpt": translated["policy_excerpt"],
            "trace": [
                {
                    "step": 1,
                    "event_type": "task_loaded",
                    "content": f"Loaded task {task.task_id} for domain {domain_name}.",
                },
                {
                    "step": 2,
                    "event_type": "tools_executed",
                    "content": f"Executed {len(tool_results)} required tool(s).",
                },
                {
                    "step": 3,
                    "event_type": "result_built",
                    "content": "Built structured quipu_lab τ²-style result.",
                },
            ],
        }
        return self.translate_response(execution_response)
