from __future__ import annotations

from .config import AppConfig
from .models import AgentCardPayload


def _build_capabilities(config: AppConfig) -> list[str]:
    capabilities = [
        "a2a",
        "judge-friendly",
        "fresh-state",
        "purple-agent",
    ]

    if config.enable_openenv:
        capabilities.append("openenv-adapter")
    if config.enable_tau2:
        capabilities.append("tau2-adapter")
    if config.enable_security:
        capabilities.append("security-adapter")

    return capabilities


def _build_tracks(config: AppConfig) -> list[str]:
    tracks = [config.track]
    for integration in config.enabled_integrations():
        if integration not in tracks:
            tracks.append(integration)
    return tracks


def build_agent_card(config: AppConfig) -> AgentCardPayload:
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
        },
    )


def agent_card_response_dict(config: AppConfig) -> dict[str, object]:
    return build_agent_card(config).to_dict()
