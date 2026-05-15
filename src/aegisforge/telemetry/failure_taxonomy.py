from __future__ import annotations

"""Failure taxonomy for AegisForge telemetry.

The original taxonomy classified a small set of operational failures:

    none, format, policy, budget, routing, tooling, recovery, unknown

Sprint 4 / AgentBeats Purple Benchmark reporting needs more diagnostic labels
without breaking existing callers. The public call remains:

    FailureTaxonomy().classify(error_code="...", message="...")

The legacy labels are preserved, while additional labels help scorecards and
episode summaries distinguish telemetry/identity issues from safety, privacy,
assertion, and benchmark-adapter failures.
"""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Iterable, Mapping


class FailureLabel(str, Enum):
    # Legacy labels. Do not remove or rename.
    NONE = "none"
    FORMAT = "format"
    POLICY = "policy"
    BUDGET = "budget"
    ROUTING = "routing"
    TOOLING = "tooling"
    RECOVERY = "recovery"
    UNKNOWN = "unknown"

    # Sprint 4 / benchmark diagnostics.
    IDENTITY = "identity"
    TELEMETRY = "telemetry"
    ASSERTION = "assertion"
    ADAPTER = "adapter"
    SCENARIO = "scenario"
    GUARDRAIL = "guardrail"
    PRIVACY = "privacy"
    SECRET = "secret"
    SUPPLY_CHAIN = "supply_chain"
    TIMEOUT = "timeout"
    ENVIRONMENT = "environment"
    DEPENDENCY = "dependency"
    IO = "io"


def _normalize_text(*parts: Any) -> str:
    """Normalize error code/message/context into a searchable lowercase string."""

    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, Mapping):
            for key, value in part.items():
                chunks.append(str(key))
                chunks.append(str(value))
            continue
        if isinstance(part, (list, tuple, set)):
            chunks.extend(str(item) for item in part if item is not None)
            continue
        chunks.append(str(part))

    text = " ".join(chunks).lower()
    text = text.replace("-", "_")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    return any(pattern in text for pattern in patterns)


