from __future__ import annotations

"""Artifact writing helpers for AegisForge telemetry.

This module is intentionally small and dependency-free.  It writes deterministic
local artifacts for traces, post-mortems, and SkillsBench/AgentBeats forensic
evidence while keeping all paths constrained under a caller-provided root.

Design goals:
- preserve the original simple API: write_json(...) and write_text(...);
- add binary-safe artifact writing for generated files and manifests;
- compute stable sha256/size/mime metadata for every artifact record;
- reject absolute paths and path traversal attempts;
- use atomic replace writes so partially-written telemetry files are avoided;
- avoid network access, shell execution, secrets, or benchmark-specific logic.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
import hashlib
import json
import mimetypes
import os
import tempfile


ARTIFACT_WRITER_VERSION = "artifact_writer_v0_3_safe_manifest_hashes_2026_06_09"


JsonDict = dict[str, Any]


@dataclass(slots=True)
class ArtifactRecord:
    """Serializable description of one artifact written or registered locally."""

    name: str
    kind: str
    path: str
    metadata: JsonDict = field(default_factory=dict)

    @property
    def relative_path(self) -> str:
        return str(self.metadata.get("relative_path") or "")

    @property
    def sha256(self) -> str:
        return str(self.metadata.get("sha256") or "")

    @property
    def size_bytes(self) -> int:
        value = self.metadata.get("size_bytes", self.metadata.get("size", 0))
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def as_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "metadata": dict(self.metadata),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _guess_mime(path: Path, *, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or fallback


def _coerce_jsonable(value: Any) -> Any:
    """Best-effort JSON-compatible conversion for manifests."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _coerce_jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_jsonable(child) for child in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            return _coerce_jsonable(value.as_dict())
        except Exception:
            return str(value)
    return str(value)


