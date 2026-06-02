from __future__ import annotations

"""SkillsBench result emission utilities for AegisForge.

This module converts a normalized SkillsBenchResultPayload into deterministic
files, manifests, and executor-friendly artifact records.  It is intentionally
filesystem-only: no LLM calls, no shell execution, no network access, and no
secret handling.

The goal is to give the higher-level harness one stable place to persist and
describe outputs, while keeping the generic A2A executor thin.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import base64
import hashlib
import json
import mimetypes
import os
import re

from .contract import (
    CONTRACT_VERSION,
    SkillsBenchRequest,
    SkillsBenchResultPayload,
    build_result_payload,
)
from .workspace import SkillsBenchWorkspace, WorkspaceFile


RESULT_EMITTER_VERSION = "skillsbench_result_emitter_v0_1_worker_contract_files_2026_06_02"

DEFAULT_RESULT_JSON = "skillsbench_result.json"
DEFAULT_ANSWER_MD = "skillsbench_answer.md"
DEFAULT_MANIFEST_JSON = "skillsbench_output_manifest.json"
DEFAULT_ARTIFACT_INDEX_MD = "skillsbench_artifact_index.md"

MAX_TEXT_BYTES = int(os.getenv("AEGISFORGE_SKILLSBENCH_EMITTER_MAX_TEXT_BYTES", "400000"))
MAX_BINARY_BYTES = int(os.getenv("AEGISFORGE_SKILLSBENCH_EMITTER_MAX_BINARY_BYTES", "12000000"))
MAX_OUTPUT_FILES = int(os.getenv("AEGISFORGE_SKILLSBENCH_EMITTER_MAX_OUTPUT_FILES", "32"))


@dataclass(frozen=True)
class EmittedFile:
    """Serializable file description for one persisted output."""

    name: str
    path: str
    relative_path: str
    mime_type: str
    size_bytes: int
    sha256: str
    role: str = "output"
    artifact_name: str = ""
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SkillsBenchEmission:
    """Result of persisting a SkillsBench harness output."""

    version: str
    created_at: str
    task_id: str
    category: str
    family: str
    output_dir: str
    status: str
    answer: str
    result_payload: dict[str, Any]
    files: tuple[EmittedFile, ...] = field(default_factory=tuple)
    artifact_records: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    manifest_path: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["files"] = [item.as_dict() for item in self.files]
        data["artifact_records"] = list(self.artifact_records)
        data["warnings"] = list(self.warnings)
        return data

    def final_text(self) -> str:
        """Compact JSON text suitable for A2A final messages."""

        public = {
            "status": self.status,
            "track": "skillsbench",
            "task_id": self.task_id,
            "category": self.category,
            "family": self.family,
            "answer": self.answer,
            "files": [item.as_dict() for item in self.files],
            "artifact_refs_candidate": [
                {
                    "artifact_name": record.get("artifact_name"),
                    "file_name": record.get("file_name") or record.get("name"),
                    "mime_type": record.get("mime_type"),
                    "sha256": record.get("sha256"),
                    "size_bytes": record.get("size_bytes"),
                    "path": record.get("path"),
                    "uri": record.get("uri"),
                }
                for record in self.artifact_records
            ],
            "result_payload": self.result_payload,
        }
        return json.dumps(public, ensure_ascii=False, separators=(",", ":"), default=str)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: Any, *, fallback: str = "skillsbench_output", limit: int = 120) -> str:
    text = str(value or "").strip()
    text = text.replace("\\", "/").split("/")[-1]
    text = re.sub(r"[^A-Za-z0-9_.+\-]+", "_", text).strip("._")
    if not text:
        text = fallback
    return text[:limit]


def _artifact_name(value: Any, *, fallback: str = "skillsbench_artifact") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.+\-]+", "_", text).strip("._")
    return (text or fallback)[:120]


def _mime_for_name(name: str) -> str:
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    suffix = Path(name).suffix.lower()
    if suffix in {".md", ".txt", ".log"}:
        return "text/markdown" if suffix == ".md" else "text/plain"
    if suffix in {".json", ".json5"}:
        return "application/json"
    if suffix == ".csv":
        return "text/csv"
    if suffix in {".patch", ".diff"}:
        return "text/x-diff"
    if suffix == ".lean":
        return "text/plain"
    return "application/octet-stream"


def _is_binary_name(name: str) -> bool:
    return Path(name).suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".zip",
        ".tar",
        ".gz",
        ".mp3",
        ".wav",
        ".mp4",
        ".mov",
        ".avi",
        ".stl",
        ".dxf",
        ".obj",
        ".pcap",
        ".db",
        ".sqlite",
    }


def _coerce_jsonable(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return str(value)[:1000]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "sha256": hashlib.sha256(value).hexdigest(),
            "size_bytes": len(value),
            "base64": base64.b64encode(value[:MAX_BINARY_BYTES]).decode("ascii"),
        }
    if isinstance(value, bytearray):
        return _coerce_jsonable(bytes(value), depth=depth + 1)
    if isinstance(value, Mapping):
        return {str(k): _coerce_jsonable(v, depth=depth + 1) for k, v in list(value.items())[:500]}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_jsonable(item, depth=depth + 1) for item in list(value)[:500]]
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)[:2000]


def _bytes_from_any(value: Any, *, default_json: bool = True) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value[:MAX_BINARY_BYTES]
    if isinstance(value, bytearray):
        return bytes(value)[:MAX_BINARY_BYTES]
    if isinstance(value, str):
        text = value
        # Try base64 only when the field looks intentionally encoded.
        stripped = text.strip()
        if stripped.startswith("base64:"):
            try:
                return base64.b64decode(stripped.split(":", 1)[1], validate=True)[:MAX_BINARY_BYTES]
            except Exception:
                pass
        return text.encode("utf-8", errors="replace")[:MAX_TEXT_BYTES]
    if default_json:
        return (json.dumps(_coerce_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")[:MAX_TEXT_BYTES]
    return str(value).encode("utf-8", errors="replace")[:MAX_TEXT_BYTES]


def _extract_content(record: Mapping[str, Any]) -> bytes:
    for key in ("bytes", "file_bytes", "raw_bytes", "content_bytes"):
        if key in record:
            value = record.get(key)
            if isinstance(value, str):
                # Accept both plain text and base64-encoded bytes.
                encoding = str(record.get("encoding") or record.get("content_encoding") or "").lower()
                if encoding == "base64":
                    try:
                        return base64.b64decode(value, validate=True)[:MAX_BINARY_BYTES]
                    except Exception:
                        return value.encode("utf-8", errors="replace")[:MAX_TEXT_BYTES]
            return _bytes_from_any(value)
    for key in ("text", "content", "markdown", "body"):
        if key in record:
            return _bytes_from_any(record.get(key))
    for key in ("payload", "data", "json"):
        if key in record:
            return _bytes_from_any(record.get(key))
    return b""


def _safe_output_name(record: Mapping[str, Any], *, fallback: str) -> str:
    name = (
        record.get("file_name")
        or record.get("filename")
        or record.get("name")
        or record.get("path")
        or fallback
    )
    safe = _slug(name, fallback=fallback)
    if "." not in safe:
        mime = str(record.get("mime_type") or record.get("mime") or "")
        if "json" in mime:
            safe += ".json"
        elif "csv" in mime:
            safe += ".csv"
        elif "diff" in mime or "patch" in mime:
            safe += ".patch"
        else:
            safe += ".md"
    return safe


def _read_path_if_safe(record: Mapping[str, Any], workspace: SkillsBenchWorkspace) -> bytes:
    path_value = record.get("path")
    if not path_value:
        return b""
    try:
        path = Path(str(path_value)).expanduser()
        if not path.is_absolute():
            path = workspace.root / path
        path = path.resolve()
        # Accept paths under workspace root or output_dir only.
        path.relative_to(workspace.root.resolve())
    except Exception:
        try:
            path.relative_to(workspace.output_dir.resolve())
        except Exception:
            return b""
    try:
        return path.read_bytes()[:MAX_BINARY_BYTES]
    except Exception:
        return b""


def artifact_record_from_file(file: EmittedFile, *, raw: bytes | None = None) -> dict[str, Any]:
    """Create an executor-compatible artifact record for FilePart emission."""

    raw_bytes = raw if raw is not None else b""
    return {
        "artifact_name": file.artifact_name or Path(file.name).stem,
        "name": file.name,
        "file_name": file.name,
        "mime_type": file.mime_type,
        "sha256": file.sha256,
        "size_bytes": file.size_bytes,
        "path": file.path,
        "uri": f"artifact://skillsbench/{file.sha256[:16]}/{file.name}",
        "role": file.role,
        "description": file.description,
        # Keep both forms: JSON-safe base64 and direct bytes for in-process use.
        "encoding": "base64",
        "file_bytes": base64.b64encode(raw_bytes[:MAX_BINARY_BYTES]).decode("ascii") if raw_bytes else "",
    }


class SkillsBenchResultEmitter:
    """Persist SkillsBench result payloads and build artifact records."""

    def __init__(self, workspace: SkillsBenchWorkspace) -> None:
        self.workspace = workspace
        self.warnings: list[str] = []

    @classmethod
    def for_request(cls, request: SkillsBenchRequest) -> "SkillsBenchResultEmitter":
        return cls(SkillsBenchWorkspace.discover(request.metadata, request.prompt, task_id=request.task_id or "skillsbench_task"))

    def _write(self, name: str, content: Any, *, mime_type: str | None = None, role: str = "output", artifact_name: str = "", description: str = "") -> tuple[EmittedFile, bytes]:
        raw = _bytes_from_any(content)
        if not raw:
            raw = b"\n"
        if _is_binary_name(name):
            raw = raw[:MAX_BINARY_BYTES]
        else:
            raw = raw[:MAX_TEXT_BYTES]

        info: WorkspaceFile = self.workspace.write_output(name, raw, mime_type=mime_type or _mime_for_name(name))
        emitted = EmittedFile(
            name=info.name,
            path=info.path,
            relative_path=info.relative_path,
            mime_type=mime_type or info.mime_type,
            size_bytes=info.size_bytes,
            sha256=info.sha256,
            role=role,
            artifact_name=artifact_name or Path(info.name).stem,
            description=description,
        )
        return emitted, raw

    def _emit_user_files(self, result: SkillsBenchResultPayload) -> tuple[list[EmittedFile], list[dict[str, Any]], list[dict[str, Any]]]:
        emitted_files: list[EmittedFile] = []
        artifact_records: list[dict[str, Any]] = []
        file_manifest: list[dict[str, Any]] = []

        combined: list[Mapping[str, Any]] = []
        for item in list(result.files) + list(result.artifacts):
            if isinstance(item, Mapping):
                combined.append(item)

        for index, record in enumerate(combined[:MAX_OUTPUT_FILES]):
            try:
                raw = _extract_content(record)
                if not raw:
                    raw = _read_path_if_safe(record, self.workspace)
                if not raw:
                    # Manifest-only records are still represented as JSON so the
                    # evaluator/gateway can see the intended output shape.
                    raw = _bytes_from_any({"record": _coerce_jsonable(record), "note": "manifest-only output record"})
                    fallback = f"skillsbench_record_{index}.json"
                    mime = "application/json"
                else:
                    fallback = f"skillsbench_output_{index}.md"
                    mime = str(record.get("mime_type") or record.get("mime") or "")

                name = _safe_output_name(record, fallback=fallback)
                mime_type = mime or str(record.get("mime_type") or record.get("mime") or _mime_for_name(name))
                emitted, written_raw = self._write(
                    name,
                    raw,
                    mime_type=mime_type,
                    role=str(record.get("role") or "harness_output"),
                    artifact_name=_artifact_name(record.get("artifact_name") or record.get("artifact") or Path(name).stem),
                    description=str(record.get("description") or record.get("summary") or ""),
                )
                emitted_files.append(emitted)
                artifact_records.append(artifact_record_from_file(emitted, raw=written_raw))
                file_manifest.append(emitted.as_dict())
            except Exception as exc:
                self.warnings.append(f"failed to emit output record #{index}: {str(exc)[:240]}")

        return emitted_files, artifact_records, file_manifest

    def emit(self, request: SkillsBenchRequest, result: SkillsBenchResultPayload) -> SkillsBenchEmission:
        """Persist the full result and return executor-friendly artifacts."""

        emitted_files: list[EmittedFile] = []
        artifact_records: list[dict[str, Any]] = []

        # 1. Main answer as markdown.
        answer_text = result.answer or ""
        answer_md = (
            f"# SkillsBench Answer\n\n"
            f"- task_id: `{request.task_id or 'unknown'}`\n"
            f"- category: `{request.category or 'unknown'}`\n"
            f"- family: `{request.family or 'general'}`\n"
            f"- status: `{result.status}`\n\n"
            f"{answer_text}\n"
        )
        answer_file, answer_raw = self._write(
            DEFAULT_ANSWER_MD,
            answer_md,
            mime_type="text/markdown",
            role="answer",
            artifact_name="skillsbench_answer",
            description="Primary natural-language answer emitted by AegisForge SkillsBench harness.",
        )
        emitted_files.append(answer_file)
        artifact_records.append(artifact_record_from_file(answer_file, raw=answer_raw))

        # 2. Harness-produced files/artifacts.
        user_files, user_artifacts, file_manifest = self._emit_user_files(result)
        emitted_files.extend(user_files)
        artifact_records.extend(user_artifacts)

        # 3. Structured result JSON.
        result_payload = result.as_dict()
        result_payload.update(
            {
                "emitter_version": RESULT_EMITTER_VERSION,
                "request": request.as_dict(),
                "emitted_files": [item.as_dict() for item in emitted_files],
                "artifact_refs_candidate": [
                    {
                        "artifact_name": record.get("artifact_name"),
                        "file_name": record.get("file_name"),
                        "mime_type": record.get("mime_type"),
                        "sha256": record.get("sha256"),
                        "size_bytes": record.get("size_bytes"),
                        "uri": record.get("uri"),
                    }
                    for record in artifact_records
                ],
            }
        )
        result_file, result_raw = self._write(
            DEFAULT_RESULT_JSON,
            result_payload,
            mime_type="application/json",
            role="result_payload",
            artifact_name="skillsbench_result",
            description="Structured SkillsBench result payload.",
        )
        emitted_files.insert(0, result_file)
        artifact_records.insert(0, artifact_record_from_file(result_file, raw=result_raw))

        # 4. Artifact index for human/log diagnostics.
        index_md = self._render_artifact_index(request, result, emitted_files, artifact_records)
        index_file, index_raw = self._write(
            DEFAULT_ARTIFACT_INDEX_MD,
            index_md,
            mime_type="text/markdown",
            role="artifact_index",
            artifact_name="skillsbench_artifact_index",
            description="Human-readable index of emitted SkillsBench outputs.",
        )
        emitted_files.append(index_file)
        artifact_records.append(artifact_record_from_file(index_file, raw=index_raw))

        # 5. Final output manifest. Keep this last so it includes everything.
        manifest = {
            "schema": "aegisforge.skillsbench.output_manifest.v0_1",
            "version": RESULT_EMITTER_VERSION,
            "created_at": now_iso(),
            "contract_version": CONTRACT_VERSION,
            "task_id": request.task_id,
            "category": request.category,
            "family": request.family,
            "status": result.status,
            "output_dir": str(self.workspace.output_dir),
            "files": [item.as_dict() for item in emitted_files],
            "artifact_refs_candidate": [
                {
                    "artifact_name": record.get("artifact_name"),
                    "file_name": record.get("file_name"),
                    "mime_type": record.get("mime_type"),
                    "sha256": record.get("sha256"),
                    "size_bytes": record.get("size_bytes"),
                    "path": record.get("path"),
                    "uri": record.get("uri"),
                }
                for record in artifact_records
            ],
            "warnings": list(self.warnings),
            "safe_scope": {
                "no_network": True,
                "no_shell_execution": True,
                "workspace_bounded_writes": True,
                "no_secret_materialization": True,
            },
        }
        manifest_file, manifest_raw = self._write(
            DEFAULT_MANIFEST_JSON,
            manifest,
            mime_type="application/json",
            role="output_manifest",
            artifact_name="skillsbench_output_manifest",
            description="Manifest describing all persisted SkillsBench outputs.",
        )
        emitted_files.append(manifest_file)
        artifact_records.append(artifact_record_from_file(manifest_file, raw=manifest_raw))

        return SkillsBenchEmission(
            version=RESULT_EMITTER_VERSION,
            created_at=now_iso(),
            task_id=request.task_id,
            category=request.category,
            family=request.family,
            output_dir=str(self.workspace.output_dir),
            status=result.status,
            answer=result.answer,
            result_payload=result_payload,
            files=tuple(emitted_files),
            artifact_records=tuple(artifact_records),
            manifest_path=manifest_file.path,
            warnings=tuple(self.warnings),
        )

    @staticmethod
    def _render_artifact_index(
        request: SkillsBenchRequest,
        result: SkillsBenchResultPayload,
        files: Sequence[EmittedFile],
        artifact_records: Sequence[Mapping[str, Any]],
    ) -> str:
        lines = [
            "# SkillsBench Artifact Index",
            "",
            f"- emitter: `{RESULT_EMITTER_VERSION}`",
            f"- contract: `{CONTRACT_VERSION}`",
            f"- task_id: `{request.task_id or 'unknown'}`",
            f"- category: `{request.category or 'unknown'}`",
            f"- family: `{request.family or 'general'}`",
            f"- status: `{result.status}`",
            "",
            "## Files",
        ]
        for item in files:
            lines.append(f"- `{item.name}` ({item.mime_type}, {item.size_bytes} bytes, sha256={item.sha256})")
        lines.extend(["", "## Artifact ref candidates"])
        for record in artifact_records:
            lines.append(
                "- `{}` uri={} sha256={}".format(
                    record.get("file_name") or record.get("name"),
                    record.get("uri"),
                    record.get("sha256"),
                )
            )
        return "\n".join(lines).strip() + "\n"


def emit_result(
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
    workspace: SkillsBenchWorkspace | None = None,
) -> SkillsBenchEmission:
    """Convenience wrapper used by harness.py / adapter.py."""

    payload = build_result_payload(
        request,
        answer=answer,
        files=files,
        artifacts=artifacts,
        commands=commands,
        validation=validation,
        diagnostics=diagnostics,
        status=status,
        error=error,
    )
    emitter = SkillsBenchResultEmitter(workspace or SkillsBenchWorkspace.discover(request.metadata, request.prompt, task_id=request.task_id or "skillsbench_task"))
    return emitter.emit(request, payload)


def emission_as_agent_response(emission: SkillsBenchEmission) -> dict[str, Any]:
    """Return a shape compatible with current agent.py/executor.py collection.

    The existing executor already knows how to collect `artifacts`, `files`,
    `artifact_outputs`, and `artifact_refs_candidate` from agent state.  This
    helper returns all of those surfaces explicitly.
    """

    artifact_outputs = [
        {
            "artifact_name": record.get("artifact_name"),
            "file_name": record.get("file_name") or record.get("name"),
            "mime_type": record.get("mime_type"),
            "sha256": record.get("sha256"),
            "size_bytes": record.get("size_bytes"),
            "path": record.get("path"),
            "uri": record.get("uri"),
            "encoding": record.get("encoding"),
            "file_bytes": record.get("file_bytes"),
        }
        for record in emission.artifact_records
    ]
    return {
        "final_text": emission.final_text(),
        "payload": emission.as_dict(),
        "artifacts": list(emission.artifact_records),
        "artifact_outputs": artifact_outputs,
        "files": [item.as_dict() for item in emission.files],
        "deliverables": [item.name for item in emission.files],
        "artifact_refs_candidate": [
            {
                "artifact_name": item.get("artifact_name"),
                "file_name": item.get("file_name"),
                "mime_type": item.get("mime_type"),
                "sha256": item.get("sha256"),
                "size_bytes": item.get("size_bytes"),
                "uri": item.get("uri"),
                "path": item.get("path"),
            }
            for item in artifact_outputs
        ],
    }


def emit_failure_result(
    request: SkillsBenchRequest,
    *,
    error: str,
    diagnostics: Mapping[str, Any] | None = None,
    workspace: SkillsBenchWorkspace | None = None,
) -> SkillsBenchEmission:
    """Create a bounded failure payload without throwing away task context."""

    return emit_result(
        request,
        answer=f"SkillsBench task failed in AegisForge adapter before completion: {error}",
        files=[
            {
                "artifact_name": "skillsbench_failure",
                "file_name": "skillsbench_failure.json",
                "mime_type": "application/json",
                "payload": {
                    "error": str(error),
                    "diagnostics": dict(diagnostics or {}),
                    "task_id": request.task_id,
                    "category": request.category,
                    "family": request.family,
                },
            }
        ],
        diagnostics=diagnostics,
        status="failed",
        error=error,
        workspace=workspace,
    )


def validate_emitter_selftest() -> dict[str, Any]:
    """Small local validation used by tests and manual smoke checks."""

    from .contract import normalize_skillsbench_request

    request = normalize_skillsbench_request(
        message={
            "metadata": {"task_id": "dialogue-parser", "task_set": "standard-v1"},
            "text": "Solve dialogue-parser.",
        }
    )
    workspace = SkillsBenchWorkspace.discover(request.metadata, request.prompt, task_id=request.task_id)
    emission = emit_result(
        request,
        answer="ok",
        files=[
            {
                "artifact_name": "solution",
                "file_name": "solution.json",
                "mime_type": "application/json",
                "payload": {"ok": True},
            }
        ],
        workspace=workspace,
    )
    errors: list[str] = []
    if not emission.files:
        errors.append("no files emitted")
    if not emission.artifact_records:
        errors.append("no artifact records emitted")
    try:
        json.loads(emission.final_text())
    except Exception as exc:
        errors.append(f"final_text is not JSON: {exc}")
    return {
        "ok": not errors,
        "errors": errors,
        "version": RESULT_EMITTER_VERSION,
        "file_count": len(emission.files),
        "artifact_count": len(emission.artifact_records),
        "manifest_path": emission.manifest_path,
    }


__all__ = [
    "RESULT_EMITTER_VERSION",
    "EmittedFile",
    "SkillsBenchEmission",
    "SkillsBenchResultEmitter",
    "artifact_record_from_file",
    "emission_as_agent_response",
    "emit_failure_result",
    "emit_result",
    "now_iso",
    "validate_emitter_selftest",
]
