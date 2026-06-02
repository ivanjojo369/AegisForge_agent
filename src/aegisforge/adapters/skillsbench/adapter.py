from __future__ import annotations

"""High-level SkillsBench adapter for AegisForge.

This is the single integration point that agent.py and executor.py should call.
It hides the lower-level SkillsBench package pieces behind one stable API:

    normalize request  -> plan/harness  -> emit result files/artifact records
    contract.py        -> harness.py     -> result_emitter.py

The adapter is intentionally defensive:
- no network access;
- no shell execution;
- no direct secret handling;
- no private/hidden answer lookup;
- no hard dependency on A2A internals;
- graceful failure payloads instead of uncaught exceptions.

Recommended usage from agent.py:

    from .adapters.skillsbench.adapter import handle_skillsbench

    result = handle_skillsbench(
        message=message,
        metadata=metadata,
        text=text,
        reasoner=lambda ctx: self._skillsbench_reason(ctx),
    )

Recommended usage from executor.py:

    from .adapters.skillsbench.adapter import is_skillsbench_payload
"""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any
import json
import os

from .contract import (
    CONTRACT_VERSION,
    SkillsBenchRequest,
    extract_message_metadata,
    extract_message_text,
    is_skillsbench_request,
    normalize_skillsbench_request,
    request_debug_summary,
)
from .harness import (
    HARNESS_VERSION,
    ReasonerCallback,
    SkillsBenchHarness,
)
from .result_emitter import (
    RESULT_EMITTER_VERSION,
    SkillsBenchEmission,
    emission_as_agent_response,
    emit_failure_result,
)
from .task_catalog import (
    TASK_CATALOG_VERSION,
    catalog_summary,
    classify_task,
    validate_catalog,
)
from .workspace import (
    WORKSPACE_VERSION,
    SkillsBenchWorkspace,
)


ADAPTER_VERSION = "skillsbench_adapter_v0_1_agent_executor_bridge_2026_06_02"

AdapterLogger = Callable[[str, Mapping[str, Any]], None]


@dataclass(frozen=True)
class SkillsBenchAdapterConfig:
    """Runtime knobs for the SkillsBench adapter."""

    trust_environment: bool = True
    include_workspace_text: bool = True
    max_text_context_chars: int = 60000
    emit_failure_payloads: bool = True
    debug: bool = False
    source: str = "aegisforge"

    @classmethod
    def from_env(cls) -> "SkillsBenchAdapterConfig":
        return cls(
            trust_environment=_env_bool("AEGISFORGE_SKILLSBENCH_TRUST_ENV", default=True),
            include_workspace_text=_env_bool("AEGISFORGE_SKILLSBENCH_INCLUDE_WORKSPACE_TEXT", default=True),
            max_text_context_chars=_env_int("AEGISFORGE_SKILLSBENCH_MAX_TEXT_CONTEXT_CHARS", default=60000),
            emit_failure_payloads=_env_bool("AEGISFORGE_SKILLSBENCH_EMIT_FAILURE_PAYLOADS", default=True),
            debug=_env_bool("AEGISFORGE_SKILLSBENCH_ADAPTER_DEBUG", default=False),
            source=os.getenv("AEGISFORGE_SKILLSBENCH_ADAPTER_SOURCE", "aegisforge"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SkillsBenchAdapterResult:
    """Stable return object for agent.py/executor.py."""

    version: str
    ok: bool
    request: dict[str, Any]
    response: dict[str, Any]
    final_text: str
    payload: dict[str, Any]
    artifacts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    files: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    deliverables: tuple[str, ...] = field(default_factory=tuple)
    artifact_outputs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    artifact_refs_candidate: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifacts"] = list(self.artifacts)
        data["files"] = list(self.files)
        data["deliverables"] = list(self.deliverables)
        data["artifact_outputs"] = list(self.artifact_outputs)
        data["artifact_refs_candidate"] = list(self.artifact_refs_candidate)
        return data

    def as_agent_response(self) -> dict[str, Any]:
        """Shape compatible with the existing AegisForge agent/executor path."""

        return {
            "final_text": self.final_text,
            "payload": self.payload,
            "artifacts": list(self.artifacts),
            "files": list(self.files),
            "deliverables": list(self.deliverables),
            "artifact_outputs": list(self.artifact_outputs),
            "artifact_refs_candidate": list(self.artifact_refs_candidate),
            "diagnostics": dict(self.diagnostics),
            "ok": self.ok,
            "error": self.error,
        }


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "force", "forced"}


def _env_int(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _safe_json(value: Any, *, limit: int = 200000) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))[:limit]
    except Exception:
        return str(value)[:limit]


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}


