from __future__ import annotations

"""Evidence verification layer for AegisForge NCP.

This module converts observations, tool results, payload fragments, trace
messages, and candidate claims into structured evidence records.  It is a
deterministic verifier, not a judge model.  Its role is to make grounding
explicit enough for planning, uncertainty estimation, self-checking, scorecards,
and tests.

Design rules:
- store evidence about claims, not benchmark answers;
- never create task-specific lookup tables;
- preserve source references and status transitions;
- detect unsupported, conflicting, stale, policy-sensitive, and low-grounding
  conditions;
- keep outputs compact, redacted, and serializable.
"""

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import json
import math
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .state import CognitiveState, EvidenceRecord, TraceEvent, WorkingMemoryItem


class SupportsToDict(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


NCP_EVIDENCE_VERSION = "0.1.0"

VALID_CLAIM_TYPES = (
    "objective",
    "constraint",
    "tool_result",
    "observation",
    "policy",
    "numeric",
    "source_grounded",
    "decision_rationale",
    "adapter_contract",
    "unknown",
)

VALID_SOURCE_TYPES = (
    "payload",
    "tool_result",
    "observation",
    "document",
    "trace",
    "memory",
    "plan",
    "policy",
    "adapter",
    "external",
    "unknown",
)

VALID_EVIDENCE_STATUS = ("unknown", "supported", "unsupported", "conflicting", "not_applicable")

DEFAULT_MIN_SUPPORT_SCORE = 0.42
DEFAULT_CONFLICT_SCORE = 0.58
DEFAULT_MAX_TEXT_CHARS = 1_200
DEFAULT_MAX_CLAIMS = 48
DEFAULT_MAX_SOURCES = 80

GROUNDING_REQUIRED_DOMAINS = {
    "finance",
    "research",
    "healthcare",
    "legal_domain",
    "web",
    "business_process",
    "agent_safety",
    "agent_security",
    "cybersecurity",
    "software_testing",
    "defi",
}

POLICY_SENSITIVE_WORDS = {
    "secret",
    "credential",
    "token",
    "password",
    "private key",
    "jailbreak",
    "bypass",
    "exploit",
    "delete",
    "destructive",
    "exfiltrate",
    "leak",
    "hardcode",
    "lookup table",
    "gold answer",
    "ground truth",
    "unsafe",
}

NEGATION_WORDS = {
    "no",
    "not",
    "never",
    "none",
    "without",
    "avoid",
    "deny",
    "denied",
    "cannot",
    "can't",
    "must not",
    "do not",
    "don't",
    "forbidden",
    "prohibited",
}

SUPPORT_WORDS = {
    "verified",
    "confirmed",
    "observed",
    "found",
    "matches",
    "supported",
    "available",
    "present",
    "success",
    "passed",
    "allowed",
}

UNCERTAINTY_WORDS = {
    "unknown",
    "unclear",
    "missing",
    "incomplete",
    "ambiguous",
    "possibly",
    "maybe",
    "conflict",
    "contradict",
    "stale",
    "not enough",
    "unsupported",
}

EVIDENCE_WORDS = {
    "evidence",
    "source",
    "citation",
    "document",
    "artifact",
    "observation",
    "observed",
    "tool result",
    "database",
    "trace",
    "log",
    "state",
    "grounded",
    "verified",
    "confirmed",
    "supported",
    "reference",
    "record",
}

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|private[_-]?key|session|cookie|auth)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|ghp_[a-z0-9_]{16,}|xox[baprs]-[a-z0-9-]{10,}|"
    r"bearer\s+[a-z0-9._-]{16,}|api[_-]?key\s*[:=]\s*[a-z0-9._-]{12,})"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_.$%-]{2,}")
_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)*(?:\.\d+)?%?")
_URL_RE = re.compile(r"https?://\S+")


