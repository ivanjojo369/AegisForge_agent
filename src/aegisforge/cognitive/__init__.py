from __future__ import annotations

"""AegisForge NCP — Neuro-Cognitive Purple Core.

The ``aegisforge.cognitive`` package exposes the NCP controller and its
supporting cognitive layers:

- attention: salience selection
- state: typed cognitive state contracts
- working_memory: active-memory store and context selection
- episodic_memory: generalized lessons without benchmark answer caching
- evidence: deterministic claim/source grounding
- uncertainty: confidence, risk, and action gating
- metacognition: self-audit and readiness checks
- controller: top-level orchestration for agent.py integration

The package uses lazy exports so importing ``aegisforge.cognitive`` is cheap and
does not force the whole controller stack to load until a symbol is actually
requested.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any


NCP_PACKAGE_VERSION = "0.1.0"


_EXPORTS: dict[str, tuple[str, str]] = {
    # attention.py
    "NCP_ATTENTION_VERSION": ("attention", "NCP_ATTENTION_VERSION"),
    "AttentionConfig": ("attention", "AttentionConfig"),
    "AttentionFrame": ("attention", "AttentionFrame"),
    "AttentionGate": ("attention", "AttentionGate"),
    "AttentionSignal": ("attention", "AttentionSignal"),
    "select_attention": ("attention", "select_attention"),

    # state.py
    "DEFAULT_ASSESSMENT_MODE": ("state", "DEFAULT_ASSESSMENT_MODE"),
    "DEFAULT_BENCHMARK": ("state", "DEFAULT_BENCHMARK"),
    "DEFAULT_SCENARIO_FAMILY": ("state", "DEFAULT_SCENARIO_FAMILY"),
    "NCP_STATE_VERSION": ("state", "NCP_STATE_VERSION"),
    "SPRINT4_DOMAIN_CATEGORIES": ("state", "SPRINT4_DOMAIN_CATEGORIES"),
    "SPRINT4_SCENARIOS_BY_DOMAIN": ("state", "SPRINT4_SCENARIOS_BY_DOMAIN"),
    "CognitiveDecision": ("state", "CognitiveDecision"),
    "CognitiveScorecard": ("state", "CognitiveScorecard"),
    "CognitiveState": ("state", "CognitiveState"),
    "DecisionOption": ("state", "DecisionOption"),
    "DomainAdapterProfile": ("state", "DomainAdapterProfile"),
    "EvidenceRecord": ("state", "EvidenceRecord"),
    "PolicyBoundary": ("state", "PolicyBoundary"),
    "TaskTheory": ("state", "TaskTheory"),
    "TraceEvent": ("state", "TraceEvent"),
    "UncertaintyEstimate": ("state", "UncertaintyEstimate"),
    "WorkingMemoryItem": ("state", "WorkingMemoryItem"),
    "new_cognitive_state": ("state", "new_cognitive_state"),
    "state_from_attention": ("state", "state_from_attention"),

    # working_memory.py
    "NCP_WORKING_MEMORY_VERSION": ("working_memory", "NCP_WORKING_MEMORY_VERSION"),
    "WorkingMemoryConfig": ("working_memory", "WorkingMemoryConfig"),
    "WorkingMemoryStore": ("working_memory", "WorkingMemoryStore"),
    "WorkingMemoryStats": ("working_memory", "WorkingMemoryStats"),
    "MemoryCompressionResult": ("working_memory", "MemoryCompressionResult"),
    "MemoryQuery": ("working_memory", "MemoryQuery"),
    "MemoryScore": ("working_memory", "MemoryScore"),
    "MemorySelection": ("working_memory", "MemorySelection"),
    "build_working_memory": ("working_memory", "build_working_memory"),
    "format_memory_prompt": ("working_memory", "format_memory_prompt"),
    "memory_items_from_attention": ("working_memory", "memory_items_from_attention"),
    "update_state_with_working_memory": ("working_memory", "update_state_with_working_memory"),

    # episodic_memory.py
    "NCP_EPISODIC_MEMORY_VERSION": ("episodic_memory", "NCP_EPISODIC_MEMORY_VERSION"),
    "DEFAULT_JSONL_NAME": ("episodic_memory", "DEFAULT_JSONL_NAME"),
    "EpisodeOutcome": ("episodic_memory", "EpisodeOutcome"),
    "EpisodeRecord": ("episodic_memory", "EpisodeRecord"),
    "EpisodeSignature": ("episodic_memory", "EpisodeSignature"),
    "EpisodicLesson": ("episodic_memory", "EpisodicLesson"),
    "EpisodicMemoryConfig": ("episodic_memory", "EpisodicMemoryConfig"),
    "EpisodicMemoryStats": ("episodic_memory", "EpisodicMemoryStats"),
    "EpisodicMemoryStore": ("episodic_memory", "EpisodicMemoryStore"),
    "EpisodicRetrievalQuery": ("episodic_memory", "EpisodicRetrievalQuery"),
    "EpisodicRetrievalResult": ("episodic_memory", "EpisodicRetrievalResult"),
    "RetrievedLesson": ("episodic_memory", "RetrievedLesson"),
    "build_episodic_memory": ("episodic_memory", "build_episodic_memory"),
    "extract_lessons_from_state": ("episodic_memory", "extract_lessons_from_state"),
    "format_episodic_prompt": ("episodic_memory", "format_episodic_prompt"),
    "record_episode": ("episodic_memory", "record_episode"),
    "retrieve_episodic_lessons": ("episodic_memory", "retrieve_episodic_lessons"),

    # evidence.py
    "NCP_EVIDENCE_VERSION": ("evidence", "NCP_EVIDENCE_VERSION"),
    "EvidenceBundle": ("evidence", "EvidenceBundle"),
    "EvidenceClaim": ("evidence", "EvidenceClaim"),
    "EvidenceConfig": ("evidence", "EvidenceConfig"),
    "EvidenceDecision": ("evidence", "EvidenceDecision"),
    "EvidenceSource": ("evidence", "EvidenceSource"),
    "EvidenceVerifier": ("evidence", "EvidenceVerifier"),
    "ClaimAssessment": ("evidence", "ClaimAssessment"),
    "SourceMatch": ("evidence", "SourceMatch"),
    "build_evidence_bundle": ("evidence", "build_evidence_bundle"),
    "evidence_records_from_tool_result": ("evidence", "evidence_records_from_tool_result"),
    "update_state_evidence": ("evidence", "update_state_evidence"),
    "verify_claims": ("evidence", "verify_claims"),

    # uncertainty.py
    "NCP_UNCERTAINTY_VERSION": ("uncertainty", "NCP_UNCERTAINTY_VERSION"),
    "VALID_RECOMMENDED_ACTIONS": ("uncertainty", "VALID_RECOMMENDED_ACTIONS"),
    "ActionGate": ("uncertainty", "ActionGate"),
    "EvidenceGap": ("uncertainty", "EvidenceGap"),
    "UncertaintyAssessment": ("uncertainty", "UncertaintyAssessment"),
    "UncertaintyCategory": ("uncertainty", "UncertaintyCategory"),
    "UncertaintyConfig": ("uncertainty", "UncertaintyConfig"),
    "UncertaintyEstimator": ("uncertainty", "UncertaintyEstimator"),
    "UncertaintyFactor": ("uncertainty", "UncertaintyFactor"),
    "build_decision_options_from_assessment": ("uncertainty", "build_decision_options_from_assessment"),
    "estimate_uncertainty": ("uncertainty", "estimate_uncertainty"),
    "gate_candidate_action": ("uncertainty", "gate_candidate_action"),
    "update_state_uncertainty": ("uncertainty", "update_state_uncertainty"),

    # metacognition.py
    "NCP_METACOGNITION_VERSION": ("metacognition", "NCP_METACOGNITION_VERSION"),
    "MetaAction": ("metacognition", "MetaAction"),
    "MetaCheck": ("metacognition", "MetaCheck"),
    "MetaCheckStatus": ("metacognition", "MetaCheckStatus"),
    "MetaRiskProfile": ("metacognition", "MetaRiskProfile"),
    "MetacognitionConfig": ("metacognition", "MetacognitionConfig"),
    "MetacognitionReport": ("metacognition", "MetacognitionReport"),
    "MetacognitiveController": ("metacognition", "MetacognitiveController"),
    "metacognitive_memory_items": ("metacognition", "metacognitive_memory_items"),
    "run_metacognitive_check": ("metacognition", "run_metacognitive_check"),
    "select_safest_option": ("metacognition", "select_safest_option"),
    "update_state_metacognition": ("metacognition", "update_state_metacognition"),

    # controller.py
    "NCP_CONTROLLER_VERSION": ("controller", "NCP_CONTROLLER_VERSION"),
    "CognitiveController": ("controller", "CognitiveController"),
    "CognitiveControllerConfig": ("controller", "CognitiveControllerConfig"),
    "ControllerArtifacts": ("controller", "ControllerArtifacts"),
    "ControllerInput": ("controller", "ControllerInput"),
    "ControllerOutput": ("controller", "ControllerOutput"),
    "ControllerPhase": ("controller", "ControllerPhase"),
    "ControllerSession": ("controller", "ControllerSession"),
    "ControllerStep": ("controller", "ControllerStep"),
    "controller_output_to_response_payload": ("controller", "controller_output_to_response_payload"),
    "evaluate_candidate_action": ("controller", "evaluate_candidate_action"),
    "prepare_cognitive_context": ("controller", "prepare_cognitive_context"),
    "run_cognitive_controller": ("controller", "run_cognitive_controller"),
}


__all__ = ["NCP_PACKAGE_VERSION", *_EXPORTS.keys()]


def __getattr__(name: str) -> Any:
    """Load public symbols lazily from their implementation modules."""

    try:
        module_name, symbol_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(f"{__name__}.{module_name}")
    symbol = getattr(module, symbol_name)
    globals()[name] = symbol
    return symbol


def __dir__() -> list[str]:
    return sorted(__all__)


if TYPE_CHECKING:
    from .attention import (
        NCP_ATTENTION_VERSION,
        AttentionConfig,
        AttentionFrame,
        AttentionGate,
        AttentionSignal,
        select_attention,
    )
    from .state import (
        DEFAULT_ASSESSMENT_MODE,
        DEFAULT_BENCHMARK,
        DEFAULT_SCENARIO_FAMILY,
        NCP_STATE_VERSION,
        SPRINT4_DOMAIN_CATEGORIES,
        SPRINT4_SCENARIOS_BY_DOMAIN,
        CognitiveDecision,
        CognitiveScorecard,
        CognitiveState,
        DecisionOption,
        DomainAdapterProfile,
        EvidenceRecord,
        PolicyBoundary,
        TaskTheory,
        TraceEvent,
        UncertaintyEstimate,
        WorkingMemoryItem,
        new_cognitive_state,
        state_from_attention,
    )
    from .working_memory import (
        NCP_WORKING_MEMORY_VERSION,
        MemoryCompressionResult,
        MemoryQuery,
        MemoryScore,
        MemorySelection,
        WorkingMemoryConfig,
        WorkingMemoryStats,
        WorkingMemoryStore,
        build_working_memory,
        format_memory_prompt,
        memory_items_from_attention,
        update_state_with_working_memory,
    )
    from .episodic_memory import (
        DEFAULT_JSONL_NAME,
        NCP_EPISODIC_MEMORY_VERSION,
        EpisodeOutcome,
        EpisodeRecord,
        EpisodeSignature,
        EpisodicLesson,
        EpisodicMemoryConfig,
        EpisodicMemoryStats,
        EpisodicMemoryStore,
        EpisodicRetrievalQuery,
        EpisodicRetrievalResult,
        RetrievedLesson,
        build_episodic_memory,
        extract_lessons_from_state,
        format_episodic_prompt,
        record_episode,
        retrieve_episodic_lessons,
    )
    from .evidence import (
        NCP_EVIDENCE_VERSION,
        ClaimAssessment,
        EvidenceBundle,
        EvidenceClaim,
        EvidenceConfig,
        EvidenceDecision,
        EvidenceSource,
        EvidenceVerifier,
        SourceMatch,
        build_evidence_bundle,
        evidence_records_from_tool_result,
        update_state_evidence,
        verify_claims,
    )
    from .uncertainty import (
        NCP_UNCERTAINTY_VERSION,
        VALID_RECOMMENDED_ACTIONS,
        ActionGate,
        EvidenceGap,
        UncertaintyAssessment,
        UncertaintyCategory,
        UncertaintyConfig,
        UncertaintyEstimator,
        UncertaintyFactor,
        build_decision_options_from_assessment,
        estimate_uncertainty,
        gate_candidate_action,
        update_state_uncertainty,
    )
    from .metacognition import (
        NCP_METACOGNITION_VERSION,
        MetaAction,
        MetaCheck,
        MetaCheckStatus,
        MetaRiskProfile,
        MetacognitionConfig,
        MetacognitionReport,
        MetacognitiveController,
        metacognitive_memory_items,
        run_metacognitive_check,
        select_safest_option,
        update_state_metacognition,
    )
    from .controller import (
        NCP_CONTROLLER_VERSION,
        CognitiveController,
        CognitiveControllerConfig,
        ControllerArtifacts,
        ControllerInput,
        ControllerOutput,
        ControllerPhase,
        ControllerSession,
        ControllerStep,
        controller_output_to_response_payload,
        evaluate_candidate_action,
        prepare_cognitive_context,
        run_cognitive_controller,
    )
