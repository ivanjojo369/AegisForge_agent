from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class FailureLabel(str, Enum):
    NONE = "none"
    FORMAT = "format"
    POLICY = "policy"
    BUDGET = "budget"
    ROUTING = "routing"
    TOOLING = "tooling"
    RECOVERY = "recovery"
    UNKNOWN = "unknown"

@dataclass(frozen=True, slots=True)
class FailureTaxonomy:
    def classify(self, *, error_code: str | None = None, message: str | None = None) -> FailureLabel:
        code = (error_code or "").lower()
        text = (message or "").lower()
        if not code and not text:
            return FailureLabel.NONE
        if "format" in code or "schema" in text or "json" in text:
            return FailureLabel.FORMAT
        if "policy" in code or "unsafe" in text or "blocked" in text:
            return FailureLabel.POLICY
        if "budget" in code or "token" in text or "limit" in text:
            return FailureLabel.BUDGET
        if "route" in code or "adapter" in text:
            return FailureLabel.ROUTING
        if "tool" in code or "lookup" in text or "probe" in text:
            return FailureLabel.TOOLING
        if "retry" in text or "fallback" in text or "recovery" in code:
            return FailureLabel.RECOVERY
        return FailureLabel.UNKNOWN
