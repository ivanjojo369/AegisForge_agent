from __future__ import annotations

"""Cognitive controller for AegisForge NCP — Neuro-Cognitive Purple Core.

This is the orchestration layer that wires together the NCP cognitive modules:

1. attention.py          -> salience selection
2. state.py              -> immutable cognitive state contract
3. working_memory.py     -> active memory and prompt/context selection
4. episodic_memory.py    -> generalized lessons from prior episodes
5. evidence.py           -> claim/source grounding
6. uncertainty.py        -> confidence, risk, and action gating
7. metacognition.py      -> self-audit and final readiness decision

The controller is intentionally adapter-friendly.  It does not replace the
existing AegisForge agent, router, planner, self-check, or telemetry modules.
It gives ``agent.py`` a single high-level object that can prepare cognitive
context before planning, assess a proposed action after planning, and record an
episode afterward.

Important constraints:
- no benchmark answer lookup tables;
- no same-scenario episodic answer cache;
- no direct dependency on a specific LLM provider;
- deterministic, serializable artifacts for tests and traces;
- explicit policy/evidence/uncertainty/metacognitive gates.
"""

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import json
import math
import re
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .attention import AttentionConfig, AttentionFrame, AttentionGate, select_attention
from .episodic_memory import (
    EpisodeOutcome,
    EpisodeRecord,
    EpisodicMemoryConfig,
    EpisodicMemoryStore,
    EpisodicRetrievalResult,
    build_episodic_memory,
)
from .evidence import EvidenceBundle, EvidenceConfig, EvidenceVerifier, build_evidence_bundle
from .metacognition import (
    MetacognitionConfig,
    MetacognitionReport,
    MetacognitiveController,
    metacognitive_memory_items,
)
from .state import (
    CognitiveDecision,
    CognitiveScorecard,
    CognitiveState,
    DecisionOption,
    EvidenceRecord,
    TaskTheory,
    TraceEvent,
    WorkingMemoryItem,
    new_cognitive_state,
)
from .uncertainty import (
    ActionGate,
    UncertaintyAssessment,
    UncertaintyConfig,
    UncertaintyEstimator,
    build_decision_options_from_assessment,
)
from .working_memory import (
    MemoryQuery,
    MemorySelection,
    WorkingMemoryConfig,
    WorkingMemoryStore,
    build_working_memory,
    format_memory_prompt,
)


class SupportsToDict(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


class SupportsInvoke(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


NCP_CONTROLLER_VERSION = "0.1.0"

DEFAULT_MAX_CONTROLLER_STEPS = 64
DEFAULT_PROMPT_CONTEXT_CHARS = 7_500
DEFAULT_FINAL_CONTEXT_CHARS = 8_500

VALID_CONTROLLER_STATUS = (
    "initialized",
    "prepared",
    "awaiting_plan",
    "awaiting_action",
    "needs_evidence",
    "needs_review",
    "blocked",
    "ready",
    "completed",
    "failed",
)

VALID_PHASES = (
    "initialize",
    "attention",
    "working_memory",
    "episodic_retrieval",
    "evidence",
    "uncertainty",
    "metacognition",
    "decision",
    "finalize",
    "record_episode",
)

SAFE_FALLBACK_ACTION = "block_or_safe_alternative"

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|private[_-]?key|session|cookie|auth)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|ghp_[a-z0-9_]{16,}|xox[baprs]-[a-z0-9-]{10,}|"
    r"bearer\s+[a-z0-9._-]{16,}|api[_-]?key\s*[:=]\s*[a-z0-9._-]{12,})"
)


class ControllerPhase(str, Enum):
    INITIALIZE = "initialize"
    ATTENTION = "attention"
    WORKING_MEMORY = "working_memory"
    EPISODIC_RETRIEVAL = "episodic_retrieval"
    EVIDENCE = "evidence"
    UNCERTAINTY = "uncertainty"
    METACOGNITION = "metacognition"
    DECISION = "decision"
    FINALIZE = "finalize"
    RECORD_EPISODE = "record_episode"


