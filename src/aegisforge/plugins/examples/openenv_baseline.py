from __future__ import annotations

from ..contracts import PluginContext, PluginResult


class OpenEnvBaselinePlugin:
    """Minimal reference plugin for environment-style tasks."""

    name = "openenv_baseline"
    supported_tracks = ["openenv"]

    def run(self, context: PluginContext) -> PluginResult:
        tool_like = any(
            token in context.prompt.lower()
            for token in ["lookup", "query", "ticket", "table", "probe", "environment"]
        )

        output = {
            "mode": "openenv_baseline",
            "task_id": context.task_id,
            "tool_use_likely": tool_like,
            "action_hint": "collect_minimum_evidence" if tool_like else "respond_directly",
        }

        return PluginResult(
            ok=True,
            name=self.name,
            output=output,
            warnings=[],
        )
