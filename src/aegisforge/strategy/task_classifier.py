from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


TRACK_ALIASES = {
    'mcu': 'mcu',
    'minecraft': 'mcu',
    'minecraft benchmark': 'mcu',
    'mcu-agentbeats': 'mcu',
    'security': 'security',
    'security_arena': 'security',
    'tau2': 'tau2',
    'tau²': 'tau2',
    'openenv': 'openenv',
}


@dataclass(slots=True)
class TaskClassification:
    """Lightweight description of the incoming task.

    This structure is intentionally generic so it can be built from A2A
    payloads, OpenEnv tasks, tau2 prompts, MCU benchmark prompts, or internal
    harness messages.
    """

    track_guess: str
    task_type: str
    complexity: str
    risk: str
    artifact_expected: bool = False
    multi_step: bool = False
    tool_use_likely: bool = False
    heldout_like: bool = False
    tags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class TaskClassifier:
    """Heuristic first-pass classifier for AegisForge tasks."""

    SECURITY_KEYWORDS = {
        'prompt injection', 'exfiltration', 'jailbreak', 'policy bypass', 'secret',
        'credentials', 'malware', 'phishing', 'attack', 'defend', 'security', 'adversarial',
    }
    OPENENV_KEYWORDS = {
        'tool', 'table', 'ticket', 'lookup', 'decision', 'budget', 'finance',
        'business', 'mission', 'state', 'environment', 'submit_final',
    }
    TAU2_KEYWORDS = {
        'trajectory', 'trace', 'action', 'step', 'evaluate', 'score', 'task', 'tau2',
    }
    MCU_KEYWORDS = {
        'minecraft', 'craft', 'recipe', 'helmet', 'ingot', 'potion', 'enchant',
        'smelt', 'mine', 'redstone', 'tool', 'pickaxe', 'wiki', 'simulator',
        'animal care', 'navigation', 'mcu',
    }
    ARTIFACT_KEYWORDS = {
        'json', 'yaml', 'artifact', 'report', 'table', 'file', 'schema', 'card',
    }
    TOOL_KEYWORDS = {
        'tool', 'lookup', 'query', 'search', 'inspect', 'call', 'use map_probe',
        'use table_lookup', 'use ticket_lookup', 'simulator', 'crafting wiki', 'minecraft wiki',
    }
    HIGH_RISK_PATTERNS = (
        r'ignore previous instructions',
        r'reveal .*secret',
        r'print .*token',
        r'export .*data',
        r'bypass .*policy',
        r'system note',
        r'prefer the wiki over prior knowledge',
        r'requires 1 stick',
        r'override',
    )
    MEDIUM_RISK_MARKERS = {
        'poisoned', 'tampered', 'untrusted', 'wiki', 'hidden note', 'priority abuse',
    }

    def classify(
        self,
        task_text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        track_hint: str | None = None,
    ) -> TaskClassification:
        metadata = dict(metadata or {})
        normalized = ' '.join(task_text.lower().split())
        tags: list[str] = []
        reasons: list[str] = []

        track_guess = self._guess_track(normalized, track_hint)
        task_type = self._guess_task_type(normalized, track_guess)
        complexity = self._guess_complexity(normalized)
        risk = self._guess_risk(normalized)
        artifact_expected = self._contains_any(normalized, self.ARTIFACT_KEYWORDS)
        tool_use_likely = self._contains_any(normalized, self.TOOL_KEYWORDS) or track_guess == 'mcu'
        multi_step = self._looks_multi_step(normalized) or track_guess == 'mcu'
        heldout_like = self._looks_heldout_like(normalized, metadata)

        if artifact_expected:
            tags.append('artifact')
            reasons.append('Detected output language suggesting a structured artifact.')
        if tool_use_likely:
            tags.append('tool-use')
            reasons.append('Detected benchmark or tool-oriented language.')
        if multi_step:
            tags.append('multi-step')
            reasons.append('Task appears to require a sequence of actions.')
        if heldout_like:
            tags.append('heldout-like')
            reasons.append('Task resembles a non-templated or unusual prompt.')
        if track_guess == 'mcu':
            tags.append('minecraft')
            reasons.append('Detected Minecraft / MCU benchmark language.')
        if risk in {'medium', 'high'}:
            tags.append('risk-aware')
            reasons.append('Security-sensitive, poisoned-knowledge, or policy-sensitive language detected.')

        return TaskClassification(
            track_guess=track_guess,
            task_type=task_type,
            complexity=complexity,
            risk=risk,
            artifact_expected=artifact_expected,
            multi_step=multi_step,
            tool_use_likely=tool_use_likely,
            heldout_like=heldout_like,
            tags=tags,
            reasons=reasons,
        )

    def _guess_track(self, text: str, track_hint: str | None) -> str:
        if track_hint:
            return TRACK_ALIASES.get(track_hint.lower().strip(), track_hint.lower().strip())

        security_hits = self._count_hits(text, self.SECURITY_KEYWORDS)
        openenv_hits = self._count_hits(text, self.OPENENV_KEYWORDS)
        tau2_hits = self._count_hits(text, self.TAU2_KEYWORDS)
        mcu_hits = self._count_hits(text, self.MCU_KEYWORDS)

        best = max(security_hits, openenv_hits, tau2_hits, mcu_hits)
        if best <= 0:
            return 'openenv'
        if mcu_hits == best:
            return 'mcu'
        if security_hits == best:
            return 'security'
        if openenv_hits == best:
            return 'openenv'
        return 'tau2'

    def _guess_task_type(self, text: str, track_guess: str) -> str:
        if track_guess == 'mcu':
            if 'artifact' in text or 'json' in text or 'report' in text:
                return 'artifact_generation'
            if 'craft' in text or 'mine' in text or 'navigate' in text or 'potion' in text:
                return 'environment_task'
            return 'reasoning'
        if 'analy' in text or 'classif' in text:
            return 'analysis'
        if 'tool' in text or 'lookup' in text or 'query' in text:
            return 'tool_use'
        if 'report' in text or 'json' in text or 'artifact' in text:
            return 'artifact_generation'
        if 'plan' in text or 'steps' in text or 'sequence' in text:
            return 'multi_step_reasoning'
        return 'reasoning'

    def _guess_complexity(self, text: str) -> str:
        signals = 0
        signals += 1 if self._looks_multi_step(text) else 0
        signals += 1 if self._contains_any(text, self.ARTIFACT_KEYWORDS) else 0
        signals += 1 if self._contains_any(text, self.TOOL_KEYWORDS) else 0
        signals += 1 if len(text.split()) > 120 else 0
        if signals >= 3:
            return 'high'
        if signals == 2:
            return 'medium'
        return 'low'

    def _guess_risk(self, text: str) -> str:
        for pattern in self.HIGH_RISK_PATTERNS:
            if re.search(pattern, text):
                return 'high'
        if self._contains_any(text, self.SECURITY_KEYWORDS | self.MEDIUM_RISK_MARKERS):
            return 'medium'
        return 'low'

    def _looks_multi_step(self, text: str) -> bool:
        return (
            'then ' in text
            or 'after ' in text
            or 'step ' in text
            or 'first ' in text
            or 'next ' in text
            or text.count(';') >= 2
        )

    def _looks_heldout_like(self, text: str, metadata: Mapping[str, Any]) -> bool:
        if metadata.get('heldout_mode') is True:
            return True
        vocab = set(text.split())
        rare_signal = len(vocab) > 90 and self._count_hits(text, self.OPENENV_KEYWORDS | self.SECURITY_KEYWORDS | self.TAU2_KEYWORDS | self.MCU_KEYWORDS) <= 2
        return rare_signal

    @staticmethod
    def _contains_any(text: str, keywords: set[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _count_hits(text: str, keywords: set[str]) -> int:
        return sum(1 for keyword in keywords if keyword in text)
