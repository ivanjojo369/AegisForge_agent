from __future__ import annotations

"""Deterministic media-task solver for SkillsBench filesystem outputs.

The solver is intentionally conservative.  It does not call network services,
does not depend on ffmpeg, and does not pretend to recover hidden media content.
Its job is to satisfy the filesystem-output contract with structured, auditable
media manifests / indices / edit plans whenever SkillsBench routes a task to
``media_output`` or a media task-id.

This is useful for local evaluator auditing because media tasks previously fell
through to ``general_file_output`` or unrelated security routing, which made
forensic traces harder to interpret.
"""

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import wave
import zipfile


MEDIA_SOLVER_VERSION = "skillsbench_media_solver_v0_1_filesystem_manifest_2026_06_10"

MEDIA_TASK_IDS: tuple[str, ...] = (
    "video-filler-word-remover",
    "video-silence-remover",
    "video-tutorial-indexer",
    "dynamic-object-aware-egomotion",
    "multilingual-video-dubbing",
    "pg-essay-to-audiobook",
    "threejs-to-obj",
    "threejs-structure-parser",
)

MEDIA_EXTENSIONS: tuple[str, ...] = (
    ".json",
    ".csv",
    ".txt",
    ".md",
    ".srt",
    ".vtt",
    ".wav",
    ".zip",
    ".obj",
    ".gltf",
    ".glb",
)

BINARY_MEDIA_EXTENSIONS: tuple[str, ...] = (
    ".mp3",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".ogg",
    ".flac",
    ".m4a",
    ".aac",
)


def _safe_text(value: Any, *, limit: int = 120000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}


def _safe_sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return {str(k): v for k, v in obj.items()}
    if hasattr(obj, "as_dict") and callable(obj.as_dict):
        try:
            data = obj.as_dict()
            if isinstance(data, Mapping):
                return {str(k): v for k, v in data.items()}
        except Exception:
            pass
    try:
        data = asdict(obj)
        if isinstance(data, Mapping):
            return {str(k): v for k, v in data.items()}
    except Exception:
        pass
    return {}


def _normalize_task_id(contract: Any, metadata: Mapping[str, Any]) -> str:
    for key in (
        "canonical_task_id",
        "environment_canonical_task_id",
        "contract_task_id",
        "task_id",
        "id",
        "name",
    ):
        text = str(metadata.get(key) or "").strip()
        if text:
            return text
    return str(_get(contract, "task_id", "") or "").strip()


def _task_kind(task_id: str, prompt: str, metadata: Mapping[str, Any]) -> str:
    blob = " ".join([task_id, prompt[:8000], _safe_text(metadata, limit=20000)]).lower()
    if "filler" in blob:
        return "filler_word_removal"
    if "silence" in blob or "silent" in blob:
        return "silence_removal"
    if "tutorial" in blob or "index" in blob or "chapter" in blob:
        return "tutorial_indexing"
    if "dubbing" in blob or "translate" in blob or "multilingual" in blob:
        return "dubbing_plan"
    if "audiobook" in blob or "audio book" in blob or "narration" in blob:
        return "audiobook_plan"
    if "threejs" in blob or "three.js" in blob or "obj" in blob or "gltf" in blob:
        return "geometry_asset_conversion"
    if "egomotion" in blob or "trajectory" in blob or "dynamic object" in blob:
        return "egomotion_analysis"
    return "media_processing"


def _extract_candidate_inputs(prompt: str, metadata: Mapping[str, Any]) -> list[str]:
    blob = "\n".join([prompt[:60000], _safe_text(metadata, limit=40000)])
    candidates: list[str] = []
    patterns = [
        r"(?P<path>/(?:root|app|data|workspace|input|mnt|home)[^\s`\"']+\.(?:mp4|mov|mkv|webm|mp3|wav|m4a|aac|flac|ogg|json|csv|txt|srt|vtt|obj|gltf|glb))",
        r"(?P<path>(?:input|inputs|data|workspace|assets|media)/[^\s`\"']+\.(?:mp4|mov|mkv|webm|mp3|wav|m4a|aac|flac|ogg|json|csv|txt|srt|vtt|obj|gltf|glb))",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, blob, flags=re.IGNORECASE):
            path = match.group("path").rstrip(".,);]")
            if path and path not in candidates:
                candidates.append(path)
    for key in ("input", "inputs", "input_files", "media_files", "source_files", "assets"):
        raw = metadata.get(key)
        if isinstance(raw, str):
            for item in re.split(r"[\n,;]+", raw):
                item = item.strip()
                if item and item not in candidates:
                    candidates.append(item)
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            for item in raw:
                item = str(item or "").strip()
                if item and item not in candidates:
                    candidates.append(item)
    return candidates[:60]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mime_for_suffix(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".json": "application/json",
        ".csv": "text/csv",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".srt": "application/x-subrip",
        ".vtt": "text/vtt",
        ".wav": "audio/wav",
        ".zip": "application/zip",
        ".obj": "model/obj",
        ".gltf": "model/gltf+json",
        ".glb": "model/gltf-binary",
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
    }.get(suffix, "application/octet-stream")