def _log(logger: AdapterLogger | None, event: str, payload: Mapping[str, Any]) -> None:
    if logger is None:
        return
    try:
        logger(event, payload)
    except Exception:
        return


def is_skillsbench_payload(
    *,
    message: Any = None,
    metadata: Mapping[str, Any] | None = None,
    text: str = "",
    trust_environment: bool | None = None,
) -> bool:
    """Public route detector for executor.py/agent.py.

    Callers should still give CyberGym and Pi-Bench priority before routing to
    SkillsBench.
    """

    if trust_environment is None:
        trust_environment = SkillsBenchAdapterConfig.from_env().trust_environment
    return is_skillsbench_request(
        message=message,
        metadata=metadata,
        text=text,
        trust_environment=trust_environment,
    )


class SkillsBenchAdapter:
    """Top-level SkillsBench adapter.

    This class owns no solver logic directly.  It delegates to contract.py,
    workspace.py, harness.py, and result_emitter.py, then packages the result in
    the shape that AegisForge's existing executor can already collect.
    """

    def __init__(
        self,
        *,
        config: SkillsBenchAdapterConfig | None = None,
        reasoner: ReasonerCallback | None = None,
        logger: AdapterLogger | None = None,
    ) -> None:
        self.config = config or SkillsBenchAdapterConfig.from_env()
        self.reasoner = reasoner
        self.logger = logger
        self.last_request: SkillsBenchRequest | None = None
        self.last_emission: SkillsBenchEmission | None = None
        self.last_result: SkillsBenchAdapterResult | None = None
        self.last_error: str = ""

    def should_handle(
        self,
        *,
        message: Any = None,
        metadata: Mapping[str, Any] | None = None,
        text: str = "",
    ) -> bool:
        return is_skillsbench_payload(
            message=message,
            metadata=metadata,
            text=text,
            trust_environment=self.config.trust_environment,
        )

    def normalize(
        self,
        *,
        message: Any = None,
        metadata: Mapping[str, Any] | None = None,
        text: str = "",
        include_workspace: bool = False,
    ) -> SkillsBenchRequest:
        request = normalize_skillsbench_request(
            message=message,
            metadata=metadata,
            text=text,
            include_workspace=include_workspace,
            trust_environment=self.config.trust_environment,
        )
        self.last_request = request
        return request

    def handle(
        self,
        *,
        message: Any = None,
        metadata: Mapping[str, Any] | None = None,
        text: str = "",
        request: SkillsBenchRequest | None = None,
    ) -> SkillsBenchAdapterResult:
        """Run the full adapter and return agent/executor-compatible output."""

        try:
            normalized = request or self.normalize(message=message, metadata=metadata, text=text)
            self.last_request = normalized

            _log(
                self.logger,
                "skillsbench_adapter_start",
                {
                    "version": ADAPTER_VERSION,
                    "request": request_debug_summary(normalized),
                    "config": self.config.as_dict(),
                },
            )

            harness = SkillsBenchHarness(
                reasoner=self.reasoner,
                include_text_context=self.config.include_workspace_text,
                max_text_context_chars=self.config.max_text_context_chars,
            )
            emission = harness.handle_to_emission(request=normalized)
            self.last_emission = emission

            response = emission_as_agent_response(emission)
            result = self._build_result(
                request=normalized,
                response=response,
                emission=emission,
                ok=True,
            )
            self.last_result = result

            _log(
                self.logger,
                "skillsbench_adapter_complete",
                {
                    "task_id": normalized.task_id,
                    "family": normalized.family,
                    "artifact_count": len(result.artifacts),
                    "file_count": len(result.files),
                },
            )
            return result
        except Exception as exc:
            self.last_error = str(exc)
            if not self.config.emit_failure_payloads:
                raise

            fallback = request or self.normalize(message=message, metadata=metadata, text=text)
            workspace = SkillsBenchWorkspace.discover(
                fallback.metadata,
                fallback.prompt,
                task_id=fallback.task_id or "skillsbench_task",
            )
            emission = emit_failure_result(
                fallback,
                error=str(exc),
                diagnostics={
                    "adapter_version": ADAPTER_VERSION,
                    "stage": "SkillsBenchAdapter.handle",
                    "config": self.config.as_dict(),
                },
                workspace=workspace,
            )
            response = emission_as_agent_response(emission)
            result = self._build_result(
                request=fallback,
                response=response,
                emission=emission,
                ok=False,
                error=str(exc),
            )
            self.last_emission = emission
            self.last_result = result
            _log(
                self.logger,
                "skillsbench_adapter_failure",
                {
                    "error": str(exc),
                    "task_id": fallback.task_id,
                    "family": fallback.family,
                },
            )
            return result

    def _build_result(
        self,
        *,
        request: SkillsBenchRequest,
        response: Mapping[str, Any],
        emission: SkillsBenchEmission,
        ok: bool,
        error: str = "",
    ) -> SkillsBenchAdapterResult:
        payload = _coerce_mapping(response.get("payload"))
        artifacts = tuple(_coerce_mapping(item) for item in response.get("artifacts", []) or [])
        files = tuple(_coerce_mapping(item) for item in response.get("files", []) or [])
        deliverables = tuple(str(item) for item in response.get("deliverables", []) or [])
        artifact_outputs = tuple(_coerce_mapping(item) for item in response.get("artifact_outputs", []) or [])
        artifact_refs_candidate = tuple(_coerce_mapping(item) for item in response.get("artifact_refs_candidate", []) or [])

        diagnostics = {
            "adapter_version": ADAPTER_VERSION,
            "contract_version": CONTRACT_VERSION,
            "harness_version": HARNESS_VERSION,
            "result_emitter_version": RESULT_EMITTER_VERSION,
            "workspace_version": WORKSPACE_VERSION,
            "task_catalog_version": TASK_CATALOG_VERSION,
            "request_summary": request_debug_summary(request),
            "catalog_summary": catalog_summary(),
            "emission": {
                "version": emission.version,
                "manifest_path": emission.manifest_path,
                "output_dir": emission.output_dir,
                "file_count": len(emission.files),
                "artifact_record_count": len(emission.artifact_records),
                "warnings": list(emission.warnings),
            },
            "config": self.config.as_dict(),
        }

        return SkillsBenchAdapterResult(
            version=ADAPTER_VERSION,
            ok=ok,
            request=request.as_dict(),
            response=_coerce_mapping(response),
            final_text=str(response.get("final_text") or emission.final_text()),
            payload=payload or emission.as_dict(),
            artifacts=artifacts,
            files=files,
            deliverables=deliverables,
            artifact_outputs=artifact_outputs,
            artifact_refs_candidate=artifact_refs_candidate,
            diagnostics=diagnostics,
            error=str(error or ""),
        )


