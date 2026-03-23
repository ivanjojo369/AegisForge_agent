from __future__ import annotations

from pathlib import Path
from typing import Any

from ..prompts.prompt_manager import PromptBundle, PromptManager


class PromptLoader:
    """Thin utility wrapper around PromptManager for runtime-facing code."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.manager = PromptManager(root_dir=root_dir)

    def load_track_prompt(self, track: str) -> str:
        return self.manager.load_track_prompt(track).content

    def load_runtime_prompt(
        self,
        *,
        track: str,
        artifact_required: bool = False,
        structured_output: bool = False,
        include_planning: bool = True,
        include_reflection: bool = True,
        include_tool_use: bool = True,
        extra_keys: list[str] | None = None,
    ) -> str:
        return self.manager.compose_runtime_prompt(
            track=track,
            artifact_required=artifact_required,
            structured_output=structured_output,
            include_planning=include_planning,
            include_reflection=include_reflection,
            include_tool_use=include_tool_use,
            extra_keys=extra_keys,
        )

    def load_bundle(
        self,
        *,
        track: str,
        artifact_required: bool = False,
        structured_output: bool = False,
        extra_keys: list[str] | None = None,
    ) -> PromptBundle:
        contract = None
        if artifact_required and structured_output:
            contract = "contracts/json_response.md"
        elif artifact_required:
            contract = "contracts/artifact_response.md"

        return self.manager.compose_bundle(
            include_system=True,
            include_planning=True,
            include_reflection=True,
            include_tool_use=True,
            track=track,
            contract=contract,
            extra_keys=extra_keys,
        )

    def debug_payload(
        self,
        *,
        track: str,
        artifact_required: bool = False,
        structured_output: bool = False,
        extra_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.manager.build_debug_payload(
            track=track,
            artifact_required=artifact_required,
            structured_output=structured_output,
            extra_keys=extra_keys,
        )
