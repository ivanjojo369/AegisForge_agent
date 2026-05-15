from __future__ import annotations

"""Neuro-Cognitive Purple Core attention gate for AegisForge.

The attention gate is intentionally deterministic and lightweight.  It does not
try to be a model, a memory store, or a planner.  Its job is to compress the
current episode into a small, ranked set of signals that the cognitive
controller can safely pass to working memory, planning, evidence verification,
and telemetry.

Design goals:
- preserve Sprint 4 metadata and fair-play constraints;
- rank task, policy, evidence, uncertainty, routing, and memory cues;
- avoid leaking secrets or raw oversized payloads;
- remain dependency-free and easy to unit test.
"""

from dataclasses import asdict, dataclass, field, is_dataclass
from hashlib import sha256
import json
import math
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence


class SupportsAsDict(Protocol):
    def as_dict(self) -> dict[str, Any]: ...


NCP_ATTENTION_VERSION = "0.1.0"

DEFAULT_MAX_SIGNALS = 24
DEFAULT_MAX_CONTENT_CHARS = 420
DEFAULT_TASK_EXCERPT_CHARS = 1_800
DEFAULT_MIN_SCORE = 0.14

_DIRECTIVE_WORDS = {
    "must",
    "need",
    "should",
    "required",
    "require",
    "objective",
    "goal",
    "success",
    "constraint",
    "avoid",
    "do not",
    "don't",
    "never",
    "only",
    "verify",
    "evidence",
    "cite",
    "grounded",
    "policy",
    "safe",
    "refuse",
    "manual review",
    "heldout",
    "held-out",
    "generalize",
    "budget",
    "cost",
    "latency",
    "tool",
    "adapter",
    "scenario",
    "benchmark",
}

_RISK_WORDS = {
    "attack",
    "adversarial",
    "jailbreak",
    "prompt injection",
    "injection",
    "exfiltration",
    "secret",
    "credential",
    "unsafe",
    "destructive",
    "delete",
    "exploit",
    "vulnerability",
    "suspicious",
    "redirect",
    "leak",
    "poison",
    "hidden instruction",
    "hardcode",
    "lookup table",
    "platform exploitation",
}

_EVIDENCE_WORDS = {
    "evidence",
    "source",
    "citation",
    "document",
    "artifact",
    "observation",
    "state",
    "trace",
    "log",
    "database",
    "tool result",
    "grounded",
    "unsupported",
    "insufficient information",
}

_UNCERTAINTY_WORDS = {
    "uncertain",
    "unknown",
    "ambiguous",
    "missing",
    "incomplete",
    "conflict",
    "contradict",
    "stale",
    "distractor",
    "noisy",
    "hidden",
    "not enough",
}

_SPRINT4_KEYS = {
    "assessment_mode",
    "scenario_family",
    "benchmark",
    "selected_opponent",
    "source_url",
    "scenario_id",
    "scenario_name",
    "category",
    "adapter",
    "domain",
    "track",
    "track_hint",
    "opponent_profile",
    "policy_context",
    "sprint4_policy_context",
}

