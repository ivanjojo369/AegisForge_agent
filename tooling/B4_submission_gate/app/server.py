from __future__ import annotations

"""FastAPI server for the B4 Submission Gate.

The original API exposed:
- GET  /health
- POST /check-endpoint
- POST /validate-report

This version keeps those routes and adds Sprint 4-friendly options:
- strict_sprint4 toggle;
- optional JSON/Markdown report writing;
- markdown rendering endpoint;
- explicit validation status.
"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .checks import run_submission_gate
from .reporters import (
    render_markdown_summary,
    write_json_report,
    write_markdown_report,
    write_report_bundle,
)
from .validators import (
    BENCHMARK_NAME,
    DEFAULT_ASSESSMENT_MODE,
    DEFAULT_SCENARIO_FAMILY,
    validate_endpoint_result,
    validate_submission_report,
)


app = FastAPI(
    title="B4 Submission Gate",
    version="0.2.0",
    description=(
        "Local submission-readiness gate for AegisForge. Validates endpoint "
        "health, Agent Card, repository artifacts, Sprint 4 scenario coverage, "
        "telemetry identity preservation, and controlled benchmark safety scope."
    ),
)


class EndpointCheckRequest(BaseModel):
    base_url: str
    card_url: str | None = None
    repo_root: str = "."
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=60.0)
    strict_sprint4: bool = True


class EndpointCheckAndWriteRequest(EndpointCheckRequest):
    json_output_path: str | None = None
    markdown_output_path: str | None = None


class ReportPayload(BaseModel):
    payload: dict[str, Any]


class ReportWritePayload(BaseModel):
    payload: dict[str, Any]
    json_output_path: str | None = None
    markdown_output_path: str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "B4_submission_gate",
        "version": "0.2.0",
        "benchmark": BENCHMARK_NAME,
        "assessment_mode": DEFAULT_ASSESSMENT_MODE,
        "scenario_family": DEFAULT_SCENARIO_FAMILY,
    }


@app.post("/check-endpoint")
def check_endpoint(request: EndpointCheckRequest) -> dict[str, Any]:
    report = run_submission_gate(
        base_url=request.base_url,
        card_url=request.card_url,
        repo_root=request.repo_root,
        timeout=request.timeout_seconds,
        strict_sprint4=request.strict_sprint4,
    )
    validation_errors = validate_endpoint_result(report)
    return {
        "ok": not validation_errors and bool(report.get("ok", False)),
        "validation_ok": not validation_errors,
        "validation_errors": validation_errors,
        "report": report,
    }


@app.post("/check-endpoint/write")
def check_endpoint_and_write(request: EndpointCheckAndWriteRequest) -> dict[str, Any]:
    report = run_submission_gate(
        base_url=request.base_url,
        card_url=request.card_url,
        repo_root=request.repo_root,
        timeout=request.timeout_seconds,
        strict_sprint4=request.strict_sprint4,
    )
    validation_errors = validate_endpoint_result(report)
    written = _write_requested_reports(
        report,
        json_output_path=request.json_output_path,
        markdown_output_path=request.markdown_output_path,
    )

    return {
        "ok": not validation_errors and bool(report.get("ok", False)),
        "validation_ok": not validation_errors,
        "validation_errors": validation_errors,
        "written": written,
        "report": report,
    }


@app.post("/validate-report")
def validate_report(request: ReportPayload) -> dict[str, Any]:
    errors = validate_submission_report(request.payload)
    return {
        "ok": not errors,
        "errors": errors,
    }


@app.post("/render-markdown")
def render_markdown(request: ReportPayload) -> dict[str, Any]:
    """Render a submission-gate report as markdown without writing files."""

    return {
        "ok": True,
        "markdown": render_markdown_summary(request.payload),
    }


@app.post("/write-report")
def write_report(request: ReportWritePayload) -> dict[str, Any]:
    """Write a provided report payload as JSON and/or Markdown."""

    endpoint_errors = validate_endpoint_result(request.payload)
    submission_errors = validate_submission_report(request.payload)

    # A payload may be either an endpoint report or a final submission report.
    # Treat it as valid if either validator accepts it.
    validation_ok = not endpoint_errors or not submission_errors
    written = _write_requested_reports(
        request.payload,
        json_output_path=request.json_output_path,
        markdown_output_path=request.markdown_output_path,
    )

    return {
        "ok": validation_ok,
        "validation_ok": validation_ok,
        "endpoint_validation_errors": endpoint_errors,
        "submission_validation_errors": submission_errors,
        "written": written,
    }


def _write_requested_reports(
    report: dict[str, Any],
    *,
    json_output_path: str | None,
    markdown_output_path: str | None,
) -> dict[str, str]:
    written: dict[str, str] = {}

    if json_output_path and markdown_output_path:
        return write_report_bundle(
            report,
            json_path=json_output_path,
            markdown_path=markdown_output_path,
        )

    if json_output_path:
        written["json"] = str(write_json_report(report, Path(json_output_path)))

    if markdown_output_path:
        written["markdown"] = str(write_markdown_report(report, Path(markdown_output_path)))

    return written
