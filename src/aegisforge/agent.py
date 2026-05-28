from __future__ import annotations

"""AegisForge runtime agent v0.2.10.

Drop-in replacement for ``src/aegisforge/agent.py``.

This is the importable runtime module expected by the AegisForge A2A server,
executor, and unit tests.  It is deliberately **not** a patch/apply script.

Public exports provided here:
- ``AegisForgeAgent``
- ``Agent``
- ``get_message_text``
- ``new_agent_text_message``

The implementation keeps a stable compatibility surface while adding a broader
BrowseComp-Plus final-answer mode, robust A2A message parsing, safe updater
handling, adapter/status helpers, local fixed-corpus retrieval, config-object
normalization, and optional OpenAI-compatible LLM calls when credentials are available.
"""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import inspect
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping, MutableMapping, Protocol
from urllib import error as urllib_error, request as urllib_request
import zipfile

LOGGER = logging.getLogger(__name__)

AGENT_VERSION = "0.2.10-runtime-extended-agent181-contract"
AGENT_NAME = "AegisForgeAgent"

SUPPORTED_TRACKS: tuple[str, ...] = (
    "mcu",
    "mcu-minecraft",
    "officeqa",
    "crmarena",
    "fieldworkarena",
    "maizebargain",
    "tau2",
    "osworld",
    "pibench",
    "cybergym",
    "netarena",
    "browsecomp-plus",
    "browsecomp_plus",
    "openenv",
    "purple",
)

# ---------------------------------------------------------------------------
# Optional A2A imports with safe fallbacks.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised in the real A2A runtime.
    from a2a.types import Message, Part, TaskState, TextPart
except Exception:  # pragma: no cover - local/test fallback when a2a is absent.
    Message = Any  # type: ignore[assignment]
    Part = Any  # type: ignore[assignment]
    TextPart = Any  # type: ignore[assignment]

    class _TaskStateFallback:
        completed = "completed"
        failed = "failed"
        working = "working"
        input_required = "input_required"

    TaskState = _TaskStateFallback()  # type: ignore[assignment]

try:  # pragma: no cover - use official helpers when available.
    from a2a.utils import get_message_text as _a2a_get_message_text
except Exception:  # pragma: no cover
    _a2a_get_message_text = None

try:  # pragma: no cover - use official helper when available.
    from a2a.utils import new_agent_text_message as _a2a_new_agent_text_message
except Exception:  # pragma: no cover
    _a2a_new_agent_text_message = None


class AwaitableText(str):
    """String result that also works with ``await``.

    Older tests and executors call ``agent.handle_request(...)`` directly;
    newer code may await it.  Returning an awaitable string keeps both paths
    compatible without forcing a single calling convention.
    """

    def __new__(cls, value: Any) -> "AwaitableText":
        return super().__new__(cls, str(value))

    def __await__(self):
        async def _return_text() -> str:
            return str(self)

        return _return_text().__await__()


@dataclass(slots=True)
class AgentStatus:
    ok: bool = True
    status: str = "ready"
    name: str = AGENT_NAME
    version: str = AGENT_VERSION
    mode: str = "benchmark_safe"
    track: str = "purple"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SupportsUpdateStatus(Protocol):
    async def update_status(self, state: Any, message: Any | None = None, *, final: bool = False) -> Any:
        ...


# ---------------------------------------------------------------------------
# Generic serialization / A2A message helpers.
# ---------------------------------------------------------------------------
def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    try:
        return str(value)
    except Exception:
        return ""


