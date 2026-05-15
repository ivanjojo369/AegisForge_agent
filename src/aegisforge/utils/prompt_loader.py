from __future__ import annotations

"""Runtime prompt loading helpers.

This module keeps the original PromptLoader behavior as a thin wrapper around
PromptManager, while adding Sprint 4 identity support and prompt fallback logic.

Important naming rule:
- upstream AgentBeats track/profile names such as officeqa, crmarena,
  fieldworkarena, maizebargain, osworld, pibench, cybergym, netarena,
  tau2_agentbeats, and mcu_minecraft must remain visible;
- local OpenEnv/OmniBench domains such as healthcare, web, agent_security,
  software_testing, defi, and legal_domain may map to prompt fallbacks when a
  dedicated prompt file does not exist yet;
- scenario identity must be preserved in prompt context, traces, summaries, and
  scorecards.
"""

from pathlib import Path
from typing import Any, Mapping

from ..prompts.prompt_manager import PromptBundle, PromptManager


SPRINT4_CONTEXT_KEYS: tuple[str, ...] = (
    "domain",
    "scenario_id",
    "scenario_name",
    "upstream_track",
    "category",
    "adapter",
    "assessment_mode",
    "scenario_family",
    "benchmark",
    "selected_opponent",
    "source_url",
)


TRACK_ALIASES: dict[str, str] = {
    # Upstream aliases.
    "crmarenapro": "crmarena",
    "crm_arena": "crmarena",
    "crm-arena": "crmarena",
    "fieldwork": "fieldworkarena",
    "maize_bargain": "maizebargain",
    "maize-bargain": "maizebargain",
    "tau2": "tau2_agentbeats",
    "tau2-agentbeats": "tau2_agentbeats",
    "mcu": "mcu_minecraft",
    "mcu-minecraft": "mcu_minecraft",
    "minecraft": "mcu_minecraft",
    # Local Sprint 4 domains to upstream profile names.
    "business_process": "crmarena",
    "finance": "officeqa",
    "research": "fieldworkarena",
    "multi_agent": "maizebargain",
    "computer_use": "osworld",
    "agent_safety": "pibench",
    "cybersecurity": "cybergym",
    "coding": "netarena",
    "game": "mcu_minecraft",
    "healthcare": "fhir_agent",
    "web": "comtrade",
    "agent_security": "lambda_agent_security",
    "software_testing": "logomesh",
    "defi": "ethernaut",
    "legal_domain": "agentify_bench",
}


SCENARIO_TO_DOMAIN: dict[str, str] = {
    "saleforceonespy": "business_process",
    "salesforceone": "business_process",
    "saleforceone": "business_process",
    "wikiwiper": "game",
    "tickettwister": "tau2",
    "bidbot": "multi_agent",
    "taxwiztrap": "finance",
    "lnklifter": "computer_use",
    "linklifter": "computer_use",
    "inventoryinject": "agent_safety",
    "devcontainerdoom": "coding",
    "staticshipscam": "cybersecurity",
    "whistleblowerwreck": "research",
    "docudoctor": "healthcare",
    "searchglitch": "web",
    "gymjailbreak": "agent_security",
    "codereviewruse": "software_testing",
    "cryptocrash": "defi",
    "lawfirmleak": "legal_domain",
}


PROMPT_FALLBACKS: dict[str, tuple[str, ...]] = {
    # Dedicated upstream profile first, broad profile second.
    "officeqa": ("officeqa", "openenv"),
    "crmarena": ("crmarena", "openenv"),
    "crmarenapro": ("crmarena", "openenv"),
    "fieldworkarena": ("fieldworkarena", "openenv"),
    "maizebargain": ("maizebargain", "openenv"),
    "osworld": ("osworld", "openenv"),
    "pibench": ("pibench", "security"),
    "cybergym": ("cybergym", "security"),
    "netarena": ("netarena", "security"),
    "tau2_agentbeats": ("tau2_agentbeats", "tau2", "openenv"),
    "mcu_minecraft": ("mcu_minecraft", "mcu", "openenv"),
    # Six added Sprint 4 profile names.
    "fhir_agent": ("fhir_agent", "openenv"),
    "comtrade": ("comtrade", "openenv"),
    "lambda_agent_security": ("lambda_agent_security", "security"),
    "logomesh": ("logomesh", "openenv"),
    "ethernaut": ("ethernaut", "openenv"),
    "agentify_bench": ("agentify_bench", "openenv"),
    # Broad fallbacks.
    "security": ("security", "openenv"),
    "tau2": ("tau2", "openenv"),
    "mcu": ("mcu", "openenv"),
    "openenv": ("openenv",),
}


