from __future__ import annotations

from dataclasses import dataclass

from .contracts import AdapterResult


@dataclass(slots=True)
class RecoveryDecision:
    retry: bool
    reason: str
    revised_tool_mode: str | None = None
    fallback_response: str | None = None


class RecoveryPolicy:
    """Simple recovery policy for adapter or execution failures."""

    def __init__(self, max_retries: int = 1) -> None:
        self.max_retries = max_retries

    def decide(self, *, attempt: int, result: AdapterResult) -> RecoveryDecision:
        if result.ok:
            return RecoveryDecision(retry=False, reason="No recovery required.")

        if attempt < self.max_retries and result.error_code in {"timeout", "budget_guard", "transient"}:
            return RecoveryDecision(
                retry=True,
                reason=f"Retrying after recoverable error: {result.error_code}.",
                revised_tool_mode="minimal",
            )

        fallback = self.build_fallback_response(result)
        return RecoveryDecision(
            retry=False,
            reason="Using fallback after unrecoverable failure.",
            fallback_response=fallback,
        )

    @staticmethod
    def build_fallback_response(result: AdapterResult) -> str:
        detail = result.error_message or "The task could not be completed cleanly."
        return (
            "AegisForge could not complete the full execution path safely. "
            f"Fallback reason: {detail}"
        )