def _normalize_for_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 7:
        return _coerce_text(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        try:
            return _normalize_for_json(asdict(value), depth=depth + 1)
        except Exception:
            return _coerce_text(value)
    if isinstance(value, Mapping):
        return {
            _coerce_text(key): _normalize_for_json(item, depth=depth + 1)
            for key, item in list(value.items())[:120]
        }
    if isinstance(value, (list, tuple, set)):
        return [_normalize_for_json(item, depth=depth + 1) for item in list(value)[:120]]
    if hasattr(value, "model_dump"):
        try:
            return _normalize_for_json(value.model_dump(), depth=depth + 1)
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _normalize_for_json(value.dict(), depth=depth + 1)
        except Exception:
            pass
    return _coerce_text(value)


def _read_attr_or_key(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _config_to_dict(config: Any) -> dict[str, Any]:
    """Normalize AppConfig/Pydantic/dataclass/mapping objects to a plain dict.

    Unit tests and server code pass an ``AppConfig`` object into the agent
    constructor.  ``dict(AppConfig(...))`` is not valid for many config model
    implementations, so this helper accepts the common model surfaces instead
    of assuming the config is iterable.
    """
    if config is None:
        return {}

    if isinstance(config, Mapping):
        return dict(config)

    if is_dataclass(config):
        try:
            data = asdict(config)
            return dict(data) if isinstance(data, Mapping) else {}
        except Exception:
            pass

    # Pydantic v2 and compatible config models.
    model_dump = getattr(config, "model_dump", None)
    if callable(model_dump):
        for call_kwargs in (
            {"mode": "python"},
            {},
        ):
            try:
                data = model_dump(**call_kwargs)
                if isinstance(data, Mapping):
                    return dict(data)
            except TypeError:
                continue
            except Exception:
                break

    # Pydantic v1 and older model-like objects.
    dict_method = getattr(config, "dict", None)
    if callable(dict_method):
        for call_kwargs in (
            {},
            {"exclude_none": False},
        ):
            try:
                data = dict_method(**call_kwargs)
                if isinstance(data, Mapping):
                    return dict(data)
            except TypeError:
                continue
            except Exception:
                break

    # SimpleNamespace / plain Python objects.
    try:
        data = vars(config)
        if isinstance(data, Mapping):
            return {str(key): value for key, value in data.items() if not str(key).startswith("_")}
    except TypeError:
        pass

    # Slotted config objects.
    result: dict[str, Any] = {}
    slots = getattr(type(config), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    for name in slots or ():
        if not name or str(name).startswith("_"):
            continue
        try:
            result[str(name)] = getattr(config, name)
        except Exception:
            continue
    if result:
        return result

    # Conservative last resort: expose a compact value without pretending the
    # object was iterable.
    return {"value": _coerce_text(config)}


def get_message_text(message: Any) -> str:
    """Return plain text from an A2A message-like object.

    Re-exported at module level because tests and executor code import it from
    ``aegisforge.agent``.  It handles official A2A models, dicts, JSON-RPC
    payloads, simple strings, and several historical local message shapes.
    """
    if _a2a_get_message_text is not None:
        try:
            text = _coerce_text(_a2a_get_message_text(message))
            if text:
                return text
        except Exception:
            pass

    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="ignore")

    # A2A JSON-RPC-like envelopes often wrap the actual message under params.
    if isinstance(message, Mapping):
        for key in ("text", "content", "message", "prompt", "query", "question", "input"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
        params = message.get("params")
        if isinstance(params, Mapping):
            for key in ("message", "text", "content", "prompt", "query", "question"):
                nested = params.get(key)
                if nested is not None:
                    text = get_message_text(nested)
                    if text:
                        return text
        parts = message.get("parts")
        if parts is not None:
            text = _parts_to_text(parts)
            if text:
                return text
        root = message.get("root")
        if root is not None:
            text = get_message_text(root)
            if text:
                return text
        return json.dumps(_normalize_for_json(message), ensure_ascii=False)

    parts = getattr(message, "parts", None)
    if parts is not None:
        text = _parts_to_text(parts)
        if text:
            return text

    root = getattr(message, "root", None)
    if root is not None and root is not message:
        text = get_message_text(root)
        if text:
            return text

    for attr in ("text", "content", "message", "prompt", "query", "question", "input"):
        if hasattr(message, attr):
            text = _coerce_text(getattr(message, attr))
            if text:
                return text

    return _coerce_text(message)


def _parts_to_text(parts: Any) -> str:
    chunks: list[str] = []
    try:
        iterable = list(parts)
    except Exception:
        return _coerce_text(parts)

    for part in iterable:
        if part is None:
            continue
        if isinstance(part, str):
            chunks.append(part)
            continue
        if isinstance(part, Mapping):
            for key in ("text", "content"):
                if isinstance(part.get(key), str):
                    chunks.append(_coerce_text(part.get(key)))
                    break
            else:
                root = part.get("root")
                if root is not None:
                    text = get_message_text(root)
                    if text:
                        chunks.append(text)
                elif "data" in part:
                    chunks.append(json.dumps(_normalize_for_json(part.get("data")), ensure_ascii=False))
            continue
        for attr in ("text", "content"):
            if hasattr(part, attr):
                text = _coerce_text(getattr(part, attr))
                if text:
                    chunks.append(text)
                    break
        else:
            root = getattr(part, "root", None)
            if root is not None:
                text = get_message_text(root)
                if text:
                    chunks.append(text)
    return "\n".join(chunk for chunk in chunks if chunk)


def new_agent_text_message(text: Any) -> Any:
    """Create an A2A-compatible agent text message, with a safe fallback."""
    clean_text = _coerce_text(text)
    if _a2a_new_agent_text_message is not None:
        try:
            return _a2a_new_agent_text_message(clean_text)
        except Exception:
            pass
    # Dict fallback intentionally mirrors the simplest A2A-ish shape.
    return {"role": "agent", "parts": [{"text": clean_text}]}


def _json_dumps(value: Any) -> str:
    return json.dumps(_normalize_for_json(value), ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Runtime agent.
# ---------------------------------------------------------------------------
class AegisForgeAgent:
    """Benchmark-safe AegisForge agent implementation.

    The class exposes the stable public API expected by server/executor tests
    while keeping benchmark routing deterministic and import-safe.  BrowseComp
    final responses are kept answer-only; diagnostics are stored in instance
    fields and log messages rather than user-visible output.
    """

    name = AGENT_NAME
    version = AGENT_VERSION
    supported_tracks = SUPPORTED_TRACKS

    def __init__(self, config: Any | None = None, **kwargs: Any) -> None:
        self.config: dict[str, Any] = _config_to_dict(config)
        self.config.update(kwargs)
        self.created_at = datetime.now(timezone.utc).isoformat()
        self._current_llm_calls = 0
        self._last_llm_error = ""
        self._last_route = "generic"
        self._last_answer = ""
        self.turns = 0
        self._browsecomp_plus_last_status: dict[str, Any] = {}
        self._browsecomp_plus_last_diag: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public status/summary API expected by tests and server code.
    # ------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": "Benchmark-safe AegisForge agent for A2A/OpenEnv/AgentBeats style evaluations.",
            "mode": "benchmark_safe",
            "tracks": list(self.supported_tracks),
            "capabilities": [
                "a2a_runtime_imports",
                "generic_request_handling",
                "deterministic_status",
                "adapter_statuses",
                "safe_updater_compatibility",
                "browsecomp_plus_final_answer_mode",
                "browsecomp_plus_local_retrieval",
                "optional_openai_compatible_llm",
                "config_object_normalization",
            ],
            "public_exports": ["AegisForgeAgent", "Agent", "get_message_text", "new_agent_text_message"],
        }

    def status(self) -> dict[str, Any]:
        track = _coerce_text(
            self.config.get("track")
            or os.getenv("AGENTBEATS_TRACK")
            or os.getenv("AGENTBEATS_BENCHMARK")
            or os.getenv("AEGISFORGE_TRACK")
            or "purple"
        )
        return {
            **AgentStatus(track=track).to_dict(),
            "created_at": self.created_at,
            "adapters": self.adapter_statuses(),
            "last_route": self._last_route,
            "llm_calls_used": self._current_llm_calls,
            "last_llm_error": self._last_llm_error,
            "browsecomp_plus": self._browsecomp_plus_last_status,
        }

    def adapter_statuses(self) -> dict[str, dict[str, Any]]:
        openenv_enabled = os.getenv("AEGISFORGE_OPENENV_DISABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}
        llm_ready = bool(self._llm_api_key())
        return {
            "core": {"ok": True, "enabled": True, "status": "ready"},
            "a2a": {"ok": True, "enabled": True, "status": "ready"},
            "executor": {"ok": True, "enabled": True, "status": "ready"},
            "openenv": {
                "ok": openenv_enabled,
                "enabled": openenv_enabled,
                "status": "ready" if openenv_enabled else "disabled",
            },
            "browsecomp_plus": {"ok": True, "enabled": True, "status": "ready"},
            "llm": {"ok": llm_ready, "enabled": llm_ready, "status": "ready" if llm_ready else "not_configured"},
        }

    # Historical aliases used by previous test suites/loaders.
    get_status = status
    get_summary = summary
    get_adapter_statuses = adapter_statuses

    # ------------------------------------------------------------------
    # Request handling API.
    # ------------------------------------------------------------------
    def _looks_like_updater(self, value: Any) -> bool:
        """Return True for TaskUpdater/DummyUpdater-style objects.

        The original agent_181 contract exposed ``async def run(message,
        updater) -> None``.  The core test calls whichever request-handling
        method it finds with exactly ``(message, updater)`` and expects the
        resolved value to be ``None``.  Therefore every public request alias in
        this lightweight runtime must detect updater-like second arguments and
        route through the A2A run contract instead of returning a chat string.
        """
        if value is None or isinstance(value, Mapping):
            return False
        updater_methods = (
            "update_status",
            "set_status",
            "add_artifact",
            "append_artifact",
            "add_message",
            "complete",
        )
        return any(hasattr(value, name) for name in updater_methods)

    async def handle_request(
        self,
        request: Any,
        updater: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str | None:
        """Handle a benchmark request using the agent_181-compatible contract.

        Contract copied from the full agent lineage:
        - ``await run(message, updater)`` is the canonical A2A execution path and
          returns ``None`` after writing status/artifact updates.
        - ``await handle_request(message, updater)`` must behave the same way.
        - ``await handle_request(message, metadata_dict)`` remains a direct
          request/response mode and returns the answer string.
        """
        actual_updater = updater
        actual_metadata = metadata

        if actual_metadata is None and isinstance(updater, Mapping):
            actual_metadata = updater
            actual_updater = None

        if self._looks_like_updater(actual_updater):
            await self.run(request, actual_updater)
            return None

        request_metadata = self._merge_metadata(request, actual_metadata, kwargs)
        task_text = get_message_text(request)
        answer = self._dispatch(task_text, request_metadata)
        self._last_answer = answer
        return answer

    async def ahandle_request(
        self,
        request: Any,
        updater: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str | None:
        return await self.handle_request(request, updater=updater, metadata=metadata, **kwargs)

    async def handle(
        self,
        request: Any,
        updater: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str | None:
        return await self.handle_request(request, updater=updater, metadata=metadata, **kwargs)

    async def process(
        self,
        request: Any,
        updater: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str | None:
        return await self.handle_request(request, updater=updater, metadata=metadata, **kwargs)

    async def execute(
        self,
        request: Any,
        updater: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str | None:
        return await self.handle_request(request, updater=updater, metadata=metadata, **kwargs)

    async def invoke(
        self,
        request: Any,
        updater: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str | None:
        return await self.handle_request(request, updater=updater, metadata=metadata, **kwargs)

    async def ainvoke(
        self,
        request: Any,
        updater: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str | None:
        return await self.handle_request(request, updater=updater, metadata=metadata, **kwargs)

    def handle_request_sync(
        self,
        request: Any,
        updater: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> str | None:
        """Synchronous helper for direct integrations.

        If a TaskUpdater/DummyUpdater is provided, this method follows the
        agent_181 test contract and returns ``None`` rather than a text answer.
        Use the async ``run``/``handle_request`` methods when the updater must be
        populated from synchronous code.
        """
        actual_metadata = metadata
        if actual_metadata is None and isinstance(updater, Mapping):
            actual_metadata = updater
            updater = None
        if self._looks_like_updater(updater):
            task_text = get_message_text(request)
            self._last_answer = self._dispatch(task_text, self._extract_metadata(request))
            return None
        request_metadata = self._merge_metadata(request, actual_metadata, kwargs)
        task_text = get_message_text(request)
        answer = self._dispatch(task_text, request_metadata)
        self._last_answer = answer
        return answer

    def __call__(
        self,
        request: Any,
        updater: Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        return self.handle_request(request, updater=updater, metadata=metadata, **kwargs)

    async def run(self, message: Any, updater: Any) -> None:
        self.turns = int(getattr(self, "turns", 0) or 0) + 1
        metadata = self._extract_metadata(message)
        task_text = get_message_text(message)
        """A2A executor entrypoint with the original agent_181 return contract.

        The old full agent exposes ``async def run(message, updater) -> None``:
        it sends working/completed updates and artifacts through the updater and
        does not return the final text.  Keeping that exact return contract is
        what makes ``tests/test_core/test_agent.py::test_agent_handle_request_generic``
        pass when it resolves the selected request method with ``asyncio.run``.
        """
        metadata = self._extract_metadata(message)
        task_text = get_message_text(message)
        try:
            await self._safe_update_status(updater, self._task_state("working"), "Classifying task and preparing execution route.", final=False)
            answer = self._dispatch(task_text, metadata)
            self._last_answer = answer
            await self._safe_add_artifact(updater, answer)
            await self._safe_update_status(updater, self._task_state("completed"), answer, final=True)
            return None
        except Exception as exc:
            LOGGER.exception("AegisForgeAgent run failed")
            error_text = f"AegisForgeAgent error: {type(exc).__name__}: {exc}"
            self._last_answer = error_text
            await self._safe_update_status(updater, self._task_state("failed"), error_text, final=True)
            return None

    def _dispatch(self, task_text: str, metadata: Mapping[str, Any]) -> str:
        if self._is_browsecomp_plus_protocol(task_text, metadata):
            self._last_route = "browsecomp_plus"
            return self._handle_browsecomp_plus_turn(task_text, metadata)
        if self._is_openenv_disabled_request(task_text, metadata):
            self._last_route = "openenv_disabled"
            return "OPENENV_DISABLED: the OpenEnv adapter is disabled or unavailable in this runtime."
        self._last_route = "generic"
        return self._handle_generic_turn(task_text, metadata)

    def _handle_generic_turn(self, task_text: str, metadata: Mapping[str, Any]) -> str:
        text = self._sanitize_text(task_text)
        lowered = text.lower()
        if self._is_summary_request(lowered):
            return _json_dumps(self.summary())
        if self._is_status_request(lowered):
            return _json_dumps(self.status())
        if "adapter" in lowered and "status" in lowered:
            return _json_dumps(self.adapter_statuses())
        if not text:
            return "AegisForgeAgent is ready."
        preview = text[:220]
        return f"AegisForgeAgent received the request: {preview}. Ready for benchmark-safe execution."

    async def _safe_update_status(self, updater: Any, state: Any, text: str, *, final: bool) -> bool:
        message = new_agent_text_message(text)
        attempts = (
            ("update_status", (state, message), {"final": final}),
            ("update_status", (state,), {"message": message, "final": final}),
            ("update_status", (state, message), {}),
            ("set_status", (state, message), {"final": final}),
            ("set_status", (state, message), {}),
            ("complete", (message,), {}) if final else ("", (), {}),
        )
        return await self._try_updater_calls(updater, attempts)

    async def _safe_add_artifact(self, updater: Any, text: str) -> bool:
        """Add the final response artifact using the legacy test contract name.

        ``tests/test_core/test_agent.py`` inspects the last artifact and expects
        ``artifact.get("name") == "AegisForgeResponse"``.  The full agent lineage
        used that public artifact name, so the lightweight runtime must not use
        generic names such as ``Result`` even though the payload is still plain
        text-only for broad A2A compatibility.
        """
        artifact = {"name": "AegisForgeResponse", "parts": [{"text": text}]}
        message = new_agent_text_message(text)
        attempts: tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...] = (
            ("add_artifact", (artifact,), {}),
            ("add_artifact", (), {"parts": [{"text": text}], "name": "AegisForgeResponse"}),
            ("append_artifact", (artifact,), {}),
            ("add_message", (message,), {}),
        )
        return await self._try_updater_calls(updater, attempts)

    async def _try_updater_calls(self, updater: Any, attempts: tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...]) -> bool:
        if updater is None:
            return False
        for name, args, kwargs in attempts:
            if not name or not hasattr(updater, name):
                continue
            method = getattr(updater, name)
            try:
                result = method(*args, **kwargs)
                if inspect.isawaitable(result):
                    await result
                return True
            except TypeError:
                continue
            except Exception:
                LOGGER.debug("Updater method %s failed", name, exc_info=True)
                continue
        return False

    def _task_state(self, name: str) -> Any:
        for candidate in (name, name.upper()):
            if hasattr(TaskState, candidate):
                return getattr(TaskState, candidate)
        return name

    def _is_status_request(self, text: str) -> bool:
        return "status" in text or "health" in text or "ready" in text

    def _is_summary_request(self, text: str) -> bool:
        return "summary" in text or "capabilities" in text or "agent card" in text

    def _is_openenv_disabled_request(self, task_text: str, metadata: Mapping[str, Any]) -> bool:
        combined = f"{task_text}\n{_json_dumps(metadata)}".lower()
        return "openenv" in combined and os.getenv("AEGISFORGE_OPENENV_DISABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

    # ------------------------------------------------------------------
    # BrowseComp-Plus detection and answer handling.
    # ------------------------------------------------------------------
    def _is_browsecomp_plus_protocol(self, task_text: str, metadata: Mapping[str, Any] | None = None) -> bool:
        safe_metadata: Mapping[str, Any] = metadata if isinstance(metadata, Mapping) else {}
        metadata_text = _json_dumps(safe_metadata)[:12000] if safe_metadata else ""
        env_hint = " ".join(
            _coerce_text(os.getenv(name, ""))
            for name in (
                "AGENTBEATS_TRACK",
                "AGENTBEATS_BENCHMARK",
                "AGENTBEATS_TASK",
                "BROWSECOMP_MODE",
                "BROWSECOMP_PLUS_MODE",
                "BROWSECOMP_CORPUS_PATH",
                "BROWSECOMP_PLUS_CORPUS_PATH",
                "BROWSECOMP_DATA_PATH",
                "BROWSECOMP_PLUS_DATA_PATH",
                "AMBER_COMPOSE_PROJECT",
                "GITHUB_WORKFLOW",
                "GITHUB_REPOSITORY",
                "GITHUB_REF",
                "AEGISFORGE_FORCE_BROWSECOMP_PLUS",
            )
        )
        combined = f"{task_text}\n{metadata_text}\n{env_hint}"
        lowered = combined.lower()
        compact = re.sub(r"[^a-z0-9]+", "", lowered)

        force_env = os.getenv("AEGISFORGE_FORCE_BROWSECOMP_PLUS", "").strip().lower() in {"1", "true", "yes", "on"}
        forced_markers = (
            "agent_mode': 'browsecomp_plus'",
            '"agent_mode": "browsecomp_plus"',
            "agent_mode: browsecomp_plus",
            "track': 'browsecomp-plus'",
            '"track": "browsecomp-plus"',
            "track: browsecomp-plus",
            "browsecomp-plus-leaderboard",
            "browsecomp_plus",
            "browsecomp-plus",
            "browsecomp plus",
        )
        if force_env or any(marker in lowered for marker in forced_markers) or "browsecomp" in compact:
            LOGGER.info("BROWSECOMP_PLUS_ROUTE_V0_2_5 reason=marker")
            return True

        # Do not steal strongly marked non-BrowseComp protocols.
        if any(marker in lowered for marker in (
            "maizebargain",
            "allocation_self",
            "payoff_matrix",
            "officeqa",
            "crmarena",
            "crm arena",
            "salesforce",
            "respond with [build] or [ask]",
            "[build] or [ask]",
        )):
            return False

        questionish = bool(re.search(
            r"(?is)(?:^|\n)\s*(?:question|query)\s*[:\-]\s*\S|"
            r"^\s*(?:what|which|who|whom|whose|when|where|why|how|identify|name|determine|find|list)\b|"
            r"\?\s*$",
            task_text.strip(),
        ))
        researchish = bool(re.search(
            r"(?is)\b(?:according to|based on|source|document|corpus|article|report|paper|website|published|released|founded|born|died|located|served|worked|played|won|company|organization|university|city|country|film|album|book|author|director|year|date)\b",
            combined,
        ))
        has_anchor = bool(
            re.search(r"\b(?:17|18|19|20)\d{2}\b", combined)
            or re.search(r'"[^"]{3,100}"|“[^”]{3,100}”|\'[^\']{3,100}\'', combined)
            or re.search(r"\b[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){1,6}\b", task_text)
        )
        auto_route = os.getenv("AEGISFORGE_BROWSECOMP_PLUS_AUTO_ROUTE", "1").strip().lower() not in {"0", "false", "no", "off"}
        routed = bool(auto_route and questionish and (researchish or has_anchor) and len(task_text.strip()) >= 24)
        if routed:
            LOGGER.info("BROWSECOMP_PLUS_ROUTE_V0_2_5 reason=auto_question")
        return routed

    def _handle_browsecomp_plus_turn(self, task_text: str, metadata: Mapping[str, Any]) -> str:
        question = self._browsecomp_plus_extract_question(task_text, metadata)
        prompt_context = self._browsecomp_plus_extract_context(task_text, metadata)
        local_evidence = self._browsecomp_plus_local_evidence(question or task_text)
        context = "\n\n".join(part for part in (prompt_context, local_evidence) if part).strip()

        answer = ""
        if context:
            answer = self._answer_from_context(question, context)
        if not answer:
            answer = self._call_llm_for_browsecomp(question or task_text, context)
        if not answer:
            answer = "INSUFFICIENT_INFORMATION"
        answer = self._browsecomp_plus_finalize_answer(answer, question=question)

        self._browsecomp_plus_last_status = {
            "mode": "browsecomp_plus_runtime_extended_v0_2_5",
            "question_chars": len(question),
            "prompt_context_chars": len(prompt_context),
            "local_evidence_chars": len(local_evidence),
            "context_chars": len(context),
            "local_evidence_present": bool(local_evidence),
            "retrieval_diag": _normalize_for_json(self._browsecomp_plus_last_diag),
            "llm_calls_used": self._current_llm_calls,
            "llm_error": self._last_llm_error,
            "answer_chars": len(answer),
        }
        LOGGER.info(
            "BROWSECOMP_PLUS_STATUS_V0_2_5 question_chars=%s context_chars=%s evidence_chars=%s answer_chars=%s llm_calls=%s llm_error=%s",
            len(question), len(context), len(local_evidence), len(answer), self._current_llm_calls, self._last_llm_error,
        )
        return answer

    def _browsecomp_plus_extract_question(self, task_text: str, metadata: Mapping[str, Any] | None = None) -> str:
        safe_metadata = metadata if isinstance(metadata, Mapping) else {}
        for key in ("question", "query", "prompt", "task", "input", "user_query"):
            value = safe_metadata.get(key) if isinstance(safe_metadata, Mapping) else None
            if isinstance(value, str) and value.strip():
                return self._sanitize_text(value)[:4000]
        text = _coerce_text(task_text).strip()
        match = re.search(r"(?is)(?:^|\n)\s*(?:question|query)\s*[:\-]\s*(.+?)(?=\n\s*(?:context|evidence|documents|sources|passages|answer|final)\s*[:\-]|\Z)", text)
        if match:
            return self._sanitize_text(match.group(1))[:4000]
        return self._sanitize_text(text)[:4000]

    def _browsecomp_plus_extract_context(self, task_text: str, metadata: Mapping[str, Any] | None = None) -> str:
        safe_metadata: Mapping[str, Any] = metadata if isinstance(metadata, Mapping) else {}
        parts: list[str] = []

        text = _coerce_text(task_text)
        for label in ("context", "evidence", "documents", "sources", "passages", "corpus", "snippet", "snippets"):
            pattern = rf"(?:^|\n)\s*{label}\s*[:\-]\s*(.+?)(?=\n\s*(?:question|query|answer|final)\s*[:\-]|\Z)"
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match and not self._browsecomp_plus_forbidden_name(label):
                parts.append(self._sanitize_text(match.group(1))[:8000])

        def walk(value: Any, path: str = "", depth: int = 0) -> None:
            if depth > 6 or len("\n\n".join(parts)) > 18000:
                return
            if path and self._browsecomp_plus_forbidden_name(path):
                return
            if isinstance(value, Mapping):
                for key, item in list(value.items())[:80]:
                    key_s = _coerce_text(key)
                    walk(item, f"{path}.{key_s}" if path else key_s, depth + 1)
                return
            if isinstance(value, (list, tuple, set)):
                for idx, item in enumerate(list(value)[:60]):
                    walk(item, f"{path}[{idx}]", depth + 1)
                return
            if isinstance(value, str) and len(value.strip()) >= 80:
                if re.search(r"(?i)(context|evidence|document|source|passage|corpus|snippet|content|article|text|body|page)", path):
                    parts.append(self._sanitize_text(value)[:6000])

        walk(safe_metadata)
        unique: list[str] = []
        seen: set[str] = set()
        for part in parts:
            key = re.sub(r"\s+", " ", part[:400]).lower()
            if key not in seen:
                seen.add(key)
                unique.append(part)
        return "\n\n".join(unique)[:22000]

    def _browsecomp_plus_finalize_answer(self, text: str, *, question: str = "") -> str:
        answer = self._sanitize_text(text)
        answer = re.sub(r"(?is)^(final answer|answer|respuesta final)\s*[:\-]\s*", "", answer).strip()
        answer = answer.strip(" \t\r\n`*_")
        # Final answer only: one concise line, no markdown/protocol text.
        answer = re.split(r"[\r\n]+", answer, maxsplit=1)[0].strip()
        answer = re.sub(r"(?i)^(therefore|so|thus),?\s+", "", answer).strip()
        if len(answer) > 260:
            answer = answer[:260].rsplit(" ", 1)[0].strip()
        return answer or "INSUFFICIENT_INFORMATION"

    def _answer_from_context(self, question: str, context: str) -> str:
        if not context:
            return ""
        # Explicit answer-like fields are accepted only if they are not evaluator labels.
        for pattern in (
            r"(?im)^\s*(?:response|final_response|observed_answer|candidate_answer|short_answer)\s*[:\-]\s*(.{1,240})$",
            r"(?im)^\s*(?:title|name|entity|person|place|organization)\s*[:\-]\s*(.{2,180})$",
        ):
            match = re.search(pattern, context)
            if match and not self._browsecomp_plus_forbidden_name(match.group(0).split(":", 1)[0]):
                return match.group(1).strip()

        # Otherwise use compact extractive answering from the highest-overlap sentence.
        terms = [term for term in self._browsecomp_plus_query_terms(question) if len(term) > 3][:12]
        sentences = re.split(r"(?<=[.!?])\s+|\n+", context)
        scored: list[tuple[int, int, str]] = []
        for sent in sentences[:500]:
            clean = self._sanitize_text(sent)
            if len(clean) < 8:
                continue
            low = clean.lower()
            score = sum(3 if " " in term else 1 for term in terms if term.lower() in low)
            if re.search(r"\b(?:is|was|were|are|called|named|founded|published|released|located|born|died)\b", low):
                score += 1
            if score > 0:
                scored.append((score, len(clean), clean))
        if not scored:
            return ""
        scored.sort(key=lambda item: (-item[0], item[1]))
        candidate = scored[0][2]
        # A sentence can still be too verbose; preserve the likely noun phrase when possible.
        for rx in (
            r"(?i)\b(?:answer|therefore|it is|it was|the answer is)\s*[:\-]?\s*([^.;]{2,180})",
            r"(?i)\b(?:called|named)\s+([^.;]{2,120})",
        ):
            match = re.search(rx, candidate)
            if match:
                return match.group(1).strip()
        return candidate

    def _browsecomp_plus_query_terms(self, question: str) -> list[str]:
        stop = {
            "the", "and", "for", "with", "from", "that", "this", "which", "what",
            "when", "where", "who", "whom", "whose", "how", "why", "was", "were",
            "are", "is", "does", "did", "about", "answer", "question", "query",
            "final", "only", "please", "return", "provide", "identify", "determine",
            "find", "name", "based", "according", "source", "document", "documents",
            "give", "tell", "following", "context", "evidence",
        }
        terms: list[str] = []

        def add(value: str) -> None:
            term = re.sub(r"\s+", " ", value.strip().lower()).strip(" .,:;!?()[]{}\"'`")
            if len(term) >= 3 and term not in stop and term not in terms:
                terms.append(term)

        text = _coerce_text(question)
        for groups in re.findall(r'"([^"]{3,140})"|“([^”]{3,140})”|\'([^\']{3,140})\'', text):
            add(next((item for item in groups if item), ""))
        for phrase in re.findall(r"\b[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){1,7}\b", text):
            if not phrase.isupper():
                add(phrase)
        for year in re.findall(r"\b(?:17|18|19|20)\d{2}\b", text):
            add(year)
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]{2,}", text.lower()):
            add(token)
        terms.sort(key=lambda term: ((" " not in term), -len(term), term))
        return terms[:36]

    def _browsecomp_plus_forbidden_name(self, name: str) -> bool:
        compact = re.sub(r"[^a-z0-9]+", "_", _coerce_text(name).lower())
        forbidden = (
            "answer_key", "answers_key", "gold", "label", "labels", "reward", "score", "scores",
            "result", "results", "eval", "evaluation", "leaderboard", "submission", "ground_truth",
            "truth", "solution", "solutions", "target", "expected", "oracle", "judge",
            "secret", "secrets", "credential", "credentials", "password", "passwd", "api_key",
            "apikey", "access_key", "private_key", "client_secret", "token", "tokens", "oauth",
            "bearer", "refresh_token", "id_token", "authorization",
        )
        return any(part in compact for part in forbidden)

    def _browsecomp_plus_local_evidence(self, question: str) -> str:
        roots = self._browsecomp_plus_candidate_roots()
        terms = self._browsecomp_plus_query_terms(question)
        diag: dict[str, Any] = {
            "roots_seen": len(roots),
            "root_names": [str(root)[:160] for root in roots[:12]],
            "files_seen": 0,
            "archives_seen": 0,
            "records_seen": 0,
            "hits": 0,
            "read_errors": 0,
            "forbidden_skips": 0,
        }
        self._browsecomp_plus_last_diag = diag
        if not roots or not terms:
            return ""

        suffixes = {".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".html", ".htm"}
        archive_suffixes = {".zip"}
        snippets: list[tuple[int, str, str]] = []
        scan_limit = self._int_env("AEGISFORGE_BROWSECOMP_SCAN_LIMIT", default=1600, minimum=200, maximum=3500)
        per_file_limit = self._int_env("AEGISFORGE_BROWSECOMP_FILE_LIMIT", default=650_000, minimum=80_000, maximum=2_000_000)

        def consider_text(source: str, raw: str) -> None:
            if not raw:
                return
            if self._browsecomp_plus_forbidden_name(source):
                diag["forbidden_skips"] += 1
                return
            records = self._text_records_from_jsonish(raw, source) if Path(source).suffix.lower() in {".json", ".jsonl"} else [raw]
            for idx, record in enumerate(records[:240]):
                clean = self._sanitize_text(record)
                if len(clean) < 40:
                    continue
                diag["records_seen"] += 1
                score = self._score_evidence(clean, source, terms)
                if score <= 0:
                    continue
                diag["hits"] += 1
                snippets.append((score, source, self._best_evidence_window(clean, terms)))

        for root in roots:
            try:
                iterator = root.rglob("*") if root.is_dir() else iter([root])
                for path in iterator:
                    if diag["files_seen"] >= scan_limit:
                        break
                    try:
                        if not path.is_file():
                            continue
                        if self._browsecomp_plus_forbidden_name(str(path)):
                            diag["forbidden_skips"] += 1
                            continue
                        suffix = path.suffix.lower()
                        if suffix not in suffixes and suffix not in archive_suffixes:
                            continue
                        diag["files_seen"] += 1
                        if suffix in archive_suffixes:
                            diag["archives_seen"] += 1
                            with zipfile.ZipFile(path) as zf:
                                for member in zf.infolist()[:600]:
                                    if member.is_dir():
                                        continue
                                    member_name = member.filename
                                    if self._browsecomp_plus_forbidden_name(member_name):
                                        diag["forbidden_skips"] += 1
                                        continue
                                    if Path(member_name).suffix.lower() not in suffixes:
                                        continue
                                    try:
                                        raw = zf.read(member)[:per_file_limit].decode("utf-8", errors="ignore")
                                    except Exception:
                                        diag["read_errors"] += 1
                                        continue
                                    consider_text(f"{path.name}!{member_name}", raw)
                            continue
                        raw = path.read_text(encoding="utf-8", errors="ignore")[:per_file_limit]
                        consider_text(str(path), raw)
                    except Exception:
                        diag["read_errors"] += 1
                        continue
            except Exception:
                diag["read_errors"] += 1
                continue

        snippets.sort(key=lambda item: item[0], reverse=True)
        chunks: list[str] = []
        seen: set[str] = set()
        for score, source, snippet in snippets[:12]:
            key = re.sub(r"\W+", " ", snippet[:300]).lower()
            if key in seen:
                continue
            seen.add(key)
            chunks.append(f"[source={Path(source.split('!', 1)[0]).name}; score={score}] {snippet}")
            if len("\n\n".join(chunks)) >= 14000:
                break
        return "\n\n".join(chunks)[:14000]

    def _browsecomp_plus_candidate_roots(self) -> list[Path]:
        env_names = (
            "BROWSECOMP_CORPUS_PATH",
            "BROWSECOMP_PLUS_CORPUS_PATH",
            "BROWSECOMP_DATA_PATH",
            "BROWSECOMP_PLUS_DATA_PATH",
            "CORPUS_PATH",
            "DOCUMENT_CORPUS_PATH",
            "DATASET_PATH",
            "DATA_PATH",
            "BENCHMARK_DATA_PATH",
        )
        static_roots = (
            "/data",
            "/dataset",
            "/datasets",
            "/corpus",
            "/app/data",
            "/app/corpus",
            "/app/dataset",
            "/workspace/data",
            "/workspace/corpus",
            "/workspace/dataset",
            "/workspace/datasets",
            "/workspace/docs",
            "/workspace/documents",
            "/workspace/sources",
            "/mnt/data/browsecomp",
            "/mnt/data/corpus",
            "/tmp/browsecomp",
            "/tmp/corpus",
            "/tmp/dataset",
        )
        broad_roots = {"/", "/tmp", "/app", "/workspace", "/mnt", "/mnt/data"}
        safe_child_names = ("browsecomp", "browsecomp_plus", "browsecomp-plus", "corpus", "document_corpus", "documents", "docs", "sources", "passages", "wiki", "dataset", "datasets", "data")
        roots: list[Path] = []
        seen: set[str] = set()

        def add(raw: str | Path) -> None:
            if not raw:
                return
            try:
                path = Path(raw).expanduser()
                if not path.exists():
                    return
                resolved_path = path.resolve()
                resolved = str(resolved_path)
                if resolved in broad_roots:
                    for child in safe_child_names:
                        add(resolved_path / child)
                    return
                if resolved in seen or self._browsecomp_plus_forbidden_name(resolved):
                    return
                seen.add(resolved)
                roots.append(resolved_path)
            except Exception:
                return

        for name in env_names:
            add(os.getenv(name, ""))
        for raw in static_roots:
            add(raw)

        child_name_re = re.compile(r"(?i)(browse|corpus|document|docs|source|passage|wiki|data|dataset)")
        for root in list(roots)[:16]:
            if not root.is_dir():
                continue
            try:
                for child in list(root.iterdir())[:100]:
                    if child.is_dir() and child_name_re.search(child.name):
                        add(child)
            except Exception:
                continue
        return roots[:28]

    def _text_records_from_jsonish(self, raw: str, source: str) -> list[str]:
        def flatten(value: Any, path: str = "", depth: int = 0) -> str:
            if depth > 6 or (path and self._browsecomp_plus_forbidden_name(path)):
                return ""
            if isinstance(value, str):
                return value
            if isinstance(value, (int, float, bool)):
                return str(value)
            if isinstance(value, Mapping):
                chunks: list[str] = []
                for key, item in list(value.items())[:80]:
                    key_s = _coerce_text(key)
                    if self._browsecomp_plus_forbidden_name(key_s):
                        continue
                    sub = flatten(item, f"{path}.{key_s}" if path else key_s, depth + 1)
                    if sub:
                        chunks.append(f"{key_s}: {sub}")
                return "\n".join(chunks)
            if isinstance(value, (list, tuple)):
                return "\n".join(flatten(item, path, depth + 1) for item in list(value)[:60])
            return ""

        records: list[str] = []
        try:
            obj = json.loads(raw)
            iterable = obj if isinstance(obj, list) else [obj]
            for item in iterable[:250]:
                flat = flatten(item)
                if flat:
                    records.append(flat)
        except Exception:
            for line in raw.splitlines()[:4000]:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                flat = flatten(obj)
                if flat:
                    records.append(flat)
        return records or [raw]

    def _score_evidence(self, raw: str, source_name: str, terms: list[str]) -> int:
        low = raw.lower()
        source_low = source_name.lower()
        score = 0
        for term in terms:
            term_l = term.lower()
            if " " in term_l:
                if term_l in low:
                    score += 10
                if term_l in source_low:
                    score += 12
            else:
                if len(term_l) <= 4:
                    count = len(re.findall(rf"(?<![a-z0-9]){re.escape(term_l)}(?![a-z0-9])", low))
                else:
                    count = low.count(term_l)
                if count:
                    score += min(8, count) * 2
                if term_l in source_low:
                    score += 5
        if re.search(r"(?i)\b(title|author|date|published|source|url|content|body|text)\b", raw[:1200]):
            score += 2
        return score

    def _best_evidence_window(self, raw: str, terms: list[str], radius: int = 1800) -> str:
        clean = self._sanitize_text(raw)
        low = clean.lower()
        positions = [low.find(term.lower()) for term in terms if low.find(term.lower()) >= 0]
        if not positions:
            return clean[: radius * 2]
        center = min(positions)
        start = max(0, center - radius)
        end = min(len(clean), center + radius * 2)
        return clean[start:end].strip()

    def _call_llm_for_browsecomp(self, question: str, context: str) -> str:
        system_prompt = (
            "You are a BrowseComp-Plus fixed-corpus research answer specialist. "
            "Return ONLY the final answer string. No reasoning, no citations, no markdown, no caveats. "
            "Use only the supplied question and context/evidence."
        )
        user_prompt = f"Question:\n{question[:4000].strip()}\n"
        if context:
            user_prompt += f"\nContext/evidence:\n{context[:18000]}\n"
        user_prompt += "\nFinal answer only:"
        return self._call_llm(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=160,
        )

    # ------------------------------------------------------------------
    # Metadata, sanitization, and optional LLM helpers.
    # ------------------------------------------------------------------
    def _merge_metadata(self, request: Any, metadata: Mapping[str, Any] | None, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        merged = self._extract_metadata(request)
        if isinstance(metadata, Mapping):
            merged.update(metadata)
        extra = kwargs.get("metadata") if isinstance(kwargs, Mapping) else None
        if isinstance(extra, Mapping):
            merged.update(extra)
        for key, value in kwargs.items():
            if key != "metadata":
                merged[key] = value
        return merged

    def _extract_metadata(self, message: Any) -> dict[str, Any]:
        candidates: list[Any] = []
        if isinstance(message, Mapping):
            candidates.append(message.get("metadata"))
            params = message.get("params")
            if isinstance(params, Mapping):
                candidates.append(params.get("metadata"))
                nested = params.get("message")
                if isinstance(nested, Mapping):
                    candidates.append(nested.get("metadata"))
        else:
            candidates.append(getattr(message, "metadata", None))
            root = getattr(message, "root", None)
            if root is not None:
                candidates.append(getattr(root, "metadata", None))
        result: dict[str, Any] = {}
        for raw in candidates:
            if isinstance(raw, Mapping):
                result.update(dict(raw))
        return result

    def _sanitize_text(self, value: Any) -> str:
        text = _coerce_text(value)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _int_env(self, name: str, *, default: int, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
        except Exception:
            return default

    def _llm_api_key(self) -> str:
        for name in (
            "AEGISFORGE_OPENAI_API_KEY",
            "AMBER_CONFIG_AGENT_OPENAI_API_KEY",
            "AGENT_OPENAI_API_KEY",
            "OPENAI_API_KEY",
        ):
            value = os.getenv(name, "").strip()
            if value:
                return value
        return ""

    def _llm_base_url(self) -> str:
        base = (
            os.getenv("AEGISFORGE_OPENAI_BASE_URL")
            or os.getenv("AGENT_OPENAI_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("LOCAL_LLM_BASE_URL")
            or "https://api.openai.com/v1"
        )
        return base.rstrip("/")

    def _llm_model(self) -> str:
        return (
            os.getenv("AEGISFORGE_LLM_MODEL")
            or os.getenv("AGENT_OPENAI_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("LOCAL_LLM_MODEL")
            or "gpt-4.1-mini"
        )

    def _call_llm(self, *args: Any, **kwargs: Any) -> str:
        """Call an OpenAI-compatible chat/completions endpoint if configured.

        Uses stdlib urllib to avoid adding dependencies.  Returns an empty
        string on failure and records the error in ``self._last_llm_error``.
        """
        self._current_llm_calls += 1
        api_key = self._llm_api_key()
        if not api_key:
            self._last_llm_error = "No OpenAI-compatible API key configured."
            return ""

        messages = kwargs.get("messages")
        if messages is None and args:
            messages = args[0]
        if not isinstance(messages, list):
            self._last_llm_error = "Invalid LLM messages payload."
            return ""

        payload = {
            "model": self._llm_model(),
            "messages": messages,
            "temperature": float(kwargs.get("temperature", 0.0)),
            "max_tokens": int(kwargs.get("max_tokens", 160)),
        }
        data = json.dumps(payload).encode("utf-8")
        url = f"{self._llm_base_url()}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        retries = self._int_env("AEGISFORGE_LLM_RETRIES", default=2, minimum=0, maximum=5)
        for attempt in range(retries + 1):
            request = urllib_request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib_request.urlopen(request, timeout=35) as response:  # noqa: S310 - user-configured endpoint in benchmark runtime.
                    raw = response.read().decode("utf-8", errors="ignore")
                obj = json.loads(raw)
                content = obj.get("choices", [{}])[0].get("message", {}).get("content", "")
                self._last_llm_error = ""
                return _coerce_text(content)
            except urllib_error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="ignore")[:800]
                except Exception:
                    pass
                self._last_llm_error = f"HTTPError {exc.code}: {body or exc.reason}"
                if exc.code in {408, 409, 429, 500, 502, 503, 504} and attempt < retries:
                    time.sleep(min(2.0, 0.6 * (attempt + 1)))
                    continue
                return ""
            except Exception as exc:
                self._last_llm_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    time.sleep(min(2.0, 0.4 * (attempt + 1)))
                    continue
                return ""
        return ""


# Historical alias expected by tests/loaders.
Agent = AegisForgeAgent

__all__ = [
    "AegisForgeAgent",
    "Agent",
    "AgentStatus",
    "AwaitableText",
    "get_message_text",
    "new_agent_text_message",
]
