from __future__ import annotations

from typing import Any

from .config import AppConfig
from .models import AgentCardPayload


AGENT_CARD_VERSION = "agent_card_v0_2_skillsbench_filesystem_first_2026_06_03"


# Canonical selected-opponent tracks. "mcu" covers mcu-minecraft/Minecraft Benchmark.
# SkillsBench is the General-Purpose Agent / standard-v1 evaluator.
SELECTED_OPPONENT_TRACKS = [
    "mcu",
    "officeqa",
    "crmarena",
    "fieldworkarena",
    "maizebargain",
    "tau2",
    "osworld",
    "pibench",
    "cybergym",
    "netarena",
    "skillsbench",
]

TRACK_ALIASES = {
    "mcu-minecraft": "mcu",
    "mcu_minecraft": "mcu",
    "minecraft": "mcu",
    "minecraft-benchmark": "mcu",
    "minecraft benchmark": "mcu",
    "crmarenapro": "crmarena",
    "entropic-crmarenapro": "crmarena",
    "business-process": "crmarena",
    "business_process": "crmarena",
    "tau2-agentbeats": "tau2",
    "tau2_agentbeats": "tau2",
    "tau²": "tau2",
    "osworld-green": "osworld",
    "computer-use": "osworld",
    "computer_use": "osworld",
    "pi-bench": "pibench",
    "pi_bench": "pibench",
    "pibench": "pibench",
    "agent-safety": "pibench",
    "agent_safety": "pibench",
    "policy-compliance": "pibench",
    "policy_compliance": "pibench",
    "cybergym-green": "cybergym",
    "cybersecurity": "cybergym",
    "cybersecurity-agent": "cybergym",
    "net-arena": "netarena",
    "net_arena": "netarena",

    # SkillsBench / General-Purpose Agent aliases.
    "skillsbench": "skillsbench",
    "skillsbench-agentbeats": "skillsbench",
    "skillsbench_agentbeats": "skillsbench",
    "skillsbench-leaderboard": "skillsbench",
    "skillsbench_leaderboard": "skillsbench",
    "benchflow": "skillsbench",
    "benchflow-ai": "skillsbench",
    "benchflow_ai": "skillsbench",
    "benchflowai": "skillsbench",
    "standard-v1": "skillsbench",
    "standard_v1": "skillsbench",
    "standard v1": "skillsbench",
    "with-skills": "skillsbench",
    "with_skills": "skillsbench",
    "general-purpose": "skillsbench",
    "general_purpose": "skillsbench",
    "general purpose": "skillsbench",
    "general-purpose-agent": "skillsbench",
    "general_purpose_agent": "skillsbench",
    "general purpose agent": "skillsbench",
    "general-agent": "skillsbench",
    "general_agent": "skillsbench",
    "multi-utility": "skillsbench",
    "multi_utility": "skillsbench",
    "multi utility": "skillsbench",
    "artifact-first": "skillsbench",
    "artifact_first": "skillsbench",
}

# Pi-Bench's reference/strong agents advertise this extension so the green/gateway
# can bootstrap policy/tools/context instead of treating the agent as a plain text bot.
PI_BENCH_POLICY_BOOTSTRAP_EXTENSION = "urn:pi-bench:policy-bootstrap:v1"

# SkillsBench scoring is filesystem-first in observed BenchFlow standard-v1 runs:
# the verifier checks real files written inside the task sandbox.  Keep the
# legacy artifact extension as a diagnostic/compatibility hint only.
SKILLSBENCH_FILESYSTEM_OUTPUT_EXTENSION = "urn:aegisforge:skillsbench:filesystem-output:v1"
SKILLSBENCH_ARTIFACT_OUTPUT_EXTENSION = "urn:aegisforge:skillsbench:artifact-output:diagnostic-v1"

# Keep these as conservative A2A-compatible card hints. The runtime A2A server may
# override them with SDK-native values, but the legacy card should not omit them.
A2A_PROTOCOL_VERSION = "0.3.0"
A2A_PREFERRED_TRANSPORT = "http"
A2A_DEFAULT_INPUT_MODES = ["text", "file"]
A2A_DEFAULT_OUTPUT_MODES = ["text", "file"]

