"""Bounded in-memory archive of explicitly host-confirmed engineering outcomes.

No file writes, auto-learning, or claims that self-reported results are proof.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConfirmedFinding:
    key: str
    description: str
    evidence_ref: str
    status: str

    def __post_init__(self) -> None:
        if (self.status not in {"confirmed_success", "confirmed_failure"} or
            any(not isinstance(s, str) or not s.strip() or len(s) > 4096
                for s in (self.key, self.description, self.evidence_ref))):
            raise ValueError("invalid confirmed finding")


class EngineeringMemory:
    def __init__(self, *, capacity: int = 200) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 10_000:
            raise ValueError("invalid memory capacity")
        self._capacity = capacity
        self._findings: dict[str, ConfirmedFinding] = {}

    def record_confirmed(self, finding: ConfirmedFinding, *, host_verified: bool = False) -> None:
        if type(finding) is not ConfirmedFinding or host_verified is not True:
            raise ValueError("explicit host verification required")
        if finding.key not in self._findings and len(self._findings) >= self._capacity:
            raise ValueError("memory capacity reached")
        self._findings[finding.key] = finding

    def get(self, key: str) -> ConfirmedFinding | None:
        return self._findings.get(key)

    def snapshot(self) -> tuple[ConfirmedFinding, ...]:
        return tuple(self._findings[key] for key in sorted(self._findings))


__all__ = ["EngineeringMemory", "ConfirmedFinding"]
