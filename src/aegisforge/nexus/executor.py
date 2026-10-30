"""Explicit, auditable execution gate for AegisForge Nexus.

Exact repository location: src/aegisforge/nexus/executor.py

Consumes validated NexusPlan objects produced by nexus.planner, but DOES NOT
execute a model's suggested tools by itself. Hosts must register trusted tool
handlers, opt in to execution, supply arguments, and approve each mutating step.

IMPORTANT: This is a policy/approval gate, NOT an operating-system sandbox.
Handlers must independently implement filesystem scope, input validation,
network/credential controls, time limits and any relevant isolation.

No built-in shell, Python evaluation, file writes, network access, or Nebius
inference is performed by this module. The default is a non-executing preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable, Mapping

from .planner import NexusPlan, PlanStep


class NexusExecutionError(ValueError):
    """Invalid execution configuration, untrusted plan or tool contract."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Deliberately concise output of an approved host-owned handler.

    Never put tokens, passwords, full tool payloads or raw sensitive file
    contents into ``summary`` or ``evidence``: reports may be committed.
    """

    ok: bool
    summary: str
    evidence: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """A trusted tool registered by the application, never by a model.

    With no ``validate_arguments`` callback, only zero-argument calls are
    permitted. A validator must raise ValueError if any argument is unsafe.
    """

    id: str
    description: str
    handler: Callable[[Mapping[str, Any]], ToolResult]
    mutating: bool = False
    validate_arguments: Callable[[Mapping[str, Any]], None] | None = None


@dataclass(frozen=True, slots=True)
class StepRecord:
    step_id: str
    kind: str
    status: str
    detail: str
    tool: str | None = None
    evidence: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "kind": self.kind,
            "status": self.status,
            "detail": self.detail,
            "tool": self.tool,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    goal: str
    status: str
    records: tuple[StepRecord, ...]
    tools_invoked: int
    model_request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "status": self.status,
            "records": [r.to_dict() for r in self.records],
            "tools_invoked": self.tools_invoked,
            "model_request_id": self.model_request_id,
        }


_TOOL_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_ALLOWED_KINDS = {"reason", "tool", "verify"}
_MAX_ARGUMENT_BYTES = 16 * 1024
_MAX_EVIDENCE_CHARS = 1200


def _validate_tool_registration(registry: Mapping[str, RegisteredTool]) -> dict[str, RegisteredTool]:
    if not isinstance(registry, Mapping):
        raise NexusExecutionError("tools must be a mapping of trusted registered tools")
    if len(registry) > 30:
        raise NexusExecutionError("at most 30 registered tools are supported")
    result = dict(registry)
    for name, spec in result.items():
        if not isinstance(name, str) or not _TOOL_ID.fullmatch(name):
            raise NexusExecutionError("invalid tool ID")
        if not isinstance(spec, RegisteredTool) or spec.id != name:
            raise NexusExecutionError(f"registration mismatch for {name!r}")
        if not isinstance(spec.description, str) or not spec.description.strip():
            raise NexusExecutionError(f"tool {name!r} lacks a description")
        if not callable(spec.handler):
            raise NexusExecutionError(f"tool {name!r} lacks a callable handler")
        if type(spec.mutating) is not bool:
            raise NexusExecutionError(f"tool {name!r} mutating must be bool")
        if spec.validate_arguments is not None and not callable(spec.validate_arguments):
            raise NexusExecutionError(f"tool {name!r} has an invalid validator")
    return result


def _validate_plan(plan: NexusPlan) -> None:
    """Also validate dataclasses made by hand, not only LLM-parsed plans."""
    if not isinstance(plan, NexusPlan) or not isinstance(plan.goal, str) or not plan.goal.strip():
        raise NexusExecutionError("plan must be a nonempty NexusPlan")
    if not isinstance(plan.steps, tuple) or not 2 <= len(plan.steps) <= 12:
        raise NexusExecutionError("plan must contain 2 to 12 steps")
    prior: set[str] = set()
    for i, step in enumerate(plan.steps, 1):
        if not isinstance(step, PlanStep) or step.id != f"s{i}" or step.kind not in _ALLOWED_KINDS:
            raise NexusExecutionError("plan step IDs/kinds are not valid")
        if not isinstance(step.title, str) or not step.title.strip():
            raise NexusExecutionError(f"step {step.id}: missing title")
        if not isinstance(step.description, str) or not step.description.strip():
            raise NexusExecutionError(f"step {step.id}: missing description")
        if not isinstance(step.success_criteria, str) or not step.success_criteria.strip():
            raise NexusExecutionError(f"step {step.id}: missing success criteria")
        if not isinstance(step.depends_on, tuple) or any(d not in prior for d in step.depends_on):
            raise NexusExecutionError(f"step {step.id}: invalid or forward dependencies")
        if len(set(step.depends_on)) != len(step.depends_on):
            raise NexusExecutionError(f"step {step.id}: duplicate dependency")
        if step.kind == "tool":
            if not isinstance(step.tool, str) or not _TOOL_ID.fullmatch(step.tool):
                raise NexusExecutionError(f"step {step.id}: invalid tool ID")
        elif step.tool is not None:
            raise NexusExecutionError(f"step {step.id}: non-tool step has a tool")
        prior.add(step.id)
    if plan.steps[-1].kind != "verify":
        raise NexusExecutionError("the last step must be an evidence verification step")


def _validate_arguments(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, Mapping) or not all(isinstance(k, str) for k in arguments):
        raise NexusExecutionError("tool arguments must be a mapping with string keys")
    safe = dict(arguments)
    try:
        serialized = json.dumps(safe, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NexusExecutionError("tool arguments must be JSON-compatible") from exc
    if len(serialized.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
        raise NexusExecutionError("tool arguments exceed the 16 KiB limit")
    return safe


def _validate_evidence(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    result = value.strip()
    return result if len(result) <= _MAX_EVIDENCE_CHARS else None


class NexusExecutor:
    """Execute plan steps through a restricted host-supplied tool registry.

    Each ``run`` is an explicitly initiated operation. Tool invocation is
    OFF unless ``execute_tools=True``. Mutating tools additionally require
    their step IDs in ``approved_mutations``. No automatic retries.

    ``manual_evidence`` acknowledges a reason/verify step; it is operator-
    supplied, not independent proof. Reports therefore say ``reported_complete``
    rather than asserting external correctness.
    """

    def __init__(self, tools: Mapping[str, RegisteredTool] | None = None) -> None:
        self._tools = _validate_tool_registration(tools or {})

    def allowed_tools(self) -> dict[str, str]:
        """Pass this allowlist into NexusPlanner.plan(allowed_tools=...)."""
        return {name: tool.description for name, tool in self._tools.items()}

    def run(
        self,
        plan: NexusPlan,
        *,
        execute_tools: bool = False,
        approved_mutations: frozenset[str] = frozenset(),
        tool_arguments: Mapping[str, Mapping[str, Any]] | None = None,
        manual_evidence: Mapping[str, str] | None = None,
    ) -> ExecutionReport:
        _validate_plan(plan)
        if type(execute_tools) is not bool:
            raise NexusExecutionError("execute_tools must be bool")
        if not isinstance(approved_mutations, (frozenset, set, tuple, list)) or any(
            not isinstance(item, str) for item in approved_mutations
        ):
            raise NexusExecutionError("approved_mutations must be step IDs")
        approved = frozenset(approved_mutations)
        if not approved.issubset({s.id for s in plan.steps if s.kind == "tool"}):
            raise NexusExecutionError("approval refers to an unknown or non-tool step")
        provided = tool_arguments if tool_arguments is not None else {}
        acknowledged = manual_evidence if manual_evidence is not None else {}
        if not isinstance(provided, Mapping) or not isinstance(acknowledged, Mapping):
            raise NexusExecutionError("tool_arguments and manual_evidence must be mappings")
        plan_ids = {s.id for s in plan.steps}
        tool_ids = {s.id for s in plan.steps if s.kind == "tool"}
        if not set(provided).issubset(tool_ids) or not set(acknowledged).issubset(plan_ids - tool_ids):
            raise NexusExecutionError("input references an unknown or incompatible step")

        # Fail closed for an unregistered requested tool BEFORE invoking any handler.
        for step in plan.steps:
            if step.kind == "tool" and step.tool not in self._tools:
                raise NexusExecutionError(f"tool {step.tool!r} was not registered by the host")

        # Check all arguments before calling any handlers; invalid steps do not
        # leave the workflow partially executed.
        clean_arguments = {step_id: _validate_arguments(args) for step_id, args in provided.items()}
        for step in plan.steps:
            if step.kind != "tool":
                continue
            spec = self._tools[step.tool]  # type: ignore[index]
            args = clean_arguments.get(step.id, {})
            if spec.validate_arguments is None and args:
                raise NexusExecutionError(f"tool {spec.id!r} does not accept arguments")
            if spec.validate_arguments is not None:
                try:
                    spec.validate_arguments(args)
                except (ValueError, TypeError) as exc:
                    raise NexusExecutionError(f"arguments rejected for tool {spec.id!r}") from exc
        for step_id, value in acknowledged.items():
            if _validate_evidence(value) is None:
                raise NexusExecutionError(f"invalid evidence for {step_id}")

        records: list[StepRecord] = []
        successful: set[str] = set()
        invoked = 0

        for step in plan.steps:
            if any(dep not in successful for dep in step.depends_on):
                records.append(StepRecord(step.id, step.kind, "blocked_dependency", "A prerequisite is not complete.", tool=step.tool))
                continue

            if step.kind in {"reason", "verify"}:
                evidence = acknowledged.get(step.id)
                if evidence is None:
                    records.append(StepRecord(step.id, step.kind, "awaiting_evidence", "Operator evidence required; this step is not automatically performed."))
                else:
                    records.append(StepRecord(
                        step.id, step.kind, "attested_by_operator",
                        "An operator supplied an attestation; not independently verified.",
                        evidence={"operator_attestation": evidence.strip()},
                    ))
                    successful.add(step.id)
                continue

            spec = self._tools[step.tool]  # type: ignore[index]
            if not execute_tools:
                records.append(StepRecord(step.id, step.kind, "preview_only", "Tool not executed (execute_tools=False).", tool=step.tool))
                continue
            if spec.mutating and step.id not in approved:
                records.append(StepRecord(step.id, step.kind, "approval_required", "Explicit approval required for a mutating tool.", tool=step.tool))
                continue

            invoked += 1
            try:
                result = spec.handler(clean_arguments.get(step.id, {}))
                if not isinstance(result, ToolResult) or type(result.ok) is not bool:
                    raise TypeError("tool handler returned an invalid ToolResult")
                if not isinstance(result.summary, str) or len(result.summary) > _MAX_EVIDENCE_CHARS:
                    raise TypeError("invalid tool summary")
                if not isinstance(result.evidence, Mapping) or any(
                    not isinstance(k, str) or not k or not isinstance(v, str)
                    or len(v) > _MAX_EVIDENCE_CHARS
                    for k, v in result.evidence.items()
                ):
                    raise TypeError("invalid tool evidence")
            except Exception:
                # Do NOT export raw exceptions: SDK/network errors can contain
                # credentials, file data or signed URLs.
                records.append(StepRecord(step.id, step.kind, "tool_error", "Tool raised an exception; details withheld from public audit.", tool=step.tool))
                continue

            if result.ok:
                successful.add(step.id)
            records.append(StepRecord(
                step.id, step.kind, "completed" if result.ok else "tool_failed",
                result.summary,
                tool=step.tool,
                evidence=dict(result.evidence),
            ))

        if len(successful) == len(plan.steps):
            final_status = "reported_complete"  # human attestations, not objective verification
        elif any(r.status in {"tool_error", "tool_failed"} for r in records):
            final_status = "failed"
        elif any(r.status == "approval_required" for r in records):
            final_status = "approval_required"
        elif any(r.status == "preview_only" for r in records):
            final_status = "preview"
        else:
            final_status = "awaiting_evidence"
        return ExecutionReport(
            goal=plan.goal,
            status=final_status,
            records=tuple(records),
            tools_invoked=invoked,
            model_request_id=plan.request_id,
        )


__all__ = [
    "NexusExecutor", "NexusExecutionError", "RegisteredTool", "ToolResult",
    "ExecutionReport", "StepRecord",
]
