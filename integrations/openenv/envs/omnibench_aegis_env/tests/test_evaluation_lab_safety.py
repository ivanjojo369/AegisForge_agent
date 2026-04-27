from __future__ import annotations

import pytest

from integrations.openenv.envs.omnibench_aegis_env.evaluation_lab.scanner import (
    mask_evidence,
    validate_public_github_url,
)


def test_validate_public_github_url_accepts_public_github_repo() -> None:
    assert (
        validate_public_github_url("https://github.com/RDI-Foundation/cybergym-green")
        == "https://github.com/RDI-Foundation/cybergym-green.git"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/RDI-Foundation/cybergym-green",
        "https://example.com/RDI-Foundation/cybergym-green",
        "https://github.com/RDI-Foundation",
        "file:///tmp/repo",
    ],
)
def test_validate_public_github_url_rejects_unsafe_or_unsupported_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_public_github_url(url)


def test_mask_evidence_redacts_secret_like_values() -> None:
    masked = mask_evidence("API_KEY='super-secret-value'")
    assert "super-secret-value" not in masked
    assert "<MASKED>" in masked