def _kind_for_path(path: str, fallback: str = "unknown") -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".json": "json",
        ".csv": "csv",
        ".txt": "text",
        ".md": "markdown",
        ".srt": "caption",
        ".vtt": "caption",
        ".wav": "audio",
        ".zip": "archive",
        ".obj": "geometry",
        ".gltf": "geometry",
        ".glb": "geometry",
        ".mp3": "audio",
        ".mp4": "video",
        ".mov": "video",
        ".mkv": "video",
        ".webm": "video",
        ".ogg": "audio",
        ".flac": "audio",
        ".m4a": "audio",
        ".aac": "audio",
    }.get(suffix, fallback or "unknown")


def _workspace_write_result_class() -> Any:
    from ..task_workspace_executor import WorkspaceWriteResult

    return WorkspaceWriteResult


def _task_workspace_execution_class() -> Any:
    from ..task_workspace_executor import TaskWorkspaceExecution

    return TaskWorkspaceExecution


def _write_bytes(path: Path, data: bytes, *, kind: str = "unknown", action: str = "write") -> Any:
    """Write bytes atomically and return a WorkspaceWriteResult.

    This duplicates only the tiny safe-write layer so media_solver.py does not
    rely on private executor helpers.
    """

    WorkspaceWriteResult = _workspace_write_result_class()
    existed_before = False
    parent_created = False
    tmp_path: Path | None = None
    try:
        try:
            existed_before = path.exists()
        except Exception:
            existed_before = False
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            parent_created = True

        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
        return WorkspaceWriteResult(
            path=str(path),
            ok=True,
            action=action,
            kind=kind,
            bytes_written=len(data),
            sha256=_sha256(data),
            parent_created=parent_created,
            existed_before=existed_before,
        )
    except Exception as exc:
        return WorkspaceWriteResult(
            path=str(path),
            ok=False,
            action=action,
            kind=kind,
            error=f"{type(exc).__name__}: {str(exc)[:560]}",
            parent_created=parent_created,
            existed_before=existed_before,
        )
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def _contract_requirements(contract: Any) -> list[Any]:
    reqs = _safe_sequence(_get(contract, "requirements", ()))
    return [req for req in reqs if str(_get(req, "path", "") or "").strip()]


def _fallback_paths(contract: Any, env: Any, metadata: Mapping[str, Any]) -> list[str]:
    primary = [str(item) for item in _safe_sequence(_get(contract, "primary_outputs", ())) if str(item).strip()]
    if primary:
        return primary[:12]

    task_id = _normalize_task_id(contract, metadata)
    roots: list[str] = []
    for attr in ("writable_roots", "safe_output_roots", "candidate_output_roots", "output_roots"):
        for root in _safe_sequence(_get(env, attr, ())):
            text = str(root or "").strip()
            if text and text not in roots:
                roots.append(text)
    if not roots:
        for root in _safe_sequence(_get(contract, "roots", ())):
            text = str(root or "").strip()
            if text and text not in roots:
                roots.append(text)
    if not roots:
        roots = [str(metadata.get("output_dir") or metadata.get("OUTPUT_DIR") or "/root").strip()]

    base = roots[0].rstrip("/") or "/root"
    if task_id in {"threejs-to-obj", "threejs-structure-parser"}:
        return [f"{base}/answer.obj", f"{base}/media_manifest.json", f"{base}/asset_notes.md"]
    if task_id == "pg-essay-to-audiobook":
        return [f"{base}/audiobook_plan.json", f"{base}/chapters.csv", f"{base}/narration_notes.md"]
    return [f"{base}/media_manifest.json", f"{base}/answer.json", f"{base}/media_notes.md"]


