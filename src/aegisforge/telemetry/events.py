from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
import time

class EventLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

@dataclass(slots=True)
class TelemetryEvent:
    name: str
    phase: str
    message: str
    level: EventLevel = EventLevel.INFO
    timestamp: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "phase": self.phase,
            "message": self.message,
            "level": self.level.value,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

def make_event(name: str, phase: str, message: str, *, level: EventLevel = EventLevel.INFO, payload: Mapping[str, Any] | None = None) -> TelemetryEvent:
    return TelemetryEvent(name=name, phase=phase, message=message, level=level, payload=dict(payload or {}))
