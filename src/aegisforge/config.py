from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from .models import RuntimeSummary
from .utils.validation import require_int_in_range, require_non_empty_string, validate_http_url


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class AppConfig:
    host: str
    port: int
    agent_port: int
    log_level: str
    environment: str
    public_url: str

    agent_id: str
    agent_name: str
    agent_version: str
    agent_description: str

    health_path: str
    agent_card_path: str

    enable_openenv: bool
    enable_tau2: bool
    enable_security: bool
    enable_officeqa: bool
    enable_crmarena: bool

    officeqa_data_dir: str
    officeqa_enable_rag: bool
    officeqa_answer_format: str

    crmarena_data_dir: str
    crmarena_enable_guardrails: bool
    crmarena_extraction_policy: str

    git_sha: str
    image_ref: str
    track: str

    @classmethod
    def from_env(cls) -> "AppConfig":
        host = require_non_empty_string(os.environ.get("HOST", "0.0.0.0"), "HOST")
        port = require_int_in_range(int(os.environ.get("PORT", "8000")), "PORT", minimum=1, maximum=65535)
        agent_port = require_int_in_range(
            int(os.environ.get("AGENT_PORT", str(port))),
            "AGENT_PORT",
            minimum=1,
            maximum=65535,
        )

        log_level = require_non_empty_string(os.environ.get("LOG_LEVEL", "INFO"), "LOG_LEVEL").upper()
        environment = require_non_empty_string(os.environ.get("ENVIRONMENT", "development"), "ENVIRONMENT")

        public_url = validate_http_url(
            os.environ.get("AEGISFORGE_PUBLIC_URL", f"http://127.0.0.1:{port}"),
            "AEGISFORGE_PUBLIC_URL",
        )

        agent_id = require_non_empty_string(os.environ.get("AEGISFORGE_AGENT_ID", "aegisforge"), "AEGISFORGE_AGENT_ID")
        agent_name = require_non_empty_string(
            os.environ.get("AEGISFORGE_AGENT_NAME", "AegisForge"),
            "AEGISFORGE_AGENT_NAME",
        )
        agent_version = require_non_empty_string(
            os.environ.get("AEGISFORGE_AGENT_VERSION", "0.1.0"),
            "AEGISFORGE_AGENT_VERSION",
        )
        agent_description = require_non_empty_string(
            os.environ.get(
                "AEGISFORGE_AGENT_DESCRIPTION",
                "Submission-ready A2A purple agent runtime for AgentBeats-compatible evaluations.",
            ),
            "AEGISFORGE_AGENT_DESCRIPTION",
        )

        health_path = require_non_empty_string(os.environ.get("HEALTH_PATH", "/health"), "HEALTH_PATH")
        agent_card_path = require_non_empty_string(
            os.environ.get("AGENT_CARD_PATH", "/.well-known/agent-card.json"),
            "AGENT_CARD_PATH",
        )

        return cls(
            host=host,
            port=port,
            agent_port=agent_port,
            log_level=log_level,
            environment=environment,
            public_url=public_url,
            agent_id=agent_id,
            agent_name=agent_name,
            agent_version=agent_version,
            agent_description=agent_description,
            health_path=health_path,
            agent_card_path=agent_card_path,
            enable_openenv=_read_bool_env("AEGISFORGE_ENABLE_OPENENV", False),
            enable_tau2=_read_bool_env("AEGISFORGE_ENABLE_TAU2", False),
            enable_security=_read_bool_env("AEGISFORGE_ENABLE_SECURITY", False),
            enable_officeqa=_read_bool_env("AEGISFORGE_ENABLE_OFFICEQA", False),
            enable_crmarena=_read_bool_env("AEGISFORGE_ENABLE_CRMARENA", False),
            officeqa_data_dir=os.environ.get("AEGISFORGE_OFFICEQA_DATA_DIR", "").strip(),
            officeqa_enable_rag=_read_bool_env("AEGISFORGE_OFFICEQA_ENABLE_RAG", False),
            officeqa_answer_format=os.environ.get("AEGISFORGE_OFFICEQA_ANSWER_FORMAT", "<solution>/<answer>").strip()
            or "<solution>/<answer>",
            crmarena_data_dir=os.environ.get("AEGISFORGE_CRMARENA_DATA_DIR", "").strip(),
            crmarena_enable_guardrails=_read_bool_env("AEGISFORGE_CRMARENA_ENABLE_GUARDRAILS", True),
            crmarena_extraction_policy=os.environ.get("AEGISFORGE_CRMARENA_EXTRACTION_POLICY", "deny").strip()
            or "deny",
            git_sha=os.environ.get("AEGISFORGE_GIT_SHA", "dev").strip() or "dev",
            image_ref=os.environ.get("AEGISFORGE_IMAGE_REF", "local/aegisforge:dev").strip() or "local/aegisforge:dev",
            track=os.environ.get("AEGISFORGE_TRACK", "purple").strip() or "purple",
        )

    def runtime_summary(self) -> RuntimeSummary:
        return RuntimeSummary(
            host=self.host,
            port=self.port,
            agent_port=self.agent_port,
            environment=self.environment,
            log_level=self.log_level,
        )

    def enabled_integrations(self) -> list[str]:
        integrations: list[str] = []
        if self.enable_openenv:
            integrations.append("openenv")
        if self.enable_tau2:
            integrations.append("tau2")
        if self.enable_security:
            integrations.append("security_arena")
        if self.enable_officeqa:
            integrations.append("officeqa")
        if self.enable_crmarena:
            integrations.append("crmarena")
        return integrations

    def health_url(self) -> str:
        return f"{self.public_url}{self.health_path}"

    def agent_card_url(self) -> str:
        return f"{self.public_url}{self.agent_card_path}"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
    