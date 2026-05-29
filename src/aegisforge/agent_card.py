from __future__ import annotations

from typing import Any

from .config import AppConfig
from .models import AgentCardPayload


# Canonical selected-opponent tracks. "mcu" covers mcu-minecraft/Minecraft Benchmark.
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
    "net-arena": "netarena",
    "net_arena": "netarena",
}

# Pi-Bench's reference/strong agents advertise this extension so the green/gateway
# can bootstrap policy/tools/context instead of treating the agent as a plain text bot.
PI_BENCH_POLICY_BOOTSTRAP_EXTENSION = "urn:pi-bench:policy-bootstrap:v1"

# Keep these as conservative A2A-compatible card hints. The runtime A2A server may
# override them with SDK-native values, but the legacy card should not omit them.
A2A_PROTOCOL_VERSION = "0.3.0"
A2A_PREFERRED_TRANSPORT = "http"
A2A_DEFAULT_INPUT_MODES = ["text"]
A2A_DEFAULT_OUTPUT_MODES = ["text"]

PI_BENCH_RECORD_DECISIONS = [
    "ALLOW",
    "ALLOW-CONDITIONAL",
    "DENY",
    "ESCALATE",
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

    return _dedupe(tracks)


def _build_extensions() -> list[str]:
    return [PI_BENCH_POLICY_BOOTSTRAP_EXTENSION]


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
                "including Pi-Bench policy-bootstrap and final record_decision support."
            ),
            "tags": _build_a2a_skill_tags(config),
            "examples": [
                "Handle Pi-Bench policy-compliance tasks using benchmark-provided policy and tools.",
                "Call record_decision as the final Pi-Bench step with ALLOW, ALLOW-CONDITIONAL, DENY, or ESCALATE.",
                "Route OfficeQA, CRMArena, tau2, OSWorld, CyberGym, NetArena, MAizeBargAIn, and MCU tasks through isolated profiles.",
            ],
        }
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


def build_agent_card(config: AppConfig) -> AgentCardPayload:
    selected_tracks = (
        config.selected_opponent_tracks()
        if hasattr(config, "selected_opponent_tracks")
        else list(SELECTED_OPPONENT_TRACKS)
    )
    aliases = config.track_aliases() if hasattr(config, "track_aliases") else dict(TRACK_ALIASES)
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
            "note": "mcu and mcu-minecraft are aliases for the same selected Game Agent opponent.",
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
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    }

    metadata = payload.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata.setdefault("extensions", _build_extensions())
        metadata.setdefault("pibench", _build_pibench_metadata())
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
