from __future__ import annotations

from ..contracts import PluginContext, PluginResult


class Tau2BaselinePlugin:
    """Minimal reference plugin for tau2-style action tasks."""

    name = "tau2_baseline"
    supported_tracks = ["tau2"]

    def run(self, context: PluginContext) -> PluginResult:
        sequential = any(
            token in context.prompt.lower()
            for token in ["step", "action", "trajectory", "sequence", "state"]
        )

        output = {
            "mode": "tau2_baseline",
            "task_id": context.task_id,
            "sequence_sensitivity": sequential,
            "action_hint": "preserve_consistency" if sequential else "lightweight_reasoning",
        }

        return PluginResult(
            ok=True,
            name=self.name,
            output=output,
            warnings=[],
        )
"""
This module defines the Tau2BaselinePlugin, a simple example plugin designed for tau2-style action tasks. The plugin checks the prompt for indicators of sequential or stateful reasoning, such as mentions of steps, actions, trajectories, sequences, or states. Based on this analysis, it provides hints about whether the task is likely to require preserving consistency across a sequence of actions or if it can be handled with more lightweight reasoning. This plugin serves as a baseline that can be extended with more sophisticated logic or integrated into a larger agent strategy focused on action-oriented tasks.
"""
