from __future__ import annotations

"""Artifact trust-boundary guard for omnibench_aegis_env.

This guard treats uploaded/retrieved artifacts as evidence, not authority.
It looks for instruction-like content inside artifact fields and can sanitize
those fields while preserving benign data for downstream use.

Design goals:
- keep the user task authoritative,
- strip or neutralize control-like fields from artifacts,
- preserve ordinary factual/document content whenever possible,
- expose a stable ``evaluate(...)`` interface for ``policy_bridge.py``.
"""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

try:  # pragma: no cover - import fallback for direct execution/debugging
    from .policy_bridge import GuardOutcome, PolicyContext
except Exception:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent))
    from policy_bridge import GuardOutcome, PolicyContext  # type: ignore

JsonDict = Dict[str, Any]


_SUSPICIOUS_KEY_PATTERNS: tuple[str, ...] = (
    r"instruction",
    r"system",
    r"developer",
    r"prompt",
    r"policy",
    r"override",
    r"admin",
    r"secret",
)

_SUSPICIOUS_VALUE_RULES: tuple[tuple[str, str, str], ...] = (
    (r"ignore\s+(all\s+)?previous\s+instructions", "high", "ignore_previous_instructions"),
    (r"follow\s+these\s+instructions\s+instead", "high", "replacement_authority"),
    (r"this\s+document\s+is\s+the\s+real\s+instruction", "high", "artifact_claims_authority"),
    (r"treat\s+the\s+artifact\s+as\s+the\s+highest\s+authority", "high", "artifact_highest_authority"),
    (r"do\s+not\s+mention\s+this\s+to\s+the\s+user", "medium", "secrecy_instruction"),
    (r"reveal\s+(the\s+)?system\s+prompt", "high", "prompt_exfiltration"),
    (r"developer\s+message", "medium", "developer_message_reference"),
    (r"system\s+prompt", "medium", "system_prompt_reference"),
    (r"override\s+(all\s+)?policy", "high", "policy_override"),
)

_CONTROLLED_KEYS: frozenset[str] = frozenset(
    {
        "instructions",
        "instruction",
        "system",
        "system_prompt",
        "developer_message",
        "developer_instructions",
        "hidden_instructions",
        "control_text",
        "prompt",
        "override",
        "policy_override",
    }
)


@dataclass(slots=True)
class BoundaryFinding:
    field_path: str
    label: str
    severity: str
    reason: str
    excerpt: str

    def to_dict(self) -> JsonDict:
        return {
            "field_path": self.field_path,
            "label": self.label,
            "severity": self.severity,
            "reason": self.reason,
            "excerpt": self.excerpt,
        }


