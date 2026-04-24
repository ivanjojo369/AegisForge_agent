from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _find_repo_root() -> Path:
    """Find the repository root without assuming this test file's depth."""
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "src" / "aegisforge").exists():
            return candidate
    return here.parents[1]


REPO_ROOT = _find_repo_root()
SRC_ROOT = REPO_ROOT / "src"

for path in (REPO_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from aegisforge.strategy.role_policy import RolePolicy  # noqa: E402


def _constraints_text(constraints: object) -> str:
    if constraints is None:
        return ""
    if isinstance(constraints, str):
        return constraints
    if isinstance(constraints, (list, tuple, set)):
        return " ".join(str(item) for item in constraints)
    return str(constraints)


def test_role_policy_becomes_conservative_for_security_risk() -> None:
    decision = RolePolicy().decide(
        track="security",
        risk="high",
        task_type="analysis",
        heldout_like=True,
    )

    assert decision.role == "security_defender"
    assert decision.posture == "conservative"

    constraints_text = _constraints_text(decision.constraints).lower()
    assert "avoid brittle shortcuts" in constraints_text
    assert "memorized patterns" in constraints_text


@pytest.mark.parametrize(
    ("track", "task_type", "expected_role"),
    [
        ("agent_safety", "pii_leak_review", "security_defender"),
        ("cybersecurity", "supply_chain_review", "security_defender"),
        ("coding", "devcontainer_supply_chain_review", "environment_operator"),
    ],
)
def test_role_policy_covers_sprint3_high_risk_tracks(
    track: str,
    task_type: str,
    expected_role: str,
) -> None:
    """Sprint 3 scenarios should resolve to the repo's current role taxonomy.

    Covers:
    - WhistleBlowerWreck: Agent Safety / Pi-Bench / PII Leak
    - StaticShipScam: Cybersecurity / CyberGym / Supply Chain
    - DevContainerDoom: Coding / NetArena / Supply Chain
    """
    decision = RolePolicy().decide(
        track=track,
        risk="high",
        task_type=task_type,
        heldout_like=True,
    )

    assert decision.role == expected_role
    assert decision.posture == "conservative"

    constraints_text = _constraints_text(decision.constraints).lower()
    assert "avoid brittle shortcuts" in constraints_text
    assert "memorized patterns" in constraints_text


def test_role_policy_keeps_non_high_risk_research_less_restrictive() -> None:
    decision = RolePolicy().decide(
        track="research",
        risk="low",
        task_type="analysis",
        heldout_like=False,
    )

    assert decision.posture != "conservative"