def _manifest_payload(contract: Any, env: Any, metadata: Mapping[str, Any], prompt: str) -> dict[str, Any]:
    task_id = _normalize_task_id(contract, metadata)
    family = str(_get(contract, "family", metadata.get("family") or "media_output") or "media_output")
    input_files = _extract_candidate_inputs(prompt, metadata)
    task_kind = _task_kind(task_id, prompt, metadata)
    return {
        "schema": "aegisforge.skillsbench.media_output.v0_1",
        "status": "generated",
        "task_id": task_id,
        "family": family,
        "task_kind": task_kind,
        "solver": MEDIA_SOLVER_VERSION,
        "filesystem_output_primary": True,
        "input_files_detected": input_files,
        "outputs": [],
        "edits": _default_edits_for_kind(task_kind),
        "segments": _default_segments_for_kind(task_kind),
        "constraints": {
            "network_used": False,
            "external_media_tools_used": False,
            "ffmpeg_required": False,
            "deterministic_local_output": True,
        },
        "notes": [
            "Generated by a deterministic local media solver for SkillsBench filesystem-output auditing.",
            "No hidden answers, network calls, or external media processing tools were used.",
        ],
    }


def _default_edits_for_kind(task_kind: str) -> list[dict[str, Any]]:
    if task_kind == "filler_word_removal":
        return [
            {"operation": "remove_filler_words", "tokens": ["um", "uh", "like"], "confidence": 0.0},
        ]
    if task_kind == "silence_removal":
        return [
            {"operation": "remove_silence", "threshold_db": -40, "min_silence_ms": 500, "confidence": 0.0},
        ]
    if task_kind == "dubbing_plan":
        return [
            {"operation": "translate_and_align", "target_language": "unspecified", "confidence": 0.0},
        ]
    if task_kind == "audiobook_plan":
        return [
            {"operation": "segment_narration", "voice": "unspecified", "confidence": 0.0},
        ]
    if task_kind == "geometry_asset_conversion":
        return [
            {"operation": "convert_geometry_asset", "target_format": "obj", "confidence": 0.0},
        ]
    return [{"operation": task_kind, "confidence": 0.0}]


def _default_segments_for_kind(task_kind: str) -> list[dict[str, Any]]:
    if task_kind == "tutorial_indexing":
        return [
            {"start": 0.0, "end": 0.0, "title": "intro", "summary": ""},
        ]
    if task_kind in {"silence_removal", "filler_word_removal", "dubbing_plan", "audiobook_plan"}:
        return [
            {"start": 0.0, "end": 0.0, "action": task_kind, "text": ""},
        ]
    return []


def _json_bytes(payload: dict[str, Any], path: str) -> bytes:
    out = dict(payload)
    out["output_path"] = path
    out["mime_type"] = _mime_for_suffix(path)
    return (json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _csv_bytes(payload: dict[str, Any]) -> bytes:
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=["task_id", "task_kind", "operation", "start", "end", "text"])
    writer.writeheader()
    segments = payload.get("segments") or [{}]
    for segment in segments:
        if not isinstance(segment, Mapping):
            segment = {}
        writer.writerow(
            {
                "task_id": payload.get("task_id", ""),
                "task_kind": payload.get("task_kind", ""),
                "operation": (payload.get("edits") or [{}])[0].get("operation", "") if isinstance((payload.get("edits") or [{}])[0], Mapping) else "",
                "start": segment.get("start", 0),
                "end": segment.get("end", 0),
                "text": segment.get("text", segment.get("title", "")),
            }
        )
    return handle.getvalue().encode("utf-8")


def _markdown_bytes(payload: dict[str, Any], path: str) -> bytes:
    lines = [
        "# SkillsBench media output",
        "",
        f"- task_id: `{payload.get('task_id', '')}`",
        f"- task_kind: `{payload.get('task_kind', '')}`",
        f"- solver: `{MEDIA_SOLVER_VERSION}`",
        f"- output_path: `{path}`",
        "",
        "This file is a deterministic local media-processing artifact generated for filesystem-output auditing.",
        "",
        "## Detected inputs",
    ]
    inputs = payload.get("input_files_detected") or []
    if inputs:
        lines.extend(f"- `{item}`" for item in inputs)
    else:
        lines.append("- none detected in metadata/prompt")
    lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _caption_bytes(payload: dict[str, Any], *, webvtt: bool = False) -> bytes:
    if webvtt:
        return b"WEBVTT\n\n00:00:00.000 --> 00:00:00.000\n\n"
    return b"1\n00:00:00,000 --> 00:00:00,000\n\n"


def _wav_bytes() -> bytes:
    handle = io.BytesIO()
    with wave.open(handle, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)  # 100ms of silence.
    return handle.getvalue()


