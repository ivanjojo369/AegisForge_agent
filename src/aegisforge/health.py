from __future__ import annotations

from .config import AppConfig
from .models import HealthPayload


def build_health_payload(config: AppConfig) -> HealthPayload:
    return HealthPayload(
        status="ok",
        service=config.agent_id,
        version=config.agent_version,
        public_url=config.public_url,
        environment=config.environment,
        metadata={
            "track": config.track,
            "git_sha": config.git_sha,
            "image_ref": config.image_ref,
            "integrations": config.enabled_integrations(),
        },
    )


def health_response_dict(config: AppConfig) -> dict[str, object]:
    return build_health_payload(config).to_dict()
