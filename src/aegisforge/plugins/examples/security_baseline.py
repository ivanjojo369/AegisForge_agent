from __future__ import annotations

from ..contracts import PluginContext, PluginResult


class SecurityBaselinePlugin:
    """Minimal reference plugin for security-oriented tasks."""

    name = "security_baseline"
    supported_tracks = ["security"]

    def run(self, context: PluginContext) -> PluginResult:
        suspicious = any(
            token in context.prompt.lower()
            for token in ["ignore previous", "reveal", "token", "secret", "credentials"]
        )

        output = {
            "mode": "security_baseline",
            "task_id": context.task_id,
            "risk_hint": "high" if suspicious else "medium",
            "action_hint": "inspect_and_constrain" if suspicious else "analyze_normally",
        }
        warnings = ["Suspicious prompt patterns detected."] if suspicious else []

        return PluginResult(
            ok=True,
            name=self.name,
            output=output,
            warnings=warnings,
        )
"""
This module defines the SecurityBaselinePlugin, a simple example plugin designed for security-oriented tasks. The plugin analyzes the prompt for potentially suspicious patterns that may indicate risky behavior, such as attempts to reveal secrets or ignore previous instructions. Based on the analysis, it provides hints about the risk level and recommended actions. This plugin serves as a baseline that can be extended with more sophisticated logic or integrated into a larger security-focused agent strategy.
"""
