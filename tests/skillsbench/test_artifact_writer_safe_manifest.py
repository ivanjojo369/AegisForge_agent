from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


sys.path.insert(0, str(_repo_root() / "src"))


def test_artifact_writer_writes_bytes_hashes_and_manifest(tmp_path: Path) -> None:
    from aegisforge.telemetry.artifact_writer import ArtifactWriter

    writer = ArtifactWriter(tmp_path)
    payload = b"skillsbench forensic artifact\n"

    binary = writer.write_bytes("outputs/answer.bin", payload, kind="binary")
    text = writer.write_text("outputs/notes.md", "# Notes\n", kind="markdown")
    manifest = writer.write_manifest(
        "manifest.json",
        [binary, text],
        extra={"task_id": "dialogue-parser", "source": "pytest"},
    )

    assert Path(binary.path).exists()
    assert binary.metadata["sha256"] == hashlib.sha256(payload).hexdigest()
    assert binary.metadata["size_bytes"] == len(payload)
    assert binary.metadata["relative_path"] == "outputs/answer.bin"

    manifest_payload = json.loads(Path(manifest.path).read_text(encoding="utf-8"))
    assert manifest_payload["task_id"] == "dialogue-parser"
    assert manifest_payload["source"] == "pytest"
    assert len(manifest_payload["artifacts"]) == 2
    assert manifest_payload["artifacts"][0]["sha256"] == binary.metadata["sha256"]


def test_artifact_writer_rejects_path_escape(tmp_path: Path) -> None:
    from aegisforge.telemetry.artifact_writer import ArtifactWriter

    writer = ArtifactWriter(tmp_path)

    with pytest.raises(ValueError):
        writer.write_text("../escape.txt", "bad")

    with pytest.raises(ValueError):
        writer.write_bytes("/tmp/escape.bin", b"bad")
