from __future__ import annotations

import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


sys.path.insert(0, str(_repo_root() / "src"))


def test_failure_taxonomy_exposes_skillsbench_channel_labels() -> None:
    from aegisforge.telemetry.failure_taxonomy import FailureLabel

    labels = {item.value for item in FailureLabel}
    expected = {
        "scoring_channel_mismatch",
        "artifact_refs_dropped",
        "filesystem_not_visible",
        "filesystem_outputs_not_scored",
        "official_result_zeroed",
        "result_shape_mismatch",
        "task_identity_unresolved",
        "score_eligible_inconsistent",
        "worker_result_timeout",
    }

    missing = sorted(expected.difference(labels))
    assert not missing, missing


def test_failure_taxonomy_classifies_skillsbench_forensic_messages() -> None:
    from aegisforge.telemetry.failure_taxonomy import FailureTaxonomy

    taxonomy = FailureTaxonomy()

    cases = {
        "artifact_refs_dropped": ("artifact_refs_dropped", "AegisForge emitted refs but official artifact_refs is empty"),
        "filesystem_outputs_not_scored": ("filesystem_outputs_not_scored", "workspace wrote answer.json but reward is 0.0"),
        "result_shape_mismatch": ("result_shape_mismatch", "legacy flat result shape rejected by validator"),
        "task_identity_unresolved": ("task_identity_unresolved", "request task_id was UUID and canonical task id was missing"),
        "worker_result_timeout": ("worker_timeout", "worker timed out while waiting for result"),
    }

    for expected, (code, message) in cases.items():
        actual = taxonomy.classify(error_code=code, message=message)
        assert actual.value == expected