def _clean_key(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .replace("-", "_")
        .replace(" ", "_")
        .replace(".", "_")
        .lower()
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


class PromptLoader:
    """Utility wrapper around PromptManager for runtime-facing code."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.manager = PromptManager(root_dir=root_dir)

    def normalize_track(self, track: str, *, enabled: bool = False) -> str:
        """Normalize known aliases only when requested.

        Default behavior is conservative so upstream names are not silently
        erased from benchmark metadata.
        """

        cleaned = str(track or "").strip()
        if not enabled:
            return cleaned
        return TRACK_ALIASES.get(_clean_key(cleaned), cleaned)

    def resolve_prompt_track(
        self,
        track: str | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
        normalize_track_alias: bool = True,
    ) -> str:
        """Resolve the best prompt track while preserving metadata separately."""

        candidates = self._candidate_tracks(
            track=track or "",
            metadata=metadata or {},
            normalize_track_alias=normalize_track_alias,
        )
        return candidates[0] if candidates else "openenv"

    def load_track_prompt(
        self,
        track: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        normalize_track_alias: bool = False,
        allow_fallback: bool = True,
    ) -> str:
        candidates = self._candidate_tracks(
            track=track,
            metadata=metadata or {},
            normalize_track_alias=normalize_track_alias,
        )
        if not allow_fallback:
            candidates = candidates[:1]

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                return self.manager.load_track_prompt(candidate).content
            except (FileNotFoundError, KeyError, ValueError) as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        return self.manager.load_track_prompt("openenv").content

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
        metadata: Mapping[str, Any] | None = None,
        append_sprint4_context: bool = False,
        normalize_track_alias: bool = False,
        allow_prompt_fallback: bool = True,
    ) -> str:
        candidates = self._candidate_tracks(
            track=track,
            metadata=metadata or {},
            normalize_track_alias=normalize_track_alias,
        )
        if not allow_prompt_fallback:
            candidates = candidates[:1]

        prompt = self._compose_runtime_with_candidates(
            candidates=candidates,
            artifact_required=artifact_required,
            structured_output=structured_output,
            include_planning=include_planning,
            include_reflection=include_reflection,
            include_tool_use=include_tool_use,
            extra_keys=extra_keys,
        )

        if append_sprint4_context:
            context = self.sprint4_context_block(metadata or {})
            if context:
                prompt = f"{prompt.rstrip()}\n\n{context}\n"

        return prompt

    def load_bundle(
        self,
        *,
        track: str,
        artifact_required: bool = False,
        structured_output: bool = False,
        extra_keys: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        normalize_track_alias: bool = False,
        allow_prompt_fallback: bool = True,
    ) -> PromptBundle:
        contract = None
        if artifact_required and structured_output:
            contract = "contracts/json_response.md"
        elif artifact_required:
            contract = "contracts/artifact_response.md"

        candidates = self._candidate_tracks(
            track=track,
            metadata=metadata or {},
            normalize_track_alias=normalize_track_alias,
        )
        if not allow_prompt_fallback:
            candidates = candidates[:1]

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                return self.manager.compose_bundle(
                    include_system=True,
                    include_planning=True,
                    include_reflection=True,
                    include_tool_use=True,
                    track=candidate,
                    contract=contract,
                    extra_keys=extra_keys,
                )
            except (FileNotFoundError, KeyError, ValueError) as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error

        return self.manager.compose_bundle(
            include_system=True,
            include_planning=True,
            include_reflection=True,
            include_tool_use=True,
            track="openenv",
            contract=contract,
            extra_keys=extra_keys,
        )

    def load_for_metadata(
        self,
        metadata: Mapping[str, Any],
        *,
        artifact_required: bool = False,
        structured_output: bool = False,
        include_planning: bool = True,
        include_reflection: bool = True,
        include_tool_use: bool = True,
        extra_keys: list[str] | None = None,
    ) -> str:
        """Load a runtime prompt using track/upstream/domain metadata.

        Selection preference:
        1. metadata["track"]
        2. metadata["upstream_track"] / metadata["benchmark_track"]
        3. metadata["domain"]
        4. metadata["scenario_id"] / metadata["scenario_name"]
        5. "openenv"
        """

        track = (
            str(metadata.get("track") or "").strip()
            or str(metadata.get("track_hint") or "").strip()
            or str(metadata.get("upstream_track") or "").strip()
            or str(metadata.get("benchmark_track") or "").strip()
            or str(metadata.get("domain") or "").strip()
            or str(metadata.get("scenario_id") or "").strip()
            or str(metadata.get("scenario_name") or "").strip()
            or "openenv"
        )

        return self.load_runtime_prompt(
            track=track,
            artifact_required=artifact_required,
            structured_output=structured_output,
            include_planning=include_planning,
            include_reflection=include_reflection,
            include_tool_use=include_tool_use,
            extra_keys=extra_keys,
            metadata=metadata,
            append_sprint4_context=True,
            normalize_track_alias=True,
            allow_prompt_fallback=True,
        )

    def debug_payload(
        self,
        *,
        track: str,
        artifact_required: bool = False,
        structured_output: bool = False,
        extra_keys: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self.manager.build_debug_payload(
            track=self.resolve_prompt_track(track, metadata=metadata, normalize_track_alias=True),
            artifact_required=artifact_required,
            structured_output=structured_output,
            extra_keys=extra_keys,
        )

        if metadata:
            payload = dict(payload)
            payload["sprint4_context"] = self.sprint4_context_dict(metadata)
            payload["sprint4_context_block"] = self.sprint4_context_block(metadata)
            payload["prompt_candidates"] = self._candidate_tracks(
                track=track,
                metadata=metadata,
                normalize_track_alias=True,
            )

        return payload

    def sprint4_context_dict(self, metadata: Mapping[str, Any]) -> dict[str, str]:
        """Extract prompt-safe Sprint 4 context values from metadata."""

        scenario = metadata.get("scenario")
        scenario_map = scenario if isinstance(scenario, Mapping) else {}

        context: dict[str, str] = {}
        for key in SPRINT4_CONTEXT_KEYS:
            value = metadata.get(key)
            if value in (None, ""):
                value = scenario_map.get(key)
            if value in (None, "") and key == "scenario_id":
                value = scenario_map.get("id")
            if value in (None, "") and key == "scenario_name":
                value = scenario_map.get("name")
            if value in (None, ""):
                continue
            context[key] = str(value).strip()

        return context

    def sprint4_context_block(self, metadata: Mapping[str, Any]) -> str:
        """Return a compact markdown context block for runtime prompts."""

        context = self.sprint4_context_dict(metadata)
        if not context:
            return ""

        lines = [
            "## AegisForge Sprint 4 Benchmark Context",
            "",
            "Preserve all upstream and local identity fields in traces, summaries, and scorecards.",
        ]
        for key in SPRINT4_CONTEXT_KEYS:
            if key in context:
                lines.append(f"- {key}: {context[key]}")

        return "\n".join(lines)

    def _candidate_tracks(
        self,
        *,
        track: str,
        metadata: Mapping[str, Any],
        normalize_track_alias: bool,
    ) -> list[str]:
        raw_candidates: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text:
                raw_candidates.append(text)

        add(track)
        add(metadata.get("track"))
        add(metadata.get("track_hint"))
        add(metadata.get("upstream_track"))
        add(metadata.get("benchmark_track"))
        add(metadata.get("domain"))
        add(metadata.get("scenario_id"))
        add(metadata.get("scenario_name"))

        scenario = metadata.get("scenario")
        if isinstance(scenario, Mapping):
            add(scenario.get("track"))
            add(scenario.get("upstream_track"))
            add(scenario.get("domain"))
            add(scenario.get("scenario_id"))
            add(scenario.get("id"))
            add(scenario.get("name"))

        resolved: list[str] = []
        for candidate in raw_candidates:
            key = _clean_key(candidate)
            if not key:
                continue

            if key in SCENARIO_TO_DOMAIN:
                domain = SCENARIO_TO_DOMAIN[key]
                upstream = TRACK_ALIASES.get(domain, domain)
                resolved.extend([upstream, domain])
                continue

            if normalize_track_alias:
                normalized = TRACK_ALIASES.get(key, candidate)
                resolved.append(normalized)
            else:
                resolved.append(candidate)

            alias = TRACK_ALIASES.get(key)
            if alias and alias != candidate:
                resolved.append(alias)

        expanded: list[str] = []
        for candidate in resolved or ["openenv"]:
            key = _clean_key(candidate)
            expanded.append(candidate)
            expanded.extend(PROMPT_FALLBACKS.get(key, ()))

        expanded.append("openenv")
        return _dedupe(expanded)

    def _compose_runtime_with_candidates(
        self,
        *,
        candidates: list[str],
        artifact_required: bool,
        structured_output: bool,
        include_planning: bool,
        include_reflection: bool,
        include_tool_use: bool,
        extra_keys: list[str] | None,
    ) -> str:
        last_error: Exception | None = None
        for candidate in candidates or ["openenv"]:
            try:
                return self.manager.compose_runtime_prompt(
                    track=candidate,
                    artifact_required=artifact_required,
                    structured_output=structured_output,
                    include_planning=include_planning,
                    include_reflection=include_reflection,
                    include_tool_use=include_tool_use,
                    extra_keys=extra_keys,
                )
            except (FileNotFoundError, KeyError, ValueError) as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error

        return self.manager.compose_runtime_prompt(
            track="openenv",
            artifact_required=artifact_required,
            structured_output=structured_output,
            include_planning=include_planning,
            include_reflection=include_reflection,
            include_tool_use=include_tool_use,
            extra_keys=extra_keys,
        )
