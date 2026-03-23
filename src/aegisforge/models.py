from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class HealthPayload:
    status: str
    service: str
    version: str
    public_url: str
    environment: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.environment is None:
            data.pop("environment", None)
        if not self.metadata:
            data.pop("metadata", None)
        return data


@dataclass(slots=True)
class AgentCardPayload:
    id: str
    name: str
    version: str
    description: str
    url: str
    health_url: str
    capabilities: list[str] = field(default_factory=list)
    tracks: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RuntimeSummary:
    host: str
    port: int
    agent_port: int
    environment: str
    log_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    