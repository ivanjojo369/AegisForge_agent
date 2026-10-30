"""Authenticated, localhost-only HTTP demonstration API for AegisForge Nexus.

Exact repository path: src/aegisforge/nexus/api.py

Endpoints:
    GET  /health                        public liveness, no data
    GET  /nexus/capabilities            authenticated capability description
    POST /nexus/prepare                 one planner inference, no tools
    GET  /nexus/plans/{plan_id}         inspect prepared plan and lifecycle
    POST /nexus/plans/{plan_id}/preview tool-free preview (repeatable)
    POST /nexus/plans/{plan_id}/run     one-shot, execute_tools=False ALWAYS
    GET  /nexus/plans/{plan_id}/result  sanitized final report
    GET  /nexus/plans/{plan_id}/evidence authenticated in-memory JSONL evidence

No tool handlers can execute from this HTTP API. It intentionally does not
accept approved_mutations, tool_arguments, or independent_check callbacks.
The host must not expose this development API publicly: entrypoint binds to
127.0.0.1, requires a strong bearer token and does not configure CORS.

"prepare" can incur a real Nebius inference charge if a live client is used.
All other endpoints are offline and make no Nebius requests. The plan store
is bounded, volatile, per process and expires; it is NOT durable job storage.
"""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .config import NexusConfig, NexusConfigError
from .executor import NexusExecutor
from .orchestrator import NexusOrchestrator, PreparedNexusPlan, NexusOutcome

_MAX_REQUEST_BYTES = 16 * 1024
_MAX_PENDING = 2


class NexusAPIError(ValueError):
    """Invalid server configuration; no service is started."""


@dataclass(slots=True)
class _Entry:
    prepared: PreparedNexusPlan
    created_at: float
    status: str = "prepared"  # prepared, previewing, running, completed, failed
    outcome: NexusOutcome | None = None


def _strict_keys(value: dict[str, Any], allowed: set[str]) -> None:
    if set(value) - allowed:
        raise HTTPException(status_code=422, detail="Unsupported request fields.")