_HIGH_VALUE_METADATA_KEYS = {
    *_SPRINT4_KEYS,
    "risk",
    "task_type",
    "artifact_required",
    "artifact_expected",
    "required_tools",
    "available_actions",
    "success_criteria",
    "constraints",
    "mission_id",
    "env_id",
    "payload_id",
    "task_id",
    "difficulty",
    "priority",
    "heldout_like",
    "normal_user",
    "tool_mode",
    "prompt_profile",
    "route",
    "role",
    "posture",
    "budget",
    "cost",
    "latency",
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


_SIGNAL_BASE_WEIGHTS: dict[str, float] = {
    "policy": 0.86,
    "plan": 0.78,
    "classification": 0.72,
    "task": 0.66,
    "metadata": 0.58,
    "route": 0.54,
    "memory": 0.50,
    "prompt_context": 0.48,
    "budget": 0.42,
    "diagnostic": 0.36,
}


@dataclass(frozen=True, slots=True)
class AttentionConfig:
    """Runtime knobs for the deterministic attention gate."""

    max_signals: int = DEFAULT_MAX_SIGNALS
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS
    task_excerpt_chars: int = DEFAULT_TASK_EXCERPT_CHARS
    min_score: float = DEFAULT_MIN_SCORE
    redact_sensitive: bool = True
    include_low_value_metadata: bool = False
    metadata_value_chars: int = 260
    memory_value_chars: int = 320

    def normalized(self) -> "AttentionConfig":
        return AttentionConfig(
            max_signals=max(1, int(self.max_signals)),
            max_content_chars=max(80, int(self.max_content_chars)),
            task_excerpt_chars=max(200, int(self.task_excerpt_chars)),
            min_score=min(max(float(self.min_score), 0.0), 1.0),
            redact_sensitive=bool(self.redact_sensitive),
            include_low_value_metadata=bool(self.include_low_value_metadata),
            metadata_value_chars=max(80, int(self.metadata_value_chars)),
            memory_value_chars=max(80, int(self.memory_value_chars)),
        )


@dataclass(frozen=True, slots=True)
class AttentionSignal:
    """One ranked unit of information selected for cognitive processing."""

    source: str
    kind: str
    content: str
    score: float
    tags: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "content": self.content,
            "score": round(float(self.score), 6),
            "tags": list(self.tags),
            "evidence": list(self.evidence),
            "metadata": _json_safe(dict(self.metadata)),
        }

    def compact(self) -> str:
        tag_text = f" [{', '.join(self.tags)}]" if self.tags else ""
        return f"{self.source}/{self.kind}{tag_text}: {self.content}"


@dataclass(frozen=True, slots=True)
class AttentionFrame:
    """Compact output of the attention gate for one execution episode."""

    version: str
    task_digest: str
    focus: str
    track_hint: str
    scenario_id: str | None
    assessment_mode: str
    scenario_family: str
    signals: tuple[AttentionSignal, ...]
    suppressed_count: int
    risk_tags: tuple[str, ...]
    uncertainty_hints: tuple[str, ...]
    routing_hints: Mapping[str, Any] = field(default_factory=dict)
    memory_hints: Mapping[str, Any] = field(default_factory=dict)
    summary: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_digest": self.task_digest,
            "focus": self.focus,
            "track_hint": self.track_hint,
            "scenario_id": self.scenario_id,
            "assessment_mode": self.assessment_mode,
            "scenario_family": self.scenario_family,
            "signals": [signal.to_dict() for signal in self.signals],
            "suppressed_count": self.suppressed_count,
            "risk_tags": list(self.risk_tags),
            "uncertainty_hints": list(self.uncertainty_hints),
            "routing_hints": _json_safe(dict(self.routing_hints)),
            "memory_hints": _json_safe(dict(self.memory_hints)),
            "summary": self.summary,
            "metadata": _json_safe(dict(self.metadata)),
        }

    def compact_context(self, *, max_lines: int | None = None) -> str:
        limit = len(self.signals) if max_lines is None else max(0, int(max_lines))
        lines = [
            f"NCP attention focus: {self.focus}",
            f"track={self.track_hint}; scenario={self.scenario_id or 'unknown'}; mode={self.assessment_mode}",
        ]
        for signal in self.signals[:limit]:
            lines.append(f"- {signal.compact()}")
        if self.suppressed_count:
            lines.append(f"- suppressed_low_salience={self.suppressed_count}")
        return "\n".join(lines).strip()


