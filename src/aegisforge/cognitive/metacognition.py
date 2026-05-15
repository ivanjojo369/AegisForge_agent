from __future__ import annotations

"""Metacognitive control layer for AegisForge NCP.

Metacognition is the controller's self-audit layer.  It does not replace the
planner, router, policy bridge, evidence verifier, or uncertainty estimator.
It coordinates them into a final "should we proceed?" report that is explicit,
testable, and aligned with Sprint 4 fair-play constraints.

The report focuses on reusable reasoning quality:
- evidence sufficiency;
- policy/fair-play boundary;
- hardcoding and benchmark-overfitting risk;
- held-out generalization posture;
- tool/adapter contract;
- uncertainty and action-gate consistency;
- cost/context pressure;
- traceability.

It intentionally avoids storing benchmark answers or scenario-specific lookup
tables.  Checks operate on patterns, metadata, evidence status, and action
contracts rather than memorized outputs.
"""

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import json
import math
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .evidence import EvidenceBundle, EvidenceConfig, build_evidence_bundle
from .state import CognitiveDecision, CognitiveState, DecisionOption, WorkingMemoryItem
from .uncertainty import (
    ActionGate,
    UncertaintyAssessment,
    UncertaintyConfig,
    build_decision_options_from_assessment,
    estimate_uncertainty,
)


class SupportsToDict(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


NCP_METACOGNITION_VERSION = "0.1.0"

VALID_CHECK_STATUS = ("pass", "warn", "fail", "block", "not_applicable")
VALID_OVERALL_STATUS = ("ready", "caution", "needs_evidence", "needs_review", "blocked")
VALID_META_ACTIONS = (
    "proceed",
    "proceed_with_caution",
    "gather_more_evidence",
    "run_self_check",
    "request_manual_review",
    "block_or_safe_alternative",
)

CHECK_CATEGORIES = (
    "policy",
    "evidence",
    "uncertainty",
    "tooling",
    "adapter",
    "generalization",
    "hardcoding",
    "safety",
    "cost",
    "traceability",
    "decision",
    "adversarial",
)

REQUIRED_TRACE_PHASES = ("state_init", "attention", "working_memory", "evidence", "uncertainty", "decision")

HARD_CODE_RISK_WORDS = {
    "hardcode",
    "hard-coded",
    "lookup table",
    "gold answer",
    "ground truth",
    "expected answer",
    "answer key",
    "memorized",
    "exact answer",
    "fixture answer",
    "benchmark answer",
    "if scenario",
    "if task id",
}

POLICY_RISK_WORDS = {
    "secret",
    "credential",
    "token",
    "password",
    "private key",
    "exfiltrate",
    "exploit",
    "bypass",
    "jailbreak",
    "delete",
    "destructive",
    "unauthorized",
    "leak",
    "platform exploitation",
    "real-world target",
    "third-party",
}

ADVERSARIAL_WORDS = {
    "ignore previous",
    "system prompt",
    "hidden instruction",
    "prompt injection",
    "jailbreak",
    "override",
    "developer message",
    "tool injection",
    "malicious",
    "poisoned",
    "canary",
}

GENERALIZATION_GOOD_TAGS = {
    "heldout_generalization",
    "evidence_grounding",
    "tool_contract",
    "adapter_contract",
    "traceability",
    "policy_boundary",
    "constraint_tracking",
    "uncertainty_control",
}

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|private[_-]?key|session|cookie|auth)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|ghp_[a-z0-9_]{16,}|xox[baprs]-[a-z0-9-]{10,}|"
    r"bearer\s+[a-z0-9._-]{16,}|api[_-]?key\s*[:=]\s*[a-z0-9._-]{12,})"
)
_NUMBER_HEAVY_RE = re.compile(r"(?:\d+[.,]?){4,}")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_.$%-]{2,}")


class MetaCheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    BLOCK = "block"
    NOT_APPLICABLE = "not_applicable"


class MetaAction(str, Enum):
    PROCEED = "proceed"
    PROCEED_WITH_CAUTION = "proceed_with_caution"
    GATHER_MORE_EVIDENCE = "gather_more_evidence"
    RUN_SELF_CHECK = "run_self_check"
    REQUEST_MANUAL_REVIEW = "request_manual_review"
    BLOCK_OR_SAFE_ALTERNATIVE = "block_or_safe_alternative"


@dataclass(frozen=True, slots=True)
class MetacognitionConfig:
    """Runtime thresholds for metacognitive self-audit."""

    fail_threshold: float = 0.62
    block_threshold: float = 0.82
    hardcoding_blocking: bool = True
    policy_blocking: bool = True
    require_evidence_before_final: bool = True
    require_traceability: bool = True
    require_tool_contract: bool = True
    require_generalization_notes: bool = True
    max_candidate_text_chars: int = 2_500
    max_checks: int = 24
    min_score_for_ready: float = 0.68
    conservative_on_conflict: bool = True
    uncertainty_config: UncertaintyConfig = field(default_factory=UncertaintyConfig)
    evidence_config: EvidenceConfig = field(default_factory=EvidenceConfig)

    def normalized(self) -> "MetacognitionConfig":
        return MetacognitionConfig(
            fail_threshold=min(max(float(self.fail_threshold), 0.0), 1.0),
            block_threshold=min(max(float(self.block_threshold), 0.0), 1.0),
            hardcoding_blocking=bool(self.hardcoding_blocking),
            policy_blocking=bool(self.policy_blocking),
            require_evidence_before_final=bool(self.require_evidence_before_final),
            require_traceability=bool(self.require_traceability),
            require_tool_contract=bool(self.require_tool_contract),
            require_generalization_notes=bool(self.require_generalization_notes),
            max_candidate_text_chars=max(200, int(self.max_candidate_text_chars)),
            max_checks=max(6, int(self.max_checks)),
            min_score_for_ready=min(max(float(self.min_score_for_ready), 0.0), 1.0),
            conservative_on_conflict=bool(self.conservative_on_conflict),
            uncertainty_config=self.uncertainty_config.normalized(),
            evidence_config=self.evidence_config.normalized(),
        )