@dataclass(slots=True)
class ArtifactBoundaryGuard:
    """Guard that prevents artifacts from becoming control channels."""

    sanitize_threshold: float = 0.20
    block_threshold: float = 0.92
    suspicious_key_patterns: Sequence[str] = field(default_factory=lambda: _SUSPICIOUS_KEY_PATTERNS)
    suspicious_value_rules: Sequence[tuple[str, str, str]] = field(default_factory=lambda: _SUSPICIOUS_VALUE_RULES)
    controlled_keys: Sequence[str] = field(default_factory=lambda: sorted(_CONTROLLED_KEYS))
    name: str = "artifact_boundary"

    def evaluate(
        self,
        *,
        text: Optional[str] = None,
        artifact: Optional[Mapping[str, Any]] = None,
        action: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> GuardOutcome:
        context = context or PolicyContext()
        if not artifact:
            return GuardOutcome(guard_name=self.name, ok=True, metadata={"score": 0.0, "domain": context.domain})

        findings = self.inspect_artifact(artifact)
        if not findings:
            return GuardOutcome(guard_name=self.name, ok=True, metadata={"score": 0.0, "domain": context.domain})

        score = self.score_findings(findings)
        reasons = [f"{item.field_path}: {item.label} ({item.reason})" for item in findings]
        severity = self._score_to_severity(score)
        sanitized_artifact = self.sanitize_artifact(artifact)
        metadata: JsonDict = {
            "score": score,
            "domain": context.domain,
            "scenario_id": context.scenario_id,
            "phase": context.phase,
            "matches": [item.to_dict() for item in findings],
        }

        if score >= self.block_threshold:
            return GuardOutcome(
                guard_name=self.name,
                ok=False,
                severity=severity,
                action="block",
                reasons=reasons,
                sanitized_artifact=sanitized_artifact,
                metadata=metadata,
            )

        if score >= self.sanitize_threshold:
            return GuardOutcome(
                guard_name=self.name,
                ok=False,
                severity=severity,
                action="sanitize",
                reasons=reasons,
                sanitized_artifact=sanitized_artifact,
                metadata=metadata,
            )

        return GuardOutcome(
            guard_name=self.name,
            ok=False,
            severity=severity,
            action="flag",
            reasons=reasons,
            metadata=metadata,
        )

    def inspect_artifact(self, artifact: Mapping[str, Any]) -> list[BoundaryFinding]:
        findings: list[BoundaryFinding] = []
        for path, value in _walk_mapping(artifact):
            key = path.split(".")[-1].lower()
            value_text = _coerce_text(value)

            if self._is_controlled_key(key):
                findings.append(
                    BoundaryFinding(
                        field_path=path,
                        label="controlled_key",
                        severity="high",
                        reason="artifact key looks like control/instruction channel",
                        excerpt=value_text[:160],
                    )
                )

            for pattern, severity, label in self.suspicious_value_rules:
                if value_text and re.search(pattern, value_text, flags=re.IGNORECASE):
                    findings.append(
                        BoundaryFinding(
                            field_path=path,
                            label=label,
                            severity=severity,
                            reason=f"matched pattern: {pattern}",
                            excerpt=_excerpt(value_text),
                        )
                    )
        return _dedupe_findings(findings)

    def score_findings(self, findings: Sequence[BoundaryFinding]) -> float:
        if not findings:
            return 0.0
        total = 0.0
        controlled_key_hits = 0
        for item in findings:
            total += _severity_weight(item.severity)
            if item.label == "controlled_key":
                controlled_key_hits += 1
        if controlled_key_hits >= 1:
            total += 0.12
        if _has_multi_signal_escalation(findings):
            total += 0.15
        return min(total, 1.0)

    def sanitize_artifact(self, artifact: Mapping[str, Any]) -> JsonDict:
        sanitized = _sanitize_value(dict(artifact), controlled_keys={key.lower() for key in self.controlled_keys}, key_patterns=self.suspicious_key_patterns, value_patterns=[rule[0] for rule in self.suspicious_value_rules])
        if isinstance(sanitized, dict):
            sanitized["trusted_instruction_fields_removed"] = True
            return sanitized
        return {"artifact": sanitized, "trusted_instruction_fields_removed": True}

    def _is_controlled_key(self, key: str) -> bool:
        if key in {item.lower() for item in self.controlled_keys}:
            return True
        for pattern in self.suspicious_key_patterns:
            if re.search(pattern, key, flags=re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _score_to_severity(score: float) -> str:
        if score >= 0.95:
            return "critical"
        if score >= 0.60:
            return "high"
        if score >= 0.25:
            return "medium"
        return "low"


def _walk_mapping(value: Any, prefix: str = "artifact") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{prefix}.{key}"
            yield child_path, child
            yield from _walk_mapping(child, prefix=child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{prefix}[{index}]"
            yield child_path, child
            yield from _walk_mapping(child, prefix=child_path)


def _sanitize_value(
    value: Any,
    *,
    controlled_keys: set[str],
    key_patterns: Sequence[str],
    value_patterns: Sequence[str],
) -> Any:
    if isinstance(value, Mapping):
        result: JsonDict = {}
        for raw_key, raw_child in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered in controlled_keys or any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in key_patterns):
                continue
            cleaned_child = _sanitize_value(
                raw_child,
                controlled_keys=controlled_keys,
                key_patterns=key_patterns,
                value_patterns=value_patterns,
            )
            if _value_is_suspicious(cleaned_child, value_patterns):
                continue
            result[key] = cleaned_child
        return result

    if isinstance(value, list):
        cleaned_list: list[Any] = []
        for child in value:
            cleaned_child = _sanitize_value(
                child,
                controlled_keys=controlled_keys,
                key_patterns=key_patterns,
                value_patterns=value_patterns,
            )
            if _value_is_suspicious(cleaned_child, value_patterns):
                continue
            cleaned_list.append(cleaned_child)
        return cleaned_list

    if _value_is_suspicious(value, value_patterns):
        return "[SANITIZED]"
    return value


def _value_is_suspicious(value: Any, value_patterns: Sequence[str]) -> bool:
    text = _coerce_text(value)
    if not text:
        return False
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in value_patterns)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, Mapping):
        return " ".join(f"{k}={_coerce_text(v)}" for k, v in value.items())
    if isinstance(value, list):
        return " ".join(_coerce_text(item) for item in value)
    return str(value)


def _severity_weight(severity: str) -> float:
    normalized = str(severity or "low").strip().lower()
    if normalized == "critical":
        return 0.55
    if normalized == "high":
        return 0.35
    if normalized == "medium":
        return 0.18
    return 0.08


def _has_multi_signal_escalation(findings: Sequence[BoundaryFinding]) -> bool:
    labels = {item.label for item in findings}
    has_authority = any("authority" in label or "controlled" in label for label in labels)
    has_override = any("override" in label or "ignore" in label for label in labels)
    has_exfil = any("prompt" in label or "system" in label for label in labels)
    return sum([has_authority, has_override, has_exfil]) >= 2


def _excerpt(text: str, radius: int = 160) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:radius]


def _dedupe_findings(findings: Sequence[BoundaryFinding]) -> list[BoundaryFinding]:
    deduped: list[BoundaryFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for item in findings:
        marker = (item.field_path, item.label, item.excerpt)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return deduped


def build_guard() -> ArtifactBoundaryGuard:
    return ArtifactBoundaryGuard()
