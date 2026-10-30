"""Loopback-only, bearer-authenticated, read-only Mission Control HTTP API.

Install: src/aegisforge/nexus/mission_control/mission_http_api.py

Provides an ASGI application for local operators; no network listener is
started on import. The application checks the connecting peer's IP as well as
Host. It NEVER routes writes, consumes approvals, calls models, or executes
external tools. `inspect` is a *read-only* MissionRuntime operation, not a
request to execute a mission. Service authorization is checked independently.

Requires Starlette (already used by the original Nexus HTTP API).
Use the included server entrypoint to bind 127.0.0.1 ONLY. Loopback HTTP
is not encrypted; do not expose through a public reverse proxy, port forward,
shared-host environment or untrusted local users. Treat bearer tokens as
credentials. This version has no login sessions, cookies, CORS or public API.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import ipaddress
import json
from pathlib import Path
import re
from typing import Any, Mapping

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .mission_registry import NexusMissionRegistryError

from .mission_service import NexusMissionService, NexusMissionServiceError
from .mission_monitor_views import NexusMissionMonitorViews, NexusMissionMonitorError
from .mission_checkpoint_monitor import NexusMissionCheckpointMonitor, NexusMissionCheckpointMonitorError
from .mission_source_archive import NexusMissionSourceArchive, NexusMissionSourceArchiveError
from .mission_history_view import NexusMissionHistoryView, NexusMissionHistoryViewError

MISSION_HTTP_API_SCHEMA = "aegisforge.nexus.mission_control.http_api.v1"
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_QUERY_BYTES = 1024
_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_ASSETS = {
    "/": ("mission_dashboard.html", "text/html; charset=utf-8"),
    "/dashboard": ("mission_dashboard.html", "text/html; charset=utf-8"),
    "/assets/mission_dashboard.js": ("mission_dashboard.js", "text/javascript; charset=utf-8"),
    "/assets/mission_dashboard.css": ("mission_dashboard.css", "text/css; charset=utf-8"),
}


class NexusMissionHTTPAPIError(ValueError):
    """Invalid server initialization, credential or configuration."""


class MissionAPIAuthenticator:
    """Maps bearer tokens to fixed actor IDs; stores only their SHA-256 digests.

    Tokens must be independent, random secrets of at least 32 ASCII characters.
    High entropy is an external provisioning responsibility. Fingerprints are
    not intended for storage in external logs or checkpoint snapshots.
    """

    def __init__(self, token_by_actor: Mapping[str, str]) -> None:
        if not isinstance(token_by_actor, Mapping) or not 1 <= len(token_by_actor) <= 16:
            raise NexusMissionHTTPAPIError("1-16 authenticated principals are required")
        entries: list[tuple[str, bytes]] = []
        seen: set[bytes] = set()
        for actor, token in token_by_actor.items():
            if type(actor) is not str or _ID.fullmatch(actor) is None:
                raise NexusMissionHTTPAPIError("invalid actor identifier")
            if (type(token) is not str or not 32 <= len(token) <= 256
                    or not token.isascii() or len(set(token)) < 12
                    or any(ord(c) <= 32 or ord(c) >= 127 for c in token)):
                raise NexusMissionHTTPAPIError("strong, nonwhitespace ASCII bearer token required")
            digest = sha256(token.encode("ascii")).digest()
            if digest in seen:
                raise NexusMissionHTTPAPIError("bearer tokens must be unique")
            seen.add(digest)
            entries.append((actor, digest))
        self._entries = tuple(entries)

    def actor_for(self, header: str | None) -> str | None:
        if type(header) is not str or len(header) > 280:
            return None
        parts = header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        token = parts[1]
        if not 32 <= len(token) <= 256 or not token.isascii():
            return None
        value = sha256(token.encode("ascii")).digest()
        match: str | None = None
        # Constant-time compare each configured fingerprint, even after match.
        for actor, digest in self._entries:
            if hmac.compare_digest(value, digest):
                match = actor
        return match


def _json(data: Any, *, status: int = 200, max_bytes: int = _MAX_RESPONSE_BYTES) -> Response:
    try:
        raw = json.dumps(data, ensure_ascii=True, separators=(",", ":"),
                         sort_keys=True, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise HTTPException(500, "Invalid internal response") from exc
    if len(raw) > max_bytes:
        raise HTTPException(413, "Response exceeds safety budget")
    return Response(raw, status_code=status, media_type="application/json")


def _ok(data: Any, *, max_bytes: int) -> Response:
    return _json({"schema": MISSION_HTTP_API_SCHEMA, "data": data}, max_bytes=max_bytes)


def _query(request: Request, allowed: set[str]) -> dict[str, str]:
    if len(request.scope.get("query_string", b"")) > _MAX_QUERY_BYTES:
        raise HTTPException(400, "Invalid query")
    out: dict[str, str] = {}
    for k, v in request.query_params.multi_items():
        if k not in allowed or k in out:
            raise HTTPException(400, "Invalid query")
        out[k] = v
    return out


def _peer_is_loopback(request: Request) -> bool:
    try:
        return bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except (ValueError, TypeError):
        return False


def _is_safe_mission(value: str) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def create_mission_http_app(
    service: NexusMissionService,
    *,
    token_by_actor: Mapping[str, str],
    max_response_bytes: int = _MAX_RESPONSE_BYTES,
    monitor_views: NexusMissionMonitorViews | None = None,
    checkpoint_monitor: NexusMissionCheckpointMonitor | None = None,
    source_archive: NexusMissionSourceArchive | None = None,
    history_view: NexusMissionHistoryView | None = None,
) -> Starlette:
    """Create a strictly read-only ASGI app (no listener, persistence or tasks).

    The *separate* host authorizer already bound to MissionService remains
    mandatory and decides which missions each token-bound actor may inspect.
    """
    if type(service) is not NexusMissionService:
        raise NexusMissionHTTPAPIError("trusted MissionService instance required")
    if type(max_response_bytes) is not int or not 16_384 <= max_response_bytes <= 3_000_000:
        raise NexusMissionHTTPAPIError("invalid response byte budget")
    if monitor_views is not None and type(monitor_views) is not NexusMissionMonitorViews:
        raise NexusMissionHTTPAPIError("invalid monitor view source")
    if checkpoint_monitor is not None and type(checkpoint_monitor) is not NexusMissionCheckpointMonitor:
        raise NexusMissionHTTPAPIError("invalid checkpoint monitor")
    if checkpoint_monitor is not None and not checkpoint_monitor.matches_registry(service._registry):
        raise NexusMissionHTTPAPIError("checkpoint registry does not match MissionService")
    if source_archive is not None:
        if type(source_archive) is not NexusMissionSourceArchive or checkpoint_monitor is None or source_archive._monitor is not checkpoint_monitor:
            raise NexusMissionHTTPAPIError("source archive requires matching checkpoint monitor")
        source_archive.assert_current()
    if history_view is not None:
        if (type(history_view) is not NexusMissionHistoryView or
                checkpoint_monitor is None or history_view._monitor is not checkpoint_monitor):
            raise NexusMissionHTTPAPIError("history requires matching checkpoint monitor")
        history_view.assert_current()
    views = monitor_views or NexusMissionMonitorViews()
    auth = MissionAPIAuthenticator(token_by_actor)
    assets_root = Path(__file__).resolve().parents[2] / "web" / "nexus"

    def actor(request: Request) -> str:
        # Global middleware has authenticated this request already.
        principal = getattr(request.state, "actor_id", None)
        if type(principal) is not str:
            raise HTTPException(401, "Authentication required")
        return principal

    async def capabilities(request: Request) -> Response:
        a = actor(request)
        # Both the bearer credential and the service host policy must approve.
        service.registry_version(actor_id=a)
        return _ok({"read_only": True, "effects_enabled": False,
                    "authentication": "bearer", "binding": "loopback_only",
                    "routes": ["/api/v1/status", "/api/v1/version", "/api/v1/missions",
                               "/api/v1/missions/{mission_id}",
                               "/api/v1/missions/{mission_id}/history",
                               "/api/v1/missions/{mission_id}/preview",
                               "/api/v1/missions/{mission_id}/inspect",
                               "/api/v1/missions/{mission_id}/timeline",
                               "/api/v1/missions/{mission_id}/graph",
                               "/api/v1/missions/{mission_id}/laboratory",
                               "/api/v1/missions/{mission_id}/trust",
                               "/api/v1/checkpoint",
                               "/api/v1/missions/{mission_id}/archives",
                               "/api/v1/missions/{mission_id}/archives/{generation}/{kind}"]},
                   max_bytes=max_response_bytes)

    async def status(request: Request) -> Response:
        v = service.status(actor_id=actor(request)).to_dict()
        return _ok({"api_state": "ready", "api_listener": "host_owned",
                    "service": v}, max_bytes=max_response_bytes)

    async def version(request: Request) -> Response:
        result = service.registry_version(actor_id=actor(request))
        return _ok({"registry_sha256": result}, max_bytes=max_response_bytes)

    async def checkpoint(request: Request) -> Response:
        principal = actor(request)
        _query(request, set())
        # Explicit host authorization on every request, even for checkpoint
        # metadata. List only missions the actor may read; do not disclose
        # global counts or checkpoint file locations.
        service.registry_version(actor_id=principal)
        data = (checkpoint_monitor.status() if checkpoint_monitor is not None
                else NexusMissionCheckpointMonitor.absent())
        visible = service.list_missions(actor_id=principal, limit=128)
        products = {p: 0 for p in ("nexus", "aegisgrid", "live_forge", "colossus")}
        for row in visible:
            products[row["product"]] += 1
        if history_view is not None:
            data = {**data, "historical_generations_configured": True}
        if source_archive is not None:
            data = {**data, "source_archive": source_archive.visible_status(
                r["mission_id"] for r in visible)}
        return _ok({**data, "visible_mission_count": len(visible),
                    "visible_by_product": products, "list_limit": 128},
                   max_bytes=max_response_bytes)

    async def missions(request: Request) -> Response:
        q = _query(request, {"product", "status", "limit"})
        limit = 50
        if "limit" in q:
            v = q["limit"]
            if not re.fullmatch(r"[1-9][0-9]{0,2}", v) or int(v) > 100:
                raise HTTPException(400, "Invalid list limit")
            limit = int(v)
        rows = service.list_missions(actor_id=actor(request), limit=limit,
                                     product=q.get("product"), status=q.get("status"))
        return _ok({"missions": list(rows), "count": len(rows), "limit": limit},
                   max_bytes=max_response_bytes)

    async def detail(request: Request) -> Response:
        mission_id = request.path_params["mission_id"]
        if not _is_safe_mission(mission_id):
            raise HTTPException(404, "Not found")
        _query(request, set())
        row = service.get_mission(mission_id, actor_id=actor(request))
        return _ok(row, max_bytes=max_response_bytes)

    async def history(request: Request) -> Response:
        mission_id = request.path_params["mission_id"]
        if not _is_safe_mission(mission_id):
            raise HTTPException(404, "Not found")
        _query(request, set())
        rows = service.mission_history(mission_id, actor_id=actor(request))
        return _ok({"mission_id": mission_id, "events": list(rows), "count": len(rows)},
                   max_bytes=max_response_bytes)

    async def preview(request: Request) -> Response:
        mission_id = request.path_params["mission_id"]
        if not _is_safe_mission(mission_id):
            raise HTTPException(404, "Not found")
        _query(request, set())
        report = service.preview_workflow(mission_id, actor_id=actor(request))
        return _ok(report.to_dict(), max_bytes=max_response_bytes)

    async def inspect(request: Request) -> Response:
        mission_id = request.path_params["mission_id"]
        if not _is_safe_mission(mission_id):
            raise HTTPException(404, "Not found")
        _query(request, set())
        report = service.inspect_workflow(mission_id, actor_id=actor(request))
        return _ok(report.to_dict(), max_bytes=max_response_bytes)

    async def monitor(request: Request) -> Response:
        mission_id = request.path_params["mission_id"]
        if not _is_safe_mission(mission_id):
            raise HTTPException(404, "Not found")
        _query(request, set())
        principal = actor(request)
        # Always authorize against MissionService first, even when the monitor
        # source is absent. Never disclose existence of a restricted mission.
        service.get_mission(mission_id, actor_id=principal)
        kind = request.url.path.rsplit("/", 1)[-1]
        if kind == "timeline":
            events = service.mission_history(mission_id, actor_id=principal)
            data = views.timeline(mission_id, events)
        elif kind == "graph":
            data = views.graph(mission_id)
        elif kind == "laboratory":
            data = views.laboratory(mission_id)
        elif kind == "trust":
            data = views.trust(mission_id)
        else:
            raise HTTPException(404, "Not found")
        if (kind in {"graph", "laboratory", "trust"} and data.get("available") is False
                and source_archive is not None):
            data = source_archive.summary(kind, mission_id) or data
        return _ok(data, max_bytes=max_response_bytes)

    async def archives(request: Request) -> Response:
        mission_id = request.path_params["mission_id"]
        if not _is_safe_mission(mission_id):
            raise HTTPException(404, "Not found")
        _query(request, set())
        # Do not disclose historical membership until current host policy
        # has authorized the exact mission, even if absent from current data.
        service.get_mission(mission_id, actor_id=actor(request))
        if history_view is None:
            return _ok({"available": False, "mission_id": mission_id,
                        "reason": "history_not_configured", "historical_only": True},
                       max_bytes=max_response_bytes)
        return _ok(history_view.generations(mission_id), max_bytes=max_response_bytes)

    async def historical_summary(request: Request) -> Response:
        mid = request.path_params["mission_id"]
        gen = request.path_params["generation"]
        kind = request.path_params["kind"]
        if not _is_safe_mission(mid):
            raise HTTPException(404, "Not found")
        _query(request, set())
        service.get_mission(mid, actor_id=actor(request))
        if not re.fullmatch(r"[1-9][0-9]{0,18}", gen) or kind not in {"graph", "laboratory", "trust"}:
            raise HTTPException(400, "Invalid historical request")
        if history_view is None:
            return _ok({"available": False, "mission_id": mid,
                        "reason": "history_not_configured", "historical_only": True},
                       max_bytes=max_response_bytes)
        # An absent or unauthorized historical generation is not a session
        # change. Keep it distinct from true integrity/drift failures.
        if not any(r["generation"] == int(gen) for r in history_view.generations(mid)["generations"]):
            raise HTTPException(404, "Not found")
        result = history_view.summary(int(gen), mid, kind)
        if result is None:
            return _ok({"available": False, "mission_id": mid, "kind": kind,
                        "generation": int(gen), "reason": "source_not_archived",
                        "historical_only": True}, max_bytes=max_response_bytes)
        return _ok(result, max_bytes=max_response_bytes)

    def dashboard_asset(name: str, media_type: str) -> Response:
        # Exact closed list; no user-supplied path is ever joined.
        data = (assets_root / name).read_bytes()
        if len(data) > 200_000:
            raise HTTPException(500, "Dashboard asset oversized")
        return Response(data, media_type=media_type)

    async def public_asset(request: Request) -> Response:
        name, mime = _ASSETS[request.url.path]
        return dashboard_asset(name, mime)

    routes = [
        Route("/api/v1/capabilities", capabilities, methods=["GET"]),
        Route("/api/v1/status", status, methods=["GET"]),
        Route("/api/v1/checkpoint", checkpoint, methods=["GET"]),
        Route("/api/v1/version", version, methods=["GET"]),
        Route("/api/v1/missions", missions, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}/history", history, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}/archives", archives, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}/archives/{generation}/{kind}", historical_summary, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}/preview", preview, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}/timeline", monitor, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}/graph", monitor, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}/laboratory", monitor, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}/trust", monitor, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}/inspect", inspect, methods=["GET"]),
        Route("/api/v1/missions/{mission_id}", detail, methods=["GET"]),
    ]
    for path in _ASSETS:
        routes.append(Route(path, public_asset, methods=["GET"]))

    app = Starlette(debug=False, routes=routes)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    async def boundary(request: Request, call_next: Any) -> Response:
        # Refuse requests from non-loopback peers *even if* server is bound
        # incorrectly or Host is spoofed. No proxy headers are trusted here.
        if not _peer_is_loopback(request):
            response: Response = _json({"error": "loopback_required"}, status=403)
        elif request.url.path.startswith("/api/"):
            principal = auth.actor_for(request.headers.get("authorization"))
            if principal is None:
                response = _json({"error": "authentication_required"}, status=401)
                response.headers["WWW-Authenticate"] = 'Bearer realm="Colossus Mission Control"'
            else:
                request.state.actor_id = principal
                try:
                    # A checkpoint-backed session is pinned to its startup
                    # generation. Reject all API reads if the source changes.
                    if checkpoint_monitor is not None:
                        checkpoint_monitor.assert_current()
                    if source_archive is not None:
                        source_archive.assert_current()
                    if history_view is not None:
                        history_view.assert_current()
                    response = await call_next(request)
                except NexusMissionCheckpointMonitorError:
                    response = _json({"error": "checkpoint_changed_restart_required"}, status=409)
                except NexusMissionMonitorError:
                    response = _json({"error": "monitor_source_inconsistent"}, status=409)
                except NexusMissionSourceArchiveError:
                    response = _json({"error": "source_archive_changed_restart_required"}, status=409)
                except NexusMissionHistoryViewError:
                    response = _json({"error": "history_changed_restart_required"}, status=409)
                except NexusMissionServiceError as exc:
                    # An authorized preview/inspection of an unbound workflow
                    # is not a server fault or an authentication failure.
                    # This exact public condition reveals no internal details;
                    # the service has already checked the actor's permissions.
                    if str(exc) == "mission workflow not configured":
                        response = _json({"error": "workflow_not_configured"}, status=409)
                    else:
                        response = _json({"error": "operation_denied_or_unavailable"}, status=403)
                except NexusMissionRegistryError:
                    response = _json({"error": "mission_unavailable"}, status=404)
                except HTTPException as exc:
                    response = _json({"error": "invalid_request", "status": exc.status_code},
                                     status=exc.status_code)
                except Exception:
                    response = _json({"error": "internal_error"}, status=500)
        else:
            try:
                response = await call_next(request)
            except Exception:
                response = _json({"error": "internal_error"}, status=500)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'")
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=boundary)
    return app


__all__ = ["MISSION_HTTP_API_SCHEMA", "NexusMissionHTTPAPIError",
           "MissionAPIAuthenticator", "create_mission_http_app"]