class ArtifactWriter:
    """Safe deterministic artifact writer.

    All public write methods accept a *relative* path under ``root_dir``.  Passing
    an absolute path or a path with ``..`` raises ``ValueError``.  This makes the
    writer safe to use with untrusted benchmark/task names after they have been
    slugified by the caller.
    """

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, relative_path: str | Path) -> Path:
        raw = Path(relative_path)

        if raw.is_absolute():
            raise ValueError(f"artifact path must be relative, got absolute path: {relative_path}")

        if any(part in {"..", ""} for part in raw.parts):
            raise ValueError(f"artifact path may not escape artifact root: {relative_path}")

        path = (self.root_dir / raw).resolve()
        try:
            path.relative_to(self.root_dir)
        except ValueError as exc:
            raise ValueError(f"artifact path escapes artifact root: {relative_path}") from exc
        return path

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root_dir).as_posix()
        except ValueError:
            return path.name

    def _record(
        self,
        path: Path,
        *,
        kind: str,
        sha256: str,
        size_bytes: int,
        extra_metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        metadata: JsonDict = {
            "relative_path": self._relative(path),
            "size": int(size_bytes),
            "size_bytes": int(size_bytes),
            "sha256": sha256,
            "mime_type": _guess_mime(path),
            "created_at": _utc_now(),
            "writer_version": ARTIFACT_WRITER_VERSION,
        }
        if extra_metadata:
            metadata.update({str(key): _coerce_jsonable(value) for key, value in extra_metadata.items()})

        return ArtifactRecord(
            name=path.name,
            kind=str(kind or "artifact"),
            path=str(path),
            metadata=metadata,
        )

    def write_bytes(
        self,
        relative_path: str | Path,
        content: bytes | bytearray | memoryview,
        *,
        kind: str = "binary",
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        """Write bytes atomically and return a content-addressed record."""

        path = self._safe_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = bytes(content)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)

        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(str(tmp_path), str(path))
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

        return self._record(
            path,
            kind=kind,
            sha256=_sha256_bytes(data),
            size_bytes=len(data),
            extra_metadata=metadata,
        )

    def write_text(
        self,
        relative_path: str | Path,
        content: str,
        *,
        kind: str = "text",
        encoding: str = "utf-8",
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        """Write a UTF text artifact."""

        text = str(content)
        record = self.write_bytes(
            relative_path,
            text.encode(encoding),
            kind=kind,
            metadata={"encoding": encoding, **dict(metadata or {})},
        )
        record.metadata["char_count"] = len(text)
        return record

    def write_json(
        self,
        relative_path: str | Path,
        payload: Mapping[str, Any],
        *,
        indent: int = 2,
        ensure_ascii: bool = False,
        sort_keys: bool = True,
        trailing_newline: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        """Write a JSON object with stable formatting and record its keys."""

        data = _coerce_jsonable(dict(payload))
        text = json.dumps(
            data,
            indent=indent,
            ensure_ascii=ensure_ascii,
            sort_keys=sort_keys,
        )
        if trailing_newline:
            text += "\n"

        record = self.write_text(
            relative_path,
            text,
            kind="json",
            metadata={"json_indent": indent, **dict(metadata or {})},
        )
        record.metadata["keys"] = sorted(str(key) for key in data.keys())
        return record

    def register_existing_path(
        self,
        path: str | Path,
        *,
        kind: str = "file",
        metadata: Mapping[str, Any] | None = None,
        require_under_root: bool = False,
    ) -> ArtifactRecord:
        """Create an ArtifactRecord for a file that already exists.

        This is useful for SkillsBench: solvers may write evaluator-facing files
        such as ``/root/answer.json`` directly, while telemetry still needs a
        sha256/size record.  By default external absolute paths are allowed for
        registration only, not writing.
        """

        file_path = Path(path).expanduser().resolve()
        if require_under_root:
            try:
                file_path.relative_to(self.root_dir)
            except ValueError as exc:
                raise ValueError(f"registered path is outside artifact root: {path}") from exc

        base_metadata = {
            "relative_path": self._relative(file_path),
            "mime_type": _guess_mime(file_path),
            "created_at": _utc_now(),
            "writer_version": ARTIFACT_WRITER_VERSION,
            **{str(key): _coerce_jsonable(value) for key, value in dict(metadata or {}).items()},
        }

        if not file_path.exists() or not file_path.is_file():
            return ArtifactRecord(
                name=file_path.name,
                kind=kind,
                path=str(file_path),
                metadata={
                    **base_metadata,
                    "exists": False,
                    "size": 0,
                    "size_bytes": 0,
                    "sha256": "",
                },
            )

        size_bytes = file_path.stat().st_size
        return self._record(
            file_path,
            kind=kind,
            sha256=_file_sha256(file_path),
            size_bytes=size_bytes,
            extra_metadata={"exists": True, **base_metadata},
        )

    def write_manifest(
        self,
        relative_path: str | Path,
        records: Iterable[ArtifactRecord | Mapping[str, Any]],
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        """Write a manifest for a collection of artifact records."""

        artifacts: list[JsonDict] = []
        for record in records:
            if hasattr(record, "as_dict") and callable(record.as_dict):  # type: ignore[attr-defined]
                item = record.as_dict()  # type: ignore[union-attr]
            else:
                item = dict(record)  # type: ignore[arg-type]

            metadata = dict(item.get("metadata") or {})
            flattened: JsonDict = {
                "name": item.get("name", ""),
                "kind": item.get("kind", ""),
                "path": item.get("path", ""),
                **metadata,
            }
            artifacts.append(_coerce_jsonable(flattened))

        payload: JsonDict = {
            "schema": "aegisforge.telemetry.artifact_manifest.v1",
            "writer_version": ARTIFACT_WRITER_VERSION,
            "created_at": _utc_now(),
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
        }
        if extra:
            payload.update({str(key): _coerce_jsonable(value) for key, value in extra.items()})

        return self.write_json(relative_path, payload, metadata={"manifest_artifact_count": len(artifacts)})


def validate_artifact_writer_selftest() -> JsonDict:
    """Small dependency-free selftest used by local smoke checks."""

    with tempfile.TemporaryDirectory(prefix="aegisforge-artifact-writer-") as tmp:
        writer = ArtifactWriter(tmp)
        text = writer.write_text("notes/summary.md", "# Summary\n", kind="markdown")
        data = writer.write_json("data/result.json", {"ok": True, "answer": 42})
        raw = writer.write_bytes("bin/blob.bin", b"abc", kind="binary")
        manifest = writer.write_manifest(
            "manifest.json",
            [text, data, raw],
            extra={"task_id": "selftest", "source": "validate_artifact_writer_selftest"},
        )

        errors: list[str] = []
        for record in (text, data, raw, manifest):
            path = Path(record.path)
            if not path.exists():
                errors.append(f"missing file: {record.path}")
            if not record.sha256:
                errors.append(f"missing sha256: {record.name}")
            if record.size_bytes <= 0:
                errors.append(f"invalid size: {record.name}")

        try:
            writer.write_text("../escape.txt", "bad")
            errors.append("path traversal was not rejected")
        except ValueError:
            pass

        try:
            writer.write_bytes(Path(tmp) / "absolute.bin", b"bad")
            errors.append("absolute path was not rejected")
        except ValueError:
            pass

        return {
            "ok": not errors,
            "version": ARTIFACT_WRITER_VERSION,
            "errors": errors,
            "manifest_path": manifest.path,
            "artifact_count": 4,
        }
