"""Offline tests for Nexus Artifact Manifest; never runs Nebius or Docker."""

from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
import os

import pytest

from aegisforge.nexus.proof.artifact_manifest import (
    NexusArtifactManifest, NexusArtifactManifestError, ArtifactManifestLimits,
    MANIFEST_SCHEMA,
)
from aegisforge.nexus.proof.proof_graph import NexusProofGraph
from aegisforge.nexus.proof.provenance import NexusProvenanceLedger


@pytest.fixture
def environment(tmp_path):
    base = tmp_path / "workspace"
    base.mkdir()
    (base / "src").mkdir()
    # Raw bytes make the fixture deterministic on Windows (CRLF) and POSIX (LF).
    # The manifest intentionally hashes raw bytes without newline normalization.
    (base / "src" / "module.py").write_bytes(b"value = 1\n")
    (base / "src" / "other.py").write_bytes(b"value = 2\n")
    manifest = NexusArtifactManifest("colossus.audit", base)
    return base, manifest


def register(manifest, name="alpha", path="src/module.py", kind="source"):
    return manifest.register_file(name, path, kind=kind)


def rehash(snapshot):
    body = {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
    snapshot["snapshot_sha256"] = sha256(json.dumps(body, sort_keys=True,
        separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def rehash_record(snapshot, idx=0):
    row = snapshot["records"][idx]
    body = {k: v for k, v in row.items() if k != "record_sha256"}
    row["record_sha256"] = sha256(json.dumps(body, sort_keys=True,
        separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    if idx + 1 == len(snapshot["records"]):
        snapshot["terminal_sha256"] = row["record_sha256"]
    rehash(snapshot)


def test_empty_snapshot(environment):
    _, m = environment
    s = m.snapshot()
    assert s["record_count"] == 0 and NexusArtifactManifest.verify_snapshot(s)


def test_default_flags(environment):
    _, m = environment
    s = m.snapshot()
    assert s["integrity_only"] and not s["authenticity_verified"]


def test_invalid_mission(tmp_path):
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest("private mission", tmp_path)


def test_nonexistent_root(tmp_path):
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest("colossus.audit", tmp_path / "absent")


def test_invalid_limits():
    with pytest.raises(NexusArtifactManifestError):
        ArtifactManifestLimits(max_file_bytes=0)


def test_bool_limits():
    with pytest.raises(NexusArtifactManifestError):
        ArtifactManifestLimits(max_records=True)


def test_root_directory_only(tmp_path):
    file = tmp_path / "not-dir"
    file.write_text("abc")
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest("colossus.audit", file)


def test_register_file_actual_digest(environment):
    _, m = environment
    row = register(m)
    assert row.content_sha256 == sha256(b"value = 1\n").hexdigest()
    assert row.size_bytes == len(b"value = 1\n")


def test_origin_default_is_digest(environment):
    _, m = environment
    assert len(register(m).origin_id_sha256) == 64


def test_origin_validation(environment):
    _, m = environment
    with pytest.raises(NexusArtifactManifestError):
        m.register_file("a", "src/module.py", kind="source", origin_id_sha256="not-a-hash")


def test_invalid_kind(environment):
    _, m = environment
    with pytest.raises(NexusArtifactManifestError):
        m.register_file("a", "src/module.py", kind="super-secret-custom")


def test_invalid_origin_type(environment):
    _, m = environment
    with pytest.raises(NexusArtifactManifestError):
        m.register_file("a", "src/module.py", kind="source", origin_type="not-a-source")


def test_invalid_artifact_id(environment):
    _, m = environment
    with pytest.raises(NexusArtifactManifestError):
        register(m, name="src/module.py")


def test_first_version(environment):
    _, m = environment
    row = register(m)
    assert row.version == 1 and row.sequence == 1 and row.previous_version_sha256 is None


def test_verify_match(environment):
    _, m = environment
    register(m)
    assert m.verify_file("alpha")["matched"] is True


def test_verify_mutation(environment):
    root, m = environment
    register(m)
    (root / "src" / "module.py").write_text("value = 9\n")
    assert m.verify_file("alpha")["matched"] is False


def test_verify_rejects_wrong_path(environment):
    _, m = environment
    register(m)
    assert m.verify_file("alpha", "src/other.py")["matched"] is False


def test_version_on_change(environment):
    root, m = environment
    first = register(m)
    (root / "src" / "module.py").write_text("value = 9\n")
    second = register(m)
    assert second.version == 2 and second.previous_version_sha256 == first.record_sha256
    assert m.verify_file("alpha")["matched"] is True
    assert m.verify_file("alpha", version=1)["matched"] is False


def test_unchanged_fails_no_state_change(environment):
    _, m = environment
    register(m)
    with pytest.raises(NexusArtifactManifestError):
        register(m)
    assert m.snapshot()["record_count"] == 1


def test_history(environment):
    root, m = environment
    register(m)
    (root / "src" / "module.py").write_text("value = 3\n")
    register(m)
    assert [x["version"] for x in m.history("alpha")] == [1, 2]


def test_unknown_history(environment):
    _, m = environment
    assert m.history("absent") == ()


def test_unknown_verify(environment):
    _, m = environment
    with pytest.raises(NexusArtifactManifestError):
        m.verify_file("absent")


def test_invalid_verify_version(environment):
    _, m = environment
    register(m)
    with pytest.raises(NexusArtifactManifestError):
        m.verify_file("alpha", version=True)


@pytest.mark.parametrize("unsafe", [
    "../other.py", "src/../module.py", "src//module.py", "src/./module.py",
    "/tmp/file.py", "C:\\Windows\\test.py", "\\\\server\\share\\f",
    "src/.env", ".git/config", "src/id_rsa", "src/private.pem",
    "src/.env.local", "src/credentials.json", "src/token.json",
    "src/secrets.json", "src/module.py:fork",
])
def test_unsafe_paths(environment, unsafe):
    _, m = environment
    with pytest.raises(NexusArtifactManifestError):
        register(m, path=unsafe)


def test_parent_symlink(environment, tmp_path):
    root, m = environment
    other = tmp_path / "other"
    other.mkdir()
    (other / "other.py").write_text("x=1")
    try:
        (root / "linked").symlink_to(other, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this Windows account")
    with pytest.raises(NexusArtifactManifestError):
        register(m, path="linked/other.py")


def test_file_symlink(environment):
    root, m = environment
    try:
        (root / "src" / "alias.py").symlink_to(root / "src" / "module.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this Windows account")
    with pytest.raises(NexusArtifactManifestError):
        register(m, path="src/alias.py")


def test_missing_file(environment):
    _, m = environment
    with pytest.raises(NexusArtifactManifestError):
        register(m, path="src/missing.py")


def test_directory_rejected(environment):
    _, m = environment
    with pytest.raises(NexusArtifactManifestError):
        register(m, path="src")


def test_large_file_rejected(environment):
    root, _ = environment
    m = NexusArtifactManifest("colossus.audit", root, limits=ArtifactManifestLimits(max_file_bytes=10))
    (root / "src" / "huge.py").write_bytes(b"x" * 11)
    with pytest.raises(NexusArtifactManifestError):
        register(m, path="src/huge.py")


def test_max_records(environment):
    root, _ = environment
    m = NexusArtifactManifest("colossus.audit", root, limits=ArtifactManifestLimits(max_records=1))
    register(m)
    with pytest.raises(NexusArtifactManifestError):
        register(m, name="other", path="src/other.py")


def test_max_versions(environment):
    root, _ = environment
    m = NexusArtifactManifest("colossus.audit", root, limits=ArtifactManifestLimits(max_versions_per_artifact=1))
    register(m)
    (root / "src" / "module.py").write_text("another version")
    with pytest.raises(NexusArtifactManifestError):
        register(m)


def test_snapshot_no_paths_or_raw_content(environment):
    _, m = environment
    register(m)
    payload = json.dumps(m.snapshot())
    assert "src/module.py" not in payload and "value = 1" not in payload
    assert "alpha" not in payload


def test_snapshot_rehash_detected(environment):
    _, m = environment
    register(m)
    s = m.snapshot()
    s["records"][0]["size_bytes"] = 99
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.verify_snapshot(s)


def test_bad_integrity_flags_rejected(environment):
    _, m = environment
    s = m.snapshot()
    s["authenticity_verified"] = True
    rehash(s)
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.verify_snapshot(s)


def test_rehashed_but_invalid_version_rejected(environment):
    _, m = environment
    register(m)
    s = m.snapshot()
    s["records"][0]["version"] = 2
    rehash_record(s)
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.verify_snapshot(s)


def test_rehashed_but_invalid_record_field_rejected(environment):
    _, m = environment
    register(m)
    s = m.snapshot()
    s["records"][0]["private_path"] = "secret"
    rehash_record(s)
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.verify_snapshot(s)


def test_rehashed_but_bool_size_rejected(environment):
    _, m = environment
    register(m)
    s = m.snapshot()
    s["records"][0]["size_bytes"] = True
    rehash_record(s)
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.verify_snapshot(s)


def test_chain_cannot_be_reordered(environment):
    _, m = environment
    register(m)
    register(m, name="beta", path="src/other.py")
    s = m.snapshot()
    s["records"].reverse()
    rehash(s)
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.verify_snapshot(s)


def test_snapshot_restore_requires_path(environment):
    root, m = environment
    register(m)
    restored = NexusArtifactManifest.from_snapshot(m.snapshot(), root)
    assert restored.snapshot() == m.snapshot()
    with pytest.raises(NexusArtifactManifestError):
        restored.verify_file("alpha")
    assert restored.verify_file("alpha", "src/module.py")["matched"]


def test_restored_manifest_limit(environment):
    root, m = environment
    register(m)
    register(m, name="beta", path="src/other.py")
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.from_snapshot(m.snapshot(), root, limits=ArtifactManifestLimits(max_records=1))


def test_graph_bridge(environment):
    root, m = environment
    register(m)
    (root / "src" / "module.py").write_text("value = 4")
    register(m)
    graph = m.to_proof_graph()
    s = graph.snapshot()
    assert s["node_count"] == 3 and s["edge_count"] == 1
    assert NexusProofGraph.verify_snapshot(s)


def test_provenance_bridge(environment):
    root, m = environment
    register(m)
    (root / "src" / "module.py").write_text("value = 4")
    register(m)
    ledger = m.to_provenance_ledger()
    s = ledger.snapshot()
    assert s["record_count"] == 2
    assert NexusProvenanceLedger.verify_snapshot(s)
    assert s["records"][1]["parent_record_ids"] == [s["records"][0]["record_sha256"]]


def test_no_write_to_original_files(environment):
    root, m = environment
    target = root / "src" / "module.py"
    before = target.read_bytes()
    register(m)
    m.verify_file("alpha")
    m.to_proof_graph()
    m.to_provenance_ledger()
    assert target.read_bytes() == before


def test_snapshot_has_correct_schema(environment):
    _, m = environment
    assert m.snapshot()["schema"] == MANIFEST_SCHEMA


def test_invalid_field_with_self_consistent_hash(environment):
    _, m = environment
    register(m)
    s = m.snapshot()
    s["records"][0]["origin_type"] = "fabricated"
    rehash_record(s)
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.verify_snapshot(s)


def test_stricter_snapshot_byte_limit(environment):
    _, m = environment
    register(m)
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.verify_snapshot(m.snapshot(), max_bytes=10)


def test_invalid_snapshot_byte_limit(environment):
    _, m = environment
    with pytest.raises(NexusArtifactManifestError):
        NexusArtifactManifest.verify_snapshot(m.snapshot(), max_bytes=True)


def test_version_shared_scope(environment):
    _, m = environment
    one = register(m, name="alpha", path="src/module.py")
    two = register(m, name="beta", path="src/module.py")
    assert one.artifact_id_sha256 != two.artifact_id_sha256
    assert two.version == 1


def test_backward_slash_windows_path(environment):
    _, m = environment
    row = register(m, path="src\\module.py")
    assert row.version == 1


def test_concurrent_snapshot_integrity(environment):
    _, m = environment
    register(m)
    assert NexusArtifactManifest.verify_snapshot(m.snapshot())
