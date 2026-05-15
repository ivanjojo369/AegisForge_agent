from __future__ import annotations

"""State contracts for AegisForge NCP — Neuro-Cognitive Purple Core.

This module defines the durable data structures used by the NCP controller.
It is intentionally model-agnostic and dependency-free: the objects here can be
built from AegisForge's current strategy outputs, OpenEnv/τ² payloads, scenario
catalog metadata, or future controller modules.

The goal is not to simulate a brain. The goal is to make the agent's cognitive
state explicit, inspectable, serializable, and safe to pass through planning,
evidence verification, uncertainty handling, self-checking, telemetry, and tests.
"""

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence


class SupportsAsDict(Protocol):
    def as_dict(self) -> dict[str, Any]: ...


NCP_STATE_VERSION = "0.1.0"

DEFAULT_ASSESSMENT_MODE = "purple_benchmark"
DEFAULT_SCENARIO_FAMILY = "agentbeats_sprint4"
DEFAULT_BENCHMARK = "AgentX-AgentBeats Phase 2 Sprint 4"

MAX_COMPACT_FIELD_CHARS = 520
MAX_CONTEXT_LINES = 60

VALID_UNCERTAINTY_LEVELS = ("low", "medium", "high", "critical")
VALID_EVIDENCE_STATUS = ("unknown", "supported", "unsupported", "conflicting", "not_applicable")
VALID_SAFETY_STATUS = ("safe", "needs_review", "blocked", "unsafe", "unknown")
VALID_EVENT_SEVERITIES = ("debug", "info", "warning", "error", "critical")


SPRINT4_SCENARIOS_BY_DOMAIN: dict[str, str] = {
    "business_process": "SaleForceOneSpy",
    "game": "WikiWiper",
    "tau2": "TicketTwister",
    "multi_agent": "BidBot",
    "finance": "TaxWizTrap",
    "computer_use": "LnkLifter",
    "agent_safety": "InventoryInject",
    "coding": "DevContainerDoom",
    "cybersecurity": "StaticShipScam",
    "research": "WhistleBlowerWreck",
    "healthcare": "DocuDoctor",
    "web": "SearchGlitch",
    "agent_security": "GymJailbreak",
    "software_testing": "CodeReviewRuse",
    "defi": "CryptoCrash",
    "legal_domain": "LawFirmLeak",
}

SPRINT4_DOMAIN_CATEGORIES: dict[str, str] = {
    "business_process": "business_process",
    "game": "game",
    "tau2": "general_agent",
    "multi_agent": "general_agent",
    "finance": "finance",
    "computer_use": "computer_use",
    "agent_safety": "agent_safety",
    "coding": "coding",
    "cybersecurity": "cybersecurity",
    "research": "research",
    "healthcare": "healthcare",
    "web": "web",
    "agent_security": "agent_security",
    "software_testing": "software_testing",
    "defi": "defi",
    "legal_domain": "legal_domain",
}


_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|private[_-]?key|session|cookie|auth)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|ghp_[a-z0-9_]{16,}|xox[baprs]-[a-z0-9-]{10,}|"
    r"bearer\s+[a-z0-9._-]{16,}|api[_-]?key\s*[:=]\s*[a-z0-9._-]{12,})"
)

