from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .budget_guard import BudgetState
from .task_classifier import TaskClassification
from .track_profiles import TrackProfile, get_track_profile

try:
    from .mcu import MCU_PROFILE
except Exception:  # pragma: no cover
    MCU_PROFILE = None


@dataclass(slots=True)
class RouteDecision:
    track: str
    adapter_name: str
    prompt_profile: str
    policy_profile: str
    tool_mode: str
    reasons: list[str]


class TaskRouter:
    """Choose the execution route for a classified task."""

    ADAPTERS = {
        'openenv': 'openenv',
        'security': 'security',
        'tau2': 'tau2',
        'mcu': 'mcu',
    }

    def decide(
        self,
        classification: TaskClassification,
        *,
        metadata: Mapping[str, Any] | None = None,
        budget_state: BudgetState | None = None,
        track_profile: TrackProfile | None = None,
    ) -> RouteDecision:
        metadata = dict(metadata or {})
        track = self._normalize_track(str(metadata.get('track_hint') or classification.track_guess))
        assessment_mode = str(metadata.get('assessment_mode') or 'defender').lower().strip()
        reasons: list[str] = []

        if track == 'mcu':
            adapter_name = 'mcu'
            prompt_profile = 'mcu_attacker' if assessment_mode == 'attacker' else 'mcu_defender'
            policy_profile = 'bounded_poisoning' if assessment_mode == 'attacker' else 'knowledge_hardening'
            tool_mode = 'guided'
            reasons.append('Selected MCU benchmark route.')
            reasons.append(f'Assessment mode: {assessment_mode}.')
            if metadata.get('scenario_family') == 'wikiwiper':
                reasons.append('Scenario family WikiWiper favors knowledge-source handling checks.')
            if budget_state and budget_state.near_limit:
                tool_mode = 'minimal'
                reasons.append('Budget near limit; reduce extra probing.')
            return RouteDecision(
                track=track,
                adapter_name=adapter_name,
                prompt_profile=prompt_profile,
                policy_profile=policy_profile,
                tool_mode=tool_mode,
                reasons=reasons,
            )

        profile = track_profile or get_track_profile(track)
        adapter_name = self.ADAPTERS.get(track, 'openenv')
        prompt_profile = profile.default_prompt
        policy_profile = profile.name
        tool_mode = 'allow'

        reasons.append(f'Selected track profile: {profile.name}.')

        if classification.tool_use_likely:
            tool_mode = 'guided'
            reasons.append('Task likely benefits from guided tool use.')
        else:
            tool_mode = 'minimal'
            reasons.append('Task appears solvable with minimal tool activity.')

        if budget_state and budget_state.near_limit:
            tool_mode = 'minimal'
            reasons.append('Budget is near limit; preferring a shorter route.')

        if classification.risk == 'high':
            reasons.append('High-risk task; apply more conservative adapter settings.')

        return RouteDecision(
            track=track,
            adapter_name=adapter_name,
            prompt_profile=prompt_profile,
            policy_profile=policy_profile,
            tool_mode=tool_mode,
            reasons=reasons,
        )

    @staticmethod
    def _normalize_track(track: str) -> str:
        normalized = (track or 'openenv').lower().strip()
        aliases = {
            'minecraft': 'mcu',
            'minecraft benchmark': 'mcu',
            'mcu-agentbeats': 'mcu',
            'security_arena': 'security',
            'tau²': 'tau2',
        }
        return aliases.get(normalized, normalized)
