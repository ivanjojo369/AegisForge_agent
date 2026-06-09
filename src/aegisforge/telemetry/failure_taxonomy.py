from __future__ import annotations

"""Failure taxonomy for AegisForge telemetry.

This module keeps the original lightweight public API:

    FailureTaxonomy().classify(error_code="...", message="...")

and extends it with deterministic benchmark/forensic labels for AgentBeats,
OpenEnv, and SkillsBench/BenchFlow post-mortem work.

Compatibility rules:
- Legacy labels are preserved: none, format, policy, budget, routing, tooling,
  recovery, unknown.
- ``classify(...)`` remains keyword-only and accepts ``context`` optionally.
- ``classify_many(...)``, ``most_severe(...)``, ``normalize_label(...)``, and
  ``as_dict(...)`` remain available for scorecards and summaries.
- SkillsBench forensic labels are evaluated before broad telemetry/identity/
  timeout buckets so important scoring-channel symptoms are not hidden.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping
import re


FAILURE_TAXONOMY_VERSION = "failure_taxonomy_v0_4_skillsbench_forensics_2026_06_09"


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

    # SkillsBench / BenchFlow forensic labels.
    SCORING_CHANNEL_MISMATCH = "scoring_channel_mismatch"
    ARTIFACT_REFS_DROPPED = "artifact_refs_dropped"
    FILESYSTEM_NOT_VISIBLE = "filesystem_not_visible"
    FILESYSTEM_OUTPUTS_NOT_SCORED = "filesystem_outputs_not_scored"
    OFFICIAL_RESULT_ZEROED = "official_result_zeroed"
    RESULT_SHAPE_MISMATCH = "result_shape_mismatch"
    TASK_IDENTITY_UNRESOLVED = "task_identity_unresolved"
    SCORE_ELIGIBLE_INCONSISTENT = "score_eligible_inconsistent"
    WORKER_RESULT_TIMEOUT = "worker_result_timeout"


SKILLSBENCH_LABELS: frozenset[FailureLabel] = frozenset(
    {
        FailureLabel.SCORING_CHANNEL_MISMATCH,
        FailureLabel.ARTIFACT_REFS_DROPPED,
        FailureLabel.FILESYSTEM_NOT_VISIBLE,
        FailureLabel.FILESYSTEM_OUTPUTS_NOT_SCORED,
        FailureLabel.OFFICIAL_RESULT_ZEROED,
        FailureLabel.RESULT_SHAPE_MISMATCH,
        FailureLabel.TASK_IDENTITY_UNRESOLVED,
        FailureLabel.SCORE_ELIGIBLE_INCONSISTENT,
        FailureLabel.WORKER_RESULT_TIMEOUT,
    }
)


def _iter_text_parts(value: Any, *, depth: int = 0) -> Iterable[str]:
    """Yield bounded string fragments from nested values."""

    if value is None or depth > 6:
        return

    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _iter_text_parts(child, depth=depth + 1)
        return

    if isinstance(value, (list, tuple, set, frozenset)):
        for child in list(value)[:256]:
            yield from _iter_text_parts(child, depth=depth + 1)
        return

    if isinstance(value, Enum):
        yield str(value.value)
        return

    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            yield from _iter_text_parts(value.as_dict(), depth=depth + 1)
            return
        except Exception:
            pass

    yield str(value)


def _normalize_text(*parts: Any) -> str:
    """Normalize error code/message/context into searchable lowercase text."""

    chunks: list[str] = []
    for part in parts:
        chunks.extend(_iter_text_parts(part))

    text = " ".join(chunks).lower()
    text = text.replace("-", "_")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    return any(pattern in text for pattern in patterns)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "ok", "passed", "success"}
    return bool(value)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class FailureRule:
    label: FailureLabel
    patterns: tuple[str, ...]


SKILLSBENCH_RULES: tuple[FailureRule, ...] = (
    FailureRule(
        FailureLabel.ARTIFACT_REFS_DROPPED,
        (
            "artifact_refs_dropped",
            "artifact refs dropped",
            "artifact_refs is empty",
            "artifact_refs empty",
            "official artifact_refs empty",
            "official artifact_refs is empty",
            "emitted refs but official artifact_refs",
            "aegisforge emitted refs but official artifact_refs",
            "artifact_outputs not preserved as official refs",
            "artifact_outputs_not_preserved_as_official_refs",
        ),
    ),
    FailureRule(
        FailureLabel.FILESYSTEM_OUTPUTS_NOT_SCORED,
        (
            "filesystem_outputs_not_scored",
            "filesystem outputs not scored",
            "filesystem output was not scored",
            "workspace wrote",
            "wrote answer.json but reward is 0",
            "wrote_any_file true reward 0",
            "wrote_any_file=true reward=0",
            "primary_outputs but reward 0",
            "files written but passed false",
        ),
    ),
    FailureRule(
        FailureLabel.FILESYSTEM_NOT_VISIBLE,
        (
            "filesystem_not_visible",
            "filesystem not visible",
            "workspace not visible",
            "workspace_visible false",
            "workspace_visible=false",
            "cannot access task filesystem",
            "isolated a2a container",
            "repo_not_visible",
            "task filesystem unavailable",
        ),
    ),
    FailureRule(
        FailureLabel.OFFICIAL_RESULT_ZEROED,
        (
            "official_result_zeroed",
            "zeroed official result",
            "official result zeroed",
            "official reward zero",
            "official passed false reward 0",
            "reward is 0.0",
            "0/94",
            "0 passed",
            "tasks 0/13 passed",
            "tasks 0/94 passed",
        ),
    ),
    FailureRule(
        FailureLabel.RESULT_SHAPE_MISMATCH,
        (
            "result_shape_mismatch",
            "result shape mismatch",
            "legacy flat result shape",
            "legacy_flat",
            "nested shard shape",
            "nested_shard",
            "mixed result shape",
            "mixed_results",
            "invalid leaderboard shape",
            "validator rejected result shape",
            "invalid_missing_results",
            "invalid_results_not_list",
        ),
    ),
    FailureRule(
        FailureLabel.TASK_IDENTITY_UNRESOLVED,
        (
            "task_identity_unresolved",
            "task identity unresolved",
            "identity confidence zero",
            "identity_confidence 0",
            "canonical task id missing",
            "canonical_task_id missing",
            "task_id was uuid",
            "uuid and canonical task",
            "identity_source unresolved",
            "identity_source=unresolved",
        ),
    ),
    FailureRule(
        FailureLabel.SCORE_ELIGIBLE_INCONSISTENT,
        (
            "score_eligible_inconsistent",
            "score eligible inconsistent",
            "score_eligible false",
            "score_eligible=false",
            "eligible mismatch",
            "score eligibility mismatch",
        ),
    ),
    FailureRule(
        FailureLabel.WORKER_RESULT_TIMEOUT,
        (
            "worker_result_timeout",
            "worker_timeout",
            "worker timeout",
            "result timeout",
            "worker timed out",
            "timeout waiting for worker result",
        ),
    ),
    FailureRule(
        FailureLabel.SCORING_CHANNEL_MISMATCH,
        (
            "scoring_channel_mismatch",
            "scoring channel mismatch",
            "a2a artifacts not connected to scoring",
            "artifact channel mismatch",
            "filesystem_first scorer",
            "filesystem-first scorer",
            "filesystem output primary",
            "filesystem_output_primary",
            "a2a artifact channel diagnostic only",
        ),
    ),
)


GENERAL_RULES: tuple[FailureRule, ...] = (
    FailureRule(
        FailureLabel.ASSERTION,
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
    ),
    FailureRule(
        FailureLabel.TELEMETRY,
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
    ),
    FailureRule(
        FailureLabel.IDENTITY,
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
    ),
    FailureRule(
        FailureLabel.TIMEOUT,
        (
            "timeout",
            "timed_out",
            "deadline",
            "deadline_exceeded",
            "took too long",
            "max_time",
        ),
    ),
    FailureRule(
        FailureLabel.SECRET,
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
    ),
    FailureRule(
        FailureLabel.PRIVACY,
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
    ),
    FailureRule(
        FailureLabel.SUPPLY_CHAIN,
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
    ),
    FailureRule(
        FailureLabel.GUARDRAIL,
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
    ),
    FailureRule(
        FailureLabel.ADAPTER,
        (
            "adapter",
            "context_mapper",
            "policy_bridge",
            "openenv adapter",
            "security adapter",
            "adapter_name",
        ),
    ),
    FailureRule(
        FailureLabel.SCENARIO,
        (
            "scenario",
            "sample_action",
            "payload",
            "registry",
            "domain not found",
            "unknown domain",
            "unknown scenario",
        ),
    ),
    FailureRule(
        FailureLabel.DEPENDENCY,
        (
            "dependency",
            "importerror",
            "modulenotfounderror",
            "package not installed",
            "missing package",
            "version conflict",
        ),
    ),
    FailureRule(
        FailureLabel.ENVIRONMENT,
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
    ),
    FailureRule(
        FailureLabel.IO,
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
    ),
)


@dataclass(frozen=True, slots=True)
class FailureTaxonomy:
    """Rule-based classifier for operational and benchmark failure labels."""

    version: str = FAILURE_TAXONOMY_VERSION

    def classify(
        self,
        *,
        error_code: str | None = None,
        message: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> FailureLabel:
        """Classify an error into a stable ``FailureLabel``.

        ``context`` is optional for newer call sites. Existing callers that only
        pass ``error_code`` and ``message`` keep working.
        """

        code = (error_code or "").lower().replace("-", "_")
        text = _normalize_text(error_code, message, context)

        if not text:
            return FailureLabel.NONE

        structured = self.classify_skillsbench_forensics(context or {})
        if structured is not FailureLabel.NONE:
            return structured

        for rule in SKILLSBENCH_RULES:
            if _contains_any(text, rule.patterns):
                return rule.label

        for rule in GENERAL_RULES:
            if _contains_any(text, rule.patterns):
                return rule.label

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

    def classify_skillsbench_forensics(self, evidence: Mapping[str, Any] | None) -> FailureLabel:
        """Classify structured SkillsBench evidence before substring rules.

        This method is intentionally conservative: it only returns a specific
        SkillsBench label when the structured evidence strongly implies that
        symptom. Otherwise it returns ``FailureLabel.NONE`` and normal rules run.
        """

        if not evidence:
            return FailureLabel.NONE

        label = self.normalize_label(
            evidence.get("failure_label")
            or evidence.get("label")
            or evidence.get("primary_failure_label")
            or evidence.get("primary_anomaly")
            or evidence.get("anomaly_code")
            or evidence.get("code")
        )
        if label in SKILLSBENCH_LABELS:
            return label

        anomaly_codes = evidence.get("anomaly_codes") or evidence.get("anomalies") or evidence.get("errors")
        if isinstance(anomaly_codes, (list, tuple, set, frozenset)):
            labels = [self.normalize_label(item) for item in anomaly_codes]
            selected = self.most_severe(label for label in labels if label in SKILLSBENCH_LABELS)
            if selected in SKILLSBENCH_LABELS:
                return selected

        # Result shape problems are structural and should not fall into FORMAT
        # because they matter specifically to leaderboard/SQL compatibility.
        shape = str(evidence.get("official_shape") or evidence.get("result_shape") or evidence.get("shape") or "").lower()
        validator_compatible = evidence.get("validator_compatible")
        sql_compatible = evidence.get("sql_leaderboard_compatible")
        if shape in {"legacy_flat", "mixed", "invalid_missing_results", "invalid_results_not_list", "invalid_elements"}:
            return FailureLabel.RESULT_SHAPE_MISMATCH
        if validator_compatible is False or sql_compatible is False:
            return FailureLabel.RESULT_SHAPE_MISMATCH

        infra_failure_type = str(evidence.get("infra_failure_type") or evidence.get("infra_failure") or "").lower()
        error_type = str(evidence.get("error_type") or "").lower()
        if "worker" in infra_failure_type and "timeout" in infra_failure_type:
            return FailureLabel.WORKER_RESULT_TIMEOUT
        if "worker_timeout" in error_type or "worker_timeout" in infra_failure_type:
            return FailureLabel.WORKER_RESULT_TIMEOUT

        emitted_refs = _number(
            evidence.get("emitted_artifact_ref_count")
            or evidence.get("artifact_refs_candidate_count")
            or evidence.get("artifact_refs_candidate")
            or 0
        )
        official_refs = _number(evidence.get("official_artifact_ref_count") or evidence.get("artifact_refs_count") or 0)
        if emitted_refs > 0 and official_refs <= 0:
            return FailureLabel.ARTIFACT_REFS_DROPPED

        workspace_visible = evidence.get("workspace_visible")
        if workspace_visible is False:
            return FailureLabel.FILESYSTEM_NOT_VISIBLE

        wrote_any_file = evidence.get("wrote_any_file")
        ok_workspace_writes = _number(evidence.get("ok_workspace_write_count") or evidence.get("workspace_write_count") or 0)
        reward = _number(evidence.get("official_reward") or evidence.get("reward") or 0.0)
        passed = evidence.get("official_passed", evidence.get("passed"))
        if (_truthy(wrote_any_file) or ok_workspace_writes > 0) and not _truthy(passed) and reward <= 0.0:
            return FailureLabel.FILESYSTEM_OUTPUTS_NOT_SCORED

        if (
            evidence.get("canonical_task_id") in {None, "", "unknown", "unresolved"}
            or evidence.get("identity_source") == "unresolved"
            or _number(evidence.get("identity_confidence"), default=1.0) <= 0.0
        ):
            return FailureLabel.TASK_IDENTITY_UNRESOLVED

        if evidence.get("score_eligible") is False and evidence.get("expected_score_eligible") is True:
            return FailureLabel.SCORE_ELIGIBLE_INCONSISTENT
        if evidence.get("score_eligible_inconsistent") is True:
            return FailureLabel.SCORE_ELIGIBLE_INCONSISTENT

        # Use this only when an explicit scoring-channel signal is present; do
        # not label every reward-0 row as a scoring-channel problem.
        channel = _normalize_text(
            evidence.get("scoring_channel"),
            evidence.get("artifact_channel"),
            evidence.get("output_protocol"),
            evidence.get("agent_output_protocol"),
        )
        if (
            "a2a" in channel
            and ("filesystem" in channel or "artifact_refs" in channel or "diagnostic" in channel)
        ):
            return FailureLabel.SCORING_CHANNEL_MISMATCH

        if evidence.get("official_result_zeroed") is True:
            return FailureLabel.OFFICIAL_RESULT_ZEROED

        return FailureLabel.NONE

    def classify_many(
        self,
        errors: Iterable[Mapping[str, Any] | str],
    ) -> FailureLabel:
        """Classify many errors and return the most severe non-none label."""

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

        priority = self.severity_priority()
        normalized = [self.normalize_label(label) for label in labels]
        if not normalized:
            return FailureLabel.NONE
        return max(normalized, key=lambda label: priority.get(label, 0))

    def normalize_label(self, label: FailureLabel | str | None) -> FailureLabel:
        """Normalize arbitrary strings back into a ``FailureLabel``."""

        if label is None:
            return FailureLabel.NONE
        if isinstance(label, FailureLabel):
            return label

        text = str(label).strip().lower().replace("-", "_").replace(" ", "_")
        if not text:
            return FailureLabel.NONE

        for known in FailureLabel:
            if text == known.value or text == known.name.lower():
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
            # SkillsBench aliases.
            "artifact_refs_empty": FailureLabel.ARTIFACT_REFS_DROPPED,
            "artifact_refs_missing": FailureLabel.ARTIFACT_REFS_DROPPED,
            "refs_dropped": FailureLabel.ARTIFACT_REFS_DROPPED,
            "filesystem_scoring_miss": FailureLabel.FILESYSTEM_OUTPUTS_NOT_SCORED,
            "workspace_outputs_not_scored": FailureLabel.FILESYSTEM_OUTPUTS_NOT_SCORED,
            "workspace_not_visible": FailureLabel.FILESYSTEM_NOT_VISIBLE,
            "official_zero": FailureLabel.OFFICIAL_RESULT_ZEROED,
            "zeroed": FailureLabel.OFFICIAL_RESULT_ZEROED,
            "shape_mismatch": FailureLabel.RESULT_SHAPE_MISMATCH,
            "legacy_flat": FailureLabel.RESULT_SHAPE_MISMATCH,
            "mixed_shape": FailureLabel.RESULT_SHAPE_MISMATCH,
            "identity_unresolved": FailureLabel.TASK_IDENTITY_UNRESOLVED,
            "canonical_task_missing": FailureLabel.TASK_IDENTITY_UNRESOLVED,
            "score_eligible_false": FailureLabel.SCORE_ELIGIBLE_INCONSISTENT,
            "worker_timeout": FailureLabel.WORKER_RESULT_TIMEOUT,
            "scoring_mismatch": FailureLabel.SCORING_CHANNEL_MISMATCH,
        }
        return aliases.get(text, FailureLabel.UNKNOWN)

    def severity_priority(self) -> dict[FailureLabel, int]:
        """Return stable severity priorities used by ``most_severe``."""

        return {
            FailureLabel.SECRET: 100,
            FailureLabel.PRIVACY: 95,
            FailureLabel.GUARDRAIL: 90,
            FailureLabel.SUPPLY_CHAIN: 88,
            FailureLabel.POLICY: 85,
            FailureLabel.ASSERTION: 80,
            FailureLabel.WORKER_RESULT_TIMEOUT: 79,
            FailureLabel.TIMEOUT: 75,
            FailureLabel.SCORING_CHANNEL_MISMATCH: 74,
            FailureLabel.FILESYSTEM_NOT_VISIBLE: 73,
            FailureLabel.FILESYSTEM_OUTPUTS_NOT_SCORED: 72,
            FailureLabel.ARTIFACT_REFS_DROPPED: 71,
            FailureLabel.OFFICIAL_RESULT_ZEROED: 70,
            FailureLabel.BUDGET: 70,
            FailureLabel.TOOLING: 65,
            FailureLabel.ADAPTER: 62,
            FailureLabel.ROUTING: 60,
            FailureLabel.RESULT_SHAPE_MISMATCH: 59,
            FailureLabel.SCENARIO: 58,
            FailureLabel.SCORE_ELIGIBLE_INCONSISTENT: 57,
            FailureLabel.TASK_IDENTITY_UNRESOLVED: 56,
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

    def as_dict(self) -> dict[str, str]:
        """Return available labels as a simple mapping for UIs/docs."""

        return {label.name: label.value for label in FailureLabel}

    def skillsbench_labels(self) -> tuple[str, ...]:
        """Return SkillsBench forensic label values in deterministic order."""

        return tuple(label.value for label in FailureLabel if label in SKILLSBENCH_LABELS)


def validate_failure_taxonomy_selftest() -> dict[str, Any]:
    """Dependency-free smoke test for local validation."""

    taxonomy = FailureTaxonomy()

    cases: dict[str, tuple[str, str]] = {
        "artifact_refs_dropped": (
            "artifact_refs_dropped",
            "AegisForge emitted refs but official artifact_refs is empty",
        ),
        "filesystem_outputs_not_scored": (
            "filesystem_outputs_not_scored",
            "workspace wrote answer.json but reward is 0.0",
        ),
        "result_shape_mismatch": (
            "result_shape_mismatch",
            "legacy flat result shape rejected by validator",
        ),
        "task_identity_unresolved": (
            "task_identity_unresolved",
            "request task_id was UUID and canonical task id was missing",
        ),
        "worker_result_timeout": (
            "worker_timeout",
            "worker timed out while waiting for result",
        ),
    }

    errors: list[str] = []
    for expected, (code, message) in cases.items():
        actual = taxonomy.classify(error_code=code, message=message)
        if actual.value != expected:
            errors.append(f"{code}: expected {expected}, got {actual.value}")

    structured = taxonomy.classify(
        context={
            "artifact_refs_candidate_count": 2,
            "official_artifact_ref_count": 0,
            "passed": False,
            "reward": 0.0,
        }
    )
    if structured is not FailureLabel.ARTIFACT_REFS_DROPPED:
        errors.append(f"structured refs case: got {structured.value}")

    required = {
        "scoring_channel_mismatch",
        "artifact_refs_dropped",
        "filesystem_not_visible",
        "filesystem_outputs_not_scored",
        "official_result_zeroed",
        "result_shape_mismatch",
        "task_identity_unresolved",
        "score_eligible_inconsistent",
        "worker_result_timeout",
    }
    observed = {label.value for label in FailureLabel}
    missing = sorted(required - observed)
    if missing:
        errors.append(f"missing labels: {missing}")

    return {
        "ok": not errors,
        "version": FAILURE_TAXONOMY_VERSION,
        "errors": errors,
        "label_count": len(FailureLabel),
        "skillsbench_labels": taxonomy.skillsbench_labels(),
    }
