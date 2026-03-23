from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class PluginContext:
    """Normalized context passed to AegisForge plugins."""

    task_id: str
    track: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PluginResult:
    """Structured result returned by a plugin."""

    ok: bool
    name: str
    output: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class AegisForgePlugin(Protocol):
    """Protocol that concrete plugins should implement."""

    name: str
    supported_tracks: list[str]

    def run(self, context: PluginContext) -> PluginResult:
        ...