def handle_skillsbench(
    *,
    message: Any = None,
    metadata: Mapping[str, Any] | None = None,
    text: str = "",
    reasoner: ReasonerCallback | None = None,
    config: SkillsBenchAdapterConfig | None = None,
    logger: AdapterLogger | None = None,
) -> dict[str, Any]:
    """Convenience function returning current agent.py-compatible response."""

    return SkillsBenchAdapter(config=config, reasoner=reasoner, logger=logger).handle(
        message=message,
        metadata=metadata,
        text=text,
    ).as_agent_response()


def handle_skillsbench_result(
    *,
    message: Any = None,
    metadata: Mapping[str, Any] | None = None,
    text: str = "",
    reasoner: ReasonerCallback | None = None,
    config: SkillsBenchAdapterConfig | None = None,
    logger: AdapterLogger | None = None,
) -> SkillsBenchAdapterResult:
    """Convenience function returning the typed adapter result."""

    return SkillsBenchAdapter(config=config, reasoner=reasoner, logger=logger).handle(
        message=message,
        metadata=metadata,
        text=text,
    )


def skillsbench_adapter_health() -> dict[str, Any]:
    """Small health surface for diagnostics and tests."""

    catalog_validation = validate_catalog()
    return {
        "ok": bool(catalog_validation.get("ok")),
        "adapter_version": ADAPTER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "harness_version": HARNESS_VERSION,
        "result_emitter_version": RESULT_EMITTER_VERSION,
        "workspace_version": WORKSPACE_VERSION,
        "task_catalog_version": TASK_CATALOG_VERSION,
        "catalog": catalog_validation,
        "env": {
            "AEGISFORGE_TRACK": os.getenv("AEGISFORGE_TRACK", ""),
            "AEGISFORGE_BENCHMARK": os.getenv("AEGISFORGE_BENCHMARK", ""),
            "AEGISFORGE_TASK_SET": os.getenv("AEGISFORGE_TASK_SET", ""),
            "AEGISFORGE_FORCE_SKILLSBENCH": "<set>" if os.getenv("AEGISFORGE_FORCE_SKILLSBENCH") else "",
            "AEGISFORGE_ENABLE_SKILLSBENCH": "<set>" if os.getenv("AEGISFORGE_ENABLE_SKILLSBENCH") else "",
        },
    }


