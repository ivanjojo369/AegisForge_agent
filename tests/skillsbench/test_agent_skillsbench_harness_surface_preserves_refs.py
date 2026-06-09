from __future__ import annotations

from pathlib import Path
import re


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_agent_does_not_drop_skillsbench_harness_artifact_surfaces() -> None:
    """The filesystem-first harness may treat artifact_refs as diagnostic only,
    but agent.py must not erase the diagnostic refs/files it just received.
    """

    source_path = _repo_root() / "src" / "aegisforge" / "agent.py"
    source = source_path.read_text(encoding="utf-8")

    assert "skillsbench_filesystem_harness_first" in source
    assert "artifact_outputs" in source
    assert "deliverables" in source

    forbidden_patterns = {
        "last artifact refs reset": r"self\._skillsbench_last_artifact_refs\s*=\s*\[\]",
        "empty artifact_refs_candidate literal": r"['\"]artifact_refs_candidate['\"]\s*:\s*\[\]",
    }
    for label, pattern in forbidden_patterns.items():
        assert not re.search(pattern, source), f"{label} found in agent.py"

    # Accept either an explicit helper or a typed/local artifact_refs_candidate surface.
    assert (
        "_skillsbench_refs_from_harness_surfaces" in source
        or "_skillsbench_collect_artifact_refs" in source
        or "_add_skillsbench_artifact_ref_candidate" in source
        or re.search(r"artifact_refs_candidate\s*[:=]", source)
    )