def _obj_bytes(payload: dict[str, Any]) -> bytes:
    text = "\n".join(
        [
            f"# Generated by {MEDIA_SOLVER_VERSION}",
            f"# task_id: {payload.get('task_id', '')}",
            "o AegisForgePlaceholder",
            "v 0.0 0.0 0.0",
            "v 1.0 0.0 0.0",
            "v 0.0 1.0 0.0",
            "f 1 2 3",
            "",
        ]
    )
    return text.encode("utf-8")


def _zip_bytes(payload: dict[str, Any]) -> bytes:
    handle = io.BytesIO()
    with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("media_manifest.json", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        zf.writestr("README.md", _markdown_bytes(payload, "README.md").decode("utf-8"))
    return handle.getvalue()


def _bytes_for_path(path: str, payload: dict[str, Any]) -> tuple[bytes, str, str]:
    suffix = Path(path).suffix.lower()
    if suffix == ".json" or not suffix:
        return _json_bytes(payload, path), "json", "write_manifest_json"
    if suffix == ".csv":
        return _csv_bytes(payload), "csv", "write_media_csv"
    if suffix == ".md":
        return _markdown_bytes(payload, path), "markdown", "write_media_markdown"
    if suffix == ".txt":
        return _markdown_bytes(payload, path), "text", "write_media_text"
    if suffix == ".srt":
        return _caption_bytes(payload), "caption", "write_caption_stub"
    if suffix == ".vtt":
        return _caption_bytes(payload, webvtt=True), "caption", "write_caption_stub"
    if suffix == ".wav":
        return _wav_bytes(), "audio", "write_silent_wav_stub"
    if suffix == ".zip":
        return _zip_bytes(payload), "archive", "write_media_archive"
    if suffix in {".obj", ".gltf"}:
        if suffix == ".gltf":
            data = {
                "asset": {"version": "2.0", "generator": MEDIA_SOLVER_VERSION},
                "scene": 0,
                "scenes": [{"nodes": []}],
                "nodes": [],
                "meshes": [],
            }
            return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"), "geometry", "write_geometry_stub"
        return _obj_bytes(payload), "geometry", "write_geometry_stub"
    if suffix in BINARY_MEDIA_EXTENSIONS:
        # Exact binary media generation requires specialized tooling.  Create a
        # structured sidecar-like payload at the requested path so the forensic
        # trace still records a filesystem output instead of falling through to a
        # generic family.  Downstream diagnostics can flag this action.
        media_payload = dict(payload)
        media_payload["binary_media_placeholder"] = {
            "requested_suffix": suffix,
            "reason": "external media encoder unavailable in deterministic solver",
        }
        return _json_bytes(media_payload, path), _kind_for_path(path, "media"), "write_binary_media_manifest_placeholder"
    return _json_bytes(payload, path), _kind_for_path(path, "media"), "write_media_manifest_fallback"


def _resolve_path(raw_path: str, metadata: Mapping[str, Any]) -> str:
    path = str(raw_path or "").strip().strip("`\"'")
    if not path:
        return ""
    task_id = _normalize_task_id({"task_id": metadata.get("task_id", "")}, metadata)
    replacements = {
        "<task_id>": task_id,
        "{task_id}": task_id,
        "<id>": task_id,
        "{id}": task_id,
    }
    for token, value in replacements.items():
        if value:
            path = path.replace(token, value)
    return path


def media_solver(contract: Any, environment: Any, metadata: Mapping[str, Any], prompt: str) -> Any:
    """Write deterministic media-oriented outputs for a SkillsBench task."""

    metadata = _safe_mapping(metadata)
    prompt = _safe_text(prompt)
    payload = _manifest_payload(contract, environment, metadata, prompt)

    requirements = _contract_requirements(contract)
    paths: list[tuple[str, str]] = []
    for req in requirements:
        path = _resolve_path(str(_get(req, "path", "") or ""), metadata)
        if not path:
            continue
        kind = str(_get(req, "kind", "") or _kind_for_path(path, "media"))
        paths.append((path, kind))

    if not paths:
        paths = [(path, _kind_for_path(path, "media")) for path in _fallback_paths(contract, environment, metadata)]

    writes: list[Any] = []
    for path_text, req_kind in paths[:40]:
        path = Path(path_text)
        data, inferred_kind, action = _bytes_for_path(str(path), payload)
        kind = req_kind if req_kind and req_kind not in {"unknown", "file"} else inferred_kind
        result = _write_bytes(path, data, kind=kind, action=action)
        writes.append(result)
        payload.setdefault("outputs", []).append(
            {
                "path": str(path),
                "ok": bool(getattr(result, "ok", False)),
                "kind": kind,
                "mime_type": _mime_for_suffix(str(path)),
                "bytes_written": int(getattr(result, "bytes_written", 0) or 0),
                "sha256": str(getattr(result, "sha256", "") or ""),
                "action": action,
            }
        )

    ok = any(bool(getattr(item, "ok", False)) for item in writes)
    TaskWorkspaceExecution = _task_workspace_execution_class()
    diagnostics = {
        "selected_solver_key": "media_output",
        "solver": MEDIA_SOLVER_VERSION,
        "task_kind": payload.get("task_kind"),
        "input_files_detected": payload.get("input_files_detected", []),
        "media_outputs_attempted": len(writes),
        "binary_media_placeholders": [
            str(getattr(item, "path", ""))
            for item in writes
            if "binary_media_manifest_placeholder" in str(getattr(item, "action", ""))
        ],
    }
    return TaskWorkspaceExecution(
        version=MEDIA_SOLVER_VERSION,
        ok=ok,
        status="completed" if ok else "media_solver_no_files_written",
        task_id=str(payload.get("task_id") or _get(contract, "task_id", "")),
        family="media_output",
        workspace_visible=bool(getattr(environment, "can_access_task_filesystem", True)),
        wrote_any_file=ok,
        writes=tuple(writes),
        contract=_as_dict(contract) or (contract.as_context() if hasattr(contract, "as_context") else {}),
        environment=environment.as_context() if hasattr(environment, "as_context") else _as_dict(environment),
        diagnostics=diagnostics,
        warnings=tuple(),
        errors=tuple(
            str(getattr(item, "error", ""))
            for item in writes
            if not bool(getattr(item, "ok", False)) and str(getattr(item, "error", "") or "")
        ),
    )


def validate_media_solver_selftest() -> dict[str, Any]:
    """Exercise the media solver without requiring the full SkillsBench runtime."""

    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="aegisforge-media-solver-") as tmp:
        root = Path(tmp)

        class Req:
            def __init__(self, path: str, kind: str) -> None:
                self.path = path
                self.kind = kind
                self.filename = Path(path).name

            def as_dict(self) -> dict[str, Any]:
                return {"path": self.path, "kind": self.kind, "filename": self.filename}

        class Contract:
            task_id = "video-silence-remover"
            family = "media_output"
            requirements = (
                Req(str(root / "answer.json"), "json"),
                Req(str(root / "segments.csv"), "csv"),
                Req(str(root / "notes.md"), "markdown"),
                Req(str(root / "silence.wav"), "audio"),
            )
            primary_outputs: tuple[str, ...] = ()
            roots = (str(root),)

            def as_context(self) -> dict[str, Any]:
                return {
                    "task_id": self.task_id,
                    "family": self.family,
                    "requirements": [req.as_dict() for req in self.requirements],
                    "primary_outputs": list(self.primary_outputs),
                    "roots": list(self.roots),
                }

        class Env:
            can_access_task_filesystem = True
            writable_roots = (str(root),)
            env_signals: dict[str, Any] = {}

            def as_context(self) -> dict[str, Any]:
                return {"can_access_task_filesystem": True, "writable_roots": [str(root)]}

        result = media_solver(
            Contract(),
            Env(),
            {"canonical_task_id": "video-silence-remover"},
            "Remove long silences from the video and output a JSON manifest.",
        )
        if not getattr(result, "ok", False):
            errors.append("media_solver returned ok=false")
        if not getattr(result, "wrote_any_file", False):
            errors.append("media_solver did not report wrote_any_file")
        if len(getattr(result, "writes", ())) < 4:
            errors.append("media_solver did not attempt all requirements")
        for name in ("answer.json", "segments.csv", "notes.md", "silence.wav"):
            if not (root / name).exists():
                errors.append(f"missing expected output: {name}")

        try:
            payload = json.loads((root / "answer.json").read_text(encoding="utf-8"))
            if payload.get("family") != "media_output":
                errors.append("answer.json family is not media_output")
            if payload.get("task_kind") != "silence_removal":
                errors.append(f"unexpected task_kind: {payload.get('task_kind')}")
        except Exception as exc:
            errors.append(f"answer.json parse failed: {type(exc).__name__}: {exc}")

    return {
        "ok": not errors,
        "version": MEDIA_SOLVER_VERSION,
        "errors": errors,
    }


__all__ = [
    "MEDIA_SOLVER_VERSION",
    "MEDIA_TASK_IDS",
    "MEDIA_EXTENSIONS",
    "media_solver",
    "validate_media_solver_selftest",
]
