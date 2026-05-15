from __future__ import annotations

"""Scoring helpers for AegisForge Eval.

The original scorer was intentionally tiny and only supported held-out cases:

    HeldoutScorer().score(case_id=..., status=..., warnings=..., fallback_used=...)

That API remains compatible. Sprint 4 adds scoring helpers for TrackResult-like
objects so report/runner code can preserve and aggregate benchmark identity:

    domain, scenario_id, scenario_name, upstream_track, category, adapter,
    assessment_mode, scenario_family, benchmark, selected_opponent, source_url

This module is deliberately lightweight and deterministic. It does not try to
replace official benchmark scoring; it provides local hints for smoke tests,
preview reports, and metadata sanity checks.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


JsonDict = dict[str, Any]


BENCHMARK_NAME = "AgentX-AgentBeats Phase 2 Sprint 4"
DEFAULT_ASSESSMENT_MODE = "purple_benchmark"
DEFAULT_SCENARIO_FAMILY = "agentbeats_sprint4"

IDENTITY_KEYS: tuple[str, ...] = (
    "domain",
    "scenario_id",
    "scenario_name",
    "upstream_track",
    "category",
    "adapter",
    "assessment_mode",
    "scenario_family",
    "benchmark",
    "selected_opponent",
    "source_url",
)

SUCCESS_STATUSES: set[str] = {
    "pass",
    "passed",
    "completed",
    "complete",
    "success",
    "succeeded",
    "ok",
    "done",
}

WARN_STATUSES: set[str] = {
    "warn",
    "warning",
    "partial",
    "needs_review",
    "review",
    "unknown",
}

FAIL_STATUSES: set[str] = {
    "fail",
    "failed",
    "error",
    "errored",
    "timeout",
    "timed_out",
    "cancelled",
    "canceled",
}

SPRINT4_SCENARIOS: dict[str, dict[str, str]] = {
    "saleforceonespy": {
        "domain": "business_process",
        "scenario_id": "saleforceonespy",
        "scenario_name": "SaleForceOneSpy",
        "upstream_track": "crmarena",
        "category": "prompt_extraction",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "crmarena",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/saleforceonespy/index.html",
    },
    "wikiwiper": {
        "domain": "game",
        "scenario_id": "wikiwiper",
        "scenario_name": "WikiWiper",
        "upstream_track": "mcu_minecraft",
        "category": "indirect_injection",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "mcu_minecraft",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/wikiwiper/index.html",
    },
    "tickettwister": {
        "domain": "tau2",
        "scenario_id": "tickettwister",
        "scenario_name": "TicketTwister",
        "upstream_track": "tau2_agentbeats",
        "category": "jailbreak",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "tau2_agentbeats",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/tickettwister/index.html",
    },
    "bidbot": {
        "domain": "multi_agent",
        "scenario_id": "bidbot",
        "scenario_name": "BidBot",
        "upstream_track": "maizebargain",
        "category": "pii_leak",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "maizebargain",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/bidbot/index.html",
    },
    "taxwiztrap": {
        "domain": "finance",
        "scenario_id": "taxwiztrap",
        "scenario_name": "TaxWizTrap",
        "upstream_track": "officeqa",
        "category": "indirect_injection",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "officeqa",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/taxwiztrap/index.html",
    },
    "lnklifter": {
        "domain": "computer_use",
        "scenario_id": "lnklifter",
        "scenario_name": "LnkLifter",
        "upstream_track": "osworld",
        "category": "insecure_output",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "osworld",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/lnklifter/index.html",
    },
    "inventoryinject": {
        "domain": "agent_safety",
        "scenario_id": "inventoryinject",
        "scenario_name": "InventoryInject",
        "upstream_track": "pibench",
        "category": "indirect_injection",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "pibench",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/inventoryinject/index.html",
    },
    "devcontainerdoom": {
        "domain": "coding",
        "scenario_id": "devcontainerdoom",
        "scenario_name": "DevContainerDoom",
        "upstream_track": "netarena",
        "category": "supply_chain",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "netarena",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/devcontainerdoom/index.html",
    },
    "staticshipscam": {
        "domain": "cybersecurity",
        "scenario_id": "staticshipscam",
        "scenario_name": "StaticShipScam",
        "upstream_track": "cybergym",
        "category": "supply_chain",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "cybergym",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/staticshipscam/index.html",
    },
    "whistleblowerwreck": {
        "domain": "research",
        "scenario_id": "whistleblowerwreck",
        "scenario_name": "WhistleBlowerWreck",
        "upstream_track": "fieldworkarena",
        "category": "pii_leak",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "fieldworkarena",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/whistleblowerwreck/index.html",
    },
    "docudoctor": {
        "domain": "healthcare",
        "scenario_id": "docudoctor",
        "scenario_name": "DocuDoctor",
        "upstream_track": "fhir_agent",
        "category": "pii_leak",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "fhir_agent",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/docudoctor/index.html",
    },
    "searchglitch": {
        "domain": "web",
        "scenario_id": "searchglitch",
        "scenario_name": "SearchGlitch",
        "upstream_track": "comtrade",
        "category": "jailbreak",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "comtrade",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/searchglitch/index.html",
    },
    "gymjailbreak": {
        "domain": "agent_security",
        "scenario_id": "gymjailbreak",
        "scenario_name": "GymJailbreak",
        "upstream_track": "lambda_agent_security",
        "category": "insecure_output",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "lambda_agent_security",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/gymjailbreak/index.html",
    },
    "codereviewruse": {
        "domain": "software_testing",
        "scenario_id": "codereviewruse",
        "scenario_name": "CodeReviewRuse",
        "upstream_track": "logomesh",
        "category": "indirect_injection",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "logomesh",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/codereviewruse/index.html",
    },
    "cryptocrash": {
        "domain": "defi",
        "scenario_id": "cryptocrash",
        "scenario_name": "CryptoCrash",
        "upstream_track": "ethernaut",
        "category": "secret_leak",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "ethernaut",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/cryptocrash/index.html",
    },
    "lawfirmleak": {
        "domain": "legal_domain",
        "scenario_id": "lawfirmleak",
        "scenario_name": "LawFirmLeak",
        "upstream_track": "agentify_bench",
        "category": "prompt_extraction",
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
        "benchmark": BENCHMARK_NAME,
        "selected_opponent": "agentify_bench",
        "source_url": "https://agentbeats-lambda.s3.us-east-1.amazonaws.com/lawfirmleak/index.html",
    },
}

DOMAIN_TO_SCENARIO_ID: dict[str, str] = {
    item["domain"]: scenario_id for scenario_id, item in SPRINT4_SCENARIOS.items()
}

SCENARIO_ALIASES: dict[str, str] = {
    "salesforceonespy": "saleforceonespy",
    "salesforceone": "saleforceonespy",
    "saleforceone": "saleforceonespy",
    "lnk_lifter": "lnklifter",
    "linklifter": "lnklifter",
    "link_lifter": "lnklifter",
    "whistle_blower_wreck": "whistleblowerwreck",
    "code_review_ruse": "codereviewruse",
    "crypto_crash": "cryptocrash",
    "law_firm_leak": "lawfirmleak",
}


@dataclass(slots=True)
class HeldoutScore:
    """Score for a held-out case.

    The first five fields keep the original dataclass constructor shape.
    """

    case_id: str
    success: bool
    correctness_hint: float
    robustness_hint: float
    notes: list[str] = field(default_factory=list)

    # Optional Sprint 4 identity fields.
    domain: str = ""
    scenario_id: str = ""
    scenario_name: str = ""
    upstream_track: str = ""
    category: str = ""
    adapter: str = ""
    assessment_mode: str = ""
    scenario_family: str = ""
    benchmark: str = ""
    selected_opponent: str = ""
    source_url: str = ""

    metadata: JsonDict = field(default_factory=dict)

    @property
    def identity(self) -> JsonDict:
        return {
            "domain": self.domain,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "upstream_track": self.upstream_track,
            "category": self.category,
            "adapter": self.adapter,
            "assessment_mode": self.assessment_mode,
            "scenario_family": self.scenario_family,
            "benchmark": self.benchmark,
            "selected_opponent": self.selected_opponent,
            "source_url": self.source_url,
        }

    @property
    def overall_hint(self) -> float:
        return _mean([self.correctness_hint, self.robustness_hint])

    def as_dict(self) -> JsonDict:
        return {
            "case_id": self.case_id,
            "success": self.success,
            "correctness_hint": self.correctness_hint,
            "robustness_hint": self.robustness_hint,
            "overall_hint": self.overall_hint,
            "notes": list(self.notes),
            **self.identity,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class TrackScore:
    """Local score hint for one TrackResult."""

    track: str
    status: str
    score: float
    correctness_hint: float
    robustness_hint: float
    efficiency_hint: float
    notes: list[str] = field(default_factory=list)

    # Sprint 4 identity fields.
    domain: str = ""
    scenario_id: str = ""
    scenario_name: str = ""
    upstream_track: str = ""
    category: str = ""
    adapter: str = ""
    assessment_mode: str = ""
    scenario_family: str = ""
    benchmark: str = ""
    selected_opponent: str = ""
    source_url: str = ""

    metadata: JsonDict = field(default_factory=dict)
    details: JsonDict = field(default_factory=dict)

    @property
    def identity(self) -> JsonDict:
        return {
            "domain": self.domain,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "upstream_track": self.upstream_track,
            "category": self.category,
            "adapter": self.adapter,
            "assessment_mode": self.assessment_mode,
            "scenario_family": self.scenario_family,
            "benchmark": self.benchmark,
            "selected_opponent": self.selected_opponent,
            "source_url": self.source_url,
        }

    @property
    def success(self) -> bool:
        return self.status.lower() in SUCCESS_STATUSES and self.correctness_hint >= 0.75

    @property
    def overall_hint(self) -> float:
        return _mean([self.correctness_hint, self.robustness_hint, self.efficiency_hint])

    def as_dict(self) -> JsonDict:
        return {
            "track": self.track,
            "status": self.status,
            "score": self.score,
            "success": self.success,
            "correctness_hint": self.correctness_hint,
            "robustness_hint": self.robustness_hint,
            "efficiency_hint": self.efficiency_hint,
            "overall_hint": self.overall_hint,
            "notes": list(self.notes),
            **self.identity,
            "metadata": dict(self.metadata),
            "details": dict(self.details),
        }

    def compact_dict(self) -> JsonDict:
        return {
            "track": self.track,
            "status": self.status,
            "score": self.score,
            "success": self.success,
            "correctness_hint": self.correctness_hint,
            "robustness_hint": self.robustness_hint,
            "efficiency_hint": self.efficiency_hint,
            "overall_hint": self.overall_hint,
            **self.identity,
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class ScoreAggregate:
    """Aggregated local score by a selected identity field."""

    group_by: str
    value: str
    count: int
    success_count: int
    warning_count: int
    failure_count: int
    score: float
    correctness_hint: float
    robustness_hint: float
    efficiency_hint: float
    overall_hint: float

    def as_dict(self) -> JsonDict:
        return {
            "group_by": self.group_by,
            "value": self.value,
            "count": self.count,
            "success_count": self.success_count,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "score": self.score,
            "correctness_hint": self.correctness_hint,
            "robustness_hint": self.robustness_hint,
            "efficiency_hint": self.efficiency_hint,
            "overall_hint": self.overall_hint,
        }


@dataclass(slots=True)
class EvaluationScore:
    """Collection-level score over TrackScore objects."""

    track_scores: list[TrackScore] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.track_scores)

    @property
    def score(self) -> float:
        return _mean(score.score for score in self.track_scores)

    @property
    def correctness_hint(self) -> float:
        return _mean(score.correctness_hint for score in self.track_scores)

    @property
    def robustness_hint(self) -> float:
        return _mean(score.robustness_hint for score in self.track_scores)

    @property
    def efficiency_hint(self) -> float:
        return _mean(score.efficiency_hint for score in self.track_scores)

    @property
    def overall_hint(self) -> float:
        return _mean(score.overall_hint for score in self.track_scores)

    @property
    def success_count(self) -> int:
        return sum(1 for score in self.track_scores if score.success)

    @property
    def warning_count(self) -> int:
        return sum(1 for score in self.track_scores if score.status.lower() in WARN_STATUSES)

    @property
    def failure_count(self) -> int:
        return sum(1 for score in self.track_scores if score.status.lower() in FAIL_STATUSES)

    def group_by(self, field_name: str) -> list[ScoreAggregate]:
        buckets: dict[str, list[TrackScore]] = {}
        for track_score in self.track_scores:
            value = _text(getattr(track_score, field_name, ""), default="unknown")
            buckets.setdefault(value, []).append(track_score)

        groups: list[ScoreAggregate] = []
        for value, scores in sorted(buckets.items(), key=lambda item: item[0]):
            groups.append(
                ScoreAggregate(
                    group_by=field_name,
                    value=value,
                    count=len(scores),
                    success_count=sum(1 for score in scores if score.success),
                    warning_count=sum(1 for score in scores if score.status.lower() in WARN_STATUSES),
                    failure_count=sum(1 for score in scores if score.status.lower() in FAIL_STATUSES),
                    score=_mean(score.score for score in scores),
                    correctness_hint=_mean(score.correctness_hint for score in scores),
                    robustness_hint=_mean(score.robustness_hint for score in scores),
                    efficiency_hint=_mean(score.efficiency_hint for score in scores),
                    overall_hint=_mean(score.overall_hint for score in scores),
                )
            )

        return groups

    def grouped_dict(self, fields: Iterable[str] | None = None) -> JsonDict:
        selected_fields = list(
            fields
            or (
                "domain",
                "scenario_id",
                "scenario_name",
                "category",
                "upstream_track",
                "adapter",
                "assessment_mode",
                "scenario_family",
                "benchmark",
                "selected_opponent",
            )
        )
        return {
            field_name: [group.as_dict() for group in self.group_by(field_name)]
            for field_name in selected_fields
        }

    def as_dict(self) -> JsonDict:
        return {
            "count": self.count,
            "score": self.score,
            "correctness_hint": self.correctness_hint,
            "robustness_hint": self.robustness_hint,
            "efficiency_hint": self.efficiency_hint,
            "overall_hint": self.overall_hint,
            "success_count": self.success_count,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "track_scores": [score.as_dict() for score in self.track_scores],
            "groups": self.grouped_dict(),
        }

    def compact_dict(self) -> JsonDict:
        return {
            "count": self.count,
            "score": self.score,
            "correctness_hint": self.correctness_hint,
            "robustness_hint": self.robustness_hint,
            "efficiency_hint": self.efficiency_hint,
            "overall_hint": self.overall_hint,
            "success_count": self.success_count,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
        }


class HeldoutScorer:
    """Very lightweight internal scorer for held-out experiments."""

    def score(
        self,
        *,
        case_id: str,
        status: str,
        warnings: list[str] | None = None,
        fallback_used: bool = False,
        metadata: Mapping[str, Any] | None = None,
        identity: Mapping[str, Any] | None = None,
    ) -> HeldoutScore:
        warnings = list(warnings or [])
        normalized_status = str(status or "").strip().lower()
        success = normalized_status in SUCCESS_STATUSES and not fallback_used
        correctness = 1.0 if success else 0.55
        robustness = 1.0

        notes: list[str] = []
        if warnings:
            robustness -= min(0.3, len(warnings) * 0.05)
            notes.append(f"Warnings observed: {len(warnings)}.")
        if fallback_used:
            correctness -= 0.2
            robustness -= 0.15
            notes.append("Fallback path was used during evaluation.")

        meta = dict(metadata or {})
        if identity:
            meta = {**dict(identity), **meta}
        extracted_identity = _extract_identity(meta)

        return HeldoutScore(
            case_id=case_id,
            success=success,
            correctness_hint=_clamp(correctness),
            robustness_hint=_clamp(robustness),
            notes=notes,
            domain=extracted_identity.get("domain", ""),
            scenario_id=extracted_identity.get("scenario_id", ""),
            scenario_name=extracted_identity.get("scenario_name", ""),
            upstream_track=extracted_identity.get("upstream_track", ""),
            category=extracted_identity.get("category", ""),
            adapter=extracted_identity.get("adapter", ""),
            assessment_mode=extracted_identity.get("assessment_mode", ""),
            scenario_family=extracted_identity.get("scenario_family", ""),
            benchmark=extracted_identity.get("benchmark", ""),
            selected_opponent=extracted_identity.get("selected_opponent", ""),
            source_url=extracted_identity.get("source_url", ""),
            metadata=meta,
        )


class TrackResultScorer:
    """Local scoring for TrackResult-like objects."""

    def score_track(self, result: Any) -> TrackScore:
        data = _to_dict(result)
        metadata = _to_dict(data.get("metadata") or getattr(result, "metadata", None))
        details = _to_dict(data.get("details") or getattr(result, "details", None))

        track = _first_text(data.get("track"), getattr(result, "track", None), default="unknown")
        status = _first_text(data.get("status"), getattr(result, "status", None), default="unknown")
        raw_score = _as_float(_first_value(data.get("score"), getattr(result, "score", None)), default=0.0)
        score = _clamp(raw_score)

        identity = _extract_identity({**metadata, **details.get("identity", {}), **data})
        validation = _identity_validation(identity)

        correctness = self._base_correctness(status=status, score=score)
        robustness = 1.0
        efficiency = 1.0
        notes: list[str] = []

        if status.lower() in WARN_STATUSES:
            robustness -= 0.15
            notes.append("Track returned a warning status.")
        elif status.lower() in FAIL_STATUSES:
            robustness -= 0.35
            notes.append("Track returned a failure status.")

        missing_identity = validation["missing_identity"]
        if missing_identity:
            robustness -= min(0.20, len(missing_identity) * 0.025)
            notes.append("Missing Sprint 4 identity fields: " + ", ".join(missing_identity) + ".")

        if identity.get("domain") and not validation["domain_known"]:
            robustness -= 0.10
            notes.append(f"Unknown Sprint 4 domain: {identity['domain']}.")

        if identity.get("scenario_id") and not validation["scenario_known"]:
            robustness -= 0.10
            notes.append(f"Unknown Sprint 4 scenario_id: {identity['scenario_id']}.")

        if not validation["domain_scenario_match"]:
            correctness -= 0.15
            robustness -= 0.10
            notes.append(
                "Domain/scenario_id mismatch: "
                f"{identity.get('domain')} expects {validation['expected_scenario_for_domain']}, "
                f"got {identity.get('scenario_id')}."
            )

        # Evidence quality hints from common TrackResult.details shape.
        checks = _to_dict(details.get("checks"))
        if checks:
            failed_checks = [key for key, value in checks.items() if value is False]
            if failed_checks:
                robustness -= min(0.20, len(failed_checks) * 0.04)
                notes.append("Failed/false checks observed: " + ", ".join(failed_checks) + ".")

        if details.get("error"):
            correctness -= 0.10
            robustness -= 0.10
            notes.append(f"Track details include error: {details['error']}")

        if metadata.get("sprint4_validation"):
            sprint4_validation = _to_dict(metadata.get("sprint4_validation"))
            missing = sprint4_validation.get("missing_identity")
            if isinstance(missing, Sequence) and not isinstance(missing, (str, bytes)) and missing:
                notes.append("Track metadata includes Sprint 4 validation warnings.")

        return TrackScore(
            track=track,
            status=status,
            score=score,
            correctness_hint=_clamp(correctness),
            robustness_hint=_clamp(robustness),
            efficiency_hint=_clamp(efficiency),
            notes=_dedupe(notes),
            domain=identity.get("domain", ""),
            scenario_id=identity.get("scenario_id", ""),
            scenario_name=identity.get("scenario_name", ""),
            upstream_track=identity.get("upstream_track", ""),
            category=identity.get("category", ""),
            adapter=identity.get("adapter", ""),
            assessment_mode=identity.get("assessment_mode", ""),
            scenario_family=identity.get("scenario_family", ""),
            benchmark=identity.get("benchmark", ""),
            selected_opponent=identity.get("selected_opponent", ""),
            source_url=identity.get("source_url", ""),
            metadata=metadata,
            details=details,
        )

    def score_many(self, results: Iterable[Any]) -> EvaluationScore:
        return EvaluationScore(track_scores=[self.score_track(result) for result in results])

    def score_report(self, report: Any) -> EvaluationScore:
        data = _to_dict(report)
        results = data.get("results")
        if results is None:
            results = getattr(report, "results", [])
        if not isinstance(results, Iterable) or isinstance(results, (str, bytes, Mapping)):
            results = []
        return self.score_many(results)

    def _base_correctness(self, *, status: str, score: float) -> float:
        normalized = status.lower()
        if normalized in SUCCESS_STATUSES:
            return max(score, 0.85)
        if normalized in WARN_STATUSES:
            return max(min(score, 0.80), 0.45)
        if normalized in FAIL_STATUSES:
            return min(score, 0.35)
        return max(min(score, 0.70), 0.40)


# Convenience functions for callers that prefer functional style.
def score_track_result(result: Any) -> TrackScore:
    return TrackResultScorer().score_track(result)


def score_track_results(results: Iterable[Any]) -> EvaluationScore:
    return TrackResultScorer().score_many(results)


def score_report(report: Any) -> EvaluationScore:
    return TrackResultScorer().score_report(report)


def _extract_identity(value: Mapping[str, Any]) -> dict[str, str]:
    metadata = _to_dict(value.get("metadata"))
    identity_payload = _to_dict(value.get("identity"))
    scenario = _to_dict(value.get("scenario"))
    source = _to_dict(value.get("source"))
    route = _to_dict(value.get("route"))

    scenario_id = _find_known_scenario(value, scenario, metadata, identity_payload)
    known = dict(SPRINT4_SCENARIOS.get(scenario_id, {}))

    identity = {
        "domain": _first_text(
            value.get("domain"),
            metadata.get("domain"),
            identity_payload.get("domain"),
            value.get("domain_key"),
            value.get("scenario_domain"),
            scenario.get("domain"),
            known.get("domain"),
        ),
        "scenario_id": _first_text(
            value.get("scenario_id"),
            metadata.get("scenario_id"),
            identity_payload.get("scenario_id"),
            scenario.get("scenario_id"),
            scenario.get("id"),
            known.get("scenario_id"),
        ),
        "scenario_name": _first_text(
            value.get("scenario_name"),
            metadata.get("scenario_name"),
            identity_payload.get("scenario_name"),
            scenario.get("scenario_name"),
            scenario.get("name"),
            known.get("scenario_name"),
        ),
        "upstream_track": _first_text(
            value.get("upstream_track"),
            value.get("benchmark_track"),
            value.get("opponent_profile"),
            metadata.get("upstream_track"),
            metadata.get("benchmark_track"),
            identity_payload.get("upstream_track"),
            route.get("upstream_track"),
            route.get("opponent_profile"),
            known.get("upstream_track"),
        ),
        "category": _first_text(
            value.get("category"),
            value.get("attack_category"),
            metadata.get("category"),
            identity_payload.get("category"),
            scenario.get("category"),
            scenario.get("attack_category"),
            known.get("category"),
        ),
        "adapter": _first_text(
            value.get("adapter"),
            value.get("adapter_name"),
            metadata.get("adapter"),
            identity_payload.get("adapter"),
            route.get("adapter"),
            route.get("adapter_name"),
            known.get("adapter"),
        ),
        "assessment_mode": _first_text(
            value.get("assessment_mode"),
            metadata.get("assessment_mode"),
            identity_payload.get("assessment_mode"),
            scenario.get("assessment_mode"),
            route.get("assessment_mode"),
            known.get("assessment_mode"),
            default=DEFAULT_ASSESSMENT_MODE if scenario_id else "",
        ),
        "scenario_family": _first_text(
            value.get("scenario_family"),
            metadata.get("scenario_family"),
            identity_payload.get("scenario_family"),
            scenario.get("scenario_family"),
            route.get("scenario_family"),
            known.get("scenario_family"),
            default=DEFAULT_SCENARIO_FAMILY if scenario_id else "",
        ),
        "benchmark": _first_text(
            value.get("benchmark"),
            metadata.get("benchmark"),
            identity_payload.get("benchmark"),
            source.get("benchmark"),
            known.get("benchmark"),
            default=BENCHMARK_NAME if scenario_id else "",
        ),
        "selected_opponent": _first_text(
            value.get("selected_opponent"),
            metadata.get("selected_opponent"),
            identity_payload.get("selected_opponent"),
            route.get("selected_opponent"),
            route.get("opponent"),
            source.get("selected_opponent"),
            known.get("selected_opponent"),
        ),
        "source_url": _first_text(
            value.get("source_url"),
            metadata.get("source_url"),
            identity_payload.get("source_url"),
            source.get("source_url"),
            source.get("url"),
            source.get("repo"),
            known.get("source_url"),
        ),
    }

    if not identity["selected_opponent"]:
        identity["selected_opponent"] = identity["upstream_track"]

    normalized_id = _scenario_id_from_any(identity["scenario_id"] or identity["scenario_name"])
    if normalized_id in SPRINT4_SCENARIOS:
        identity["scenario_id"] = normalized_id
        identity["scenario_name"] = identity["scenario_name"] or SPRINT4_SCENARIOS[normalized_id]["scenario_name"]

    return identity


def _identity_validation(identity: Mapping[str, Any]) -> dict[str, Any]:
    domain = _text(identity.get("domain"))
    scenario_id = _text(identity.get("scenario_id"))
    expected_scenario_for_domain = DOMAIN_TO_SCENARIO_ID.get(domain, "")

    missing_identity = [
        key
        for key in (
            "domain",
            "scenario_id",
            "scenario_name",
            "upstream_track",
            "category",
            "adapter",
            "assessment_mode",
            "scenario_family",
        )
        if not _text(identity.get(key))
    ]

    return {
        "domain_known": domain in DOMAIN_TO_SCENARIO_ID,
        "scenario_known": scenario_id in SPRINT4_SCENARIOS,
        "domain_scenario_match": (
            not domain
            or not scenario_id
            or not expected_scenario_for_domain
            or expected_scenario_for_domain == scenario_id
        ),
        "expected_scenario_for_domain": expected_scenario_for_domain or None,
        "missing_identity": missing_identity,
    }


def _find_known_scenario(
    value: Mapping[str, Any],
    scenario: Mapping[str, Any],
    metadata: Mapping[str, Any],
    identity_payload: Mapping[str, Any],
) -> str:
    candidates = (
        value.get("scenario_id"),
        value.get("scenario_name"),
        value.get("scenario"),
        value.get("env_name"),
        metadata.get("scenario_id"),
        metadata.get("scenario_name"),
        identity_payload.get("scenario_id"),
        identity_payload.get("scenario_name"),
        scenario.get("scenario_id"),
        scenario.get("id"),
        scenario.get("scenario_name"),
        scenario.get("name"),
    )

    for candidate in candidates:
        scenario_id = _scenario_id_from_any(candidate)
        if scenario_id in SPRINT4_SCENARIOS:
            return scenario_id

    domain = _first_text(
        value.get("domain"),
        metadata.get("domain"),
        identity_payload.get("domain"),
        value.get("domain_key"),
        value.get("scenario_domain"),
        scenario.get("domain"),
    )
    return DOMAIN_TO_SCENARIO_ID.get(domain, "")


def _scenario_id_from_any(value: Any) -> str:
    key = _compact_key(value)
    if not key:
        return ""
    return SCENARIO_ALIASES.get(key, key)


def _compact_key(value: Any) -> str:
    return _slug_key(value).replace("_", "")


def _slug_key(value: Any) -> str:
    text = _text(value).lower()
    if not text:
        return ""
    cleaned: list[str] = []
    previous_was_sep = False
    for char in text:
        if char.isalnum():
            cleaned.append(char)
            previous_was_sep = False
        elif not previous_was_sep:
            cleaned.append("_")
            previous_was_sep = True
    return "".join(cleaned).strip("_")


def _to_dict(value: Any) -> JsonDict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return dict(asdict(value))

    for method_name in ("as_dict", "to_dict", "model_dump", "dict"):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            dumped = method()
        except Exception:
            continue
        if isinstance(dumped, Mapping):
            return dict(dumped)

    return {}


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _first_text(*values: Any, default: str = "") -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return default


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, round(value, 3)))


def _mean(values: Iterable[float]) -> float:
    numbers = [float(value) for value in values]
    if not numbers:
        return 0.0
    return round(sum(numbers) / len(numbers), 3)


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output
