from __future__ import annotations

"""Output sanitizer for omnibench_aegis_env.

This guard focuses on *outgoing* content. It is designed to reduce risk from
active markup, executable snippets, obvious secret disclosure, and unsafe link
schemes while preserving as much useful text as possible.

Design goals:
- neutralize browser-executable output such as HTML/JS/SVG payloads,
- redact obvious secrets and credential-like strings,
- keep normal code examples and benign text usable,
- expose a stable ``evaluate(...)`` interface for ``policy_bridge.py``.
"""

from dataclasses import dataclass, field
import html
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlparse

try:  # pragma: no cover - import fallback for direct execution/debugging
    from .policy_bridge import GuardOutcome, PolicyContext
except Exception:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).resolve().parent))
    from policy_bridge import GuardOutcome, PolicyContext  # type: ignore

JsonDict = Dict[str, Any]


_ACTIVE_RULES: tuple[tuple[str, str, str], ...] = (
    (r"<script\b", "high", "script_tag"),
    (r"</script>", "medium", "script_end_tag"),
    (r"javascript:\s*", "high", "javascript_scheme"),
    (r"data:text/html", "high", "html_data_uri"),
    (r"<iframe\b", "high", "iframe_tag"),
    (r"<object\b", "high", "object_tag"),
    (r"<embed\b", "high", "embed_tag"),
    (r"<svg\b", "medium", "svg_tag"),
    (r"\bon(?:load|error|click|mouseover|focus|submit)\s*=", "high", "inline_event_handler"),
    (r"document\.cookie", "high", "cookie_access"),
    (r"window\.location", "medium", "window_location_access"),
    (r"eval\s*\(", "high", "eval_call"),
    (r"new\s+Function\s*\(", "high", "function_constructor"),
)

_SECRET_RULES: tuple[tuple[str, str, str], ...] = (
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "critical", "private_key_block"),
    (r"(?i)(api[_ -]?key\s*[:=]\s*)([^\s'\"`]+)", "high", "api_key_assignment"),
    (r"(?i)(access[_ -]?token\s*[:=]\s*)([^\s'\"`]+)", "high", "access_token_assignment"),
    (r"(?i)(secret\s*[:=]\s*)([^\s'\"`]+)", "medium", "secret_assignment"),
    (r"(?i)(authorization\s*:\s*bearer\s+)([^\s]+)", "high", "bearer_token"),
)

_URL_RULES: tuple[tuple[str, str, str], ...] = (
    (r"\bfile://[^\s)\]>]+", "high", "file_scheme_link"),
    (r"\bjavascript:[^\s)\]>]+", "high", "javascript_link"),
    (r"\bdata:text/html[^\s)\]>]+", "high", "data_html_link"),
)

_SUSPICIOUS_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "0.0.0.0", "::1"})


@dataclass(slots=True)
class Detection:
    pattern: str
    label: str
    severity: str
    span: tuple[int, int]
    excerpt: str

    def to_dict(self) -> JsonDict:
        return {
            "pattern": self.pattern,
            "label": self.label,
            "severity": self.severity,
            "span": list(self.span),
            "excerpt": self.excerpt,
        }


