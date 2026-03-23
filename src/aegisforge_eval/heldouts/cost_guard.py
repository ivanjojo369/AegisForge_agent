from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(slots=True)
class CostGuardResult:
    within_limits: bool
    warnings: list[str] = field(default_factory=list)

class CostGuard:
    """Check simple budget expectations for held-out experiments."""

    def __init__(self, *, max_llm_calls: int = 4, max_estimated_tokens: int = 9000) -> None:
        self.max_llm_calls = max_llm_calls
        self.max_estimated_tokens = max_estimated_tokens

    def evaluate(self, *, llm_calls_used: int, estimated_tokens_used: int) -> CostGuardResult:
        warnings: list[str] = []
        within = True

        if llm_calls_used > self.max_llm_calls:
            within = False
            warnings.append("LLM call count exceeded the configured limit.")
        if estimated_tokens_used > self.max_estimated_tokens:
            within = False
            warnings.append("Estimated token usage exceeded the configured limit.")

        return CostGuardResult(within_limits=within, warnings=warnings)
