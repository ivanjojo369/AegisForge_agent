"""Pure, auditable aggregation of specialist agent proposals.

A label of 'verified' is reserved for external evidence supplied by the host;
multiple model opinions are not independent verification.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

ROLES = frozenset({"planner", "architect", "coder", "reviewer", "verifier"})
VERDICTS = frozenset({"proposed", "accepted_for_review", "needs_revision", "inconclusive"})


@dataclass(frozen=True, slots=True)
class CouncilStatement:
    role: str
    verdict: str
    rationale: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in ROLES or self.verdict not in VERDICTS:
            raise ValueError("unknown council role/verdict")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("rationale required")
        if len(self.rationale) > 4000 or len(self.evidence_refs) > 32:
            raise ValueError("council statement too large")
        if any(type(ref) is not str or not ref.strip() or len(ref) > 256
               for ref in self.evidence_refs):
            raise ValueError("invalid evidence reference")


def summarize_council(statements: Iterable[CouncilStatement]) -> dict[str, object]:
    """Summarize *opinions*, never issue an execution decision."""
    rows = tuple(statements)
    if len(rows) > 64 or any(type(row) is not CouncilStatement for row in rows):
        raise ValueError("invalid council statements")
    by_role: dict[str, int] = {}
    for row in rows:
        by_role[row.role] = by_role.get(row.role, 0) + 1
    return {
        "schema": "aegisforge.nexus.intelligence.council.v1",
        "statement_count": len(rows), "roles_present": sorted(by_role),
        "objections": sum(row.verdict == "needs_revision" for row in rows),
        "requires_host_review": True,
        "independent_verification_established": False,
        "execution_authorized": False,
    }


__all__ = ["CouncilStatement", "summarize_council", "ROLES"]