PI_BENCH_RECORD_DECISIONS = [
    "ALLOW",
    "ALLOW-CONDITIONAL",
    "DENY",
    "ESCALATE",
]

SKILLSBENCH_ARTIFACT_FAMILIES = [
    "text",
    "markdown",
    "json",
    "python",
    "patch",
    "diff",
    "csv",
    "xlsx",
    "docx",
    "pptx",
    "pdf",
    "lean",
    "obj",
    "html",
    "zip",
    "audio",
    "video",
]

SKILLSBENCH_TASK_CATEGORIES = [
    "software-engineering",
    "office-white-collar",
    "natural-science",
    "industrial-physical-systems",
    "media-content-production",
    "finance-economics",
    "mathematics-or-formal-reasoning",
    "cybersecurity",
]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _with_skillsbench(items: list[str]) -> list[str]:
    return _dedupe([*items, "skillsbench"])


def _build_capabilities(config: AppConfig) -> list[str]:
    capabilities = [
        "a2a",
        "judge-friendly",
        "fresh-state",
        "purple-agent",
        "unified-opponent-profiles",
        "assistant-tool-calls",
        "policy-compliance-ops",
        "pibench-policy-bootstrap",
        "pibench-record-decision",

        # SkillsBench / general-purpose filesystem delivery.
        "skillsbench",
        "general-purpose-agent",
        "multi-utility",
        "filesystem-first",
        "filesystem-output",
        "sandbox-file-output",
        "artifact-diagnostic-output",
        "file-generation",
        "file-input",
        "file-output",
        "patch-output",
        "spreadsheet-output",
        "presentation-output",
        "document-output",
        "formal-proof-output",
    ]

    if config.enable_openenv:
        capabilities.append("openenv-adapter")
    if config.enable_tau2:
        capabilities.append("tau2-adapter")
    if config.enable_security:
        capabilities.append("security-adapter")
    if getattr(config, "enable_officeqa", False):
        capabilities.append("officeqa-adapter")
    if getattr(config, "enable_crmarena", False):
        capabilities.append("crmarena-adapter")
    if getattr(config, "enable_skillsbench", False):
        capabilities.append("skillsbench-adapter")

    return _dedupe(capabilities)


def _build_tracks(config: AppConfig) -> list[str]:
    tracks = [config.track]
    for integration in config.enabled_integrations():
        tracks.append(integration)

    selected_tracks = (
        config.selected_opponent_tracks()
        if hasattr(config, "selected_opponent_tracks")
        else list(SELECTED_OPPONENT_TRACKS)
    )
    tracks.extend(selected_tracks)
    tracks.append("skillsbench")

    return _dedupe(tracks)


def _build_extensions() -> list[str]:
    return [
        PI_BENCH_POLICY_BOOTSTRAP_EXTENSION,
        SKILLSBENCH_FILESYSTEM_OUTPUT_EXTENSION,
        SKILLSBENCH_ARTIFACT_OUTPUT_EXTENSION,
    ]


def _build_a2a_skill_tags(config: AppConfig) -> list[str]:
    return _dedupe(
        [
            "agentx-agentbeats",
            "phase2-purple",
            "unified-purple-agent",
            "a2a",
            "pibench",
            "policy-bootstrap",
            "record-decision",
            "skillsbench",
            "skillsbench-leaderboard",
            "benchflow-ai",
            "standard-v1",
            "general-purpose",
            "multi-utility",
            "filesystem-first",
            "filesystem-output",
            "artifact-diagnostic-output",
            "file-output",
            "xlsx",
            "docx",
            "pptx",
            "pdf",
            "patch",
            "lean4",
            "obj",
            *_build_tracks(config),
        ]
    )


