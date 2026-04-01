from __future__ import annotations

"""τ² adapter for integrating tau2-style domains into AegisForge.

This version keeps backward compatibility with the original single-task
``quipu_lab`` flow while adding first-class support for catalog-driven requests:

- explicit ``task`` payloads,
- ``task_id`` selection,
- ``task_ids`` batch selection,
- ``split``-based selection (for example ``base`` or ``smoke``), and
- graceful fallback to the sample task when no selection is provided.

The adapter preserves the old single-task response shape when exactly one task
is resolved, so existing runtime wiring and tests do not break unnecessarily.
When more than one task is resolved, the adapter returns a batch-oriented
payload with per-task results and a run-level summary.
"""

from dataclasses import dataclass
from typing import Any, Iterable

from .config import Tau2AdapterConfig
from .quipu_lab.policy import get_policy_excerpt, validate_task_payload
from .quipu_lab.schemas import QuipuLabTask
from .quipu_lab.tasks import build_minimal_result, build_sample_task
from .quipu_lab.tools import execute_tool

try:  # Catalog helpers are present in the upgraded tasks.py.
    from .quipu_lab.tasks import (  # type: ignore[attr-defined]
        get_smoke_tasks,
        get_task_by_id,
        get_tasks,
        iter_task_ids,
        validate_catalog,
    )
except Exception:  # pragma: no cover - backward compatibility for older tasks.py
    get_smoke_tasks = None
    get_task_by_id = None
    get_tasks = None
    iter_task_ids = None
    validate_catalog = None


