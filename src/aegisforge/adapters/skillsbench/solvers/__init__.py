from __future__ import annotations

"""SkillsBench task-specific solver registry.

This module is intentionally import-resilient.  A single broken optional solver
must not make the whole SkillsBench adapter unavailable.

Registry contract:
- keys may be task ids, family names, or legacy family aliases;
- values are solver callables that accept:
    (contract, environment, metadata, prompt)
  and return TaskWorkspaceExecution-like objects;
- default_solver_registry() is the public entrypoint used by the harness /
  task_workspace_executor bridge.
"""

from typing import Any, Callable, Mapping


SolverCallable = Callable[..., Any]

SOLVER_REGISTRY_VERSION = "skillsbench_solver_registry_v0_3_pptx_dispatch_2026_06_09"


# ---------------------------------------------------------------------------
# Optional solver imports
# ---------------------------------------------------------------------------

try:
    from .fix_build_solver import (
        FIX_BUILD_SOLVER_VERSION,
        default_solver_registry as _fix_build_default_solver_registry,
        solve_fix_build_task,
        validate_fix_build_solver_selftest,
    )
except Exception:  # pragma: no cover - keep package import resilient
    FIX_BUILD_SOLVER_VERSION = "unavailable"

    def _fix_build_default_solver_registry() -> dict[str, SolverCallable]:  # type: ignore
        return {}

    def solve_fix_build_task(*args: Any, **kwargs: Any) -> Any:  # type: ignore
        raise RuntimeError("fix_build_solver unavailable")

    def validate_fix_build_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["fix_build_solver unavailable"]}


try:
    from .deploy_smoke_solver import (
        DEPLOY_SMOKE_SOLVER_VERSION,
        deploy_smoke_solver,
        validate_deploy_smoke_solver_selftest,
    )
except Exception:  # pragma: no cover
    DEPLOY_SMOKE_SOLVER_VERSION = "unavailable"
    deploy_smoke_solver = None  # type: ignore

    def validate_deploy_smoke_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["deploy_smoke_solver unavailable"]}


try:
    from .json_csv_solver import (
        JSON_CSV_SOLVER_VERSION,
        json_csv_solver,
        validate_json_csv_solver_selftest,
    )
except Exception:  # pragma: no cover
    JSON_CSV_SOLVER_VERSION = "unavailable"
    json_csv_solver = None  # type: ignore

    def validate_json_csv_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["json_csv_solver unavailable"]}


try:
    from .code_solution_solver import (
        CODE_SOLUTION_SOLVER_VERSION,
        code_solution_solver,
        validate_code_solution_solver_selftest,
    )
except Exception:  # pragma: no cover
    CODE_SOLUTION_SOLVER_VERSION = "unavailable"
    code_solution_solver = None  # type: ignore

    def validate_code_solution_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["code_solution_solver unavailable"]}


try:
    from .office_xlsx_solver import (
        OFFICE_XLSX_SOLVER_VERSION,
        office_xlsx_solver,
        validate_office_xlsx_solver_selftest,
    )
except Exception:  # pragma: no cover
    OFFICE_XLSX_SOLVER_VERSION = "unavailable"
    office_xlsx_solver = None  # type: ignore

    def validate_office_xlsx_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["office_xlsx_solver unavailable"]}


try:
    from .office_docx_solver import (
        OFFICE_DOCX_SOLVER_VERSION,
        office_docx_solver,
        validate_office_docx_solver_selftest,
    )
except Exception:  # pragma: no cover
    OFFICE_DOCX_SOLVER_VERSION = "unavailable"
    office_docx_solver = None  # type: ignore

    def validate_office_docx_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["office_docx_solver unavailable"]}


try:
    from .office_pptx_solver import (
        OFFICE_PPTX_SOLVER_VERSION,
        office_pptx_solver,
        validate_office_pptx_solver_selftest,
    )
except Exception:  # pragma: no cover
    OFFICE_PPTX_SOLVER_VERSION = "unavailable"
    office_pptx_solver = None  # type: ignore

    def validate_office_pptx_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["office_pptx_solver unavailable"]}


try:
    from .pdf_form_solver import (
        PDF_FORM_SOLVER_VERSION,
        pdf_form_solver,
        validate_pdf_form_solver_selftest,
    )
except Exception:  # pragma: no cover
    PDF_FORM_SOLVER_VERSION = "unavailable"
    pdf_form_solver = None  # type: ignore

    def validate_pdf_form_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["pdf_form_solver unavailable"]}


try:
    from .lean_solver import (
        LEAN_SOLVER_VERSION,
        lean_solver,
        validate_lean_solver_selftest,
    )
