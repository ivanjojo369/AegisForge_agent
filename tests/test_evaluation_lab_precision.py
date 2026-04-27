from __future__ import annotations

import pytest

from integrations.openenv.envs.omnibench_aegis_env.evaluation_lab.scanner import (
    _scan_file,
    _secret_category_for_line,
    mask_evidence,
    summarize_repo_shape,
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


@pytest.mark.parametrize(
    "line",
    [
        "max_tokens=16000",
        "self.total_tokens = 0",
        "prompt_tokens = 10",
        "completion_tokens = 20",
        "pred_tokens = split(pred)",
        "gold_tokens = split(gold)",
        "token_budget = 10000",
        "std::string token = ReadToken();",
        "std::vector<std::string> tokens = absl::StrSplit(line, ' ');",
        "# Life tokens=8",
        "# Info tokens=3",
    ],
)
def test_secret_classifier_ignores_non_secret_token_counters(line: str) -> None:
    assert _secret_category_for_line(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "OPENAI_API_KEY=${OPENAI_API_KEY}",
        "password=${{ secrets.GITHUB_TOKEN }}",
        "GHCR_TOKEN: ${{ secrets.GHCR_TOKEN }}",
        "API_KEY=<your-api-key>",
        "api_key: Optional[str] = None",
        "self.api_key = api_key or settings.llm.api_key",
    ],
)
def test_secret_classifier_treats_placeholders_and_references_as_low(line: str) -> None:
    assert _secret_category_for_line(line, rel="README.md") == "secret_placeholder_reference"


def test_secret_classifier_flags_hardcoded_high_entropy_secret() -> None:
    line = "OPENAI_API_KEY='sk-proj-1234567890abcdef1234567890'"
    assert _secret_category_for_line(line, rel="src/config.py") == "secret_like_pattern_masked"
    assert "sk-proj" not in mask_evidence(line)


def test_scan_file_marks_a2a_surface_without_flagging_token_metrics() -> None:
    findings = _scan_file(
        "src/agent.py",
        "from a2a.types import DataPart, FilePart\nmax_tokens=16000\ntoken_budget=10000\n",
    )
    categories = [item.category for item in findings]
    assert "agent_surface_risk" in categories
    assert "secret_like_pattern_masked" not in categories
    assert "secret_placeholder_reference" not in categories


def test_summarize_repo_shape_normalizes_windows_and_unix_paths() -> None:
    shape = summarize_repo_shape([
        "src\\agent.py",
        "tests\\test_agent.py",
        ".github/workflows/test.yml",
        "Dockerfile.green",
        "README.md",
        "amber/amber-manifest-green.json5",
    ])
    assert shape["has_src"] is True
    assert shape["has_tests"] is True
    assert shape["has_github_actions"] is True
    assert shape["has_dockerfile"] is True
    assert shape["has_readme"] is True
    assert shape["has_amber_manifest"] is True