def _build_a2a_skills(config: AppConfig) -> list[dict[str, Any]]:
    return [
        {
            "id": "quipuloop.aegisforge.unified_purple",
            "name": "AegisForge Unified Purple Agent",
            "description": (
                "Unified AgentBeats Purple Agent with selected-opponent routing, "
                "including Pi-Bench policy-bootstrap, CyberGym contract preservation, "
                "and SkillsBench general-purpose filesystem-first delivery."
            ),
            "tags": _build_a2a_skill_tags(config),
            "examples": [
                "Handle Pi-Bench policy-compliance tasks using benchmark-provided policy and tools.",
                "Call record_decision as the final Pi-Bench step with ALLOW, ALLOW-CONDITIONAL, DENY, or ESCALATE.",
                "Route OfficeQA, CRMArena, tau2, OSWorld, CyberGym, NetArena, MAizeBargAIn, and MCU tasks through isolated profiles.",
                "For SkillsBench standard-v1 file tasks, write evaluator-visible files in the task sandbox such as patch, xlsx, docx, pptx, pdf, lean, obj, json, or markdown outputs; keep A2A artifact refs diagnostic-only.",
            ],
        },
        {
            "id": "quipuloop.aegisforge.skillsbench",
            "name": "AegisForge SkillsBench General-Purpose Filesystem Solver",
            "description": (
                "Filesystem-first general-purpose route for SkillsBench tasks spanning "
                "software engineering, office automation, spreadsheets, slides, documents, "
                "science, media conversion, finance/economics, formal reasoning, and defensive cybersecurity."
            ),
            "tags": [
                "skillsbench",
                "standard-v1",
                "general-purpose",
                "multi-utility",
                "filesystem-output",
                "artifact-diagnostic-output",
                "file-generation",
                "patch",
                "xlsx",
                "docx",
                "pptx",
                "pdf",
                "lean",
                "obj",
            ],
            "examples": [
                "fix-build-agentops -> produce a minimal patch or modified source artifact.",
                "xlsx-recover-data -> produce an xlsx/csv artifact.",
                "pptx-reference-formatting -> produce a pptx artifact.",
                "lean4-proof -> produce a .lean proof artifact.",
                "threejs-to-obj -> produce an .obj artifact.",
            ],
        },
    ]


def _build_pibench_metadata() -> dict[str, Any]:
    return {
        "policy_bootstrap_extension": PI_BENCH_POLICY_BOOTSTRAP_EXTENSION,
        "record_decision_tool": "record_decision",
        "record_decision_is_final_step": True,
        "record_decision_decisions": list(PI_BENCH_RECORD_DECISIONS),
        # The first-place logs use `rationale`; keep that spelling explicit.
        "record_decision_arguments": ["decision", "rationale"],
        "decision_channel": "assistant_tool_calls",
        "expected_tool_call_shape": {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_<opaque>",
                    "type": "function",
                    "function": {
                        "name": "record_decision",
                        "arguments": "{\"decision\":\"ESCALATE\",\"rationale\":\"...\"}",
                    },
                }
            ],
        },
    }