except Exception:  # pragma: no cover
    LEAN_SOLVER_VERSION = "unavailable"
    lean_solver = None  # type: ignore

    def validate_lean_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["lean_solver unavailable"]}


try:
    from .security_config_solver import (
        SECURITY_CONFIG_SOLVER_VERSION,
        security_config_solver,
        validate_security_config_solver_selftest,
    )
except Exception:  # pragma: no cover
    SECURITY_CONFIG_SOLVER_VERSION = "unavailable"
    security_config_solver = None  # type: ignore

    def validate_security_config_solver_selftest() -> dict[str, Any]:  # type: ignore
        return {"ok": False, "errors": ["security_config_solver unavailable"]}


# ---------------------------------------------------------------------------
# Registry construction
# ---------------------------------------------------------------------------

DEPLOY_SMOKE_TASK_IDS: tuple[str, ...] = (
    "citation-check",
    "court-form-filling",
    "dialogue-parser",
    "offer-letter-generator",
    "powerlifting-coef-calc",
)

JSON_CSV_FAMILIES: tuple[str, ...] = (
    "json_output",
    "csv_output",
    "data_json",
    "data_csv",
    "general_file_output",
)

CODE_SOLUTION_FAMILIES: tuple[str, ...] = (
    "code_solution",
    "code_workspace",
)

OFFICE_XLSX_FAMILIES: tuple[str, ...] = (
    "office_xlsx",
    "spreadsheet_office",
    "excel",
    "xlsx_output",
)

OFFICE_DOCX_FAMILIES: tuple[str, ...] = (
    "office_docx",
    "document_generation",
    "document",
    "docx_output",
)

OFFICE_PPTX_FAMILIES: tuple[str, ...] = (
    "office_pptx",
    "presentation",
    "pptx_output",
    "slides",
    "slide_deck",
    "presentation_output",
)

PDF_FORM_FAMILIES: tuple[str, ...] = (
    "pdf_document",
    "pdf_form",
    "pdf_output",
)

LEAN_FAMILIES: tuple[str, ...] = (
    "lean_solution",
    "formal_reasoning",
)

SECURITY_FAMILIES: tuple[str, ...] = (
    "security_config",
    "security_analysis",
    "security_output",
    "cybersecurity",
)


def _register_many(registry: dict[str, SolverCallable], keys: tuple[str, ...], solver: Any) -> None:
    if not callable(solver):
        return
    for key in keys:
        if key:
            registry[str(key)] = solver


def _safe_update_from_fix_build_registry(registry: dict[str, SolverCallable]) -> None:
    try:
        base = _fix_build_default_solver_registry()
    except Exception:
        base = {}
    if isinstance(base, Mapping):
        for key, solver in base.items():
            if key and callable(solver):
                registry[str(key)] = solver


def default_solver_registry() -> dict[str, SolverCallable]:
    """Return the SkillsBench solver dispatch registry.

    The registry supports both task-id routing and family routing.  Task-id
    entries are especially important for `deploy-smoke-v1`; family entries cover
    the broader `standard-v1` task set once output_contract.py classifies tasks.
    """

    registry: dict[str, SolverCallable] = {}

    # Keep any mappings already defined by fix_build_solver.py.
    _safe_update_from_fix_build_registry(registry)

    # Compatibility fallback for build-repair if fix_build_solver's own registry
    # is absent or sparse.
    if callable(solve_fix_build_task):
        registry.setdefault("bugswarm_build_repair", solve_fix_build_task)
        registry.setdefault("software_patch", solve_fix_build_task)
        registry.setdefault("fix_build", solve_fix_build_task)

    _register_many(registry, DEPLOY_SMOKE_TASK_IDS, deploy_smoke_solver)
    _register_many(registry, JSON_CSV_FAMILIES, json_csv_solver)
    _register_many(registry, CODE_SOLUTION_FAMILIES, code_solution_solver)
    _register_many(registry, OFFICE_XLSX_FAMILIES, office_xlsx_solver)
    _register_many(registry, OFFICE_DOCX_FAMILIES, office_docx_solver)
    _register_many(registry, OFFICE_PPTX_FAMILIES, office_pptx_solver)
    _register_many(registry, PDF_FORM_FAMILIES, pdf_form_solver)
    _register_many(registry, LEAN_FAMILIES, lean_solver)
    _register_many(registry, SECURITY_FAMILIES, security_config_solver)

    return registry


