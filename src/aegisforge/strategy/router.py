from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .budget_guard import BudgetState
from .task_classifier import TaskClassification
from .track_profiles import TrackProfile, get_track_profile


@dataclass(slots=True)
class RouteDecision:
    track: str
    adapter_name: str
    prompt_profile: str
    policy_profile: str
    tool_mode: str
    assessment_mode: str
    scenario_family: str
    strict_mode: bool
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "adapter_name": self.adapter_name,
            "prompt_profile": self.prompt_profile,
            "policy_profile": self.policy_profile,
            "tool_mode": self.tool_mode,
            "assessment_mode": self.assessment_mode,
            "scenario_family": self.scenario_family,
            "strict_mode": self.strict_mode,
            "reasons": list(self.reasons),
        }


class TaskRouter:
    """Choose the execution route for a classified task."""

    ADAPTERS = {
        "openenv": "openenv",
        "security": "security",
        "tau2": "tau2",
        "mcu": "mcu",
    }

    def decide(
        self,
        classification: TaskClassification,
        *,
        metadata: Mapping[str, object] | None = None,
        budget_state: BudgetState | None = None,
        track_profile: TrackProfile | None = None,
    ) -> RouteDecision:
        metadata_dict = dict(metadata or {})
        scenario = self._to_dict(metadata_dict.get("scenario"))
        signals = self._to_dict(metadata_dict.get("signals"))

        track = self._normalize_track(
            metadata_dict.get("track")
            or metadata_dict.get("track_hint")
            or classification.track_guess
            or "openenv"
        )
        assessment_mode = self._normalize_mode(
            metadata_dict.get("assessment_mode")
            or scenario.get("assessment_mode")
            or "defender"
        )
        scenario_family = self._normalize_family(
            metadata_dict.get("scenario_family")
            or scenario.get("scenario_family")
            or "general"
        )
        strict_mode = self._read_bool(metadata_dict.get("strict_mode"), default=False)
        normal_user = self._read_bool(
            metadata_dict.get("normal_user", scenario.get("normal_user")),
            default=False,
        )
        requires_artifact = self._read_bool(
            metadata_dict.get("requires_artifact", signals.get("requires_artifact")),
            default=False,
        )
        max_turns = max(
            1,
            self._safe_int(metadata_dict.get("max_turns", scenario.get("max_turns")), default=1),
        )
        expected_risk = self._normalize_risk(
            metadata_dict.get("expected_risk", signals.get("expected_risk"))
        )
        effective_risk = self._max_risk(
            self._normalize_risk(getattr(classification, "risk", None)),
            expected_risk,
        )
        heldout_like = bool(getattr(classification, "heldout_like", False)) or self._read_bool(
            metadata_dict.get("heldout_like", signals.get("heldout_like")),
            default=False,
        )

        reasons: list[str] = []

        if track == "mcu":
            return self._route_mcu(
                classification=classification,
                assessment_mode=assessment_mode,
                scenario_family=scenario_family,
                strict_mode=strict_mode,
                normal_user=normal_user,
                max_turns=max_turns,
                effective_risk=effective_risk,
                heldout_like=heldout_like,
                budget_state=budget_state,
                metadata=metadata_dict,
                reasons=reasons,
            )

        if track == "security":
            return self._route_security(
                classification=classification,
                assessment_mode=assessment_mode,
                scenario_family=scenario_family,
                strict_mode=strict_mode,
                normal_user=normal_user,
                requires_artifact=requires_artifact,
                max_turns=max_turns,
                effective_risk=effective_risk,
                heldout_like=heldout_like,
                budget_state=budget_state,
                metadata=metadata_dict,
                reasons=reasons,
            )

        return self._route_generic(
            classification=classification,
            track=track,
            assessment_mode=assessment_mode,
            scenario_family=scenario_family,
            strict_mode=strict_mode,
            effective_risk=effective_risk,
            budget_state=budget_state,
            track_profile=track_profile,
            metadata=metadata_dict,
            reasons=reasons,
        )

    def _route_mcu(
        self,
        *,
        classification: TaskClassification,
        assessment_mode: str,
        scenario_family: str,
        strict_mode: bool,
        normal_user: bool,
        max_turns: int,
        effective_risk: str,
        heldout_like: bool,
        budget_state: BudgetState | None,
        metadata: Mapping[str, object],
        reasons: list[str],
    ) -> RouteDecision:
        adapter_name = "mcu"
        prompt_profile = str(
            metadata.get("prompt_profile")
            or ("mcu_attacker" if assessment_mode == "attacker" else "mcu_defender")
        )
        policy_profile = str(
            metadata.get("policy_profile")
            or ("bounded_poisoning" if assessment_mode == "attacker" else "knowledge_hardening")
        )
        tool_mode = "guided"

        reasons.append("Selected MCU benchmark route.")
        reasons.append(f"Assessment mode: {assessment_mode}.")
        reasons.append(f"Scenario family: {scenario_family}.")

        if scenario_family == "wikiwiper":
            reasons.append("WikiWiper favors explicit trusted-source handling logic.")

        if heldout_like:
            reasons.append("Held-out-like task detected; avoid brittle source assumptions.")

        if normal_user:
            reasons.append("Normal-user compatibility should remain intact for this route.")

        if max_turns > 1:
            reasons.append(f"Multi-turn setting detected (max_turns={max_turns}).")

        if strict_mode:
            reasons.append("Strict mode enabled; keep the route tightly bounded.")
            tool_mode = "minimal"

        if effective_risk in {"high", "critical"}:
            reasons.append(f"{effective_risk.title()}-risk MCU task detected.")

        if budget_state and budget_state.near_limit:
            tool_mode = "minimal"
            reasons.append("Budget near limit; reduce extra probing.")

        return RouteDecision(
            track="mcu",
            adapter_name=adapter_name,
            prompt_profile=prompt_profile,
            policy_profile=policy_profile,
            tool_mode=tool_mode,
            assessment_mode=assessment_mode,
            scenario_family=scenario_family,
            strict_mode=strict_mode,
            reasons=reasons,
        )

    def _route_security(
        self,
        *,
        classification: TaskClassification,
        assessment_mode: str,
        scenario_family: str,
        strict_mode: bool,
        normal_user: bool,
        requires_artifact: bool,
        max_turns: int,
        effective_risk: str,
        heldout_like: bool,
        budget_state: BudgetState | None,
        metadata: Mapping[str, object],
        reasons: list[str],
    ) -> RouteDecision:
        adapter_name = "security"
        prompt_profile = str(
            metadata.get("prompt_profile")
            or ("security_attacker" if assessment_mode == "attacker" else "security_defender")
        )
        policy_profile = str(
            metadata.get("policy_profile")
            or self._security_policy_profile(
                assessment_mode=assessment_mode,
                scenario_family=scenario_family,
            )
        )
        tool_mode = self._security_tool_mode(
            classification=classification,
            assessment_mode=assessment_mode,
            strict_mode=strict_mode,
            effective_risk=effective_risk,
            budget_state=budget_state,
            requires_artifact=requires_artifact,
            max_turns=max_turns,
        )

        reasons.append("Selected Security Arena route.")
        reasons.append(f"Assessment mode: {assessment_mode}.")
        reasons.append(f"Scenario family: {scenario_family}.")

        if heldout_like:
            reasons.append("Held-out-like task detected; favor generalizable tactics over brittle shortcuts.")

        if effective_risk == "critical":
            reasons.append("Critical-risk security task detected; use the tightest route posture.")
        elif effective_risk == "high":
            reasons.append("High-risk security task detected; harden route selection.")
        elif effective_risk == "medium":
            reasons.append("Medium-risk security task detected; apply additional skepticism.")

        if assessment_mode == "attacker":
            reasons.append("Attacker mode favors adaptive probing and exploit-path iteration.")
            if requires_artifact:
                reasons.append("Attacker artifact expected; keep final route aligned with attack-plan output.")
        else:
            reasons.append("Defender mode favors safe utility, exposure minimization, and instruction isolation.")
            if normal_user:
                reasons.append("Normal-user requirement active; preserve benign utility.")

        if strict_mode:
            reasons.append("Strict mode active; prefer narrower and more explicit routing decisions.")

        if max_turns > 1:
            reasons.append(f"Multi-pass or multi-turn pressure detected (max_turns={max_turns}).")

        if budget_state and budget_state.near_limit:
            reasons.append("Budget near limit; prefer concise tactics and short finalization.")

        return RouteDecision(
            track="security",
            adapter_name=adapter_name,
            prompt_profile=prompt_profile,
            policy_profile=policy_profile,
            tool_mode=tool_mode,
            assessment_mode=assessment_mode,
            scenario_family=scenario_family,
            strict_mode=strict_mode,
            reasons=reasons,
        )

    def _route_generic(
        self,
        *,
        classification: TaskClassification,
        track: str,
        assessment_mode: str,
        scenario_family: str,
        strict_mode: bool,
        effective_risk: str,
        budget_state: BudgetState | None,
        track_profile: TrackProfile | None,
        metadata: Mapping[str, object],
        reasons: list[str],
    ) -> RouteDecision:
        profile = track_profile or get_track_profile(track)
        adapter_name = self.ADAPTERS.get(track, "openenv")
        prompt_profile = str(metadata.get("prompt_profile") or profile.default_prompt)
        policy_profile = str(metadata.get("policy_profile") or profile.name)
        tool_mode = "allow"

        reasons.append(f"Selected track profile: {profile.name}.")
        reasons.append(f"Assessment mode: {assessment_mode}.")
        reasons.append(f"Scenario family: {scenario_family}.")

        if getattr(classification, "tool_use_likely", False):
            tool_mode = "guided"
            reasons.append("Task likely benefits from guided tool use.")
        else:
            tool_mode = "minimal"
            reasons.append("Task appears solvable with minimal tool activity.")

        if strict_mode:
            tool_mode = "minimal"
            reasons.append("Strict mode enabled; reducing routing breadth.")

        if budget_state and budget_state.near_limit:
            tool_mode = "minimal"
            reasons.append("Budget is near limit; preferring a shorter route.")

        if effective_risk == "critical":
            reasons.append("Critical-risk task; keep conservative adapter settings.")
            tool_mode = "minimal"
        elif effective_risk == "high":
            reasons.append("High-risk task; apply more conservative adapter settings.")

        return RouteDecision(
            track=track,
            adapter_name=adapter_name,
            prompt_profile=prompt_profile,
            policy_profile=policy_profile,
            tool_mode=tool_mode,
            assessment_mode=assessment_mode,
            scenario_family=scenario_family,
            strict_mode=strict_mode,
            reasons=reasons,
        )

    def _security_policy_profile(self, *, assessment_mode: str, scenario_family: str) -> str:
        if assessment_mode == "attacker":
            if scenario_family in {"prompt_injection", "indirect_injection", "jailbreak"}:
                return "injection_ops"
            if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
                return "exfiltration_ops"
            if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
                return "unsafe_output_ops"
            if scenario_family in {"supply_chain", "dependency_attack"}:
                return "supply_chain_ops"
            return "security_attacker_default"

        if scenario_family in {"prompt_injection", "indirect_injection", "jailbreak"}:
            return "instruction_hardening"
        if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
            return "secret_protection"
        if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
            return "output_sanitization"
        if scenario_family in {"supply_chain", "dependency_attack"}:
            return "dependency_hardening"
        return "security_defender_default"

    def _security_tool_mode(
        self,
        *,
        classification: TaskClassification,
        assessment_mode: str,
        strict_mode: bool,
        effective_risk: str,
        budget_state: BudgetState | None,
        requires_artifact: bool,
        max_turns: int,
    ) -> str:
        if budget_state and budget_state.near_limit:
            return "minimal"

        if strict_mode and effective_risk in {"high", "critical"}:
            return "minimal"

        if assessment_mode == "attacker":
            if getattr(classification, "tool_use_likely", False) or getattr(classification, "multi_step", False):
                return "guided"
            if requires_artifact or max_turns > 1:
                return "guided"
            return "minimal"

        if effective_risk in {"high", "critical"}:
            return "guided"
        if getattr(classification, "tool_use_likely", False):
            return "guided"
        if max_turns > 1:
            return "guided"
        return "minimal"

    @staticmethod
    def _to_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    @staticmethod
    def _read_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _safe_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _max_risk(left: str, right: str) -> str:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return left if order.get(left, 0) >= order.get(right, 0) else right

    @staticmethod
    def _normalize_track(track: object) -> str:
        normalized = str(track or "openenv").lower().strip()
        aliases = {
            "minecraft": "mcu",
            "minecraft benchmark": "mcu",
            "mcu-agentbeats": "mcu",
            "security_arena": "security",
            "agent_safety": "security",
            "cybersecurity": "security",
            "tau²": "tau2",
        }
        return aliases.get(normalized, normalized)

    @staticmethod
    def _normalize_mode(value: object | None) -> str:
        raw = str(value or "").strip().lower()
        aliases = {
            "attack": "attacker",
            "offense": "attacker",
            "offensive": "attacker",
            "red": "attacker",
            "defense": "defender",
            "defensive": "defender",
            "blue": "defender",
            "guardian": "defender",
            "safe": "defender",
        }
        normalized = aliases.get(raw, raw)
        if normalized in {"attacker", "defender"}:
            return normalized
        raise ValueError(f"Unsupported assessment_mode: {value!r}")

    @staticmethod
    def _normalize_family(value: object | None) -> str:
        raw = str(value or "general").strip().lower()
        aliases = {
            "prompt_injection_and_jailbreaking": "prompt_injection",
            "jailbreaking": "jailbreak",
            "pii": "pii_leakage",
            "prompt_leakage": "prompt_extraction",
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _normalize_risk(value: object | None) -> str:
        raw = str(value or "low").strip().lower()
        return raw if raw in {"low", "medium", "high", "critical"} else "low"
    