@dataclass(frozen=True, slots=True)
class MetaCheck:
    """One metacognitive self-check result."""

    name: str
    category: str
    status: str
    severity: float
    confidence: float
    message: str
    recommended_action: str = MetaAction.PROCEED_WITH_CAUTION.value
    evidence_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        name: str,
        category: str,
        status: str,
        severity: float,
        confidence: float,
        message: str,
        recommended_action: str = MetaAction.PROCEED_WITH_CAUTION.value,
        evidence_refs: Iterable[str] = (),
        tags: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "MetaCheck":
        return cls(
            name=_normalize_identifier(name),
            category=_validate_choice(category, CHECK_CATEGORIES, "decision"),
            status=_validate_choice(status, VALID_CHECK_STATUS, "warn"),
            severity=_bounded_float(severity, default=0.0),
            confidence=_bounded_float(confidence, default=0.5),
            message=_clip(_sanitize(message), 760),
            recommended_action=_validate_choice(recommended_action, VALID_META_ACTIONS, MetaAction.PROCEED_WITH_CAUTION.value),
            evidence_refs=tuple(_unique(str(ref) for ref in evidence_refs)),
            tags=tuple(_unique(_normalize_tag(tag) for tag in tags)),
            metadata=_redact_mapping(metadata or {}),
        )

    @property
    def blocks(self) -> bool:
        return self.status == MetaCheckStatus.BLOCK.value

    @property
    def fails(self) -> bool:
        return self.status in {MetaCheckStatus.FAIL.value, MetaCheckStatus.BLOCK.value}

    def weighted_risk(self) -> float:
        status_weight = {
            "pass": 0.0,
            "not_applicable": 0.0,
            "warn": 0.45,
            "fail": 0.76,
            "block": 1.0,
        }.get(self.status, 0.45)
        return _bounded_float(self.severity * status_weight * max(0.25, self.confidence), default=0.0)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        tags = f" [{', '.join(self.tags[:5])}]" if self.tags else ""
        return f"{self.status.upper()} {self.category}/{self.name}={self.severity:.2f}{tags}: {self.message}"


@dataclass(frozen=True, slots=True)
class MetaRiskProfile:
    """Aggregated metacognitive risk dimensions."""

    hardcoding_risk: float = 0.0
    policy_risk: float = 0.0
    evidence_gap_risk: float = 0.0
    uncertainty_risk: float = 0.0
    tool_misuse_risk: float = 0.0
    adapter_risk: float = 0.0
    heldout_generalization_risk: float = 0.0
    cost_risk: float = 0.0
    traceability_risk: float = 0.0
    adversarial_risk: float = 0.0

    @classmethod
    def from_checks(cls, checks: Sequence[MetaCheck]) -> "MetaRiskProfile":
        by_category: dict[str, float] = {}
        for check in checks:
            by_category[check.category] = max(by_category.get(check.category, 0.0), check.weighted_risk())
        return cls(
            hardcoding_risk=by_category.get("hardcoding", 0.0),
            policy_risk=by_category.get("policy", 0.0),
            evidence_gap_risk=by_category.get("evidence", 0.0),
            uncertainty_risk=by_category.get("uncertainty", 0.0),
            tool_misuse_risk=by_category.get("tooling", 0.0),
            adapter_risk=by_category.get("adapter", 0.0),
            heldout_generalization_risk=by_category.get("generalization", 0.0),
            cost_risk=by_category.get("cost", 0.0),
            traceability_risk=by_category.get("traceability", 0.0),
            adversarial_risk=by_category.get("adversarial", 0.0),
        )

    def aggregate(self) -> float:
        weights = {
            "hardcoding_risk": 0.16,
            "policy_risk": 0.16,
            "evidence_gap_risk": 0.14,
            "uncertainty_risk": 0.13,
            "tool_misuse_risk": 0.09,
            "adapter_risk": 0.07,
            "heldout_generalization_risk": 0.10,
            "cost_risk": 0.05,
            "traceability_risk": 0.05,
            "adversarial_risk": 0.05,
        }
        return _bounded_float(
            self.hardcoding_risk * weights["hardcoding_risk"]
            + self.policy_risk * weights["policy_risk"]
            + self.evidence_gap_risk * weights["evidence_gap_risk"]
            + self.uncertainty_risk * weights["uncertainty_risk"]
            + self.tool_misuse_risk * weights["tool_misuse_risk"]
            + self.adapter_risk * weights["adapter_risk"]
            + self.heldout_generalization_risk * weights["heldout_generalization_risk"]
            + self.cost_risk * weights["cost_risk"]
            + self.traceability_risk * weights["traceability_risk"]
            + self.adversarial_risk * weights["adversarial_risk"],
            default=0.0,
        )

    def to_dict(self) -> dict[str, Any]:
        data = _json_safe(asdict(self))
        data["aggregate"] = round(self.aggregate(), 6)
        return data


