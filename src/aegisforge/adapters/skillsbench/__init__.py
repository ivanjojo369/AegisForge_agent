from __future__ import annotations

"""AegisForge SkillsBench adapter package.

This package is the SkillsBench-specific compatibility layer for AegisForge.
It is intentionally separated from the generic A2A executor so that CyberGym,
Pi-Bench, MAizeBargAIn, Tau2, and OpenEnv behavior remain isolated.

Public responsibilities:

- task_catalog.py: public standard-v1 taxonomy and task-family routing.
- workspace.py: safe task workspace discovery, bounded reads, and output writes.
- contract.py: normalize SkillsBench/AgentBeats request and response shapes.
- harness.py: choose task-family strategies without hardcoding answers.
- result_emitter.py: persist evaluator-facing outputs/manifests.
- adapter.py: single high-level entry point used by agent.py/executor.py.

This module only exports stable, low-level helpers that already exist.  It uses
lazy imports for future files so importing ``aegisforge.adapters.skillsbench``
does not fail while the package is being assembled incrementally.
"""

from importlib import import_module
from typing import Any

PACKAGE_VERSION = "skillsbench_adapter_package_v0_1_2026_06_02"

__all__ = [
    "PACKAGE_VERSION",
    "WORKSPACE_VERSION",
    "TASK_CATALOG_VERSION",
    "TASK_SET_NAME",
    "TASK_SET_CONDITION",
    "TASK_COUNT",
    "SkillsBenchTaskProfile",
    "SkillsBenchWorkspace",
    "WorkspaceFile",
    "WorkspaceSnapshot",
    "build_workspace_context",
    "catalog_summary",
    "classify_task",
    "discover_output_root",
    "discover_workspace_root",
    "extract_task_id",
    "get_task_profile",
    "infer_family_from_signals",
    "infer_task_id",
    "normalize_task_id",
    "preferred_outputs_for_family",
    "validate_catalog",
    "get_contract_module",
    "get_harness_module",
    "get_result_emitter_module",
    "get_adapter_module",
]


def _load_attr(module_name: str, attr_name: str) -> Any:
    module = import_module(f"{__name__}.{module_name}")
    return getattr(module, attr_name)


def __getattr__(name: str) -> Any:
    """Lazy-export package symbols without importing unfinished modules.

    ``task_catalog.py`` and ``workspace.py`` are the first stable pieces of the
    SkillsBench package.  Contract/harness/emitter/adapter modules will be added
    next, so they are intentionally exposed through explicit loader functions.
    """

    task_catalog_exports = {
        "TASK_CATALOG_VERSION",
        "TASK_SET_NAME",
        "TASK_SET_CONDITION",
        "TASK_COUNT",
        "SkillsBenchTaskProfile",
        "catalog_summary",
        "classify_task",
        "extract_task_id",
        "get_task_profile",
        "infer_family_from_signals",
        "normalize_task_id",
        "preferred_outputs_for_family",
        "validate_catalog",
    }
    if name in task_catalog_exports:
        return _load_attr("task_catalog", name)

    workspace_exports = {
        "WORKSPACE_VERSION",
        "SkillsBenchWorkspace",
        "WorkspaceFile",
        "WorkspaceSnapshot",
        "build_workspace_context",
        "discover_output_root",
        "discover_workspace_root",
        "infer_task_id",
    }
    if name in workspace_exports:
        return _load_attr("workspace", name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_contract_module() -> Any:
    """Return the future contract module when it exists."""

    return import_module(f"{__name__}.contract")


def get_harness_module() -> Any:
    """Return the future harness module when it exists."""

    return import_module(f"{__name__}.harness")


def get_result_emitter_module() -> Any:
    """Return the future result_emitter module when it exists."""

    return import_module(f"{__name__}.result_emitter")


def get_adapter_module() -> Any:
    """Return the future high-level adapter module when it exists."""

    return import_module(f"{__name__}.adapter")


def package_status() -> dict[str, Any]:
    """Small diagnostic used by health checks and tests."""

    status: dict[str, Any] = {
        "package": __name__,
        "version": PACKAGE_VERSION,
        "stable_modules": ["task_catalog", "workspace"],
        "planned_modules": ["contract", "harness", "result_emitter", "adapter"],
    }
    try:
        catalog = import_module(f"{__name__}.task_catalog")
        status["task_catalog"] = catalog.validate_catalog()
    except Exception as exc:  # pragma: no cover - defensive diagnostics
        status["task_catalog"] = {"ok": False, "error": str(exc)}

    try:
        workspace = import_module(f"{__name__}.workspace")
        status["workspace_version"] = getattr(workspace, "WORKSPACE_VERSION", "")
        status["workspace_ok"] = True
    except Exception as exc:  # pragma: no cover - defensive diagnostics
        status["workspace_ok"] = False
        status["workspace_error"] = str(exc)

    return status
