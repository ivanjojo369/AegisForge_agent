from __future__ import annotations

"""Policy bridge for omnibench_aegis_env.

This module provides a lightweight, environment-local coordination layer between
future security guards and the rest of the OpenEnv stack. It is intentionally
useful even before every dedicated guard module is fully implemented.

Design goals:
- avoid hard-failing when individual guard modules are still missing,
- provide one stable decision object for prompt / action / output checks,
- support conservative fallback heuristics until richer guards are wired.
"""

from dataclasses import dataclass, field
from importlib import import_module
import html
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence

JsonDict = Dict[str, Any]


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PolicyContext:
    domain: str = "general"
    scenario_id: Optional[str] = None
    mission_id: Optional[str] = None
    phase: str = "runtime"
    mode: str = "balanced"
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "domain": self.domain,
            "scenario_id": self.scenario_id,
            "mission_id": self.mission_id,
            "phase": self.phase,
            "mode": self.mode,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class GuardOutcome:
    guard_name: str
    ok: bool
    severity: str = "info"
    action: str = "allow"  # allow | flag | sanitize | block
    reasons: List[str] = field(default_factory=list)
    sanitized_text: Optional[str] = None
    sanitized_artifact: Optional[JsonDict] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "guard_name": self.guard_name,
            "ok": self.ok,
            "severity": self.severity,
            "action": self.action,
            "reasons": list(self.reasons),
            "sanitized_text": self.sanitized_text,
            "sanitized_artifact": dict(self.sanitized_artifact) if self.sanitized_artifact else None,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    blocked: bool
    sanitized: bool
    risk_score: float
    effective_text: Optional[str]
    effective_artifact: Optional[JsonDict]
    outcomes: List[GuardOutcome] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "allowed": self.allowed,
            "blocked": self.blocked,
            "sanitized": self.sanitized,
            "risk_score": float(self.risk_score),
            "effective_text": self.effective_text,
            "effective_artifact": dict(self.effective_artifact) if self.effective_artifact else None,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


class Guard(Protocol):
    name: str

    def evaluate(
        self,
        *,
        text: Optional[str] = None,
        artifact: Optional[Mapping[str, Any]] = None,
        action: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> GuardOutcome:
        ...


# ---------------------------------------------------------------------------
# Fallback guards
# ---------------------------------------------------------------------------


_PROMPT_INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore\s+previous\s+instructions",
    r"ignore\s+all\s+prior",
    r"system\s+prompt",
    r"developer\s+message",
    r"reveal\s+hidden\s+instructions",
    r"override\s+policy",
    r"follow\s+the\s+document\s+instead",
    r"hidden\s+text",
    r"embedded\s+instruction",
)

_PII_PATTERNS: tuple[str, ...] = (
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN style
    r"\b(?:\d[ -]*?){13,16}\b",  # card-ish sequence
    r"(?i)api[_ -]?key",
    r"(?i)private[_ -]?key",
    r"(?i)access[_ -]?token",
    r"(?i)secret",
)

_ACTIVE_OUTPUT_PATTERNS: tuple[str, ...] = (
    r"<script\b",
    r"javascript:",
    r"onerror\s*=",
    r"onload\s*=",
    r"<iframe\b",
)


class FallbackPromptInjectionGuard:
    name = "prompt_injection_guard"

    def evaluate(
        self,
        *,
        text: Optional[str] = None,
        artifact: Optional[Mapping[str, Any]] = None,
        action: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> GuardOutcome:
        haystack = "\n".join(
            item for item in [text or "", _stringify_mapping(artifact)] if item
        )
        reasons: list[str] = []
        for pattern in _PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, haystack, flags=re.IGNORECASE):
                reasons.append(f"matched suspicious prompt-injection pattern: {pattern}")

        if not reasons:
            return GuardOutcome(guard_name=self.name, ok=True)

        return GuardOutcome(
            guard_name=self.name,
            ok=False,
            severity="high",
            action="flag",
            reasons=reasons,
        )


