from __future__ import annotations

"""SkillsBench request/response contract normalization for AegisForge.

This module is the boundary between generic A2A messages and the SkillsBench
adapter.  It does not solve tasks, call an LLM, access the network, or execute
commands.  It only:

- extracts task text and metadata from heterogeneous A2A/AgentBeats payloads;
- detects whether a request should be routed to SkillsBench;
- classifies task_id/category/tags using the public standard-v1 catalog;
- attaches safe workspace context when requested;
- builds a stable response payload shape for the harness/result emitter.

The official SkillsBench green/worker remains the owner of hidden tests and
score calculation.  This contract layer only helps AegisForge speak a stable,
worker-compatible participant dialect.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
import os
import re

from .task_catalog import (
    TASK_SET_CONDITION,
    TASK_SET_NAME,
    classify_task,
    extract_task_id,
    get_task_profile,
    infer_family_from_signals,
    mime_hints_for_family,
    normalize_task_id,
    preferred_outputs_for_family,
)
from .workspace import SkillsBenchWorkspace, build_workspace_context


CONTRACT_VERSION = "skillsbench_contract_v0_1_request_response_normalizer_2026_06_02"

SKILLSBENCH_ENV_MARKERS = (
    "AEGISFORGE_FORCE_SKILLSBENCH",
    "AEGISFORGE_ENABLE_SKILLSBENCH",
    "AEGISFORGE_TRACK",
    "AEGISFORGE_BENCHMARK",
    "AEGISFORGE_ADAPTER",
    "AEGISFORGE_TASK_SET",
    "AEGISFORGE_CONDITION",
    "AEGISFORGE_SCENARIO_FAMILY",
    "SKILLSBENCH_AGENT_HARNESS",
    "SKILLSBENCH_AGENT_MODEL",
    "SKILLSBENCH_AGENT_PROVIDER",
    "SKILLSBENCH_AGENT_BASE_URL",
    "SKILLSBENCH_AGENT_TIMEOUT_SEC",
)

SKILLSBENCH_TEXT_MARKERS = (
    "skillsbench",
    "skillsbench-leaderboard",
    "benchflow",
    "benchflow-ai",
    "standard-v1",
    "with_skills",
    "with-skills",
    "artifact_refs",
    "artifact-refs",
    "task_id",
    "task digest",
    "task_digest",
)

REQUEST_METADATA_KEYS = (
    "metadata",
    "meta",
    "context",
    "task",
    "task_info",
    "task_metadata",
    "scenario",
    "request",
    "input",
    "params",
    "configuration",
)

TEXT_KEYS = (
    "text",
    "content",
    "prompt",
    "instruction",
    "instructions",
    "message",
    "task",
    "description",
    "body",
    "query",
    "question",
)

SAFE_TEXT_LIMIT = int(os.getenv("AEGISFORGE_SKILLSBENCH_CONTRACT_TEXT_LIMIT", "200000"))


@dataclass(frozen=True)
class SkillsBenchArtifactRequest:
    """Expected output artifact shape inferred from task family/catalog."""

    name: str
    mime_type: str
    role: str = "candidate_output"
    required: bool = False
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SkillsBenchRequest:
    """Normalized request handed from agent.py/executor.py to the harness."""

    contract_version: str
    track: str
    benchmark: str
    task_set: str
    condition: str
    task_id: str
    category: str
    difficulty: str
    tags: tuple[str, ...]
    family: str
    prompt: str
    metadata: dict[str, Any]
    classification: dict[str, Any]
    expected_outputs: tuple[SkillsBenchArtifactRequest, ...]
    workspace_context: dict[str, Any] = field(default_factory=dict)
    shard_index: int | None = None
    num_shards: int | None = None
    source: str = "a2a"
    route_reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        data["warnings"] = list(self.warnings)
        data["expected_outputs"] = [item.as_dict() for item in self.expected_outputs]
        return data

    @property
    def has_known_task(self) -> bool:
        return bool(self.task_id and self.classification.get("matched"))

    @property
    def is_standard_v1(self) -> bool:
        return self.task_set == "standard-v1" or self.classification.get("source") == "standard-v1"


@dataclass(frozen=True)
class SkillsBenchResultPayload:
    """Stable payload shape returned by the SkillsBench adapter/harness."""

    contract_version: str
    status: str
    task_id: str
    category: str
    family: str
    answer: str
    files: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    artifacts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    commands: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    validation: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["files"] = list(self.files)
        data["artifacts"] = list(self.artifacts)
        data["commands"] = list(self.commands)
        return data

    def final_text(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, separators=(",", ":"), default=str)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    return {}


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return _safe_text(value, limit=500)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child, depth=depth + 1) for key, child in list(value.items())[:300]}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child, depth=depth + 1) for child in list(value)[:300]]
    try:
        json.dumps(value)
        return value
    except Exception:
        return _safe_text(value, limit=1000)


def _safe_text(value: Any, *, limit: int = SAFE_TEXT_LIMIT) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


def _maybe_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return {}
    if not (cleaned.startswith("{") and cleaned.endswith("}")):
        return {}
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return {}
    return _coerce_mapping(parsed)


def _flatten_text(value: Any, *, limit: int = SAFE_TEXT_LIMIT, depth: int = 0) -> str:
    if value is None or depth > 7 or limit <= 0:
        return ""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, Mapping):
        parts: list[str] = []
        total = 0
        for key, child in list(value.items())[:220]:
            key_text = str(key)
            child_text = _flatten_text(child, limit=1200, depth=depth + 1)
            parts.append(key_text)
            if child_text:
                parts.append(child_text)
            total += len(key_text) + len(child_text)
            if total >= limit:
                break
        return " ".join(parts)[:limit]
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(child, limit=1200, depth=depth + 1) for child in list(value)[:220])[:limit]
    return _safe_text(value, limit=limit)


def _get_attr_or_key(value: Any, key: str) -> Any:
    if isinstance(value, Mapping) and key in value:
        return value[key]
    return getattr(value, key, None)


def _deep_merge(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> dict[str, Any]:
    out = _coerce_mapping(left)
    for key, value in _coerce_mapping(right).items():
        if key in out and isinstance(out[key], Mapping) and isinstance(value, Mapping):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _deep_find_first(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    wanted = {key.lower() for key in keys}
    stack: list[Any] = [mapping]
    seen = 0
    while stack and seen < 1200:
        current = stack.pop()
        seen += 1
        if isinstance(current, Mapping):
            for key, value in current.items():
                if str(key).lower() in wanted:
                    return value
                if isinstance(value, (Mapping, list, tuple)):
                    stack.append(value)
        elif isinstance(current, (list, tuple)):
            stack.extend(list(current)[:120])
    return None


def extract_message_metadata(message: Any) -> dict[str, Any]:
    """Extract best-effort metadata from A2A-like objects or dictionaries."""

    metadata: dict[str, Any] = {}

    direct = _get_attr_or_key(message, "metadata")
    if isinstance(direct, Mapping):
        metadata = _deep_merge(metadata, direct)

    if isinstance(message, Mapping):
        for key in REQUEST_METADATA_KEYS:
            value = message.get(key)
            if isinstance(value, Mapping):
                metadata = _deep_merge(metadata, value)
        # Keep selected top-level fields because some gateways do not nest them.
        for key in (
            "task_id",
            "id",
            "task_set",
            "condition",
            "category",
            "difficulty",
            "tags",
            "benchmark",
            "track",
            "adapter",
            "shard_index",
            "num_shards",
            "workspace",
            "workspace_dir",
            "input_dir",
            "output_dir",
        ):
            if key in message and key not in metadata:
                metadata[key] = _json_safe(message[key])

    # A2A message objects often expose context/task identifiers.
    for attr in ("task_id", "id", "context_id", "contextId", "role"):
        value = getattr(message, attr, None)
        if value and attr not in metadata:
            metadata[attr] = _json_safe(value)

    # Parse embedded JSON if message itself is string-like.
    if isinstance(message, str):
        metadata = _deep_merge(metadata, _maybe_json_object(message))

    return metadata


def extract_message_text(message: Any) -> str:
    """Extract readable task prompt text from A2A-like payloads."""

    if message is None:
        return ""

    if isinstance(message, str):
        return message[:SAFE_TEXT_LIMIT]

    pieces: list[str] = []

    if isinstance(message, Mapping):
        for key in TEXT_KEYS:
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                pieces.append(value)
            elif isinstance(value, Mapping):
                nested = extract_message_text(value)
                if nested:
                    pieces.append(nested)

        parts = message.get("parts")
        if isinstance(parts, list):
            for part in parts[:80]:
                part_text = extract_message_text(part)
                if part_text:
                    pieces.append(part_text)

    # A2A typed objects frequently have .parts where TextPart is under .root
    parts = getattr(message, "parts", None)
    if isinstance(parts, list):
        for part in parts[:80]:
            part_text = extract_message_text(part)
            if part_text:
                pieces.append(part_text)

    root = getattr(message, "root", None)
    if root is not None and root is not message:
        root_text = extract_message_text(root)
        if root_text:
            pieces.append(root_text)

    for attr in TEXT_KEYS:
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            pieces.append(value)

    if not pieces:
        flattened = _flatten_text(message, limit=SAFE_TEXT_LIMIT)
        if flattened:
            pieces.append(flattened)

    text = "\n".join(piece.strip() for piece in pieces if piece and piece.strip())
    return text[:SAFE_TEXT_LIMIT]


def _env_snapshot() -> dict[str, str]:
    return {key: str(os.getenv(key, "") or "") for key in SKILLSBENCH_ENV_MARKERS}


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name, "") or "").strip().lower() in {"1", "true", "yes", "on", "force", "forced"}


def is_skillsbench_request(
    *,
    message: Any = None,
    metadata: Mapping[str, Any] | None = None,
    text: str = "",
    trust_environment: bool = True,
) -> bool:
    """Return True when a payload should be routed to SkillsBench.

    CyberGym/Pi-Bench priority should still be enforced by the caller before
    invoking this function.
    """

    merged = _deep_merge(extract_message_metadata(message), metadata or {})
    task_text = "\n".join(part for part in (extract_message_text(message), str(text or "")) if part)

    if trust_environment:
        if _env_truthy("AEGISFORGE_FORCE_SKILLSBENCH") or _env_truthy("AEGISFORGE_ENABLE_SKILLSBENCH"):
            return True
        env_blob = " ".join(_env_snapshot().values()).lower().replace("_", "-")
        if any(marker in env_blob for marker in ("skillsbench", "benchflow", "standard-v1", "with-skills")):
            return True

    explicit_track = _deep_find_first(merged, ("track", "benchmark", "adapter", "task_set", "scenario_family"))
    if explicit_track:
        normalized = str(explicit_track).lower().replace("_", "-")
        if any(marker in normalized for marker in ("skillsbench", "benchflow", "standard-v1", "general-purpose")):
            return True

    if extract_task_id(merged, task_text):
        return True

    blob = (_flatten_text(merged) + "\n" + task_text).lower().replace("_", "-")
    return any(marker.replace("_", "-") in blob for marker in SKILLSBENCH_TEXT_MARKERS)


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, str):
        # Tags sometimes arrive as comma-separated text.
        if "," in value:
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return (value.strip(),) if value.strip() else tuple()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple()


def _artifact_requests_for(classification: Mapping[str, Any]) -> tuple[SkillsBenchArtifactRequest, ...]:
    family = str(classification.get("family") or "general")
    outputs = list(classification.get("preferred_outputs") or preferred_outputs_for_family(family))
    mimes = list(classification.get("mime_hints") or mime_hints_for_family(family))
    specs: list[SkillsBenchArtifactRequest] = []
    for index, name in enumerate(outputs):
        mime = mimes[index] if index < len(mimes) else "application/octet-stream"
        required = index == 0
        specs.append(
            SkillsBenchArtifactRequest(
                name=str(name),
                mime_type=str(mime),
                required=required,
                description=f"Preferred SkillsBench {family} output #{index + 1}",
            )
        )
    return tuple(specs)


def normalize_skillsbench_request(
    *,
    message: Any = None,
    metadata: Mapping[str, Any] | None = None,
    text: str = "",
    include_workspace: bool = False,
    trust_environment: bool = True,
) -> SkillsBenchRequest:
    """Normalize an A2A/AgentBeats SkillsBench request."""

    warnings: list[str] = []
    extracted_metadata = extract_message_metadata(message)
    merged_metadata = _deep_merge(extracted_metadata, metadata or {})
    message_text = extract_message_text(message)
    prompt = "\n".join(part for part in (message_text, str(text or "")) if part and str(part).strip())[:SAFE_TEXT_LIMIT]

    classification = classify_task(merged_metadata, prompt)
    profile = classification.get("profile") if isinstance(classification.get("profile"), Mapping) else None

    task_id = ""
    category = ""
    difficulty = ""
    tags: tuple[str, ...] = tuple()

    if profile:
        task_id = str(profile.get("task_id", ""))
        category = str(profile.get("category", ""))
        difficulty = str(profile.get("difficulty", ""))
        tags = _string_list(profile.get("tags"))
    else:
        task_id = normalize_task_id(
            _deep_find_first(merged_metadata, ("task_id", "id", "task", "name", "challenge_id", "scenario_id"))
            or extract_task_id(merged_metadata, prompt)
        )
        category = str(_deep_find_first(merged_metadata, ("category", "task_category")) or "general-purpose")
        difficulty = str(_deep_find_first(merged_metadata, ("difficulty", "level")) or "unknown")
        tags = _string_list(_deep_find_first(merged_metadata, ("tags", "labels")))

    family = str(classification.get("family") or infer_family_from_signals(merged_metadata, prompt) or "general")
    task_set = str(_deep_find_first(merged_metadata, ("task_set", "suite")) or os.getenv("AEGISFORGE_TASK_SET") or TASK_SET_NAME or "standard-v1")
    condition = str(_deep_find_first(merged_metadata, ("condition", "skills_condition")) or os.getenv("AEGISFORGE_CONDITION") or TASK_SET_CONDITION or "with_skills")
    benchmark = str(_deep_find_first(merged_metadata, ("benchmark", "leaderboard")) or os.getenv("AEGISFORGE_BENCHMARK") or "SkillsBench")
    track = str(_deep_find_first(merged_metadata, ("track", "adapter")) or os.getenv("AEGISFORGE_TRACK") or "skillsbench")

    if not task_id:
        warnings.append("No exact standard-v1 task_id was detected; using signal-inferred family only.")
    if not prompt.strip():
        warnings.append("Request prompt text is empty after normalization.")

    workspace_context: dict[str, Any] = {}
    if include_workspace:
        try:
            workspace_context = build_workspace_context(merged_metadata, prompt, include_text=False)
        except Exception as exc:
            warnings.append(f"workspace context unavailable: {str(exc)[:240]}")

    expected_outputs = _artifact_requests_for(classification)

    route_reason = "env/metadata/text indicates SkillsBench" if is_skillsbench_request(
        message=message,
        metadata=merged_metadata,
        text=prompt,
        trust_environment=trust_environment,
    ) else "not-skillsbench-by-heuristic"

    return SkillsBenchRequest(
        contract_version=CONTRACT_VERSION,
        track="skillsbench" if "skillsbench" in track.lower() or "benchflow" in track.lower() else track,
        benchmark=benchmark,
        task_set=task_set,
        condition=condition,
        task_id=task_id,
        category=category,
        difficulty=difficulty,
        tags=tags,
        family=family,
        prompt=prompt,
        metadata=merged_metadata,
        classification=classification,
        expected_outputs=expected_outputs,
        workspace_context=workspace_context,
        shard_index=_int_or_none(_deep_find_first(merged_metadata, ("shard_index", "shard")) or os.getenv("SHARD_INDEX")),
        num_shards=_int_or_none(_deep_find_first(merged_metadata, ("num_shards", "shards")) or os.getenv("NUM_SHARDS")),
        source="a2a/agentbeats",
        route_reason=route_reason,
        warnings=tuple(warnings),
    )


def discover_request_workspace(request: SkillsBenchRequest) -> SkillsBenchWorkspace:
    """Create a workspace object from a normalized request."""

    return SkillsBenchWorkspace.discover(request.metadata, request.prompt, task_id=request.task_id or "skillsbench_task")


def build_result_payload(
    request: SkillsBenchRequest,
    *,
    answer: str,
    files: Sequence[Mapping[str, Any]] | None = None,
    artifacts: Sequence[Mapping[str, Any]] | None = None,
    commands: Sequence[Mapping[str, Any]] | None = None,
    validation: Mapping[str, Any] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    status: str = "completed",
    error: str = "",
) -> SkillsBenchResultPayload:
    """Build a stable result payload for result_emitter.py / adapter.py."""

    base_diagnostics = {
        "created_at": now_iso(),
        "request_contract": request.contract_version,
        "task_set": request.task_set,
        "condition": request.condition,
        "difficulty": request.difficulty,
        "tags": list(request.tags),
        "expected_outputs": [item.as_dict() for item in request.expected_outputs],
        "route_reason": request.route_reason,
        "warnings": list(request.warnings),
    }
    if diagnostics:
        base_diagnostics.update(_coerce_mapping(diagnostics))

    return SkillsBenchResultPayload(
        contract_version=CONTRACT_VERSION,
        status=status,
        task_id=request.task_id,
        category=request.category,
        family=request.family,
        answer=str(answer or ""),
        files=tuple(_coerce_mapping(item) for item in (files or [])),
        artifacts=tuple(_coerce_mapping(item) for item in (artifacts or [])),
        commands=tuple(_coerce_mapping(item) for item in (commands or [])),
        validation=_coerce_mapping(validation or {}),
        diagnostics=base_diagnostics,
        error=str(error or ""),
    )


def request_debug_summary(request: SkillsBenchRequest) -> dict[str, Any]:
    """Compact, non-secret summary for logs/health checks."""

    env = _env_snapshot()
    safe_env = {key: ("<set>" if value and "KEY" in key else value) for key, value in env.items()}
    return {
        "contract_version": request.contract_version,
        "track": request.track,
        "benchmark": request.benchmark,
        "task_set": request.task_set,
        "condition": request.condition,
        "task_id": request.task_id,
        "category": request.category,
        "difficulty": request.difficulty,
        "family": request.family,
        "known_task": request.has_known_task,
        "expected_outputs": [item.as_dict() for item in request.expected_outputs],
        "shard_index": request.shard_index,
        "num_shards": request.num_shards,
        "route_reason": request.route_reason,
        "warnings": list(request.warnings),
        "env": safe_env,
    }


def validate_contract_selftest() -> dict[str, Any]:
    """Small import-time-free self-test used by local validation."""

    sample = {
        "metadata": {
            "task_id": "dialogue-parser",
            "task_set": "standard-v1",
            "condition": "with_skills",
        },
        "text": "Solve task_id: dialogue-parser. Parse dialogue JSON.",
    }
    request = normalize_skillsbench_request(message=sample)
    result = build_result_payload(request, answer="ok", files=[{"name": "solution.json"}])
    errors: list[str] = []
    if request.task_id != "dialogue-parser":
        errors.append(f"unexpected task_id: {request.task_id}")
    if request.family != "data_json":
        errors.append(f"unexpected family: {request.family}")
    if result.status != "completed":
        errors.append(f"unexpected status: {result.status}")
    try:
        json.loads(result.final_text())
    except Exception as exc:
        errors.append(f"result final_text is not JSON: {exc}")
    return {
        "ok": not errors,
        "errors": errors,
        "contract_version": CONTRACT_VERSION,
        "sample_request": request_debug_summary(request),
    }


__all__ = [
    "CONTRACT_VERSION",
    "SkillsBenchArtifactRequest",
    "SkillsBenchRequest",
    "SkillsBenchResultPayload",
    "build_result_payload",
    "discover_request_workspace",
    "extract_message_metadata",
    "extract_message_text",
    "is_skillsbench_request",
    "normalize_skillsbench_request",
    "now_iso",
    "request_debug_summary",
    "validate_contract_selftest",
]