class AttentionGate:
    """Deterministic salience selector for AegisForge NCP.

    The gate can be called from ``agent.py`` after classification/routing/planning
    or from a future ``cognitive.controller`` before committing items to working
    memory.  It accepts arbitrary dataclasses, mappings, or lightweight objects,
    because existing strategy components expose mixed shapes.
    """

    def __init__(self, config: AttentionConfig | None = None) -> None:
        self.config = (config or AttentionConfig()).normalized()

    def select(
        self,
        *,
        task_text: str,
        metadata: Mapping[str, Any] | None = None,
        classification: Any = None,
        route: Any = None,
        plan: Any = None,
        budget_state: Any = None,
        policy_context: Mapping[str, Any] | None = None,
        prompt_context: Mapping[str, Any] | None = None,
        memory: Mapping[str, Any] | Sequence[Any] | None = None,
    ) -> AttentionFrame:
        """Return a ranked attention frame for the current execution state."""

        safe_task = self._sanitize(str(task_text or ""))
        metadata_map = _as_mapping(metadata)
        classification_map = _as_mapping(classification)
        route_map = _as_mapping(route)
        plan_map = _as_mapping(plan)
        budget_map = _as_mapping(budget_state)
        policy_map = _as_mapping(policy_context)
        prompt_map = _as_mapping(prompt_context)

        track_hint = _normalize_identifier(
            _first_present(
                metadata_map,
                classification_map,
                route_map,
                keys=("track_hint", "track", "domain", "adapter_name"),
                default="openenv",
            )
        )
        scenario_id = _first_present(
            metadata_map,
            policy_map,
            keys=("scenario_id", "scenario", "scenario_name", "id", "slug"),
            default=None,
        )
        assessment_mode = str(metadata_map.get("assessment_mode") or "purple_benchmark")
        scenario_family = str(metadata_map.get("scenario_family") or "agentbeats_sprint4")

        signals: list[AttentionSignal] = []
        signals.extend(self._task_signals(safe_task))
        signals.extend(self._metadata_signals(metadata_map, source="metadata"))
        signals.extend(self._object_signals(classification_map, source="classification"))
        signals.extend(self._object_signals(route_map, source="route"))
        signals.extend(self._plan_signals(plan_map))
        signals.extend(self._object_signals(budget_map, source="budget"))
        signals.extend(self._metadata_signals(policy_map, source="policy"))
        signals.extend(self._metadata_signals(prompt_map, source="prompt_context"))
        signals.extend(self._memory_signals(memory))

        deduped = self._dedupe(signals)
        ranked = sorted(deduped, key=lambda signal: (-signal.score, signal.source, signal.kind, signal.content))
        kept = tuple(signal for signal in ranked if signal.score >= self.config.min_score)[: self.config.max_signals]
        suppressed_count = max(0, len(ranked) - len(kept))

        risk_tags = _unique(
            tag
            for signal in kept
            for tag in signal.tags
            if tag.startswith("risk:") or tag in {"hardcoding_guard", "policy_boundary", "secret_boundary"}
        )
        uncertainty_hints = _unique(
            hint
            for signal in kept
            for hint in signal.tags
            if hint.startswith("uncertainty:") or hint in {"needs_evidence", "ambiguous_context"}
        )

        focus = self._build_focus(
            task_text=safe_task,
            track_hint=track_hint,
            scenario_id=str(scenario_id) if scenario_id else None,
            signals=kept,
        )
        summary = self._build_summary(
            focus=focus,
            signals=kept,
            risk_tags=risk_tags,
            uncertainty_hints=uncertainty_hints,
        )

        return AttentionFrame(
            version=NCP_ATTENTION_VERSION,
            task_digest=_stable_digest(safe_task, metadata_map),
            focus=focus,
            track_hint=track_hint,
            scenario_id=str(scenario_id) if scenario_id else None,
            assessment_mode=assessment_mode,
            scenario_family=scenario_family,
            signals=kept,
            suppressed_count=suppressed_count,
            risk_tags=tuple(risk_tags),
            uncertainty_hints=tuple(uncertainty_hints),
            routing_hints=self._routing_hints(route_map=route_map, classification_map=classification_map),
            memory_hints=self._memory_hints(kept),
            summary=summary,
            metadata={
                "candidate_signal_count": len(signals),
                "ranked_signal_count": len(ranked),
                "max_signals": self.config.max_signals,
                "min_score": self.config.min_score,
            },
        )

    def _task_signals(self, task_text: str) -> list[AttentionSignal]:
        if not task_text.strip():
            return [
                AttentionSignal(
                    source="task",
                    kind="empty_task",
                    content="No task text was supplied.",
                    score=0.72,
                    tags=("uncertainty:empty_task", "needs_evidence"),
                )
            ]

        excerpt = _clip(task_text, self.config.task_excerpt_chars)
        fragments = [frag.strip() for frag in _SENTENCE_SPLIT_RE.split(excerpt) if frag.strip()]
        if not fragments:
            fragments = [excerpt]

        signals: list[AttentionSignal] = [
            self._make_signal(
                source="task",
                kind="task_excerpt",
                content=_clip(excerpt, self.config.max_content_chars),
                base=0.62,
                evidence=("raw_task",),
            )
        ]

        # Select a small number of directive-heavy fragments; this prevents long
        # benchmark prompts from dominating the attention frame.
        scored_fragments = []
        for fragment in fragments[:40]:
            score, tags = self._salience(fragment, base=0.44)
            if score >= 0.46 or any(word in fragment.lower() for word in ("scenario", "objective", "constraint", "success")):
                scored_fragments.append((score, fragment, tags))
        for score, fragment, tags in sorted(scored_fragments, reverse=True)[:8]:
            signals.append(
                AttentionSignal(
                    source="task",
                    kind="directive",
                    content=_clip(fragment, self.config.max_content_chars),
                    score=score,
                    tags=tags,
                    evidence=("task_fragment",),
                )
            )
        return signals

    def _metadata_signals(self, mapping: Mapping[str, Any], *, source: str) -> list[AttentionSignal]:
        if not mapping:
            return []

        signals: list[AttentionSignal] = []
        for key in sorted(mapping):
            if not self.config.include_low_value_metadata and key not in _HIGH_VALUE_METADATA_KEYS:
                continue
            value = mapping.get(key)
            if value is None or value == "":
                continue

            content = f"{key}={self._format_value(key, value, limit=self.config.metadata_value_chars)}"
            base = _SIGNAL_BASE_WEIGHTS.get(source, 0.46)
            if key in _SPRINT4_KEYS:
                base += 0.12
            elif key in _HIGH_VALUE_METADATA_KEYS:
                base += 0.06

            signal = self._make_signal(
                source=source,
                kind=str(key),
                content=content,
                base=base,
                evidence=(f"{source}.{key}",),
                metadata={"key": key},
            )
            signals.append(signal)

        return signals

    def _object_signals(self, mapping: Mapping[str, Any], *, source: str) -> list[AttentionSignal]:
        if not mapping:
            return []

        interesting_keys = {
            "track_guess",
            "track",
            "risk",
            "task_type",
            "artifact_expected",
            "artifact_required",
            "heldout_like",
            "adapter_name",
            "tool_mode",
            "prompt_profile",
            "near_limit",
            "compress_now",
            "estimated_tokens_used",
            "estimated_budget",
            "llm_calls",
        }
        view = {key: mapping[key] for key in mapping if key in interesting_keys and mapping.get(key) not in (None, "")}
        if not view:
            return []

        signals: list[AttentionSignal] = []
        for key, value in sorted(view.items()):
            signals.append(
                self._make_signal(
                    source=source,
                    kind=str(key),
                    content=f"{key}={self._format_value(key, value, limit=self.config.metadata_value_chars)}",
                    base=_SIGNAL_BASE_WEIGHTS.get(source, 0.46),
                    evidence=(f"{source}.{key}",),
                    metadata={"key": key},
                )
            )
        return signals

    def _plan_signals(self, plan_map: Mapping[str, Any]) -> list[AttentionSignal]:
        if not plan_map:
            return []

        signals: list[AttentionSignal] = []
        goal = plan_map.get("goal")
        if goal:
            signals.append(
                self._make_signal(
                    source="plan",
                    kind="goal",
                    content=f"goal={self._format_value('goal', goal, limit=self.config.max_content_chars)}",
                    base=0.82,
                    evidence=("plan.goal",),
                )
            )

        steps = plan_map.get("steps")
        if isinstance(steps, Sequence) and not isinstance(steps, (str, bytes, bytearray)):
            step_names: list[str] = []
            for step in steps[:10]:
                step_map = _as_mapping(step)
                if step_map:
                    name = step_map.get("name") or step_map.get("title") or step_map.get("id")
                    reason = step_map.get("reason") or step_map.get("description")
                    step_names.append(str(name or reason or step))
                else:
                    step_names.append(str(step))
            if step_names:
                signals.append(
                    self._make_signal(
                        source="plan",
                        kind="steps",
                        content="steps=" + _clip(" -> ".join(step_names), self.config.max_content_chars),
                        base=0.76,
                        evidence=("plan.steps",),
                    )
                )

        for key in ("estimated_budget", "requires_tools", "risk_controls"):
            if key in plan_map and plan_map.get(key) not in (None, ""):
                signals.append(
                    self._make_signal(
                        source="plan",
                        kind=key,
                        content=f"{key}={self._format_value(key, plan_map[key], limit=self.config.metadata_value_chars)}",
                        base=0.62,
                        evidence=(f"plan.{key}",),
                    )
                )
        return signals

    def _memory_signals(self, memory: Mapping[str, Any] | Sequence[Any] | None) -> list[AttentionSignal]:
        if memory is None:
            return []

        items: list[tuple[str, Any]] = []
        if isinstance(memory, Mapping):
            items = [(str(key), value) for key, value in memory.items()]
        elif isinstance(memory, Sequence) and not isinstance(memory, (str, bytes, bytearray)):
            items = [(f"item_{idx}", value) for idx, value in enumerate(memory[:12])]
        else:
            items = [("memory", memory)]

        signals: list[AttentionSignal] = []
        for key, value in items[:12]:
            if value in (None, ""):
                continue
            signals.append(
                self._make_signal(
                    source="memory",
                    kind=key,
                    content=f"{key}={self._format_value(key, value, limit=self.config.memory_value_chars)}",
                    base=0.50,
                    evidence=(f"memory.{key}",),
                    metadata={"memory_key": key},
                )
            )
        return signals

    def _make_signal(
        self,
        *,
        source: str,
        kind: str,
        content: str,
        base: float,
        evidence: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> AttentionSignal:
        safe_content = _clip(self._sanitize(content), self.config.max_content_chars)
        score, tags = self._salience(safe_content, base=base)
        return AttentionSignal(
            source=source,
            kind=kind,
            content=safe_content,
            score=score,
            tags=tags,
            evidence=tuple(evidence),
            metadata=dict(metadata or {}),
        )

    def _salience(self, text: str, *, base: float) -> tuple[float, tuple[str, ...]]:
        lowered = text.lower()
        score = float(base)
        tags: list[str] = []

        directive_hits = [word for word in _DIRECTIVE_WORDS if word in lowered]
        risk_hits = [word for word in _RISK_WORDS if word in lowered]
        evidence_hits = [word for word in _EVIDENCE_WORDS if word in lowered]
        uncertainty_hits = [word for word in _UNCERTAINTY_WORDS if word in lowered]

        if directive_hits:
            score += min(0.16, 0.025 * len(directive_hits))
            tags.append("directive")
        if risk_hits:
            score += min(0.22, 0.035 * len(risk_hits))
            tags.extend(f"risk:{_normalize_identifier(hit)}" for hit in risk_hits[:4])
        if evidence_hits:
            score += min(0.16, 0.03 * len(evidence_hits))
            tags.append("needs_evidence")
        if uncertainty_hits:
            score += min(0.16, 0.03 * len(uncertainty_hits))
            tags.extend(f"uncertainty:{_normalize_identifier(hit)}" for hit in uncertainty_hits[:3])
        if "hardcode" in lowered or "lookup table" in lowered:
            score += 0.12
            tags.append("hardcoding_guard")
        if "policy" in lowered or "guardrail" in lowered or "refuse" in lowered:
            score += 0.08
            tags.append("policy_boundary")
        if "secret" in lowered or "credential" in lowered or "token" in lowered:
            score += 0.10
            tags.append("secret_boundary")

        # A gentle length penalty keeps huge prompt fragments from winning solely
        # due to keyword density.
        length_penalty = min(0.08, max(0, len(text) - 500) / 5000)
        score = min(1.0, max(0.0, score - length_penalty))
        return score, tuple(_unique(tags))

    def _dedupe(self, signals: Iterable[AttentionSignal]) -> list[AttentionSignal]:
        by_key: dict[str, AttentionSignal] = {}
        for signal in signals:
            key = _dedupe_key(signal.content)
            current = by_key.get(key)
            if current is None or signal.score > current.score:
                by_key[key] = signal
        return list(by_key.values())

    def _format_value(self, key: str, value: Any, *, limit: int) -> str:
        if self.config.redact_sensitive and _SECRET_KEY_RE.search(str(key)):
            return "<redacted>"
        safe_value = _json_safe(value)
        try:
            text = json.dumps(safe_value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            text = repr(safe_value)
        return _clip(self._sanitize(text), limit)

    def _sanitize(self, text: str) -> str:
        compact = re.sub(r"\s+", " ", str(text or "")).strip()
        if self.config.redact_sensitive:
            compact = _SECRET_VALUE_RE.sub("<redacted>", compact)
        return compact

    @staticmethod
    def _build_focus(
        *,
        task_text: str,
        track_hint: str,
        scenario_id: str | None,
        signals: Sequence[AttentionSignal],
    ) -> str:
        top = signals[0].content if signals else _clip(task_text, 160)
        top = re.sub(r"^[a-zA-Z0-9_.-]+=", "", top).strip()
        scenario_part = f" scenario={scenario_id}" if scenario_id else ""
        return _clip(f"{track_hint}{scenario_part}: {top}", 220)

    @staticmethod
    def _build_summary(
        *,
        focus: str,
        signals: Sequence[AttentionSignal],
        risk_tags: Sequence[str],
        uncertainty_hints: Sequence[str],
    ) -> str:
        policy_count = sum(1 for signal in signals if signal.source == "policy")
        evidence_count = sum(1 for signal in signals if "needs_evidence" in signal.tags)
        parts = [f"Focus selected: {focus}"]
        if risk_tags:
            parts.append(f"risk_tags={len(risk_tags)}")
        if uncertainty_hints:
            parts.append(f"uncertainty_hints={len(uncertainty_hints)}")
        if policy_count:
            parts.append(f"policy_signals={policy_count}")
        if evidence_count:
            parts.append(f"evidence_signals={evidence_count}")
        return "; ".join(parts) + "."

    @staticmethod
    def _routing_hints(*, route_map: Mapping[str, Any], classification_map: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "track": classification_map.get("track_guess") or classification_map.get("track"),
                "risk": classification_map.get("risk"),
                "task_type": classification_map.get("task_type"),
                "adapter": route_map.get("adapter_name") or route_map.get("adapter"),
                "tool_mode": route_map.get("tool_mode"),
                "prompt_profile": route_map.get("prompt_profile"),
            }.items()
            if value not in (None, "")
        }

    @staticmethod
    def _memory_hints(signals: Sequence[AttentionSignal]) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for signal in signals:
            counts[signal.source] = counts.get(signal.source, 0) + 1
        return {
            "recommended_working_memory_slots": min(12, max(4, len(signals) // 2)),
            "source_counts": counts,
            "retain_policy_signals": any(signal.source == "policy" for signal in signals),
            "retain_uncertainty_signals": any(
                tag.startswith("uncertainty:") for signal in signals for tag in signal.tags
            ),
        }


def select_attention(
    *,
    task_text: str,
    metadata: Mapping[str, Any] | None = None,
    classification: Any = None,
    route: Any = None,
    plan: Any = None,
    budget_state: Any = None,
    policy_context: Mapping[str, Any] | None = None,
    prompt_context: Mapping[str, Any] | None = None,
    memory: Mapping[str, Any] | Sequence[Any] | None = None,
    config: AttentionConfig | None = None,
) -> AttentionFrame:
    """Convenience wrapper used by tests and future controller integration."""

    return AttentionGate(config=config).select(
        task_text=task_text,
        metadata=metadata,
        classification=classification,
        route=route,
        plan=plan,
        budget_state=budget_state,
        policy_context=policy_context,
        prompt_context=prompt_context,
        memory=memory,
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
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
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
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
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            return _json_safe(value.as_dict())
        except Exception:
            return repr(value)
    return repr(value)


def _stable_digest(task_text: str, metadata: Mapping[str, Any]) -> str:
    payload = {
        "task": _clip(task_text, 4_000),
        "track": metadata.get("track") or metadata.get("track_hint"),
        "scenario_id": metadata.get("scenario_id") or metadata.get("scenario_name"),
        "assessment_mode": metadata.get("assessment_mode"),
        "scenario_family": metadata.get("scenario_family"),
    }
    blob = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True)
    return sha256(blob.encode("utf-8")).hexdigest()[:16]


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    values: list[str] = []
    for item in items:
        item = str(item).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        values.append(item)
    return tuple(values)


def _dedupe_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())[:180]


def _normalize_identifier(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("τ²", "tau2")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def _first_present(*mappings: Mapping[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    for mapping in mappings:
        for key in keys:
            value = mapping.get(key)
            if value not in (None, ""):
                return value
    return default


__all__ = [
    "NCP_ATTENTION_VERSION",
    "AttentionConfig",
    "AttentionFrame",
    "AttentionGate",
    "AttentionSignal",
    "select_attention",
]
