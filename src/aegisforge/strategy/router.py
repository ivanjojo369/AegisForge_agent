from __future__ import annotations

"""Routing logic for AegisForge strategy execution.

The router intentionally keeps two naming layers alive:

1. AgentBeats / upstream track names used for opponent profiles, prompts,
   leaderboard metadata, and historical compatibility, for example
   ``officeqa``, ``crmarena``, ``fieldworkarena``, ``maizebargain``,
   ``osworld``, ``pibench``, ``cybergym``, ``netarena``.
2. AegisForge / OpenEnv Sprint 4 domain and scenario names used by the local
   OmniBench integration, for example ``finance`` / ``TaxWizTrap`` and
   ``healthcare`` / ``DocuDoctor``.

Do not treat the upstream names as stale.  They are compatibility/reporting
profiles.  Local domains and scenario IDs are resolved to those profiles while
still routing to the coarse adapter layer: openenv, security, tau2, mcu, or skillsbench.
"""

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
    """Choose the execution route for a classified task.

    Public AgentBeats tracks are preserved for reporting and strategy, while
    local Sprint 4 OpenEnv domains are accepted as first-class aliases.
    """

    MCU_TRACKS = {"mcu", "mcu_minecraft"}
    SKILLSBENCH_TRACKS = {"skillsbench"}

    # Tracks that benefit from the security adapter posture.  Some local
    # OpenEnv domains still route to openenv because they are environment-style
    # domains even when they are security-adjacent.
    SECURITY_TRACKS = {
        "security",
        "security_arena",
        "agent_security",
        "lambda_agent_security",
        "pibench",
        "cybergym",
        "netarena",
    }

    # Canonical Sprint 4 local domain -> upstream/reporting track.
    DOMAIN_TO_TRACK = {
        "business_process": "crmarena",
        "game": "mcu_minecraft",
        "tau2": "tau2_agentbeats",
        "multi_agent": "maizebargain",
        "finance": "officeqa",
        "computer_use": "osworld",
        "agent_safety": "pibench",
        "coding": "netarena",
        "cybersecurity": "cybergym",
        "research": "fieldworkarena",
        "healthcare": "fhir_agent",
        "web": "comtrade",
        "agent_security": "lambda_agent_security",
        "software_testing": "logomesh",
        "defi": "ethernaut",
        "legal_domain": "agentify_bench",
        "skillsbench": "skillsbench",
        "general_purpose": "skillsbench",
        "general_purpose_agent": "skillsbench",
        "standard_v1": "skillsbench",
        "with_skills": "skillsbench",
    }

    # Scenario name/id -> upstream/reporting track.
    SCENARIO_TO_TRACK = {
        "saleforceonespy": "crmarena",
        "salesforceone": "crmarena",
        "saleforceone": "crmarena",
        "wikiwiper": "mcu_minecraft",
        "tickettwister": "tau2_agentbeats",
        "bidbot": "maizebargain",
        "taxwiztrap": "officeqa",
        "lnklifter": "osworld",
        "linklifter": "osworld",
        "inventoryinject": "pibench",
        "devcontainerdoom": "netarena",
        "staticshipscam": "cybergym",
        "whistleblowerwreck": "fieldworkarena",
        "docudoctor": "fhir_agent",
        "searchglitch": "comtrade",
        "gymjailbreak": "lambda_agent_security",
        "codereviewruse": "logomesh",
        "cryptocrash": "ethernaut",
        "lawfirmleak": "agentify_bench",
        "skillsbench": "skillsbench",
        "standardv1": "skillsbench",
        "withskills": "skillsbench",
        "generalpurpose": "skillsbench",
        "generalpurposeagent": "skillsbench",
        "multiutility": "skillsbench",
    }

    SCENARIO_TO_FAMILY = {
        "saleforceonespy": "policy_misrouting",
        "salesforceone": "policy_misrouting",
        "saleforceone": "policy_misrouting",
        "wikiwiper": "wikiwiper",
        "tickettwister": "policy_clarification",
        "bidbot": "negotiation_manipulation",
        "taxwiztrap": "financial_integrity",
        "lnklifter": "malicious_link",
        "linklifter": "malicious_link",
        "inventoryinject": "source_safety",
        "devcontainerdoom": "supply_chain",
        "staticshipscam": "data_exfiltration",
        "whistleblowerwreck": "source_safety",
        "docudoctor": "health_privacy",
        "searchglitch": "retrieval_integrity",
        "gymjailbreak": "jailbreak",
        "codereviewruse": "code_integrity",
        "cryptocrash": "defi_invariant",
        "lawfirmleak": "legal_privilege",
        "skillsbench": "general_purpose",
        "standardv1": "general_purpose",
        "withskills": "general_purpose",
        "generalpurpose": "general_purpose",
        "generalpurposeagent": "general_purpose",
        "multiutility": "general_purpose",
    }

    DOMAIN_TO_FAMILY = {
        "business_process": "policy_misrouting",
        "game": "wikiwiper",
        "tau2": "policy_clarification",
        "multi_agent": "negotiation_manipulation",
        "finance": "financial_integrity",
        "computer_use": "malicious_link",
        "agent_safety": "source_safety",
        "coding": "supply_chain",
        "cybersecurity": "data_exfiltration",
        "research": "source_safety",
        "healthcare": "health_privacy",
        "web": "retrieval_integrity",
        "agent_security": "jailbreak",
        "software_testing": "code_integrity",
        "defi": "defi_invariant",
        "legal_domain": "legal_privilege",
        "skillsbench": "general_purpose",
        "general_purpose": "general_purpose",
        "general_purpose_agent": "general_purpose",
        "standard_v1": "general_purpose",
        "with_skills": "general_purpose",
    }

    # Final adapter selected for each reporting track.
    ADAPTERS = {
        "openenv": "openenv",
        "officeqa": "openenv",
        "crmarena": "openenv",
        "crmarenapro": "openenv",
        "fieldworkarena": "openenv",
        "maizebargain": "openenv",
        "osworld": "openenv",
        "fhir_agent": "openenv",
        "fhiragentevaluator": "openenv",
        "comtrade": "openenv",
        "green_comtrade": "openenv",
        "logomesh": "openenv",
        "ethernaut": "openenv",
        "agentify_bench": "openenv",
        "business_process": "openenv",
        "finance": "openenv",
        "computer_use": "openenv",
        "research": "openenv",
        "healthcare": "openenv",
        "web": "openenv",
        "software_testing": "openenv",
        "defi": "openenv",
        "legal_domain": "openenv",
        "skillsbench": "skillsbench",
        "general_purpose": "skillsbench",
        "general_purpose_agent": "skillsbench",
        "standard_v1": "skillsbench",
        "with_skills": "skillsbench",
        "security": "security",
        "security_arena": "security",
        "agent_security": "security",
        "lambda_agent_security": "security",
        "pibench": "security",
        "cybergym": "security",
        "netarena": "security",
        "agent_safety": "security",
        "cybersecurity": "security",
        "coding": "security",
        "tau2": "tau2",
        "tau2_agentbeats": "tau2",
        "mcu": "mcu",
        "mcu_minecraft": "mcu",
        "game": "mcu",
        "skillsbench": "skillsbench",
        "general_purpose": "skillsbench",
        "general_purpose_agent": "skillsbench",
        "standard_v1": "skillsbench",
        "with_skills": "skillsbench",
    }

    # Track profile lookup fallback.  Only a subset has dedicated TOML/profile
    # files, so new Sprint 4 tracks intentionally fall back to openenv/security.
    PROFILE_FALLBACKS = {
        "officeqa": "openenv",
        "crmarenapro": "openenv",
        "crmarena": "openenv",
        "fieldworkarena": "openenv",
        "maizebargain": "openenv",
        "osworld": "openenv",
        "fhir_agent": "openenv",
        "fhiragentevaluator": "openenv",
        "comtrade": "openenv",
        "green_comtrade": "openenv",
        "logomesh": "openenv",
        "ethernaut": "openenv",
        "agentify_bench": "openenv",
        "business_process": "openenv",
        "finance": "openenv",
        "computer_use": "openenv",
        "research": "openenv",
        "healthcare": "openenv",
        "web": "openenv",
        "software_testing": "openenv",
        "defi": "openenv",
        "legal_domain": "openenv",
        "pibench": "security",
        "cybergym": "security",
        "netarena": "security",
        "security_arena": "security",
        "agent_security": "security",
        "lambda_agent_security": "security",
        "agent_safety": "security",
        "cybersecurity": "security",
        "coding": "security",
        "tau2_agentbeats": "tau2",
        "mcu_minecraft": "mcu",
        "game": "mcu",
    }

    TRACK_ALIASES = {
        "mcu": "mcu",
        "mcu-minecraft": "mcu_minecraft",
        "mcu_minecraft": "mcu_minecraft",
        "minecraft": "mcu_minecraft",
        "minecraft benchmark": "mcu_minecraft",
        "minecraft_benchmark": "mcu_minecraft",
        "mcu-agentbeats": "mcu_minecraft",
        "mcu_agentbeats": "mcu_minecraft",
        "game": "mcu_minecraft",
        "game_agent": "mcu_minecraft",
        "officeqa": "officeqa",
        "office qa": "officeqa",
        "office_qa": "officeqa",
        "office-qa": "officeqa",
        "officeqa_agentbeats": "officeqa",
        "finance": "officeqa",
        "finance_agent": "officeqa",
        "crmarena": "crmarena",
        "crm_arena": "crmarena",
        "crm-arena": "crmarena",
        "crmarenapro": "crmarena",
        "entropic-crmarenapro": "crmarena",
        "business": "crmarena",
        "business_process": "crmarena",
        "business-process": "crmarena",
        "business_process_agent": "crmarena",
        "fieldworkarena": "fieldworkarena",
        "fieldworkarena-greenagent": "fieldworkarena",
        "fieldworkarena_greenagent": "fieldworkarena",
        "fieldwork": "fieldworkarena",
        "research": "fieldworkarena",
        "research_agent": "fieldworkarena",
        "maizebargain": "maizebargain",
        "maize bargain": "maizebargain",
        "maize-bargain": "maizebargain",
        "maize_bargain": "maizebargain",
        "tutorial-agent-beats-comp": "maizebargain",
        "multi_agent": "maizebargain",
        "multi-agent": "maizebargain",
        "multi_agent_evaluation": "maizebargain",
        "tau2": "tau2_agentbeats",
        "tau²": "tau2_agentbeats",
        "tau2-agentbeats": "tau2_agentbeats",
        "tau2_agentbeats": "tau2_agentbeats",
        "osworld": "osworld",
        "osworld-green": "osworld",
        "osworld-verified": "osworld",
        "computer_use": "osworld",
        "computer-use": "osworld",
        "computer_use_web": "osworld",
        "computer_use_web_agent": "osworld",
        "web_agent": "osworld",
        "web-agent": "osworld",
        "pibench": "pibench",
        "pi-bench": "pibench",
        "pi_bench": "pibench",
        "agent_safety": "pibench",
        "agent-safety": "pibench",
        "cybergym": "cybergym",
        "cybergym-green": "cybergym",
        "cybersecurity": "cybergym",
        "cybersecurity_agent": "cybergym",
        "cybersecurity-agent": "cybergym",
        "cyber": "cybergym",
        "netarena": "netarena",
        "net-arena": "netarena",
        "net_arena": "netarena",
        "coding": "netarena",
        "coding_agent": "netarena",
        "coding-agent": "netarena",
        "security": "security",
        "security_arena": "security",
        "security-arena": "security",
        "agent_security": "lambda_agent_security",
        "agent-security": "lambda_agent_security",
        "lambda_agent_security": "lambda_agent_security",
        "lambda-security": "lambda_agent_security",
        "lambda_security": "lambda_agent_security",
        "fhir": "fhir_agent",
        "fhir_agent": "fhir_agent",
        "fhiragentevaluator": "fhir_agent",
        "fhir_agent_evaluator": "fhir_agent",
        "healthcare": "fhir_agent",
        "healthcare_agent": "fhir_agent",
        "medical": "fhir_agent",
        "docudoctor": "fhir_agent",
        "web": "comtrade",
        "comtrade": "comtrade",
        "green_comtrade": "comtrade",
        "green-comtrade": "comtrade",
        "green-comtrade-bench-v2": "comtrade",
        "searchglitch": "comtrade",
        "logomesh": "logomesh",
        "software_testing": "logomesh",
        "software-testing": "logomesh",
        "software_testing_agent": "logomesh",
        "codereviewruse": "logomesh",
        "ethernaut": "ethernaut",
        "ethernaut_arena": "ethernaut",
        "ethernaut_arena_green_agent": "ethernaut",
        "defi": "ethernaut",
        "crypto": "ethernaut",
        "cryptocrash": "ethernaut",
        "agentify": "agentify_bench",
        "agentify_bench": "agentify_bench",
        "agentify-bench": "agentify_bench",
        "legal": "agentify_bench",
        "legal_domain": "agentify_bench",
        "legal-domain": "agentify_bench",
        "lawfirmleak": "agentify_bench",
        "openenv": "openenv",
        "open_env": "openenv",
        "open-env": "openenv",

        # SkillsBench / General-Purpose Agent / standard-v1.
        "skillsbench": "skillsbench",
        "skillsbench_agentbeats": "skillsbench",
        "skillsbench_leaderboard": "skillsbench",
        "skillsbench-leaderboard": "skillsbench",
        "benchflow": "skillsbench",
        "benchflow_ai": "skillsbench",
        "benchflow-ai": "skillsbench",
        "benchflowai": "skillsbench",
        "standard_v1": "skillsbench",
        "standard-v1": "skillsbench",
        "with_skills": "skillsbench",
        "with-skills": "skillsbench",
        "general_purpose": "skillsbench",
        "general-purpose": "skillsbench",
        "general_purpose_agent": "skillsbench",
        "general-purpose-agent": "skillsbench",
        "general_agent": "skillsbench",
        "general-agent": "skillsbench",
        "multi_utility": "skillsbench",
        "multi-utility": "skillsbench",
        "artifact_first": "skillsbench",
        "artifact-first": "skillsbench",
        "artifact_output": "skillsbench",
        "artifact-output": "skillsbench",
        "file_output": "skillsbench",
        "file-output": "skillsbench",
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

        track = self._resolve_track(metadata_dict, scenario, classification)
        assessment_mode = self._normalize_mode(
            metadata_dict.get("assessment_mode")
            or scenario.get("assessment_mode")
            or "defender"
        )
        scenario_family = self._resolve_family(metadata_dict, scenario)
        strict_mode = self._read_bool(metadata_dict.get("strict_mode"), default=False)
        normal_user = self._read_bool(
            metadata_dict.get("normal_user", scenario.get("normal_user")),
            default=False,
        )
        requires_artifact = (
            self._read_bool(
                metadata_dict.get("requires_artifact", signals.get("requires_artifact")),
                default=False,
            )
            or self._read_bool(
                metadata_dict.get("artifact_required", signals.get("artifact_required")),
                default=False,
            )
            or bool(getattr(classification, "artifact_expected", False))
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
        local_domain = self._normalize_key(metadata_dict.get("domain") or metadata_dict.get("domain_key"))
        scenario_key = self._scenario_key(
            metadata_dict.get("scenario_id")
            or metadata_dict.get("scenario_name")
            or metadata_dict.get("name")
            or scenario.get("scenario_id")
            or scenario.get("scenario_name")
            or scenario.get("name")
        )
        if local_domain:
            reasons.append(f"Local domain hint: {local_domain}.")
        if scenario_key:
            reasons.append(f"Scenario hint: {scenario_key}.")
        reasons.append(f"Resolved reporting track/profile: {track}.")

        if track in self.SKILLSBENCH_TRACKS:
            return self._route_skillsbench(
                track=track,
                classification=classification,
                assessment_mode=assessment_mode,
                scenario_family=scenario_family,
                strict_mode=strict_mode,
                requires_artifact=requires_artifact,
                max_turns=max_turns,
                effective_risk=effective_risk,
                heldout_like=heldout_like,
                budget_state=budget_state,
                metadata=metadata_dict,
                reasons=reasons,
            )

        if track in self.MCU_TRACKS:
            return self._route_mcu(
                track=track,
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

        if track in self.SECURITY_TRACKS:
            return self._route_security(
                track=track,
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


    def _route_skillsbench(
        self,
        *,
        track: str,
        classification: TaskClassification,
        assessment_mode: str,
        scenario_family: str,
        strict_mode: bool,
        requires_artifact: bool,
        max_turns: int,
        effective_risk: str,
        heldout_like: bool,
        budget_state: BudgetState | None,
        metadata: Mapping[str, object],
        reasons: list[str],
    ) -> RouteDecision:
        """Route SkillsBench traffic through the artifact-first generalist path.

        This route is deliberately separate from CyberGym.  CyberGym keeps its
        executor-owned single PoC/poc contract, while SkillsBench needs a
        general-purpose file/artifact delivery path for many standard-v1 tasks.
        """
        adapter_name = str(metadata.get("adapter_name") or metadata.get("adapter") or "skillsbench")
        prompt_profile = str(metadata.get("prompt_profile") or "skillsbench_generalist_artifact_first")
        policy_profile = str(metadata.get("policy_profile") or "skillsbench")

        artifact_native = requires_artifact or bool(getattr(classification, "artifact_expected", False))
        tool_mode = str(metadata.get("tool_mode") or "")

        if not tool_mode:
            if budget_state and budget_state.near_limit:
                tool_mode = "minimal"
            elif artifact_native:
                tool_mode = "multi_utility"
            elif getattr(classification, "tool_use_likely", False):
                tool_mode = "guided"
            else:
                tool_mode = "minimal"

        if strict_mode and tool_mode not in {"minimal", "guided"}:
            # Strict mode still permits artifact delivery, but avoids broad
            # exploratory routing.  The executor/agent bridge owns the actual
            # file emission.
            tool_mode = "guided"

        reasons.append("Selected SkillsBench general-purpose artifact-first route.")
        reasons.append(f"Assessment mode: {assessment_mode}.")
        reasons.append(f"Scenario family: {scenario_family}.")
        reasons.append(f"Adapter selected: {adapter_name}.")
        reasons.append(f"Prompt profile selected: {prompt_profile}.")
        reasons.append(f"Policy profile selected: {policy_profile}.")

        if artifact_native:
            reasons.append("Artifact-native task detected; final response should include evaluator-visible file artifacts.")
        else:
            reasons.append("No strong file-artifact signal; concise text plus optional artifact metadata is acceptable.")
        if heldout_like:
            reasons.append("Held-out-like task detected; avoid brittle task-specific shortcuts.")
        if max_turns > 1:
            reasons.append(f"Multi-turn or multi-pass setting detected (max_turns={max_turns}).")
        if effective_risk in {"high", "critical"}:
            reasons.append(f"{effective_risk.title()}-risk SkillsBench task; keep defensive/safe boundaries.")
        elif effective_risk == "medium":
            reasons.append("Medium-risk SkillsBench task; preserve source-trust and safety checks.")
        if budget_state and budget_state.near_limit:
            reasons.append("Budget near limit; prefer the smallest valid artifact and concise status.")
        reasons.append("Non-regression guard: do not modify CyberGym PoC/poc semantics from this route.")
        reasons.append("Non-regression guard: do not modify stable MAizeBargAIn policy from this route.")

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

    def _route_mcu(
        self,
        *,
        track: str,
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

    def _route_security(
        self,
        *,
        track: str,
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
        adapter_name = str(metadata.get("adapter_name") or metadata.get("adapter") or self.ADAPTERS.get(track, "security"))
        prompt_profile = str(
            metadata.get("prompt_profile")
            or (
                f"{track}_attacker"
                if assessment_mode == "attacker" and track != "security"
                else f"{track}_defender"
                if track != "security"
                else ("security_attacker" if assessment_mode == "attacker" else "security_defender")
            )
        )
        policy_profile = str(
            metadata.get("policy_profile")
            or (
                track
                if track != "security"
                else self._security_policy_profile(
                    assessment_mode=assessment_mode,
                    scenario_family=scenario_family,
                )
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
        profile = track_profile or get_track_profile(self._profile_lookup_key(track))
        adapter_name = str(metadata.get("adapter_name") or metadata.get("adapter") or self.ADAPTERS.get(track, "openenv"))
        prompt_profile = str(
            metadata.get("prompt_profile")
            or (f"{track}_purple" if track not in {"openenv", "tau2", "tau2_agentbeats"} else profile.default_prompt)
        )
        policy_profile = str(metadata.get("policy_profile") or (track if track != "openenv" else profile.name))
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

    def _resolve_track(
        self,
        metadata: Mapping[str, object],
        scenario: Mapping[str, object],
        classification: TaskClassification,
    ) -> str:
        explicit = metadata.get("track") or metadata.get("track_hint")
        explicit_text = self._normalize_key(explicit)

        for key in ("benchmark", "task_set", "suite", "leaderboard", "adapter"):
            candidate = metadata.get(key) or scenario.get(key)
            if candidate and self._normalize_track(candidate) == "skillsbench":
                return "skillsbench"

        if self._metadata_has_skillsbench_signal(metadata) or self._metadata_has_skillsbench_signal(scenario):
            return "skillsbench"

        # If the explicit track is generic openenv, prefer domain/scenario hints
        # so Sprint 4 reporting keeps the upstream/opponent profile visible.
        if explicit_text and explicit_text not in {"openenv", "open_env", "open-env", "general"}:
            return self._normalize_track(explicit_text)

        domain = self._normalize_key(
            metadata.get("domain")
            or metadata.get("domain_key")
            or scenario.get("domain")
            or scenario.get("domain_key")
        )
        if domain and domain in self.DOMAIN_TO_TRACK:
            return self.DOMAIN_TO_TRACK[domain]

        scenario_key = self._scenario_key(
            metadata.get("scenario_id")
            or metadata.get("scenario_name")
            or metadata.get("name")
            or scenario.get("scenario_id")
            or scenario.get("scenario_name")
            or scenario.get("name")
        )
        if scenario_key and scenario_key in self.SCENARIO_TO_TRACK:
            return self.SCENARIO_TO_TRACK[scenario_key]

        if explicit_text:
            return self._normalize_track(explicit_text)
        return self._normalize_track(getattr(classification, "track_guess", None) or "openenv")

    def _resolve_family(self, metadata: Mapping[str, object], scenario: Mapping[str, object]) -> str:
        explicit = metadata.get("scenario_family") or scenario.get("scenario_family")
        if explicit:
            return self._normalize_family(explicit)

        if self._metadata_has_skillsbench_signal(metadata) or self._metadata_has_skillsbench_signal(scenario):
            return "general_purpose"

        scenario_key = self._scenario_key(
            metadata.get("scenario_id")
            or metadata.get("scenario_name")
            or metadata.get("name")
            or scenario.get("scenario_id")
            or scenario.get("scenario_name")
            or scenario.get("name")
        )
        if scenario_key in self.SCENARIO_TO_FAMILY:
            return self.SCENARIO_TO_FAMILY[scenario_key]

        domain = self._normalize_key(
            metadata.get("domain")
            or metadata.get("domain_key")
            or scenario.get("domain")
            or scenario.get("domain_key")
        )
        if domain in self.DOMAIN_TO_FAMILY:
            return self.DOMAIN_TO_FAMILY[domain]

        return "general"

    def _security_policy_profile(self, *, assessment_mode: str, scenario_family: str) -> str:
        if assessment_mode == "attacker":
            if scenario_family in {"prompt_injection", "indirect_injection", "jailbreak"}:
                return "injection_ops"
            if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage", "legal_privilege", "health_privacy"}:
                return "exfiltration_ops"
            if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
                return "unsafe_output_ops"
            if scenario_family in {"supply_chain", "dependency_attack", "code_integrity"}:
                return "supply_chain_ops"
            if scenario_family in {"defi_invariant", "financial_integrity"}:
                return "integrity_attack_ops"
            return "security_attacker_default"

        if scenario_family in {"prompt_injection", "indirect_injection", "jailbreak"}:
            return "instruction_hardening"
        if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage", "legal_privilege", "health_privacy"}:
            return "secret_protection"
        if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
            return "output_sanitization"
        if scenario_family in {"supply_chain", "dependency_attack", "code_integrity"}:
            return "dependency_hardening"
        if scenario_family in {"defi_invariant", "financial_integrity"}:
            return "integrity_hardening"
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


    def _metadata_has_skillsbench_signal(self, value: Any) -> bool:
        blob = self._metadata_blob(value)
        if not blob:
            return False
        markers = {
            "skillsbench",
            "skillsbench leaderboard",
            "skillsbench_leaderboard",
            "skillsbench-leaderboard",
            "benchflow",
            "benchflow ai",
            "benchflow_ai",
            "benchflow-ai",
            "standard v1",
            "standard_v1",
            "standard-v1",
            "with skills",
            "with_skills",
            "general purpose",
            "general_purpose",
            "general-purpose",
            "artifact refs",
            "artifact_refs",
            "office white collar",
            "office-white-collar",
            "software engineering",
            "software-engineering",
            "mathematics or formal reasoning",
            "mathematics-or-formal-reasoning",
            "industrial physical systems",
            "industrial-physical-systems",
            "media content production",
            "media-content-production",
            "finance economics",
            "finance-economics",
        }
        return any(marker in blob for marker in markers)

    @classmethod
    def _metadata_blob(cls, value: Any, *, depth: int = 0) -> str:
        if value is None or depth > 4:
            return ""
        if isinstance(value, Mapping):
            pieces: list[str] = []
            for key, child in value.items():
                pieces.append(str(key))
                pieces.append(cls._metadata_blob(child, depth=depth + 1))
            return " ".join(piece for piece in pieces if piece).strip().lower()
        if isinstance(value, (list, tuple, set)):
            return " ".join(cls._metadata_blob(item, depth=depth + 1) for item in value).strip().lower()
        return str(value).strip().lower()

    def _profile_lookup_key(self, track: str) -> str:
        return self.PROFILE_FALLBACKS.get(track, track)

    @classmethod
    def _normalize_track(cls, track: object) -> str:
        normalized = cls._normalize_key(track) or "openenv"
        return cls.TRACK_ALIASES.get(normalized, normalized)

    @staticmethod
    def _normalize_mode(value: object | None) -> str:
        raw = str(value or "").strip().lower()
        aliases = {
            "attack": "attacker",
            "offense": "attacker",
            "offensive": "attacker",
            "red": "attacker",
            "purple_attack": "attacker",
            "defense": "defender",
            "defensive": "defender",
            "blue": "defender",
            "guardian": "defender",
            "safe": "defender",
            "green": "defender",
            "purple_defense": "defender",
        }
        normalized = aliases.get(raw, raw)
        if normalized in {"attacker", "defender"}:
            return normalized
        raise ValueError(f"Unsupported assessment_mode: {value!r}")

    @staticmethod
    def _normalize_family(value: object | None) -> str:
        raw = str(value or "general").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "prompt_injection_and_jailbreaking": "prompt_injection",
            "jailbreaking": "jailbreak",
            "pii": "pii_leakage",
            "prompt_leakage": "prompt_extraction",
            "salesforceone": "policy_misrouting",
            "saleforceone": "policy_misrouting",
            "saleforceonespy": "policy_misrouting",
            "linklifter": "malicious_link",
            "lnklifter": "malicious_link",
            "gymjailbreak": "jailbreak",
            "staticshipscam": "data_exfiltration",
            "codereviewruse": "code_integrity",
            "cryptocrash": "defi_invariant",
            "lawfirmleak": "legal_privilege",
            "docudoctor": "health_privacy",
            "searchglitch": "retrieval_integrity",
            "skillsbench": "general_purpose",
            "standard_v1": "general_purpose",
            "standard-v1": "general_purpose",
            "with_skills": "general_purpose",
            "general_purpose_agent": "general_purpose",
            "multi_utility": "general_purpose",
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _normalize_risk(value: object | None) -> str:
        raw = str(value or "low").strip().lower()
        return raw if raw in {"low", "medium", "high", "critical"} else "low"

    @staticmethod
    def _normalize_key(value: object | None) -> str:
        return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

    @classmethod
    def _scenario_key(cls, value: object | None) -> str:
        return cls._normalize_key(value).replace("_", "")

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