@dataclass(slots=True)
class Tau2AdapterResult:
    """Normalized result envelope returned by ``Tau2Adapter``."""

    ok: bool
    provider: str
    domain_name: str
    payload: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
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
    """Reusable integration layer for τ²-style domains inside AegisForge."""

    provider_name = "tau2"

    def __init__(self, config: Tau2AdapterConfig | None = None) -> None:
        self.config = config or Tau2AdapterConfig.from_env()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def status(self) -> dict[str, Any]:
        catalog_errors: list[str] = []
        if callable(validate_catalog):
            try:
                catalog_errors = list(validate_catalog())
            except Exception:
                catalog_errors = ["catalog_validation_failed"]

        smoke_count = 0
        if callable(get_smoke_tasks):
            try:
                smoke_count = len(list(get_smoke_tasks()))
            except Exception:
                smoke_count = 0

        return {
            "provider": self.provider_name,
            "enabled": self.config.enabled,
            "base_url": self.config.base_url,
            "domain_name": self.config.domain_name,
            "timeout_seconds": self.config.timeout_seconds,
            "strict_mode": self.config.strict_mode,
            "supports_task_catalog": callable(get_tasks),
            "supports_batch_tasks": True,
            "smoke_task_count": smoke_count,
            "catalog_errors": catalog_errors,
        }

    def _extract_task_payload(self, request_data: dict[str, Any]) -> dict[str, Any]:
        task = request_data.get("task")
        if isinstance(task, dict):
            return dict(task)
        return dict(request_data)

    def _default_tool_args(self, tool_name: str, task: QuipuLabTask) -> dict[str, Any]:
        if tool_name == "list_available_assets":
            return {"kind": "general"}
        if tool_name == "draft_structured_plan":
            return {"goal": task.user_goal, "max_steps": 3}
        if tool_name == "lookup_lab_note":
            return {"query": task.title}
        return {}

    def _merge_with_sample_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Backfill missing required fields from the sample task.

        This preserves compatibility with older tests and request shapes that
        passed an explicit ``task`` object but omitted ``task_id`` or other
        required fields, expecting the adapter to inherit them from the sample.
        """
        sample = build_sample_task().to_dict()
        merged = dict(sample)
        merged.update(payload)

        # Merge metadata shallowly so callers can override only selected keys.
        sample_metadata = sample.get("metadata", {})
        payload_metadata = payload.get("metadata", {})
        if isinstance(sample_metadata, dict) or isinstance(payload_metadata, dict):
            merged["metadata"] = {
                **(sample_metadata if isinstance(sample_metadata, dict) else {}),
                **(payload_metadata if isinstance(payload_metadata, dict) else {}),
            }
        return merged

    def _coerce_task(self, payload: dict[str, Any]) -> QuipuLabTask:
        errors = validate_task_payload(payload)
        if errors:
            raise ValueError("Tau2 quipu_lab request is invalid: " + "; ".join(errors))
        return QuipuLabTask.from_dict(payload)

    def _load_catalog_task(self, task_id: str) -> QuipuLabTask:
        if callable(get_task_by_id):
            return get_task_by_id(task_id)

        sample = build_sample_task()
        if task_id == sample.task_id:
            return sample
        raise ValueError(f"Unknown quipu_lab task_id: {task_id}")

    def _load_catalog_split(self, split: str) -> list[QuipuLabTask]:
        normalized_split = (split or "base").strip().lower()

        if normalized_split == "smoke" and callable(get_smoke_tasks):
            return list(get_smoke_tasks())

        if callable(get_tasks):
            return list(get_tasks(normalized_split))

        # Backward-compatible fallback for older tasks.py files.
        return [build_sample_task()]

    def _dedupe_tasks(self, tasks: Iterable[QuipuLabTask]) -> list[QuipuLabTask]:
        ordered: list[QuipuLabTask] = []
        seen: set[str] = set()
        for task in tasks:
            if task.task_id in seen:
                continue
            ordered.append(task)
            seen.add(task.task_id)
        return ordered

    def _resolve_quipu_lab_tasks(self, normalized: dict[str, Any]) -> tuple[list[QuipuLabTask], dict[str, Any]]:
        selection: dict[str, Any] = {
            "mode": "fallback_sample",
            "requested_split": None,
            "requested_task_ids": [],
        }

        # Highest priority: fully explicit task payload.
        task_field = normalized.get("task")
        if isinstance(task_field, dict):
            # Backward compatibility: older callers/tests may omit task_id and
            # rely on the sample task to provide it.
            if not task_field.get("task_id"):
                task_field = self._merge_with_sample_task(task_field)
            task = self._coerce_task(task_field)
            selection["mode"] = "explicit_task"
            selection["requested_task_ids"] = [task.task_id]
            return [task], selection

        resolved: list[QuipuLabTask] = []

        task_ids_raw = normalized.get("task_ids")
        if isinstance(task_ids_raw, (list, tuple)):
            requested_ids = [str(item).strip() for item in task_ids_raw if str(item).strip()]
            if requested_ids:
                selection["mode"] = "task_ids"
                selection["requested_task_ids"] = list(requested_ids)
                resolved.extend(self._load_catalog_task(task_id) for task_id in requested_ids)

        task_id_raw = normalized.get("task_id")
        if isinstance(task_id_raw, str) and task_id_raw.strip():
            task_id = task_id_raw.strip()
            selection["mode"] = "task_id"
            selection["requested_task_ids"] = [task_id]
            resolved = [self._load_catalog_task(task_id)]

        split_raw = normalized.get("split") or normalized.get("task_split")
        if not resolved and isinstance(split_raw, str) and split_raw.strip():
            split = split_raw.strip()
            selection["mode"] = "split"
            selection["requested_split"] = split
            resolved = self._load_catalog_split(split)

        if not resolved:
            selection["mode"] = "fallback_sample"
            resolved = [build_sample_task()]
            selection["requested_task_ids"] = [resolved[0].task_id]

        limit_raw = normalized.get("max_tasks") or normalized.get("limit")
        if limit_raw is not None:
            try:
                limit = max(1, int(limit_raw))
                resolved = resolved[:limit]
                selection["limit"] = limit
            except (TypeError, ValueError):
                pass

        resolved = self._dedupe_tasks(resolved)
        if not resolved:
            raise ValueError("No quipu_lab tasks could be resolved from the request.")

        return resolved, selection

    def validate_request(self, request_data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request_data, dict):
            raise ValueError("Tau2 adapter request must be a dict.")

        normalized = dict(request_data)
        if not normalized.get("domain_name"):
            normalized["domain_name"] = self.config.domain_name
        if "provider" not in normalized:
            normalized["provider"] = self.provider_name

        if normalized["domain_name"] == "quipu_lab":
            tasks, selection = self._resolve_quipu_lab_tasks(normalized)
            normalized["tasks"] = [task.to_dict() for task in tasks]
            normalized["task_selection"] = selection
            normalized["task_count"] = len(tasks)

            # Backward-compatible single-task fields based on the first task.
            primary_task = tasks[0]
            normalized["task"] = primary_task.to_dict()
            normalized.setdefault("turns", list(primary_task.conversation_context))
            normalized.setdefault("tools", list(primary_task.required_tools))
            normalized["selected_task_ids"] = [task.task_id for task in tasks]

        return normalized

    def translate_request(self, request_data: dict[str, Any]) -> dict[str, Any]:
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
            translated["tasks"] = list(normalized.get("tasks", []))
            translated["task_selection"] = dict(normalized.get("task_selection", {}))
            translated["policy_excerpt"] = get_policy_excerpt()

        return translated

    def translate_response(self, response_data: dict[str, Any]) -> Tau2AdapterResult:
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

    def _execute_single_quipu_task(self, task: QuipuLabTask, policy_excerpt: dict[str, Any]) -> dict[str, Any]:
        tool_results: list[dict[str, Any]] = []
        for tool_name in task.required_tools:
            tool_results.append(execute_tool(tool_name, self._default_tool_args(tool_name, task)))

        result = build_minimal_result(task)
        trace = [
            {
                "step": 1,
                "event_type": "task_loaded",
                "content": f"Loaded task {task.task_id} for domain quipu_lab.",
            },
            {
                "step": 2,
                "event_type": "tools_executed",
                "content": f"Executed {len(tool_results)} required tool(s).",
            },
            {
                "step": 3,
                "event_type": "result_built",
                "content": "Built structured quipu_lab tau2-style result.",
            },
        ]

        return {
            "task": task.to_dict(),
            "result": result,
            "tool_results": tool_results,
            "policy_excerpt": policy_excerpt,
            "trace": trace,
        }

    def execute(self, request_data: dict[str, Any]) -> Tau2AdapterResult:
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

        if domain_name != "quipu_lab":
            structured_response = {
                "ok": True,
                "provider": self.provider_name,
                "domain_name": domain_name,
                "echo": translated["input"],
            }
            return self.translate_response(structured_response)

        policy_excerpt = translated["policy_excerpt"]
        tasks = [QuipuLabTask.from_dict(payload) for payload in translated.get("tasks", [])]
        if not tasks:
            tasks = [QuipuLabTask.from_dict(translated["task"])]

        per_task_runs = [self._execute_single_quipu_task(task, policy_excerpt) for task in tasks]

        if len(per_task_runs) == 1:
            single = per_task_runs[0]
            execution_response = {
                "ok": True,
                "provider": self.provider_name,
                "domain_name": domain_name,
                "task": single["task"],
                "result": single["result"],
                "tool_results": single["tool_results"],
                "policy_excerpt": single["policy_excerpt"],
                "trace": single["trace"],
                "task_selection": translated.get("task_selection", {}),
                "task_count": 1,
                "selected_task_ids": [single["task"]["task_id"]],
            }
            return self.translate_response(execution_response)

        aggregate_trace = []
        for index, item in enumerate(per_task_runs, start=1):
            aggregate_trace.append(
                {
                    "step": index,
                    "event_type": "task_completed",
                    "content": f"Completed {item['task']['task_id']} with {len(item['tool_results'])} tool result(s).",
                }
            )

        execution_response = {
            "ok": True,
            "provider": self.provider_name,
            "domain_name": domain_name,
            "task_selection": translated.get("task_selection", {}),
            "task_count": len(per_task_runs),
            "selected_task_ids": [item["task"]["task_id"] for item in per_task_runs],
            "tasks": [item["task"] for item in per_task_runs],
            "results": [item["result"] for item in per_task_runs],
            "tool_results": [item["tool_results"] for item in per_task_runs],
            "policy_excerpt": policy_excerpt,
            "trace": aggregate_trace,
            "summary": {
                "completed": len(per_task_runs),
                "all_ok": all(item["result"].get("status") == "ok" for item in per_task_runs),
            },
        }
        return self.translate_response(execution_response)
