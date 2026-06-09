from __future__ import annotations

import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


sys.path.insert(0, str(_repo_root() / "src"))


def test_solver_registry_includes_pptx_and_core_families() -> None:
    from aegisforge.adapters.skillsbench.solvers import (
        default_solver_registry,
        validate_solver_registry_selftest,
    )

    registry = default_solver_registry()
    required = {
        "json_output",
        "csv_output",
        "code_solution",
        "office_xlsx",
        "office_docx",
        "office_pptx",
        "presentation",
        "pptx_output",
        "pdf_document",
        "lean_solution",
        "security_config",
        "dialogue-parser",
        "court-form-filling",
    }

    missing = sorted(required.difference(registry))
    assert not missing, missing

    selftest = validate_solver_registry_selftest()
    assert selftest["ok"], selftest
    assert "office_pptx_solver" in selftest["versions"]


def test_all_solver_selftests_are_available_and_report_structured_results() -> None:
    from aegisforge.adapters.skillsbench.solvers import validate_all_solver_selftests

    result = validate_all_solver_selftests()

    assert "results" in result, result
    assert isinstance(result["results"], dict), result
    assert result["results"], result

    # The detailed selftests are diagnostics. They may expose solver-specific
    # quality gaps during local research, but this test should only fail when
    # the registry cannot call/report them at all.
    for name, item in result["results"].items():
        assert isinstance(item, dict), (name, item)
        assert "ok" in item, (name, item)
        assert "errors" in item, (name, item)