@dataclass(frozen=True, slots=True)
class MetacognitionReport:
    """Full self-audit report for the current state/action."""

    report_id: str
    version: str
    overall_status: str
    recommended_action: str
    checks: tuple[MetaCheck, ...]
    risk_profile: MetaRiskProfile
    uncertainty: UncertaintyAssessment
    evidence_bundle: EvidenceBundle | None = None
    readiness_score: float = 0.0
    safe_to_finalize: bool = False
    summary: str = ""
    candidate_digest: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def blocking_checks(self) -> tuple[MetaCheck, ...]:
        return tuple(check for check in self.checks if check.blocks)

    def failing_checks(self) -> tuple[MetaCheck, ...]:
        return tuple(check for check in self.checks if check.fails)

    def to_decision(
        self,
        *,
        selected_action: str | None = None,
        turn_index: int = 0,
    ) -> CognitiveDecision:
        action = selected_action or self.recommended_action
        safety_status = "safe"
        if self.overall_status == "blocked":
            safety_status = "blocked"
        elif self.overall_status in {"needs_review", "needs_evidence"}:
            safety_status = "needs_review"
        elif self.overall_status == "caution":
            safety_status = "needs_review"

        return CognitiveDecision.select(
            selected_action=action,
            rationale=self.summary or f"Metacognitive report recommended {self.recommended_action}.",
            options=build_decision_options_from_assessment(self.uncertainty),
            confidence=self.readiness_score,
            expected_utility=self.readiness_score,
            risk=self.risk_profile.aggregate(),
            cost=0.0,
            safety_status=safety_status,
            policy_checks=[check.name for check in self.checks if check.category in {"policy", "hardcoding", "safety"}],
            evidence_refs=[
                ref
                for check in self.checks
                for ref in check.evidence_refs
            ][:16],
            turn_index=turn_index,
            metadata={
                "report_id": self.report_id,
                "overall_status": self.overall_status,
                "recommended_action": self.recommended_action,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "version": self.version,
            "overall_status": self.overall_status,
            "recommended_action": self.recommended_action,
            "checks": [check.to_dict() for check in self.checks],
            "risk_profile": self.risk_profile.to_dict(),
            "uncertainty": self.uncertainty.to_dict(),
            "evidence_bundle": self.evidence_bundle.to_dict() if self.evidence_bundle else None,
            "readiness_score": round(float(self.readiness_score), 6),
            "safe_to_finalize": self.safe_to_finalize,
            "summary": self.summary,
            "candidate_digest": self.candidate_digest,
            "metadata": _json_safe(dict(self.metadata)),
        }

    def compact_context(self) -> str:
        lines = [
            (
                f"NCP metacognition: status={self.overall_status} "
                f"action={self.recommended_action} readiness={self.readiness_score:.2f} "
                f"risk={self.risk_profile.aggregate():.2f}"
            ),
            f"Summary: {self.summary}",
        ]
        blocking = self.blocking_checks()
        failing = self.failing_checks()
        if blocking:
            lines.append("Blocking checks:")
            for check in blocking[:6]:
                lines.append(f"- {check.compact()}")
        elif failing:
            lines.append("Failing checks:")
            for check in failing[:6]:
                lines.append(f"- {check.compact()}")
        else:
            lines.append("Top checks:")
            for check in sorted(self.checks, key=lambda item: item.weighted_risk(), reverse=True)[:8]:
                lines.append(f"- {check.compact()}")
        return "\n".join(lines).strip()


class MetacognitiveController:
    """Self-audit controller for AegisForge NCP."""

    def __init__(self, config: MetacognitionConfig | None = None) -> None:
        self.config = (config or MetacognitionConfig()).normalized()

    def evaluate(
        self,
        state: CognitiveState,
        *,
        candidate_action: str | DecisionOption | CognitiveDecision | None = None,
        candidate_text: str | None = None,
        evidence_bundle: EvidenceBundle | None = None,
        external_signals: Mapping[str, Any] | None = None,
    ) -> MetacognitionReport:
        """Evaluate state/action readiness and return a metacognitive report."""

        candidate_text = _clip(_sanitize(candidate_text or _candidate_text(candidate_action)), self.config.max_candidate_text_chars)
        evidence_bundle = evidence_bundle or build_evidence_bundle(
            state=state,
            candidate_text=candidate_text or None,
            config=self.config.evidence_config,
        )
        uncertainty = estimate_uncertainty(
            state,
            candidate_action=candidate_action,
            external_signals=external_signals,
            config=self.config.uncertainty_config,
        )

        checks: list[MetaCheck] = []
        checks.extend(self._check_policy_boundary(state, candidate_text, uncertainty))
        checks.extend(self._check_hardcoding_risk(state, candidate_text))
        checks.extend(self._check_evidence_grounding(state, evidence_bundle, uncertainty))
        checks.extend(self._check_uncertainty_consistency(state, uncertainty))
        checks.extend(self._check_tool_contract(state))
        checks.extend(self._check_adapter_fit(state))
        checks.extend(self._check_generalization(state, candidate_text))
        checks.extend(self._check_cost_and_context(state))
        checks.extend(self._check_traceability(state))
        checks.extend(self._check_adversarial_context(state, candidate_text))
        checks.extend(self._check_candidate_action(state, candidate_action, uncertainty))
        checks = self._dedupe_checks(checks)[: self.config.max_checks]

        risk_profile = MetaRiskProfile.from_checks(checks)
        readiness_score = self._readiness_score(state, checks, risk_profile, evidence_bundle, uncertainty)
        overall_status = self._overall_status(
            checks=checks,
            readiness_score=readiness_score,
            risk_profile=risk_profile,
            uncertainty=uncertainty,
            evidence_bundle=evidence_bundle,
        )
        recommended_action = self._recommended_action(
            overall_status=overall_status,
            checks=checks,
            uncertainty=uncertainty,
            evidence_bundle=evidence_bundle,
        )
        summary = self._summary(
            overall_status=overall_status,
            recommended_action=recommended_action,
            checks=checks,
            readiness_score=readiness_score,
            risk_profile=risk_profile,
        )
        report_id = _stable_id(
            "meta",
            state.episode_id,
            overall_status,
            recommended_action,
            readiness_score,
            [check.name for check in checks],
            evidence_bundle.bundle_id if evidence_bundle else None,
            uncertainty.assessment_id,
        )[:24]

        return MetacognitionReport(
            report_id=report_id,
            version=NCP_METACOGNITION_VERSION,
            overall_status=overall_status,
            recommended_action=recommended_action,
            checks=tuple(checks),
            risk_profile=risk_profile,
            uncertainty=uncertainty,
            evidence_bundle=evidence_bundle,
            readiness_score=readiness_score,
            safe_to_finalize=overall_status == "ready",
            summary=summary,
            candidate_digest=_stable_digest(candidate_text) if candidate_text else None,
            metadata={
                "episode_id": state.episode_id,
                "domain": state.task_theory.domain,
                "scenario_id": state.task_theory.scenario_id,
                "scenario_family": state.task_theory.scenario_family,
                "assessment_mode": state.task_theory.assessment_mode,
                "external_signal_keys": sorted(_as_mapping(external_signals).keys()),
            },
        )

    def update_state(
        self,
        state: CognitiveState,
        *,
        candidate_action: str | DecisionOption | CognitiveDecision | None = None,
        candidate_text: str | None = None,
        evidence_bundle: EvidenceBundle | None = None,
        external_signals: Mapping[str, Any] | None = None,
        append_decision: bool = False,
    ) -> CognitiveState:
        """Attach metacognitive trace and optionally a decision to state."""

        report = self.evaluate(
            state,
            candidate_action=candidate_action,
            candidate_text=candidate_text,
            evidence_bundle=evidence_bundle,
            external_signals=external_signals,
        )
        updated = state.update_uncertainty(report.uncertainty.to_estimate())
        severity = "critical" if report.overall_status == "blocked" else "warning" if report.overall_status != "ready" else "info"
        updated = updated.append_trace(
            phase="metacognition",
            message=report.compact_context(),
            severity=severity,
            refs=[report.report_id, report.uncertainty.assessment_id],
            metadata={"report": report.to_dict()},
        ).refresh_scorecard()
        if append_decision:
            updated = updated.decide(report.to_decision(turn_index=updated.turn_index))
        return updated.refresh_scorecard()

    def select_option(
        self,
        report: MetacognitionReport,
        options: Sequence[DecisionOption],
    ) -> DecisionOption | None:
        """Select the safest utility option under the report's risk profile."""

        if not options:
            return None

        blocked = report.overall_status == "blocked"
        scored: list[tuple[float, DecisionOption]] = []
        for option in options:
            option_action = option.action.lower()
            policy_sensitive = _contains_any(option_action, POLICY_RISK_WORDS) or _contains_any(option_action, HARD_CODE_RISK_WORDS)
            safety_penalty = 0.35 if policy_sensitive else 0.0
            if blocked and not _contains_any(option_action, {"safe", "review", "block", "evidence"}):
                safety_penalty += 0.40
            score = option.expected_utility + option.confidence - option.risk - option.cost - safety_penalty
            scored.append((score, option))
        scored.sort(key=lambda item: (item[0], item[1].confidence), reverse=True)
        return scored[0][1]

    def _check_policy_boundary(
        self,
        state: CognitiveState,
        candidate_text: str,
        uncertainty: UncertaintyAssessment,
    ) -> list[MetaCheck]:
        text = " ".join([
            state.task_theory.objective,
            candidate_text,
            " ".join(item.content for item in state.working_memory[:24]),
        ]).lower()
        hits = sorted(word for word in POLICY_RISK_WORDS if word in text)
        denied_hits = [
            denied for denied in state.policy_boundary.denied_behaviors
            if denied.replace("_", " ").lower() in text
        ]

        if uncertainty.risk_level in {"high", "critical"} or hits or denied_hits:
            severity = 0.86 if uncertainty.risk_level == "critical" else min(1.0, 0.48 + 0.045 * (len(hits) + len(denied_hits)))
            status = "block" if self.config.policy_blocking and severity >= self.config.block_threshold else "fail" if severity >= self.config.fail_threshold else "warn"
            return [
                MetaCheck.new(
                    name="policy_boundary",
                    category="policy",
                    status=status,
                    severity=severity,
                    confidence=0.82,
                    message="Policy/fair-play boundary risk is active; safe alternatives or review may be required.",
                    recommended_action="block_or_safe_alternative" if status == "block" else "request_manual_review",
                    tags=("policy_boundary", *hits[:8], *denied_hits[:6]),
                    metadata={"risk_level": uncertainty.risk_level, "hits": hits[:12], "denied_hits": denied_hits[:12]},
                )
            ]

        return [
            MetaCheck.new(
                name="policy_boundary",
                category="policy",
                status="pass",
                severity=0.08,
                confidence=0.76,
                message="No obvious policy/fair-play boundary violation detected.",
                recommended_action="proceed",
                tags=("policy_boundary",),
            )
        ]

    def _check_hardcoding_risk(self, state: CognitiveState, candidate_text: str) -> list[MetaCheck]:
        text_parts = [candidate_text, state.task_theory.objective]
        text_parts.extend(item.content for item in state.working_memory if item.source in {"plan", "memory", "episodic_memory"})
        text = " ".join(text_parts).lower()
        hits = sorted(word for word in HARD_CODE_RISK_WORDS if word in text)
        scenario_conditioning = self._scenario_conditioning_risk(state, text)
        numeric_exact = bool(_NUMBER_HEAVY_RE.search(candidate_text)) and len(candidate_text) < 600

        severity = min(1.0, 0.20 + 0.10 * len(hits) + (0.22 if scenario_conditioning else 0.0) + (0.14 if numeric_exact else 0.0))
        if hits or scenario_conditioning or numeric_exact:
            status = "block" if self.config.hardcoding_blocking and severity >= self.config.block_threshold else "fail" if severity >= self.config.fail_threshold else "warn"
            return [
                MetaCheck.new(
                    name="hardcoding_and_lookup_risk",
                    category="hardcoding",
                    status=status,
                    severity=severity,
                    confidence=0.80,
                    message="Candidate context contains possible hardcoding, answer-key, or scenario-specific lookup-table patterns.",
                    recommended_action="block_or_safe_alternative" if status == "block" else "run_self_check",
                    tags=("hardcoding_guard", *hits[:8]),
                    metadata={
                        "hits": hits[:12],
                        "scenario_conditioning": scenario_conditioning,
                        "numeric_exact": numeric_exact,
                    },
                )
            ]

        return [
            MetaCheck.new(
                name="hardcoding_and_lookup_risk",
                category="hardcoding",
                status="pass",
                severity=0.06,
                confidence=0.74,
                message="No obvious hardcoded-answer or task-specific lookup-table pattern detected.",
                recommended_action="proceed",
                tags=("hardcoding_guard",),
            )
        ]

    def _check_evidence_grounding(
        self,
        state: CognitiveState,
        evidence_bundle: EvidenceBundle,
        uncertainty: UncertaintyAssessment,
    ) -> list[MetaCheck]:
        checks: list[MetaCheck] = []
        requires_evidence = bool(state.task_theory.evidence_requirements or state.task_theory.required_tools)
        if self.config.require_evidence_before_final:
            requires_evidence = True

        if evidence_bundle.conflict_ratio > 0:
            checks.append(
                MetaCheck.new(
                    name="conflicting_evidence",
                    category="evidence",
                    status="fail" if not self.config.conservative_on_conflict else "block",
                    severity=min(1.0, 0.72 + evidence_bundle.conflict_ratio),
                    confidence=0.86,
                    message="Evidence bundle contains conflicting claim assessments.",
                    recommended_action="request_manual_review",
                    evidence_refs=[record.evidence_id for record in evidence_bundle.records()[:8]],
                    tags=("evidence_grounding", "conflicting_evidence"),
                    metadata={"bundle_id": evidence_bundle.bundle_id, "conflict_ratio": evidence_bundle.conflict_ratio},
                )
            )
        elif evidence_bundle.unsupported_required_count:
            checks.append(
                MetaCheck.new(
                    name="unsupported_required_evidence",
                    category="evidence",
                    status="fail",
                    severity=min(1.0, 0.55 + 0.07 * evidence_bundle.unsupported_required_count),
                    confidence=0.82,
                    message="Required claims are not sufficiently supported.",
                    recommended_action="gather_more_evidence",
                    evidence_refs=[record.evidence_id for record in evidence_bundle.records()[:8]],
                    tags=("evidence_grounding", "needs_evidence"),
                    metadata={"unsupported_required_count": evidence_bundle.unsupported_required_count},
                )
            )
        elif requires_evidence and evidence_bundle.supported_ratio < 0.35:
            checks.append(
                MetaCheck.new(
                    name="weak_evidence_grounding",
                    category="evidence",
                    status="warn",
                    severity=0.48,
                    confidence=0.76,
                    message="Evidence support ratio is low for a task that should be grounded.",
                    recommended_action="gather_more_evidence",
                    tags=("evidence_grounding", "needs_evidence"),
                    metadata={"supported_ratio": evidence_bundle.supported_ratio},
                )
            )
        else:
            checks.append(
                MetaCheck.new(
                    name="evidence_grounding",
                    category="evidence",
                    status="pass",
                    severity=max(0.02, 0.20 * (1.0 - evidence_bundle.supported_ratio)),
                    confidence=0.75,
                    message="Evidence grounding is sufficient for current stage.",
                    recommended_action="proceed",
                    tags=("evidence_grounding",),
                    metadata={"supported_ratio": evidence_bundle.supported_ratio},
                )
            )

        if uncertainty.evidence_gaps:
            checks.append(
                MetaCheck.new(
                    name="uncertainty_evidence_gaps",
                    category="evidence",
                    status="warn",
                    severity=min(1.0, max(gap.severity for gap in uncertainty.evidence_gaps)),
                    confidence=0.78,
                    message="Uncertainty estimator reported unresolved evidence gaps.",
                    recommended_action=uncertainty.recommended_action if uncertainty.recommended_action in VALID_META_ACTIONS else "gather_more_evidence",
                    evidence_refs=[gap.gap_id for gap in uncertainty.evidence_gaps[:8]],
                    tags=("needs_evidence",),
                    metadata={"gap_count": len(uncertainty.evidence_gaps)},
                )
            )

        return checks

    def _check_uncertainty_consistency(
        self,
        state: CognitiveState,
        uncertainty: UncertaintyAssessment,
    ) -> list[MetaCheck]:
        if uncertainty.uncertainty_level in {"critical", "high"}:
            return [
                MetaCheck.new(
                    name="uncertainty_level",
                    category="uncertainty",
                    status="fail" if uncertainty.uncertainty_level == "high" else "block",
                    severity=uncertainty.uncertainty_score,
                    confidence=0.84,
                    message=f"Uncertainty level is {uncertainty.uncertainty_level}; recommended action is {uncertainty.recommended_action}.",
                    recommended_action=_map_uncertainty_action(uncertainty.recommended_action),
                    evidence_refs=[uncertainty.assessment_id],
                    tags=("uncertainty_control", uncertainty.uncertainty_level),
                )
            ]
        if uncertainty.action_gate.allowed is False:
            return [
                MetaCheck.new(
                    name="action_gate_blocked",
                    category="uncertainty",
                    status="block",
                    severity=uncertainty.action_gate.risk,
                    confidence=uncertainty.action_gate.confidence,
                    message=uncertainty.action_gate.reason,
                    recommended_action="block_or_safe_alternative",
                    evidence_refs=[uncertainty.assessment_id],
                    tags=("action_gate", "policy_boundary"),
                    metadata=uncertainty.action_gate.to_dict(),
                )
            ]
        return [
            MetaCheck.new(
                name="uncertainty_level",
                category="uncertainty",
                status="pass" if uncertainty.uncertainty_level == "low" else "warn",
                severity=uncertainty.uncertainty_score,
                confidence=uncertainty.confidence,
                message=f"Uncertainty level is {uncertainty.uncertainty_level}.",
                recommended_action="proceed" if uncertainty.uncertainty_level == "low" else "proceed_with_caution",
                evidence_refs=[uncertainty.assessment_id],
                tags=("uncertainty_control", uncertainty.uncertainty_level),
            )
        ]

    def _check_tool_contract(self, state: CognitiveState) -> list[MetaCheck]:
        required = set(_normalize_identifier(tool) for tool in state.task_theory.required_tools)
        if not required:
            return [
                MetaCheck.new(
                    name="tool_contract",
                    category="tooling",
                    status="not_applicable",
                    severity=0.0,
                    confidence=0.65,
                    message="No required tools declared for this task.",
                    recommended_action="proceed",
                    tags=("tool_contract",),
                )
            ]

        available = set(_normalize_identifier(action) for action in state.task_theory.available_actions)
        available |= set(_normalize_identifier(action) for action in state.adapter_profile.available_actions)
        observed = set()
        for record in state.evidence:
            observed.add(_normalize_identifier(record.source))
            observed.add(_normalize_identifier(record.kind))
        for item in state.working_memory:
            observed.add(_normalize_identifier(item.source))
            observed |= set(_normalize_identifier(ref) for ref in item.evidence_refs)

        missing = sorted(tool for tool in required if tool not in available and tool not in observed)
        if missing and self.config.require_tool_contract:
            return [
                MetaCheck.new(
                    name="required_tool_contract",
                    category="tooling",
                    status="warn",
                    severity=min(1.0, 0.42 + 0.08 * len(missing)),
                    confidence=0.78,
                    message="Some required tools/actions are not yet available or observed.",
                    recommended_action="gather_more_evidence",
                    tags=("tool_contract", "needs_evidence"),
                    metadata={"missing": missing[:12], "required": sorted(required)[:16], "available": sorted(available)[:16]},
                )
            ]

        return [
            MetaCheck.new(
                name="required_tool_contract",
                category="tooling",
                status="pass",
                severity=0.08,
                confidence=0.72,
                message="Required tool/action contract appears satisfiable.",
                recommended_action="proceed",
                tags=("tool_contract",),
                metadata={"required": sorted(required)[:16]},
            )
        ]

    def _check_adapter_fit(self, state: CognitiveState) -> list[MetaCheck]:
        domain = state.task_theory.domain
        adapter = state.adapter_profile.adapter
        if domain == "unknown" or adapter == "unknown":
            return [
                MetaCheck.new(
                    name="adapter_fit",
                    category="adapter",
                    status="warn",
                    severity=0.46,
                    confidence=0.70,
                    message="Domain or adapter is unknown; routing may be brittle.",
                    recommended_action="gather_more_evidence",
                    tags=("adapter_contract",),
                )
            ]

        if state.adapter_profile.domain != "unknown" and _normalize_identifier(domain) != _normalize_identifier(state.adapter_profile.domain):
            return [
                MetaCheck.new(
                    name="adapter_domain_mismatch",
                    category="adapter",
                    status="warn",
                    severity=0.52,
                    confidence=0.72,
                    message="Task domain and adapter profile domain differ.",
                    recommended_action="run_self_check",
                    tags=("adapter_contract",),
                    metadata={"task_domain": domain, "adapter_domain": state.adapter_profile.domain},
                )
            ]

        return [
            MetaCheck.new(
                name="adapter_fit",
                category="adapter",
                status="pass",
                severity=0.05,
                confidence=0.72,
                message="Adapter profile is aligned with the task domain.",
                recommended_action="proceed",
                tags=("adapter_contract",),
            )
        ]

    def _check_generalization(self, state: CognitiveState, candidate_text: str) -> list[MetaCheck]:
        notes = state.task_theory.generalization_notes
        tags = set()
        for item in state.working_memory:
            tags.update(item.tags)

        good_signals = len(tags & GENERALIZATION_GOOD_TAGS) + len(notes)
        scenario_name = _normalize_identifier(state.task_theory.scenario_name or state.task_theory.scenario_id or "")
        candidate_norm = _normalize_identifier(candidate_text)
        scenario_overfit = bool(scenario_name and scenario_name in candidate_norm and _contains_any(candidate_text.lower(), {"if", "case", "exact", "lookup"}))

        if scenario_overfit:
            return [
                MetaCheck.new(
                    name="heldout_generalization",
                    category="generalization",
                    status="fail",
                    severity=0.68,
                    confidence=0.76,
                    message="Candidate appears to branch on a scenario-specific identifier; this may harm held-out generality.",
                    recommended_action="run_self_check",
                    tags=("heldout_generalization", "overfit_risk"),
                    metadata={"scenario_name": scenario_name},
                )
            ]

        if self.config.require_generalization_notes and good_signals < 2:
            return [
                MetaCheck.new(
                    name="heldout_generalization",
                    category="generalization",
                    status="warn",
                    severity=0.36,
                    confidence=0.66,
                    message="Few explicit generalization signals are present; keep strategy pattern-based rather than scenario-specific.",
                    recommended_action="proceed_with_caution",
                    tags=("heldout_generalization",),
                    metadata={"good_signals": good_signals},
                )
            ]

        return [
            MetaCheck.new(
                name="heldout_generalization",
                category="generalization",
                status="pass",
                severity=0.06,
                confidence=0.72,
                message="Generalization posture is acceptable: strategy signals are pattern-level, not answer-level.",
                recommended_action="proceed",
                tags=("heldout_generalization",),
                metadata={"good_signals": good_signals},
            )
        ]

    def _check_cost_and_context(self, state: CognitiveState) -> list[MetaCheck]:
        memory_count = len(state.working_memory)
        cost_efficiency = getattr(state.scorecard, "cost_efficiency", 0.0)
        if memory_count > 40 or cost_efficiency < 0.45:
            return [
                MetaCheck.new(
                    name="cost_and_context_pressure",
                    category="cost",
                    status="warn",
                    severity=min(1.0, 0.34 + max(0, memory_count - 30) / 100 + max(0, 0.45 - cost_efficiency)),
                    confidence=0.68,
                    message="Context or cost pressure is elevated; compact low-salience memory before continuing.",
                    recommended_action="proceed_with_caution",
                    tags=("context_budget", "cost_efficiency"),
                    metadata={"memory_count": memory_count, "cost_efficiency": cost_efficiency},
                )
            ]
        return [
            MetaCheck.new(
                name="cost_and_context_pressure",
                category="cost",
                status="pass",
                severity=0.06,
                confidence=0.65,
                message="No significant cost/context pressure detected.",
                recommended_action="proceed",
                tags=("context_budget",),
                metadata={"memory_count": memory_count, "cost_efficiency": cost_efficiency},
            )
        ]

    def _check_traceability(self, state: CognitiveState) -> list[MetaCheck]:
        if not self.config.require_traceability:
            return []
        phases = {event.phase for event in state.trace}
        missing = [phase for phase in REQUIRED_TRACE_PHASES if phase not in phases]
        if len(missing) >= 3:
            return [
                MetaCheck.new(
                    name="traceability",
                    category="traceability",
                    status="warn",
                    severity=min(1.0, 0.28 + 0.08 * len(missing)),
                    confidence=0.70,
                    message="Several expected NCP trace phases are missing.",
                    recommended_action="run_self_check",
                    tags=("traceability", "auditability"),
                    metadata={"missing": missing, "present": sorted(phases)},
                )
            ]
        return [
            MetaCheck.new(
                name="traceability",
                category="traceability",
                status="pass",
                severity=0.08,
                confidence=0.68,
                message="Traceability is sufficient for audit.",
                recommended_action="proceed",
                tags=("traceability", "auditability"),
                metadata={"missing": missing, "present": sorted(phases)},
            )
        ]

    def _check_adversarial_context(self, state: CognitiveState, candidate_text: str) -> list[MetaCheck]:
        text = " ".join([candidate_text, state.task_theory.objective, " ".join(item.content for item in state.working_memory[:20])]).lower()
        hits = sorted(word for word in ADVERSARIAL_WORDS if word in text)
        risk_tags = [
            tag
            for item in state.working_memory
            for tag in item.tags
            if str(tag).startswith("risk:") or tag in {"prompt_injection", "context_injection"}
        ]
        if hits or risk_tags:
            return [
                MetaCheck.new(
                    name="adversarial_context",
                    category="adversarial",
                    status="warn",
                    severity=min(1.0, 0.42 + 0.04 * (len(hits) + len(risk_tags))),
                    confidence=0.74,
                    message="Adversarial or injection-like context is present; prioritize policy and evidence channels.",
                    recommended_action="run_self_check",
                    tags=("adversarial_self_check", *hits[:8], *risk_tags[:8]),
                    metadata={"hits": hits[:12], "risk_tags": risk_tags[:12]},
                )
            ]
        return [
            MetaCheck.new(
                name="adversarial_context",
                category="adversarial",
                status="pass",
                severity=0.04,
                confidence=0.64,
                message="No obvious adversarial context pattern detected.",
                recommended_action="proceed",
                tags=("adversarial_self_check",),
            )
        ]

    def _check_candidate_action(
        self,
        state: CognitiveState,
        candidate_action: str | DecisionOption | CognitiveDecision | None,
        uncertainty: UncertaintyAssessment,
    ) -> list[MetaCheck]:
        gate = uncertainty.action_gate
        if not gate.allowed:
            return [
                MetaCheck.new(
                    name="candidate_action_gate",
                    category="decision",
                    status="block",
                    severity=max(0.80, gate.risk),
                    confidence=gate.confidence,
                    message=gate.reason,
                    recommended_action="block_or_safe_alternative",
                    tags=("action_gate",),
                    metadata=gate.to_dict(),
                )
            ]
        if gate.recommended_action in {"gather_more_evidence", "pause_or_request_review", "block_or_safe_alternative"}:
            return [
                MetaCheck.new(
                    name="candidate_action_gate",
                    category="decision",
                    status="warn",
                    severity=max(0.36, gate.risk),
                    confidence=gate.confidence,
                    message=gate.reason,
                    recommended_action=_map_uncertainty_action(gate.recommended_action),
                    tags=("action_gate",),
                    metadata=gate.to_dict(),
                )
            ]
        return [
            MetaCheck.new(
                name="candidate_action_gate",
                category="decision",
                status="pass",
                severity=gate.risk,
                confidence=gate.confidence,
                message=gate.reason,
                recommended_action="proceed",
                tags=("action_gate",),
                metadata=gate.to_dict(),
            )
        ]

    @staticmethod
    def _scenario_conditioning_risk(state: CognitiveState, text: str) -> bool:
        scenario_id = _normalize_identifier(state.task_theory.scenario_id or "")
        scenario_name = _normalize_identifier(state.task_theory.scenario_name or "")
        normalized = _normalize_identifier(text)
        if scenario_id and scenario_id in normalized and _contains_any(text, {"if", "case", "lookup", "return", "answer"}):
            return True
        if scenario_name and scenario_name in normalized and _contains_any(text, {"if", "case", "lookup", "return", "answer"}):
            return True
        return False

    @staticmethod
    def _dedupe_checks(checks: Sequence[MetaCheck]) -> list[MetaCheck]:
        by_key: dict[tuple[str, str], MetaCheck] = {}
        for check in checks:
            key = (check.category, check.name)
            current = by_key.get(key)
            if current is None or _status_rank(check.status) > _status_rank(current.status) or check.severity > current.severity:
                by_key[key] = check
        return sorted(by_key.values(), key=lambda item: (-_status_rank(item.status), -item.severity, item.category, item.name))

    def _readiness_score(
        self,
        state: CognitiveState,
        checks: Sequence[MetaCheck],
        risk_profile: MetaRiskProfile,
        evidence_bundle: EvidenceBundle,
        uncertainty: UncertaintyAssessment,
    ) -> float:
        base = 0.70
        aggregate_risk = risk_profile.aggregate()
        fail_penalty = sum(0.10 for check in checks if check.status == "fail")
        block_penalty = sum(0.20 for check in checks if check.status == "block")
        warn_penalty = min(0.18, sum(0.025 for check in checks if check.status == "warn"))
        evidence_bonus = min(0.10, evidence_bundle.supported_ratio * 0.10)
        trace_bonus = 0.04 if any(check.category == "traceability" and check.status == "pass" for check in checks) else 0.0
        scorecard_bonus = min(0.08, max(0.0, state.scorecard.aggregate() - 0.50) * 0.18)
        uncertainty_penalty = max(0.0, uncertainty.uncertainty_score - 0.30) * 0.24
        readiness = base + evidence_bonus + trace_bonus + scorecard_bonus - aggregate_risk - fail_penalty - block_penalty - warn_penalty - uncertainty_penalty
        return _bounded_float(readiness, default=0.40)

    def _overall_status(
        self,
        *,
        checks: Sequence[MetaCheck],
        readiness_score: float,
        risk_profile: MetaRiskProfile,
        uncertainty: UncertaintyAssessment,
        evidence_bundle: EvidenceBundle,
    ) -> str:
        if any(check.status == "block" for check in checks):
            return "blocked"
        if uncertainty.risk_level == "critical":
            return "blocked"
        if any(check.status == "fail" and check.category in {"policy", "hardcoding", "safety"} for check in checks):
            return "needs_review"
        if evidence_bundle.grounding_decision in {"verify_more", "escalate_conflict"}:
            return "needs_evidence" if evidence_bundle.grounding_decision == "verify_more" else "needs_review"
        if uncertainty.uncertainty_level in {"high", "critical"}:
            return "needs_evidence" if uncertainty.uncertainty_level == "high" else "needs_review"
        if readiness_score < self.config.min_score_for_ready or any(check.status == "warn" for check in checks):
            return "caution"
        return "ready"

    @staticmethod
    def _recommended_action(
        *,
        overall_status: str,
        checks: Sequence[MetaCheck],
        uncertainty: UncertaintyAssessment,
        evidence_bundle: EvidenceBundle,
    ) -> str:
        if overall_status == "blocked":
            return "block_or_safe_alternative"
        if overall_status == "needs_review":
            return "request_manual_review"
        if overall_status == "needs_evidence":
            return "gather_more_evidence"
        action_votes = [check.recommended_action for check in checks if check.recommended_action != "proceed"]
        if uncertainty.recommended_action in {"gather_more_evidence", "pause_or_request_review", "block_or_safe_alternative"}:
            return _map_uncertainty_action(uncertainty.recommended_action)
        if evidence_bundle.grounding_decision == "policy_review":
            return "run_self_check"
        if "run_self_check" in action_votes:
            return "run_self_check"
        if overall_status == "caution":
            return "proceed_with_caution"
        return "proceed"

    @staticmethod
    def _summary(
        *,
        overall_status: str,
        recommended_action: str,
        checks: Sequence[MetaCheck],
        readiness_score: float,
        risk_profile: MetaRiskProfile,
    ) -> str:
        counts: dict[str, int] = {}
        for check in checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        counts_text = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        top_risk = _top_risk_name(risk_profile)
        return (
            f"Metacognitive status={overall_status}; recommended_action={recommended_action}; "
            f"readiness={readiness_score:.2f}; aggregate_risk={risk_profile.aggregate():.2f}; "
            f"top_risk={top_risk}; checks({counts_text})."
        )


def run_metacognitive_check(
    state: CognitiveState,
    *,
    candidate_action: str | DecisionOption | CognitiveDecision | None = None,
    candidate_text: str | None = None,
    evidence_bundle: EvidenceBundle | None = None,
    external_signals: Mapping[str, Any] | None = None,
    config: MetacognitionConfig | None = None,
) -> MetacognitionReport:
    """Convenience wrapper for tests and controller integration."""

    return MetacognitiveController(config=config).evaluate(
        state,
        candidate_action=candidate_action,
        candidate_text=candidate_text,
        evidence_bundle=evidence_bundle,
        external_signals=external_signals,
    )


def update_state_metacognition(
    state: CognitiveState,
    *,
    candidate_action: str | DecisionOption | CognitiveDecision | None = None,
    candidate_text: str | None = None,
    evidence_bundle: EvidenceBundle | None = None,
    external_signals: Mapping[str, Any] | None = None,
    config: MetacognitionConfig | None = None,
    append_decision: bool = False,
) -> CognitiveState:
    """Run metacognitive evaluation and append the report to state trace."""

    return MetacognitiveController(config=config).update_state(
        state,
        candidate_action=candidate_action,
        candidate_text=candidate_text,
        evidence_bundle=evidence_bundle,
        external_signals=external_signals,
        append_decision=append_decision,
    )


def select_safest_option(
    report: MetacognitionReport,
    options: Sequence[DecisionOption],
    *,
    config: MetacognitionConfig | None = None,
) -> DecisionOption | None:
    """Select the safest option given a metacognitive report."""

    return MetacognitiveController(config=config).select_option(report, options)


def metacognitive_memory_items(
    report: MetacognitionReport,
    *,
    turn_index: int = 0,
    max_items: int = 8,
) -> tuple[WorkingMemoryItem, ...]:
    """Convert a report into compact working-memory items."""

    items: list[WorkingMemoryItem] = [
        WorkingMemoryItem(
            key=f"meta_{report.report_id[:18]}",
            content=report.summary,
            source="metacognition",
            salience=max(0.45, 1.0 - report.readiness_score),
            turn_index=turn_index,
            tags=("metacognition", report.overall_status, report.recommended_action),
            evidence_refs=(report.report_id, report.uncertainty.assessment_id),
            ttl_turns=8,
            locked=report.overall_status in {"blocked", "needs_review"},
            metadata={"report_id": report.report_id, "risk": report.risk_profile.to_dict()},
        )
    ]
    for check in sorted(report.checks, key=lambda item: item.weighted_risk(), reverse=True)[: max(0, max_items - 1)]:
        items.append(
            WorkingMemoryItem(
                key=f"meta_{check.category}_{check.name}"[:64],
                content=check.message,
                source="metacognition",
                salience=max(0.25, check.weighted_risk()),
                turn_index=turn_index,
                tags=("metacognition", check.category, check.status, *check.tags[:6]),
                evidence_refs=check.evidence_refs,
                ttl_turns=6,
                locked=check.status in {"block", "fail"},
                metadata={"recommended_action": check.recommended_action, "severity": check.severity},
            )
        )
    return tuple(items)


def _candidate_text(candidate: str | DecisionOption | CognitiveDecision | None) -> str:
    if candidate is None:
        return ""
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, DecisionOption):
        return f"{candidate.action}. {candidate.rationale}"
    if isinstance(candidate, CognitiveDecision):
        return f"{candidate.selected_action}. {candidate.rationale}"
    data = _as_mapping(candidate)
    return str(data.get("action") or data.get("selected_action") or data.get("rationale") or "")


