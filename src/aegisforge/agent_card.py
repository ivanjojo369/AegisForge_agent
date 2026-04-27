from __future__ import annotations

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
    "minecraft": "mcu",
    "minecraft-benchmark": "mcu",
    "crmarenapro": "crmarena",
    "entropic-crmarenapro": "crmarena",
    "tau2-agentbeats": "tau2",
    "tau²": "tau2",
    "osworld-green": "osworld",
    "pi-bench": "pibench",
    "cybergym-green": "cybergym",
    "net-arena": "netarena",
}


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


def build_agent_card(config: AppConfig) -> AgentCardPayload:
    selected_tracks = (
        config.selected_opponent_tracks()
        if hasattr(config, "selected_opponent_tracks")
        else list(SELECTED_OPPONENT_TRACKS)
    )
    aliases = config.track_aliases() if hasattr(config, "track_aliases") else dict(TRACK_ALIASES)

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
            "note": "mcu and mcu-minecraft are aliases for the same selected Game Agent opponent.",
        },
    )


def agent_card_response_dict(config: AppConfig) -> dict[str, object]:
    return build_agent_card(config).to_dict()
