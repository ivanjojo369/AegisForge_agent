"""Orchestration layer for AegisForge.

This package makes the episode lifecycle explicit. It separates task-context
normalization, adapter contracts, execution flow, recovery behavior, and
state reset concerns from the higher-level agent shell.
"""

from .contracts import AdapterRequest, AdapterResult, ExecutionEvent
from .episode import EpisodeState, EpisodeStatus
from .execution_loop import ExecutionLoop
from .recovery import RecoveryDecision, RecoveryPolicy
from .state_reset import StateResetManager
from .task_context import TaskContext, TaskContextBuilder

__all__ = [
    "AdapterRequest",
    "AdapterResult",
    "ExecutionEvent",
    "EpisodeState",
    "EpisodeStatus",
    "ExecutionLoop",
    "RecoveryDecision",
    "RecoveryPolicy",
    "StateResetManager",
    "TaskContext",
    "TaskContextBuilder",
]