def _build_skillsbench_metadata() -> dict[str, Any]:
    return {
        "version": AGENT_CARD_VERSION,
        "filesystem_output_extension": SKILLSBENCH_FILESYSTEM_OUTPUT_EXTENSION,
        "artifact_output_extension": SKILLSBENCH_ARTIFACT_OUTPUT_EXTENSION,
        "track": "skillsbench",
        "benchmark": "SkillsBench",
        "task_set": "standard-v1",
        "condition": "with_skills",
        "route": "general_purpose_filesystem_first",
        "filesystem_output_primary": True,
        "artifact_refs_diagnostic_only": True,
        "a2a_file_part_optional_diagnostic": True,
        "artifact_first": False,
        "requires_file_output_for_artifact_native_tasks": True,
        "known_output_roots": [
            "/root",
            "/root/output",
            "/app/workspace",
            "/app/output",
            "/output",
            "/workspace",
            "/home/github/build/failed",
            "/logs/verifier",
        ],
        "artifact_families": list(SKILLSBENCH_ARTIFACT_FAMILIES),
        "task_categories": list(SKILLSBENCH_TASK_CATEGORIES),
        "solver_aligned_families": [
            "json_output",
            "csv_output",
            "code_solution",
            "office_xlsx",
            "office_docx",
            "pdf_document",
            "lean_solution",
            "security_config",
            "general_file_output",
        ],
        "task_specific_routes": [
            "dialogue-parser",
            "citation-check",
            "court-form-filling",
            "offer-letter-generator",
            "powerlifting-coef-calc",
        ],
        "minimum_contract": {
            "status_text": "concise",
            "file_tasks": "write real evaluator-visible files inside the SkillsBench task sandbox",
            "artifact_refs": "diagnostic_only; not the primary scoring channel and may be empty in official results",
            "a2a_file_part": "optional_diagnostic_compatibility",
            "filesystem_outputs": "primary scoring channel",
        },
        "output_contract": {
            "primary_channel": "filesystem",
            "diagnostic_channel": "a2a_artifact_refs",
            "do_not_assume": [
                "non_empty_artifact_refs_are_required_for_score",
                "FilePart_is_the_primary_scoring_channel",
            ],
        },
        "non_regression_guards": {
            "cybergym": "do not alter the single Artifact(name='PoC') / FilePart(name='poc') contract",
            "maizebargain": "do not alter the stable EF1-repair baseline unless explicitly requested",
            "pibench": "preserve record_decision assistant tool-call discovery metadata",
        },
    }


def build_agent_card(config: AppConfig) -> AgentCardPayload:
    selected_tracks = (
        config.selected_opponent_tracks()
        if hasattr(config, "selected_opponent_tracks")
        else list(SELECTED_OPPONENT_TRACKS)
    )
    selected_tracks = _with_skillsbench(list(selected_tracks))

    aliases = config.track_aliases() if hasattr(config, "track_aliases") else dict(TRACK_ALIASES)
    aliases = {**TRACK_ALIASES, **aliases}
    extensions = _build_extensions()

    return AgentCardPayload(
        id=config.agent_id,
        name=config.agent_name,
        version=config.agent_version,
        description=config.agent_description,
        url=config.public_url,
        health_url=config.health_url(),
        capabilities=_build_capabilities(config),
        tracks=_build_tracks(config),
        integrations=config.enabled_integrations(),
        metadata={
            "provider": "AegisForge",
            "runtime": "python",
            "transport": "http",
            "card_path": config.agent_card_path,
            "git_sha": config.git_sha,
            "image_ref": config.image_ref,
            "selected_opponent_tracks": selected_tracks,
            "track_aliases": aliases,
            "extensions": extensions,
            "a2a": {
                "protocolVersion": A2A_PROTOCOL_VERSION,
                "preferredTransport": A2A_PREFERRED_TRANSPORT,
                "defaultInputModes": list(A2A_DEFAULT_INPUT_MODES),
                "defaultOutputModes": list(A2A_DEFAULT_OUTPUT_MODES),
                "extensions": extensions,
                "skills": _build_a2a_skills(config),
            },
            "pibench": _build_pibench_metadata(),
            "skillsbench": _build_skillsbench_metadata(),
            "note": (
                "mcu and mcu-minecraft are aliases for the same selected Game Agent opponent; "
                "skillsbench is the general-purpose standard-v1 filesystem-first evaluator route."
            ),
        },
    )


