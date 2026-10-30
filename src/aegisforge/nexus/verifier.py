"""Evidence-first verification for AegisForge Nexus.

Exact repository path: src/aegisforge/nexus/verifier.py

Consumes NexusPlan + ExecutionReport. It does NOT infer that a task succeeded
from an LLM's assertion, an operator attestation, or a tool's `ok` flag.

VERIFIED requires a structurally consistent, completed execution and successful
HOST-OWNED, INDEPENDENT read-only checks covering every tool step and the final
verification step. The host must implement those checks against real artifacts
or independently observed state. This module cannot guarantee that a supplied
callback is truly independent or read-only; that is the host's responsibility.

No network, disk access, subprocess invocation, model inference, or retries are
performed by the module itself. No check callback runs by default.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping, Sequence

from .planner import NexusPlan, PlanStep
from .executor import ExecutionReport, StepRecord, NexusExecutionError, _validate_plan


class NexusVerificationError(ValueError):
    """Invalid plan, execution report, expectation, or trusted check contract."""


@dataclass(frozen=True, slots=True)
class EvidenceExpectation:
    """An exact comparison against a tool-reported evidence field.

    This tests report consistency, NOT independent external correctness.
    The expected value must be supplied by the trusted host, never by a model.
    """

    step_id: str
    key: str
    expected: str


@dataclass(frozen=True, slots=True)
class IndependentCheck:
    """A trusted host-defined check against independently observed state.

    The callback receives the planned step and its execution record. The
    callback MUST only perform read-only checks within authorized scope.
    For example, it may compare an externally measured file hash with an
    expected hash. Do not implement it as `lambda *_: True` in production,
    and do not let model output construct checks or code.

    evidence_ref is a nonsecret local identifier for the independent evidence,
    never a bearer URL, token, or private file content.
    """

    id: str
    step_id: str
    description: str
    handler: Callable[[PlanStep, StepRecord], bool]
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    kind: str                # execution | expectation | independent_check | coverage
    step_id: str
    verdict: str             # pass | fail | inconclusive
    code: str
    check_id: str | None = None
    evidence_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "step_id": self.step_id,
            "verdict": self.verdict,
            "code": self.code,
            "check_id": self.check_id,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    goal: str
    status: str              # verified | failed | inconclusive
    findings: tuple[VerificationFinding, ...]
    independent_checks_passed: int
    independent_checks_run: int
    plan_request_id: str | None

    def to_dict(self) -> dict[str, Any]:
        """An audit-safe summary; does not serialize raw tool evidence."""
        return {
            "goal": self.goal,
            "status": self.status,
            "findings": [item.to_dict() for item in self.findings],
            "independent_checks_passed": self.independent_checks_passed,
            "independent_checks_run": self.independent_checks_run,
            "plan_request_id": self.plan_request_id,
        }


_CHECK_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_EVIDENCE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
# No query parameters / fragment (which could carry signatures or credentials).
_SAFE_EVIDENCE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_TOOL_STATUSES = frozenset({
    "completed", "tool_failed", "tool_error", "preview_only",
    "approval_required", "blocked_dependency",
})
_MANUAL_STATUSES = frozenset({
    "attested_by_operator", "awaiting_evidence", "blocked_dependency",
})
_SUCCEEDED = frozenset({"completed", "attested_by_operator"})
_FAILED = frozenset({"tool_failed", "tool_error"})


def _validate_execution(plan: NexusPlan, report: ExecutionReport) -> None:
    """Reject inconsistent or fabricated report structures before any callback."""
    try:
        _validate_plan(plan)
    except NexusExecutionError as exc:
        raise NexusVerificationError("Invalid NexusPlan structure.") from exc
    if not isinstance(report, ExecutionReport):
        raise NexusVerificationError("Expected an ExecutionReport.")
    if report.goal != plan.goal or report.model_request_id != plan.request_id:
        raise NexusVerificationError("Execution report is not bound to this plan.")
    if not isinstance(report.records, tuple) or len(report.records) != len(plan.steps):
        raise NexusVerificationError("Execution report must have one record per plan step.")
    if type(report.tools_invoked) is not int or not 0 <= report.tools_invoked <= len(plan.steps):
        raise NexusVerificationError("Invalid tools_invoked count.")

    succeeded: set[str] = set()
    invocations = 0
    for step, record in zip(plan.steps, report.records):
        if not isinstance(record, StepRecord):
            raise NexusVerificationError("Report contains a malformed step record.")
        if (record.step_id, record.kind, record.tool) != (step.id, step.kind, step.tool):
            raise NexusVerificationError("Record does not match the corresponding plan step.")
        allowed = _TOOL_STATUSES if step.kind == "tool" else _MANUAL_STATUSES
        if record.status not in allowed:
            raise NexusVerificationError("Report contains an unsupported step status.")
        if not isinstance(record.detail, str) or len(record.detail) > 1200:
            raise NexusVerificationError("Record detail is malformed.")
        if not isinstance(record.evidence, dict) or len(record.evidence) > 50:
            raise NexusVerificationError("Record evidence is malformed.")
        for key, value in record.evidence.items():
            if (not isinstance(key, str) or not _EVIDENCE_KEY.fullmatch(key)
                    or not isinstance(value, str) or len(value) > 1200):
                raise NexusVerificationError("Record has invalid evidence entries.")
        if step.kind != "tool":
            if record.status == "attested_by_operator":
                if set(record.evidence) != {"operator_attestation"} or not record.evidence["operator_attestation"].strip():
                    raise NexusVerificationError("Operator step has no valid attestation.")
            elif record.evidence:
                raise NexusVerificationError("Pending manual step cannot contain evidence.")

        dependency_satisfied = all(dep in succeeded for dep in step.depends_on)
        if not dependency_satisfied and record.status != "blocked_dependency":
            raise NexusVerificationError("An executed step bypassed an incomplete dependency.")
        if dependency_satisfied and record.status == "blocked_dependency":
            raise NexusVerificationError("A step was blocked despite completed dependencies.")
        if record.status in {"completed", "tool_failed", "tool_error"}:
            invocations += 1
        if record.status in _SUCCEEDED:
            succeeded.add(step.id)

    if report.tools_invoked != invocations:
        raise NexusVerificationError("Tool invocation count conflicts with step records.")
    if len(succeeded) == len(plan.steps):
        expected_status = "reported_complete"
    elif any(r.status in _FAILED for r in report.records):
        expected_status = "failed"
    elif any(r.status == "approval_required" for r in report.records):
        expected_status = "approval_required"
    elif any(r.status == "preview_only" for r in report.records):
        expected_status = "preview"
    else:
        expected_status = "awaiting_evidence"
    if report.status != expected_status:
        raise NexusVerificationError("Final execution status conflicts with records.")


def _validate_inputs(
    plan: NexusPlan,
    expectations: Sequence[EvidenceExpectation],
    independent_checks: Sequence[IndependentCheck],
) -> None:
    if (not isinstance(expectations, (tuple, list)) or len(expectations) > 64
            or not isinstance(independent_checks, (tuple, list)) or len(independent_checks) > 64):
        raise NexusVerificationError("Expected at most 64 expectations and 64 checks.")
    kind_by_id = {step.id: step.kind for step in plan.steps}
    seen_expectations: set[tuple[str, str]] = set()
    for item in expectations:
        if not isinstance(item, EvidenceExpectation) or kind_by_id.get(item.step_id) != "tool":
            raise NexusVerificationError("Expectations may target tool steps only.")
        if not isinstance(item.key, str) or not _EVIDENCE_KEY.fullmatch(item.key):
            raise NexusVerificationError("Expectation key is invalid.")
        if not isinstance(item.expected, str) or len(item.expected) > 1200:
            raise NexusVerificationError("Expected value must be a short string.")
        identifier = (item.step_id, item.key)
        if identifier in seen_expectations:
            raise NexusVerificationError("Duplicate expectation.")
        seen_expectations.add(identifier)
    seen_checks: set[str] = set()
    for check in independent_checks:
        if not isinstance(check, IndependentCheck):
            raise NexusVerificationError("Invalid independent check.")
        if not isinstance(check.id, str) or not _CHECK_ID.fullmatch(check.id) or check.id in seen_checks:
            raise NexusVerificationError("Duplicate or invalid check ID.")
        seen_checks.add(check.id)
        if kind_by_id.get(check.step_id) not in {"tool", "verify"}:
            raise NexusVerificationError("Independent checks target tool or verify steps only.")
        if not isinstance(check.description, str) or not 1 <= len(check.description.strip()) <= 300:
            raise NexusVerificationError("Independent check description is invalid.")
        if not callable(check.handler):
            raise NexusVerificationError("Independent check needs a callable handler.")
        if (not isinstance(check.evidence_ref, str)
                or not _SAFE_EVIDENCE_REF.fullmatch(check.evidence_ref)):
            raise NexusVerificationError("Use a short nonsecret evidence reference without tokens.")


class NexusVerifier:
    """Validate recorded execution and call explicit host-owned evidence checks.

    No check runs unless its host-provided callback is explicitly passed here.
    The verifier cannot inspect the real world by itself, cannot interpret
    arbitrary natural-language success_criteria, and does not execute tools.
    """

    def verify(
        self,
        plan: NexusPlan,
        report: ExecutionReport,
        *,
        expectations: Sequence[EvidenceExpectation] = (),
        independent_checks: Sequence[IndependentCheck] = (),
    ) -> VerificationReport:
        _validate_execution(plan, report)
        _validate_inputs(plan, expectations, independent_checks)
        findings: list[VerificationFinding] = []
        incomplete = False
        failed = False

        for record in report.records:
            if record.status in _FAILED:
                failed = True
                verdict, code = "fail", "execution_failed"
            elif record.status not in _SUCCEEDED:
                incomplete = True
                verdict, code = "inconclusive", "step_not_completed"
            elif record.status == "attested_by_operator":
                verdict, code = "inconclusive", "operator_attestation_only"
            else:
                verdict, code = "pass", "tool_reported_complete"
            findings.append(VerificationFinding("execution", record.step_id, verdict, code))

        # Comparison with a tool's own evidence is useful, but is not by itself
        # proof of an actual-world result: the tool may be buggy or mistaken.
        evidence_by_id = {record.step_id: record.evidence for record in report.records}
        for expectation in expectations:
            observed = evidence_by_id[expectation.step_id].get(expectation.key)
            if observed is None:
                incomplete = True
                verdict, code = "inconclusive", "reported_evidence_missing"
            elif observed != expectation.expected:
                failed = True
                verdict, code = "fail", "reported_evidence_mismatch"
            else:
                verdict, code = "pass", "reported_evidence_matches"
            findings.append(VerificationFinding(
                "expectation", expectation.step_id, verdict, code,
                check_id=f"{expectation.step_id}:{expectation.key}",
            ))

        # Do not call external callbacks against incomplete or failed reports.
        # Checks should only examine a fully reported execution, not complete it.
        can_check = not failed and not incomplete and report.status == "reported_complete"
        checked_steps: set[str] = set()
        run = 0
        passed = 0
        plan_steps = {s.id: s for s in plan.steps}
        records = {r.step_id: r for r in report.records}
        for check in independent_checks:
            if not can_check:
                findings.append(VerificationFinding(
                    "independent_check", check.step_id, "inconclusive", "not_run_incomplete_execution",
                    check_id=check.id,
                ))
                continue
            run += 1
            try:
                result = check.handler(plan_steps[check.step_id], records[check.step_id])
                if type(result) is not bool:
                    raise TypeError("Check did not return a bool")
            except Exception:
                # Never render raw exception details, as they may reveal secrets.
                incomplete = True
                findings.append(VerificationFinding(
                    "independent_check", check.step_id, "inconclusive", "check_unavailable",
                    check_id=check.id,
                ))
                continue
            if result:
                passed += 1
                checked_steps.add(check.step_id)
            else:
                failed = True
            findings.append(VerificationFinding(
                "independent_check", check.step_id,
                "pass" if result else "fail",
                "independent_evidence_passed" if result else "independent_evidence_failed",
                check_id=check.id,
                evidence_ref=check.evidence_ref,
            ))

        # A successful tool execution is insufficient, and operator attestation
        # of the final verify step cannot make a task objectively verified.
        required = {s.id for s in plan.steps if s.kind == "tool"}
        required.add(plan.steps[-1].id)
        for step_id in sorted(required - checked_steps):
            incomplete = True
            findings.append(VerificationFinding(
                "coverage", step_id, "inconclusive", "independent_evidence_required",
            ))

        status = "failed" if failed else "inconclusive" if incomplete else "verified"
        return VerificationReport(
            goal=plan.goal,
            status=status,
            findings=tuple(findings),
            independent_checks_passed=passed,
            independent_checks_run=run,
            plan_request_id=plan.request_id,
        )


__all__ = [
    "NexusVerifier", "NexusVerificationError", "EvidenceExpectation",
    "IndependentCheck", "VerificationFinding", "VerificationReport",
]