@dataclass(frozen=True, slots=True)
class FailureTaxonomy:
    """Rule-based classifier for operational and benchmark failure labels.

    The classifier is intentionally conservative and deterministic so that
    scorecards remain reproducible across local smoke tests and benchmark runs.
    """

    def classify(
        self,
        *,
        error_code: str | None = None,
        message: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> FailureLabel:
        """Classify an error into a stable FailureLabel.

        ``context`` is optional for newer call sites. Existing callers that only
        pass ``error_code`` and ``message`` keep working.
        """

        code = (error_code or "").lower().replace("-", "_")
        text = _normalize_text(error_code, message, context)

        if not text:
            return FailureLabel.NONE

        # Specific labels first. These are useful for Sprint 4 scorecards.
        if _contains_any(
            text,
            (
                "assertion",
                "nl_assertion",
                "action_check",
                "db_check",
                "expected_action",
                "expected_tool_call",
                "evaluation_failed",
                "judge_failed",
                "verdict_failed",
            ),
        ):
            return FailureLabel.ASSERTION

        if _contains_any(
            text,
            (
                "trace_schema",
                "episode_summary",
                "scorecard",
                "telemetry",
                "trace_id",
                "duration_ms",
                "missing_trace",
                "event_log",
                "compact_dict",
            ),
        ):
            return FailureLabel.TELEMETRY

        if _contains_any(
            text,
            (
                "scenario_id",
                "scenario_name",
                "scenario_family",
                "upstream_track",
                "selected_opponent",
                "benchmark_track",
                "missing_identity",
                "identity",
            ),
        ):
            return FailureLabel.IDENTITY

        if _contains_any(
            text,
            (
                "timeout",
                "timed_out",
                "deadline",
                "deadline_exceeded",
                "took too long",
                "max_time",
            ),
        ):
            return FailureLabel.TIMEOUT

        if _contains_any(
            text,
            (
                "secret",
                "private_key",
                "api_key",
                "credential",
                "password",
                "token leak",
                "leaked token",
                "exfiltrate secret",
                "secret_leak",
            ),
        ):
            return FailureLabel.SECRET

        if _contains_any(
            text,
            (
                "pii",
                "privacy",
                "private information",
                "personal data",
                "deanonym",
                "de_anonym",
                "anonym",
                "redaction",
                "hipaa",
                "phi",
                "confidential user",
            ),
        ):
            return FailureLabel.PRIVACY

        if _contains_any(
            text,
            (
                "supply_chain",
                "supply chain",
                "malicious package",
                "malicious dependency",
                "dependency confusion",
                "poisoned package",
                "docker image",
                "untrusted image",
            ),
        ):
            return FailureLabel.SUPPLY_CHAIN

        if _contains_any(
            text,
            (
                "guardrail",
                "jailbreak",
                "prompt injection",
                "indirect injection",
                "insecure output",
                "unsafe output",
                "safety boundary",
                "benchmark_only",
                "controlled_only",
            ),
        ):
            return FailureLabel.GUARDRAIL

        if _contains_any(
            text,
            (
                "adapter",
                "context_mapper",
                "policy_bridge",
                "openenv adapter",
                "security adapter",
                "adapter_name",
            ),
        ):
            return FailureLabel.ADAPTER

        if _contains_any(
            text,
            (
                "scenario",
                "sample_action",
                "payload",
                "registry",
                "domain not found",
                "unknown domain",
                "unknown scenario",
            ),
        ):
            return FailureLabel.SCENARIO

        if _contains_any(
            text,
            (
                "dependency",
                "importerror",
                "modulenotfounderror",
                "package not installed",
                "missing package",
                "version conflict",
            ),
        ):
            return FailureLabel.DEPENDENCY

        if _contains_any(
            text,
            (
                "environment",
                "env var",
                "environment variable",
                "port",
                "server unavailable",
                "connection refused",
                "connection error",
                "docker",
                "container",
                "subprocess",
            ),
        ):
            return FailureLabel.ENVIRONMENT

        if _contains_any(
            text,
            (
                "file not found",
                "filenotfounderror",
                "permission denied",
                "permissionerror",
                "ioerror",
                "oserror",
                "read failed",
                "write failed",
                "path",
            ),
        ):
            return FailureLabel.IO

        # Legacy labels and compatibility behavior.
        if "format" in code or _contains_any(text, ("schema", "json", "parse", "serialization", "deserialize")):
            return FailureLabel.FORMAT

        if "policy" in code or _contains_any(
            text,
            (
                "policy",
                "unsafe",
                "blocked",
                "disallowed",
                "refusal",
                "restricted",
                "not allowed",
            ),
        ):
            return FailureLabel.POLICY

        if "budget" in code or _contains_any(
            text,
            (
                "budget",
                "token",
                "limit",
                "quota",
                "rate limit",
                "hard limit",
                "near limit",
                "max_tokens",
            ),
        ):
            return FailureLabel.BUDGET

        if "route" in code or _contains_any(
            text,
            (
                "routing",
                "router",
                "route",
                "classifier",
                "track selection",
                "profile selection",
            ),
        ):
            return FailureLabel.ROUTING

        if "tool" in code or _contains_any(
            text,
            (
                "tool",
                "tool_call",
                "lookup",
                "probe",
                "function call",
                "external call",
                "api call",
            ),
        ):
            return FailureLabel.TOOLING

        if "recovery" in code or _contains_any(
            text,
            (
                "retry",
                "fallback",
                "fall back",
                "recover",
                "recovery",
                "self repair",
                "repair",
            ),
        ):
            return FailureLabel.RECOVERY

        return FailureLabel.UNKNOWN

    def classify_many(
        self,
        errors: Iterable[Mapping[str, Any] | str],
    ) -> FailureLabel:
        """Classify many errors and return the most severe non-none label.

        This is useful when a runner collected several warnings/errors but the
        scorecard needs a single failure label.
        """

        labels: list[FailureLabel] = []
        for item in errors:
            if isinstance(item, Mapping):
                labels.append(
                    self.classify(
                        error_code=str(item.get("error_code") or item.get("code") or ""),
                        message=str(item.get("message") or item.get("error") or ""),
                        context=item.get("context") if isinstance(item.get("context"), Mapping) else item,
                    )
                )
            else:
                labels.append(self.classify(message=str(item)))

        return self.most_severe(labels)

    def most_severe(self, labels: Iterable[FailureLabel | str]) -> FailureLabel:
        """Return the highest-priority label from a collection."""

        priority = {
            FailureLabel.SECRET: 100,
            FailureLabel.PRIVACY: 95,
            FailureLabel.GUARDRAIL: 90,
            FailureLabel.SUPPLY_CHAIN: 88,
            FailureLabel.POLICY: 85,
            FailureLabel.ASSERTION: 80,
            FailureLabel.TIMEOUT: 75,
            FailureLabel.BUDGET: 70,
            FailureLabel.TOOLING: 65,
            FailureLabel.ADAPTER: 62,
            FailureLabel.ROUTING: 60,
            FailureLabel.SCENARIO: 58,
            FailureLabel.IDENTITY: 55,
            FailureLabel.TELEMETRY: 50,
            FailureLabel.FORMAT: 45,
            FailureLabel.ENVIRONMENT: 40,
            FailureLabel.DEPENDENCY: 35,
            FailureLabel.IO: 30,
            FailureLabel.RECOVERY: 20,
            FailureLabel.UNKNOWN: 10,
            FailureLabel.NONE: 0,
        }

        normalized = [self.normalize_label(label) for label in labels]
        if not normalized:
            return FailureLabel.NONE
        return max(normalized, key=lambda label: priority.get(label, 0))

    def normalize_label(self, label: FailureLabel | str | None) -> FailureLabel:
        """Normalize arbitrary strings back into a FailureLabel."""

        if label is None:
            return FailureLabel.NONE
        if isinstance(label, FailureLabel):
            return label

        text = str(label).strip().lower().replace("-", "_")
        if not text:
            return FailureLabel.NONE

        for known in FailureLabel:
            if text == known.value:
                return known

        aliases = {
            "safe_policy": FailureLabel.POLICY,
            "safety": FailureLabel.GUARDRAIL,
            "security": FailureLabel.GUARDRAIL,
            "injection": FailureLabel.GUARDRAIL,
            "prompt_injection": FailureLabel.GUARDRAIL,
            "indirect_injection": FailureLabel.GUARDRAIL,
            "pii_leak": FailureLabel.PRIVACY,
            "data_leak": FailureLabel.PRIVACY,
            "secret_leak": FailureLabel.SECRET,
            "key_leak": FailureLabel.SECRET,
            "supplychain": FailureLabel.SUPPLY_CHAIN,
            "supply_chain_attack": FailureLabel.SUPPLY_CHAIN,
            "eval": FailureLabel.ASSERTION,
            "evaluation": FailureLabel.ASSERTION,
            "judge": FailureLabel.ASSERTION,
            "trace": FailureLabel.TELEMETRY,
            "metadata": FailureLabel.IDENTITY,
            "benchmark_identity": FailureLabel.IDENTITY,
            "env": FailureLabel.ENVIRONMENT,
            "file": FailureLabel.IO,
        }
        return aliases.get(text, FailureLabel.UNKNOWN)

    def as_dict(self) -> dict[str, str]:
        """Return available labels as a simple mapping for UIs/docs."""

        return {label.name: label.value for label in FailureLabel}
