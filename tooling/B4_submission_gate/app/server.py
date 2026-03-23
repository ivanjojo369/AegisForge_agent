from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .checks import run_submission_gate
from .validators import validate_endpoint_result, validate_submission_report

app = FastAPI(title="B4 Submission Gate", version="0.1.0")


class EndpointCheckRequest(BaseModel):
    base_url: str
    card_url: str | None = None
    repo_root: str = "."
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=60.0)


class ReportPayload(BaseModel):
    payload: dict[str, Any]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "B4_submission_gate"}


@app.post("/check-endpoint")
def check_endpoint(request: EndpointCheckRequest) -> dict[str, Any]:
    report = run_submission_gate(
        base_url=request.base_url,
        card_url=request.card_url,
        repo_root=request.repo_root,
        timeout=request.timeout_seconds,
    )
    return {
        "ok": True,
        "validation_errors": validate_endpoint_result(report),
        "report": report,
    }


@app.post("/validate-report")
def validate_report(request: ReportPayload) -> dict[str, Any]:
    errors = validate_submission_report(request.payload)
    return {"ok": not errors, "errors": errors}
