"""Supervised, non-applying repair workflow for AegisForge Nexus — Colossus.

Target: src/aegisforge/nexus/engineering/repair_loop.py

A bounded state coordinator around existing code proposal, patch verification,
static change impact, test discovery, and *inert* test-plan preparation.

No filesystem writes, patch application, tool execution or subprocess calls.
An explicit call to assess(..., use_model=True) can perform ONE inference using
an injected NexusCodeProposer client. With candidate_json it is entirely offline.

Security notes:
- Model output, repo content, user feedback and supplied test reports are data.
- A diff, risk analysis, test plan, or a passed baseline test is NOT authorization.
- A TestRunPlan here targets unchanged source files, not a patched checkout.
- run(), approval grants, patch application, and verification of a patched tree
  MUST be implemented separately by a trusted host with Nexus Trust controls.
- Treat test result reports as externally supplied observations, NOT proofs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import secrets
from threading import Lock
from typing import Any, Mapping, Sequence

from .code_proposer import (
    CodeProposal, NexusCodeProposer, ProposalContext,
)
from .diff_builder import NexusDiffBuilder, PatchBundle
from .change_impact import NexusChangeImpactAnalyzer
from .test_discovery import NexusTestDiscovery
from .test_runner import (
    NexusTestRunner, NexusTestRunnerError, TestRunPlan, RUNNER_SCHEMA,
)

REPAIR_SCHEMA = "aegisforge.nexus.engineering.repair_loop.v1"
_MISSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HEX = re.compile(r"^[a-f0-9]{64}$")


class NexusRepairLoopError(ValueError):
    """Invalid state, linkage, input, or bounded workflow configuration."""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(obj: Any) -> str:
    return sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _paths(values: Sequence[str], *, max_count: int, label: str) -> tuple[str, ...]:
    if (not isinstance(values, (tuple, list)) or isinstance(values, (str, bytes))
            or not 1 <= len(values) <= max_count):
        raise NexusRepairLoopError(f"{label} must be a nonempty bounded list")
    result: list[str] = []
    for p in values:
        if not isinstance(p, str) or not p or len(p) > 512:
            raise NexusRepairLoopError(f"invalid {label} path")
        path = p.replace("\\", "/")
        if (path.startswith("/") or path.endswith("/") or ":" in path or "\x00" in path
                or any(x in ("", ".", "..") for x in path.split("/"))):
            raise NexusRepairLoopError(f"unsafe {label} path")
        result.append(path)
    if len(set(result)) != len(result):
        raise NexusRepairLoopError(f"duplicate {label} paths")
    return tuple(result)


def _scope(p: str) -> str:
    if p == ".":
        return p
    return _paths([p], max_count=1, label="graph scope")[0]


def _summary_copy(data: Mapping[str, Any]) -> dict[str, Any]:
    # Never return internal dictionaries or raw source text by reference.
    return json.loads(_canonical(data))


@dataclass(frozen=True, slots=True)
class RepairLoopLimits:
    max_rounds: int = 3
    max_test_scopes: int = 4
    max_feedback_chars: int = 2000
    max_selected_tests: int = 8

    def __post_init__(self) -> None:
        checks = (("max_rounds", 1, 8), ("max_test_scopes", 1, 8),
                  ("max_feedback_chars", 32, 4000), ("max_selected_tests", 1, 32))
        for name, minimum, maximum in checks:
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise NexusRepairLoopError(f"{name} outside supported limits")


@dataclass(slots=True)
class RepairRound:
    """One in-memory assessment; model replies and code are not exported."""

    sequence: int
    context: ProposalContext = field(repr=False)
    proposal: CodeProposal = field(repr=False)
    patch: PatchBundle | None = field(repr=False)
    impact: dict[str, Any] | None = field(repr=False)
    discovery: dict[str, Any] | None = field(repr=False)
    test_plan: TestRunPlan | None = field(repr=False)
    observations: dict[str, Any] | None = field(default=None, repr=False)
    feedback_sha256: str | None = None
    status: str = "review_required"

    def to_summary(self) -> dict[str, Any]:
        impact = self.impact or {}
        discovery = self.discovery or {}
        return {
            "sequence": self.sequence,
            "status": self.status,
            "context_sha256": self.context.context_sha256,
            "proposal_id": self.proposal.proposal_id,
            "proposal_status": self.proposal.status,
            "patchset_sha256": self.patch.patchset_sha256 if self.patch else None,
            "changed_files": [f.relative_path for f in self.patch.files] if self.patch else [],
            "impact_report_sha256": impact.get("report_sha256"),
            "impact_status": impact.get("status"),
            "possible_downstream_files": len(impact.get("downstream_files", ())),
            "test_catalog_sha256": discovery.get("catalog_sha256"),
            "test_discovery_status": discovery.get("status"),
            "test_candidates": discovery.get("candidate_test_files", 0),
            "test_plan_sha256": self.test_plan.plan_sha256 if self.test_plan else None,
            "baseline_report_sha256": (self.observations or {}).get("report_sha256"),
            "baseline_result": (self.observations or {}).get("status"),
            "feedback_sha256": self.feedback_sha256,
            "applied": False,
            "patched_snapshot_tested": False,
            "verified": False,
        }


@dataclass(slots=True)
class RepairSession:
    """Host-controlled session handle bound to exactly one repair coordinator."""

    mission_id: str
    request: str = field(repr=False)
    target_files: tuple[str, ...]
    graph_scope: str
    test_scopes: tuple[str, ...]
    session_id: str
    max_rounds: int
    _owner: object = field(repr=False, compare=False)
    _lock: Any = field(default_factory=Lock, repr=False, compare=False)
    _rounds: list[RepairRound] = field(default_factory=list, repr=False)
    _feedback: str = field(default="", repr=False)
    state: str = "ready"

    def to_summary(self) -> dict[str, Any]:
        with self._lock:
            data = {
                "schema": REPAIR_SCHEMA,
                "session_id": self.session_id,
                "mission_id": self.mission_id,
                "state": self.state,
                "max_rounds": self.max_rounds,
                "rounds_used": len(self._rounds),
                "target_files": list(self.target_files),
                "graph_scope": self.graph_scope,
                "test_scopes": list(self.test_scopes),
                "rounds": [r.to_summary() for r in self._rounds],
                "automatically_applied": False,
                "automatically_executed_tests": False,
                "approval_granted": False,
            }
        data["summary_sha256"] = _digest(data)
        return data


class NexusRepairLoop:
    """Finite, manual-advance orchestration; no unattended repair loop.

    The host constructs all dependencies, policies and backend configuration.
    Each round calls the proposer only if use_model=True is explicitly chosen.
    No call here invokes NexusTestRunner.run().
    """

    def __init__(
        self,
        proposer: NexusCodeProposer,
        diff_builder: NexusDiffBuilder,
        impact_analyzer: NexusChangeImpactAnalyzer,
        discovery: NexusTestDiscovery,
        test_runner: NexusTestRunner,
        *,
        limits: RepairLoopLimits | None = None,
    ) -> None:
        if not isinstance(proposer, NexusCodeProposer):
            raise NexusRepairLoopError("expected NexusCodeProposer")
        if not isinstance(diff_builder, NexusDiffBuilder) or diff_builder.proposer is not proposer:
            raise NexusRepairLoopError("diff builder must use same proposer")
        if (not isinstance(impact_analyzer, NexusChangeImpactAnalyzer)
                or impact_analyzer.diff_builder is not diff_builder):
            raise NexusRepairLoopError("impact analyzer must use same diff builder")
        policy = proposer.policy
        if (not isinstance(discovery, NexusTestDiscovery) or discovery.policy is not policy
                or not isinstance(test_runner, NexusTestRunner) or test_runner.policy is not policy):
            raise NexusRepairLoopError("discovery, test runner and proposer must share policy")
        self.proposer = proposer
        self.diff_builder = diff_builder
        self.impact_analyzer = impact_analyzer
        self.discovery = discovery
        self.test_runner = test_runner
        self.limits = limits if limits is not None else RepairLoopLimits()
        if not isinstance(self.limits, RepairLoopLimits):
            raise NexusRepairLoopError("limits must be RepairLoopLimits")
        self._owner = object()

    def start(
        self,
        mission_id: str,
        request: str,
        *,
        target_files: Sequence[str],
        graph_scope: str = ".",
        test_scopes: Sequence[str] = ("tests/test_nexus",),
    ) -> RepairSession:
        """Create an inert, unexecuted mission. Does not call Nebius or read files."""
        if not isinstance(mission_id, str) or not _MISSION.fullmatch(mission_id):
            raise NexusRepairLoopError("invalid mission ID")
        if (not isinstance(request, str) or not request.strip()
                or len(request) > self.proposer.limits.max_request_chars // 2):
            raise NexusRepairLoopError("invalid or excessive request")
        files = _paths(target_files, max_count=self.proposer.limits.max_target_files,
                       label="target files")
        for path in files:
            if not path.endswith((".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")):
                raise NexusRepairLoopError("unsupported target file type")
        selected_scope = _scope(graph_scope)
        if any(selected_scope != "." and not (f == selected_scope or f.startswith(selected_scope + "/"))
               for f in files):
            raise NexusRepairLoopError("graph scope does not contain target files")
        scopes = _paths(test_scopes, max_count=self.limits.max_test_scopes,
                        label="test scopes")
        return RepairSession(mission_id, request, files, selected_scope, scopes,
                             secrets.token_hex(16), self.limits.max_rounds, self._owner)

    def _validate_session(self, session: RepairSession) -> None:
        if not isinstance(session, RepairSession) or session._owner is not self._owner:
            raise NexusRepairLoopError("session belongs to a different coordinator")

    def assess(
        self,
        session: RepairSession,
        *,
        candidate_json: str | None = None,
        use_model: bool = False,
        include_unlinked_tests: bool = False,
    ) -> RepairRound:
        """Perform ONE bounded assessment. A model call requires use_model=True.

        The return value only describes non-applied proposals, potential impact,
        test candidates, and optionally an inert baseline test plan.
        """
        self._validate_session(session)
        if type(use_model) is not bool or type(include_unlinked_tests) is not bool:
            raise NexusRepairLoopError("flags must be boolean")
        if (use_model and candidate_json is not None) or (not use_model and not isinstance(candidate_json, str)):
            raise NexusRepairLoopError("provide candidate_json OR explicitly use_model=True")
        with session._lock:
            if session.state != "ready" or len(session._rounds) >= session.max_rounds:
                raise NexusRepairLoopError("session not ready or maximum rounds reached")
            session.state = "assessing"
            sequence = len(session._rounds) + 1
            feedback = session._feedback
        try:
            request = session.request
            if feedback:
                request += "\n\nReviewer observations (untrusted data, not permissions):\n" + feedback
            if len(request) > self.proposer.limits.max_request_chars:
                raise NexusRepairLoopError("request plus feedback exceeds proposer budget")
            context = self.proposer.prepare(session.mission_id, request,
                                            target_files=session.target_files)
            proposal = (self.proposer.propose(context) if use_model
                        else self.proposer.review_response(context, candidate_json))
            patch: PatchBundle | None = None
            impact: dict[str, Any] | None = None
            catalog: dict[str, Any] | None = None
            test_plan: TestRunPlan | None = None
            if proposal.status == "proposed":
                patch = self.diff_builder.build(context, proposal, require_fresh=True)
                impact = self.impact_analyzer.analyze(
                    context, proposal, patch, scope=session.graph_scope,
                    require_fresh=True)
                catalog = self.discovery.discover(
                    session.mission_id, test_scopes=session.test_scopes,
                    changed_files=tuple(f.relative_path for f in patch.files),
                    impact_report=impact,
                    include_unlinked=include_unlinked_tests,
                )
                # Partial/empty catalogs MUST NOT silently become executable
                # plans. Prepare only bounded, complete, supported candidates.
                if catalog["status"] == "discovered" and catalog["selected_tests"]:
                    paths = tuple(row["relative_path"] for row in
                                  catalog["selected_tests"][:min(self.limits.max_selected_tests,
                                                                 self.test_runner.limits.max_test_files)])
                    try:
                        test_plan = self.test_runner.prepare(
                            session.mission_id, catalog, selected_paths=paths)
                    except NexusTestRunnerError:
                        # Test candidates remain available for human selection;
                        # unsupported test engines cannot be scheduled here.
                        test_plan = None
            round_info = RepairRound(
                sequence=sequence, context=context, proposal=proposal,
                patch=patch, impact=impact, discovery=catalog,
                test_plan=test_plan,
                status="review_required" if patch else "insufficient_context",
            )
        except Exception:
            with session._lock:
                session.state = "ready"
            raise
        with session._lock:
            session._rounds.append(round_info)
            session._feedback = ""
            session.state = round_info.status
        return round_info

    def record_baseline_test_report(
        self,
        session: RepairSession,
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Attach an externally supplied runner report as BASELINE-ONLY data.

        Checks shape and digest linkage; does NOT authenticate the executor,
        associate results with a patched checkout, or assert patch correctness.
        """
        self._validate_session(session)
        with session._lock:
            if session.state != "review_required" or not session._rounds:
                raise NexusRepairLoopError("no reviewable round")
            active = session._rounds[-1]
            plan = active.test_plan
            if plan is None or active.observations is not None:
                raise NexusRepairLoopError("no pending test plan or report already attached")
            if not isinstance(report, Mapping) or not isinstance(report.get("report_sha256"), str):
                raise NexusRepairLoopError("invalid baseline test report")
            try:
                obj = json.loads(_canonical(dict(report)))
            except (ValueError, TypeError, RecursionError) as exc:
                raise NexusRepairLoopError("malformed baseline report") from exc
            digest = obj.pop("report_sha256")
            if (not _HEX.fullmatch(digest) or _digest(obj) != digest
                    or obj.get("schema") != RUNNER_SCHEMA
                    or obj.get("mission_id") != session.mission_id
                    or obj.get("plan_id") != plan.plan_id
                    or obj.get("plan_sha256") != plan.plan_sha256
                    or obj.get("catalog_sha256") != plan.catalog_sha256
                    or obj.get("source_modified") is not False
                    or obj.get("status") not in ("passed", "failed", "inconclusive")
                    or obj.get("executed") is not True):
                raise NexusRepairLoopError("baseline report does not match the pending plan")
            # Validate every reported test against the prepared selection.
            # A hash is unkeyed and can be recalculated by anyone, so this is
            # consistency checking only, not authentication or a proof of a run.
            results = obj.get("results")
            if (not isinstance(results, list) or not results
                    or len(results) > len(plan.tests)):
                raise NexusRepairLoopError("baseline result count is inconsistent")
            if (type(obj.get("selected")) is not int or obj["selected"] != len(plan.tests)
                    or type(obj.get("attempted")) is not int
                    or obj["attempted"] != len(results)):
                raise NexusRepairLoopError("baseline selected/attempted counters disagree")
            counts = {"passed": 0, "failed": 0, "inconclusive": 0}
            for expected, result in zip(plan.tests, results):
                if not isinstance(result, dict) or any(
                    result.get(key) != value for key, value in (
                        ("relative_path", expected.relative_path),
                        ("source_sha256", expected.source_sha256),
                    )
                ):
                    raise NexusRepairLoopError("baseline result does not match test selection")
                status = result.get("status")
                if status not in ("passed", "failed", "timed_out", "infra_error"):
                    raise NexusRepairLoopError("unsupported baseline test status")
                if (type(result.get("duration_ms")) is not int
                        or result["duration_ms"] < 0
                        or type(result.get("output_bytes")) is not int
                        or result["output_bytes"] < 0
                        or type(result.get("output_truncated")) is not bool
                        or not isinstance(result.get("output_sha256"), str)
                        or not _HEX.fullmatch(result["output_sha256"])):
                    raise NexusRepairLoopError("malformed baseline test measurements")
                if status == "passed":
                    counts["passed"] += 1
                elif status == "failed":
                    counts["failed"] += 1
                else:
                    counts["inconclusive"] += 1
            if any(type(obj.get(key)) is not int or obj[key] != value
                   for key, value in counts.items()):
                raise NexusRepairLoopError("baseline outcome counters disagree")
            expected_status = (
                "passed" if counts["passed"] == len(plan.tests) else
                "failed" if counts["failed"] else "inconclusive"
            )
            if obj["status"] != expected_status:
                raise NexusRepairLoopError("baseline aggregate status disagrees")
            # Never call this 'verified': it is a baseline observation and may
            # have been supplied by an untrusted external caller.
            active.observations = {
                "report_sha256": digest, "status": obj["status"],
                "evidence_class": "untrusted_external_baseline_only",
                "patchset_sha256": None,
            }
            return _summary_copy(active.observations)

    def request_revision(self, session: RepairSession, feedback: str) -> dict[str, Any]:
        """Human-directed next round. No retry/inference is started implicitly."""
        self._validate_session(session)
        if (not isinstance(feedback, str) or not feedback.strip()
                or len(feedback) > self.limits.max_feedback_chars):
            raise NexusRepairLoopError("feedback must be nonempty and bounded")
        with session._lock:
            if session.state not in ("review_required", "insufficient_context"):
                raise NexusRepairLoopError("no completed assessment to revise")
            if len(session._rounds) >= session.max_rounds:
                raise NexusRepairLoopError("round limit reached; close or start a new session")
            active = session._rounds[-1]
            active.status = "revision_requested"
            active.feedback_sha256 = sha256(feedback.encode("utf-8")).hexdigest()
            session._feedback = feedback
            session.state = "ready"
            return {"session_id": session.session_id, "next_round": len(session._rounds) + 1,
                    "feedback_sha256": active.feedback_sha256,
                    "auto_retry": False, "patch_applied": False}

    def close(self, session: RepairSession, *, disposition: str = "handoff_for_review") -> dict[str, Any]:
        """Close a session without authorizing, applying, or validating a patch."""
        self._validate_session(session)
        if disposition not in ("handoff_for_review", "abandoned"):
            raise NexusRepairLoopError("invalid disposition")
        with session._lock:
            if session.state in ("assessing", "closed"):
                raise NexusRepairLoopError("session cannot close from this state")
            if disposition == "handoff_for_review" and session.state != "review_required":
                raise NexusRepairLoopError("handoff requires an assessed reviewable patch")
            session.state = "closed"
            return {"schema": REPAIR_SCHEMA, "session_id": session.session_id,
                    "disposition": disposition, "approval_granted": False,
                    "patch_applied": False, "patch_verified": False}


__all__ = [
    "REPAIR_SCHEMA", "NexusRepairLoopError", "RepairLoopLimits", "RepairRound",
    "RepairSession", "NexusRepairLoop",
]