def _map_uncertainty_action(action: str) -> str:
    action = str(action or "")
    mapping = {
        "proceed": "proceed",
        "proceed_with_caution": "proceed_with_caution",
        "gather_more_evidence": "gather_more_evidence",
        "ask_clarifying_question": "gather_more_evidence",
        "pause_or_request_review": "request_manual_review",
        "block_or_safe_alternative": "block_or_safe_alternative",
    }
    return mapping.get(action, "proceed_with_caution")


def _status_rank(status: str) -> int:
    return {
        "not_applicable": 0,
        "pass": 1,
        "warn": 2,
        "fail": 3,
        "block": 4,
    }.get(status, 2)


def _top_risk_name(profile: MetaRiskProfile) -> str:
    data = asdict(profile)
    if not data:
        return "none"
    key, value = max(data.items(), key=lambda item: item[1])
    return key if value > 0 else "none"


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            result = value.to_dict()
            return dict(result) if isinstance(result, Mapping) else {}
        except Exception:
            return {}
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            result = value.as_dict()
            return dict(result) if isinstance(result, Mapping) else {}
        except Exception:
            return {}
    if is_dataclass(value):
        try:
            return dict(asdict(value))
        except Exception:
            return {}
    if hasattr(value, "__dict__"):
        return {key: val for key, val in vars(value).items() if not key.startswith("_")}
    return {}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            return repr(value)
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            return _json_safe(value.as_dict())
        except Exception:
            return repr(value)
    return repr(value)