def _as_response_dict(card: AgentCardPayload, config: AppConfig) -> dict[str, Any]:
    payload = card.to_dict()

    # Preserve the original list-style capabilities for legacy consumers under
    # capability_tags, then add A2A-compatible fields for gateways/checkers that
    # read this legacy card directly.
    capability_tags = payload.get("capabilities", [])
    if isinstance(capability_tags, list):
        payload.setdefault("capability_tags", list(capability_tags))

    payload.setdefault("protocolVersion", A2A_PROTOCOL_VERSION)
    payload.setdefault("preferredTransport", A2A_PREFERRED_TRANSPORT)
    payload.setdefault("defaultInputModes", list(A2A_DEFAULT_INPUT_MODES))
    payload.setdefault("defaultOutputModes", list(A2A_DEFAULT_OUTPUT_MODES))
    payload.setdefault("extensions", _build_extensions())
    payload.setdefault("skills", _build_a2a_skills(config))

    # A2A AgentCard.capabilities is object-shaped. If the legacy card already
    # exposed list-style tags at this key, keep those tags under capability_tags.
    payload["capabilities"] = {
        # Legacy smoke-test/runtime flags.
        "a2a": True,
        "judge-friendly": True,
        "fresh-state": True,
        "purple-agent": True,

        # A2A object-shaped runtime capabilities.
        "streaming": True,
        "pushNotification": False,
        "pushNotifications": False,
        "stateTransitionHistory": False,

        # Pi-Bench discovery hints.
        "toolCalls": True,
        "assistantToolCalls": True,

        # SkillsBench / general-purpose filesystem discovery hints.
        "fileInput": True,
        "fileOutput": True,
        "filesystemOutput": True,
        "sandboxFileOutput": True,
        "artifactOutput": True,
        "artifactOutputDiagnosticOnly": True,
        "multiArtifactOutput": True,
    }

    metadata = payload.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata.setdefault("extensions", _build_extensions())
        metadata.setdefault("pibench", _build_pibench_metadata())
        metadata.setdefault("skillsbench", _build_skillsbench_metadata())
        metadata.setdefault(
            "a2a",
            {
                "protocolVersion": A2A_PROTOCOL_VERSION,
                "preferredTransport": A2A_PREFERRED_TRANSPORT,
                "defaultInputModes": list(A2A_DEFAULT_INPUT_MODES),
                "defaultOutputModes": list(A2A_DEFAULT_OUTPUT_MODES),
                "extensions": _build_extensions(),
                "skills": _build_a2a_skills(config),
            },
        )

    return payload


def agent_card_response_dict(config: AppConfig) -> dict[str, object]:
    return _as_response_dict(build_agent_card(config), config)



def validate_agent_card_selftest() -> dict[str, Any]:
    """Validate that SkillsBench card metadata is filesystem-first while CyberGym remains separate."""

    metadata = _build_skillsbench_metadata()
    extensions = _build_extensions()
    errors: list[str] = []

    if SKILLSBENCH_FILESYSTEM_OUTPUT_EXTENSION not in extensions:
        errors.append("missing SkillsBench filesystem-output extension")
    if not metadata.get("filesystem_output_primary"):
        errors.append("SkillsBench metadata should mark filesystem_output_primary")
    if not metadata.get("artifact_refs_diagnostic_only"):
        errors.append("SkillsBench metadata should mark artifact_refs_diagnostic_only")
    minimum = metadata.get("minimum_contract", {})
    if isinstance(minimum, dict):
        artifact_refs = str(minimum.get("artifact_refs", "")).lower()
        if "must not be empty" in artifact_refs:
            errors.append("SkillsBench metadata still claims artifact_refs must not be empty")
        if "filesystem" not in str(minimum.get("filesystem_outputs", "")).lower():
            errors.append("SkillsBench metadata missing filesystem output contract")
    else:
        errors.append("minimum_contract is not a dict")
    if "cybergym" not in metadata.get("non_regression_guards", {}):
        errors.append("missing CyberGym non-regression guard")

    return {
        "ok": not errors,
        "errors": errors,
        "version": AGENT_CARD_VERSION,
        "extensions": extensions,
        "skillsbench": metadata,
    }


__all__ = [
    "AGENT_CARD_VERSION",
    "SELECTED_OPPONENT_TRACKS",
    "TRACK_ALIASES",
    "PI_BENCH_POLICY_BOOTSTRAP_EXTENSION",
    "SKILLSBENCH_FILESYSTEM_OUTPUT_EXTENSION",
    "SKILLSBENCH_ARTIFACT_OUTPUT_EXTENSION",
    "A2A_PROTOCOL_VERSION",
    "A2A_PREFERRED_TRANSPORT",
    "A2A_DEFAULT_INPUT_MODES",
    "A2A_DEFAULT_OUTPUT_MODES",
    "build_agent_card",
    "agent_card_response_dict",
    "validate_agent_card_selftest",
]