def solver_registry_versions() -> dict[str, str]:
    """Return available solver versions for diagnostics/logging."""

    return {
        "registry": SOLVER_REGISTRY_VERSION,
        "fix_build_solver": FIX_BUILD_SOLVER_VERSION,
        "deploy_smoke_solver": DEPLOY_SMOKE_SOLVER_VERSION,
        "json_csv_solver": JSON_CSV_SOLVER_VERSION,
        "code_solution_solver": CODE_SOLUTION_SOLVER_VERSION,
        "office_xlsx_solver": OFFICE_XLSX_SOLVER_VERSION,
        "office_docx_solver": OFFICE_DOCX_SOLVER_VERSION,
        "office_pptx_solver": OFFICE_PPTX_SOLVER_VERSION,
        "pdf_form_solver": PDF_FORM_SOLVER_VERSION,
        "lean_solver": LEAN_SOLVER_VERSION,
        "security_config_solver": SECURITY_CONFIG_SOLVER_VERSION,
    }


def validate_solver_registry_selftest() -> dict[str, Any]:
    """Validate that core registry keys are present when solvers are installed."""

    registry = default_solver_registry()
    required_keys = {
        "dialogue-parser",
        "court-form-filling",
        "offer-letter-generator",
        "powerlifting-coef-calc",
        "citation-check",
        "json_output",
        "csv_output",
        "code_solution",
        "office_xlsx",
        "office_docx",
        "office_pptx",
        "presentation",
        "pdf_document",
        "lean_solution",
        "security_config",
    }
    missing = sorted(key for key in required_keys if key not in registry)
    unavailable = {
        name: version
        for name, version in solver_registry_versions().items()
        if name != "registry" and version == "unavailable"
    }

    return {
        "ok": not missing,
        "version": SOLVER_REGISTRY_VERSION,
        "registry_size": len(registry),
        "missing_required_keys": missing,
        "unavailable_solvers": unavailable,
        "versions": solver_registry_versions(),
        "sample_keys": sorted(registry)[:80],
    }


def validate_all_solver_selftests() -> dict[str, Any]:
    """Run all available solver selftests.

    This is intentionally separate from validate_solver_registry_selftest()
    because some selftests may be slower or depend on repo-local context.
    """

    tests = {
        "fix_build_solver": validate_fix_build_solver_selftest,
        "deploy_smoke_solver": validate_deploy_smoke_solver_selftest,
        "json_csv_solver": validate_json_csv_solver_selftest,
        "code_solution_solver": validate_code_solution_solver_selftest,
        "office_xlsx_solver": validate_office_xlsx_solver_selftest,
        "office_docx_solver": validate_office_docx_solver_selftest,
        "office_pptx_solver": validate_office_pptx_solver_selftest,
        "pdf_form_solver": validate_pdf_form_solver_selftest,
        "lean_solver": validate_lean_solver_selftest,
        "security_config_solver": validate_security_config_solver_selftest,
    }

    results: dict[str, Any] = {}
    errors: list[str] = []
    for name, fn in tests.items():
        try:
            result = fn()
        except Exception as exc:
            result = {"ok": False, "errors": [str(exc)[:500]]}
        results[name] = result
        if not result.get("ok"):
            errors.append(name)

    return {
        "ok": not errors,
        "version": SOLVER_REGISTRY_VERSION,
        "failed": errors,
        "results": results,
    }


__all__ = [
    "SOLVER_REGISTRY_VERSION",
    "SolverCallable",
    "FIX_BUILD_SOLVER_VERSION",
    "DEPLOY_SMOKE_SOLVER_VERSION",
    "JSON_CSV_SOLVER_VERSION",
    "CODE_SOLUTION_SOLVER_VERSION",
    "OFFICE_XLSX_SOLVER_VERSION",
    "OFFICE_DOCX_SOLVER_VERSION",
    "OFFICE_PPTX_SOLVER_VERSION",
    "PDF_FORM_SOLVER_VERSION",
    "LEAN_SOLVER_VERSION",
    "SECURITY_CONFIG_SOLVER_VERSION",
    "solve_fix_build_task",
    "deploy_smoke_solver",
    "json_csv_solver",
    "code_solution_solver",
    "office_xlsx_solver",
    "office_docx_solver",
    "office_pptx_solver",
    "pdf_form_solver",
    "lean_solver",
    "security_config_solver",
    "default_solver_registry",
    "solver_registry_versions",
    "validate_solver_registry_selftest",
    "validate_all_solver_selftests",
    "validate_fix_build_solver_selftest",
    "validate_deploy_smoke_solver_selftest",
    "validate_json_csv_solver_selftest",
    "validate_code_solution_solver_selftest",
    "validate_office_xlsx_solver_selftest",
    "validate_office_docx_solver_selftest",
    "validate_office_pptx_solver_selftest",
    "validate_pdf_form_solver_selftest",
    "validate_lean_solver_selftest",
    "validate_security_config_solver_selftest",
]