def _redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        key_text = str(key)
        if _SECRET_KEY_RE.search(key_text):
            out[key_text] = "<redacted>"
        elif isinstance(value, Mapping):
            out[key_text] = _redact_mapping(value)
        elif isinstance(value, list):
            out[key_text] = [_redact_value(item) for item in value]
        elif isinstance(value, tuple):
            out[key_text] = tuple(_redact_value(item) for item in value)
        else:
            out[key_text] = _redact_value(value)
    return out


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize(value)
    if isinstance(value, Mapping):
        return _redact_mapping(value)
    return _json_safe(value)


def _sanitize(text: Any) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    compact = _SECRET_VALUE_RE.sub("<redacted>", compact)
    return compact


def _stable_id(prefix: str, *parts: Any) -> str:
    blob = json.dumps([_json_safe(part) for part in parts], ensure_ascii=False, sort_keys=True)
    return f"{prefix}_{sha256(blob.encode('utf-8')).hexdigest()}"


def _stable_digest(text: str) -> str:
    return sha256(_sanitize(text).encode("utf-8")).hexdigest()[:16]


def _normalize_identifier(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("τ²", "tau2").replace("t²", "tau2")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def _normalize_tag(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("risk:") or text.startswith("uncertainty:"):
        prefix, rest = text.split(":", 1)
        return f"{prefix}:{_normalize_identifier(rest)}"
    return _normalize_identifier(text)


def _validate_choice(value: Any, choices: Sequence[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in set(choices) else default


def _bounded_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return min(1.0, max(0.0, number))


def _clip(text: Any, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen or text == "unknown":
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def _contains_any(text: str, words: Iterable[str]) -> bool:
    lowered = str(text or "").lower()
    return any(word in lowered for word in words)


__all__ = [
    "NCP_METACOGNITION_VERSION",
    "MetaAction",
    "MetaCheck",
    "MetaCheckStatus",
    "MetaRiskProfile",
    "MetacognitionConfig",
    "MetacognitionReport",
    "MetacognitiveController",
    "metacognitive_memory_items",
    "run_metacognitive_check",
    "select_safest_option",
    "update_state_metacognition",
]