class FallbackArtifactBoundaryGuard:
    name = "artifact_boundary"

    def evaluate(
        self,
        *,
        text: Optional[str] = None,
        artifact: Optional[Mapping[str, Any]] = None,
        action: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> GuardOutcome:
        if not artifact:
            return GuardOutcome(guard_name=self.name, ok=True)

        reasons: list[str] = []
        artifact_text = _stringify_mapping(artifact)
        for pattern in _PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, artifact_text, flags=re.IGNORECASE):
                reasons.append(f"artifact contains control-like instruction: {pattern}")

        if not reasons:
            return GuardOutcome(guard_name=self.name, ok=True)

        sanitized = dict(artifact)
        sanitized["trusted_instruction_fields_removed"] = True
        return GuardOutcome(
            guard_name=self.name,
            ok=False,
            severity="high",
            action="sanitize",
            reasons=reasons,
            sanitized_artifact=sanitized,
        )


class FallbackOutputSanitizer:
    name = "output_sanitizer"

    def evaluate(
        self,
        *,
        text: Optional[str] = None,
        artifact: Optional[Mapping[str, Any]] = None,
        action: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> GuardOutcome:
        if not text:
            return GuardOutcome(guard_name=self.name, ok=True)

        reasons: list[str] = []
        sanitized_text = text
        for pattern in _ACTIVE_OUTPUT_PATTERNS:
            if re.search(pattern, sanitized_text, flags=re.IGNORECASE):
                reasons.append(f"active output pattern detected: {pattern}")

        if not reasons:
            return GuardOutcome(guard_name=self.name, ok=True)

        sanitized_text = html.escape(text)
        return GuardOutcome(
            guard_name=self.name,
            ok=False,
            severity="high",
            action="sanitize",
            reasons=reasons,
            sanitized_text=sanitized_text,
        )


class FallbackDependencyGuard:
    name = "dependency_guard"

    def evaluate(
        self,
        *,
        text: Optional[str] = None,
        artifact: Optional[Mapping[str, Any]] = None,
        action: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> GuardOutcome:
        if not text:
            return GuardOutcome(guard_name=self.name, ok=True)

        lower = text.lower()
        suspicious = []
        if "pip install" in lower and "http://" in lower:
            suspicious.append("package installation from non-secure direct URL")
        if "npm install" in lower and re.search(r"@[0-9a-z_.-]+/[0-9a-z_.-]+", lower) is None and "latest" in lower:
            suspicious.append("dependency suggestion is underspecified and weakly verified")

        if not suspicious:
            return GuardOutcome(guard_name=self.name, ok=True)

        return GuardOutcome(
            guard_name=self.name,
            ok=False,
            severity="medium",
            action="flag",
            reasons=suspicious,
        )


class FallbackPrivacyGuard:
    name = "privacy_guard"

    def evaluate(
        self,
        *,
        text: Optional[str] = None,
        artifact: Optional[Mapping[str, Any]] = None,
        action: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> GuardOutcome:
        haystack = "\n".join(item for item in [text or "", _stringify_mapping(artifact)] if item)
        reasons: list[str] = []
        for pattern in _PII_PATTERNS:
            if re.search(pattern, haystack, flags=re.IGNORECASE):
                reasons.append(f"possible sensitive token or PII matched: {pattern}")

        if not reasons:
            return GuardOutcome(guard_name=self.name, ok=True)

        redacted = _redact_pii(haystack)
        return GuardOutcome(
            guard_name=self.name,
            ok=False,
            severity="high",
            action="sanitize",
            reasons=reasons,
            sanitized_text=redacted if text else None,
        )


# ---------------------------------------------------------------------------
# Bridge implementation
# ---------------------------------------------------------------------------


class PolicyBridge:
    """Bridge that normalizes guard execution into a single policy decision."""

    def __init__(self, guards: Optional[Sequence[Guard]] = None) -> None:
        self.guards: list[Guard] = list(guards) if guards is not None else self._load_default_guards()

    def evaluate_prompt(
        self,
        text: str,
        *,
        artifact: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> PolicyDecision:
        return self._run(text=text, artifact=artifact, action=None, context=context)

    def evaluate_action(
        self,
        action: Mapping[str, Any],
        *,
        text: Optional[str] = None,
        artifact: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> PolicyDecision:
        return self._run(text=text, artifact=artifact, action=action, context=context)

    def evaluate_output(
        self,
        text: str,
        *,
        artifact: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> PolicyDecision:
        return self._run(text=text, artifact=artifact, action=None, context=context)

    def _run(
        self,
        *,
        text: Optional[str],
        artifact: Optional[Mapping[str, Any]],
        action: Optional[Mapping[str, Any]],
        context: Optional[PolicyContext],
    ) -> PolicyDecision:
        context = context or PolicyContext()
        effective_text = text
        effective_artifact = dict(artifact) if artifact else None
        outcomes: list[GuardOutcome] = []
        reasons: list[str] = []
        blocked = False
        sanitized = False
        risk_score = 0.0

        for guard in self.guards:
            outcome = guard.evaluate(
                text=effective_text,
                artifact=effective_artifact,
                action=action,
                context=context,
            )
            outcomes.append(outcome)
            reasons.extend(outcome.reasons)
            risk_score += _severity_weight(outcome.severity) if not outcome.ok else 0.0

            if outcome.sanitized_text is not None:
                effective_text = outcome.sanitized_text
                sanitized = True
            if outcome.sanitized_artifact is not None:
                effective_artifact = dict(outcome.sanitized_artifact)
                sanitized = True
            if outcome.action == "block":
                blocked = True

        allowed = not blocked
        risk_score = min(risk_score, 1.0)
        return PolicyDecision(
            allowed=allowed,
            blocked=blocked,
            sanitized=sanitized,
            risk_score=risk_score,
            effective_text=effective_text,
            effective_artifact=effective_artifact,
            outcomes=outcomes,
            reasons=reasons,
            metadata={
                "guard_count": len(self.guards),
                "context": context.to_dict(),
            },
        )

    def _load_default_guards(self) -> list[Guard]:
        guards: list[Guard] = []

        guards.append(_load_external_guard(
            module_name=".prompt_injection_guard",
            candidates=("PromptInjectionGuard", "build_guard"),
            fallback=FallbackPromptInjectionGuard,
        ))
        guards.append(_load_external_guard(
            module_name=".artifact_boundary",
            candidates=("ArtifactBoundaryGuard", "build_guard"),
            fallback=FallbackArtifactBoundaryGuard,
        ))
        guards.append(_load_external_guard(
            module_name=".output_sanitizer",
            candidates=("OutputSanitizer", "build_guard"),
            fallback=FallbackOutputSanitizer,
        ))
        guards.append(_load_external_guard(
            module_name=".dependency_guard",
            candidates=("DependencyGuard", "build_guard"),
            fallback=FallbackDependencyGuard,
        ))
        guards.append(_load_external_guard(
            module_name=".privacy_guard",
            candidates=("PrivacyGuard", "build_guard"),
            fallback=FallbackPrivacyGuard,
        ))

        return guards


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_external_guard(
    *,
    module_name: str,
    candidates: Sequence[str],
    fallback: type[Guard],
) -> Guard:
    try:
        module = import_module(module_name, package=__package__)
    except Exception:
        return fallback()

    for candidate in candidates:
        value = getattr(module, candidate, None)
        if value is None:
            continue
        if callable(value) and candidate == "build_guard":
            try:
                return value()
            except Exception:
                continue
        if isinstance(value, type):
            try:
                return value()
            except Exception:
                continue

    return fallback()


def _severity_weight(severity: str) -> float:
    normalized = str(severity or "info").lower().strip()
    if normalized == "critical":
        return 0.60
    if normalized == "high":
        return 0.35
    if normalized == "medium":
        return 0.18
    if normalized == "low":
        return 0.08
    return 0.02


def _stringify_mapping(value: Optional[Mapping[str, Any]]) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for key, item in value.items():
        parts.append(f"{key}={item}")
    return "\n".join(parts)


def _redact_pii(text: str) -> str:
    redacted = text
    redacted = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED-SSN]", redacted)
    redacted = re.sub(r"\b(?:\d[ -]*?){13,16}\b", "[REDACTED-CARD]", redacted)
    redacted = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)(\S+)", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(access[_ -]?token\s*[:=]\s*)(\S+)", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(private[_ -]?key\s*[:=]\s*)(\S+)", r"\1[REDACTED]", redacted)
    return redacted


def build_policy_bridge() -> PolicyBridge:
    return PolicyBridge()


__all__ = [
    "GuardOutcome",
    "PolicyBridge",
    "PolicyContext",
    "PolicyDecision",
    "build_policy_bridge",
]