class EvidenceDecision(str, Enum):
    ACCEPT = "accept"
    VERIFY_MORE = "verify_more"
    REJECT_UNSUPPORTED = "reject_unsupported"
    ESCALATE_CONFLICT = "escalate_conflict"
    POLICY_REVIEW = "policy_review"


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    """Runtime knobs for deterministic evidence verification."""

    min_support_score: float = DEFAULT_MIN_SUPPORT_SCORE
    conflict_score: float = DEFAULT_CONFLICT_SCORE
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    max_claims: int = DEFAULT_MAX_CLAIMS
    max_sources: int = DEFAULT_MAX_SOURCES
    min_tokens_for_claim: int = 3
    require_grounding_for_sensitive_domains: bool = True
    redact_sensitive: bool = True
    preserve_source_excerpt: bool = True
    numeric_exact_match_bonus: float = 0.18
    citation_bonus: float = 0.08
    policy_sensitivity_penalty: float = 0.10
    uncertainty_penalty: float = 0.12
    contradiction_penalty: float = 0.25

    def normalized(self) -> "EvidenceConfig":
        return EvidenceConfig(
            min_support_score=min(max(float(self.min_support_score), 0.0), 1.0),
            conflict_score=min(max(float(self.conflict_score), 0.0), 1.0),
            max_text_chars=max(160, int(self.max_text_chars)),
            max_claims=max(1, int(self.max_claims)),
            max_sources=max(1, int(self.max_sources)),
            min_tokens_for_claim=max(1, int(self.min_tokens_for_claim)),
            require_grounding_for_sensitive_domains=bool(self.require_grounding_for_sensitive_domains),
            redact_sensitive=bool(self.redact_sensitive),
            preserve_source_excerpt=bool(self.preserve_source_excerpt),
            numeric_exact_match_bonus=max(0.0, float(self.numeric_exact_match_bonus)),
            citation_bonus=max(0.0, float(self.citation_bonus)),
            policy_sensitivity_penalty=max(0.0, float(self.policy_sensitivity_penalty)),
            uncertainty_penalty=max(0.0, float(self.uncertainty_penalty)),
            contradiction_penalty=max(0.0, float(self.contradiction_penalty)),
        )


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """A compact source of truth candidate."""

    source_id: str
    source_type: str
    title: str
    content: str
    confidence: float = 0.5
    timestamp: str | None = None
    refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        source_type: str,
        content: str,
        title: str = "",
        confidence: float = 0.5,
        timestamp: str | None = None,
        refs: Iterable[str] = (),
        tags: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "EvidenceSource":
        safe_type = _validate_choice(source_type, VALID_SOURCE_TYPES, "unknown")
        safe_content = _sanitize(content)
        source_id = _stable_id("src", safe_type, title, safe_content, refs)[:24]
        return cls(
            source_id=source_id,
            source_type=safe_type,
            title=_clip(_sanitize(title or safe_type), 240),
            content=_clip(safe_content, DEFAULT_MAX_TEXT_CHARS),
            confidence=_bounded_float(confidence, default=0.5),
            timestamp=timestamp,
            refs=tuple(_unique(str(ref) for ref in refs)),
            tags=tuple(_unique(_normalize_tag(tag) for tag in tags)),
            metadata=_redact_mapping(metadata or {}),
        )

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[str, Any],
        *,
        source_type: str = "external",
        title: str = "",
        confidence: float = 0.5,
    ) -> "EvidenceSource":
        data = _as_mapping(mapping)
        content = data.get("content") or data.get("text") or data.get("body") or data.get("message")
        if content is None:
            content = json.dumps(_json_safe(data), ensure_ascii=False, sort_keys=True)
        return cls.new(
            source_type=str(data.get("source_type") or source_type),
            title=str(data.get("title") or data.get("name") or title or source_type),
            content=str(content),
            confidence=_bounded_float(data.get("confidence"), default=confidence),
            timestamp=_safe_text(data.get("timestamp")),
            refs=_coerce_tuple(data.get("refs") or data.get("references") or data.get("evidence_refs")),
            tags=_coerce_tuple(data.get("tags")),
            metadata={
                key: value
                for key, value in data.items()
                if key not in {"content", "text", "body", "message", "source_type", "title", "name", "confidence", "timestamp", "refs", "references", "evidence_refs", "tags"}
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        ref_text = f" refs={','.join(self.refs[:3])}" if self.refs else ""
        return f"{self.source_type}:{self.title} conf={self.confidence:.2f}{ref_text} — {_clip(self.content, 180)}"


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    """A claim that needs grounding."""

    claim_id: str
    text: str
    claim_type: str = "unknown"
    importance: float = 0.5
    source_hint: str | None = None
    required: bool = False
    turn_index: int = 0
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        text: str,
        claim_type: str = "unknown",
        importance: float = 0.5,
        source_hint: str | None = None,
        required: bool = False,
        turn_index: int = 0,
        tags: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "EvidenceClaim":
        safe_text = _clip(_sanitize(text), DEFAULT_MAX_TEXT_CHARS)
        safe_type = _validate_choice(claim_type, VALID_CLAIM_TYPES, "unknown")
        claim_id = _stable_id("claim", safe_type, safe_text, source_hint)[:24]
        return cls(
            claim_id=claim_id,
            text=safe_text,
            claim_type=safe_type,
            importance=_bounded_float(importance, default=0.5),
            source_hint=_safe_text(source_hint),
            required=bool(required),
            turn_index=int(turn_index),
            tags=tuple(_unique(_normalize_tag(tag) for tag in tags)),
            metadata=_redact_mapping(metadata or {}),
        )

    @property
    def tokens(self) -> set[str]:
        return _tokens(self.text)

    @property
    def numbers(self) -> tuple[str, ...]:
        return tuple(_NUMBER_RE.findall(self.text))

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        required = " required" if self.required else ""
        return f"{self.claim_type}/{self.importance:.2f}{required}: {_clip(self.text, 220)}"


@dataclass(frozen=True, slots=True)
class SourceMatch:
    """A source-to-claim match with scoring detail."""

    source: EvidenceSource
    support_score: float
    conflict_score: float
    overlap_tokens: tuple[str, ...] = ()
    matched_numbers: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "support_score": round(float(self.support_score), 6),
            "conflict_score": round(float(self.conflict_score), 6),
            "overlap_tokens": list(self.overlap_tokens),
            "matched_numbers": list(self.matched_numbers),
            "reasons": list(self.reasons),
        }

    def compact(self) -> str:
        return (
            f"{self.source.source_id} support={self.support_score:.2f} "
            f"conflict={self.conflict_score:.2f} reasons={','.join(self.reasons[:4])}"
        )


@dataclass(frozen=True, slots=True)
class ClaimAssessment:
    """Verification result for a single claim."""

    claim: EvidenceClaim
    status: str
    confidence: float
    best_match: SourceMatch | None = None
    matches: tuple[SourceMatch, ...] = ()
    decision: str = EvidenceDecision.VERIFY_MORE.value
    explanation: str = ""
    evidence_record: EvidenceRecord | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_record(self) -> EvidenceRecord:
        if self.evidence_record is not None:
            return self.evidence_record
        support = None
        source = "evidence_verifier"
        kind = self.claim.claim_type
        refs: list[str] = []
        if self.best_match:
            source = self.best_match.source.source_id
            support = self.best_match.source.content
            refs = [self.best_match.source.source_id, *self.best_match.source.refs]
        return EvidenceRecord.new(
            source=source,
            kind=kind,
            claim=self.claim.text,
            status=self.status,
            confidence=self.confidence,
            support=support,
            verifier="ncp_evidence_verifier",
            turn_index=self.claim.turn_index,
            related_memory_keys=refs,
            metadata={
                "claim_id": self.claim.claim_id,
                "decision": self.decision,
                "explanation": self.explanation,
                "matches": [match.to_dict() for match in self.matches[:4]],
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim.to_dict(),
            "status": self.status,
            "confidence": round(float(self.confidence), 6),
            "best_match": self.best_match.to_dict() if self.best_match else None,
            "matches": [match.to_dict() for match in self.matches],
            "decision": self.decision,
            "explanation": self.explanation,
            "evidence_record": self.evidence_record.to_dict() if self.evidence_record else None,
            "metadata": _json_safe(dict(self.metadata)),
        }

    def compact(self) -> str:
        return f"{self.status}/{self.confidence:.2f} {self.claim.compact()} — {self.explanation}"


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """A verification bundle for one state or action."""

    bundle_id: str
    version: str
    claims: tuple[EvidenceClaim, ...]
    sources: tuple[EvidenceSource, ...]
    assessments: tuple[ClaimAssessment, ...]
    status_counts: Mapping[str, int]
    supported_ratio: float
    conflict_ratio: float
    unsupported_required_count: int
    grounding_decision: str
    summary: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(assessment.to_record() for assessment in self.assessments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "version": self.version,
            "claims": [claim.to_dict() for claim in self.claims],
            "sources": [source.to_dict() for source in self.sources],
            "assessments": [assessment.to_dict() for assessment in self.assessments],
            "status_counts": dict(self.status_counts),
            "supported_ratio": round(float(self.supported_ratio), 6),
            "conflict_ratio": round(float(self.conflict_ratio), 6),
            "unsupported_required_count": self.unsupported_required_count,
            "grounding_decision": self.grounding_decision,
            "summary": self.summary,
            "metadata": _json_safe(dict(self.metadata)),
        }

    def compact_context(self) -> str:
        lines = [
            (
                f"NCP evidence bundle: decision={self.grounding_decision} "
                f"supported={self.supported_ratio:.2f} conflict={self.conflict_ratio:.2f} "
                f"claims={len(self.claims)} sources={len(self.sources)}"
            ),
            f"Summary: {self.summary}",
        ]
        for assessment in self.assessments[:8]:
            lines.append(f"- {assessment.compact()}")
        if len(self.assessments) > 8:
            lines.append(f"- omitted_assessments={len(self.assessments) - 8}")
        return "\n".join(lines).strip()


class EvidenceVerifier:
    """Deterministic evidence extraction and verification component."""

    def __init__(self, config: EvidenceConfig | None = None) -> None:
        self.config = (config or EvidenceConfig()).normalized()

    def build_bundle(
        self,
        *,
        state: CognitiveState | None = None,
        claims: Sequence[EvidenceClaim | str | Mapping[str, Any]] = (),
        sources: Sequence[EvidenceSource | str | Mapping[str, Any]] = (),
        candidate_text: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvidenceBundle:
        """Build and verify a compact evidence bundle."""

        claim_list: list[EvidenceClaim] = []
        source_list: list[EvidenceSource] = []

        if state is not None:
            claim_list.extend(self.extract_claims_from_state(state))
            source_list.extend(self.extract_sources_from_state(state))

        claim_list.extend(_coerce_claims(claims, turn_index=state.turn_index if state else 0))
        source_list.extend(_coerce_sources(sources))

        if candidate_text:
            claim_list.extend(self.extract_claims_from_text(
                candidate_text,
                claim_type="decision_rationale",
                required=state is not None and self._requires_grounding(state),
                turn_index=state.turn_index if state else 0,
                source_hint="candidate_text",
            ))

        claim_list = _dedupe_claims(claim_list)[: self.config.max_claims]
        source_list = _dedupe_sources(source_list)[: self.config.max_sources]

        assessments = tuple(self.verify_claim(claim, source_list) for claim in claim_list)
        status_counts = _status_counts(assessments)
        supported_ratio = status_counts.get("supported", 0) / max(1, len(assessments))
        conflict_ratio = status_counts.get("conflicting", 0) / max(1, len(assessments))
        unsupported_required = sum(
            1 for assessment in assessments
            if assessment.claim.required and assessment.status in {"unsupported", "unknown", "conflicting"}
        )
        decision = self._grounding_decision(
            assessments=assessments,
            supported_ratio=supported_ratio,
            conflict_ratio=conflict_ratio,
            unsupported_required=unsupported_required,
        )
        summary = self._summarize(
            assessments=assessments,
            sources=source_list,
            decision=decision,
            supported_ratio=supported_ratio,
            conflict_ratio=conflict_ratio,
        )
        bundle_id = _stable_id(
            "evbundle",
            [claim.claim_id for claim in claim_list],
            [source.source_id for source in source_list],
            decision,
        )[:24]
        return EvidenceBundle(
            bundle_id=bundle_id,
            version=NCP_EVIDENCE_VERSION,
            claims=tuple(claim_list),
            sources=tuple(source_list),
            assessments=assessments,
            status_counts=status_counts,
            supported_ratio=supported_ratio,
            conflict_ratio=conflict_ratio,
            unsupported_required_count=unsupported_required,
            grounding_decision=decision,
            summary=summary,
            metadata=_redact_mapping(
                {
                    "state_episode_id": state.episode_id if state else None,
                    "domain": state.task_theory.domain if state else None,
                    **dict(metadata or {}),
                }
            ),
        )

    def verify_claim(self, claim: EvidenceClaim, sources: Sequence[EvidenceSource]) -> ClaimAssessment:
        """Verify a single claim against candidate sources."""

        if not claim.text.strip():
            return ClaimAssessment(
                claim=claim,
                status="not_applicable",
                confidence=0.0,
                decision=EvidenceDecision.REJECT_UNSUPPORTED.value,
                explanation="Empty claim.",
            )

        if _claim_is_policy_boundary(claim):
            return self._policy_claim_assessment(claim, sources)

        matches = tuple(
            sorted(
                (self.match_source(claim, source) for source in sources),
                key=lambda match: (-max(match.support_score, match.conflict_score), -match.source.confidence, match.source.source_id),
            )
        )
        best = matches[0] if matches else None

        if best is None:
            status = "unknown" if not claim.required else "unsupported"
            decision = EvidenceDecision.VERIFY_MORE.value if claim.required else EvidenceDecision.REJECT_UNSUPPORTED.value
            return ClaimAssessment(
                claim=claim,
                status=status,
                confidence=0.20 if claim.required else 0.10,
                best_match=None,
                matches=(),
                decision=decision,
                explanation="No candidate evidence source available.",
            )

        support_score = best.support_score
        conflict_score = best.conflict_score
        policy_sensitive = _contains_any(claim.text.lower(), POLICY_SENSITIVE_WORDS)

        if conflict_score >= self.config.conflict_score and conflict_score > support_score + 0.08:
            status = "conflicting"
            confidence = _bounded_float(conflict_score, default=0.65)
            decision = EvidenceDecision.ESCALATE_CONFLICT.value
            explanation = "Best source appears to conflict with the claim."
        elif support_score >= self.config.min_support_score:
            status = "supported"
            confidence = _bounded_float((support_score + best.source.confidence) / 2, default=support_score)
            decision = EvidenceDecision.POLICY_REVIEW.value if policy_sensitive else EvidenceDecision.ACCEPT.value
            explanation = "Claim is supported by the best available source."
        elif claim.required:
            status = "unsupported"
            confidence = _bounded_float(max(0.20, 1.0 - support_score), default=0.45)
            decision = EvidenceDecision.VERIFY_MORE.value
            explanation = "Required claim lacks enough support."
        else:
            status = "unknown"
            confidence = _bounded_float(max(0.10, support_score), default=0.20)
            decision = EvidenceDecision.VERIFY_MORE.value
            explanation = "Claim support is weak or indirect."

        return ClaimAssessment(
            claim=claim,
            status=status,
            confidence=confidence,
            best_match=best,
            matches=matches[:6],
            decision=decision,
            explanation=explanation,
            metadata={"policy_sensitive": policy_sensitive},
        )

    def match_source(self, claim: EvidenceClaim, source: EvidenceSource) -> SourceMatch:
        """Score lexical/numeric/negation support between a claim and source."""

        claim_tokens = _tokens(claim.text)
        source_tokens = _tokens(source.content)
        overlap = tuple(sorted(claim_tokens & source_tokens))
        token_score = len(overlap) / max(1, len(claim_tokens))

        claim_numbers = set(claim.numbers)
        source_numbers = set(_NUMBER_RE.findall(source.content))
        matched_numbers = tuple(sorted(claim_numbers & source_numbers))
        missing_numbers = tuple(sorted(claim_numbers - source_numbers))
        numeric_score = 0.0
        if claim_numbers:
            numeric_score = len(matched_numbers) / max(1, len(claim_numbers))

        source_lower = source.content.lower()
        claim_lower = claim.text.lower()
        support_hits = [word for word in SUPPORT_WORDS if word in source_lower]
        uncertainty_hits = [word for word in UNCERTAINTY_WORDS if word in source_lower]
        source_negated = _has_negation(source_lower)
        claim_negated = _has_negation(claim_lower)
        negation_conflict = source_negated != claim_negated and token_score >= 0.25

        citation_score = self.config.citation_bonus if source.refs or _URL_RE.search(source.content) else 0.0
        support_score = 0.58 * token_score + 0.20 * source.confidence + citation_score
        if claim_numbers:
            support_score += self.config.numeric_exact_match_bonus * numeric_score
            if missing_numbers:
                support_score -= min(0.18, 0.06 * len(missing_numbers))
        if support_hits:
            support_score += min(0.10, 0.025 * len(support_hits))
        if uncertainty_hits:
            support_score -= min(0.18, self.config.uncertainty_penalty)
        if _contains_any(claim_lower, POLICY_SENSITIVE_WORDS):
            support_score -= self.config.policy_sensitivity_penalty
        if negation_conflict:
            support_score -= self.config.contradiction_penalty

        conflict_score = 0.0
        if negation_conflict:
            conflict_score += 0.46
        if claim_numbers and missing_numbers and token_score >= 0.30:
            conflict_score += min(0.35, 0.08 * len(missing_numbers))
        if "conflict" in source_lower or "contradict" in source_lower or "unsupported" in source_lower:
            conflict_score += 0.20
        if _contains_any(source_lower, {"denied", "not allowed", "forbidden", "must not"}) and token_score >= 0.20:
            conflict_score += 0.16

        reasons = []
        if overlap:
            reasons.append(f"token_overlap={len(overlap)}")
        if matched_numbers:
            reasons.append(f"numeric_match={len(matched_numbers)}")
        if missing_numbers:
            reasons.append(f"numeric_missing={len(missing_numbers)}")
        if source.refs:
            reasons.append("has_refs")
        if support_hits:
            reasons.append("support_terms")
        if uncertainty_hits:
            reasons.append("uncertainty_terms")
        if negation_conflict:
            reasons.append("negation_conflict")

        return SourceMatch(
            source=source,
            support_score=_bounded_float(support_score, default=0.0),
            conflict_score=_bounded_float(conflict_score, default=0.0),
            overlap_tokens=overlap[:24],
            matched_numbers=matched_numbers[:12],
            reasons=tuple(reasons),
        )

    def extract_claims_from_state(self, state: CognitiveState) -> tuple[EvidenceClaim, ...]:
        """Extract verification claims from current CognitiveState."""

        claims: list[EvidenceClaim] = []
        requires_grounding = self._requires_grounding(state)

        claims.append(
            EvidenceClaim.new(
                text=state.task_theory.objective,
                claim_type="objective",
                importance=0.78,
                required=True,
                turn_index=state.turn_index,
                source_hint="task_theory.objective",
                tags=("objective", "task_grounding"),
            )
        )

        for constraint in state.task_theory.constraints[:12]:
            claims.append(
                EvidenceClaim.new(
                    text=constraint,
                    claim_type="constraint",
                    importance=0.72,
                    required=True,
                    turn_index=state.turn_index,
                    source_hint="task_theory.constraints",
                    tags=("constraint", "must_preserve"),
                )
            )

        for criterion in state.task_theory.success_criteria[:12]:
            claims.append(
                EvidenceClaim.new(
                    text=criterion,
                    claim_type="objective",
                    importance=0.70,
                    required=requires_grounding,
                    turn_index=state.turn_index,
                    source_hint="task_theory.success_criteria",
                    tags=("success_criteria",),
                )
            )

        for requirement in state.task_theory.evidence_requirements[:10]:
            claims.append(
                EvidenceClaim.new(
                    text=requirement,
                    claim_type="source_grounded",
                    importance=0.76,
                    required=requires_grounding,
                    turn_index=state.turn_index,
                    source_hint="task_theory.evidence_requirements",
                    tags=("evidence_requirement", "needs_evidence"),
                )
            )

        for tool in state.task_theory.required_tools[:12]:
            claims.append(
                EvidenceClaim.new(
                    text=f"Required tool/action is available or should be used: {tool}",
                    claim_type="adapter_contract",
                    importance=0.64,
                    required=True,
                    turn_index=state.turn_index,
                    source_hint="task_theory.required_tools",
                    tags=("tool_contract",),
                )
            )

        for item in state.working_memory:
            if item.locked or item.source in {"policy", "metadata", "plan", "task"} or any(tag in item.tags for tag in ("needs_evidence", "policy_boundary")):
                claims.append(
                    EvidenceClaim.new(
                        text=item.content,
                        claim_type=_claim_type_from_memory(item),
                        importance=max(0.42, item.salience),
                        required=item.locked or "needs_evidence" in item.tags,
                        turn_index=item.turn_index,
                        source_hint=f"working_memory.{item.source}",
                        tags=item.tags,
                        metadata={"memory_key": item.key},
                    )
                )

        for decision in state.decisions[-6:]:
            claims.append(
                EvidenceClaim.new(
                    text=decision.rationale,
                    claim_type="decision_rationale",
                    importance=max(0.50, decision.confidence),
                    required=requires_grounding or decision.safety_status in {"blocked", "unsafe", "needs_review"},
                    turn_index=decision.turn_index,
                    source_hint=f"decision.{decision.decision_id}",
                    tags=("decision_rationale", decision.safety_status),
                    metadata={"decision_id": decision.decision_id, "selected_action": decision.selected_action},
                )
            )

        return tuple(_dedupe_claims(claims))[: self.config.max_claims]

    def extract_sources_from_state(self, state: CognitiveState) -> tuple[EvidenceSource, ...]:
        """Extract evidence sources from CognitiveState without storing answers."""

        sources: list[EvidenceSource] = []

        sources.append(
            EvidenceSource.new(
                source_type="payload",
                title="task_theory",
                content=state.task_theory.compact(),
                confidence=max(0.50, state.task_theory.confidence),
                refs=(state.episode_id,),
                tags=("task_theory", state.task_theory.domain, state.task_theory.task_type),
                metadata={
                    "scenario_id": state.task_theory.scenario_id,
                    "scenario_family": state.task_theory.scenario_family,
                    "assessment_mode": state.task_theory.assessment_mode,
                },
            )
        )

        sources.append(
            EvidenceSource.new(
                source_type="policy",
                title="policy_boundary",
                content=json.dumps(_json_safe(state.policy_boundary.to_dict()), ensure_ascii=False, sort_keys=True),
                confidence=0.86,
                refs=(state.episode_id,),
                tags=("policy_boundary", "fair_play"),
            )
        )

        if state.adapter_profile.available_actions or state.adapter_profile.required_tools:
            sources.append(
                EvidenceSource.new(
                    source_type="adapter",
                    title="adapter_profile",
                    content=json.dumps(_json_safe(state.adapter_profile.to_dict()), ensure_ascii=False, sort_keys=True),
                    confidence=0.72,
                    refs=(state.episode_id,),
                    tags=("adapter_contract", state.adapter_profile.domain),
                )
            )

        for record in state.evidence:
            source_content = record.support or record.claim
            sources.append(
                EvidenceSource.new(
                    source_type="tool_result" if record.source not in {"evidence_verifier", "ncp_evidence_verifier"} else "observation",
                    title=f"{record.kind}:{record.status}",
                    content=source_content,
                    confidence=record.confidence,
                    refs=(record.evidence_id, *record.related_memory_keys),
                    tags=(record.status, record.kind),
                    metadata={"verifier": record.verifier, "source": record.source},
                )
            )

        for item in state.working_memory:
            source_type = "memory"
            if item.source in {"policy", "metadata", "plan"}:
                source_type = item.source
            sources.append(
                EvidenceSource.new(
                    source_type=source_type,
                    title=f"wm:{item.source}:{item.key}",
                    content=item.content,
                    confidence=item.salience,
                    refs=(item.key, *item.evidence_refs),
                    tags=item.tags,
                    metadata={"locked": item.locked, "turn_index": item.turn_index},
                )
            )

        for event in state.trace[-40:]:
            sources.append(
                EvidenceSource.new(
                    source_type="trace",
                    title=f"trace:{event.phase}",
                    content=event.message,
                    confidence=0.48 if event.severity in {"debug", "info"} else 0.60,
                    refs=(event.event_id, *event.refs),
                    tags=(event.phase, event.severity),
                    metadata={"turn_index": event.turn_index},
                )
            )

        return tuple(_dedupe_sources(sources))[: self.config.max_sources]

    def extract_claims_from_text(
        self,
        text: str,
        *,
        claim_type: str = "unknown",
        required: bool = False,
        turn_index: int = 0,
        source_hint: str | None = None,
        tags: Iterable[str] = (),
    ) -> tuple[EvidenceClaim, ...]:
        """Extract compact claims from free text using deterministic heuristics."""

        safe_text = _clip(_sanitize(text), self.config.max_text_chars * 3)
        fragments = [frag.strip() for frag in _SENTENCE_SPLIT_RE.split(safe_text) if frag.strip()]
        if not fragments and safe_text:
            fragments = [safe_text]

        claims: list[EvidenceClaim] = []
        for fragment in fragments:
            tokens = _tokens(fragment)
            if len(tokens) < self.config.min_tokens_for_claim:
                continue
            importance = 0.48
            inferred_type = claim_type
            lowered = fragment.lower()
            if _NUMBER_RE.search(fragment):
                inferred_type = "numeric" if claim_type == "unknown" else claim_type
                importance += 0.10
            if _contains_any(lowered, POLICY_SENSITIVE_WORDS):
                inferred_type = "policy"
                importance += 0.16
            if _contains_any(lowered, EVIDENCE_WORDS):
                inferred_type = "source_grounded"
                importance += 0.10
            if any(word in lowered for word in ("must", "required", "should", "do not", "avoid", "only")):
                importance += 0.12

            claims.append(
                EvidenceClaim.new(
                    text=fragment,
                    claim_type=inferred_type,
                    importance=importance,
                    required=required or importance >= 0.68,
                    turn_index=turn_index,
                    source_hint=source_hint,
                    tags=tags,
                )
            )

        return tuple(_dedupe_claims(claims))[: self.config.max_claims]

    def ingest_tool_result(
        self,
        *,
        tool_name: str,
        result: Any,
        status: str = "unknown",
        confidence: float = 0.62,
        turn_index: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[EvidenceRecord, EvidenceSource]:
        """Convert a tool result into an EvidenceRecord and EvidenceSource."""

        result_text = _format_value(result)
        source = EvidenceSource.new(
            source_type="tool_result",
            title=f"tool:{tool_name}",
            content=result_text,
            confidence=confidence,
            refs=(str(tool_name),),
            tags=("tool_result", _normalize_identifier(tool_name), status),
            metadata=metadata,
        )
        record = EvidenceRecord.new(
            source=tool_name,
            kind="tool_result",
            claim=f"Tool result observed for {tool_name}.",
            status=_validate_choice(status, VALID_EVIDENCE_STATUS, "unknown"),
            confidence=confidence,
            support=source.content,
            verifier="ncp_evidence_verifier",
            turn_index=turn_index,
            related_memory_keys=(source.source_id,),
            metadata={"tool_name": tool_name, **dict(metadata or {})},
        )
        return record, source

    def update_state_with_bundle(
        self,
        state: CognitiveState,
        bundle: EvidenceBundle,
        *,
        append_trace: bool = True,
    ) -> CognitiveState:
        """Attach bundle evidence records to CognitiveState."""

        next_state = state
        for record in bundle.records():
            next_state = next_state.observe_evidence(record)

        if append_trace:
            severity = "warning" if bundle.grounding_decision in {"verify_more", "escalate_conflict", "policy_review"} else "info"
            next_state = next_state.append_trace(
                phase="evidence",
                message=bundle.compact_context(),
                severity=severity,
                refs=[bundle.bundle_id, *[record.evidence_id for record in bundle.records()[:6]]],
                metadata={"bundle": bundle.to_dict()},
            ).refresh_scorecard()
        return next_state.reestimate_uncertainty().refresh_scorecard()

    def _policy_claim_assessment(
        self,
        claim: EvidenceClaim,
        sources: Sequence[EvidenceSource],
    ) -> ClaimAssessment:
        policy_sources = [source for source in sources if source.source_type == "policy" or "policy_boundary" in source.tags]
        if not policy_sources:
            return ClaimAssessment(
                claim=claim,
                status="unknown",
                confidence=0.36,
                decision=EvidenceDecision.POLICY_REVIEW.value,
                explanation="Policy-sensitive claim requires policy source review, but no policy source was present.",
            )
        matches = tuple(sorted(
            (self.match_source(claim, source) for source in policy_sources),
            key=lambda match: (-match.support_score, -match.conflict_score),
        ))
        best = matches[0]
        status = "supported" if best.support_score >= 0.25 else "unknown"
        return ClaimAssessment(
            claim=claim,
            status=status,
            confidence=max(0.42, best.support_score),
            best_match=best,
            matches=matches[:4],
            decision=EvidenceDecision.POLICY_REVIEW.value,
            explanation="Policy-sensitive claim was routed to policy review.",
            metadata={"policy_sensitive": True},
        )

    def _requires_grounding(self, state: CognitiveState) -> bool:
        if state.task_theory.evidence_requirements:
            return True
        if self.config.require_grounding_for_sensitive_domains and state.task_theory.domain in GROUNDING_REQUIRED_DOMAINS:
            return True
        if state.task_theory.required_tools:
            return True
        return False

    @staticmethod
    def _grounding_decision(
        *,
        assessments: Sequence[ClaimAssessment],
        supported_ratio: float,
        conflict_ratio: float,
        unsupported_required: int,
    ) -> str:
        if not assessments:
            return EvidenceDecision.VERIFY_MORE.value
        if conflict_ratio > 0:
            return EvidenceDecision.ESCALATE_CONFLICT.value
        if unsupported_required:
            return EvidenceDecision.VERIFY_MORE.value
        if any(assessment.decision == EvidenceDecision.POLICY_REVIEW.value for assessment in assessments):
            return EvidenceDecision.POLICY_REVIEW.value
        if supported_ratio >= 0.60:
            return EvidenceDecision.ACCEPT.value
        return EvidenceDecision.VERIFY_MORE.value

    @staticmethod
    def _summarize(
        *,
        assessments: Sequence[ClaimAssessment],
        sources: Sequence[EvidenceSource],
        decision: str,
        supported_ratio: float,
        conflict_ratio: float,
    ) -> str:
        counts = _status_counts(assessments)
        parts = [
            f"decision={decision}",
            f"claims={len(assessments)}",
            f"sources={len(sources)}",
            f"supported_ratio={supported_ratio:.2f}",
            f"conflict_ratio={conflict_ratio:.2f}",
        ]
        if counts:
            parts.append("statuses=" + ",".join(f"{k}:{v}" for k, v in sorted(counts.items())))
        return "; ".join(parts) + "."


def build_evidence_bundle(
    *,
    state: CognitiveState | None = None,
    claims: Sequence[EvidenceClaim | str | Mapping[str, Any]] = (),
    sources: Sequence[EvidenceSource | str | Mapping[str, Any]] = (),
    candidate_text: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    config: EvidenceConfig | None = None,
) -> EvidenceBundle:
    """Convenience wrapper for verifier bundle creation."""

    return EvidenceVerifier(config=config).build_bundle(
        state=state,
        claims=claims,
        sources=sources,
        candidate_text=candidate_text,
        metadata=metadata,
    )


def verify_claims(
    *,
    claims: Sequence[EvidenceClaim | str | Mapping[str, Any]],
    sources: Sequence[EvidenceSource | str | Mapping[str, Any]],
    config: EvidenceConfig | None = None,
) -> tuple[ClaimAssessment, ...]:
    """Verify supplied claims against supplied sources."""

    verifier = EvidenceVerifier(config=config)
    bundle = verifier.build_bundle(claims=claims, sources=sources)
    return bundle.assessments


def update_state_evidence(
    state: CognitiveState,
    *,
    claims: Sequence[EvidenceClaim | str | Mapping[str, Any]] = (),
    sources: Sequence[EvidenceSource | str | Mapping[str, Any]] = (),
    candidate_text: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    config: EvidenceConfig | None = None,
    append_trace: bool = True,
) -> CognitiveState:
    """Build an evidence bundle and attach it to a CognitiveState."""

    verifier = EvidenceVerifier(config=config)
    bundle = verifier.build_bundle(
        state=state,
        claims=claims,
        sources=sources,
        candidate_text=candidate_text,
        metadata=metadata,
    )
    return verifier.update_state_with_bundle(state, bundle, append_trace=append_trace)


def evidence_records_from_tool_result(
    *,
    tool_name: str,
    result: Any,
    status: str = "unknown",
    confidence: float = 0.62,
    turn_index: int = 0,
    metadata: Mapping[str, Any] | None = None,
    config: EvidenceConfig | None = None,
) -> tuple[EvidenceRecord, EvidenceSource]:
    """Convert a tool result into evidence objects."""

    return EvidenceVerifier(config=config).ingest_tool_result(
        tool_name=tool_name,
        result=result,
        status=status,
        confidence=confidence,
        turn_index=turn_index,
        metadata=metadata,
    )


def _claim_type_from_memory(item: WorkingMemoryItem) -> str:
    if item.source == "policy" or "policy_boundary" in item.tags:
        return "policy"
    if item.source == "plan":
        return "decision_rationale"
    if item.source == "metadata":
        return "adapter_contract"
    if "needs_evidence" in item.tags:
        return "source_grounded"
    if _NUMBER_RE.search(item.content):
        return "numeric"
    return "observation"


def _claim_is_policy_boundary(claim: EvidenceClaim) -> bool:
    lowered = claim.text.lower()
    return claim.claim_type == "policy" or _contains_any(lowered, POLICY_SENSITIVE_WORDS) or "policy_boundary" in claim.tags


def _dedupe_claims(claims: Sequence[EvidenceClaim]) -> list[EvidenceClaim]:
    kept: list[EvidenceClaim] = []
    for claim in claims:
        if not claim.text.strip():
            continue
        duplicate_index = None
        for idx, current in enumerate(kept):
            if claim.claim_type == current.claim_type and _text_similarity(claim.text, current.text) >= 0.88:
                duplicate_index = idx
                break
        if duplicate_index is None:
            kept.append(claim)
        else:
            current = kept[duplicate_index]
            kept[duplicate_index] = replace(
                current,
                importance=max(current.importance, claim.importance),
                required=current.required or claim.required,
                tags=tuple(_unique((*current.tags, *claim.tags))),
                metadata=_redact_mapping({**dict(current.metadata), **dict(claim.metadata)}),
            )
    return sorted(kept, key=lambda item: (-item.required, -item.importance, item.claim_type, item.claim_id))


def _dedupe_sources(sources: Sequence[EvidenceSource]) -> list[EvidenceSource]:
    kept: list[EvidenceSource] = []
    for source in sources:
        if not source.content.strip():
            continue
        duplicate_index = None
        for idx, current in enumerate(kept):
            if source.source_type == current.source_type and _text_similarity(source.content, current.content) >= 0.92:
                duplicate_index = idx
                break
        if duplicate_index is None:
            kept.append(source)
        else:
            current = kept[duplicate_index]
            kept[duplicate_index] = replace(
                current,
                confidence=max(current.confidence, source.confidence),
                refs=tuple(_unique((*current.refs, *source.refs))),
                tags=tuple(_unique((*current.tags, *source.tags))),
                metadata=_redact_mapping({**dict(current.metadata), **dict(source.metadata)}),
            )
    return sorted(kept, key=lambda item: (-item.confidence, item.source_type, item.source_id))


def _status_counts(assessments: Sequence[ClaimAssessment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for assessment in assessments:
        counts[assessment.status] = counts.get(assessment.status, 0) + 1
    return counts


def _coerce_claims(items: Sequence[EvidenceClaim | str | Mapping[str, Any]], *, turn_index: int = 0) -> list[EvidenceClaim]:
    claims: list[EvidenceClaim] = []
    for item in items:
        if isinstance(item, EvidenceClaim):
            claims.append(item)
        elif isinstance(item, str):
            claims.append(EvidenceClaim.new(text=item, turn_index=turn_index))
        elif isinstance(item, Mapping):
            data = _as_mapping(item)
            text = data.get("text") or data.get("claim") or data.get("content") or ""
            claims.append(
                EvidenceClaim.new(
                    text=str(text),
                    claim_type=str(data.get("claim_type") or data.get("type") or "unknown"),
                    importance=_bounded_float(data.get("importance"), default=0.5),
                    source_hint=_safe_text(data.get("source_hint")),
                    required=bool(data.get("required", False)),
                    turn_index=int(data.get("turn_index") or turn_index),
                    tags=_coerce_tuple(data.get("tags")),
                    metadata={key: value for key, value in data.items() if key not in {"text", "claim", "content", "claim_type", "type", "importance", "source_hint", "required", "turn_index", "tags"}},
                )
            )
    return claims


def _coerce_sources(items: Sequence[EvidenceSource | str | Mapping[str, Any]]) -> list[EvidenceSource]:
    sources: list[EvidenceSource] = []
    for item in items:
        if isinstance(item, EvidenceSource):
            sources.append(item)
        elif isinstance(item, str):
            sources.append(EvidenceSource.new(source_type="external", content=item, title="external_text"))
        elif isinstance(item, Mapping):
            sources.append(EvidenceSource.from_mapping(item))
    return sources


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


def _format_value(value: Any) -> str:
    safe = _json_safe(value)
    try:
        return _sanitize(json.dumps(safe, ensure_ascii=False, sort_keys=True))
    except TypeError:
        return _sanitize(repr(safe))


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


def _safe_text(value: Any) -> str | None:
    if value in (None, "") or isinstance(value, Mapping):
        return None
    text = _sanitize(value)
    return text or None


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


def _coerce_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(_unique(str(item) for item in value))
    return tuple(_unique([str(value)]))


def _tokens(text: str) -> set[str]:
    return {
        token.lower().strip(".,;:!?()[]{}\"'")
        for token in _TOKEN_RE.findall(str(text or ""))
        if len(token.strip(".,;:!?()[]{}\"'")) > 1
    }


def _text_similarity(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _contains_any(text: str, words: Iterable[str]) -> bool:
    lowered = str(text or "").lower()
    return any(word in lowered for word in words)


def _has_negation(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(word in lowered for word in NEGATION_WORDS)


__all__ = [
    "NCP_EVIDENCE_VERSION",
    "EvidenceBundle",
    "EvidenceClaim",
    "EvidenceConfig",
    "EvidenceDecision",
    "EvidenceSource",
    "EvidenceVerifier",
    "ClaimAssessment",
    "SourceMatch",
    "build_evidence_bundle",
    "evidence_records_from_tool_result",
    "update_state_evidence",
    "verify_claims",
]