@dataclass(frozen=True, slots=True)
class CognitiveControllerConfig:
    """Configuration for the top-level NCP controller."""

    attention: AttentionConfig = field(default_factory=AttentionConfig)
    working_memory: WorkingMemoryConfig = field(default_factory=WorkingMemoryConfig)
    episodic_memory: EpisodicMemoryConfig = field(default_factory=EpisodicMemoryConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    uncertainty: UncertaintyConfig = field(default_factory=UncertaintyConfig)
    metacognition: MetacognitionConfig = field(default_factory=MetacognitionConfig)
    enable_episodic_retrieval: bool = True
    enable_evidence_verification: bool = True
    enable_uncertainty: bool = True
    enable_metacognition: bool = True
    record_episodes: bool = False
    max_controller_steps: int = DEFAULT_MAX_CONTROLLER_STEPS
    prompt_context_chars: int = DEFAULT_PROMPT_CONTEXT_CHARS
    final_context_chars: int = DEFAULT_FINAL_CONTEXT_CHARS
    safe_default_action: str = SAFE_FALLBACK_ACTION
    fail_closed_on_policy_block: bool = True
    include_prompt_context_in_output: bool = True
    redact_sensitive: bool = True

    def normalized(self) -> "CognitiveControllerConfig":
        return CognitiveControllerConfig(
            attention=self.attention.normalized(),
            working_memory=self.working_memory.normalized(),
            episodic_memory=self.episodic_memory.normalized(),
            evidence=self.evidence.normalized(),
            uncertainty=self.uncertainty.normalized(),
            metacognition=self.metacognition.normalized(),
            enable_episodic_retrieval=bool(self.enable_episodic_retrieval),
            enable_evidence_verification=bool(self.enable_evidence_verification),
            enable_uncertainty=bool(self.enable_uncertainty),
            enable_metacognition=bool(self.enable_metacognition),
            record_episodes=bool(self.record_episodes),
            max_controller_steps=max(8, int(self.max_controller_steps)),
            prompt_context_chars=max(1_200, int(self.prompt_context_chars)),
            final_context_chars=max(1_200, int(self.final_context_chars)),
            safe_default_action=str(self.safe_default_action or SAFE_FALLBACK_ACTION),
            fail_closed_on_policy_block=bool(self.fail_closed_on_policy_block),
            include_prompt_context_in_output=bool(self.include_prompt_context_in_output),
            redact_sensitive=bool(self.redact_sensitive),
        )


@dataclass(frozen=True, slots=True)
class ControllerInput:
    """Input contract for one NCP controller run."""

    task_text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    classification: Any = None
    route: Any = None
    plan: Any = None
    budget_state: Any = None
    policy_context: Mapping[str, Any] = field(default_factory=dict)
    prompt_context: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    memory: Mapping[str, Any] | Sequence[Any] | None = None
    candidate_action: str | DecisionOption | CognitiveDecision | None = None
    candidate_text: str | None = None
    external_signals: Mapping[str, Any] = field(default_factory=dict)
    desired_lesson_types: tuple[str, ...] = ()
    retrieval_tags: tuple[str, ...] = ()
    outcome: EpisodeOutcome | None = None
    extra_metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(
            {
                "task_text": _clip(_sanitize(self.task_text), 2_000),
                "metadata": _redact_mapping(dict(self.metadata)),
                "classification": _redact_mapping(_as_mapping(self.classification)),
                "route": _redact_mapping(_as_mapping(self.route)),
                "plan": _redact_mapping(_as_mapping(self.plan)),
                "budget_state": _redact_mapping(_as_mapping(self.budget_state)),
                "policy_context": _redact_mapping(dict(self.policy_context)),
                "prompt_context": _redact_mapping(dict(self.prompt_context)),
                "payload": _redact_mapping(dict(self.payload)),
                "candidate_action": _candidate_text(self.candidate_action),
                "candidate_text": _clip(_sanitize(self.candidate_text or ""), 2_000),
                "external_signal_keys": sorted(dict(self.external_signals).keys()),
                "desired_lesson_types": list(self.desired_lesson_types),
                "retrieval_tags": list(self.retrieval_tags),
                "extra_metadata": _redact_mapping(dict(self.extra_metadata)),
            }
        )


@dataclass(frozen=True, slots=True)
class ControllerStep:
    """One phase transition emitted by the controller."""

    phase: str
    status: str
    message: str
    refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        phase: str | ControllerPhase,
        status: str,
        message: str,
        refs: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "ControllerStep":
        phase_value = phase.value if isinstance(phase, ControllerPhase) else str(phase)
        return cls(
            phase=_validate_choice(phase_value, VALID_PHASES, "initialize"),
            status=_validate_choice(status, VALID_CONTROLLER_STATUS, "prepared"),
            message=_clip(_sanitize(message), 1_000),
            refs=tuple(_unique(str(ref) for ref in refs)),
            metadata=_redact_mapping(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class ControllerArtifacts:
    """Structured artifacts produced by a controller pass."""

    attention_frame: AttentionFrame | None = None
    memory_selection: MemorySelection | None = None
    episodic_retrieval: EpisodicRetrievalResult | None = None
    evidence_bundle: EvidenceBundle | None = None
    uncertainty: UncertaintyAssessment | None = None
    metacognition: MetacognitionReport | None = None
    decision: CognitiveDecision | None = None
    recorded_episode: EpisodeRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attention_frame": self.attention_frame.to_dict() if self.attention_frame else None,
            "memory_selection": self.memory_selection.to_dict() if self.memory_selection else None,
            "episodic_retrieval": self.episodic_retrieval.to_dict() if self.episodic_retrieval else None,
            "evidence_bundle": self.evidence_bundle.to_dict() if self.evidence_bundle else None,
            "uncertainty": self.uncertainty.to_dict() if self.uncertainty else None,
            "metacognition": self.metacognition.to_dict() if self.metacognition else None,
            "decision": self.decision.to_dict() if self.decision else None,
            "recorded_episode": self.recorded_episode.to_dict() if self.recorded_episode else None,
        }


@dataclass(frozen=True, slots=True)
class ControllerOutput:
    """Final output from a controller run."""

    run_id: str
    version: str
    status: str
    recommended_action: str
    state: CognitiveState
    artifacts: ControllerArtifacts
    steps: tuple[ControllerStep, ...]
    prompt_context: str = ""
    controller_summary: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def safe_to_finalize(self) -> bool:
        if self.status != "ready":
            return False
        if self.artifacts.metacognition and not self.artifacts.metacognition.safe_to_finalize:
            return False
        if self.artifacts.uncertainty and self.artifacts.uncertainty.action_gate.allowed is False:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "version": self.version,
            "status": self.status,
            "recommended_action": self.recommended_action,
            "safe_to_finalize": self.safe_to_finalize,
            "state": self.state.to_dict(),
            "artifacts": self.artifacts.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "prompt_context": self.prompt_context,
            "controller_summary": self.controller_summary,
            "metadata": _json_safe(dict(self.metadata)),
        }

    def compact_context(self) -> str:
        lines = [
            f"AegisForge NCP Controller: status={self.status} action={self.recommended_action}",
            f"run_id={self.run_id}; safe_to_finalize={self.safe_to_finalize}",
            self.controller_summary,
        ]
        if self.prompt_context:
            lines.append(self.prompt_context)
        return "\n".join(line for line in lines if line).strip()


@dataclass(slots=True)
class ControllerSession:
    """Mutable session used internally by CognitiveController."""

    controller_input: ControllerInput
    config: CognitiveControllerConfig
    state: CognitiveState | None = None
    memory_store: WorkingMemoryStore | None = None
    artifacts: ControllerArtifacts = field(default_factory=ControllerArtifacts)
    steps: list[ControllerStep] = field(default_factory=list)
    status: str = "initialized"

    def add_step(
        self,
        phase: str | ControllerPhase,
        status: str,
        message: str,
        *,
        refs: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if len(self.steps) >= self.config.max_controller_steps:
            return
        step = ControllerStep.new(
            phase=phase,
            status=status,
            message=message,
            refs=refs,
            metadata=metadata,
        )
        self.steps.append(step)
        self.status = step.status

    def require_state(self) -> CognitiveState:
        if self.state is None:
            raise RuntimeError("Cognitive state has not been initialized.")
        return self.state

    def require_memory_store(self) -> WorkingMemoryStore:
        if self.memory_store is None:
            raise RuntimeError("Working memory store has not been initialized.")
        return self.memory_store


class CognitiveController:
    """Top-level orchestrator for the NCP modules.

    Typical usage from ``agent.py``:

    1. Call ``prepare(...)`` before planning or tool execution to obtain a
       compact cognitive context.
    2. Use the returned ``prompt_context`` and ``state`` to drive the existing
       planner/router/action path.
    3. Call ``evaluate_action(...)`` with the candidate action or candidate text.
    4. Optionally call ``record_episode(...)`` after the run completes.
    """

    def __init__(
        self,
        *,
        config: CognitiveControllerConfig | None = None,
        episodic_memory: EpisodicMemoryStore | None = None,
        hooks: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        self.config = (config or CognitiveControllerConfig()).normalized()
        self.attention_gate = AttentionGate(self.config.attention)
        self.evidence_verifier = EvidenceVerifier(self.config.evidence)
        self.uncertainty_estimator = UncertaintyEstimator(self.config.uncertainty)
        self.metacognitive_controller = MetacognitiveController(self.config.metacognition)
        self.episodic_memory = episodic_memory or build_episodic_memory(config=self.config.episodic_memory, autoload=bool(self.config.episodic_memory.persist_path))
        self.hooks = dict(hooks or {})

    def run(self, controller_input: ControllerInput | Mapping[str, Any] | None = None, **kwargs: Any) -> ControllerOutput:
        """Run the full prepare -> assess -> finalize path."""

        session = self._new_session(controller_input, **kwargs)
        try:
            self._initialize(session)
            self._select_attention(session)
            self._build_working_memory(session)
            self._retrieve_episodic_lessons(session)
            self._verify_evidence(session)
            self._estimate_uncertainty(session)
            self._run_metacognition(session)
            self._record_decision(session)
            self._record_episode_if_enabled(session)
            return self._finalize(session)
        except Exception as exc:
            return self._fail(session, exc)

    def prepare(self, controller_input: ControllerInput | Mapping[str, Any] | None = None, **kwargs: Any) -> ControllerOutput:
        """Run the pre-planning cognitive path.

        This is the best integration point before existing AegisForge planning:
        it produces state, attention, working memory, episodic lessons, evidence
        bootstrap, uncertainty, and prompt context without forcing a final action.
        """

        session = self._new_session(controller_input, **kwargs)
        try:
            self._initialize(session)
            self._select_attention(session)
            self._build_working_memory(session)
            self._retrieve_episodic_lessons(session)
            self._verify_evidence(session)
            self._estimate_uncertainty(session)
            return self._finalize(session, forced_status="prepared")
        except Exception as exc:
            return self._fail(session, exc)

    def evaluate_action(
        self,
        state: CognitiveState,
        *,
        candidate_action: str | DecisionOption | CognitiveDecision | None = None,
        candidate_text: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        external_signals: Mapping[str, Any] | None = None,
    ) -> ControllerOutput:
        """Evaluate a planned/candidate action using evidence, uncertainty and metacognition."""

        controller_input = ControllerInput(
            task_text=state.task_theory.objective,
            metadata={**dict(state.task_theory.metadata), **dict(metadata or {})},
            candidate_action=candidate_action,
            candidate_text=candidate_text,
            external_signals=dict(external_signals or {}),
        )
        session = ControllerSession(controller_input=controller_input, config=self.config, state=state)
        session.memory_store = WorkingMemoryStore.from_state(state, config=self.config.working_memory)
        session.add_step(ControllerPhase.INITIALIZE, "awaiting_action", "Loaded existing CognitiveState for candidate action evaluation.", refs=[state.episode_id])
        try:
            self._verify_evidence(session)
            self._estimate_uncertainty(session)
            self._run_metacognition(session)
            self._record_decision(session)
            return self._finalize(session)
        except Exception as exc:
            return self._fail(session, exc)

    def record_episode(
        self,
        state: CognitiveState,
        *,
        outcome: EpisodeOutcome | None = None,
        extra_metadata: Mapping[str, Any] | None = None,
        persist: bool = True,
    ) -> EpisodeRecord:
        """Record generalized lessons from a state into episodic memory."""

        return self.episodic_memory.record_state(
            state,
            outcome=outcome,
            extra_metadata=extra_metadata,
            persist=persist,
        )

    def close(self) -> None:
        """Persist episodic memory if configured."""

        self.episodic_memory.save()

    def _new_session(self, controller_input: ControllerInput | Mapping[str, Any] | None = None, **kwargs: Any) -> ControllerSession:
        if controller_input is None:
            data = dict(kwargs)
        elif isinstance(controller_input, ControllerInput):
            data = controller_input.to_dict()
            # Preserve non-serializable runtime objects from the dataclass.
            data.update(
                {
                    "task_text": controller_input.task_text,
                    "metadata": controller_input.metadata,
                    "classification": controller_input.classification,
                    "route": controller_input.route,
                    "plan": controller_input.plan,
                    "budget_state": controller_input.budget_state,
                    "policy_context": controller_input.policy_context,
                    "prompt_context": controller_input.prompt_context,
                    "payload": controller_input.payload,
                    "memory": controller_input.memory,
                    "candidate_action": controller_input.candidate_action,
                    "candidate_text": controller_input.candidate_text,
                    "external_signals": controller_input.external_signals,
                    "desired_lesson_types": controller_input.desired_lesson_types,
                    "retrieval_tags": controller_input.retrieval_tags,
                    "outcome": controller_input.outcome,
                    "extra_metadata": controller_input.extra_metadata,
                }
            )
            data.update(kwargs)
        else:
            data = {**dict(controller_input), **kwargs}

        ci = ControllerInput(
            task_text=str(data.get("task_text") or data.get("task") or data.get("objective") or ""),
            metadata=_as_mapping(data.get("metadata")),
            classification=data.get("classification"),
            route=data.get("route"),
            plan=data.get("plan"),
            budget_state=data.get("budget_state"),
            policy_context=_as_mapping(data.get("policy_context")),
            prompt_context=_as_mapping(data.get("prompt_context")),
            payload=_as_mapping(data.get("payload")),
            memory=data.get("memory"),
            candidate_action=data.get("candidate_action"),
            candidate_text=data.get("candidate_text"),
            external_signals=_as_mapping(data.get("external_signals")),
            desired_lesson_types=tuple(_unique(str(item) for item in data.get("desired_lesson_types") or ())),
            retrieval_tags=tuple(_unique(str(item) for item in data.get("retrieval_tags") or ())),
            outcome=data.get("outcome") if isinstance(data.get("outcome"), EpisodeOutcome) else None,
            extra_metadata=_as_mapping(data.get("extra_metadata")),
        )
        return ControllerSession(controller_input=ci, config=self.config)

    def _initialize(self, session: ControllerSession) -> None:
        ci = session.controller_input
        state = new_cognitive_state(
            task_text=ci.task_text,
            metadata=ci.metadata,
            classification=ci.classification,
            route=ci.route,
            plan=ci.plan,
            payload=ci.payload,
            policy_context=ci.policy_context,
        )
        session.state = state
        session.add_step(
            ControllerPhase.INITIALIZE,
            "prepared",
            f"Initialized NCP cognitive state for {state.task_theory.domain}/{state.task_theory.scenario_name or state.task_theory.scenario_id or 'unknown'}.",
            refs=[state.episode_id],
            metadata={"task_theory": state.task_theory.to_dict(), "adapter": state.adapter_profile.to_dict()},
        )
        self._call_hook("on_initialize", session=session, state=state)

    def _select_attention(self, session: ControllerSession) -> None:
        ci = session.controller_input
        state = session.require_state()
        frame = self.attention_gate.select(
            task_text=ci.task_text,
            metadata=ci.metadata,
            classification=ci.classification,
            route=ci.route,
            plan=ci.plan,
            budget_state=ci.budget_state,
            policy_context=ci.policy_context,
            prompt_context=ci.prompt_context,
            memory=ci.memory,
        )
        state = state.with_attention_frame(frame)
        session.state = state
        session.artifacts = replace(session.artifacts, attention_frame=frame)
        session.add_step(
            ControllerPhase.ATTENTION,
            "prepared",
            f"Selected {len(frame.signals)} attention signals.",
            refs=[frame.task_digest],
            metadata={"attention": frame.to_dict()},
        )
        self._call_hook("on_attention", session=session, state=state, attention_frame=frame)

    def _build_working_memory(self, session: ControllerSession) -> None:
        state = session.require_state()
        frame = session.artifacts.attention_frame
        store = build_working_memory(
            state=state,
            attention_frame=frame,
            config=self.config.working_memory,
            turn_index=state.turn_index,
        )
        selection = store.query(
            MemoryQuery.from_task(
                task_text=state.task_theory.objective,
                tags=("policy_boundary", "needs_evidence", "tool_contract", "heldout_generalization"),
                max_items=min(self.config.working_memory.capacity, 24),
                max_chars=self.config.prompt_context_chars,
            )
        )
        state = store.update_state(state)
        session.state = state
        session.memory_store = store
        session.artifacts = replace(session.artifacts, memory_selection=selection)
        session.add_step(
            ControllerPhase.WORKING_MEMORY,
            "prepared",
            f"Built working memory with {len(store)} items; selected {len(selection.selected)} for context.",
            refs=[score.item.key for score in selection.selected[:8]],
            metadata={"stats": store.stats().to_dict(), "selection": selection.to_dict()},
        )
        self._call_hook("on_working_memory", session=session, state=state, memory_store=store, selection=selection)

    def _retrieve_episodic_lessons(self, session: ControllerSession) -> None:
        if not self.config.enable_episodic_retrieval:
            session.add_step(ControllerPhase.EPISODIC_RETRIEVAL, "prepared", "Episodic retrieval disabled.")
            return

        state = session.require_state()
        result = self.episodic_memory.retrieve_for_state(
            state,
            text=state.task_theory.objective,
            desired_lesson_types=session.controller_input.desired_lesson_types,
            tags=session.controller_input.retrieval_tags,
            max_lessons=self.config.episodic_memory.max_lessons_per_query,
        )
        state = self.episodic_memory.apply_lessons_to_state(state, retrieval=result)
        session.state = state

        if session.memory_store is None:
            session.memory_store = WorkingMemoryStore.from_state(state, config=self.config.working_memory)
        else:
            session.memory_store = WorkingMemoryStore.from_state(state, config=self.config.working_memory)

        selection = session.memory_store.query(
            MemoryQuery.from_task(
                task_text=state.task_theory.objective,
                tags=("episodic_lesson", "policy_boundary", "needs_evidence", "tool_contract"),
                max_items=min(self.config.working_memory.capacity, 28),
                max_chars=self.config.prompt_context_chars,
            )
        )
        session.artifacts = replace(session.artifacts, episodic_retrieval=result, memory_selection=selection)
        session.add_step(
            ControllerPhase.EPISODIC_RETRIEVAL,
            "prepared",
            f"Retrieved {len(result.lessons)} generalized episodic lessons.",
            refs=[lesson.lesson.lesson_id for lesson in result.lessons[:8]],
            metadata={"retrieval": result.to_dict()},
        )
        self._call_hook("on_episodic_retrieval", session=session, state=state, retrieval=result)

    def _verify_evidence(self, session: ControllerSession) -> None:
        if not self.config.enable_evidence_verification:
            session.add_step(ControllerPhase.EVIDENCE, "prepared", "Evidence verification disabled.")
            return

        state = session.require_state()
        ci = session.controller_input
        bundle = self.evidence_verifier.build_bundle(
            state=state,
            candidate_text=ci.candidate_text or _candidate_text(ci.candidate_action) or None,
            metadata=ci.extra_metadata,
        )
        state = self.evidence_verifier.update_state_with_bundle(state, bundle)
        session.state = state
        session.artifacts = replace(session.artifacts, evidence_bundle=bundle)
        if session.memory_store is not None:
            session.memory_store = WorkingMemoryStore.from_state(state, config=self.config.working_memory)
        session.add_step(
            ControllerPhase.EVIDENCE,
            "needs_evidence" if bundle.grounding_decision in {"verify_more", "escalate_conflict"} else "prepared",
            f"Evidence bundle decision={bundle.grounding_decision}; supported_ratio={bundle.supported_ratio:.2f}.",
            refs=[bundle.bundle_id],
            metadata={"evidence_bundle": bundle.to_dict()},
        )
        self._call_hook("on_evidence", session=session, state=state, evidence_bundle=bundle)

    def _estimate_uncertainty(self, session: ControllerSession) -> None:
        if not self.config.enable_uncertainty:
            session.add_step(ControllerPhase.UNCERTAINTY, "prepared", "Uncertainty estimation disabled.")
            return

        state = session.require_state()
        ci = session.controller_input
        assessment = self.uncertainty_estimator.assess_state(
            state,
            candidate_action=ci.candidate_action,
            external_signals=ci.external_signals,
        )
        state = state.update_uncertainty(assessment.to_estimate())
        state = state.append_trace(
            phase="uncertainty",
            message=assessment.compact_context(),
            severity="warning" if assessment.uncertainty_level in {"high", "critical"} else "info",
            refs=[assessment.assessment_id],
            metadata={"assessment": assessment.to_dict()},
        ).refresh_scorecard()
        session.state = state
        session.artifacts = replace(session.artifacts, uncertainty=assessment)

        if session.memory_store is not None:
            session.memory_store = WorkingMemoryStore.from_state(state, config=self.config.working_memory)

        status = "needs_review" if assessment.recommended_action == "pause_or_request_review" else "blocked" if assessment.recommended_action == "block_or_safe_alternative" else "needs_evidence" if assessment.recommended_action == "gather_more_evidence" else "prepared"
        session.add_step(
            ControllerPhase.UNCERTAINTY,
            status,
            f"Uncertainty={assessment.uncertainty_level}; risk={assessment.risk_level}; action={assessment.recommended_action}.",
            refs=[assessment.assessment_id],
            metadata={"uncertainty": assessment.to_dict()},
        )
        self._call_hook("on_uncertainty", session=session, state=state, uncertainty=assessment)

    def _run_metacognition(self, session: ControllerSession) -> None:
        if not self.config.enable_metacognition:
            session.add_step(ControllerPhase.METACOGNITION, "prepared", "Metacognition disabled.")
            return

        state = session.require_state()
        ci = session.controller_input
        report = self.metacognitive_controller.evaluate(
            state,
            candidate_action=ci.candidate_action,
            candidate_text=ci.candidate_text,
            evidence_bundle=session.artifacts.evidence_bundle,
            external_signals=ci.external_signals,
        )

        state = state.update_uncertainty(report.uncertainty.to_estimate())
        for item in metacognitive_memory_items(report, turn_index=state.turn_index):
            state = state.remember(item)
        state = state.append_trace(
            phase="metacognition",
            message=report.compact_context(),
            severity="critical" if report.overall_status == "blocked" else "warning" if report.overall_status != "ready" else "info",
            refs=[report.report_id, report.uncertainty.assessment_id],
            metadata={"report": report.to_dict()},
        ).refresh_scorecard()

        session.state = state
        session.artifacts = replace(session.artifacts, metacognition=report, uncertainty=report.uncertainty)
        if session.memory_store is not None:
            session.memory_store = WorkingMemoryStore.from_state(state, config=self.config.working_memory)

        status = {
            "ready": "ready",
            "caution": "prepared",
            "needs_evidence": "needs_evidence",
            "needs_review": "needs_review",
            "blocked": "blocked",
        }.get(report.overall_status, "prepared")
        session.add_step(
            ControllerPhase.METACOGNITION,
            status,
            report.summary,
            refs=[report.report_id],
            metadata={"metacognition": report.to_dict()},
        )
        self._call_hook("on_metacognition", session=session, state=state, report=report)

    def _record_decision(self, session: ControllerSession) -> None:
        state = session.require_state()
        report = session.artifacts.metacognition
        uncertainty = session.artifacts.uncertainty
        ci = session.controller_input

        if report is not None:
            decision = report.to_decision(
                selected_action=self._action_from_report(report, ci),
                turn_index=state.turn_index,
            )
        elif uncertainty is not None:
            action = uncertainty.recommended_action
            safety_status = "blocked" if action == "block_or_safe_alternative" else "needs_review" if action in {"pause_or_request_review", "gather_more_evidence"} else "safe"
            decision = CognitiveDecision.select(
                selected_action=action,
                rationale=uncertainty.compact_context(),
                options=build_decision_options_from_assessment(uncertainty),
                confidence=uncertainty.confidence,
                expected_utility=uncertainty.confidence,
                risk=uncertainty.risk_score,
                safety_status=safety_status,
                evidence_refs=[gap.gap_id for gap in uncertainty.evidence_gaps],
                turn_index=state.turn_index,
                metadata={"source": "uncertainty"},
            )
        else:
            decision = CognitiveDecision.select(
                selected_action=str(ci.candidate_action or self.config.safe_default_action),
                rationale="No uncertainty/metacognition artifact available; using safe default.",
                confidence=0.35,
                risk=0.55,
                safety_status="needs_review",
                turn_index=state.turn_index,
                metadata={"source": "controller_fallback"},
            )

        state = state.decide(decision)
        session.state = state
        session.artifacts = replace(session.artifacts, decision=decision)
        session.add_step(
            ControllerPhase.DECISION,
            "blocked" if decision.safety_status == "blocked" else "needs_review" if decision.safety_status == "needs_review" else "ready",
            decision.compact(),
            refs=[decision.decision_id],
            metadata={"decision": decision.to_dict()},
        )
        self._call_hook("on_decision", session=session, state=state, decision=decision)

    def _record_episode_if_enabled(self, session: ControllerSession) -> None:
        if not self.config.record_episodes:
            return
        state = session.require_state()
        record = self.episodic_memory.record_state(
            state,
            outcome=session.controller_input.outcome,
            extra_metadata=session.controller_input.extra_metadata,
            persist=True,
        )
        session.artifacts = replace(session.artifacts, recorded_episode=record)
        session.add_step(
            ControllerPhase.RECORD_EPISODE,
            "completed",
            f"Recorded generalized episodic memory record {record.episode_id}.",
            refs=[record.episode_id],
            metadata={"episode": record.to_dict()},
        )
        self._call_hook("on_record_episode", session=session, state=state, record=record)

    def _finalize(self, session: ControllerSession, *, forced_status: str | None = None) -> ControllerOutput:
        state = session.require_state()
        selection = self._refresh_prompt_selection(session)
        prompt_context = self._build_prompt_context(session, selection)
        recommended_action = self._recommended_action(session)
        status = forced_status or self._status_from_artifacts(session, recommended_action)

        if self.config.fail_closed_on_policy_block and status == "blocked":
            recommended_action = self.config.safe_default_action

        run_id = _stable_id(
            "ncp_run",
            state.episode_id,
            status,
            recommended_action,
            [step.phase + ":" + step.status for step in session.steps],
        )[:24]
        summary = self._controller_summary(session, status=status, recommended_action=recommended_action)

        final_artifacts = replace(session.artifacts, memory_selection=selection)
        output = ControllerOutput(
            run_id=run_id,
            version=NCP_CONTROLLER_VERSION,
            status=status,
            recommended_action=recommended_action,
            state=state.refresh_scorecard(),
            artifacts=final_artifacts,
            steps=tuple(session.steps),
            prompt_context=prompt_context if self.config.include_prompt_context_in_output else "",
            controller_summary=summary,
            metadata={
                "controller_status": status,
                "step_count": len(session.steps),
                "state_validation_errors": state.validate(),
                "config": _config_summary(self.config),
            },
        )
        self._call_hook("on_finalize", session=session, output=output)
        return output

    def _fail(self, session: ControllerSession, exc: Exception) -> ControllerOutput:
        message = f"NCP controller failed safely: {type(exc).__name__}: {exc}"
        if session.state is None:
            fallback_state = new_cognitive_state(
                task_text=session.controller_input.task_text or "Controller failed before state initialization.",
                metadata=session.controller_input.metadata,
                policy_context=session.controller_input.policy_context,
            )
            session.state = fallback_state
        state = session.require_state().append_trace(
            phase="controller_failure",
            message=message,
            severity="error",
            metadata={"error_type": type(exc).__name__},
        ).refresh_scorecard()
        session.state = state
        session.add_step(ControllerPhase.FINALIZE, "failed", message, metadata={"error": type(exc).__name__})
        decision = CognitiveDecision.select(
            selected_action=self.config.safe_default_action,
            rationale=message,
            confidence=0.10,
            risk=0.80,
            safety_status="needs_review",
            turn_index=state.turn_index,
            metadata={"source": "controller_exception"},
        )
        state = state.decide(decision)
        session.state = state
        session.artifacts = replace(session.artifacts, decision=decision)
        return ControllerOutput(
            run_id=_stable_id("ncp_run_failed", state.episode_id, message)[:24],
            version=NCP_CONTROLLER_VERSION,
            status="failed",
            recommended_action=self.config.safe_default_action,
            state=state,
            artifacts=session.artifacts,
            steps=tuple(session.steps),
            prompt_context="",
            controller_summary=message,
            metadata={"error": type(exc).__name__, "controller_failed_closed": True},
        )

    def _refresh_prompt_selection(self, session: ControllerSession) -> MemorySelection | None:
        state = session.require_state()
        store = session.memory_store or WorkingMemoryStore.from_state(state, config=self.config.working_memory)
        selection = store.query(
            MemoryQuery.from_task(
                task_text=state.task_theory.objective,
                tags=(
                    "policy_boundary",
                    "hardcoding_guard",
                    "needs_evidence",
                    "tool_contract",
                    "adapter_contract",
                    "heldout_generalization",
                    "metacognition",
                    "episodic_lesson",
                ),
                max_items=min(self.config.working_memory.capacity, 32),
                max_chars=self.config.final_context_chars,
            )
        )
        session.memory_store = store
        session.artifacts = replace(session.artifacts, memory_selection=selection)
        return selection

    def _build_prompt_context(self, session: ControllerSession, selection: MemorySelection | None) -> str:
        state = session.require_state()
        lines = [
            "AegisForge NCP Context",
            state.compact_context(max_lines=18),
        ]
        if selection is not None:
            lines.append(format_memory_prompt(selection))
        if session.artifacts.episodic_retrieval is not None:
            lines.append(session.artifacts.episodic_retrieval.compact_context())
        if session.artifacts.evidence_bundle is not None:
            lines.append(session.artifacts.evidence_bundle.compact_context())
        if session.artifacts.uncertainty is not None:
            lines.append(session.artifacts.uncertainty.compact_context())
        if session.artifacts.metacognition is not None:
            lines.append(session.artifacts.metacognition.compact_context())
        return _clip("\n\n".join(part for part in lines if part), self.config.final_context_chars)

    def _recommended_action(self, session: ControllerSession) -> str:
        report = session.artifacts.metacognition
        if report is not None:
            return report.recommended_action
        uncertainty = session.artifacts.uncertainty
        if uncertainty is not None:
            return uncertainty.recommended_action
        decision = session.artifacts.decision
        if decision is not None:
            return decision.selected_action
        return "proceed_with_caution"

    def _status_from_artifacts(self, session: ControllerSession, recommended_action: str) -> str:
        report = session.artifacts.metacognition
        if report is not None:
            return {
                "ready": "ready",
                "caution": "ready" if recommended_action == "proceed_with_caution" else "prepared",
                "needs_evidence": "needs_evidence",
                "needs_review": "needs_review",
                "blocked": "blocked",
            }.get(report.overall_status, "prepared")

        uncertainty = session.artifacts.uncertainty
        if uncertainty is not None:
            if uncertainty.recommended_action == "block_or_safe_alternative":
                return "blocked"
            if uncertainty.recommended_action == "pause_or_request_review":
                return "needs_review"
            if uncertainty.recommended_action == "gather_more_evidence":
                return "needs_evidence"
            return "ready"

        return "prepared"

    def _action_from_report(self, report: MetacognitionReport, ci: ControllerInput) -> str:
        if report.overall_status == "blocked":
            return self.config.safe_default_action
        if report.recommended_action in {"gather_more_evidence", "request_manual_review", "run_self_check", "proceed_with_caution"}:
            return report.recommended_action
        if ci.candidate_action is not None:
            return _candidate_action_name(ci.candidate_action)
        return report.recommended_action or "proceed"

    @staticmethod
    def _controller_summary(session: ControllerSession, *, status: str, recommended_action: str) -> str:
        state = session.require_state()
        pieces = [
            f"NCP status={status}",
            f"recommended_action={recommended_action}",
            f"domain={state.task_theory.domain}",
            f"scenario={state.task_theory.scenario_name or state.task_theory.scenario_id or 'unknown'}",
            f"scorecard={state.scorecard.aggregate():.3f}",
        ]
        if session.artifacts.uncertainty is not None:
            pieces.append(f"uncertainty={session.artifacts.uncertainty.uncertainty_level}")
            pieces.append(f"risk={session.artifacts.uncertainty.risk_level}")
        if session.artifacts.metacognition is not None:
            pieces.append(f"meta={session.artifacts.metacognition.overall_status}")
            pieces.append(f"readiness={session.artifacts.metacognition.readiness_score:.2f}")
        return "; ".join(pieces) + "."

    def _call_hook(self, name: str, **kwargs: Any) -> None:
        hook = self.hooks.get(name)
        if hook is None:
            return
        try:
            hook(**kwargs)
        except Exception:
            # Hooks must not compromise controller fail-closed behavior.
            return


def run_cognitive_controller(
    controller_input: ControllerInput | Mapping[str, Any] | None = None,
    *,
    config: CognitiveControllerConfig | None = None,
    episodic_memory: EpisodicMemoryStore | None = None,
    **kwargs: Any,
) -> ControllerOutput:
    """Convenience wrapper for one-shot controller execution."""

    return CognitiveController(config=config, episodic_memory=episodic_memory).run(controller_input, **kwargs)


def prepare_cognitive_context(
    controller_input: ControllerInput | Mapping[str, Any] | None = None,
    *,
    config: CognitiveControllerConfig | None = None,
    episodic_memory: EpisodicMemoryStore | None = None,
    **kwargs: Any,
) -> ControllerOutput:
    """Convenience wrapper for pre-planning NCP context preparation."""

    return CognitiveController(config=config, episodic_memory=episodic_memory).prepare(controller_input, **kwargs)


def evaluate_candidate_action(
    state: CognitiveState,
    *,
    candidate_action: str | DecisionOption | CognitiveDecision | None = None,
    candidate_text: str | None = None,
    config: CognitiveControllerConfig | None = None,
    episodic_memory: EpisodicMemoryStore | None = None,
    metadata: Mapping[str, Any] | None = None,
    external_signals: Mapping[str, Any] | None = None,
) -> ControllerOutput:
    """Convenience wrapper for post-planning action evaluation."""

    return CognitiveController(config=config, episodic_memory=episodic_memory).evaluate_action(
        state,
        candidate_action=candidate_action,
        candidate_text=candidate_text,
        metadata=metadata,
        external_signals=external_signals,
    )


def controller_output_to_response_payload(output: ControllerOutput) -> dict[str, Any]:
    """Build a small response payload suitable for agent.py or API responses."""

    decision = output.artifacts.decision
    return {
        "ncp_version": output.version,
        "run_id": output.run_id,
        "status": output.status,
        "recommended_action": output.recommended_action,
        "safe_to_finalize": output.safe_to_finalize,
        "decision": decision.to_dict() if decision else None,
        "scorecard": output.state.scorecard.to_dict(),
        "uncertainty": output.artifacts.uncertainty.to_dict() if output.artifacts.uncertainty else None,
        "metacognition": output.artifacts.metacognition.to_dict() if output.artifacts.metacognition else None,
        "controller_summary": output.controller_summary,
        "prompt_context": output.prompt_context,
    }


def _candidate_text(candidate: str | DecisionOption | CognitiveDecision | None) -> str:
    if candidate is None:
        return ""
    if isinstance(candidate, str):
        return _sanitize(candidate)
    if isinstance(candidate, DecisionOption):
        return _sanitize(f"{candidate.action}. {candidate.rationale}")
    if isinstance(candidate, CognitiveDecision):
        return _sanitize(f"{candidate.selected_action}. {candidate.rationale}")
    data = _as_mapping(candidate)
    return _sanitize(str(data.get("action") or data.get("selected_action") or data.get("rationale") or data.get("message") or ""))


def _candidate_action_name(candidate: str | DecisionOption | CognitiveDecision | None) -> str:
    if candidate is None:
        return "proceed"
    if isinstance(candidate, str):
        return _clip(_sanitize(candidate), 240)
    if isinstance(candidate, DecisionOption):
        return _clip(_sanitize(candidate.action), 240)
    if isinstance(candidate, CognitiveDecision):
        return _clip(_sanitize(candidate.selected_action), 240)
    data = _as_mapping(candidate)
    return _clip(_sanitize(str(data.get("action") or data.get("selected_action") or "proceed")), 240)


def _config_summary(config: CognitiveControllerConfig) -> dict[str, Any]:
    return {
        "enable_episodic_retrieval": config.enable_episodic_retrieval,
        "enable_evidence_verification": config.enable_evidence_verification,
        "enable_uncertainty": config.enable_uncertainty,
        "enable_metacognition": config.enable_metacognition,
        "record_episodes": config.record_episodes,
        "prompt_context_chars": config.prompt_context_chars,
        "final_context_chars": config.final_context_chars,
        "fail_closed_on_policy_block": config.fail_closed_on_policy_block,
    }


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
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen or text == "unknown":
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


__all__ = [
    "NCP_CONTROLLER_VERSION",
    "CognitiveController",
    "CognitiveControllerConfig",
    "ControllerArtifacts",
    "ControllerInput",
    "ControllerOutput",
    "ControllerPhase",
    "ControllerSession",
    "ControllerStep",
    "controller_output_to_response_payload",
    "evaluate_candidate_action",
    "prepare_cognitive_context",
    "run_cognitive_controller",
]