async def _json_object(request: Request, *, allow_empty: bool = False) -> dict[str, Any]:
    """Read a bounded JSON body without retaining unknown client-provided fields."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        if allow_empty and not request.headers.get("content-type") and request.headers.get("content-length") == "0":
            return {}
        raise HTTPException(status_code=415, detail="Content-Type must be application/json.")
    length = request.headers.get("content-length")
    if length is not None:
        try:
            n = int(length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.") from None
        if n > _MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Request body too large.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > _MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Request body too large.")
        data.extend(chunk)
    if not data and allow_empty:
        return {}
    def reject_nonfinite(_value: str) -> None:
        raise ValueError("Non-finite JSON numbers are not permitted")

    try:
        obj = json.loads(data.decode("utf-8"), parse_constant=reject_nonfinite)
    except (UnicodeError, ValueError):
        raise HTTPException(status_code=400, detail="Malformed JSON.") from None
    if not isinstance(obj, dict):
        raise HTTPException(status_code=422, detail="JSON body must be an object.")
    return obj


def create_app(
    orchestrator: NexusOrchestrator,
    *,
    api_token: str,
    max_plans: int = 20,
    ttl_seconds: int = 900,
    clock: Callable[[], float] = time.monotonic,
) -> Starlette:
    """Assemble a no-tool-over-HTTP API around an injected NexusOrchestrator.

    Constructing the app has no side effects, network requests or file writes.
    The token must be generated/stored by the trusted operator, never by LLM.
    """
    if not isinstance(orchestrator, NexusOrchestrator):
        raise NexusAPIError("orchestrator must be a NexusOrchestrator")
    if not isinstance(api_token, str) or len(api_token) < 32 or not api_token.strip():
        raise NexusAPIError("NEXUS_API_TOKEN must be at least 32 characters.")
    if type(max_plans) is not int or not 1 <= max_plans <= 100:
        raise NexusAPIError("max_plans must be an integer between 1 and 100.")
    if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 3600:
        raise NexusAPIError("ttl_seconds must be an integer between 30 and 3600.")
    if not callable(clock):
        raise NexusAPIError("clock must be callable.")

    store: dict[str, _Entry] = {}
    lock = threading.Lock()
    pending = 0

    def expire() -> None:
        # Always called under lock; an in-flight run/preview is not evicted.
        cutoff = clock() - ttl_seconds
        for plan_id in list(store):
            item = store[plan_id]
            if item.created_at <= cutoff and item.status not in ("previewing", "running"):
                del store[plan_id]

    def require_entry(plan_id: str) -> _Entry:
        # Caller holds lock. Return only high-entropy, server-generated IDs.
        expire()
        item = store.get(plan_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Plan not found or expired.")
        return item

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        supplied = header[len(prefix):] if header.startswith(prefix) else ""
        if not supplied or not hmac.compare_digest(supplied, api_token):
            raise HTTPException(
                status_code=401, detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def capabilities(request: Request) -> Response:
        auth(request)
        return JSONResponse({
            "api_version": "1",
            "provider": "AegisForge Nexus",
            "workflow": ["prepare", "preview", "run", "result", "evidence"],
            "nebius_inference_on_prepare": True,
            "tool_execution_over_http": False,
            "independent_verification_over_http": False,
            "volatile_memory_store": True,
            "ttl_seconds": ttl_seconds,
        })

    async def prepare(request: Request) -> Response:
        nonlocal pending
        auth(request)
        payload = await _json_object(request)
        _strict_keys(payload, {"task", "constraints"})
        task = payload.get("task")
        if not isinstance(task, str) or not task.strip() or len(task) > 10000:
            raise HTTPException(status_code=422, detail="task must be 1-10000 characters.")
        constraints = payload.get("constraints", [])
        if (not isinstance(constraints, list) or len(constraints) > 30 or
            any(not isinstance(s, str) or not s.strip() or len(s) > 1000 for s in constraints)):
            raise HTTPException(status_code=422, detail="constraints must be up to 30 nonempty strings.")
        with lock:
            expire()
            if len(store) + pending >= max_plans or pending >= _MAX_PENDING:
                raise HTTPException(status_code=429, detail="Plan capacity exceeded; retry later.")
            pending += 1
        try:
            # Nebius call is synchronous: isolate from Starlette event loop.
            prepared = await run_in_threadpool(orchestrator.prepare, task, constraints=constraints)
        except Exception:
            with lock:
                pending -= 1
            # Never leak auth headers, provider error bodies or model output.
            raise HTTPException(status_code=502, detail="Planning failed; review secure local diagnostics.") from None
        with lock:
            # Release the pending reservation and insert the new plan atomically.
            pending -= 1
            expire()
            plan_id = secrets.token_urlsafe(24)
            while plan_id in store:
                plan_id = secrets.token_urlsafe(24)
            store[plan_id] = _Entry(prepared=prepared, created_at=clock())
        return JSONResponse({"plan_id": plan_id, "status": "prepared", "plan": prepared.plan.to_dict()}, status_code=201)

    async def get_plan(request: Request) -> Response:
        auth(request)
        with lock:
            item = require_entry(request.path_params["plan_id"])
            result = {"status": item.status, "plan": item.prepared.plan.to_dict(),
                      "consumed": item.prepared.consumed}
        return JSONResponse(result)

    async def preview(request: Request) -> Response:
        auth(request)
        payload = await _json_object(request, allow_empty=True)
        _strict_keys(payload, set())
        with lock:
            item = require_entry(request.path_params["plan_id"])
            if item.status != "prepared":
                raise HTTPException(status_code=409, detail="Plan is not available for preview.")
            item.status = "previewing"
        try:
            report = await run_in_threadpool(orchestrator.preview, item.prepared)
        except Exception:
            raise HTTPException(status_code=422, detail="Preview could not be completed.") from None
        finally:
            with lock:
                if item.status == "previewing":
                    item.status = "prepared"
        # Do not export tool argument data, raw evidence or plan prompt.
        return JSONResponse({
            "status": report.status,
            "tools_invoked": report.tools_invoked,
            "steps": [{"id": r.step_id, "kind": r.kind, "status": r.status, "tool": r.tool}
                      for r in report.records],
        })

    async def run(request: Request) -> Response:
        auth(request)
        payload = await _json_object(request, allow_empty=True)
        _strict_keys(payload, {"execute_tools", "manual_evidence"})
        if payload.get("execute_tools", False) is not False:
            raise HTTPException(status_code=403, detail="Executing tools over HTTP is disabled.")
        attestation = payload.get("manual_evidence", {})
        if (not isinstance(attestation, dict) or len(attestation) > 12 or
            any(not isinstance(k, str) or len(k) > 12 or not isinstance(v, str) or len(v) > 1200
                for k, v in attestation.items())):
            raise HTTPException(status_code=422, detail="Invalid manual_evidence.")
        with lock:
            item = require_entry(request.path_params["plan_id"])
            if item.status != "prepared":
                raise HTTPException(status_code=409, detail="Plan already used or busy.")
            # Consume at API boundary, so concurrent requests cannot replay it.
            item.status = "running"
        try:
            outcome = await run_in_threadpool(
                orchestrator.run, item.prepared,
                execute_tools=False, manual_evidence=attestation,
            )
        except Exception:
            with lock:
                item.status = "failed"
            raise HTTPException(status_code=422, detail="Run failed; prepared plan cannot be replayed.") from None
        with lock:
            item.outcome = outcome
            item.status = "completed"
        return JSONResponse({"status": "completed", **outcome.to_summary()})

    async def result(request: Request) -> Response:
        auth(request)
        with lock:
            item = require_entry(request.path_params["plan_id"])
            if item.outcome is None:
                raise HTTPException(status_code=409, detail="Plan has no completed result.")
            summary = item.outcome.to_summary()
        return JSONResponse(summary)

    async def evidence(request: Request) -> Response:
        auth(request)
        with lock:
            item = require_entry(request.path_params["plan_id"])
            if item.outcome is None:
                raise HTTPException(status_code=409, detail="Plan has no completed evidence.")
            jsonl = item.outcome.evidence.to_jsonl()
        return PlainTextResponse(
            jsonl, media_type="application/x-ndjson; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="nexus-evidence.jsonl"'},
        )

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/nexus/capabilities", capabilities, methods=["GET"]),
        Route("/nexus/prepare", prepare, methods=["POST"]),
        Route("/nexus/plans/{plan_id:str}", get_plan, methods=["GET"]),
        Route("/nexus/plans/{plan_id:str}/preview", preview, methods=["POST"]),
        Route("/nexus/plans/{plan_id:str}/run", run, methods=["POST"]),
        Route("/nexus/plans/{plan_id:str}/result", result, methods=["GET"]),
        Route("/nexus/plans/{plan_id:str}/evidence", evidence, methods=["GET"]),
    ]
    app = Starlette(routes=routes, debug=False)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    async def protect_responses(request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=protect_responses)
    return app


def main() -> None:
    """Run a *local-only*, inference-backed API. No registered tools."""
    try:
        settings = NexusConfig.from_env(require_api_key=True)
        token = os.environ.get("NEXUS_API_TOKEN", "")
        app = create_app(settings.build_orchestrator(NexusExecutor()), api_token=token)
        port = int(os.environ.get("NEXUS_API_PORT", "8011"))
        if not 1 <= port <= 65535:
            raise NexusAPIError("NEXUS_API_PORT must be from 1 to 65535")
    except (NexusConfigError, NexusAPIError, ValueError) as exc:
        raise SystemExit(f"Nexus API configuration error: {exc}") from None
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()


__all__ = ["create_app", "NexusAPIError", "main"]
