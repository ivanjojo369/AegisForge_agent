from __future__ import annotations

"""Uncertainty estimation and action gating for AegisForge NCP.

This module turns the agent's current cognitive state into an explicit
uncertainty assessment.  It is not a probabilistic model in the heavy ML sense;
it is a deterministic, auditable controller component that estimates whether the
agent should proceed, gather more evidence, ask for review, or block/redirect a
candidate action.

The estimator focuses on Sprint 4 needs:
- avoid unsupported claims and brittle benchmark shortcuts;
- detect evidence gaps, policy boundaries, adapter/tool mismatches, and
  adversarial context;
- preserve cost efficiency by asking for more evidence only when it is valuable;
- emit compact, serializable artifacts for tests, traces, and scorecards.
"""

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import json
import math
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .state import (
    CognitiveDecision,
    CognitiveState,
    DecisionOption,
    EvidenceRecord,
    TaskTheory,
    UncertaintyEstimate,
    WorkingMemoryItem,
)


class SupportsToDict(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


NCP_UNCERTAINTY_VERSION = "0.1.0"

VALID_UNCERTAINTY_LEVELS = ("low", "medium", "high", "critical")
VALID_RISK_LEVELS = ("low", "medium", "high", "critical")
VALID_RECOMMENDED_ACTIONS = (
    "proceed",
    "proceed_with_caution",
    "gather_more_evidence",
    "ask_clarifying_question",
    "pause_or_request_review",
    "block_or_safe_alternative",
)

DEFAULT_CONFIDENCE_PRIOR = 0.56
DEFAULT_EVIDENCE_MINIMUM = 1
DEFAULT_CRITICAL_RISK_THRESHOLD = 0.78
DEFAULT_HIGH_UNCERTAINTY_THRESHOLD = 0.64
DEFAULT_MEDIUM_UNCERTAINTY_THRESHOLD = 0.34

EVIDENCE_REQUIRED_DOMAINS = {
    "finance",
    "legal_domain",
    "healthcare",
    "research",
    "web",
    "agent_safety",
    "agent_security",
    "cybersecurity",
    "software_testing",
    "business_process",
}

HIGH_RISK_TAG_PREFIXES = (
    "risk:",
    "uncertainty:",
)

HIGH_RISK_TAGS = {
    "hardcoding_guard",
    "secret_boundary",
    "policy_boundary",
    "needs_evidence",
    "ambiguous_context",
    "prompt_injection",
    "context_injection",
    "unsafe_action",
    "unsupported_claim",
    "destructive_action",
    "secret_extraction",
    "platform_exploitation",
}

POLICY_RISK_WORDS = {
    "hardcode",
    "lookup table",
    "gold answer",
    "ground truth",
    "secret",
    "credential",
    "token",
    "exfiltrate",
    "delete",
    "destructive",
    "exploit",
    "jailbreak",
    "bypass",
    "unsafe",
    "leak",
    "private key",
    "password",
    "platform exploitation",
    "real-world action",
}

EVIDENCE_WORDS = {
    "evidence",
    "source",
    "citation",
    "document",
    "artifact",
    "observation",
    "tool result",
    "database",
    "trace",
    "log",
    "state",
    "grounded",
}

AMBIGUITY_WORDS = {
    "ambiguous",
    "unknown",
    "unclear",
    "missing",
    "incomplete",
    "conflict",
    "contradict",
    "stale",
    "distractor",
    "noisy",
    "hidden",
    "not enough",
    "insufficient",
}

ACTION_BLOCKLIST_WORDS = {
    "delete",
    "exfiltrate",
    "leak",
    "bypass",
    "exploit",
    "steal",
    "credential",
    "secret",
    "password",
    "private key",
    "unauthorized",
    "real payment",
    "external recipient",
}

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|private[_-]?key|session|cookie|auth)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|ghp_[a-z0-9_]{16,}|xox[baprs]-[a-z0-9-]{10,}|"
    r"bearer\s+[a-z0-9._-]{16,}|api[_-]?key\s*[:=]\s*[a-z0-9._-]{12,})"
)
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")


class UncertaintyCategory(str, Enum):
    EVIDENCE = "evidence"
    AMBIGUITY = "ambiguity"
    POLICY = "policy"
    SAFETY = "safety"
    ADAPTER = "adapter"
    TOOLING = "tooling"
    COST = "cost"
    MEMORY = "memory"
    ACTION = "action"
    ADVERSARIAL = "adversarial"
    TRACEABILITY = "traceability"


