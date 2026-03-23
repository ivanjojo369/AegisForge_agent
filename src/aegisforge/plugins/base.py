from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PluginContext:
    agent_name: str
    runtime_config: dict[str, Any] = field(default_factory=dict)


class BasePlugin(ABC):
    """
    Minimal plugin base for optional AegisForge extensions.

    Plugins should remain small, explicit, and side-effect-light.
    They are helpers around the runtime, not a replacement for the core
    agent / executor / server contract.
    """

    name: str = "base"
    version: str = "0.1.0"
    enabled: bool = True

    def __init__(self, context: PluginContext | None = None) -> None:
        self.context = context or PluginContext(agent_name="AegisForge")

    @abstractmethod
    def setup(self) -> None:
        """Initialize plugin resources."""

    @abstractmethod
    def teardown(self) -> None:
        """Release plugin resources."""

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "enabled": self.enabled,
        }
    