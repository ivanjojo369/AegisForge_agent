from __future__ import annotations

"""SkillsBench task-specific solver registry."""

try:
    from .fix_build_solver import (
        FIX_BUILD_SOLVER_VERSION,
        default_solver_registry,
        solve_fix_build_task,
        validate_fix_build_solver_selftest,
    )
except Exception:  # pragma: no cover - keep package import resilient
    FIX_BUILD_SOLVER_VERSION = "unavailable"

    def solve_fix_build_task(*args, **kwargs):  # type: ignore
        raise RuntimeError("fix_build_solver unavailable")

    def default_solver_registry() -> dict:
        return {}

    def validate_fix_build_solver_selftest() -> dict:
        return {"ok": False, "errors": ["fix_build_solver unavailable"]}


__all__ = [
    "FIX_BUILD_SOLVER_VERSION",
    "solve_fix_build_task",
    "default_solver_registry",
    "validate_fix_build_solver_selftest",
]