@dataclass(frozen=True, slots=True)
class UncertaintyConfig:
    """Runtime weights and thresholds for uncertainty estimation."""

    confidence_prior: float = DEFAULT_CONFIDENCE_PRIOR
    evidence_minimum: int = DEFAULT_EVIDENCE_MINIMUM
    require_evidence_for_sensitive_domains: bool = True
    critical_risk_threshold: float = DEFAULT_CRITICAL_RISK_THRESHOLD
    high_uncertainty_threshold: float = DEFAULT_HIGH_UNCERTAINTY_THRESHOLD
    medium_uncertainty_threshold: float = DEFAULT_MEDIUM_UNCERTAINTY_THRESHOLD
    max_factor_count: int = 18
    max_gap_count: int = 10
    policy_blocking: bool = True
    conservative_when_no_tools: bool = True
    reward_supported_evidence: float = 0.035
    penalty_unsupported_evidence: float = 0.065
    penalty_conflicting_evidence: float = 0.120
    penalty_missing_required_tool: float = 0.080
    penalty_policy_risk: float = 0.140
    penalty_action_risk: float = 0.150
    penalty_ambiguity: float = 0.045
    penalty_memory_pressure: float = 0.050
    penalty_trace_gap: float = 0.030
    redact_sensitive: bool = True

    def normalized(self) -> "UncertaintyConfig":
        return UncertaintyConfig(
            confidence_prior=_bounded_float(self.confidence_prior, default=DEFAULT_CONFIDENCE_PRIOR),
            evidence_minimum=max(0, int(self.evidence_minimum)),
            require_evidence_for_sensitive_domains=bool(self.require_evidence_for_sensitive_domains),
            critical_risk_threshold=min(max(float(self.critical_risk_threshold), 0.0), 1.0),
            high_uncertainty_threshold=min(max(float(self.high_uncertainty_threshold), 0.0), 1.0),
            medium_uncertainty_threshold=min(max(float(self.medium_uncertainty_threshold), 0.0), 1.0),
            max_factor_count=max(4, int(self.max_factor_count)),
            max_gap_count=max(1, int(self.max_gap_count)),
            policy_blocking=bool(self.policy_blocking),
            conservative_when_no_tools=bool(self.conservative_when_no_tools),
            reward_supported_evidence=max(0.0, float(self.reward_supported_evidence)),
            penalty_unsupported_evidence=max(0.0, float(self.penalty_unsupported_evidence)),
            penalty_conflicting_evidence=max(0.0, float(self.penalty_conflicting_evidence)),
            penalty_missing_required_tool=max(0.0, float(self.penalty_missing_required_tool)),
            penalty_policy_risk=max(0.0, float(self.penalty_policy_risk)),
            penalty_action_risk=max(0.0, float(self.penalty_action_risk)),
            penalty_ambiguity=max(0.0, float(self.penalty_ambiguity)),
            penalty_memory_pressure=max(0.0, float(self.penalty_memory_pressure)),
            penalty_trace_gap=max(0.0, float(self.penalty_trace_gap)),
            redact_sensitive=bool(self.redact_sensitive),
        )