def validate_adapter_selftest() -> dict[str, Any]:
    """End-to-end adapter smoke without LLM, network, or shell execution."""

    metadata = {
        "task_id": "dialogue-parser",
        "task_set": "standard-v1",
        "condition": "with_skills",
        "category": "software-engineering",
    }
    text = "Solve SkillsBench task_id: dialogue-parser. Return a JSON parser output."
    adapter = SkillsBenchAdapter(config=SkillsBenchAdapterConfig(include_workspace_text=False, debug=True))
    result = adapter.handle(metadata=metadata, text=text)

    errors: list[str] = []
    if not result.ok:
        errors.append(f"adapter returned failure: {result.error}")
    if not result.artifacts:
        errors.append("no artifacts returned")
    if not result.files:
        errors.append("no files returned")
    if "dialogue-parser" not in _safe_json(result.request):
        errors.append("task_id missing from request")
    try:
        json.loads(result.final_text)
    except Exception as exc:
        errors.append(f"final_text is not JSON: {exc}")

    classification = classify_task(metadata, text)
    if classification.get("family") != "data_json":
        errors.append(f"unexpected classification: {classification}")

    return {
        "ok": not errors,
        "errors": errors,
        "adapter_version": ADAPTER_VERSION,
        "artifact_count": len(result.artifacts),
        "file_count": len(result.files),
        "deliverables": list(result.deliverables),
        "health": skillsbench_adapter_health(),
    }


__all__ = [
    "ADAPTER_VERSION",
    "AdapterLogger",
    "SkillsBenchAdapter",
    "SkillsBenchAdapterConfig",
    "SkillsBenchAdapterResult",
    "handle_skillsbench",
    "handle_skillsbench_result",
    "is_skillsbench_payload",
    "skillsbench_adapter_health",
    "validate_adapter_selftest",
]
