from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass, field
from .events import TelemetryEvent

@dataclass
class InMemoryEventSink:
    events: list[TelemetryEvent] = field(default_factory=list)

    def __call__(self, event: TelemetryEvent) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()

class EventEmitter:
    def __init__(self, *sinks: Callable[[TelemetryEvent], None]) -> None:
        self._sinks = list(sinks)

    def add_sink(self, sink: Callable[[TelemetryEvent], None]) -> None:
        self._sinks.append(sink)

    def emit(self, event: TelemetryEvent) -> None:
        for sink in self._sinks:
            sink(event)

    def has_sinks(self) -> bool:
        return bool(self._sinks)