@dataclass(frozen=True, slots=True)
class UncertaintyFactor:
    """One factor contributing to uncertainty or risk."""

    name: str
    category: str
    severity: float
    contribution: float
    description: str
    evidence_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        name: str,
        category: str | UncertaintyCategory,
        severity: float,
        contribution: float,
        description: str,
        evidence_refs: Iterable[str] = (),
        tags: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "UncertaintyFactor":
        cat = category.value if isinstance(category, UncertaintyCategory) else str(category)
        return cls(
            name=_normalize_identifier(name),
            category=_normalize_identifier(cat),
            severity=_bounded_float(severity, default=0.0),
            contribution=max(0.0, float(contribution or 0.0)),
            description=_clip(_sanitize(description), 640),
            evidence_refs=tuple(_unique(str(ref) for ref in evidence_refs)),
            tags=tuple(_unique(_normalize_tag(tag) for tag in tags)),
            metadata=_redact_mapping(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        tags = f" [{', '.join(self.tags[:4])}]" if self.tags else ""
        return f"{self.category}/{self.name}={self.severity:.2f}{tags}: {self.description}"


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    """A missing or weak-evidence condition found by the estimator."""

    gap_id: str
    gap_type: str
    severity: float
    description: str
    recommended_action: str
    related_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        gap_type: str,
        severity: float,
        description: str,
        recommended_action: str,
        related_refs: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "EvidenceGap":
        gap_id = _stable_id("gap", gap_type, description, related_refs)[:22]
        return cls(
            gap_id=gap_id,
            gap_type=_normalize_identifier(gap_type),
            severity=_bounded_float(severity, default=0.0),
            description=_clip(_sanitize(description), 720),
            recommended_action=_validate_choice(recommended_action, VALID_RECOMMENDED_ACTIONS, "gather_more_evidence"),
            related_refs=tuple(_unique(str(ref) for ref in related_refs)),
            metadata=_redact_mapping(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        return f"{self.gap_type}/{self.severity:.2f}: {self.description} -> {self.recommended_action}"


@dataclass(frozen=True, slots=True)
class ActionGate:
    """Decision gate for a candidate action."""

    allowed: bool
    recommended_action: str
    reason: str
    confidence: float
    risk: float
    required_followups: tuple[str, ...] = ()
    blocked_factors: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        status = "allowed" if self.allowed else "blocked"
        return f"{status}: {self.recommended_action} risk={self.risk:.2f} confidence={self.confidence:.2f} — {self.reason}"


@dataclass(frozen=True, slots=True)
class UncertaintyAssessment:
    """Full audit artifact emitted by the estimator."""

    assessment_id: str
    version: str
    confidence: float
    uncertainty_score: float
    uncertainty_level: str
    risk_score: float
    risk_level: str
    recommended_action: str
    factors: tuple[UncertaintyFactor, ...]
    evidence_gaps: tuple[EvidenceGap, ...]
    action_gate: ActionGate
    supported_evidence_count: int = 0
    unsupported_evidence_count: int = 0
    conflicting_evidence_count: int = 0
    missing_tool_count: int = 0
    ambiguity_count: int = 0
    policy_risk_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_estimate(self) -> UncertaintyEstimate:
        gap = self.evidence_gaps[0].gap_type if self.evidence_gaps else None
        drivers = tuple(
            _unique(
                [
                    *(factor.name for factor in self.factors[:8]),
                    *(gap.gap_type for gap in self.evidence_gaps[:4]),
                ]
            )
        ) or ("stable_state",)
        return UncertaintyEstimate(
            confidence=self.confidence,
            level=self.uncertainty_level,
            evidence_gap=gap,
            ambiguity_count=self.ambiguity_count,
            contradiction_count=self.conflicting_evidence_count,
            risk_count=self.policy_risk_count,
            recommended_action=self.recommended_action,
            drivers=drivers,
            metadata={
                "assessment_id": self.assessment_id,
                "risk_level": self.risk_level,
                "risk_score": round(self.risk_score, 6),
                "uncertainty_score": round(self.uncertainty_score, 6),
                "action_gate": self.action_gate.to_dict(),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "version": self.version,
            "confidence": round(float(self.confidence), 6),
            "uncertainty_score": round(float(self.uncertainty_score), 6),
            "uncertainty_level": self.uncertainty_level,
            "risk_score": round(float(self.risk_score), 6),
            "risk_level": self.risk_level,
            "recommended_action": self.recommended_action,
            "factors": [factor.to_dict() for factor in self.factors],
            "evidence_gaps": [gap.to_dict() for gap in self.evidence_gaps],
            "action_gate": self.action_gate.to_dict(),
            "supported_evidence_count": self.supported_evidence_count,
            "unsupported_evidence_count": self.unsupported_evidence_count,
            "conflicting_evidence_count": self.conflicting_evidence_count,
            "missing_tool_count": self.missing_tool_count,
            "ambiguity_count": self.ambiguity_count,
            "policy_risk_count": self.policy_risk_count,
            "metadata": _json_safe(dict(self.metadata)),
        }

    def compact_context(self) -> str:
        lines = [
            (
                f"NCP uncertainty: level={self.uncertainty_level} "
                f"confidence={self.confidence:.2f} risk={self.risk_level}/{self.risk_score:.2f} "
                f"action={self.recommended_action}"
            ),
            f"Action gate: {self.action_gate.compact()}",
        ]
        if self.evidence_gaps:
            lines.append("Evidence gaps:")
            for gap in self.evidence_gaps[:5]:
                lines.append(f"- {gap.compact()}")
        if self.factors:
            lines.append("Top factors:")
            for factor in self.factors[:6]:
                lines.append(f"- {factor.compact()}")
        return "\n".join(lines).strip()


class UncertaintyEstimator:
    """Deterministic uncertainty estimator for NCP controller integration."""

    def __init__(self, config: UncertaintyConfig | None = None) -> None:
        self.config = (config or UncertaintyConfig()).normalized()

    def assess_state(
        self,
        state: CognitiveState,
        *,
        candidate_action: str | DecisionOption | CognitiveDecision | None = None,
        external_signals: Mapping[str, Any] | None = None,
    ) -> UncertaintyAssessment:
        """Assess uncertainty from a full CognitiveState."""

        external = _as_mapping(external_signals)
        factors: list[UncertaintyFactor] = []
        gaps: list[EvidenceGap] = []

        evidence_counts = self._assess_evidence(state, factors, gaps)
        missing_tool_count = self._assess_tools(state, factors, gaps)
        ambiguity_count = self._assess_ambiguity(state, factors, gaps)
        policy_risk_count = self._assess_policy(state, factors, gaps)
        self._assess_adapter(state, factors, gaps)
        self._assess_memory(state, factors, gaps)
        self._assess_traceability(state, factors, gaps)
        self._assess_prior_decisions(state, factors, gaps)
        self._assess_external_signals(external, factors, gaps)

        action_gate = self.gate_action(
            state=state,
            candidate_action=candidate_action,
            factors=factors,
            gaps=gaps,
            evidence_counts=evidence_counts,
        )

        uncertainty_score, risk_score, confidence = self._aggregate_scores(
            state=state,
            factors=factors,
            gaps=gaps,
            action_gate=action_gate,
            evidence_counts=evidence_counts,
        )
        uncertainty_level = self._uncertainty_level(uncertainty_score)
        risk_level = self._risk_level(risk_score)
        recommended_action = self._recommended_action(
            uncertainty_level=uncertainty_level,
            risk_level=risk_level,
            action_gate=action_gate,
            gaps=gaps,
            evidence_counts=evidence_counts,
        )

        factors = sorted(factors, key=lambda factor: (-factor.contribution, -factor.severity, factor.category, factor.name))
        gaps = sorted(gaps, key=lambda gap: (-gap.severity, gap.gap_type, gap.gap_id))
        factors_tuple = tuple(factors[: self.config.max_factor_count])
        gaps_tuple = tuple(gaps[: self.config.max_gap_count])

        assessment_id = _stable_id(
            "unc",
            state.episode_id,
            confidence,
            uncertainty_level,
            risk_level,
            recommended_action,
            [factor.name for factor in factors_tuple],
            [gap.gap_id for gap in gaps_tuple],
        )[:24]

        return UncertaintyAssessment(
            assessment_id=assessment_id,
            version=NCP_UNCERTAINTY_VERSION,
            confidence=confidence,
            uncertainty_score=uncertainty_score,
            uncertainty_level=uncertainty_level,
            risk_score=risk_score,
            risk_level=risk_level,
            recommended_action=recommended_action,
            factors=factors_tuple,
            evidence_gaps=gaps_tuple,
            action_gate=action_gate,
            supported_evidence_count=evidence_counts["supported"],
            unsupported_evidence_count=evidence_counts["unsupported"],
            conflicting_evidence_count=evidence_counts["conflicting"],
            missing_tool_count=missing_tool_count,
            ambiguity_count=ambiguity_count,
            policy_risk_count=policy_risk_count,
            metadata={
                "episode_id": state.episode_id,
                "domain": state.task_theory.domain,
                "scenario_id": state.task_theory.scenario_id,
                "scenario_family": state.task_theory.scenario_family,
                "assessment_mode": state.task_theory.assessment_mode,
                "factor_count_total": len(factors),
                "gap_count_total": len(gaps),
            },
        )

    def update_state(
        self,
        state: CognitiveState,
        *,
        candidate_action: str | DecisionOption | CognitiveDecision | None = None,
        external_signals: Mapping[str, Any] | None = None,
        append_trace: bool = True,
    ) -> CognitiveState:
        """Assess uncertainty and return an updated CognitiveState."""

        assessment = self.assess_state(
            state,
            candidate_action=candidate_action,
            external_signals=external_signals,
        )
        updated = state.update_uncertainty(assessment.to_estimate())
        if append_trace:
            updated = updated.append_trace(
                phase="uncertainty",
                message=assessment.compact_context(),
                severity="warning" if assessment.uncertainty_level in {"high", "critical"} else "info",
                refs=[assessment.assessment_id, *[gap.gap_id for gap in assessment.evidence_gaps[:4]]],
                metadata={
                    "assessment": assessment.to_dict(),
                    "recommended_action": assessment.recommended_action,
                    "action_gate": assessment.action_gate.to_dict(),
                },
            ).refresh_scorecard()
        return updated

    def gate_action(
        self,
        *,
        state: CognitiveState,
        candidate_action: str | DecisionOption | CognitiveDecision | None = None,
        factors: Sequence[UncertaintyFactor] = (),
        gaps: Sequence[EvidenceGap] = (),
        evidence_counts: Mapping[str, int] | None = None,
    ) -> ActionGate:
        """Return an action gate for a candidate action under current uncertainty."""

        evidence_counts = dict(evidence_counts or {})
        action_text, action_confidence, action_risk, action_cost = _candidate_action_parts(candidate_action)
        required_followups: list[str] = []
        blocked_factors: list[str] = []

        factor_risk = min(1.0, sum(factor.contribution for factor in factors if factor.category in {"policy", "safety", "action", "adversarial"}))
        gap_risk = min(1.0, sum(gap.severity for gap in gaps) / max(1, len(gaps)))
        risk = _bounded_float(max(action_risk, factor_risk, gap_risk), default=0.0)

        lowered = action_text.lower()
        action_word_hits = sorted(word for word in ACTION_BLOCKLIST_WORDS if word in lowered)
        if action_word_hits:
            risk = max(risk, 0.82)
            blocked_factors.extend(f"action_word:{word}" for word in action_word_hits[:6])

        policy_terms = sorted(word for word in POLICY_RISK_WORDS if word in lowered)
        if policy_terms:
            risk = max(risk, 0.74)
            blocked_factors.extend(f"policy_term:{word}" for word in policy_terms[:6])

        if evidence_counts.get("conflicting", 0):
            required_followups.append("resolve_conflicting_evidence")
        if evidence_counts.get("unsupported", 0):
            required_followups.append("avoid_unsupported_claims")
        if any(gap.gap_type in {"missing_required_evidence", "no_evidence_records"} for gap in gaps):
            required_followups.append("gather_or_verify_evidence")
        if any(factor.category == "tooling" for factor in factors):
            required_followups.append("satisfy_required_tool_contract")
        if any(factor.category == "policy" and factor.severity >= 0.72 for factor in factors):
            required_followups.append("run_policy_self_check")

        blocking = self.config.policy_blocking and (
            risk >= self.config.critical_risk_threshold
            or any("secret" in item or "credential" in item for item in blocked_factors)
            or any("platform exploitation" in item or "lookup table" in item for item in blocked_factors)
        )

        if blocking:
            return ActionGate(
                allowed=False,
                recommended_action="block_or_safe_alternative",
                reason="Candidate action crosses policy/safety risk threshold.",
                confidence=_bounded_float(action_confidence - 0.20, default=0.35),
                risk=risk,
                required_followups=tuple(_unique(required_followups or ("offer_safe_alternative",))),
                blocked_factors=tuple(_unique(blocked_factors)),
                metadata={"action_cost": action_cost, "action_text_excerpt": _clip(action_text, 240)},
            )

        if required_followups and risk >= 0.45:
            return ActionGate(
                allowed=True,
                recommended_action="gather_more_evidence",
                reason="Action is not blocked, but follow-up checks are required before finalization.",
                confidence=_bounded_float(action_confidence - 0.08, default=0.45),
                risk=risk,
                required_followups=tuple(_unique(required_followups)),
                blocked_factors=tuple(_unique(blocked_factors)),
                metadata={"action_cost": action_cost, "action_text_excerpt": _clip(action_text, 240)},
            )

        if risk >= 0.30:
            return ActionGate(
                allowed=True,
                recommended_action="proceed_with_caution",
                reason="Action is allowed with elevated risk monitoring.",
                confidence=action_confidence,
                risk=risk,
                required_followups=tuple(_unique(required_followups)),
                blocked_factors=tuple(_unique(blocked_factors)),
                metadata={"action_cost": action_cost, "action_text_excerpt": _clip(action_text, 240)},
            )

        return ActionGate(
            allowed=True,
            recommended_action="proceed",
            reason="No blocking uncertainty or safety condition detected.",
            confidence=action_confidence,
            risk=risk,
            required_followups=tuple(_unique(required_followups)),
            blocked_factors=tuple(_unique(blocked_factors)),
            metadata={"action_cost": action_cost, "action_text_excerpt": _clip(action_text, 240)},
        )

    def _assess_evidence(
        self,
        state: CognitiveState,
        factors: list[UncertaintyFactor],
        gaps: list[EvidenceGap],
    ) -> dict[str, int]:
        counts = {
            "supported": 0,
            "unsupported": 0,
            "conflicting": 0,
            "unknown": 0,
            "not_applicable": 0,
        }
        for record in state.evidence:
            status = str(record.status or "unknown")
            counts[status] = counts.get(status, 0) + 1

        requires_evidence = bool(state.task_theory.evidence_requirements)
        if self.config.require_evidence_for_sensitive_domains and state.task_theory.domain in EVIDENCE_REQUIRED_DOMAINS:
            requires_evidence = True

        if requires_evidence and sum(counts.values()) == 0:
            factors.append(
                UncertaintyFactor.new(
                    name="no_evidence_records",
                    category=UncertaintyCategory.EVIDENCE,
                    severity=0.72,
                    contribution=0.18,
                    description="Task/domain requires grounded evidence but no evidence records are present.",
                    tags=("needs_evidence",),
                )
            )
            gaps.append(
                EvidenceGap.new(
                    gap_type="no_evidence_records",
                    severity=0.78,
                    description="No evidence records are available for a task that should be grounded.",
                    recommended_action="gather_more_evidence",
                )
            )

        if counts.get("supported", 0) < self.config.evidence_minimum and requires_evidence:
            factors.append(
                UncertaintyFactor.new(
                    name="missing_required_evidence",
                    category=UncertaintyCategory.EVIDENCE,
                    severity=0.58,
                    contribution=0.12,
                    description="Supported evidence count is below the configured minimum.",
                    tags=("needs_evidence",),
                    metadata={"supported": counts.get("supported", 0), "minimum": self.config.evidence_minimum},
                )
            )
            gaps.append(
                EvidenceGap.new(
                    gap_type="missing_required_evidence",
                    severity=0.60,
                    description="More supporting evidence is needed before a confident final action.",
                    recommended_action="gather_more_evidence",
                )
            )

        if counts.get("unsupported", 0):
            refs = [record.evidence_id for record in state.evidence if record.status == "unsupported"]
            factors.append(
                UncertaintyFactor.new(
                    name="unsupported_claims",
                    category=UncertaintyCategory.EVIDENCE,
                    severity=min(1.0, 0.45 + 0.08 * counts["unsupported"]),
                    contribution=self.config.penalty_unsupported_evidence * counts["unsupported"],
                    description="One or more claims are marked unsupported.",
                    evidence_refs=refs,
                    tags=("unsupported_claim", "needs_evidence"),
                )
            )
            gaps.append(
                EvidenceGap.new(
                    gap_type="unsupported_claims_present",
                    severity=min(1.0, 0.42 + 0.08 * counts["unsupported"]),
                    description="Unsupported claims should be removed, qualified, or verified.",
                    recommended_action="gather_more_evidence",
                    related_refs=refs,
                )
            )

        if counts.get("conflicting", 0):
            refs = [record.evidence_id for record in state.evidence if record.status == "conflicting"]
            factors.append(
                UncertaintyFactor.new(
                    name="conflicting_evidence",
                    category=UncertaintyCategory.EVIDENCE,
                    severity=min(1.0, 0.64 + 0.10 * counts["conflicting"]),
                    contribution=self.config.penalty_conflicting_evidence * counts["conflicting"],
                    description="Evidence contains explicit conflicts.",
                    evidence_refs=refs,
                    tags=("conflicting_evidence", "needs_evidence"),
                )
            )
            gaps.append(
                EvidenceGap.new(
                    gap_type="conflicting_evidence_present",
                    severity=min(1.0, 0.70 + 0.10 * counts["conflicting"]),
                    description="Conflicting evidence must be resolved or escalated before finalization.",
                    recommended_action="pause_or_request_review",
                    related_refs=refs,
                )
            )

        return counts

    def _assess_tools(
        self,
        state: CognitiveState,
        factors: list[UncertaintyFactor],
        gaps: list[EvidenceGap],
    ) -> int:
        required = set(_normalize_identifier(tool) for tool in state.task_theory.required_tools)
        available = set(_normalize_identifier(action) for action in state.task_theory.available_actions)
        adapter_actions = set(_normalize_identifier(action) for action in state.adapter_profile.available_actions)
        available |= adapter_actions

        if not required:
            return 0

        used_or_seen = set()
        for record in state.evidence:
            used_or_seen.add(_normalize_identifier(record.source))
            used_or_seen.add(_normalize_identifier(record.kind))
        for item in state.working_memory:
            used_or_seen.add(_normalize_identifier(item.source))
            for ref in item.evidence_refs:
                used_or_seen.add(_normalize_identifier(ref))

        missing = sorted(tool for tool in required if tool not in used_or_seen and tool not in available)
        if not missing:
            return 0

        severity = min(1.0, 0.38 + 0.10 * len(missing))
        factors.append(
            UncertaintyFactor.new(
                name="missing_required_tools",
                category=UncertaintyCategory.TOOLING,
                severity=severity,
                contribution=self.config.penalty_missing_required_tool * len(missing),
                description=f"Required tools/actions have not been observed or advertised: {', '.join(missing[:8])}.",
                tags=("tool_contract", "needs_evidence"),
                metadata={"missing": missing[:12]},
            )
        )
        gaps.append(
            EvidenceGap.new(
                gap_type="missing_required_tools",
                severity=severity,
                description="The task declares required tools/actions that are not yet satisfied.",
                recommended_action="gather_more_evidence" if self.config.conservative_when_no_tools else "proceed_with_caution",
                metadata={"missing": missing[:12]},
            )
        )
        return len(missing)

    def _assess_ambiguity(
        self,
        state: CognitiveState,
        factors: list[UncertaintyFactor],
        gaps: list[EvidenceGap],
    ) -> int:
        ambiguity_tags: list[str] = []
        ambiguity_text_hits: list[str] = []

        for item in state.working_memory:
            for tag in item.tags:
                tag_text = str(tag)
                if tag_text.startswith("uncertainty:") or tag_text in {"ambiguous_context", "needs_evidence"}:
                    ambiguity_tags.append(tag_text)
            lowered = item.content.lower()
            hits = [word for word in AMBIGUITY_WORDS if word in lowered]
            ambiguity_text_hits.extend(hits[:3])

        objective_hits = [word for word in AMBIGUITY_WORDS if word in state.task_theory.objective.lower()]
        ambiguity_text_hits.extend(objective_hits[:3])

        ambiguity_count = len(ambiguity_tags) + len(ambiguity_text_hits)
        if ambiguity_count:
            severity = min(1.0, 0.28 + 0.035 * ambiguity_count)
            factors.append(
                UncertaintyFactor.new(
                    name="ambiguous_context",
                    category=UncertaintyCategory.AMBIGUITY,
                    severity=severity,
                    contribution=self.config.penalty_ambiguity * ambiguity_count,
                    description="Working memory or objective contains ambiguity/missing/conflict signals.",
                    tags=tuple(_unique((*ambiguity_tags[:8], "ambiguous_context"))),
                    metadata={"text_hits": list(_unique(ambiguity_text_hits))[:10]},
                )
            )
            if severity >= 0.45:
                gaps.append(
                    EvidenceGap.new(
                        gap_type="ambiguous_context",
                        severity=severity,
                        description="Ambiguity should be resolved with additional observation or a conservative answer.",
                        recommended_action="ask_clarifying_question" if state.task_theory.domain == "unknown" else "gather_more_evidence",
                    )
                )
        return ambiguity_count

    def _assess_policy(
        self,
        state: CognitiveState,
        factors: list[UncertaintyFactor],
        gaps: list[EvidenceGap],
    ) -> int:
        policy_hits: list[str] = []
        for item in state.working_memory:
            lowered = item.content.lower()
            policy_hits.extend(word for word in POLICY_RISK_WORDS if word in lowered)
            for tag in item.tags:
                if tag in HIGH_RISK_TAGS or str(tag).startswith("risk:"):
                    policy_hits.append(str(tag))

        denied = list(state.policy_boundary.denied_behaviors)
        for denied_item in denied:
            denied_norm = denied_item.replace("_", " ").lower()
            if denied_norm in state.task_theory.objective.lower():
                policy_hits.append(denied_item)

        count = len(policy_hits)
        if count:
            severity = min(1.0, 0.46 + 0.045 * count)
            factors.append(
                UncertaintyFactor.new(
                    name="policy_boundary_risk",
                    category=UncertaintyCategory.POLICY,
                    severity=severity,
                    contribution=self.config.penalty_policy_risk * min(3, count),
                    description="Policy, fair-play, or safety-boundary risk is active in context.",
                    tags=tuple(_unique(("policy_boundary", *policy_hits[:10]))),
                    metadata={"hits": list(_unique(policy_hits))[:12]},
                )
            )
            if severity >= 0.70:
                gaps.append(
                    EvidenceGap.new(
                        gap_type="policy_boundary_review",
                        severity=severity,
                        description="Policy boundary risk is high enough to require self-check or safe alternative.",
                        recommended_action="pause_or_request_review",
                        metadata={"hits": list(_unique(policy_hits))[:12]},
                    )
                )
        return count

    def _assess_adapter(
        self,
        state: CognitiveState,
        factors: list[UncertaintyFactor],
        gaps: list[EvidenceGap],
    ) -> None:
        if state.task_theory.domain == "unknown" or state.adapter_profile.adapter == "unknown":
            factors.append(
                UncertaintyFactor.new(
                    name="adapter_or_domain_unknown",
                    category=UncertaintyCategory.ADAPTER,
                    severity=0.48,
                    contribution=0.07,
                    description="Domain or adapter profile is unknown, reducing routing confidence.",
                    tags=("adapter_contract",),
                )
            )
            gaps.append(
                EvidenceGap.new(
                    gap_type="adapter_profile_missing",
                    severity=0.42,
                    description="Resolve the domain/adapter profile before using specialized actions.",
                    recommended_action="gather_more_evidence",
                )
            )

        if state.adapter_profile.available_actions and state.task_theory.available_actions:
            adapter_actions = {_normalize_identifier(action) for action in state.adapter_profile.available_actions}
            task_actions = {_normalize_identifier(action) for action in state.task_theory.available_actions}
            if adapter_actions and task_actions and not (adapter_actions & task_actions):
                factors.append(
                    UncertaintyFactor.new(
                        name="adapter_action_mismatch",
                        category=UncertaintyCategory.ADAPTER,
                        severity=0.52,
                        contribution=0.08,
                        description="Adapter actions and task advertised actions do not overlap.",
                        tags=("adapter_contract", "tool_contract"),
                        metadata={"adapter_actions": sorted(adapter_actions)[:12], "task_actions": sorted(task_actions)[:12]},
                    )
                )

    def _assess_memory(
        self,
        state: CognitiveState,
        factors: list[UncertaintyFactor],
        gaps: list[EvidenceGap],
    ) -> None:
        memory_count = len(state.working_memory)
        locked_count = sum(1 for item in state.working_memory if item.locked)
        if memory_count > 32:
            severity = min(1.0, 0.30 + (memory_count - 32) / 80)
            factors.append(
                UncertaintyFactor.new(
                    name="memory_pressure",
                    category=UncertaintyCategory.MEMORY,
                    severity=severity,
                    contribution=self.config.penalty_memory_pressure * severity,
                    description="Working-memory item count is high; low-salience context may distract planning.",
                    tags=("context_budget", "memory_pressure"),
                    metadata={"memory_count": memory_count, "locked_count": locked_count},
                )
            )
            gaps.append(
                EvidenceGap.new(
                    gap_type="memory_compression_needed",
                    severity=severity,
                    description="Compress or prune low-salience working-memory items.",
                    recommended_action="proceed_with_caution",
                    metadata={"memory_count": memory_count},
                )
            )

    def _assess_traceability(
        self,
        state: CognitiveState,
        factors: list[UncertaintyFactor],
        gaps: list[EvidenceGap],
    ) -> None:
        phases = {event.phase for event in state.trace}
        required = {"state_init", "attention", "working_memory", "uncertainty", "decision"}
        missing = sorted(required - phases)
        if len(missing) >= 3:
            factors.append(
                UncertaintyFactor.new(
                    name="traceability_gap",
                    category=UncertaintyCategory.TRACEABILITY,
                    severity=0.36,
                    contribution=self.config.penalty_trace_gap * len(missing),
                    description="Several expected trace phases are missing.",
                    tags=("traceability",),
                    metadata={"missing_phases": missing},
                )
            )

    def _assess_prior_decisions(
        self,
        state: CognitiveState,
        factors: list[UncertaintyFactor],
        gaps: list[EvidenceGap],
    ) -> None:
        blocked = [decision for decision in state.decisions if decision.safety_status == "blocked"]
        unsafe = [decision for decision in state.decisions if decision.safety_status == "unsafe"]
        if blocked or unsafe:
            severity = min(1.0, 0.58 + 0.10 * len(blocked) + 0.16 * len(unsafe))
            factors.append(
                UncertaintyFactor.new(
                    name="unsafe_or_blocked_prior_decision",
                    category=UncertaintyCategory.SAFETY,
                    severity=severity,
                    contribution=0.12 * len(blocked) + 0.18 * len(unsafe),
                    description="Prior decision history contains blocked or unsafe actions.",
                    evidence_refs=[decision.decision_id for decision in (*blocked, *unsafe)],
                    tags=("policy_boundary", "unsafe_action"),
                )
            )
            gaps.append(
                EvidenceGap.new(
                    gap_type="prior_decision_safety_review",
                    severity=severity,
                    description="Review prior unsafe/blocked decision before continuing.",
                    recommended_action="pause_or_request_review",
                    related_refs=[decision.decision_id for decision in (*blocked, *unsafe)],
                )
            )

    def _assess_external_signals(
        self,
        external: Mapping[str, Any],
        factors: list[UncertaintyFactor],
        gaps: list[EvidenceGap],
    ) -> None:
        if not external:
            return
        for key, value in external.items():
            if value in (None, "", False):
                continue
            key_norm = _normalize_identifier(key)
            if key_norm in {"error", "exception", "timeout", "tool_error", "adapter_error"}:
                factors.append(
                    UncertaintyFactor.new(
                        name=f"external_{key_norm}",
                        category=UncertaintyCategory.TOOLING,
                        severity=0.62,
                        contribution=0.10,
                        description=f"External signal indicates {key_norm}: {_clip(value, 220)}",
                        tags=("tool_error", "needs_evidence"),
                    )
                )
                gaps.append(
                    EvidenceGap.new(
                        gap_type="external_tool_or_adapter_error",
                        severity=0.60,
                        description="External tool/adapter error should be handled before finalization.",
                        recommended_action="gather_more_evidence",
                    )
                )

    def _aggregate_scores(
        self,
        *,
        state: CognitiveState,
        factors: Sequence[UncertaintyFactor],
        gaps: Sequence[EvidenceGap],
        action_gate: ActionGate,
        evidence_counts: Mapping[str, int],
    ) -> tuple[float, float, float]:
        base_confidence = _bounded_float(state.task_theory.confidence, default=self.config.confidence_prior)
        if base_confidence == 0.0:
            base_confidence = self.config.confidence_prior

        uncertainty_penalty = min(0.72, sum(factor.contribution for factor in factors))
        gap_penalty = min(0.30, sum(gap.severity for gap in gaps) / max(8, len(gaps) * 4))
        evidence_reward = min(0.22, evidence_counts.get("supported", 0) * self.config.reward_supported_evidence)

        confidence = _bounded_float(base_confidence + evidence_reward - uncertainty_penalty - gap_penalty, default=base_confidence)
        risk_score = _bounded_float(
            max(
                action_gate.risk,
                sum(factor.contribution for factor in factors if factor.category in {"policy", "safety", "action", "adversarial"}),
                max((gap.severity for gap in gaps), default=0.0) * 0.85,
            ),
            default=0.0,
        )
        uncertainty_score = _bounded_float(
            (1.0 - confidence) * 0.72
            + min(0.28, uncertainty_penalty)
            + min(0.16, gap_penalty)
            + (0.10 if not action_gate.allowed else 0.0),
            default=0.5,
        )
        return uncertainty_score, risk_score, confidence

    def _uncertainty_level(self, uncertainty_score: float) -> str:
        if uncertainty_score >= self.config.critical_risk_threshold:
            return "critical"
        if uncertainty_score >= self.config.high_uncertainty_threshold:
            return "high"
        if uncertainty_score >= self.config.medium_uncertainty_threshold:
            return "medium"
        return "low"

    @staticmethod
    def _risk_level(risk_score: float) -> str:
        if risk_score >= 0.78:
            return "critical"
        if risk_score >= 0.58:
            return "high"
        if risk_score >= 0.30:
            return "medium"
        return "low"

    @staticmethod
    def _recommended_action(
        *,
        uncertainty_level: str,
        risk_level: str,
        action_gate: ActionGate,
        gaps: Sequence[EvidenceGap],
        evidence_counts: Mapping[str, int],
    ) -> str:
        if not action_gate.allowed:
            return "block_or_safe_alternative"
        if risk_level == "critical":
            return "block_or_safe_alternative"
        if risk_level == "high":
            return "pause_or_request_review"
        if any(gap.recommended_action == "pause_or_request_review" for gap in gaps):
            return "pause_or_request_review"
        if any(gap.recommended_action == "ask_clarifying_question" for gap in gaps):
            return "ask_clarifying_question"
        if evidence_counts.get("conflicting", 0):
            return "pause_or_request_review"
        if evidence_counts.get("unsupported", 0) or any(gap.recommended_action == "gather_more_evidence" for gap in gaps):
            return "gather_more_evidence"
        if uncertainty_level == "critical":
            return "pause_or_request_review"
        if uncertainty_level == "high":
            return "gather_more_evidence"
        if uncertainty_level == "medium":
            return "proceed_with_caution"
        return action_gate.recommended_action if action_gate.recommended_action in VALID_RECOMMENDED_ACTIONS else "proceed"


def estimate_uncertainty(
    state: CognitiveState,
    *,
    candidate_action: str | DecisionOption | CognitiveDecision | None = None,
    external_signals: Mapping[str, Any] | None = None,
    config: UncertaintyConfig | None = None,
) -> UncertaintyAssessment:
    """Convenience wrapper for tests and controller code."""

    return UncertaintyEstimator(config=config).assess_state(
        state,
        candidate_action=candidate_action,
        external_signals=external_signals,
    )


def update_state_uncertainty(
    state: CognitiveState,
    *,
    candidate_action: str | DecisionOption | CognitiveDecision | None = None,
    external_signals: Mapping[str, Any] | None = None,
    config: UncertaintyConfig | None = None,
    append_trace: bool = True,
) -> CognitiveState:
    """Return a state with refreshed uncertainty and trace metadata."""

    return UncertaintyEstimator(config=config).update_state(
        state,
        candidate_action=candidate_action,
        external_signals=external_signals,
        append_trace=append_trace,
    )


def gate_candidate_action(
    state: CognitiveState,
    candidate_action: str | DecisionOption | CognitiveDecision | None,
    *,
    config: UncertaintyConfig | None = None,
) -> ActionGate:
    """Run only the action gate for a candidate action."""

    estimator = UncertaintyEstimator(config=config)
    assessment = estimator.assess_state(state, candidate_action=candidate_action)
    return assessment.action_gate


def build_decision_options_from_assessment(
    assessment: UncertaintyAssessment,
    *,
    proceed_action: str = "proceed_with_current_plan",
    evidence_action: str = "gather_more_evidence",
    review_action: str = "request_manual_review",
    safe_action: str = "offer_safe_alternative",
) -> tuple[DecisionOption, ...]:
    """Build generic decision options from an uncertainty assessment.

    These are strategy options, not benchmark answers.
    """

    options = [
        DecisionOption(
            action=proceed_action,
            rationale="Proceed when confidence is sufficient and action gate allows it.",
            expected_utility=max(0.0, assessment.confidence - assessment.risk_score * 0.35),
            risk=assessment.risk_score,
            cost=0.12,
            confidence=assessment.confidence,
            evidence_refs=tuple(ref for gap in assessment.evidence_gaps for ref in gap.related_refs),
            metadata={"source": "uncertainty_assessment", "action_gate": assessment.action_gate.to_dict()},
        ),
        DecisionOption(
            action=evidence_action,
            rationale="Gather or verify evidence to reduce uncertainty before finalization.",
            expected_utility=0.62 if assessment.evidence_gaps else 0.34,
            risk=max(0.04, assessment.risk_score * 0.35),
            cost=0.28,
            confidence=max(0.45, assessment.confidence),
            evidence_refs=tuple(ref for gap in assessment.evidence_gaps for ref in gap.related_refs),
            metadata={"gap_count": len(assessment.evidence_gaps)},
        ),
        DecisionOption(
            action=review_action,
            rationale="Escalate when policy, safety, or conflicting evidence creates high uncertainty.",
            expected_utility=0.72 if assessment.risk_level in {"high", "critical"} else 0.32,
            risk=0.10,
            cost=0.35,
            confidence=0.70,
            metadata={"risk_level": assessment.risk_level},
        ),
        DecisionOption(
            action=safe_action,
            rationale="Use a safe alternative if the candidate path is blocked or unsafe.",
            expected_utility=0.76 if not assessment.action_gate.allowed else 0.38,
            risk=0.06,
            cost=0.18,
            confidence=0.74,
            metadata={"allowed": assessment.action_gate.allowed},
        ),
    ]
    return tuple(options)


def _candidate_action_parts(candidate: str | DecisionOption | CognitiveDecision | None) -> tuple[str, float, float, float]:
    if candidate is None:
        return "", 0.58, 0.0, 0.0
    if isinstance(candidate, str):
        return _sanitize(candidate), 0.58, 0.0, 0.0
    data = _as_mapping(candidate)
    if isinstance(candidate, DecisionOption):
        return (
            _sanitize(candidate.action),
            _bounded_float(candidate.confidence, default=0.55),
            _bounded_float(candidate.risk, default=0.0),
            max(0.0, float(candidate.cost or 0.0)),
        )
    if isinstance(candidate, CognitiveDecision):
        return (
            _sanitize(candidate.selected_action),
            _bounded_float(candidate.confidence, default=0.55),
            _bounded_float(candidate.risk, default=0.0),
            max(0.0, float(candidate.cost or 0.0)),
        )
    action = data.get("action") or data.get("selected_action") or data.get("name") or data.get("tool") or ""
    return (
        _sanitize(action),
        _bounded_float(data.get("confidence"), default=0.55),
        _bounded_float(data.get("risk"), default=0.0),
        max(0.0, float(data.get("cost") or 0.0)),
    )


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


def _bounded_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return min(1.0, max(0.0, number))


def _validate_choice(value: Any, choices: Sequence[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in set(choices) else default


def _clip(text: Any, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    values: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen or text == "unknown":
            continue
        seen.add(text)
        values.append(text)
    return tuple(values)


def _tokens(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(str(text or "")) if len(tok) > 1}


def _text_similarity(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


__all__ = [
    "NCP_UNCERTAINTY_VERSION",
    "VALID_RECOMMENDED_ACTIONS",
    "ActionGate",
    "EvidenceGap",
    "UncertaintyAssessment",
    "UncertaintyCategory",
    "UncertaintyConfig",
    "UncertaintyEstimator",
    "UncertaintyFactor",
    "build_decision_options_from_assessment",
    "estimate_uncertainty",
    "gate_candidate_action",
    "update_state_uncertainty",
]
