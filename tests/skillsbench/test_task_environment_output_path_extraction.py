from __future__ import annotations

import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


sys.path.insert(0, str(_repo_root() / "src"))


def test_task_environment_extracts_full_output_paths_not_generic_roots() -> None:
    from aegisforge.adapters.skillsbench.task_environment import extract_output_path_candidates

    prompt = """
    trial_id: dialogue-parser__agentbeats__019e276a
    Write your findings to `/root/answer.json`.
    Also create /root/output/report.md and /app/workspace/solution.py.
    For build repair write /home/github/build/failed/<repo>/<id>/patch_0.diff.
    """

    candidates = extract_output_path_candidates(prompt, {})
    paths = [candidate.path for candidate in candidates]

    assert "/root/answer.json" in paths
    assert "/root/output/report.md" in paths
    assert "/app/workspace/solution.py" in paths
    assert "/home/github/build/failed/<repo>/<id>/patch_0.diff" in paths

    # The extractor must not collapse concrete output files into generic roots.
    assert "/root/" not in paths
    assert "/app/" not in paths
    assert "/home/github/build/" not in paths


def test_task_environment_selftest_recovers_canonical_identity_and_paths() -> None:
    from aegisforge.adapters.skillsbench.task_environment import validate_task_environment_selftest

    result = validate_task_environment_selftest()

    assert result["ok"], result
    assert result["canonical_task_id"] == "dialogue-parser"
    sample_paths = {item["path"] for item in result.get("sample_candidates", [])}
    assert "/root/answer.json" in sample_paths
    assert "/root/output/report.md" in sample_paths
    assert "/app/workspace/solution.py" in sample_paths
