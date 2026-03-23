from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .contracts import AdapterRequest, AdapterResult, ExecutionEvent
from .episode import EpisodeState, EpisodeStatus
from .recovery import RecoveryPolicy
from .state_reset import StateResetManager


class ExecutionLoop:
    """Explicit episode execution loop for AegisForge.

    The loop is deliberately small and adapter-agnostic. It receives a
    prepared ``AdapterRequest`` and delegates the actual benchmark logic to
    an injected callable.
    """

    def __init__(
        self,
        *,
        recovery_policy: RecoveryPolicy | None = None,
        state_reset: StateResetManager | None = None,
        event_sink: Callable[[ExecutionEvent], None] | None = None,
    ) -> None:
        self.recovery_policy = recovery_policy or RecoveryPolicy()
        self.state_reset = state_reset or StateResetManager()
        self.event_sink = event_sink

    def run(
        self,
        episode: EpisodeState,
        request: AdapterRequest,
        adapter_call: Callable[[AdapterRequest], AdapterResult],
    ) -> EpisodeState:
        self._emit("episode_start", "setup", "Starting episode orchestration.", task_id=episode.context.task_id)
        episode.status = EpisodeStatus.RUNNING

        while True:
            episode.attempt += 1
            self._emit("adapter_call", "execute", "Dispatching request to adapter.", attempt=episode.attempt)
            result = adapter_call(request)

            if result.ok:
                episode.mark_completed(result.response_text, result.artifacts)
                self._emit("episode_complete", "finalize", "Episode completed successfully.")
                return episode

            episode.status = EpisodeStatus.RECOVERING
            decision = self.recovery_policy.decide(attempt=episode.attempt, result=result)
            episode.warnings.extend(result.warnings)

            self._emit(
                "recovery_decision",
                "recover",
                decision.reason,
                retry=decision.retry,
                error_code=result.error_code,
            )

            if decision.retry:
                if decision.revised_tool_mode:
                    request.tool_mode = decision.revised_tool_mode
                continue

            episode.mark_completed(
                decision.fallback_response or result.response_text or "",
                result.artifacts,
            )
            episode.warnings.append("Completed with fallback path.")
            self._emit("episode_fallback", "finalize", "Episode finalized through fallback.")
            return episode

    def reset_between_runs(self) -> dict[str, Any]:
        report = self.state_reset.build_reset_report()
        self._emit("state_reset", "cleanup", "Prepared clean-state reset report.", **report)
        return report

    def _emit(self, name: str, phase: str, message: str, **payload: Any) -> None:
        if self.event_sink is None:
            return
        self.event_sink(ExecutionEvent(name=name, phase=phase, message=message, payload=payload))
