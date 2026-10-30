"""Two-phase, evidence-oriented coordinator for AegisForge Nexus.

Exact repository path: src/aegisforge/nexus/orchestrator.py

prepare(task)       -> one model call via the injected NexusPlanner, NO tools.
preview(prepared)   -> tool-free dry run; may be repeated before run().
run(prepared, ...)  -> consumes prepared plan ONCE; actual registered tool
                       handlers run ONLY with execute_tools=True; mutating
                       tools also require per-step approval.

Independent checks are supplied by the trusted host, never generated or
executed from the LLM's plan. The host is responsible for enforcing read-only
checks, tool isolation, resource limits, and authorization of external effects.

Evidence remains in memory until outcome.evidence.save_jsonl(path) is called.
This module does not run shell commands, make network requests itself, create
files, or start cloud resources. The injected planner may call Nebius when
prepare() is explicitly invoked; trusted host tool/check callbacks may perform
their own I/O when run() is explicitly authorized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Mapping, Sequence

from .evidence import NexusEvidenceTrail
from .executor import ExecutionReport, NexusExecutor
from .planner import NexusPlan, NexusPlanner
from .verifier import (
    EvidenceExpectation,
    IndependentCheck,
    NexusVerifier,
    VerificationReport,
)


class NexusOrchestrationError(ValueError):
    """The orchestrator was used with an invalid, foreign, or spent plan."""


@dataclass(slots=True)
class PreparedNexusPlan:
    """Plan awaiting operator review; private lifecycle state prevents replay.

    `plan` can be inspected, but never treat its content as authorization.
    A PreparedNexusPlan belongs to exactly one NexusOrchestrator instance.
    """

    plan: NexusPlan
    evidence: NexusEvidenceTrail
    _owner: object = field(repr=False)
    _consumed: bool = field(default=False, repr=False, init=False)
    _lock: Any = field(default_factory=Lock, repr=False, init=False)

    @property
    def consumed(self) -> bool:
        with self._lock:
            return self._consumed

    def _claim(self) -> None:
        # Claim BEFORE any trusted handler can run; no accidental duplicate
        # execution after exceptions or simultaneous requests.
        with self._lock:
            if self._consumed:
                raise NexusOrchestrationError(
                    "This prepared plan was already used. Prepare a fresh plan for another run."
                )
            if len(self.evidence.events) != 1 or self.evidence.events[0].stage != "plan":
                raise NexusOrchestrationError("The prepared plan's evidence trail is not fresh.")
            self._consumed = True


@dataclass(frozen=True, slots=True)
class NexusOutcome:
    """Result of ONE attempt, including safe exportable summary evidence.

    `execution` may contain trusted tool-provided short strings: do not publish
    or log `execution.to_dict()` without a separate privacy review.
    `evidence` records only bounded audit summaries, never raw tool payloads.
    """

    plan: NexusPlan
    execution: ExecutionReport
    verification: VerificationReport
    evidence: NexusEvidenceTrail

    @property
    def status(self) -> str:
        """Independent verification status: verified | failed | inconclusive."""
        return self.verification.status

    def to_summary(self) -> dict[str, Any]:
        """Compact audit-safe summary, excluding task text and raw evidence."""
        return {
            "run_id": self.evidence.run_id,
            "execution_status": self.execution.status,
            "verification_status": self.verification.status,
            "steps": len(self.plan.steps),
            "tools_invoked": self.execution.tools_invoked,
            "independent_checks_run": self.verification.independent_checks_run,
            "independent_checks_passed": self.verification.independent_checks_passed,
            "audit_events": len(self.evidence.events),
            "audit_sha256": self.evidence.final_sha256,
        }


class NexusOrchestrator:
    """Glue for Planner -> Executor -> Verifier -> EvidenceTrail.

    Deliberately no `run_task(text, execute_tools=True)` shortcut: review the
    model-generated plan between prepare() and run().

    The executor's trusted registered tool IDs are automatically advertised
    to the planner. The caller does not get to widen that allowlist by passing
    a second collection of tool names.
    """

    def __init__(
        self,
        planner: NexusPlanner,
        executor: NexusExecutor,
        verifier: NexusVerifier | None = None,
    ) -> None:
        if not isinstance(planner, NexusPlanner):
            raise NexusOrchestrationError("planner must be a NexusPlanner")
        if not isinstance(executor, NexusExecutor):
            raise NexusOrchestrationError("executor must be a NexusExecutor")
        if verifier is not None and not isinstance(verifier, NexusVerifier):
            raise NexusOrchestrationError("verifier must be a NexusVerifier")
        self.planner = planner
        self.executor = executor
        self.verifier = verifier or NexusVerifier()
        self._owner = object()

    def prepare(self, task: str, *, constraints: Sequence[str] = ()) -> PreparedNexusPlan:
        """Request exactly one validated plan from Nebius (if a live client is used).

        No registered tool/check handler is executed and no file is written.
        One API call may have a cost when `planner.client` is real Nebius.
        """
        plan = self.planner.plan(
            task,
            allowed_tools=self.executor.allowed_tools(),
            constraints=constraints,
        )
        evidence = NexusEvidenceTrail()
        evidence.record_plan(plan)
        return PreparedNexusPlan(plan=plan, evidence=evidence, _owner=self._owner)

    def _require_prepared(self, prepared: PreparedNexusPlan) -> None:
        if not isinstance(prepared, PreparedNexusPlan) or prepared._owner is not self._owner:
            raise NexusOrchestrationError("Prepared plan belongs to a different orchestrator.")
        if prepared.consumed:
            raise NexusOrchestrationError("Prepared plan already consumed; cannot replay.")

    def preview(
        self,
        prepared: PreparedNexusPlan,
        *,
        tool_arguments: Mapping[str, Mapping[str, Any]] | None = None,
        manual_evidence: Mapping[str, str] | None = None,
    ) -> ExecutionReport:
        """Preview blocking/approval decisions without calling tool handlers.

        The host's argument-validation callbacks can still be invoked during
        preflight; those callbacks MUST be pure and without side effects.
        Preview does not consume the plan or write audit execution events.
        """
        self._require_prepared(prepared)
        return self.executor.run(
            prepared.plan,
            execute_tools=False,
            tool_arguments=tool_arguments,
            manual_evidence=manual_evidence,
        )

    def run(
        self,
        prepared: PreparedNexusPlan,
        *,
        execute_tools: bool = False,
        approved_mutations: frozenset[str] = frozenset(),
        tool_arguments: Mapping[str, Mapping[str, Any]] | None = None,
        manual_evidence: Mapping[str, str] | None = None,
        expectations: Sequence[EvidenceExpectation] = (),
        independent_checks: Sequence[IndependentCheck] = (),
    ) -> NexusOutcome:
        """Complete one prepared plan, with execution off unless opted in.

        `approved_mutations` contains individual tool step IDs (e.g. 's2'),
        NOT model text. Independent check callbacks should inspect genuinely
        independent read-only evidence and are invoked only by NexusVerifier
        after a fully reported execution. Even with tool success, the status
        stays inconclusive until sufficient independent checks pass.

        This is a ONE-SHOT method; a failed preflight or callback cannot be
        retried using the same prepared plan (prevents duplicate mutations).
        """
        self._require_prepared(prepared)
        prepared._claim()
        execution = self.executor.run(
            prepared.plan,
            execute_tools=execute_tools,
            approved_mutations=approved_mutations,
            tool_arguments=tool_arguments,
            manual_evidence=manual_evidence,
        )
        prepared.evidence.record_execution(execution)
        verification = self.verifier.verify(
            prepared.plan,
            execution,
            expectations=expectations,
            independent_checks=independent_checks,
        )
        prepared.evidence.record_verification(verification)
        return NexusOutcome(
            plan=prepared.plan,
            execution=execution,
            verification=verification,
            evidence=prepared.evidence,
        )


__all__ = [
    "NexusOrchestrator",
    "NexusOrchestrationError",
    "PreparedNexusPlan",
    "NexusOutcome",
]
