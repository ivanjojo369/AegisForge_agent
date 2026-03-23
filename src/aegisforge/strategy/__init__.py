"""Strategy layer for AegisForge.

This package centralizes the decision logic that should not live directly
inside ``agent.py`` or benchmark-specific adapters. The goal is to make
planning, routing, budgeting, and self-checking explicit and reusable
across tracks.

The modules here are intentionally lightweight and dependency-safe so they
can be introduced into an existing repository incrementally.
"""

from .budget_guard import BudgetGuard, BudgetLimits, BudgetState, BudgetStepUsage
from .planner import ExecutionPlan, PlanStep, TaskPlanner
from .router import RouteDecision, TaskRouter
from .self_check import SelfCheck, SelfCheckIssue, SelfCheckResult
from .task_classifier import TaskClassification, TaskClassifier
from .track_profiles import (
    TrackProfile,
    get_track_profile,
    OPENENV_PROFILE,
    SECURITY_PROFILE,
    TAU2_PROFILE,
)

__all__ = [
    "BudgetGuard",
    "BudgetLimits",
    "BudgetState",
    "BudgetStepUsage",
    "ExecutionPlan",
    "PlanStep",
    "RouteDecision",
    "SelfCheck",
    "SelfCheckIssue",
    "SelfCheckResult",
    "TaskClassification",
    "TaskClassifier",
    "TaskPlanner",
    "TaskRouter",
    "TrackProfile",
    "OPENENV_PROFILE",
    "SECURITY_PROFILE",
    "TAU2_PROFILE",
    "get_track_profile",
]