@dataclass(slots=True)
class OutputSanitizer:
    """Guard that sanitizes risky outgoing content."""

    block_threshold: float = 1.0
    sanitize_threshold: float = 0.18
    active_rules: Sequence[tuple[str, str, str]] = field(default_factory=lambda: _ACTIVE_RULES)
    secret_rules: Sequence[tuple[str, str, str]] = field(default_factory=lambda: _SECRET_RULES)
    url_rules: Sequence[tuple[str, str, str]] = field(default_factory=lambda: _URL_RULES)
    name: str = "output_sanitizer"

    def evaluate(
        self,
        *,
        text: Optional[str] = None,
        artifact: Optional[Mapping[str, Any]] = None,
        action: Optional[Mapping[str, Any]] = None,
        context: Optional[PolicyContext] = None,
    ) -> GuardOutcome:
        context = context or PolicyContext()
        combined = self._combine_sources(text=text, artifact=artifact, action=action)
        findings = self.detect(combined)
        if not findings:
            return GuardOutcome(
                guard_name=self.name,
                ok=True,
                metadata={"score": 0.0, "domain": context.domain},
            )

        score = self.score_findings(findings)
        reasons = [f"{item.label}: {item.excerpt}" for item in findings]
        severity = self._score_to_severity(score)
        metadata: JsonDict = {
            "score": score,
            "domain": context.domain,
            "scenario_id": context.scenario_id,
            "phase": context.phase,
            "matches": [item.to_dict() for item in findings],
        }

        sanitized_text = None
        if text is not None and score >= self.sanitize_threshold:
            sanitized_text = self.sanitize_text(text)

        sanitized_artifact = None
        if artifact is not None and score >= self.sanitize_threshold:
            sanitized_artifact = self.sanitize_artifact(artifact)

        if score >= self.block_threshold:
            return GuardOutcome(
                guard_name=self.name,
                ok=False,
                severity=severity,
                action="block",
                reasons=reasons,
                sanitized_text=sanitized_text,
                sanitized_artifact=sanitized_artifact,
                metadata=metadata,
            )

        if sanitized_text is not None or sanitized_artifact is not None:
            return GuardOutcome(
                guard_name=self.name,
                ok=False,
                severity=severity,
                action="sanitize",
                reasons=reasons,
                sanitized_text=sanitized_text,
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

    def detect(self, text: str) -> list[Detection]:
        if not text.strip():
            return []

        findings: list[Detection] = []
        findings.extend(self._detect_by_rules(text, self.active_rules))
        findings.extend(self._detect_by_rules(text, self.secret_rules))
        findings.extend(self._detect_by_rules(text, self.url_rules))
        findings.extend(self._detect_suspicious_hosts(text))
        return _dedupe_findings(findings)

    def sanitize_text(self, text: str) -> str:
        if not text:
            return text

        sanitized = text
        sanitized = self._redact_secrets(sanitized)
        sanitized = self._neutralize_unsafe_urls(sanitized)
        sanitized = self._sanitize_active_markup(sanitized)
        return sanitized

    def sanitize_artifact(self, artifact: Mapping[str, Any]) -> JsonDict:
        sanitized: JsonDict = {}
        for key, value in artifact.items():
            sanitized[str(key)] = self._sanitize_value(value)
        sanitized["output_sanitized"] = True
        return sanitized

    def score_findings(self, findings: Sequence[Detection]) -> float:
        if not findings:
            return 0.0
        total = 0.0
        for item in findings:
            total += _severity_weight(item.severity)
        labels = {item.label for item in findings}
        if any("private_key" in label or "token" in label or "api_key" in label for label in labels):
            total += 0.20
        if any("script" in label or "event_handler" in label or "javascript" in label for label in labels):
            total += 0.15
        return min(total, 1.0)

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.sanitize_text(value)
        if isinstance(value, Mapping):
            return {str(k): self._sanitize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        return value

    def _redact_secrets(self, text: str) -> str:
        redacted = text
        redacted = re.sub(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            "[REDACTED-PRIVATE-KEY]",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)([^\s'\"`]+)", r"\1[REDACTED]", redacted)
        redacted = re.sub(r"(?i)(access[_ -]?token\s*[:=]\s*)([^\s'\"`]+)", r"\1[REDACTED]", redacted)
        redacted = re.sub(r"(?i)(secret\s*[:=]\s*)([^\s'\"`]+)", r"\1[REDACTED]", redacted)
        redacted = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)([^\s]+)", r"\1[REDACTED]", redacted)
        return redacted

    def _neutralize_unsafe_urls(self, text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            raw = match.group(0)
            lowered = raw.lower()
            if lowered.startswith("javascript:"):
                return "[BLOCKED-JAVASCRIPT-URL]"
            if lowered.startswith("data:text/html"):
                return "[BLOCKED-DATA-HTML-URL]"
            if lowered.startswith("file://"):
                return "[BLOCKED-FILE-URL]"
            return raw

        neutralized = re.sub(r"\b(?:javascript:|data:text/html|file://)[^\s)\]>]+", replace, text, flags=re.IGNORECASE)

        # Also neutralize explicitly parsed local-only http/https URLs.
        def maybe_rewrite_http_url(match: re.Match[str]) -> str:
            raw = match.group(0)
            try:
                parsed = urlparse(raw)
            except Exception:
                return raw
            host = (parsed.hostname or "").lower()
            if host in _SUSPICIOUS_HOSTS:
                return f"[LOCAL-URL:{host}]"
            return raw

        neutralized = re.sub(r"https?://[^\s)\]>]+", maybe_rewrite_http_url, neutralized, flags=re.IGNORECASE)
        return neutralized

    def _sanitize_active_markup(self, text: str) -> str:
        lower = text.lower()
        has_active_markup = any(
            re.search(pattern, lower, flags=re.IGNORECASE)
            for pattern, _severity, _label in self.active_rules
        )
        if not has_active_markup:
            return text

        # Escape the whole payload once it is clearly active markup-heavy.
        escaped = html.escape(text)
        return f"[SANITIZED-ACTIVE-CONTENT]\n{escaped}"

    def _detect_by_rules(
        self,
        text: str,
        rules: Sequence[tuple[str, str, str]],
    ) -> list[Detection]:
        findings: list[Detection] = []
        for pattern, severity, label in rules:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                findings.append(
                    Detection(
                        pattern=pattern,
                        label=label,
                        severity=severity,
                        span=(match.start(), match.end()),
                        excerpt=_excerpt(text, match.start(), match.end()),
                    )
                )
        return findings

    def _detect_suspicious_hosts(self, text: str) -> list[Detection]:
        findings: list[Detection] = []
        for match in re.finditer(r"https?://[^\s)\]>]+", text, flags=re.IGNORECASE):
            raw = match.group(0)
            try:
                parsed = urlparse(raw)
            except Exception:
                continue
            host = (parsed.hostname or "").lower()
            if host in _SUSPICIOUS_HOSTS:
                findings.append(
                    Detection(
                        pattern=r"https?://...local-host",
                        label="local_service_url",
                        severity="medium",
                        span=(match.start(), match.end()),
                        excerpt=_excerpt(text, match.start(), match.end()),
                    )
                )
        return findings

    @staticmethod
    def _combine_sources(
        *,
        text: Optional[str],
        artifact: Optional[Mapping[str, Any]],
        action: Optional[Mapping[str, Any]],
    ) -> str:
        parts: list[str] = []
        if text:
            parts.append(str(text))
        if artifact:
            parts.append(json.dumps(dict(artifact), ensure_ascii=False, sort_keys=True, default=str))
        if action:
            parts.append(json.dumps(dict(action), ensure_ascii=False, sort_keys=True, default=str))
        return "\n".join(parts).strip()

    @staticmethod
    def _score_to_severity(score: float) -> str:
        if score >= 1.0:
            return "critical"
        if score >= 0.60:
            return "high"
        if score >= 0.25:
            return "medium"
        return "low"



def _severity_weight(severity: str) -> float:
    normalized = str(severity or "low").strip().lower()
    if normalized == "critical":
        return 0.65
    if normalized == "high":
        return 0.35
    if normalized == "medium":
        return 0.18
    return 0.08



def _excerpt(text: str, start: int, end: int, radius: int = 48) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    excerpt = text[lo:hi].replace("\n", " ").strip()
    return excerpt[:160]



def _dedupe_findings(findings: Sequence[Detection]) -> list[Detection]:
    deduped: list[Detection] = []
    seen: set[tuple[str, tuple[int, int]]] = set()
    for item in findings:
        marker = (item.label, item.span)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return deduped



def build_guard() -> OutputSanitizer:
    return OutputSanitizer()


__all__ = [
    "Detection",
    "OutputSanitizer",
    "build_guard",
]
