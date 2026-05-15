from __future__ import annotations

"""Episodic-memory layer for AegisForge NCP.

Episodic memory stores reusable lessons from prior runs without storing task
answers or benchmark-specific lookup tables.  The store is designed to remember
patterns such as:
- which strategy class worked for a domain family;
- which uncertainty/evidence gaps caused failure;
- which policy boundaries were relevant;
- which adapter/tool profile was useful;
- which follow-up checks improved safety and score.

It is intentionally deterministic and dependency-free.  Persistence is optional
and uses JSONL so the module remains easy to inspect in tests, CI, and local
AgentX-AgentBeats workflows.
"""

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .state import (
    CognitiveDecision,
    CognitiveScorecard,
    CognitiveState,
    EvidenceRecord,
    TaskTheory,
    TraceEvent,
    UncertaintyEstimate,
)


class SupportsToDict(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


NCP_EPISODIC_MEMORY_VERSION = "0.1.0"

DEFAULT_MAX_EPISODES = 800
DEFAULT_MAX_LESSONS = 12
DEFAULT_RECENCY_HALF_LIFE_DAYS = 21.0
DEFAULT_MIN_RETRIEVAL_SCORE = 0.16
DEFAULT_MAX_TEXT_CHARS = 900
DEFAULT_JSONL_NAME = "ncp_episodic_memory.jsonl"

VALID_OUTCOMES = ("success", "partial", "failed", "blocked", "unknown")
VALID_LESSON_TYPES = (
    "strategy",
    "risk",
    "evidence",
    "uncertainty",
    "tooling",
    "policy",
    "adapter",
    "cost",
    "traceability",
)
VALID_CONFIDENCE = ("low", "medium", "high")

SENSITIVE_TAGS = {
    "answer",
    "final_answer",
    "gold",
    "ground_truth",
    "solution",
    "lookup_table",
    "label",
    "score_target",
}

GENERALITY_SAFE_FIELDS = {
    "episode_id",
    "domain",
    "track",
    "scenario_family",
    "assessment_mode",
    "benchmark",
    "scenario_id",
    "scenario_name",
    "category",
    "adapter",
    "selected_opponent",
    "task_type",
    "outcome",
    "success",
    "safety_status",
    "uncertainty_level",
    "evidence_statuses",
    "strategy_tags",
    "failure_modes",
    "lessons",
    "scorecard",
    "created_at",
    "updated_at",
    "version",
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
_NUMBER_HEAVY_RE = re.compile(r"(?:\d+[.,]?){4,}")


@dataclass(frozen=True, slots=True)
class EpisodicMemoryConfig:
    """Runtime knobs for the local episodic-memory store."""

    max_episodes: int = DEFAULT_MAX_EPISODES
    max_lessons_per_query: int = DEFAULT_MAX_LESSONS
    min_retrieval_score: float = DEFAULT_MIN_RETRIEVAL_SCORE
    recency_half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    persist_path: str | None = None
    redact_sensitive: bool = True
    store_raw_trace: bool = False
    store_exact_outputs: bool = False
    prefer_same_domain: bool = True
    prefer_same_scenario_family: bool = True
    allow_same_scenario_retrieval: bool = False
    dedupe_similarity_threshold: float = 0.94

    def normalized(self) -> "EpisodicMemoryConfig":
        return EpisodicMemoryConfig(
            max_episodes=max(10, int(self.max_episodes)),
            max_lessons_per_query=max(1, int(self.max_lessons_per_query)),
            min_retrieval_score=min(max(float(self.min_retrieval_score), 0.0), 1.0),
            recency_half_life_days=max(1.0, float(self.recency_half_life_days)),
            max_text_chars=max(160, int(self.max_text_chars)),
            persist_path=self.persist_path,
            redact_sensitive=bool(self.redact_sensitive),
            store_raw_trace=bool(self.store_raw_trace),
            store_exact_outputs=bool(self.store_exact_outputs),
            prefer_same_domain=bool(self.prefer_same_domain),
            prefer_same_scenario_family=bool(self.prefer_same_scenario_family),
            allow_same_scenario_retrieval=bool(self.allow_same_scenario_retrieval),
            dedupe_similarity_threshold=min(max(float(self.dedupe_similarity_threshold), 0.60), 1.0),
        )


@dataclass(frozen=True, slots=True)
class EpisodeSignature:
    """Generalized signature used to match current tasks to past episodes."""

    domain: str = "unknown"
    track: str = "unknown"
    task_type: str = "general"
    scenario_family: str = "agentbeats_sprint4"
    assessment_mode: str = "purple_benchmark"
    benchmark: str = "AgentX-AgentBeats Phase 2 Sprint 4"
    scenario_id: str | None = None
    scenario_name: str | None = None
    adapter: str | None = None
    selected_opponent: str | None = None
    required_tools: tuple[str, ...] = ()
    constraint_tags: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()
    evidence_tags: tuple[str, ...] = ()
    uncertainty_tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_task_theory(
        cls,
        task_theory: TaskTheory,
        *,
        risk_tags: Iterable[str] = (),
        evidence_tags: Iterable[str] = (),
        uncertainty_tags: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "EpisodeSignature":
        return cls(
            domain=_normalize_identifier(task_theory.domain),
            track=_normalize_identifier(task_theory.track),
            task_type=_normalize_identifier(task_theory.task_type),
            scenario_family=str(task_theory.scenario_family or "agentbeats_sprint4"),
            assessment_mode=str(task_theory.assessment_mode or "purple_benchmark"),
            benchmark=str(task_theory.benchmark or "AgentX-AgentBeats Phase 2 Sprint 4"),
            scenario_id=_safe_text(task_theory.scenario_id),
            scenario_name=_safe_text(task_theory.scenario_name),
            adapter=_safe_text(task_theory.adapter),
            selected_opponent=_safe_text(task_theory.selected_opponent),
            required_tools=tuple(_unique(_normalize_identifier(tool) for tool in task_theory.required_tools)),
            constraint_tags=tuple(_unique(_semantic_tag(item) for item in task_theory.constraints)),
            risk_tags=tuple(_unique(_normalize_tag(tag) for tag in risk_tags)),
            evidence_tags=tuple(_unique(_normalize_tag(tag) for tag in evidence_tags)),
            uncertainty_tags=tuple(_unique(_normalize_tag(tag) for tag in uncertainty_tags)),
            metadata=_redact_mapping(metadata or {}),
        )

    @classmethod
    def from_state(cls, state: CognitiveState) -> "EpisodeSignature":
        risk_tags = []
        uncertainty_tags = []
        for item in state.working_memory:
            for tag in item.tags:
                if str(tag).startswith("risk:"):
                    risk_tags.append(str(tag))
                if str(tag).startswith("uncertainty:"):
                    uncertainty_tags.append(str(tag))
        evidence_tags = [record.status for record in state.evidence]
        return cls.from_task_theory(
            state.task_theory,
            risk_tags=risk_tags,
            evidence_tags=evidence_tags,
            uncertainty_tags=uncertainty_tags,
            metadata={
                "adapter_category": state.adapter_profile.category,
                "tool_mode": state.adapter_profile.tool_mode,
                "prompt_profile": state.adapter_profile.prompt_profile,
            },
        )

    def stable_key(self, *, include_scenario: bool = False) -> str:
        payload = {
            "domain": self.domain,
            "track": self.track,
            "task_type": self.task_type,
            "scenario_family": self.scenario_family,
            "assessment_mode": self.assessment_mode,
            "adapter": self.adapter,
            "selected_opponent": self.selected_opponent,
            "required_tools": sorted(self.required_tools),
            "constraint_tags": sorted(self.constraint_tags),
            "risk_tags": sorted(self.risk_tags),
            "evidence_tags": sorted(self.evidence_tags),
            "uncertainty_tags": sorted(self.uncertainty_tags),
        }
        if include_scenario:
            payload["scenario_id"] = self.scenario_id
            payload["scenario_name"] = self.scenario_name
        return sha256(json.dumps(_json_safe(payload), sort_keys=True).encode("utf-8")).hexdigest()[:20]

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        scenario = self.scenario_name or self.scenario_id or "heldout-compatible"
        tools = ",".join(self.required_tools[:5]) if self.required_tools else "none"
        return (
            f"domain={self.domain}; track={self.track}; task_type={self.task_type}; "
            f"scenario={scenario}; tools={tools}; family={self.scenario_family}"
        )


@dataclass(frozen=True, slots=True)
class EpisodeOutcome:
    """General outcome information from one completed or partial episode."""

    outcome: str = "unknown"
    success: bool = False
    reward: float | None = None
    score: float | None = None
    safety_status: str = "unknown"
    blocked_by_policy: bool = False
    completed: bool = False
    termination_reason: str | None = None
    cost_estimate: float | None = None
    latency_ms: float | None = None
    token_estimate: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state(
        cls,
        state: CognitiveState,
        *,
        outcome: str | None = None,
        success: bool | None = None,
        reward: float | None = None,
        score: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "EpisodeOutcome":
        last_decision = state.decisions[-1] if state.decisions else None
        safety_status = last_decision.safety_status if last_decision else "unknown"
        aggregate = state.scorecard.aggregate() if hasattr(state.scorecard, "aggregate") else None
        inferred_success = bool(success) if success is not None else bool(aggregate is not None and aggregate >= 0.66)
        inferred_outcome = outcome or ("success" if inferred_success else "partial" if aggregate and aggregate >= 0.45 else "unknown")
        return cls(
            outcome=_validate_choice(inferred_outcome, VALID_OUTCOMES, "unknown"),
            success=inferred_success,
            reward=reward,
            score=score if score is not None else aggregate,
            safety_status=safety_status,
            blocked_by_policy=bool(last_decision and last_decision.safety_status == "blocked"),
            completed=state.status in {"completed", "final", "decision_recorded"} or bool(state.decisions),
            termination_reason=_safe_text(_as_mapping(metadata).get("termination_reason")),
            cost_estimate=_optional_float(_as_mapping(metadata).get("cost_estimate")),
            latency_ms=_optional_float(_as_mapping(metadata).get("latency_ms")),
            token_estimate=_optional_int(_as_mapping(metadata).get("token_estimate")),
            metadata=_redact_mapping(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class EpisodicLesson:
    """A reusable, generalized lesson extracted from an episode."""

    lesson_id: str
    lesson_type: str
    statement: str
    confidence: float = 0.5
    support_count: int = 1
    contraindication_count: int = 0
    domains: tuple[str, ...] = ()
    task_types: tuple[str, ...] = ()
    scenario_families: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        lesson_type: str,
        statement: str,
        confidence: float = 0.5,
        domains: Iterable[str] = (),
        task_types: Iterable[str] = (),
        scenario_families: Iterable[str] = (),
        tags: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "EpisodicLesson":
        safe_type = _validate_choice(lesson_type, VALID_LESSON_TYPES, "strategy")
        safe_statement = _clip(_sanitize(statement), DEFAULT_MAX_TEXT_CHARS)
        lesson_id = _stable_id("lesson", safe_type, safe_statement, sorted(domains), sorted(tags))[:24]
        now = _utc_now()
        return cls(
            lesson_id=lesson_id,
            lesson_type=safe_type,
            statement=safe_statement,
            confidence=_bounded_float(confidence, default=0.5),
            support_count=1,
            contraindication_count=0,
            domains=tuple(_unique(_normalize_identifier(item) for item in domains)),
            task_types=tuple(_unique(_normalize_identifier(item) for item in task_types)),
            scenario_families=tuple(_unique(str(item) for item in scenario_families)),
            tags=tuple(_unique(_normalize_tag(item) for item in tags)),
            evidence_refs=tuple(_unique(str(ref) for ref in evidence_refs)),
            created_at=now,
            updated_at=now,
            metadata=_redact_mapping(metadata or {}),
        )

    def reinforce(self, *, confidence_delta: float = 0.05, evidence_refs: Iterable[str] = ()) -> "EpisodicLesson":
        return replace(
            self,
            confidence=_bounded_float(self.confidence + confidence_delta, default=self.confidence),
            support_count=self.support_count + 1,
            evidence_refs=tuple(_unique((*self.evidence_refs, *[str(ref) for ref in evidence_refs]))),
            updated_at=_utc_now(),
        )

    def contradict(self, *, confidence_delta: float = 0.08, evidence_refs: Iterable[str] = ()) -> "EpisodicLesson":
        return replace(
            self,
            confidence=_bounded_float(self.confidence - confidence_delta, default=self.confidence),
            contraindication_count=self.contraindication_count + 1,
            evidence_refs=tuple(_unique((*self.evidence_refs, *[str(ref) for ref in evidence_refs]))),
            updated_at=_utc_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        tag_text = f" [{', '.join(self.tags[:5])}]" if self.tags else ""
        return f"{self.lesson_type}/{self.confidence:.2f}{tag_text}: {self.statement}"


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    """One sanitized episodic record.

    This record intentionally stores generalized task context and lessons, not
    final answers.  Exact task outputs remain outside this module unless the
    caller explicitly opts in, and even then sensitive-looking fields are redacted.
    """

    episode_id: str
    signature: EpisodeSignature
    outcome: EpisodeOutcome
    lessons: tuple[EpisodicLesson, ...]
    scorecard: Mapping[str, Any] = field(default_factory=dict)
    uncertainty: Mapping[str, Any] = field(default_factory=dict)
    evidence_summary: Mapping[str, Any] = field(default_factory=dict)
    decision_summary: Mapping[str, Any] = field(default_factory=dict)
    trace_summary: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    version: str = NCP_EPISODIC_MEMORY_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state(
        cls,
        state: CognitiveState,
        *,
        outcome: EpisodeOutcome | None = None,
        lessons: Sequence[EpisodicLesson] | None = None,
        extra_metadata: Mapping[str, Any] | None = None,
        store_raw_trace: bool = False,
    ) -> "EpisodeRecord":
        signature = EpisodeSignature.from_state(state)
        episode_outcome = outcome or EpisodeOutcome.from_state(state)
        extracted_lessons = tuple(lessons) if lessons is not None else extract_lessons_from_state(state, episode_outcome)
        now = _utc_now()
        return cls(
            episode_id=state.episode_id,
            signature=signature,
            outcome=episode_outcome,
            lessons=extracted_lessons,
            scorecard=_scorecard_summary(state.scorecard),
            uncertainty=_uncertainty_summary(state.uncertainty),
            evidence_summary=_evidence_summary(state.evidence),
            decision_summary=_decision_summary(state.decisions),
            trace_summary=_trace_summary(state.trace, include_raw=store_raw_trace),
            created_at=now,
            updated_at=now,
            metadata=_redact_mapping(
                {
                    "attention_digest": state.attention_digest,
                    "state_status": state.status,
                    "turn_index": state.turn_index,
                    **dict(extra_metadata or {}),
                }
            ),
        ).sanitized()

    def sanitized(self) -> "EpisodeRecord":
        data = self.to_dict()
        data = _drop_unsafe_episode_fields(data)
        lessons = tuple(EpisodicLesson.from_dict(item) for item in data.get("lessons", []))
        return replace(
            self,
            lessons=lessons,
            metadata=_redact_mapping(data.get("metadata", {})),
            scorecard=_redact_mapping(data.get("scorecard", {})),
            uncertainty=_redact_mapping(data.get("uncertainty", {})),
            evidence_summary=_redact_mapping(data.get("evidence_summary", {})),
            decision_summary=_redact_mapping(data.get("decision_summary", {})),
            trace_summary=_redact_mapping(data.get("trace_summary", {})),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EpisodeRecord":
        signature = EpisodeSignature(**_filter_dataclass_kwargs(EpisodeSignature, _as_mapping(data.get("signature"))))
        outcome = EpisodeOutcome(**_filter_dataclass_kwargs(EpisodeOutcome, _as_mapping(data.get("outcome"))))
        lessons = tuple(EpisodicLesson.from_dict(item) for item in data.get("lessons", []) if isinstance(item, Mapping))
        return cls(
            episode_id=str(data.get("episode_id") or _stable_id("episode", signature.stable_key())[:24]),
            signature=signature,
            outcome=outcome,
            lessons=lessons,
            scorecard=_redact_mapping(_as_mapping(data.get("scorecard"))),
            uncertainty=_redact_mapping(_as_mapping(data.get("uncertainty"))),
            evidence_summary=_redact_mapping(_as_mapping(data.get("evidence_summary"))),
            decision_summary=_redact_mapping(_as_mapping(data.get("decision_summary"))),
            trace_summary=_redact_mapping(_as_mapping(data.get("trace_summary"))),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
            version=str(data.get("version") or NCP_EPISODIC_MEMORY_VERSION),
            metadata=_redact_mapping(_as_mapping(data.get("metadata"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        return (
            f"{self.episode_id} {self.outcome.outcome} score={self.outcome.score} "
            f"{self.signature.compact()} lessons={len(self.lessons)}"
        )


# Patch-friendly constructor attached after class definition to keep class concise.
def _lesson_from_dict(data: Mapping[str, Any]) -> EpisodicLesson:
    kwargs = _filter_dataclass_kwargs(EpisodicLesson, _as_mapping(data))
    return EpisodicLesson(**kwargs)


setattr(EpisodicLesson, "from_dict", staticmethod(_lesson_from_dict))


@dataclass(frozen=True, slots=True)
class EpisodicRetrievalQuery:
    """Query used to retrieve lessons from episodic memory."""

    signature: EpisodeSignature
    text: str = ""
    desired_lesson_types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    max_lessons: int | None = None
    min_score: float | None = None
    exclude_episode_ids: tuple[str, ...] = ()
    include_failed: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state(
        cls,
        state: CognitiveState,
        *,
        text: str = "",
        desired_lesson_types: Iterable[str] = (),
        tags: Iterable[str] = (),
        max_lessons: int | None = None,
        min_score: float | None = None,
    ) -> "EpisodicRetrievalQuery":
        return cls(
            signature=EpisodeSignature.from_state(state),
            text=_sanitize(text or state.task_theory.objective),
            desired_lesson_types=tuple(_unique(_validate_choice(item, VALID_LESSON_TYPES, "strategy") for item in desired_lesson_types)),
            tags=tuple(_unique(_normalize_tag(item) for item in tags)),
            max_lessons=max_lessons,
            min_score=min_score,
            exclude_episode_ids=(state.episode_id,),
        )


@dataclass(frozen=True, slots=True)
class RetrievedLesson:
    """A lesson returned by an episodic-memory query."""

    lesson: EpisodicLesson
    episode_id: str
    score: float
    reasons: tuple[str, ...] = ()
    source_signature: EpisodeSignature | None = None
    outcome: EpisodeOutcome | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lesson": self.lesson.to_dict(),
            "episode_id": self.episode_id,
            "score": round(float(self.score), 6),
            "reasons": list(self.reasons),
            "source_signature": self.source_signature.to_dict() if self.source_signature else None,
            "outcome": self.outcome.to_dict() if self.outcome else None,
        }

    def compact(self) -> str:
        return f"[{self.score:.2f}] {self.lesson.compact()} (episode={self.episode_id})"


@dataclass(frozen=True, slots=True)
class EpisodicRetrievalResult:
    """Result bundle from episodic retrieval."""

    query_digest: str
    lessons: tuple[RetrievedLesson, ...]
    scanned_episodes: int
    omitted_count: int
    summary: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_digest": self.query_digest,
            "lessons": [lesson.to_dict() for lesson in self.lessons],
            "scanned_episodes": self.scanned_episodes,
            "omitted_count": self.omitted_count,
            "summary": self.summary,
            "metadata": _json_safe(dict(self.metadata)),
        }

    def compact_context(self, *, title: str = "NCP Episodic Lessons") -> str:
        lines = [
            f"{title}:",
            f"- summary: {self.summary}",
        ]
        if not self.lessons:
            lines.append("- no reusable episodic lessons retrieved")
            return "\n".join(lines)
        for retrieved in self.lessons:
            lines.append(f"- {retrieved.compact()}")
        if self.omitted_count:
            lines.append(f"- omitted_lower_score_lessons: {self.omitted_count}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class EpisodicMemoryStats:
    """Inspectable episodic-memory statistics."""

    version: str
    episode_count: int
    lesson_count: int
    domain_counts: Mapping[str, int]
    outcome_counts: Mapping[str, int]
    lesson_type_counts: Mapping[str, int]
    max_episodes: int
    persist_path: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


class EpisodicMemoryStore:
    """Mutable generalized episode store for the NCP controller."""

    def __init__(
        self,
        *,
        config: EpisodicMemoryConfig | None = None,
        episodes: Iterable[EpisodeRecord] = (),
        autoload: bool = False,
    ) -> None:
        self.config = (config or EpisodicMemoryConfig()).normalized()
        self._episodes: dict[str, EpisodeRecord] = {}
        self._lesson_index: dict[str, EpisodicLesson] = {}
        if autoload and self.config.persist_path:
            self.load()
        for episode in episodes:
            self.add_episode(episode, persist=False)
        self.enforce_capacity()

    def __len__(self) -> int:
        return len(self._episodes)

    def __iter__(self):
        return iter(self.episodes())

    def episodes(self) -> tuple[EpisodeRecord, ...]:
        return tuple(sorted(self._episodes.values(), key=lambda rec: rec.updated_at or rec.created_at, reverse=True))

    def lessons(self) -> tuple[EpisodicLesson, ...]:
        return tuple(sorted(self._lesson_index.values(), key=lambda lesson: (lesson.confidence, lesson.support_count), reverse=True))

    def add_episode(self, episode: EpisodeRecord, *, persist: bool = True) -> EpisodeRecord:
        clean = self._sanitize_episode(episode)
        existing = self._episodes.get(clean.episode_id)
        if existing is not None:
            clean = self._merge_episode(existing, clean)
        self._episodes[clean.episode_id] = clean
        self._update_lesson_index(clean)
        self.enforce_capacity()
        if persist:
            self.save()
        return clean

    def record_state(
        self,
        state: CognitiveState,
        *,
        outcome: EpisodeOutcome | None = None,
        extra_lessons: Sequence[EpisodicLesson] = (),
        extra_metadata: Mapping[str, Any] | None = None,
        persist: bool = True,
    ) -> EpisodeRecord:
        base_lessons = list(extract_lessons_from_state(state, outcome or EpisodeOutcome.from_state(state)))
        base_lessons.extend(extra_lessons)
        record = EpisodeRecord.from_state(
            state,
            outcome=outcome,
            lessons=base_lessons,
            extra_metadata=extra_metadata,
            store_raw_trace=self.config.store_raw_trace,
        )
        return self.add_episode(record, persist=persist)

    def retrieve(self, query: EpisodicRetrievalQuery) -> EpisodicRetrievalResult:
        max_lessons = query.max_lessons or self.config.max_lessons_per_query
        min_score = self.config.min_retrieval_score if query.min_score is None else query.min_score
        max_lessons = max(1, int(max_lessons))
        min_score = min(max(float(min_score), 0.0), 1.0)

        candidates: list[RetrievedLesson] = []
        excluded = set(query.exclude_episode_ids)
        for episode in self._episodes.values():
            if episode.episode_id in excluded:
                continue
            if not self.config.allow_same_scenario_retrieval and _same_scenario(query.signature, episode.signature):
                # Avoid turning memory into a same-task answer cache.
                continue
            if not query.include_failed and episode.outcome.outcome in {"failed", "blocked"}:
                continue
            for lesson in episode.lessons:
                if query.desired_lesson_types and lesson.lesson_type not in set(query.desired_lesson_types):
                    continue
                score, reasons = self._score_lesson(lesson, episode, query)
                if score >= min_score:
                    candidates.append(
                        RetrievedLesson(
                            lesson=lesson,
                            episode_id=episode.episode_id,
                            score=score,
                            reasons=reasons,
                            source_signature=episode.signature,
                            outcome=episode.outcome,
                        )
                    )

        candidates.sort(key=lambda item: (-item.score, -item.lesson.confidence, item.lesson.lesson_type, item.lesson.lesson_id))
        deduped = _dedupe_retrieved_lessons(candidates, threshold=self.config.dedupe_similarity_threshold)
        selected = tuple(deduped[:max_lessons])
        omitted_count = max(0, len(deduped) - len(selected))
        summary = self._summarize_retrieval(selected, scanned=len(self._episodes), omitted=omitted_count)

        return EpisodicRetrievalResult(
            query_digest=_query_digest(query),
            lessons=selected,
            scanned_episodes=len(self._episodes),
            omitted_count=omitted_count,
            summary=summary,
            metadata={
                "min_score": min_score,
                "max_lessons": max_lessons,
                "same_scenario_retrieval": self.config.allow_same_scenario_retrieval,
            },
        )

    def retrieve_for_state(
        self,
        state: CognitiveState,
        *,
        text: str = "",
        desired_lesson_types: Iterable[str] = (),
        tags: Iterable[str] = (),
        max_lessons: int | None = None,
    ) -> EpisodicRetrievalResult:
        return self.retrieve(
            EpisodicRetrievalQuery.from_state(
                state,
                text=text,
                desired_lesson_types=desired_lesson_types,
                tags=tags,
                max_lessons=max_lessons,
            )
        )

    def apply_lessons_to_state(
        self,
        state: CognitiveState,
        *,
        retrieval: EpisodicRetrievalResult | None = None,
        max_lessons: int | None = None,
    ) -> CognitiveState:
        result = retrieval or self.retrieve_for_state(state, max_lessons=max_lessons)
        next_state = state
        for retrieved in result.lessons:
            lesson = retrieved.lesson
            content = f"episodic_{lesson.lesson_type}: {lesson.statement}"
            item = _working_memory_item_from_lesson(lesson, score=retrieved.score, turn_index=state.turn_index)
            next_state = next_state.remember(item)
        next_state = next_state.append_trace(
            phase="episodic_memory",
            message=f"Retrieved {len(result.lessons)} generalized episodic lessons.",
            refs=[lesson.lesson.lesson_id for lesson in result.lessons[:8]],
            metadata={
                "query_digest": result.query_digest,
                "omitted_count": result.omitted_count,
                "scanned_episodes": result.scanned_episodes,
            },
        )
        return next_state.reestimate_uncertainty().refresh_scorecard()

    def forget_episode(self, episode_id: str, *, persist: bool = True) -> bool:
        episode_id = str(episode_id)
        if episode_id not in self._episodes:
            return False
        del self._episodes[episode_id]
        self._rebuild_lesson_index()
        if persist:
            self.save()
        return True

    def clear(self, *, persist: bool = True) -> int:
        count = len(self._episodes)
        self._episodes.clear()
        self._lesson_index.clear()
        if persist:
            self.save()
        return count

    def enforce_capacity(self) -> int:
        if len(self._episodes) <= self.config.max_episodes:
            return 0
        ranked = sorted(
            self._episodes.values(),
            key=lambda ep: (
                _outcome_value(ep.outcome),
                _optional_timestamp(ep.updated_at or ep.created_at),
                len(ep.lessons),
            ),
            reverse=True,
        )
        keep = ranked[: self.config.max_episodes]
        keep_ids = {ep.episode_id for ep in keep}
        dropped = len(self._episodes) - len(keep_ids)
        self._episodes = {episode_id: ep for episode_id, ep in self._episodes.items() if episode_id in keep_ids}
        self._rebuild_lesson_index()
        return max(0, dropped)

    def save(self, path: str | Path | None = None) -> None:
        persist_path = Path(path or self.config.persist_path or "")
        if not str(persist_path):
            return
        persist_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = persist_path.with_suffix(persist_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            for episode in self.episodes():
                fh.write(json.dumps(episode.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        tmp_path.replace(persist_path)

    def load(self, path: str | Path | None = None) -> int:
        persist_path = Path(path or self.config.persist_path or "")
        if not str(persist_path) or not persist_path.exists():
            return 0
        loaded = 0
        with persist_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    episode = EpisodeRecord.from_dict(data)
                    self.add_episode(episode, persist=False)
                    loaded += 1
                except Exception:
                    continue
        self.enforce_capacity()
        return loaded

    def stats(self) -> EpisodicMemoryStats:
        domain_counts = {}
        outcome_counts = {}
        lesson_type_counts = {}
        for episode in self._episodes.values():
            domain_counts[episode.signature.domain] = domain_counts.get(episode.signature.domain, 0) + 1
            outcome_counts[episode.outcome.outcome] = outcome_counts.get(episode.outcome.outcome, 0) + 1
            for lesson in episode.lessons:
                lesson_type_counts[lesson.lesson_type] = lesson_type_counts.get(lesson.lesson_type, 0) + 1
        return EpisodicMemoryStats(
            version=NCP_EPISODIC_MEMORY_VERSION,
            episode_count=len(self._episodes),
            lesson_count=sum(len(ep.lessons) for ep in self._episodes.values()),
            domain_counts=dict(sorted(domain_counts.items())),
            outcome_counts=dict(sorted(outcome_counts.items())),
            lesson_type_counts=dict(sorted(lesson_type_counts.items())),
            max_episodes=self.config.max_episodes,
            persist_path=self.config.persist_path,
            updated_at=_utc_now(),
        )

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "version": NCP_EPISODIC_MEMORY_VERSION,
            "config": _json_safe(asdict(self.config)),
            "stats": self.stats().to_dict(),
            "episodes": [episode.to_dict() for episode in self.episodes()],
        }

    def _sanitize_episode(self, episode: EpisodeRecord) -> EpisodeRecord:
        clean = episode.sanitized()
        if not self.config.store_exact_outputs:
            clean = replace(clean, metadata=_drop_sensitive_exact_fields(clean.metadata))
        return clean

    def _merge_episode(self, old: EpisodeRecord, new: EpisodeRecord) -> EpisodeRecord:
        old_lessons = {lesson.lesson_id: lesson for lesson in old.lessons}
        for lesson in new.lessons:
            current = old_lessons.get(lesson.lesson_id)
            if current is None:
                old_lessons[lesson.lesson_id] = lesson
            else:
                if new.outcome.success:
                    old_lessons[lesson.lesson_id] = current.reinforce(evidence_refs=lesson.evidence_refs)
                else:
                    old_lessons[lesson.lesson_id] = current.contradict(evidence_refs=lesson.evidence_refs)
        return replace(
            old,
            outcome=new.outcome,
            lessons=tuple(old_lessons.values()),
            scorecard={**dict(old.scorecard), **dict(new.scorecard)},
            uncertainty={**dict(old.uncertainty), **dict(new.uncertainty)},
            evidence_summary={**dict(old.evidence_summary), **dict(new.evidence_summary)},
            decision_summary={**dict(old.decision_summary), **dict(new.decision_summary)},
            trace_summary={**dict(old.trace_summary), **dict(new.trace_summary)},
            updated_at=_utc_now(),
            metadata=_redact_mapping({**dict(old.metadata), **dict(new.metadata)}),
        )

    def _update_lesson_index(self, episode: EpisodeRecord) -> None:
        for lesson in episode.lessons:
            current = self._lesson_index.get(lesson.lesson_id)
            if current is None:
                self._lesson_index[lesson.lesson_id] = lesson
            elif episode.outcome.success:
                self._lesson_index[lesson.lesson_id] = current.reinforce(evidence_refs=lesson.evidence_refs)
            else:
                self._lesson_index[lesson.lesson_id] = current.contradict(evidence_refs=lesson.evidence_refs)

    def _rebuild_lesson_index(self) -> None:
        self._lesson_index.clear()
        for episode in self._episodes.values():
            self._update_lesson_index(episode)

    def _score_lesson(
        self,
        lesson: EpisodicLesson,
        episode: EpisodeRecord,
        query: EpisodicRetrievalQuery,
    ) -> tuple[float, tuple[str, ...]]:
        score = 0.18 + 0.36 * lesson.confidence
        reasons: list[str] = [f"lesson_confidence={lesson.confidence:.2f}"]

        signature = query.signature
        source = episode.signature

        if self.config.prefer_same_domain and signature.domain == source.domain:
            score += 0.18
            reasons.append("same_domain")
        elif signature.domain in lesson.domains:
            score += 0.12
            reasons.append("lesson_domain_match")

        if signature.track == source.track:
            score += 0.06
            reasons.append("same_track")
        if signature.task_type == source.task_type:
            score += 0.10
            reasons.append("same_task_type")

        if self.config.prefer_same_scenario_family and signature.scenario_family == source.scenario_family:
            score += 0.10
            reasons.append("same_scenario_family")

        tool_overlap = _overlap_ratio(signature.required_tools, source.required_tools)
        if tool_overlap:
            score += 0.10 * tool_overlap
            reasons.append(f"tool_overlap={tool_overlap:.2f}")

        risk_overlap = _overlap_ratio(signature.risk_tags, source.risk_tags)
        if risk_overlap:
            score += 0.11 * risk_overlap
            reasons.append(f"risk_overlap={risk_overlap:.2f}")

        uncertainty_overlap = _overlap_ratio(signature.uncertainty_tags, source.uncertainty_tags)
        if uncertainty_overlap:
            score += 0.09 * uncertainty_overlap
            reasons.append(f"uncertainty_overlap={uncertainty_overlap:.2f}")

        query_tags = set(query.tags)
        if query_tags:
            tag_overlap = len(query_tags & set(lesson.tags))
            if tag_overlap:
                score += min(0.15, 0.05 * tag_overlap)
                reasons.append(f"query_tag_overlap={tag_overlap}")

        if query.text:
            sim = _text_similarity(query.text, lesson.statement)
            if sim:
                score += 0.16 * sim
                reasons.append(f"text_similarity={sim:.2f}")

        if query.desired_lesson_types and lesson.lesson_type in set(query.desired_lesson_types):
            score += 0.10
            reasons.append("desired_type")

        if episode.outcome.success:
            score += 0.07
            reasons.append("successful_episode")
        elif episode.outcome.outcome in {"failed", "blocked"}:
            score += 0.03
            reasons.append("failure_lesson")

        age_days = max(0.0, (_now_timestamp() - _optional_timestamp(episode.updated_at or episode.created_at)) / 86400.0)
        recency = _recency_score(age_days, self.config.recency_half_life_days)
        score += 0.08 * recency
        reasons.append(f"recency={recency:.2f}")

        if lesson.contraindication_count:
            penalty = min(0.18, 0.04 * lesson.contraindication_count)
            score -= penalty
            reasons.append(f"contraindication_penalty={penalty:.2f}")

        return _bounded_float(score, default=0.0), tuple(reasons)

    @staticmethod
    def _summarize_retrieval(selected: Sequence[RetrievedLesson], *, scanned: int, omitted: int) -> str:
        if not selected:
            return f"No generalized lessons retrieved from {scanned} episodes."
        type_counts: dict[str, int] = {}
        domains: dict[str, int] = {}
        for item in selected:
            type_counts[item.lesson.lesson_type] = type_counts.get(item.lesson.lesson_type, 0) + 1
            if item.source_signature:
                domains[item.source_signature.domain] = domains.get(item.source_signature.domain, 0) + 1
        type_text = ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items()))
        domain_text = ", ".join(f"{k}={v}" for k, v in sorted(domains.items())[:5])
        parts = [f"retrieved={len(selected)}", f"types({type_text})"]
        if domain_text:
            parts.append(f"domains({domain_text})")
        if omitted:
            parts.append(f"omitted={omitted}")
        return "; ".join(parts) + "."


def extract_lessons_from_state(
    state: CognitiveState,
    outcome: EpisodeOutcome | None = None,
) -> tuple[EpisodicLesson, ...]:
    """Extract generalized lessons from a CognitiveState.

    The extraction is intentionally conservative. It stores patterns, not final
    task answers.  It favors policy/evidence/uncertainty/adapter lessons that
    can generalize to held-out tasks.
    """

    outcome = outcome or EpisodeOutcome.from_state(state)
    tt = state.task_theory
    domains = (tt.domain,)
    task_types = (tt.task_type,)
    families = (tt.scenario_family,)

    lessons: list[EpisodicLesson] = []

    if tt.required_tools:
        lessons.append(
            EpisodicLesson.new(
                lesson_type="tooling",
                statement=(
                    f"For {tt.domain}/{tt.task_type}, preserve tool discipline: use required tools "
                    f"({', '.join(tt.required_tools[:6])}) before committing material conclusions."
                ),
                confidence=0.70 if outcome.success else 0.52,
                domains=domains,
                task_types=task_types,
                scenario_families=families,
                tags=("tool_sequence", "evidence_before_answer"),
            )
        )

    if tt.constraints or tt.success_criteria:
        lessons.append(
            EpisodicLesson.new(
                lesson_type="strategy",
                statement=(
                    f"Translate constraints and success criteria into explicit checks before action; "
                    f"this reduces brittle benchmark-specific behavior in {tt.domain} tasks."
                ),
                confidence=0.68 if outcome.success else 0.55,
                domains=domains,
                task_types=task_types,
                scenario_families=families,
                tags=("constraint_tracking", "heldout_generalization"),
            )
        )

    if state.policy_boundary.denied_behaviors:
        lessons.append(
            EpisodicLesson.new(
                lesson_type="policy",
                statement=(
                    "Keep fair-play and policy boundaries active throughout the episode: "
                    "avoid hardcoded answers, task-specific lookup tables, platform exploitation, "
                    "secret extraction, and real-world unsafe actions."
                ),
                confidence=0.82,
                domains=domains,
                task_types=task_types,
                scenario_families=families,
                tags=("fair_play", "policy_boundary", "hardcoding_guard"),
            )
        )

    unsupported = sum(1 for record in state.evidence if record.status == "unsupported")
    conflicting = sum(1 for record in state.evidence if record.status == "conflicting")
    supported = sum(1 for record in state.evidence if record.status == "supported")
    if supported or unsupported or conflicting or tt.evidence_requirements:
        status_hint = f"supported={supported}, unsupported={unsupported}, conflicting={conflicting}"
        lessons.append(
            EpisodicLesson.new(
                lesson_type="evidence",
                statement=(
                    f"Track evidence status explicitly ({status_hint}); when evidence is missing or conflicting, "
                    "prefer more observation, safe insufficiency, or manual review over unsupported claims."
                ),
                confidence=0.74 if unsupported or conflicting else 0.66,
                domains=domains,
                task_types=task_types,
                scenario_families=families,
                tags=("evidence_grounding", "needs_evidence"),
                evidence_refs=(record.evidence_id for record in state.evidence[:8]),
            )
        )

    if state.uncertainty.level in {"high", "critical"} or state.uncertainty.evidence_gap:
        lessons.append(
            EpisodicLesson.new(
                lesson_type="uncertainty",
                statement=(
                    f"When uncertainty is {state.uncertainty.level}"
                    f"{' with ' + state.uncertainty.evidence_gap if state.uncertainty.evidence_gap else ''}, "
                    f"use the recommended action '{state.uncertainty.recommended_action}' before finalizing."
                ),
                confidence=0.72,
                domains=domains,
                task_types=task_types,
                scenario_families=families,
                tags=("uncertainty_control", state.uncertainty.level, "active_inference_style"),
            )
        )

    blocked_or_unsafe = any(decision.safety_status in {"blocked", "unsafe"} for decision in state.decisions)
    if blocked_or_unsafe or outcome.blocked_by_policy:
        lessons.append(
            EpisodicLesson.new(
                lesson_type="risk",
                statement=(
                    "If a candidate action crosses policy or safety boundaries, preserve useful safe alternatives "
                    "instead of completing the risky action."
                ),
                confidence=0.78,
                domains=domains,
                task_types=task_types,
                scenario_families=families,
                tags=("risk_control", "safe_alternative", "policy_boundary"),
            )
        )

    if state.adapter_profile.adapter or state.adapter_profile.available_actions:
        lessons.append(
            EpisodicLesson.new(
                lesson_type="adapter",
                statement=(
                    f"Use the adapter profile as the contract for action shape and available actions in "
                    f"{state.adapter_profile.domain}; avoid inventing hidden tools."
                ),
                confidence=0.67 if outcome.success else 0.55,
                domains=domains,
                task_types=task_types,
                scenario_families=families,
                tags=("adapter_contract", "tool_affordance"),
            )
        )

    if state.scorecard.cost_efficiency < 0.65:
        lessons.append(
            EpisodicLesson.new(
                lesson_type="cost",
                statement=(
                    "Memory/context pressure can reduce cost efficiency; compact low-salience context and keep "
                    "only policy, evidence, plan, and active uncertainty signals."
                ),
                confidence=0.64,
                domains=domains,
                task_types=task_types,
                scenario_families=families,
                tags=("context_budget", "compression"),
            )
        )

    if state.trace:
        lessons.append(
            EpisodicLesson.new(
                lesson_type="traceability",
                statement=(
                    "Emit compact trace events at attention, memory, evidence, uncertainty, and decision phases "
                    "so held-out evaluation can inspect general reasoning rather than task-specific memorization."
                ),
                confidence=0.70,
                domains=domains,
                task_types=task_types,
                scenario_families=families,
                tags=("traceability", "auditability"),
            )
        )

    return tuple(_dedupe_lessons(lessons))


def build_episodic_memory(
    *,
    config: EpisodicMemoryConfig | None = None,
    episodes: Iterable[EpisodeRecord] = (),
    autoload: bool = False,
) -> EpisodicMemoryStore:
    return EpisodicMemoryStore(config=config, episodes=episodes, autoload=autoload)


def record_episode(
    store: EpisodicMemoryStore,
    state: CognitiveState,
    *,
    outcome: EpisodeOutcome | None = None,
    extra_lessons: Sequence[EpisodicLesson] = (),
    extra_metadata: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> EpisodeRecord:
    return store.record_state(
        state,
        outcome=outcome,
        extra_lessons=extra_lessons,
        extra_metadata=extra_metadata,
        persist=persist,
    )


def retrieve_episodic_lessons(
    store: EpisodicMemoryStore,
    state: CognitiveState,
    *,
    text: str = "",
    desired_lesson_types: Iterable[str] = (),
    tags: Iterable[str] = (),
    max_lessons: int | None = None,
) -> EpisodicRetrievalResult:
    return store.retrieve_for_state(
        state,
        text=text,
        desired_lesson_types=desired_lesson_types,
        tags=tags,
        max_lessons=max_lessons,
    )


def format_episodic_prompt(result: EpisodicRetrievalResult, *, title: str = "NCP Episodic Lessons") -> str:
    return result.compact_context(title=title)


def _working_memory_item_from_lesson(
    lesson: EpisodicLesson,
    *,
    score: float,
    turn_index: int,
) -> Any:
    # Imported lazily through state's public class to avoid a direct dependency
    # on working_memory.py and prevent circular imports.
    from .state import WorkingMemoryItem

    return WorkingMemoryItem(
        key=f"episodic_{lesson.lesson_id[:18]}",
        content=_clip(lesson.statement, 620),
        source="episodic_memory",
        salience=_bounded_float(score, default=lesson.confidence),
        turn_index=turn_index,
        tags=tuple(_unique(("episodic_lesson", lesson.lesson_type, *lesson.tags))),
        evidence_refs=lesson.evidence_refs,
        ttl_turns=10,
        locked=lesson.lesson_type in {"policy", "risk"},
        metadata={
            "lesson_id": lesson.lesson_id,
            "lesson_type": lesson.lesson_type,
            "support_count": lesson.support_count,
            "contraindication_count": lesson.contraindication_count,
        },
    )


def _scorecard_summary(scorecard: CognitiveScorecard | Mapping[str, Any]) -> dict[str, Any]:
    data = scorecard.to_dict() if hasattr(scorecard, "to_dict") else _as_mapping(scorecard)
    allowed = {
        "generality",
        "evidence_grounding",
        "uncertainty_handling",
        "safety",
        "cost_efficiency",
        "traceability",
        "adapter_fit",
        "innovation",
        "aggregate",
        "metadata",
    }
    return _redact_mapping({key: value for key, value in data.items() if key in allowed})


def _uncertainty_summary(uncertainty: UncertaintyEstimate | Mapping[str, Any]) -> dict[str, Any]:
    data = uncertainty.to_dict() if hasattr(uncertainty, "to_dict") else _as_mapping(uncertainty)
    allowed = {
        "confidence",
        "level",
        "evidence_gap",
        "ambiguity_count",
        "contradiction_count",
        "risk_count",
        "recommended_action",
        "drivers",
    }
    return _redact_mapping({key: value for key, value in data.items() if key in allowed})


def _evidence_summary(evidence: Sequence[EvidenceRecord]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    kinds: dict[str, int] = {}
    refs: list[str] = []
    for record in evidence:
        statuses[record.status] = statuses.get(record.status, 0) + 1
        kinds[record.kind] = kinds.get(record.kind, 0) + 1
        refs.append(record.evidence_id)
    return {
        "count": len(evidence),
        "statuses": dict(sorted(statuses.items())),
        "kinds": dict(sorted(kinds.items())),
        "refs": refs[:16],
    }


def _decision_summary(decisions: Sequence[CognitiveDecision]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    actions: list[str] = []
    for decision in decisions:
        statuses[decision.safety_status] = statuses.get(decision.safety_status, 0) + 1
        actions.append(_generalize_action(decision.selected_action))
    return {
        "count": len(decisions),
        "safety_statuses": dict(sorted(statuses.items())),
        "action_patterns": list(_unique(actions))[:12],
    }


def _trace_summary(trace: Sequence[TraceEvent], *, include_raw: bool = False) -> dict[str, Any]:
    phases: dict[str, int] = {}
    severities: dict[str, int] = {}
    for event in trace:
        phases[event.phase] = phases.get(event.phase, 0) + 1
        severities[event.severity] = severities.get(event.severity, 0) + 1
    result: dict[str, Any] = {
        "count": len(trace),
        "phases": dict(sorted(phases.items())),
        "severities": dict(sorted(severities.items())),
    }
    if include_raw:
        result["events"] = [
            {
                "phase": event.phase,
                "severity": event.severity,
                "message": _clip(_sanitize(event.message), 240),
                "refs": list(event.refs[:6]),
            }
            for event in trace[:80]
        ]
    return result


def _drop_unsafe_episode_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in data.items():
        key_text = str(key)
        if _is_sensitive_key(key_text):
            continue
        if isinstance(value, Mapping):
            safe[key_text] = _drop_unsafe_episode_fields(value)
        elif isinstance(value, list):
            safe[key_text] = [
                _drop_unsafe_episode_fields(item) if isinstance(item, Mapping) else _redact_value(item)
                for item in value
            ]
        else:
            safe[key_text] = _redact_value(value)
    return safe


def _drop_sensitive_exact_fields(mapping: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        key_norm = _normalize_identifier(key)
        if key_norm in SENSITIVE_TAGS or any(tag in key_norm for tag in SENSITIVE_TAGS):
            continue
        if isinstance(value, Mapping):
            out[str(key)] = _drop_sensitive_exact_fields(value)
        elif isinstance(value, str) and _looks_like_exact_answer(value):
            out[str(key)] = "<generalized>"
        else:
            out[str(key)] = _redact_value(value)
    return out


def _dedupe_lessons(lessons: Sequence[EpisodicLesson]) -> list[EpisodicLesson]:
    kept: list[EpisodicLesson] = []
    for lesson in lessons:
        if _looks_like_exact_answer(lesson.statement):
            continue
        duplicate_index = None
        for idx, current in enumerate(kept):
            if lesson.lesson_type == current.lesson_type and _text_similarity(lesson.statement, current.statement) >= 0.88:
                duplicate_index = idx
                break
        if duplicate_index is None:
            kept.append(lesson)
        else:
            kept[duplicate_index] = kept[duplicate_index].reinforce(evidence_refs=lesson.evidence_refs)
    return kept


def _dedupe_retrieved_lessons(
    lessons: Sequence[RetrievedLesson],
    *,
    threshold: float,
) -> list[RetrievedLesson]:
    kept: list[RetrievedLesson] = []
    for lesson in lessons:
        duplicate = False
        for current in kept:
            if (
                lesson.lesson.lesson_type == current.lesson.lesson_type
                and _text_similarity(lesson.lesson.statement, current.lesson.statement) >= threshold
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(lesson)
    return kept


def _same_scenario(left: EpisodeSignature, right: EpisodeSignature) -> bool:
    left_id = _normalize_identifier(left.scenario_id or left.scenario_name or "")
    right_id = _normalize_identifier(right.scenario_id or right.scenario_name or "")
    return bool(left_id and right_id and left_id == right_id)


def _outcome_value(outcome: EpisodeOutcome) -> float:
    base = {
        "success": 1.0,
        "partial": 0.72,
        "blocked": 0.62,
        "failed": 0.54,
        "unknown": 0.40,
    }.get(outcome.outcome, 0.40)
    if outcome.score is not None:
        base = max(base, min(1.0, float(outcome.score)))
    if outcome.safety_status in {"safe", "needs_review"}:
        base += 0.04
    return min(1.0, base)


def _generalize_action(action: str) -> str:
    text = _sanitize(action).lower()
    text = _SECRET_VALUE_RE.sub("<redacted>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "<num>", text)
    text = re.sub(r"https?://\S+", "<url>", text)
    return _clip(text, 180)


def _looks_like_exact_answer(text: str) -> bool:
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in ("final answer:", "gold answer", "ground truth", "exact answer")):
        return True
    if _NUMBER_HEAVY_RE.search(lowered) and len(lowered) < 240:
        return True
    return False


def _is_sensitive_key(key: str) -> bool:
    norm = _normalize_identifier(key)
    if _SECRET_KEY_RE.search(key):
        return True
    return norm in SENSITIVE_TAGS or any(tag in norm for tag in SENSITIVE_TAGS)


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


def _filter_dataclass_kwargs(cls: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    names = set(getattr(cls, "__dataclass_fields__", {}).keys())
    return {key: value for key, value in data.items() if key in names}


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


def _safe_text(value: Any) -> str | None:
    if value in (None, "") or isinstance(value, Mapping):
        return None
    text = _sanitize(value)
    return text or None


def _semantic_tag(value: Any) -> str:
    text = _normalize_identifier(value)
    parts = [part for part in text.split("_") if part]
    return "_".join(parts[:6]) if parts else "unknown"


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


def _stable_id(prefix: str, *parts: Any) -> str:
    blob = json.dumps([_json_safe(part) for part in parts], ensure_ascii=False, sort_keys=True)
    return f"{prefix}_{sha256(blob.encode('utf-8')).hexdigest()}"


def _bounded_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return min(1.0, max(0.0, number))


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _text_similarity(a: str, b: str) -> float:
    left = _tokens(a)
    right = _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _overlap_ratio(left: Sequence[str], right: Sequence[str]) -> float:
    a = set(left)
    b = set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _query_digest(query: EpisodicRetrievalQuery) -> str:
    blob = json.dumps(_json_safe(asdict(query)), ensure_ascii=False, sort_keys=True)
    return sha256(blob.encode("utf-8")).hexdigest()[:16]


def _recency_score(age_days: float, half_life_days: float) -> float:
    age = max(0.0, float(age_days))
    half_life = max(1.0, float(half_life_days))
    return math.exp(-math.log(2) * age / half_life)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


def _optional_timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        text = value.replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


__all__ = [
    "NCP_EPISODIC_MEMORY_VERSION",
    "DEFAULT_JSONL_NAME",
    "EpisodeOutcome",
    "EpisodeRecord",
    "EpisodeSignature",
    "EpisodicLesson",
    "EpisodicMemoryConfig",
    "EpisodicMemoryStats",
    "EpisodicMemoryStore",
    "EpisodicRetrievalQuery",
    "EpisodicRetrievalResult",
    "RetrievedLesson",
    "build_episodic_memory",
    "extract_lessons_from_state",
    "format_episodic_prompt",
    "record_episode",
    "retrieve_episodic_lessons",
]
