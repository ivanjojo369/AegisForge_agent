"""Privacy-minimizing audit trail for AegisForge Nexus.

Repository path: src/aegisforge/nexus/evidence.py

This module captures *summaries* of the NexusPlan, ExecutionReport and
VerificationReport contracts; never raw prompts, model replies, tool output,
operator attestations, tokens, URLs, file contents, or exception strings.

SHA-256 linking detects inadvertent alterations to a journal, but is NOT a
cryptographic signature or trusted proof against an attacker who can rewrite
and rehash a file. Anchor its terminal digest in a separately controlled log
if stronger provenance is required.

Nothing is written to disk until save_jsonl() is explicitly called. Export
creates a NEW file; it never overwrites existing evidence. The application
owns filesystem permissions, access control, and any independent verification.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from .executor import ExecutionReport, NexusExecutionError, _validate_plan
from .planner import NexusPlan
from .verifier import VerificationReport, NexusVerificationError, _validate_execution

SCHEMA = "aegisforge.nexus.evidence.v1"
_ZERO = "0" * 64
_HEX = re.compile(r"^[a-f0-9]{64}$")
_RUN = re.compile(r"^[a-f0-9]{32}$")
_STEP = re.compile(r"^s[1-9][0-9]*$")
_EXECUTION_STATUSES = frozenset({
    "reported_complete", "failed", "approval_required", "preview", "awaiting_evidence"
})
_VERDICTS = frozenset({"verified", "failed", "inconclusive"})
_MAX_JOURNAL_BYTES = 1024 * 1024


class NexusEvidenceError(ValueError):
    """Evidence is incomplete, inconsistent, or does not pass integrity checks."""


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(data: Any) -> str:
    return sha256(_json_bytes(data)).hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    """One digest-linked event containing bounded, nonsecret summary fields."""

    schema: str
    run_id: str
    sequence: int
    at_utc: str
    stage: str
    status: str
    plan_sha256: str
    summary: dict[str, Any]
    previous_sha256: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        # Isolate the caller from the event's internal mutable summary values.
        return json.loads(_json_bytes({
            "schema": self.schema,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "at_utc": self.at_utc,
            "stage": self.stage,
            "status": self.status,
            "plan_sha256": self.plan_sha256,
            "summary": self.summary,
            "previous_sha256": self.previous_sha256,
            "sha256": self.sha256,
        }))


def verify_jsonl(path: str | Path) -> tuple[EvidenceEvent, ...]:
    """Read and check structure, ordering, and hash links of an exported journal.

    This verifies file consistency, NOT the truth of real-world tool results.
    Does not call Nebius, run tools or create cloud resources.
    """
    p = Path(path)
    if not p.is_file() or p.is_symlink() or p.stat().st_size > _MAX_JOURNAL_BYTES:
        raise NexusEvidenceError("Evidence file missing, linked, or too large.")
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise NexusEvidenceError("Could not read evidence file.") from exc
    if not 1 <= len(lines) <= 3 or any(not line.strip() for line in lines):
        raise NexusEvidenceError("Journal must contain one to three nonempty events.")

    events: list[EvidenceEvent] = []
    expected_run: str | None = None
    expected_plan: str | None = None
    previous = _ZERO
    required_keys = set(EvidenceEvent.__dataclass_fields__)
    stages = ("plan", "execution", "verification")
    for index, line in enumerate(lines, 1):
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict) or set(raw) != required_keys:
                raise ValueError("wrong fields")
            stage = stages[index - 1]
            if (raw["schema"] != SCHEMA or raw["sequence"] != index
                    or type(raw["sequence"]) is not int or raw["stage"] != stage):
                raise ValueError("wrong schema or event order")
            if (not isinstance(raw["run_id"], str) or not _RUN.fullmatch(raw["run_id"])
                    or not isinstance(raw["plan_sha256"], str)
                    or not _HEX.fullmatch(raw["plan_sha256"])):
                raise ValueError("invalid run or plan digest")
            if (not isinstance(raw["previous_sha256"], str)
                    or not isinstance(raw["sha256"], str)
                    or not _HEX.fullmatch(raw["sha256"])
                    or raw["previous_sha256"] != previous):
                raise ValueError("broken hash chain")
            if (expected_run is not None and raw["run_id"] != expected_run
                    or expected_plan is not None and raw["plan_sha256"] != expected_plan):
                raise ValueError("mixed runs or plans")
            at = raw["at_utc"]
            if (not isinstance(at, str) or not at.endswith("Z")
                    or datetime.fromisoformat(at.replace("Z", "+00:00")).utcoffset().total_seconds() != 0):
                raise ValueError("invalid timestamp")
            summary = raw["summary"]
            if not isinstance(summary, dict):
                raise ValueError("invalid summary")
            _validate_summary(stage, raw["status"], summary)
            if raw["sha256"] != _digest({k: v for k, v in raw.items() if k != "sha256"}):
                raise ValueError("entry altered")
            events.append(EvidenceEvent(**raw))
            previous = raw["sha256"]
            expected_run = raw["run_id"]
            expected_plan = raw["plan_sha256"]
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise NexusEvidenceError(f"Invalid evidence event at line {index}.") from exc
    if len(events) >= 2:
        first_count = events[0].summary["steps"]
        rows = events[1].summary["steps"]
        if len(rows) != first_count:
            raise NexusEvidenceError("Execution step count disagrees with plan.")
        invoked = sum(r["status"] in {"completed", "tool_failed", "tool_error"} for r in rows)
        if invoked != events[1].summary["tools_invoked"]:
            raise NexusEvidenceError("Invocation count disagrees with step records.")
    if len(events) == 3:
        if events[2].status == "verified" and events[1].status != "reported_complete":
            raise NexusEvidenceError("Cannot mark an incomplete execution as verified.")
    return tuple(events)


def _natural(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 1000


def _validate_summary(stage: str, status: Any, summary: dict[str, Any]) -> None:
    if stage == "plan":
        if status != "proposed" or set(summary) != {"steps", "reason", "tool", "verify"}:
            raise ValueError("bad planning summary")
        if not all(_natural(v) for v in summary.values()) or not 2 <= summary["steps"] <= 12 or summary["verify"] < 1:
            raise ValueError("bad plan counts")
        if summary["steps"] != summary["reason"] + summary["tool"] + summary["verify"]:
            raise ValueError("bad plan totals")
    elif stage == "execution":
        if status not in _EXECUTION_STATUSES or set(summary) != {"tools_invoked", "steps"}:
            raise ValueError("bad execution summary")
        rows = summary["steps"]
        if (not _natural(summary["tools_invoked"]) or not isinstance(rows, list)
                or len(rows) > 12):
            raise ValueError("bad step summary")
        for i, row in enumerate(rows, 1):
            if (not isinstance(row, dict) or set(row) != {"id", "status"}
                    or row["id"] != f"s{i}" or row["status"] not in {
                        "completed", "tool_failed", "tool_error", "preview_only",
                        "approval_required", "blocked_dependency", "attested_by_operator",
                        "awaiting_evidence",
                    }):
                raise ValueError("bad step status")
    elif stage == "verification":
        if status not in _VERDICTS or set(summary) != {"independent_checks_run", "independent_checks_passed", "findings"}:
            raise ValueError("bad verification summary")
        if not _natural(summary["independent_checks_run"]) or not _natural(summary["independent_checks_passed"]):
            raise ValueError("bad independent checks")
        if summary["independent_checks_passed"] > summary["independent_checks_run"]:
            raise ValueError("more checks passed than executed")
        f = summary["findings"]
        if not isinstance(f, dict) or set(f) != {"pass", "fail", "inconclusive"} or not all(_natural(v) for v in f.values()):
            raise ValueError("bad finding counts")
        if status == "verified" and (summary["independent_checks_run"] < 1 or summary["independent_checks_passed"] != summary["independent_checks_run"] or f["fail"]):
            raise ValueError("unjustified verified status")
    else:
        raise ValueError("unexpected stage")


class NexusEvidenceTrail:
    """One Nexus task's safe audit summary; explicitly capture each phase.

    Constructing, recording and serializing in memory never invokes APIs or
    writes files. Use save_jsonl(path) to opt in to a new on-disk snapshot.
    """

    def __init__(self) -> None:
        self.run_id = uuid4().hex
        self._plan: NexusPlan | None = None
        self._events: list[EvidenceEvent] = []
        self._plan_hash: str | None = None

    @property
    def events(self) -> tuple[EvidenceEvent, ...]:
        return tuple(self._events)

    @property
    def final_sha256(self) -> str | None:
        return self._events[-1].sha256 if self._events else None

    def record_plan(self, plan: NexusPlan) -> EvidenceEvent:
        if self._events:
            raise NexusEvidenceError("A journal can record only one plan.")
        try:
            _validate_plan(plan)
            # Goal, instructions and model replies are hashed, never exported.
            fingerprint = _digest({
                "goal": plan.goal,
                "steps": [s.to_dict() for s in plan.steps],
                "model": plan.model,
                "request_id": plan.request_id,
            })
        except (NexusExecutionError, TypeError, ValueError) as exc:
            raise NexusEvidenceError("Invalid plan contract.") from exc
        counts = Counter(step.kind for step in plan.steps)
        metrics = {"steps": len(plan.steps), "reason": counts["reason"],
                   "tool": counts["tool"], "verify": counts["verify"]}
        event = self._append("plan", "proposed", metrics, fingerprint)
        self._plan = plan
        self._plan_hash = fingerprint
        return event

    def record_execution(self, report: ExecutionReport) -> EvidenceEvent:
        if len(self._events) != 1 or self._plan is None or self._plan_hash is None:
            raise NexusEvidenceError("Record the plan before execution, exactly once.")
        try:
            _validate_execution(self._plan, report)
        except (NexusVerificationError, NexusExecutionError, TypeError) as exc:
            raise NexusEvidenceError("Execution report does not match its plan.") from exc
        summary = {"tools_invoked": report.tools_invoked,
                   "steps": [{"id": r.step_id, "status": r.status} for r in report.records]}
        return self._append("execution", report.status, summary, self._plan_hash)

    def record_verification(self, report: VerificationReport) -> EvidenceEvent:
        if len(self._events) != 2 or self._plan is None or self._plan_hash is None:
            raise NexusEvidenceError("Record planning and execution before verification.")
        if (not isinstance(report, VerificationReport) or report.goal != self._plan.goal
                or report.plan_request_id != self._plan.request_id or report.status not in _VERDICTS):
            raise NexusEvidenceError("Verification report does not match its plan.")
        if (type(report.independent_checks_passed) is not int or type(report.independent_checks_run) is not int
                or not isinstance(report.findings, tuple)):
            raise NexusEvidenceError("Malformed verification report.")
        verdicts = Counter()
        for finding in report.findings:
            verdict = getattr(finding, "verdict", None)
            if verdict not in {"pass", "fail", "inconclusive"}:
                raise NexusEvidenceError("Malformed verification finding.")
            verdicts[verdict] += 1
        summary = {"independent_checks_run": report.independent_checks_run,
                   "independent_checks_passed": report.independent_checks_passed,
                   "findings": {v: verdicts[v] for v in ("pass", "fail", "inconclusive")}}
        return self._append("verification", report.status, summary, self._plan_hash)

    def _append(self, stage: str, status: str, summary: dict[str, Any], fingerprint: str) -> EvidenceEvent:
        try:
            _validate_summary(stage, status, summary)
        except (ValueError, TypeError) as exc:
            raise NexusEvidenceError("Invalid evidence summary.") from exc
        raw = {
            "schema": SCHEMA, "run_id": self.run_id, "sequence": len(self._events) + 1,
            "at_utc": _utc_timestamp(), "stage": stage, "status": status,
            "plan_sha256": fingerprint,
            "summary": summary,
            "previous_sha256": self.final_sha256 or _ZERO,
        }
        event = EvidenceEvent(**raw, sha256=_digest(raw))
        self._events.append(event)
        return event

    def to_jsonl(self) -> str:
        return "".join(json.dumps(e.to_dict(), sort_keys=True, ensure_ascii=True,
                                  separators=(",", ":")) + "\n" for e in self._events)

    def save_jsonl(self, path: str | Path) -> Path:
        """Create a fresh file only. No overwrite, mkdir, or implicit I/O."""
        if not self._events:
            raise NexusEvidenceError("Cannot export an empty journal.")
        target = Path(path)
        if target.is_symlink():
            raise NexusEvidenceError("Refusing symlink evidence target.")
        # 'x' prevents accidentally replacing any previous evidence snapshot.
        try:
            with target.open("x", encoding="utf-8", newline="\n") as fp:
                fp.write(self.to_jsonl())
                fp.flush()
                os.fsync(fp.fileno())
        except FileExistsError:
            raise
        # An independent read validates the on-disk representation.
        verify_jsonl(target)
        return target


__all__ = ["NexusEvidenceTrail", "EvidenceEvent", "NexusEvidenceError", "verify_jsonl", "SCHEMA"]
