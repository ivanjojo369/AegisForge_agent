"""Local-only web dashboard for AegisForge Nexus.

Repository path: scripts/nexus/serve_nexus_web.py

OFFLINE (default): serves UI to inspect user-selected JSON/JSONL evidence.
LIVE (--live): same-origin UI plus the *existing* authenticated Nexus API.
The API cannot execute tools, and only /nexus/prepare calls Nebius.

The optional --verified-demo mode serves only fixed calculator/scorecard
regressions and requests separate browser confirmations before every effect.
It never executes arbitrary user-provided commands or chooses arbitrary paths.
Default API remains tool-free.
No raw files are served as static assets. Authorized small-source preview
requires a separate explicit operation and never sends code to a model.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
import hmac
import ipaddress
import json
import re
import secrets
import threading
import traceback
from time import monotonic
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "src" / "aegisforge" / "web" / "nexus"
_ALLOWED_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class _SyntheticRepairSession:
    """Single in-memory fixture run with one human decision per pending effect.

    The runner accepts only the previously reviewed and SHA-bound scorecard
    patch or the fixed calculator; never arbitrary paths or shell commands.
    All Docker effects remain in the existing host.
    """

    def __init__(self, image: str, runner: Any = None) -> None:
        self.image = image
        self._runner = runner
        self._scorecard_job: dict[str, str] | None = None
        self._source_snapshot = False
        self._lock = threading.Lock()
        self._status = "idle"
        self._run_id = ""
        self._pending: dict[str, str] | None = None
        self._decision: bool | None = None
        self._event: threading.Event | None = None
        self._result: dict[str, Any] | None = None
        # Preserve bounded in-process round evidence; never persist source or tokens.
        self._history: list[dict[str, Any]] = []
        self._message = "Listo para una demostración sintética sin Nebius."

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"status": self._status, "run_id": self._run_id,
                    "image": self.image, "synthetic": not self._source_snapshot,
                    "source_snapshot": self._source_snapshot,
                    "nebius_calls": 0, "pending": dict(self._pending) if self._pending else None,
                    "message": self._message,
                    "result": dict(self._result) if self._result else None,
                    "round_history": [dict(item) for item in self._history]}

    def start(self, *, scorecard_job: dict[str, str] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._status in ("running", "awaiting_review"):
                raise HTTPException(409, "A verified repair demonstration is already active")
            self._run_id = secrets.token_urlsafe(20)
            self._scorecard_job = dict(scorecard_job) if scorecard_job else None
            self._source_snapshot = scorecard_job is not None
            self._status = "running"
            self._result = None
            self._pending = None
            self._decision = None
            self._event = None
            self._message = ("Preparando una COPIA TEMPORAL del archivo revisado." if scorecard_job
                             else "Preparando la reparación sintética en un directorio temporal.")
        thread = threading.Thread(target=self._run, name="colossus-local-fixture", daemon=True)
        thread.start()
        return self.snapshot()

    def _review(self, label: str, fingerprint: str) -> bool:
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            return False
        effects = {
            "TRUST TEST REVIEW": (
                "Revisar y aprobar la solicitud exacta y temporal de Trust para "
                "ejecutar test_calculator.py en Docker, sin red ni montaje del "
                "repositorio real."
            ),
            "HOST VALIDATION REVIEW": (
                "Autorizar desde el anfitrión UNA ejecución de test_calculator.py "
                "en Docker aislado. Trust solicitará otra revisión independiente "
                "antes de ejecutar."
            ),
            "HOST CANDIDATE WRITE": (
                "Escribir el parche fijo exclusivamente en dos copias "
                "temporales, nunca en el repositorio original."
            ),
        }
        if label not in effects:
            return False
        if self._source_snapshot:
            effects = {
                "TRUST TEST REVIEW": "Revisar Trust para ejecutar una prueba temporal y autorizada sobre la copia del archivo en Docker sin red.",
                "HOST VALIDATION REVIEW": "Autorizar una ejecución Docker sobre una copia temporal del archivo seleccionado.",
                "HOST CANDIDATE WRITE": "Escribir el parche revisado SOLAMENTE en dos copias temporales; no tocar el repositorio original.",
            }
        event = threading.Event()
        pending = {"request_id": secrets.token_urlsafe(18), "label": label,
                   "fingerprint": fingerprint,
                   "confirmation": "AUTORIZAR " + fingerprint[:12],
                   "effect": effects[label],
                   "patch": (self._scorecard_job.get("relative_path", "scripts/build_scorecard.py")
                             if self._source_snapshot and self._scorecard_job
                             else "calculator.py: return a - b  →  return a + b"),
                   "expires_in_seconds": 480}
        with self._lock:
            self._pending = pending
            self._event = event
            self._decision = None
            self._status = "awaiting_review"
            self._message = "Revisa el efecto y autoriza o rechaza esta operación." 
        approved_in_time = event.wait(timeout=480)
        with self._lock:
            decision = approved_in_time and self._decision is True
            if self._pending is pending:
                self._pending = None
                self._event = None
                self._decision = None
            self._status = "running" if decision else "denied"
            self._message = "Autorización aceptada; continuando." if decision else "Autorización denegada o vencida."
        return decision

    def decide(self, request_id: str, decision: str, confirmation: str) -> dict[str, Any]:
        with self._lock:
            pending = self._pending
            if not pending or self._status != "awaiting_review" or pending["request_id"] != request_id:
                raise HTTPException(409, "Review request not pending")
            if self._event is None or self._event.is_set():
                raise HTTPException(409, "Review request already consumed")
            if decision == "approve":
                if not hmac.compare_digest(confirmation, pending["confirmation"]):
                    raise HTTPException(422, "Confirmation does not match the current review")
                self._decision = True
            elif decision == "deny" and confirmation == "":
                self._decision = False
            else:
                raise HTTPException(422, "Invalid review decision")
            self._event.set()
            return {"accepted": True, "request_id": request_id, "decision": decision}

    def _run(self) -> None:
        try:
            # The existing demonstration executes only its fixed calculator fixture.
            if self._runner is None:
                import importlib.util
                fixture_path = ROOT / "scripts" / "nexus" / "demo_nexus_engineering_host_offline.py"
                spec = importlib.util.spec_from_file_location("colossus_fixture_demo", fixture_path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("Fixture demonstration is unavailable")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                runner = module.run_docker_verified_repair
            else:
                runner = self._runner
            job = self._scorecard_job
            if job is None:
                result = runner(self.image, approval_callback=self._review)
            else:
                # Uses actual authorized source bytes but only temporary checkouts.
                result = runner(
                    self.image, approval_callback=self._review,
                    scorecard_source=job["source"],
                    expected_source_sha256=job["base_sha256"],
                    expected_patchset_sha256=job["patchset_sha256"],
                    **({"reviewed_target_path": job["relative_path"],
                        "reviewed_regression_source": job["regression_source"]}
                       if "regression_source" in job else {}),
                    **({"reviewed_candidate_json": job["candidate_json"],
                        "reviewed_task": job["task"],
                        "reviewed_mission_id": job["mission_id"],
                        "proposal_origin": job["origin"]}
                       if "candidate_json" in job else {}),
                )
            if not isinstance(result, dict) or result.get("synthetic") is not (job is None):
                raise RuntimeError("Unexpected demonstration result")
            # Avoid returning arbitrary output from a future runner revision.
            allowed = {"mode", "image", "synthetic", "nebius_calls", "baseline",
                       "candidate", "verified_repair", "verification_scope",
                       "proof_graph_sha256", "original_untouched",
                       "human_review_required", "authenticity_verified",
                       "source_snapshot", "target_path", "base_sha256",
                       "patchset_sha256", "regression_test_sha256", "proposal_origin"}
            safe_result = {k: v for k, v in result.items() if k in allowed}
            with self._lock:
                self._result = safe_result
                if job is not None:
                    self._history.append({
                        "run_id": self._run_id,
                        "target_path": safe_result.get("target_path"),
                        "base_sha256": safe_result.get("base_sha256"),
                        "patchset_sha256": safe_result.get("patchset_sha256"),
                        "proposal_origin": safe_result.get("proposal_origin"),
                        "baseline": safe_result.get("baseline"),
                        "candidate": safe_result.get("candidate"),
                        "verified_repair": safe_result.get("verified_repair"),
                        "proof_graph_sha256": safe_result.get("proof_graph_sha256"),
                        "authenticity_verified": False,
                    })
                    self._history = self._history[-3:]
                self._status = "completed"
                self._message = (
                    ("Regresión limitada: parche comprobado para este test." if result.get("verified_repair") is True
                     else "La regresión terminó, pero el parche NO quedó verificado; revisa ambos resultados.")
                    if job else "Demostración sintética finalizada; revisa el alcance de su evidencia.")
        except PermissionError:
            with self._lock:
                self._status = "denied"
                self._message = "La operación fue rechazada por Trust. Ninguna reparación se certificó."
        except Exception:
            # Error details only in the local operator terminal, never in HTTP.
            traceback.print_exc()
            with self._lock:
                self._status = "failed"
                self._message = "Falló la demostración; revisa el terminal local."
        finally:
            with self._lock:
                self._pending = None
                self._event = None
                self._decision = None
                self._scorecard_job = None  # release source bytes after one attempt



def build_app(*, live: bool = False, orchestrator: Any = None, token: str | None = None,
              verified_demo: bool = False, demo_image: str | None = None,
              demo_token: str | None = None, demo_runner: Any = None,
              inspect_repo: str | Path | None = None,
              aegisgrid_checkpoint_dir: str | None = None,
              nebius_proposals: bool = False, nebius_client_factory: Any = None) -> Starlette:
    """Build a local UI; real inference is never triggered by app creation."""
    if nebius_proposals and (not verified_demo or inspect_repo is None or live):
        raise ValueError("Nebius code proposals require --verified-demo and --inspect-repo; no --live")
    if nebius_proposals and nebius_client_factory is None and not os.getenv("NEBIUS_API_KEY"):
        raise ValueError("NEBIUS_API_KEY must be set before enabling opt-in Nebius proposals")
    if inspect_repo is not None and not verified_demo:
        raise ValueError("Repository inspection requires the locally authenticated verified demo")
    workspace = None
    if inspect_repo is not None:
        # The root is chosen by the local operator, NEVER by a browser request.
        from aegisforge.nexus.workspace.manager import NexusWorkspaceManager, WorkspaceLimits
        workspace = NexusWorkspaceManager(
            inspect_repo, limits=WorkspaceLimits(max_files=750, max_visited_entries=14000),
            task_id="nexus_web_inspection",
        )
    if verified_demo:
        if live:
            raise ValueError("Keep the live inference API separate from the Docker demo")
        if not isinstance(demo_image, str) or not re.fullmatch(r"colossus-pytest@sha256:[0-9a-f]{64}", demo_image):
            raise ValueError("A local digest-pinned colossus-pytest image is required")
        if (not isinstance(demo_token, str) or not 32 <= len(demo_token) <= 256
                or not demo_token.isascii() or len(set(demo_token)) < 12
                or any(ord(ch) <= 32 or ord(ch) >= 127 for ch in demo_token)):
            raise ValueError("A strong, dedicated local demonstration token is required")
        session = _SyntheticRepairSession(demo_image, runner=demo_runner)
    else:
        session = None
    if live:
        from aegisforge.nexus.api import create_app
        from aegisforge.nexus.executor import NexusExecutor
        from aegisforge.nexus.config import NexusConfig
        if orchestrator is None:
            settings = NexusConfig.from_env(require_api_key=True)
            orchestrator = settings.build_orchestrator(NexusExecutor())
        app = create_app(orchestrator, api_token=token if token is not None else os.getenv("NEXUS_API_TOKEN", ""))
    else:
        app = Starlette(debug=False)
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    from aegisforge.products.aegisgrid.integration import AegisGridIntegration
    grid_integration = AegisGridIntegration(checkpoint_dir=aegisgrid_checkpoint_dir)

    async def web_status(_request: Any) -> Response:
        return JSONResponse({"mode": "live" if live else "offline",
                             "tool_execution_over_http": False,
                             "inference_on_prepare": bool(live),
                             "evidence_files_auto_exposed": False,
                             "synthetic_demo_available": bool(verified_demo),
                             "synthetic_demo_effects_over_http": bool(verified_demo),
                             "synthetic_demo_image": demo_image if verified_demo else None,
                             "repo_inspection_available": workspace is not None,
                             "repo_local_code_preview_available": workspace is not None,
                             "scorecard_proposal_preview_available": workspace is not None,
                             "scorecard_copy_docker_available": workspace is not None and verified_demo,
                             "nebius_code_proposals_enabled": nebius_proposals,
                             "nebius_code_proposal_limit": 2 if nebius_proposals else 0,
                             "aegisgrid_mission_checkpoint_enabled": grid_integration.persistent})

    async def asset(request: Any) -> Response:
        # Only known immutable filenames are accessible, not arbitrary paths.
        route = request.url.path
        if route not in _ALLOWED_FILES:
            return JSONResponse({"error": "not found"}, status_code=404)
        name, media_type = _ALLOWED_FILES[route]
        path = WEB_DIR / name
        if not path.is_file():
            return JSONResponse({"error": "UI file missing"}, status_code=503)
        return FileResponse(path, media_type=media_type)

    reviewed_scorecard: dict[str, Any] | None = None
    # One initial opt-in model attempt; at most ONE explicitly approved revision
    # after a completed, failed Docker candidate. Unknown billing still blocks retries.
    nebius_lock = threading.Lock()
    nebius_consumed = False
    nebius_revision_consumed = False
    catalog_lock = threading.Lock()
    catalog_check_started = False
    catalog_check_result: dict[str, Any] | None = None

    def safe_nebius_failure(exc: Exception) -> str:
        """Categorize without disclosing source, responses, credentials or paths."""
        from aegisforge.nexus.nebius_client import NebiusAPIError, NebiusConfigurationError
        from aegisforge.nexus.engineering.code_proposer import NexusCodeProposalError
        from aegisforge.nexus.engineering.diff_builder import NexusDiffBuilderError
        if isinstance(exc, NebiusConfigurationError):
            return "client_configuration"
        if isinstance(exc, NebiusAPIError):
            status = re.search(r"Nebius returned HTTP ([0-9]{3})\b", str(exc))
            if status:
                return "provider_http_" + status.group(1)
            if "timed out" in str(exc):
                return "provider_timeout"
            if "network request failed" in str(exc):
                return "provider_transport"
            return "provider_response_invalid"
        if isinstance(exc, NexusCodeProposalError):
            # Fixed, non-sensitive diagnoses only. Never echo model text or
            # source code; no retries after an authorized inference.
            reason = str(exc)
            if "truncated" in reason:
                return "proposal_truncated"
            if "empty final content" in reason:
                return "proposal_empty_content"
            if "not valid JSON" in reason or "not a single JSON" in reason:
                return "proposal_json_invalid"
            # Keep these labels fixed and content-free. The client can show
            # which validation rule rejected the reply without exposing model
            # text, source code or provider credentials. Distinguish an error
            # before inference from a malformed model response.
            if "request is missing, invalid or too long" in reason:
                return "proposal_request_invalid"
            if "source context is stale" in reason:
                return "proposal_source_stale"
            if "model attempted to call a tool" in reason:
                return "proposal_tool_call_rejected"
            if "duplicate keys" in reason:
                return "proposal_duplicate_json_keys"
            if "exactly summary and edits" in reason:
                return "proposal_root_schema_rejected"
            if "summary is missing" in reason:
                return "proposal_summary_invalid"
            if "proposal exceeds selected file count" in reason:
                return "proposal_edit_count_invalid"
            if "malformed edit" in reason:
                return "proposal_edit_fields_invalid"
            if "edit path not authorized or repeated" in reason:
                return "proposal_edit_path_invalid"
            if "search anchor" in reason:
                return "proposal_anchor_invalid"
            if "proposed Python code has syntax errors" in reason:
                return "proposal_syntax_invalid"
            if "proposed edit changes nothing" in reason:
                return "proposal_no_changes"
            if "missing, invalid or too long" in reason:
                return "proposal_field_invalid"
            return "proposal_schema_rejected"
        if isinstance(exc, NexusDiffBuilderError):
            return "diff_validation_rejected"
        if isinstance(exc, (ValueError, TypeError)):
            return "local_validation_rejected"
        return "unexpected_internal_error"

    if verified_demo:
        async def authenticate_demo(request: Request) -> None:
            try:
                if not request.client or not ipaddress.ip_address(request.client.host).is_loopback:
                    raise HTTPException(403, "Local connection required")
            except ValueError:
                raise HTTPException(403, "Local connection required") from None
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "Cross-origin access forbidden")
            bearer = request.headers.get("authorization", "")
            supplied = bearer[7:] if bearer.startswith("Bearer ") else ""
            if not supplied or not hmac.compare_digest(supplied, demo_token):
                raise HTTPException(401, "Local demonstration token required")

        async def body_json(request: Request, *, max_bytes: int = 2048) -> dict[str, Any]:
            if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                raise HTTPException(415, "Expected application/json")
            chunks = bytearray()
            async for chunk in request.stream():
                if len(chunks) + len(chunk) > max_bytes:
                    raise HTTPException(413, "Request too large")
                chunks.extend(chunk)
            try:
                value = json.loads(chunks.decode("utf-8"))
            except (ValueError, UnicodeError):
                raise HTTPException(400, "Invalid JSON") from None
            if not isinstance(value, dict):
                raise HTTPException(422, "Expected JSON object")
            return value

        async def demo_status(request: Request) -> Response:
            await authenticate_demo(request)
            return JSONResponse(session.snapshot())

        async def demo_start(request: Request) -> Response:
            await authenticate_demo(request)
            value = await body_json(request)
            if set(value) != {"confirmation"} or value["confirmation"] != "INICIAR DEMOSTRACION SINTETICA":
                raise HTTPException(422, "Explicit fixture confirmation required")
            return JSONResponse(session.start(), status_code=202)

        async def demo_decision(request: Request) -> Response:
            await authenticate_demo(request)
            value = await body_json(request)
            if set(value) != {"request_id", "decision", "confirmation"} or any(type(v) is not str for v in value.values()):
                raise HTTPException(422, "Invalid decision shape")
            if len(value["request_id"]) > 100 or len(value["confirmation"]) > 100:
                raise HTTPException(422, "Invalid decision length")
            return JSONResponse(session.decide(value["request_id"], value["decision"], value["confirmation"]))

        async def inspect_repository(request: Request) -> Response:
            await authenticate_demo(request)
            if workspace is None:
                raise HTTPException(404, "Repository inspection not enabled")
            # No file contents, environment variables or absolute system paths
            # leave this endpoint. The existing WorkspaceManager handles bounded
            # traversal and rejects known sensitive paths and symlinks.
            inventory = workspace.inventory(with_hashes=True)
            return JSONResponse({
                "schema": inventory["schema"],
                "repository": workspace.root.name,
                "mode": "read_only",
                "file_count": inventory["file_count"],
                "truncated": inventory["truncated"],
                "warnings": inventory["warnings"],
                "files": [
                    {"relative_path": f["relative_path"],
                     "size_bytes": f["size_bytes"],
                     "sha256": f["sha256"],
                     "is_text_candidate": f["is_text_candidate"]}
                    for f in inventory["files"]
                ],
            })

        def verified_source(relative: str, expected: str) -> tuple[str, dict[str, Any]]:
            """The same bounded allowlist/hash/secret checks for preview and proposal."""
            listing = workspace.inventory(with_hashes=True)
            candidates = [f for f in listing["files"]
                          if f["relative_path"] == relative and f["is_text_candidate"]
                          and f["sha256"] == expected]
            if len(candidates) != 1:
                raise HTTPException(409, "File is not in the permitted inventory or has changed")
            entry = candidates[0]
            if not 1 <= entry["size_bytes"] <= 10_000:
                raise HTTPException(413, "Preview limited to files of at most 10 KB")
            try:
                from aegisforge.nexus.workspace.manager import NexusWorkspaceError
                content = workspace.read_text(relative, max_chars=10_000)
            except (NexusWorkspaceError, UnicodeError, OSError):
                raise HTTPException(409, "File could not be safely inspected") from None
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected:
                raise HTTPException(409, "File content no longer matches reviewed SHA-256")
            if (re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", content)
                    or re.search(r"(?im)^\s*[A-Z_]*(?:API_KEY|SECRET|PASSWORD|ACCESS_TOKEN)\s*=\s*['\"][^'\"\r\n]{8,}", content)
                    or re.search(r"\b(?:ghp_|github_pat_|AKIA)[A-Za-z0-9_]{12,}", content)):
                raise HTTPException(422, "Potential credentials detected; source preview refused")
            return content, entry

        async def inspect_source(request: Request) -> Response:
            """Explicit, bounded source preview; never runs code or contacts a model."""
            await authenticate_demo(request)
            if workspace is None:
                raise HTTPException(404, "Repository inspection not enabled")
            value = await body_json(request)
            if set(value) != {"relative_path", "expected_sha256", "confirmation"}:
                raise HTTPException(422, "Invalid file inspection request")
            relative = value["relative_path"]
            expected = value["expected_sha256"]
            if (type(relative) is not str or not 1 <= len(relative) <= 260
                    or type(expected) is not str or not re.fullmatch(r"[a-f0-9]{64}", expected)
                    or value["confirmation"] != "LEER ARCHIVO LOCAL AUTORIZADO"):
                raise HTTPException(422, "Explicit file inspection confirmation required")

            content, entry = verified_source(relative, expected)

            analysis: dict[str, Any] = {"language": "python" if relative.lower().endswith(".py") else "text",
                                        "syntax": "not_checked", "imports": [], "functions": [], "classes": []}
            if relative.lower().endswith(".py"):
                try:
                    tree = ast.parse(content, filename=relative)
                except SyntaxError as exc:
                    analysis["syntax"] = "error"
                    analysis["syntax_error"] = {"line": exc.lineno, "message": str(exc.msg)[:160]}
                else:
                    analysis["syntax"] = "ok"
                    for statement in tree.body:
                        if isinstance(statement, ast.Import):
                            analysis["imports"].extend(alias.name for alias in statement.names)
                        elif isinstance(statement, ast.ImportFrom):
                            analysis["imports"].append("." * statement.level + (statement.module or ""))
                        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            analysis["functions"].append(statement.name)
                        elif isinstance(statement, ast.ClassDef):
                            analysis["classes"].append(statement.name)
                    for field in ("imports", "functions", "classes"):
                        analysis[field] = analysis[field][:40]
            return JSONResponse({"mode": "local_read_only", "relative_path": relative,
                                 "sha256": expected, "size_bytes": entry["size_bytes"],
                                 "content": content, "analysis": analysis,
                                 "nebius_calls": 0, "executed": False, "modified": False})

        async def preview_scorecard_proposal(request: Request) -> Response:
            """A reviewed, deterministic fix for one known bug; never writes/runs."""
            nonlocal reviewed_scorecard
            await authenticate_demo(request)
            if workspace is None:
                raise HTTPException(404, "Repository inspection not enabled")
            value = await body_json(request)
            if set(value) != {"relative_path", "expected_sha256", "confirmation"}:
                raise HTTPException(422, "Invalid proposal request")
            relative, expected = value["relative_path"], value["expected_sha256"]
            if (type(relative) is not str or relative != "scripts/build_scorecard.py"
                    or type(expected) is not str or not re.fullmatch(r"[a-f0-9]{64}", expected)
                    or value["confirmation"] != "REVISAR PROPUESTA LOCAL SIN EFECTOS"):
                raise HTTPException(422, "Only the approved local scorecard example is supported")
            content, _entry = verified_source(relative, expected)
            from importlib.util import spec_from_file_location, module_from_spec
            fixture_path = ROOT / "scripts" / "nexus" / "demo_nexus_engineering_host_offline.py"
            spec = spec_from_file_location("colossus_fixture_preview", fixture_path)
            if spec is None or spec.loader is None:
                raise HTTPException(503, "Local repair module unavailable")
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            try:
                candidate_json = module.scorecard_candidate_json(content)
            except (ValueError, SyntaxError):
                raise HTTPException(409, "Reviewed scorecard source is unsupported") from None
            try:
                from aegisforge.nexus.workspace.workspace_policy import NexusWorkspacePolicy, WorkspaceRule
                from aegisforge.nexus.engineering.code_proposer import NexusCodeProposer
                from aegisforge.nexus.engineering.diff_builder import NexusDiffBuilder
                mission_id = "mission.local.scorecard.repair"
                policy = NexusWorkspacePolicy(workspace, rules=[
                    WorkspaceRule(mission_id, relative, frozenset({"read_text"}))
                ])
                proposer = NexusCodeProposer(policy)  # No client: inference is impossible here.
                context = proposer.prepare(mission_id, "Handle null and non-string warnings safely", target_files=(relative,))
                if context.sources[0].source_text_sha256 != expected:
                    raise HTTPException(409, "Source has changed since browser inspection")
                proposal = proposer.review_response(context, candidate_json)
                bundle = NexusDiffBuilder(proposer).build(context, proposal, require_fresh=True)
            except HTTPException:
                raise
            except (ValueError, OSError, RuntimeError):
                # Do not expose source or paths through error diagnostics.
                raise HTTPException(409, "Proposal could not be safely verified") from None
            if len(bundle.files) != 1 or bundle.files[0].relative_path != relative:
                raise HTTPException(409, "Unexpected proposal scope")
            review_id = secrets.token_urlsafe(20)
            reviewed_scorecard = {"review_id": review_id, "base_sha256": expected,
                                  "patchset_sha256": bundle.patchset_sha256,
                                  "issued_at": monotonic()}
            return JSONResponse({"review_id": review_id,"mode": "local_review_only", "source": "deterministic_offline_rule",
                                 "relative_path": relative, "base_sha256": expected,
                                 "proposal_id": proposal.proposal_id,
                                 "patchset_sha256": bundle.patchset_sha256,
                                 "summary": proposal.summary,
                                 "file": bundle.files[0].to_dict(include_diff=True),
                                 "applied": False, "executed": False, "nebius_calls": 0,
                                 "authenticity_verified": False,
                                 "verification_scope": "diff_only_no_tests"})

        def offer_exact_scorecard_candidate(*, relative: str, expected: str,
                                            source: str, goal: str, origin: str,
                                            mission_id: str, proposer: Any,
                                            context: Any, proposal: Any,
                                            bundle: Any) -> dict[str, str] | None:
            """Revalidate the SAME diff for the existing one-file Docker fixture.

            No model call, write or test here. Whole-file replacement is only
            a canonical transport into the existing host for this small file.
            All edits are independently reconstructed and compared to the
            exact model patch SHA before an execution offer can be issued.
            """
            if (relative != "scripts/build_scorecard.py" or len(bundle.files) != 1
                    or bundle.files[0].relative_path != relative or not proposal.edits
                    or len(source) > 2500):
                return None
            from aegisforge.nexus.engineering.diff_builder import NexusDiffBuilder
            builder = NexusDiffBuilder(proposer)
            candidate = builder.render_reviewed_text(bundle, context, proposal, relative,
                                                     require_fresh=True)
            if len(candidate) > 5000 or source == candidate:
                return None
            serialized = json.dumps({
                "summary": proposal.summary,
                "edits": [{"path": relative, "search": source, "replace": candidate,
                           "rationale": proposal.edits[0].rationale}],
            }, ensure_ascii=False)
            replay = proposer.review_response(context, serialized)
            replay_bundle = builder.build(context, replay, require_fresh=True)
            if replay_bundle.patchset_sha256 != bundle.patchset_sha256:
                return None
            if hashlib.sha256(source.encode("utf-8")).hexdigest() != expected:
                return None
            return {"review_id": secrets.token_urlsafe(20),
                    "base_sha256": expected, "patchset_sha256": bundle.patchset_sha256,
                    "candidate_json": serialized, "mission_id": mission_id,
                    "task": goal, "origin": origin, "issued_at": monotonic()}

        def offer_operator_regression(*, relative: str, expected: str,
                                      source: str, candidate: str,
                                      regression_source: str) -> dict[str, str]:
            """Bind an explicit operator-reviewed pytest regression to one diff.

            All bytes are held in this process and never sent to Nebius.
            Verification proves only the supplied test, not every behavior.
            """
            if (type(regression_source) is not str or not 20 <= len(regression_source) <= 6000
                    or "\x00" in regression_source):
                raise ValueError("Operator regression must contain 20-6000 characters")
            tests = ast.parse(regression_source, filename="test_colossus_review.py")
            if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and node.name.startswith("test_") for node in tests.body):
                raise ValueError("Regression requires a pytest test_ function")
            from aegisforge.nexus.workspace.workspace_policy import NexusWorkspacePolicy, WorkspaceRule
            from aegisforge.nexus.engineering.code_proposer import NexusCodeProposer, CodeProposalLimits
            from aegisforge.nexus.engineering.diff_builder import NexusDiffBuilder
            mission_id = "mission.local.generic.review"
            policy = NexusWorkspacePolicy(workspace, rules=[
                WorkspaceRule(mission_id, relative, frozenset({"read_text"}))])
            proposer = NexusCodeProposer(policy, allow_text_targets=True, limits=CodeProposalLimits(
                max_target_files=1, max_source_chars=10000, max_context_chars=12000,
                max_search_chars=10000, max_replace_chars=12000,
                max_reply_chars=30000, max_diff_chars=16000))
            goal = "Run operator-reviewed regression on a disposable source snapshot"
            context = proposer.prepare(mission_id, goal, target_files=(relative,))
            if context.sources[0].source_text_sha256 != expected:
                raise ValueError("Source changed during review")
            serialized = json.dumps({
                "summary": "Operator-imported diff and independently supplied regression",
                "edits": [{"path": relative, "search": source, "replace": candidate,
                           "rationale": "Review actual supplied patch with an explicit Docker regression"}],
            }, ensure_ascii=False)
            proposal = proposer.review_response(context, serialized)
            bundle = NexusDiffBuilder(proposer).build(context, proposal, require_fresh=True)
            restored = NexusDiffBuilder(proposer).render_reviewed_text(
                bundle, context, proposal, relative, require_fresh=True)
            if restored != candidate:
                raise ValueError("Proposed text no longer matches imported diff")
            return {"review_id": secrets.token_urlsafe(20),
                    "base_sha256": expected, "patchset_sha256": bundle.patchset_sha256,
                    "candidate_json": serialized, "mission_id": mission_id,
                    "task": goal, "relative_path": relative,
                    "regression_source": regression_source,
                    "regression_sha256": hashlib.sha256(regression_source.encode("utf-8")).hexdigest(),
                    "origin": "user_imported_unverified", "issued_at": monotonic()}

        async def check_nebius_model_catalog(request: Request) -> Response:
            """One explicit GET /models per server session; no source or inference."""
            nonlocal catalog_check_started, catalog_check_result
            await authenticate_demo(request)
            if not nebius_proposals or workspace is None:
                raise HTTPException(404, "Nebius proposals are disabled")
            with catalog_lock:
                if catalog_check_result is not None:
                    return JSONResponse(dict(catalog_check_result))
                if catalog_check_started:
                    raise HTTPException(409, "A model catalog check is already in progress")
                catalog_check_started = True

            def _catalog_get() -> dict[str, Any]:
                from aegisforge.nexus.nebius_client import NebiusClient
                client = nebius_client_factory() if nebius_client_factory is not None else NebiusClient(timeout_seconds=15)
                try:
                    model_id = getattr(client, "model", None)
                    if not isinstance(model_id, str) or not model_id:
                        raise ValueError("Missing model identifier")
                    ids = client.list_model_ids()  # GET /models only; no prompts or source code
                    return {"status": "ok", "model": model_id,
                            "model_available": model_id in ids,
                            "inference_calls": 0, "source_sent": False,
                            "usage_or_billing_status": "not_checked"}
                finally:
                    close = getattr(client, "close", None)
                    if callable(close):
                        close()
            try:
                answer = await run_in_threadpool(_catalog_get)
            except Exception as exc:
                answer = {"status": "failed", "error_code": safe_nebius_failure(exc),
                          "inference_calls": 0, "source_sent": False,
                          "usage_or_billing_status": "not_checked"}
            with catalog_lock:
                catalog_check_result = dict(answer)
            return JSONResponse(answer)

        def failed_revision_record() -> dict[str, Any] | None:
            """Only the last failed, in-process reviewed source execution is eligible."""
            snapshot = session.snapshot()
            result = snapshot.get("result")
            if (snapshot.get("status") != "completed" or not isinstance(result, dict)
                    or result.get("source_snapshot") is not True
                    or result.get("synthetic") is not False
                    or type(result.get("target_path")) is not str
                    or result.get("baseline") != "sandbox_failed"
                    or result.get("candidate") != "sandbox_failed"
                    or result.get("verified_repair") is not False
                    or result.get("original_untouched") is not True
                    or result.get("verification_scope") != "insufficient_before_after_evidence"
                    or not re.fullmatch(r"[0-9a-f]{64}", result.get("base_sha256") or "")
                    or not re.fullmatch(r"[0-9a-f]{64}", result.get("patchset_sha256") or "")):
                return None
            return {"run_id": snapshot["run_id"],
                    "target_path": result["target_path"],
                    "base_sha256": result["base_sha256"],
                    "patchset_sha256": result["patchset_sha256"],
                    "baseline": result["baseline"], "candidate": result["candidate"]}

        def revision_feedback() -> str:
            # This is a bounded *hypothesis* prompted by status, not a traceback.
            record = failed_revision_record()
            if record is not None and record["target_path"] != "scripts/build_scorecard.py":
                return ("Verificacion previa en Docker: la regresion revisada no paso "
                        "con el candidato. Examina el archivo y la prueba pertinentes. "
                        "Conserva los casos limite; no afirmes reparacion sin otra prueba.")
            return ("Verificacion previa en Docker: baseline=sandbox_failed; "
                    "candidate=sandbox_failed; verified_repair=false. "
                    "La prueba abarca warnings=[null, 'fallback used', 7], null, 42, objeto y texto. "
                    "Revisa len(None), contenedores no lista y elementos no texto; "
                    "conserva warning_count y fallback. No afirmes reparacion sin otra prueba.")

        async def get_revision_feedback(request: Request) -> Response:
            """Display non-sensitive feedback, with NO inference or side effects."""
            await authenticate_demo(request)
            if workspace is None:
                raise HTTPException(404, "Repository inspection not enabled")
            record = failed_revision_record()
            if record is None:
                raise HTTPException(409, "No matching failed Docker candidate for a revision")
            with nebius_lock:
                available = nebius_proposals and not nebius_revision_consumed
            return JSONResponse({**record, "feedback": revision_feedback(),
                                 "mode": "review_feedback_only", "nebius_calls": 0,
                                 "can_request_revision_model": available,
                                 "source_sent": False,
                                 "authenticity_verified": False})

        async def propose_with_nebius(request: Request) -> Response:
            """A consented bounded first inference, or one reviewed revision inference.

            Never executes, writes or creates a Docker review offer. Source code
            is transmitted to the configured Nebius endpoint only after explicit
            UI consent and only when the operator enabled this server feature.
            """
            nonlocal nebius_consumed, nebius_revision_consumed, reviewed_scorecard
            await authenticate_demo(request)
            if not nebius_proposals or workspace is None:
                raise HTTPException(404, "Nebius code proposals are disabled")
            data = await body_json(request)
            revision_mode = "revision_run_id" in data
            expected_keys = {"relative_path", "expected_sha256", "goal", "confirmation"}
            if set(data) != (expected_keys | {"revision_run_id"} if revision_mode else expected_keys):
                raise HTTPException(422, "Invalid model proposal request")
            relative, expected, goal = (data[k] for k in ("relative_path", "expected_sha256", "goal"))
            if (type(relative) is not str or type(expected) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", expected)
                    or type(goal) is not str or not 10 <= len(goal.strip()) <= 600
                    or type(data["confirmation"]) is not str
                    or data["confirmation"] != "AUTORIZAR UNA CONSULTA NEBIUS Y ENVIO DEL CODIGO"):
                raise HTTPException(422, "Separate explicit source-sharing and billing consent required")
            if Path(relative).suffix.lower() not in {".py", ".pyi", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}:
                raise HTTPException(422, "Selected file is not supported for model proposals")
            content, entry = verified_source(relative, expected)
            if len(content) > 8000:
                raise HTTPException(413, "Model sharing limited to 8,000 source characters")
            with nebius_lock:
                if revision_mode:
                    record = failed_revision_record()
                    if (record is None or type(data["revision_run_id"]) is not str
                            or not hmac.compare_digest(relative, record["target_path"])
                            or not hmac.compare_digest(data["revision_run_id"], record["run_id"])
                            or not hmac.compare_digest(expected, record["base_sha256"])):
                        raise HTTPException(409, "Revision not bound to a failed Docker candidate")
                    if nebius_revision_consumed:
                        raise HTTPException(409, "Revision attempt already consumed; no automatic retry")
                    nebius_revision_consumed = True  # consumed BEFORE contacting provider
                else:
                    if nebius_consumed:
                        raise HTTPException(409, "First Nebius attempt consumed; use supervised revision after a failed Docker run")
                    nebius_consumed = True
            # The new, explicitly authorized attempt supersedes any local offer.
            reviewed_scorecard = None
            prompt_goal = (goal + "\n\n" + revision_feedback()) if revision_mode else goal

            def _single_model_proposal() -> dict[str, Any]:
                from aegisforge.nexus.workspace.workspace_policy import NexusWorkspacePolicy, WorkspaceRule
                from aegisforge.nexus.engineering.code_proposer import (NexusCodeProposer, CodeProposalLimits)
                from aegisforge.nexus.engineering.diff_builder import NexusDiffBuilder
                from aegisforge.nexus.nebius_client import NebiusClient
                mission_id = "mission.local.nebius.review"
                policy = NexusWorkspacePolicy(workspace, rules=[
                    WorkspaceRule(mission_id, relative, frozenset({"read_text"}))
                ])
                client = nebius_client_factory() if nebius_client_factory is not None else NebiusClient(timeout_seconds=60)
                try:
                    if nebius_client_factory is None and (
                        not getattr(client, "model", "").casefold().startswith("nvidia/")
                        or "nemotron" not in getattr(client, "model", "").casefold()
                    ):
                        raise ValueError("A NVIDIA Nemotron model must be selected")
                    proposer = NexusCodeProposer(policy, client=client, limits=CodeProposalLimits(
                        max_target_files=1, max_source_chars=10000, max_context_chars=12000,
                        max_request_chars=1000, max_reply_chars=16000,
                        max_search_chars=2500, max_replace_chars=5000,
                        max_diff_chars=16000, max_tokens=2048,
                    ))
                    context = proposer.prepare(mission_id, prompt_goal, target_files=(relative,))
                    if len(context.sources) != 1 or context.sources[0].source_text_sha256 != expected:
                        raise ValueError("Source changed before inference")
                    proposal = proposer.propose(context)  # EXACTLY one charged call, no tools
                    if (not isinstance(proposal.model, str)
                            or not proposal.model.casefold().startswith("nvidia/")
                            or "nemotron" not in proposal.model.casefold()):
                        raise ValueError("Model identity is not a NVIDIA Nemotron model")
                    bundle = NexusDiffBuilder(proposer).build(context, proposal, require_fresh=True)
                    if any(row.relative_path != relative for row in bundle.files):
                        raise ValueError("Unexpected model edit scope")
                    offer = offer_exact_scorecard_candidate(
                        relative=relative, expected=expected, source=content, goal=prompt_goal,
                        origin="nvidia_via_nebius", mission_id=mission_id,
                        proposer=proposer, context=context, proposal=proposal, bundle=bundle,
                    ) if bundle.files else None
                    return {
                        "mode": "nebius_review_only", "source": "nvidia_via_nebius",
                        "revision_round": 2 if revision_mode else 1,
                        "relative_path": relative, "base_sha256": expected,
                        "summary": proposal.summary, "model": proposal.model,
                        "request_id": proposal.request_id, "usage": dict(proposal.usage),
                        "proposal_id": proposal.proposal_id,
                        "patchset_sha256": bundle.patchset_sha256 if bundle.files else None,
                        "files": [f.to_dict(include_diff=True) for f in bundle.files],
                        "applied": False, "executed": False, "nebius_calls": 1,
                        "authenticity_verified": False, "verification_scope": "diff_only_no_tests",
                        "docker_execution_enabled": offer is not None,
                        "review_id": offer["review_id"] if offer is not None else None,
                        "_execution_offer": offer,
                    }
                finally:
                    close = getattr(client, "close", None)
                    if callable(close):
                        close()

            try:
                result = await run_in_threadpool(_single_model_proposal)
                reviewed_scorecard = result.pop("_execution_offer", None)
            except Exception as exc:
                # Never echo provider response bodies, source, secrets or stack traces.
                # Keep the attempt consumed: the provider may have billed it.
                return JSONResponse({
                    "error_code": safe_nebius_failure(exc),
                    "retry_blocked": True,
                    "billing_status": "unknown",
                }, status_code=502)
            return JSONResponse(result)

        async def import_existing_reviewed_diff(request: Request) -> Response:
            """Review one exact, canonical diff for an inventoried text file.

            This is non-executing for all files. Only the existing scorecard
            regression fixture can additionally offer an isolated Docker run.
            Never infer a model-provider origin from an imported patch.
            """
            nonlocal reviewed_scorecard
            await authenticate_demo(request)
            if workspace is None:
                raise HTTPException(404, "Repository inspection not enabled")
            data = await body_json(request, max_bytes=28000)
            mandatory = {"relative_path", "expected_sha256", "unified_diff", "confirmation"}
            if not mandatory.issubset(data) or set(data) - mandatory - {"regression_source"}:
                raise HTTPException(422, "Invalid reviewed diff import")
            operator_regression = data.get("regression_source")
            if operator_regression is not None and (type(operator_regression) is not str
                    or not 20 <= len(operator_regression) <= 6000):
                raise HTTPException(422, "Invalid operator regression")
            relative, expected, diff = (data[k] for k in ("relative_path", "expected_sha256", "unified_diff"))
            if (type(relative) is not str or not 1 <= len(relative) <= 260
                    or type(expected) is not str or not re.fullmatch(r"[a-f0-9]{64}", expected)
                    or type(diff) is not str or not 1 <= len(diff) <= 12000
                    or data["confirmation"] != "IMPORTAR DIFF PARA REVISION SIN NEBIUS"):
                raise HTTPException(422, "Invalid repository diff review request")
            content, _ = verified_source(relative, expected)
            # A browser textarea normalizes CRLF to LF. Restore the source's
            # line terminators for diff body lines only; unified-diff headers
            # and hunk headers are always LF in difflib's canonical output.
            # The strict reconstruction and canonical equality checks below
            # still reject stale, edited or noncanonical patches.
            if "\r\n" in content:
                if "\r" in diff or "\n" in content.replace("\r\n", ""):
                    raise HTTPException(409, "Unsupported mixed line endings")
                diff = "".join(
                    row[:-1] + "\r\n"
                    if row.endswith("\n") and row.startswith((" ", "+", "-"))
                    and index >= 2 and not row.startswith("@@ ")
                    else row
                    for index, row in enumerate(diff.splitlines(keepends=True))
                )
            try:
                from difflib import unified_diff
                from aegisforge.nexus.engineering.code_proposer import ProposedEdit
                from aegisforge.nexus.engineering.diff_builder import (
                    _reconstruct, DiffBuilderLimits,
                )
                # Strict, single-file, exact-source patch reconstruction. No
                # new files, deletions, renames, path traversal or commands.
                updated, hunks = _reconstruct(content, ProposedEdit(
                    relative, expected, "0" * 64, "Operator-reviewed imported diff", diff,
                ), DiffBuilderLimits(max_diff_chars_per_file=12000))
                canonical = "".join(unified_diff(
                    content.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile="a/" + relative, tofile="b/" + relative, n=3,
                ))
                if canonical != diff or content == updated:
                    raise ValueError("Noncanonical or empty diff")
                if relative.lower().endswith((".py", ".pyi")):
                    ast.parse(updated, filename=relative)
                # A stable, bounded preview identity, not a certified origin.
                patchset = hashlib.sha256(json.dumps({
                    "path": relative, "base": expected,
                    "diff": canonical,
                }, sort_keys=True).encode("utf-8")).hexdigest()
                offer = None
                if operator_regression is not None:
                    offer = offer_operator_regression(
                        relative=relative, expected=expected, source=content,
                        candidate=updated, regression_source=operator_regression,
                    )
                    patchset = offer["patchset_sha256"]
                elif relative == "scripts/build_scorecard.py" and len(content) <= 2500 and len(updated) <= 5000:
                    from aegisforge.nexus.workspace.workspace_policy import NexusWorkspacePolicy, WorkspaceRule
                    from aegisforge.nexus.engineering.code_proposer import NexusCodeProposer
                    from aegisforge.nexus.engineering.diff_builder import NexusDiffBuilder
                    mission_id = "mission.local.imported.review"
                    goal = "Review imported scorecard warnings fix with isolated regression"
                    policy = NexusWorkspacePolicy(workspace, rules=[
                        WorkspaceRule(mission_id, relative, frozenset({"read_text"}))])
                    proposer = NexusCodeProposer(policy)
                    context = proposer.prepare(mission_id, goal, target_files=(relative,))
                    proposal = proposer.review_response(context, json.dumps({
                        "summary": "Operator-imported diff; provider origin not independently verified",
                        "edits": [{"path": relative, "search": content, "replace": updated,
                                   "rationale": "Review previously shown proposal with Docker"}],
                    }))
                    bundle = NexusDiffBuilder(proposer).build(context, proposal, require_fresh=True)
                    offer = offer_exact_scorecard_candidate(
                        relative=relative, expected=expected, source=content, goal=goal,
                        origin="user_imported_unverified", mission_id=mission_id,
                        proposer=proposer, context=context, proposal=proposal, bundle=bundle,
                    )
                    if offer is None:
                        raise ValueError("Scorecard review could not be verified")
                    patchset = bundle.patchset_sha256
            except (ValueError, SyntaxError, RuntimeError, OSError) as exc:
                raise HTTPException(409, "Imported diff is not valid for the current source") from None
            except Exception as exc:
                from aegisforge.nexus.engineering.code_proposer import NexusCodeProposalError
                from aegisforge.nexus.engineering.diff_builder import NexusDiffBuilderError
                from aegisforge.nexus.workspace.workspace_policy import NexusWorkspacePolicyError
                if isinstance(exc, (NexusCodeProposalError, NexusDiffBuilderError,
                                    NexusWorkspacePolicyError)):
                    raise HTTPException(409, "Reviewed patch or test exceeds validation limits") from None
                raise
            reviewed_scorecard = offer
            return JSONResponse({
                "mode": "imported_review_only", "source": "user_imported_unverified",
                "review_id": offer["review_id"] if offer is not None else None,
                "relative_path": relative, "base_sha256": expected,
                "patchset_sha256": patchset,
                "file": {"relative_path": relative, "base_text_sha256": expected,
                         "proposed_text_sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
                         "additions": sum(h.additions for h in hunks),
                         "deletions": sum(h.deletions for h in hunks),
                         "unified_diff": canonical},
                "applied": False, "executed": False, "nebius_calls": 0,
                "authenticity_verified": False,
                "docker_execution_enabled": offer is not None,
                "regression_sha256": offer.get("regression_sha256") if offer else None,
                "verification_scope": "pending_operator_regression" if operator_regression else "diff_only_no_tests",
            })

        async def start_reviewed_scorecard(request: Request) -> Response:
            """One-use and explicit: only the last previewed diff, exact live source."""
            nonlocal reviewed_scorecard
            await authenticate_demo(request)
            if workspace is None:
                raise HTTPException(404, "Repository inspection not enabled")
            data = await body_json(request)
            required = {"review_id", "expected_sha256", "patchset_sha256", "confirmation"}
            if (not required.issubset(data) or set(data) - required - {"regression_sha256"}
                    or any(type(v) is not str for v in data.values())
                    or data["confirmation"] != "EJECUTAR COPIA REVISADA EN DOCKER"):
                raise HTTPException(422, "Exact reviewed execution confirmation required")
            offer = reviewed_scorecard
            if (offer is None or monotonic() - offer["issued_at"] > 480
                    or any(not hmac.compare_digest(data[k], offer[k])
                           for k in ("review_id", "expected_sha256", "patchset_sha256")
                           if k in offer)
                    or not hmac.compare_digest(data["expected_sha256"], offer["base_sha256"])):
                raise HTTPException(409, "Review expired or does not match the last diff")
            target = offer.get("relative_path", "scripts/build_scorecard.py")
            if "regression_source" in offer and not hmac.compare_digest(
                data.get("regression_sha256", ""), offer["regression_sha256"]
            ):
                raise HTTPException(409, "Regression changed since review")
            source, _ = verified_source(target, offer["base_sha256"])
            # Consume the offer; no automatic replay after an uncertain effect.
            reviewed_scorecard = None
            job = {"source": source, "base_sha256": offer["base_sha256"],
                   "patchset_sha256": offer["patchset_sha256"]}
            if "regression_source" in offer:
                job.update({k: offer[k] for k in ("relative_path", "regression_source")})
            if "candidate_json" in offer:
                job.update({k: offer[k] for k in ("candidate_json", "task", "mission_id", "origin")})
            return JSONResponse(session.start(scorecard_job=job), status_code=202)

        app.router.routes.extend([
            Route("/demo/repo/model-status", check_nebius_model_catalog, methods=["GET"]),
            Route("/demo/repo/model-proposal", propose_with_nebius, methods=["POST"]),
            Route("/demo/repo/revision-feedback", get_revision_feedback, methods=["GET"]),
            Route("/demo/repo/import-diff", import_existing_reviewed_diff, methods=["POST"]),
            Route("/demo/repo/execute", start_reviewed_scorecard, methods=["POST"]),
            Route("/demo/repo/source", inspect_source, methods=["POST"]),
            Route("/demo/repo/proposal", preview_scorecard_proposal, methods=["POST"]),
            Route("/demo/repo/inspect", inspect_repository, methods=["GET"]),
            Route("/demo/repair/status", demo_status, methods=["GET"]),
            Route("/demo/repair/start", demo_start, methods=["POST"]),
            Route("/demo/repair/decision", demo_decision, methods=["POST"]),
        ])

    async def simulate_aegisgrid(request: Request) -> Response:
        """Run a bounded scenario and record it in existing Colossus contracts."""
        origin = request.headers.get("origin")
        if origin is not None and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "origin_denied"}, status_code=403)
        if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return JSONResponse({"error": "origin_denied"}, status_code=403)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse({"error": "json_required"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 4096:
                return JSONResponse({"error": "request_too_large"}, status_code=413)
            body.extend(chunk)
        try:
            from aegisforge.products.aegisgrid.simulator import simulate
            scenario = json.loads(body.decode("utf-8"))
            result = simulate(scenario)
        except (ValueError, UnicodeError, OverflowError):
            return JSONResponse({"error": "invalid_scenario"}, status_code=422)
        try:
            result["colossus"] = await run_in_threadpool(grid_integration.record, result)
        except Exception:
            # Do not expose internal evidence, filesystem details or secret state.
            return JSONResponse({"error": "colossus_integration_unavailable"}, status_code=503)
        return JSONResponse(result)

    async def simulate_aegisgrid_multiday(request: Request) -> Response:
        """Compare bounded synthetic strategies; archive via existing mission contracts."""
        origin = request.headers.get("origin")
        if origin is not None and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "origin_denied"}, status_code=403)
        if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return JSONResponse({"error": "origin_denied"}, status_code=403)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse({"error": "json_required"}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 4096:
                return JSONResponse({"error": "request_too_large"}, status_code=413)
            body.extend(chunk)
        try:
            from aegisforge.products.aegisgrid.multiday import simulate_multiday
            from aegisforge.products.aegisgrid.multiday_validation import verify_multiday
            config = json.loads(body.decode("utf-8"))
            result = await run_in_threadpool(simulate_multiday, config)
            if not result["verification"]["passed"] or not verify_multiday(result)["passed"]:
                return JSONResponse({"error": "energy_balance_failed"}, status_code=422)
        except (ValueError, TypeError, UnicodeError, OverflowError):
            return JSONResponse({"error": "invalid_scenario"}, status_code=422)
        try:
            result["colossus"] = await run_in_threadpool(grid_integration.record, result)
        except Exception:
            return JSONResponse({"error": "colossus_integration_unavailable"}, status_code=503)
        return JSONResponse(result)

    app.router.routes.extend([
        Route("/aegisgrid/simulate", simulate_aegisgrid, methods=["POST"]),
        Route("/aegisgrid/multiday", simulate_aegisgrid_multiday, methods=["POST"]),
        Route("/web-status", web_status, methods=["GET"]),
        Route("/", asset, methods=["GET"]),
        Route("/styles.css", asset, methods=["GET"]),
        Route("/app.js", asset, methods=["GET"]),
    ])

    async def security_headers(_request: Any, call_next: Any) -> Response:
        response = await call_next(_request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; "
            "font-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=security_headers)
    return app


def main() -> None:
    p = argparse.ArgumentParser(description="AegisForge Nexus web UI — local only")
    p.add_argument("--live", action="store_true", help="Enable authenticated, NO-TOOL Nexus planning API")
    p.add_argument("--verified-demo", action="store_true", help="Enable fixed, human-reviewed Docker fixture from UI")
    p.add_argument("--demo-image", help="Existing digest-pinned colossus-pytest Docker image")
    p.add_argument("--inspect-repo", help="Absolute operator-authorized repository directory (read-only)")
    p.add_argument("--allow-nebius-proposals", action="store_true",
                   help="Permit one opt-in proposal and one independently approved post-failure revision (may incur costs)")
    p.add_argument("--aegisgrid-checkpoint-dir", default=None,
                   help="Existing operator-owned directory for optional Mission Control registry checkpoints")
    p.add_argument("--port", type=int, default=8012)
    args = p.parse_args()
    if not 1024 <= args.port <= 65535:
        p.error("Use an unprivileged port from 1024 to 65535")
    if args.verified_demo and args.live:
        p.error("--verified-demo and --live must be started separately")
    if args.verified_demo and not args.demo_image:
        p.error("--demo-image required with --verified-demo")
    if args.demo_image and not args.verified_demo:
        p.error("--demo-image requires --verified-demo")
    if args.allow_nebius_proposals and (not args.verified_demo or not args.inspect_repo):
        p.error("--allow-nebius-proposals requires --verified-demo and --inspect-repo")
    if args.inspect_repo and not args.verified_demo:
        p.error("--inspect-repo requires --verified-demo for local authentication")
    demo_token = (os.getenv("COLOSSUS_DEMO_TOKEN") or secrets.token_urlsafe(36)) if args.verified_demo else None
    try:
        app = build_app(live=args.live, verified_demo=args.verified_demo,
                        demo_image=args.demo_image, demo_token=demo_token,
                        inspect_repo=args.inspect_repo,
                        aegisgrid_checkpoint_dir=args.aegisgrid_checkpoint_dir,
                        nebius_proposals=args.allow_nebius_proposals)
    except (ValueError, ImportError) as exc:
        p.exit(status=2, message=f"Configuration error: {exc}\n")
    import uvicorn
    print(f"Nexus dashboard: http://127.0.0.1:{args.port}/", flush=True)
    if args.verified_demo:
        print("Mode: supervised Docker; Nebius code proposals " +
              ("OPT-IN, one first call plus one post-failure revision" if args.allow_nebius_proposals else "DISABLED"), flush=True)
        if args.inspect_repo:
            print("Repository inspection: explicitly authorized, read-only; no code execution", flush=True)
        if not os.getenv("COLOSSUS_DEMO_TOKEN"):
            print("One-session local demo token (copy into the console): " + demo_token, flush=True)
    else:
        print("Mode: LIVE planning only; tools disabled over HTTP" if args.live
              else "Mode: OFFLINE; import evidence files manually; no Nebius calls", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