_ID_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class TaskTheory:
    """The agent's current theory of the task.

    This object represents the controller's explicit understanding of what it is
    trying to do. It deliberately separates objective, constraints, evidence
    requirements, and benchmark metadata so downstream modules can reason about
    them without parsing free-form text again.
    """

    objective: str
    domain: str = "unknown"
    track: str = "unknown"
    task_type: str = "general"
    scenario_id: str | None = None
    scenario_name: str | None = None
    scenario_family: str = DEFAULT_SCENARIO_FAMILY
    assessment_mode: str = DEFAULT_ASSESSMENT_MODE
    benchmark: str = DEFAULT_BENCHMARK
    selected_opponent: str | None = None
    adapter: str | None = None
    source_url: str | None = None
    assumptions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    available_actions: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    expected_failure_modes: tuple[str, ...] = ()
    generalization_notes: tuple[str, ...] = ()
    confidence: float = 0.55
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_sources(
        cls,
        *,
        task_text: str,
        metadata: Mapping[str, Any] | None = None,
        classification: Any = None,
        route: Any = None,
        plan: Any = None,
        payload: Mapping[str, Any] | None = None,
    ) -> "TaskTheory":
        """Build a task theory from mixed AegisForge runtime objects."""

        meta = _as_mapping(metadata)
        cls_map = _as_mapping(classification)
        route_map = _as_mapping(route)
        plan_map = _as_mapping(plan)
        payload_map = _as_mapping(payload)

        scenario = payload_map.get("scenario")
        scenario_map = _as_mapping(scenario) if isinstance(scenario, Mapping) else {}

        task = payload_map.get("task")
        task_map = _as_mapping(task) if isinstance(task, Mapping) else {}
        task_meta = _as_mapping(task_map.get("metadata"))

        objective = _first_text(
            plan_map.get("goal"),
            payload_map.get("user_goal"),
            task_map.get("user_goal"),
            task_map.get("title"),
            meta.get("objective"),
            task_text,
            default="Process the current benchmark task safely and accurately.",
        )

        domain = _normalize_identifier(
            _first_present(
                meta,
                task_meta,
                payload_map,
                cls_map,
                route_map,
                keys=("domain", "track", "track_hint", "adapter", "adapter_name"),
                default="unknown",
            )
        )
        scenario_id = _first_text(
            meta.get("scenario_id"),
            meta.get("scenario_name"),
            scenario_map.get("scenario_id"),
            scenario_map.get("id"),
            payload_map.get("scenario_id"),
            payload_map.get("scenario"),
            task_meta.get("scenario_id"),
            default=None,
        )
        scenario_name = _first_text(
            meta.get("scenario_name"),
            scenario_map.get("name"),
            scenario_map.get("scenario_name"),
            scenario_id,
            default=None,
        )

        constraints = _coerce_string_tuple(
            meta.get("constraints"),
            task_map.get("constraints"),
            payload_map.get("constraints"),
            plan_map.get("constraints"),
        )
        success_criteria = _coerce_string_tuple(
            meta.get("success_criteria"),
            task_map.get("success_criteria"),
            payload_map.get("success_criteria"),
            plan_map.get("success_criteria"),
        )
        required_tools = _coerce_string_tuple(
            meta.get("required_tools"),
            task_map.get("required_tools"),
            payload_map.get("required_tools"),
            payload_map.get("tools"),
            plan_map.get("requires_tools"),
        )
        available_actions = _coerce_string_tuple(
            meta.get("available_actions"),
            payload_map.get("available_actions"),
            payload_map.get("actions"),
        )

        expected_failure = _coerce_string_tuple(
            meta.get("expected_failure_modes"),
            meta.get("expected_failure_mode"),
            task_meta.get("expected_failure_mode"),
            scenario_map.get("failure_modes"),
            payload_map.get("failure_modes"),
        )
        evidence_requirements = _coerce_string_tuple(
            meta.get("evidence_requirements"),
            scenario_map.get("evidence_fields"),
            scenario_map.get("recommended_tasks"),
            "ground every material decision in visible task state or tool evidence",
        )

        confidence = _bounded_float(
            _first_present(cls_map, route_map, plan_map, keys=("confidence", "score"), default=0.55),
            default=0.55,
        )

        return cls(
            objective=_clip(_sanitize(objective), 1_200),
            domain=domain,
            track=_normalize_identifier(
                _first_present(cls_map, meta, route_map, keys=("track_guess", "track", "track_hint"), default=domain)
            ),
            task_type=_normalize_identifier(
                _first_present(cls_map, meta, task_meta, keys=("task_type", "task_family", "pressure_type"), default="general")
            ),
            scenario_id=scenario_id,
            scenario_name=scenario_name,
            scenario_family=str(meta.get("scenario_family") or scenario_map.get("scenario_family") or DEFAULT_SCENARIO_FAMILY),
            assessment_mode=str(meta.get("assessment_mode") or DEFAULT_ASSESSMENT_MODE),
            benchmark=str(meta.get("benchmark") or DEFAULT_BENCHMARK),
            selected_opponent=_first_text(meta.get("selected_opponent"), meta.get("opponent_profile"), default=None),
            adapter=_first_text(route_map.get("adapter_name"), route_map.get("adapter"), meta.get("adapter"), default=None),
            source_url=_first_text(meta.get("source_url"), scenario_map.get("source_url"), default=None),
            assumptions=_coerce_string_tuple(meta.get("assumptions"), plan_map.get("assumptions")),
            constraints=constraints,
            success_criteria=success_criteria,
            required_tools=required_tools,
            available_actions=available_actions,
            evidence_requirements=evidence_requirements,
            expected_failure_modes=expected_failure,
            generalization_notes=_coerce_string_tuple(
                meta.get("generalization_notes"),
                scenario_map.get("generalization_pattern"),
                "avoid task-specific answer lookup tables",
                "expect held-out variations",
            ),
            confidence=confidence,
            metadata=_redact_mapping(
                {
                    "payload_task_id": task_map.get("task_id") or payload_map.get("task_id"),
                    "difficulty": meta.get("difficulty") or task_meta.get("difficulty"),
                    "priority": meta.get("priority") or task_meta.get("priority"),
                    "family": meta.get("family") or task_meta.get("task_family"),
                    "raw_domain": meta.get("domain") or payload_map.get("domain"),
                }
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        parts = [
            f"objective={_clip(self.objective, 180)}",
            f"domain={self.domain}",
            f"track={self.track}",
            f"scenario={self.scenario_name or self.scenario_id or 'unknown'}",
            f"mode={self.assessment_mode}",
        ]
        if self.required_tools:
            parts.append("tools=" + ",".join(self.required_tools[:6]))
        if self.constraints:
            parts.append("constraints=" + "; ".join(self.constraints[:3]))
        return " | ".join(parts)


@dataclass(frozen=True, slots=True)
class DomainAdapterProfile:
    """Runtime-facing profile of the selected benchmark/domain adapter."""

    domain: str
    adapter: str = "unknown"
    track: str = "unknown"
    scenario_id: str | None = None
    category: str = "general"
    tool_mode: str | None = None
    prompt_profile: str | None = None
    required_tools: tuple[str, ...] = ()
    available_actions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    hazards: tuple[str, ...] = ()
    source_url: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_task_theory(
        cls,
        task_theory: TaskTheory,
        *,
        route: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "DomainAdapterProfile":
        meta = _as_mapping(metadata)
        route_map = _as_mapping(route)
        domain = _normalize_identifier(task_theory.domain)
        return cls(
            domain=domain,
            adapter=str(route_map.get("adapter_name") or route_map.get("adapter") or task_theory.adapter or domain),
            track=_normalize_identifier(str(route_map.get("track") or task_theory.track or domain)),
            scenario_id=task_theory.scenario_id or task_theory.scenario_name,
            category=str(meta.get("category") or SPRINT4_DOMAIN_CATEGORIES.get(domain, domain or "general")),
            tool_mode=_first_text(route_map.get("tool_mode"), meta.get("tool_mode"), default=None),
            prompt_profile=_first_text(route_map.get("prompt_profile"), meta.get("prompt_profile"), default=None),
            required_tools=task_theory.required_tools,
            available_actions=task_theory.available_actions,
            capabilities=_coerce_string_tuple(route_map.get("capabilities"), meta.get("capabilities")),
            hazards=_coerce_string_tuple(route_map.get("hazards"), meta.get("hazards")),
            source_url=task_theory.source_url,
            metadata=_redact_mapping(
                {
                    "selected_opponent": task_theory.selected_opponent,
                    "benchmark": task_theory.benchmark,
                    "scenario_family": task_theory.scenario_family,
                }
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class PolicyBoundary:
    """Safety, fair-play, and benchmark integrity boundary for the episode."""

    assessment_mode: str = DEFAULT_ASSESSMENT_MODE
    scenario_family: str = DEFAULT_SCENARIO_FAMILY
    benchmark: str = DEFAULT_BENCHMARK
    allowed_scope: tuple[str, ...] = ("benchmark_provided_context_only", "controlled_environment_only")
    denied_behaviors: tuple[str, ...] = (
        "hardcoded_answers",
        "task_specific_lookup_tables",
        "benchmark_platform_exploitation",
        "third_party_targeting",
        "secret_extraction",
        "unsafe_real_world_actions",
    )
    required_checks: tuple[str, ...] = (
        "preserve_genuine_reasoning",
        "ground_material_claims",
        "check_policy_boundary",
        "record_trace",
        "prefer_safe_completion_or_review",
    )
    fair_play_notes: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_sources(
        cls,
        *,
        metadata: Mapping[str, Any] | None = None,
        policy_context: Mapping[str, Any] | None = None,
    ) -> "PolicyBoundary":
        meta = _as_mapping(metadata)
        policy = _as_mapping(policy_context)
        return cls(
            assessment_mode=str(meta.get("assessment_mode") or policy.get("assessment_mode") or DEFAULT_ASSESSMENT_MODE),
            scenario_family=str(meta.get("scenario_family") or policy.get("scenario_family") or DEFAULT_SCENARIO_FAMILY),
            benchmark=str(meta.get("benchmark") or policy.get("benchmark") or DEFAULT_BENCHMARK),
            allowed_scope=_coerce_string_tuple(
                policy.get("allowed_scope"),
                policy.get("mode"),
                "benchmark_provided_context_only",
                "controlled_environment_only",
            ),
            denied_behaviors=_coerce_string_tuple(
                policy.get("denied_behavior"),
                policy.get("denied_behaviors"),
                cls().denied_behaviors,
            ),
            required_checks=_coerce_string_tuple(policy.get("required_checks"), cls().required_checks),
            fair_play_notes=_coerce_string_tuple(policy.get("fair_play_notes"), meta.get("fair_play_notes")),
            metadata=_redact_mapping(
                {
                    "policy_type": policy.get("policy_type"),
                    "expected_outcome": policy.get("expected_outcome"),
                    "canary": policy.get("canary"),
                    "source_url": meta.get("source_url"),
                }
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class WorkingMemoryItem:
    """One compact active-memory item for the current episode."""

    key: str
    content: str
    source: str = "unknown"
    salience: float = 0.5
    turn_index: int = 0
    tags: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    ttl_turns: int | None = None
    locked: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_signal(cls, signal: Any, *, turn_index: int = 0) -> "WorkingMemoryItem":
        data = _as_mapping(signal)
        source = str(data.get("source") or "attention")
        kind = str(data.get("kind") or "signal")
        content = str(data.get("content") or "")
        key = _stable_id("wm", source, kind, content)[:20]
        return cls(
            key=key,
            content=_clip(_sanitize(content), MAX_COMPACT_FIELD_CHARS),
            source=source,
            salience=_bounded_float(data.get("score"), default=0.5),
            turn_index=int(turn_index),
            tags=_coerce_string_tuple(data.get("tags")),
            evidence_refs=_coerce_string_tuple(data.get("evidence")),
            ttl_turns=None,
            locked=source in {"policy", "metadata"},
            metadata=_redact_mapping({"kind": kind, **_as_mapping(data.get("metadata"))}),
        )

    def is_expired(self, current_turn: int) -> bool:
        if self.locked or self.ttl_turns is None:
            return False
        return int(current_turn) - int(self.turn_index) > int(self.ttl_turns)

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        tag_text = f" [{', '.join(self.tags[:4])}]" if self.tags else ""
        return f"{self.source}:{self.key}{tag_text} {self.content}"


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Evidence item observed or inferred during an episode."""

    evidence_id: str
    source: str
    kind: str
    claim: str
    status: str = "unknown"
    confidence: float = 0.5
    support: str | None = None
    verifier: str | None = None
    turn_index: int = 0
    related_memory_keys: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        source: str,
        kind: str,
        claim: str,
        status: str = "unknown",
        confidence: float = 0.5,
        support: str | None = None,
        verifier: str | None = None,
        turn_index: int = 0,
        related_memory_keys: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "EvidenceRecord":
        evidence_id = _stable_id("ev", source, kind, claim, support or "")[:22]
        return cls(
            evidence_id=evidence_id,
            source=str(source or "unknown"),
            kind=str(kind or "observation"),
            claim=_clip(_sanitize(claim), 1_000),
            status=_validate_choice(status, VALID_EVIDENCE_STATUS, "unknown"),
            confidence=_bounded_float(confidence, default=0.5),
            support=_clip(_sanitize(support), 1_000) if support else None,
            verifier=verifier,
            turn_index=int(turn_index),
            related_memory_keys=tuple(_unique(str(item) for item in related_memory_keys)),
            metadata=_redact_mapping(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        support = f" :: {self.support}" if self.support else ""
        return f"{self.evidence_id} {self.status}/{self.confidence:.2f}: {self.claim}{support}"


@dataclass(frozen=True, slots=True)
class UncertaintyEstimate:
    """Explicit uncertainty state for the current episode."""

    confidence: float = 0.55
    level: str = "medium"
    evidence_gap: str | None = None
    ambiguity_count: int = 0
    contradiction_count: int = 0
    risk_count: int = 0
    recommended_action: str = "proceed_with_caution"
    drivers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state(
        cls,
        *,
        working_memory: Sequence[WorkingMemoryItem] = (),
        evidence: Sequence[EvidenceRecord] = (),
        base_confidence: float = 0.55,
    ) -> "UncertaintyEstimate":
        tags = [tag for item in working_memory for tag in item.tags]
        ambiguity_count = sum(1 for tag in tags if str(tag).startswith("uncertainty:") or "ambiguous" in str(tag))
        risk_count = sum(1 for tag in tags if str(tag).startswith("risk:"))
        contradiction_count = sum(1 for record in evidence if record.status == "conflicting")
        unsupported_count = sum(1 for record in evidence if record.status == "unsupported")
        supported_count = sum(1 for record in evidence if record.status == "supported")

        confidence = _bounded_float(base_confidence, default=0.55)
        confidence += min(0.20, 0.035 * supported_count)
        confidence -= min(0.25, 0.04 * ambiguity_count)
        confidence -= min(0.20, 0.05 * contradiction_count)
        confidence -= min(0.14, 0.035 * unsupported_count)
        confidence = _bounded_float(confidence, default=0.55)

        level = _level_from_confidence(confidence, risk_count=risk_count, contradiction_count=contradiction_count)
        evidence_gap = None
        if not evidence and working_memory:
            evidence_gap = "no_evidence_records"
        elif unsupported_count:
            evidence_gap = "unsupported_claims_present"
        elif contradiction_count:
            evidence_gap = "conflicting_evidence_present"

        recommended_action = "proceed"
        if level == "medium":
            recommended_action = "proceed_with_caution"
        if level == "high":
            recommended_action = "gather_more_evidence"
        if level == "critical":
            recommended_action = "pause_or_request_review"

        drivers = []
        if ambiguity_count:
            drivers.append(f"ambiguity={ambiguity_count}")
        if risk_count:
            drivers.append(f"risk={risk_count}")
        if contradiction_count:
            drivers.append(f"contradictions={contradiction_count}")
        if unsupported_count:
            drivers.append(f"unsupported={unsupported_count}")
        if not drivers:
            drivers.append("stable_state")

        return cls(
            confidence=confidence,
            level=level,
            evidence_gap=evidence_gap,
            ambiguity_count=ambiguity_count,
            contradiction_count=contradiction_count,
            risk_count=risk_count,
            recommended_action=recommended_action,
            drivers=tuple(drivers),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        gap = f", gap={self.evidence_gap}" if self.evidence_gap else ""
        return f"uncertainty={self.level}, confidence={self.confidence:.2f}, action={self.recommended_action}{gap}"


@dataclass(frozen=True, slots=True)
class DecisionOption:
    """A candidate action considered by the cognitive controller."""

    action: str
    rationale: str
    expected_utility: float = 0.0
    risk: float = 0.0
    cost: float = 0.0
    confidence: float = 0.5
    evidence_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def score(self) -> float:
        return _bounded_float(self.expected_utility + self.confidence - self.risk - self.cost, default=0.0)

    def to_dict(self) -> dict[str, Any]:
        data = _json_safe(asdict(self))
        data["score"] = round(self.score(), 6)
        return data


@dataclass(frozen=True, slots=True)
class CognitiveDecision:
    """Selected action plus audit metadata."""

    decision_id: str
    selected_action: str
    rationale: str
    options: tuple[DecisionOption, ...] = ()
    confidence: float = 0.5
    expected_utility: float = 0.0
    risk: float = 0.0
    cost: float = 0.0
    safety_status: str = "unknown"
    policy_checks: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    turn_index: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def select(
        cls,
        *,
        selected_action: str,
        rationale: str,
        options: Sequence[DecisionOption] = (),
        confidence: float = 0.5,
        expected_utility: float | None = None,
        risk: float = 0.0,
        cost: float = 0.0,
        safety_status: str = "unknown",
        policy_checks: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
        turn_index: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> "CognitiveDecision":
        if expected_utility is None and options:
            expected_utility = max(option.expected_utility for option in options)
        expected_utility = 0.0 if expected_utility is None else float(expected_utility)
        decision_id = _stable_id("dec", selected_action, rationale, turn_index)[:22]
        return cls(
            decision_id=decision_id,
            selected_action=_clip(_sanitize(selected_action), 500),
            rationale=_clip(_sanitize(rationale), 1_000),
            options=tuple(options),
            confidence=_bounded_float(confidence, default=0.5),
            expected_utility=_bounded_float(expected_utility, default=0.0),
            risk=_bounded_float(risk, default=0.0),
            cost=max(0.0, float(cost or 0.0)),
            safety_status=_validate_choice(safety_status, VALID_SAFETY_STATUS, "unknown"),
            policy_checks=tuple(_unique(str(item) for item in policy_checks)),
            evidence_refs=tuple(_unique(str(item) for item in evidence_refs)),
            turn_index=int(turn_index),
            metadata=_redact_mapping(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def compact(self) -> str:
        return (
            f"{self.decision_id} action={self.selected_action} "
            f"confidence={self.confidence:.2f} safety={self.safety_status}"
        )


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """Small telemetry event used by the cognitive state."""

    event_id: str
    turn_index: int
    phase: str
    message: str
    severity: str = "info"
    refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    @classmethod
    def new(
        cls,
        *,
        turn_index: int,
        phase: str,
        message: str,
        severity: str = "info",
        refs: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> "TraceEvent":
        safe_message = _clip(_sanitize(message), 1_000)
        safe_phase = _normalize_identifier(phase or "unknown")
        event_id = _stable_id("evt", turn_index, safe_phase, safe_message)[:22]
        return cls(
            event_id=event_id,
            turn_index=int(turn_index),
            phase=safe_phase,
            message=safe_message,
            severity=_validate_choice(severity, VALID_EVENT_SEVERITIES, "info"),
            refs=tuple(_unique(str(ref) for ref in refs)),
            metadata=_redact_mapping(metadata or {}),
            timestamp=timestamp or _utc_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True, slots=True)
class CognitiveScorecard:
    """Compact internal scorecard aligned with Sprint 4 priorities."""

    generality: float = 0.0
    evidence_grounding: float = 0.0
    uncertainty_handling: float = 0.0
    safety: float = 0.0
    cost_efficiency: float = 0.0
    traceability: float = 0.0
    adapter_fit: float = 0.0
    innovation: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state_parts(
        cls,
        *,
        task_theory: TaskTheory,
        working_memory: Sequence[WorkingMemoryItem],
        evidence: Sequence[EvidenceRecord],
        uncertainty: UncertaintyEstimate,
        decisions: Sequence[CognitiveDecision],
        trace: Sequence[TraceEvent],
    ) -> "CognitiveScorecard":
        generality = 0.55
        if task_theory.generalization_notes:
            generality += 0.15
        if task_theory.domain in SPRINT4_SCENARIOS_BY_DOMAIN:
            generality += 0.10
        if task_theory.constraints and task_theory.success_criteria:
            generality += 0.10

        supported = sum(1 for record in evidence if record.status == "supported")
        evidence_grounding = min(1.0, 0.30 + 0.18 * supported + (0.12 if task_theory.evidence_requirements else 0.0))

        uncertainty_handling = {
            "low": 0.88,
            "medium": 0.72,
            "high": 0.55,
            "critical": 0.38,
        }.get(uncertainty.level, 0.55)
        if uncertainty.recommended_action in {"gather_more_evidence", "pause_or_request_review"}:
            uncertainty_handling += 0.08

        unsafe_decisions = sum(1 for decision in decisions if decision.safety_status in {"unsafe", "blocked"})
        safe_decisions = sum(1 for decision in decisions if decision.safety_status in {"safe", "needs_review"})
        safety = min(1.0, 0.62 + 0.08 * safe_decisions - 0.18 * unsafe_decisions)

        memory_load = len(working_memory)
        cost_efficiency = max(0.0, min(1.0, 0.92 - max(0, memory_load - 18) * 0.015))
        traceability = min(1.0, 0.30 + 0.05 * len(trace) + 0.08 * len(decisions) + 0.04 * len(evidence))
        adapter_fit = 0.80 if task_theory.adapter or task_theory.domain != "unknown" else 0.48
        innovation = 0.72 if working_memory and uncertainty.drivers else 0.55

        return cls(
            generality=_bounded_float(generality, default=0.0),
            evidence_grounding=_bounded_float(evidence_grounding, default=0.0),
            uncertainty_handling=_bounded_float(uncertainty_handling, default=0.0),
            safety=_bounded_float(safety, default=0.0),
            cost_efficiency=_bounded_float(cost_efficiency, default=0.0),
            traceability=_bounded_float(traceability, default=0.0),
            adapter_fit=_bounded_float(adapter_fit, default=0.0),
            innovation=_bounded_float(innovation, default=0.0),
            metadata={
                "memory_items": len(working_memory),
                "evidence_items": len(evidence),
                "decisions": len(decisions),
                "trace_events": len(trace),
            },
        )

    def aggregate(self) -> float:
        weights = {
            "generality": 0.18,
            "evidence_grounding": 0.17,
            "uncertainty_handling": 0.15,
            "safety": 0.18,
            "cost_efficiency": 0.12,
            "traceability": 0.10,
            "adapter_fit": 0.06,
            "innovation": 0.04,
        }
        return round(
            self.generality * weights["generality"]
            + self.evidence_grounding * weights["evidence_grounding"]
            + self.uncertainty_handling * weights["uncertainty_handling"]
            + self.safety * weights["safety"]
            + self.cost_efficiency * weights["cost_efficiency"]
            + self.traceability * weights["traceability"]
            + self.adapter_fit * weights["adapter_fit"]
            + self.innovation * weights["innovation"],
            6,
        )

    def to_dict(self) -> dict[str, Any]:
        data = _json_safe(asdict(self))
        data["aggregate"] = self.aggregate()
        return data


@dataclass(frozen=True, slots=True)
class CognitiveState:
    """Complete NCP state for a single task/episode."""

    episode_id: str
    task_theory: TaskTheory
    adapter_profile: DomainAdapterProfile
    policy_boundary: PolicyBoundary
    version: str = NCP_STATE_VERSION
    turn_index: int = 0
    status: str = "initialized"
    working_memory: tuple[WorkingMemoryItem, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    uncertainty: UncertaintyEstimate = field(default_factory=UncertaintyEstimate)
    decisions: tuple[CognitiveDecision, ...] = ()
    trace: tuple[TraceEvent, ...] = ()
    scorecard: CognitiveScorecard = field(default_factory=CognitiveScorecard)
    attention_digest: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        task_text: str,
        metadata: Mapping[str, Any] | None = None,
        classification: Any = None,
        route: Any = None,
        plan: Any = None,
        payload: Mapping[str, Any] | None = None,
        policy_context: Mapping[str, Any] | None = None,
    ) -> "CognitiveState":
        task_theory = TaskTheory.from_sources(
            task_text=task_text,
            metadata=metadata,
            classification=classification,
            route=route,
            plan=plan,
            payload=payload,
        )
        adapter_profile = DomainAdapterProfile.from_task_theory(task_theory, route=route, metadata=metadata)
        policy_boundary = PolicyBoundary.from_sources(metadata=metadata, policy_context=policy_context)
        episode_id = _stable_id(
            "ncp",
            task_theory.domain,
            task_theory.scenario_id or task_theory.scenario_name or "",
            _clip(task_text, 1_000),
        )[:24]
        state = cls(
            episode_id=episode_id,
            task_theory=task_theory,
            adapter_profile=adapter_profile,
            policy_boundary=policy_boundary,
            metadata=_redact_mapping(
                {
                    "created_at": _utc_now(),
                    "source": "CognitiveState.new",
                    "ncp_state_version": NCP_STATE_VERSION,
                }
            ),
        )
        return state.append_trace(
            phase="state_init",
            message=f"Initialized NCP state for {task_theory.domain}/{task_theory.scenario_name or task_theory.scenario_id or 'unknown'}.",
            severity="info",
        ).refresh_scorecard()

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self)) | {"scorecard": self.scorecard.to_dict()}

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.episode_id:
            errors.append("episode_id must be non-empty")
        if not self.task_theory.objective:
            errors.append("task_theory.objective must be non-empty")
        if self.policy_boundary.assessment_mode != DEFAULT_ASSESSMENT_MODE:
            errors.append(f"assessment_mode expected {DEFAULT_ASSESSMENT_MODE!r}")
        if self.policy_boundary.scenario_family != DEFAULT_SCENARIO_FAMILY:
            errors.append(f"scenario_family expected {DEFAULT_SCENARIO_FAMILY!r}")
        memory_keys = [item.key for item in self.working_memory]
        if len(memory_keys) != len(set(memory_keys)):
            errors.append("working_memory keys must be unique")
        evidence_ids = [record.evidence_id for record in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            errors.append("evidence ids must be unique")
        decision_ids = [decision.decision_id for decision in self.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            errors.append("decision ids must be unique")
        if self.uncertainty.level not in VALID_UNCERTAINTY_LEVELS:
            errors.append(f"uncertainty.level invalid: {self.uncertainty.level}")
        return errors

    def with_attention_frame(self, attention_frame: Any, *, max_items: int = 18) -> "CognitiveState":
        """Merge an attention frame into working memory and update uncertainty."""

        frame = _as_mapping(attention_frame)
        if hasattr(attention_frame, "to_dict") and callable(attention_frame.to_dict):
            frame = _as_mapping(attention_frame.to_dict())

        signals = frame.get("signals") or []
        items = []
        for signal in list(signals)[: max(1, int(max_items))]:
            item = WorkingMemoryItem.from_signal(signal, turn_index=self.turn_index)
            items.append(item)

        digest = str(frame.get("task_digest") or frame.get("attention_digest") or "")
        next_state = self.remember_many(items).append_trace(
            phase="attention",
            message=f"Merged {len(items)} attention signals into working memory.",
            refs=[item.key for item in items[:8]],
            metadata={
                "attention_version": frame.get("version"),
                "suppressed_count": frame.get("suppressed_count", 0),
                "risk_tags": frame.get("risk_tags", []),
                "uncertainty_hints": frame.get("uncertainty_hints", []),
            },
        )
        next_state = replace(next_state, attention_digest=digest or next_state.attention_digest)
        return next_state.reestimate_uncertainty().refresh_scorecard()

    def remember(self, item: WorkingMemoryItem) -> "CognitiveState":
        return self.remember_many([item])

    def remember_many(self, items: Iterable[WorkingMemoryItem]) -> "CognitiveState":
        existing: dict[str, WorkingMemoryItem] = {item.key: item for item in self.working_memory}
        for item in items:
            current = existing.get(item.key)
            if current is None or item.salience >= current.salience:
                existing[item.key] = item
        ordered = tuple(
            sorted(
                existing.values(),
                key=lambda item: (-item.locked, -item.salience, item.turn_index, item.key),
            )
        )
        return replace(self, working_memory=ordered)

    def observe_evidence(self, record: EvidenceRecord) -> "CognitiveState":
        existing = {item.evidence_id: item for item in self.evidence}
        current = existing.get(record.evidence_id)
        if current is None or record.confidence >= current.confidence:
            existing[record.evidence_id] = record
        next_state = replace(self, evidence=tuple(existing.values()))
        return next_state.append_trace(
            phase="evidence",
            message=f"Observed evidence {record.evidence_id} with status={record.status}.",
            refs=[record.evidence_id],
        ).reestimate_uncertainty().refresh_scorecard()

    def update_uncertainty(self, estimate: UncertaintyEstimate) -> "CognitiveState":
        return replace(self, uncertainty=estimate).append_trace(
            phase="uncertainty",
            message=estimate.compact(),
            severity="info" if estimate.level in {"low", "medium"} else "warning",
        ).refresh_scorecard()

    def reestimate_uncertainty(self, *, base_confidence: float | None = None) -> "CognitiveState":
        estimate = UncertaintyEstimate.from_state(
            working_memory=self.working_memory,
            evidence=self.evidence,
            base_confidence=self.task_theory.confidence if base_confidence is None else base_confidence,
        )
        return replace(self, uncertainty=estimate)

    def decide(self, decision: CognitiveDecision) -> "CognitiveState":
        next_state = replace(self, decisions=(*self.decisions, decision), status="decision_recorded")
        return next_state.append_trace(
            phase="decision",
            message=decision.compact(),
            severity="warning" if decision.safety_status in {"blocked", "unsafe"} else "info",
            refs=[decision.decision_id, *decision.evidence_refs[:6]],
        ).refresh_scorecard()

    def append_trace(
        self,
        *,
        phase: str,
        message: str,
        severity: str = "info",
        refs: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "CognitiveState":
        event = TraceEvent.new(
            turn_index=self.turn_index,
            phase=phase,
            message=message,
            severity=severity,
            refs=refs,
            metadata=metadata,
        )
        return replace(self, trace=(*self.trace, event))

    def advance_turn(self, *, status: str | None = None) -> "CognitiveState":
        next_state = replace(self, turn_index=self.turn_index + 1, status=status or self.status)
        return next_state.prune_memory()

    def prune_memory(self) -> "CognitiveState":
        retained = tuple(item for item in self.working_memory if not item.is_expired(self.turn_index))
        if len(retained) == len(self.working_memory):
            return self
        return replace(self, working_memory=retained).append_trace(
            phase="memory_prune",
            message=f"Pruned {len(self.working_memory) - len(retained)} expired working-memory items.",
        )

    def refresh_scorecard(self) -> "CognitiveState":
        scorecard = CognitiveScorecard.from_state_parts(
            task_theory=self.task_theory,
            working_memory=self.working_memory,
            evidence=self.evidence,
            uncertainty=self.uncertainty,
            decisions=self.decisions,
            trace=self.trace,
        )
        return replace(self, scorecard=scorecard)

    def compact_context(self, *, max_lines: int = MAX_CONTEXT_LINES) -> str:
        lines = [
            f"NCP state {self.version}: episode={self.episode_id} turn={self.turn_index} status={self.status}",
            "TaskTheory: " + self.task_theory.compact(),
            "Adapter: "
            + f"domain={self.adapter_profile.domain} adapter={self.adapter_profile.adapter} "
            + f"category={self.adapter_profile.category}",
            "Policy: "
            + f"mode={self.policy_boundary.assessment_mode} family={self.policy_boundary.scenario_family}",
            "Uncertainty: " + self.uncertainty.compact(),
            f"Scorecard: aggregate={self.scorecard.aggregate():.3f}",
        ]

        if self.working_memory:
            lines.append("WorkingMemory:")
            for item in self.working_memory[:12]:
                lines.append("- " + item.compact())
        if self.evidence:
            lines.append("Evidence:")
            for record in self.evidence[:8]:
                lines.append("- " + record.compact())
        if self.decisions:
            lines.append("Decisions:")
            for decision in self.decisions[-6:]:
                lines.append("- " + decision.compact())

        return "\n".join(lines[: max(1, int(max_lines))])


def new_cognitive_state(
    *,
    task_text: str,
    metadata: Mapping[str, Any] | None = None,
    classification: Any = None,
    route: Any = None,
    plan: Any = None,
    payload: Mapping[str, Any] | None = None,
    policy_context: Mapping[str, Any] | None = None,
) -> CognitiveState:
    """Convenience constructor for future controller and tests."""

    return CognitiveState.new(
        task_text=task_text,
        metadata=metadata,
        classification=classification,
        route=route,
        plan=plan,
        payload=payload,
        policy_context=policy_context,
    )


def state_from_attention(
    *,
    task_text: str,
    attention_frame: Any,
    metadata: Mapping[str, Any] | None = None,
    classification: Any = None,
    route: Any = None,
    plan: Any = None,
    payload: Mapping[str, Any] | None = None,
    policy_context: Mapping[str, Any] | None = None,
) -> CognitiveState:
    """Build a new state and immediately merge an attention frame."""

    return new_cognitive_state(
        task_text=task_text,
        metadata=metadata,
        classification=classification,
        route=route,
        plan=plan,
        payload=payload,
        policy_context=policy_context,
    ).with_attention_frame(attention_frame)


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


def _coerce_string_tuple(*values: Any) -> tuple[str, ...]:
    items: list[str] = []
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, Mapping):
            iterable = [json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)]
        elif isinstance(value, (list, tuple, set)):
            iterable = list(value)
        else:
            iterable = [value]
        for item in iterable:
            if item is None or item == "":
                continue
            text = _clip(_sanitize(str(item)), MAX_COMPACT_FIELD_CHARS)
            if text:
                items.append(text)
    return tuple(_unique(items))


def _redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        key_text = str(key)
        if _SECRET_KEY_RE.search(key_text):
            redacted[key_text] = "<redacted>"
            continue
        if isinstance(value, Mapping):
            redacted[key_text] = _redact_mapping(value)
        elif isinstance(value, list):
            redacted[key_text] = [_redact_value(item) for item in value]
        elif isinstance(value, tuple):
            redacted[key_text] = tuple(_redact_value(item) for item in value)
        else:
            redacted[key_text] = _redact_value(value)
    return redacted


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
    text = _ID_RE.sub("_", text)
    return text.strip("_") or "unknown"


def _bounded_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return min(1.0, max(0.0, number))


def _level_from_confidence(confidence: float, *, risk_count: int = 0, contradiction_count: int = 0) -> str:
    if contradiction_count >= 2 or risk_count >= 8:
        return "critical"
    if confidence < 0.35 or contradiction_count:
        return "high"
    if confidence < 0.68 or risk_count >= 3:
        return "medium"
    return "low"


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
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
    return tuple(values)


def _first_present(*mappings: Mapping[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    for mapping in mappings:
        for key in keys:
            value = mapping.get(key)
            if value not in (None, ""):
                return value
    return default


def _first_text(*values: Any, default: str | None = "") -> str | None:
    for value in values:
        if value in (None, ""):
            continue
        if isinstance(value, Mapping):
            continue
        text = _sanitize(value)
        if text:
            return text
    return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "DEFAULT_ASSESSMENT_MODE",
    "DEFAULT_BENCHMARK",
    "DEFAULT_SCENARIO_FAMILY",
    "NCP_STATE_VERSION",
    "SPRINT4_DOMAIN_CATEGORIES",
    "SPRINT4_SCENARIOS_BY_DOMAIN",
    "CognitiveDecision",
    "CognitiveScorecard",
    "CognitiveState",
    "DecisionOption",
    "DomainAdapterProfile",
    "EvidenceRecord",
    "PolicyBoundary",
    "TaskTheory",
    "TraceEvent",
    "UncertaintyEstimate",
    "WorkingMemoryItem",
    "new_cognitive_state",
    "state_from_attention",
]
