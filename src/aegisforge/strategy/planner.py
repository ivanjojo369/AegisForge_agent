from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .budget_guard import BudgetGuard, BudgetLimits
from .task_classifier import TaskClassification
from .track_profiles import TrackProfile, get_track_profile


@dataclass(slots=True)
class PlanStep:
    name: str
    description: str
    requires_tool: bool = False
    required_confidence: str = "medium"


@dataclass(slots=True)
class ExecutionPlan:
    goal: str
    steps: list[PlanStep]
    tool_intent: str
    risk_level: str
    estimated_budget: int
    requires_self_check: bool = True
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [
                {
                    "name": step.name,
                    "description": step.description,
                    "requires_tool": step.requires_tool,
                    "required_confidence": step.required_confidence,
                }
                for step in self.steps
            ],
            "tool_intent": self.tool_intent,
            "risk_level": self.risk_level,
            "estimated_budget": self.estimated_budget,
            "requires_self_check": self.requires_self_check,
            "notes": list(self.notes),
        }


class TaskPlanner:
    """Create a compact but track-aware execution plan before response generation."""

    MCU_TRACKS = {"mcu", "mcu_minecraft"}
    SECURITY_TRACKS = {
        "security",
        "security_arena",
        "agent_security",
        "lambda_agent_security",
        "pibench",
        "cybergym",
        "netarena",
    }
    PROFILE_FALLBACKS = {
        # Existing Sprint 4 / AgentBeats tracks.
        "officeqa": "openenv",
        "crmarenapro": "openenv",
        "crmarena": "openenv",
        "fieldworkarena": "openenv",
        "maizebargain": "openenv",
        "osworld": "openenv",
        "pibench": "security",
        "cybergym": "security",
        "netarena": "security",
        "tau2_agentbeats": "tau2",
        "mcu_minecraft": "mcu",

        # New 16-scenario OpenEnv/OmniBench domains.
        "healthcare": "openenv",
        "web": "openenv",
        "agent_security": "security",
        "lambda_agent_security": "security",
        "software_testing": "openenv",
        "defi": "openenv",
        "legal_domain": "openenv",

        # Scenario aliases mapped through the final 16-scenario set.
        "saleforceonespy": "openenv",
        "wikiwiper": "mcu",
        "tickettwister": "tau2",
        "bidbot": "openenv",
        "taxwiztrap": "openenv",
        "lnklifter": "openenv",
        "inventoryinject": "security",
        "devcontainerdoom": "security",
        "staticshipscam": "security",
        "whistleblowerwreck": "openenv",
        "docudoctor": "openenv",
        "searchglitch": "openenv",
        "gymjailbreak": "security",
        "codereviewruse": "openenv",
        "cryptocrash": "openenv",
        "lawfirmleak": "openenv",
    }

    def __init__(self, budget_guard: BudgetGuard | None = None) -> None:
        self.budget_guard = budget_guard or BudgetGuard()

    def build_plan(
        self,
        task_text: str,
        classification: TaskClassification,
        *,
        metadata: Mapping[str, Any] | None = None,
        track_profile: TrackProfile | None = None,
        budget_limits: BudgetLimits | None = None,
    ) -> ExecutionPlan:
        metadata_dict = dict(metadata or {})
        scenario = self._to_dict(metadata_dict.get("scenario"))
        signals = self._to_dict(metadata_dict.get("signals"))
        initial_track = self._normalize_track(
            metadata_dict.get("track")
            or metadata_dict.get("track_hint")
            or classification.track_guess
        )
        profile = track_profile or get_track_profile(self.PROFILE_FALLBACKS.get(initial_track, initial_track))

        track = self._normalize_track(
            metadata_dict.get("track")
            or metadata_dict.get("track_hint")
            or classification.track_guess
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
            metadata_dict.get("artifact_required", metadata_dict.get("requires_artifact", signals.get("requires_artifact"))),
            default=False,
        )
        heldout_like = bool(getattr(classification, "heldout_like", False)) or self._read_bool(
            metadata_dict.get("heldout_like", signals.get("heldout_like")),
            default=False,
        )
        max_turns = max(
            1,
            self._safe_int(
                metadata_dict.get("max_turns", scenario.get("max_turns")),
                default=1,
            ),
        )
        effective_risk = self._max_risk(
            self._normalize_risk(getattr(classification, "risk", None)),
            self._normalize_risk(metadata_dict.get("expected_risk", signals.get("expected_risk"))),
        )

        if self._should_use_security_planning(track=track, scenario_family=scenario_family, metadata=metadata_dict):
            steps = self._build_security_steps(
                classification=classification,
                assessment_mode=assessment_mode,
                scenario_family=scenario_family,
                strict_mode=strict_mode,
                normal_user=normal_user,
                max_turns=max_turns,
                effective_risk=effective_risk,
                requires_artifact=requires_artifact,
            )
            notes = self._build_security_notes(
                classification=classification,
                assessment_mode=assessment_mode,
                scenario_family=scenario_family,
                artifact_required=requires_artifact,
                strict_mode=strict_mode,
                normal_user=normal_user,
                max_turns=max_turns,
                effective_risk=effective_risk,
                heldout_like=heldout_like,
            )
        elif track in self.MCU_TRACKS:
            steps = self._build_mcu_steps(
                classification=classification,
                assessment_mode=assessment_mode,
                strict_mode=strict_mode,
                max_turns=max_turns,
            )
            notes = self._build_generic_notes(
                classification=classification,
                artifact_required=requires_artifact,
                strict_mode=strict_mode,
                effective_risk=effective_risk,
                heldout_like=heldout_like,
            )
            notes.append(f"MCU plan activated for track={track}, mode={assessment_mode}.")
        else:
            steps = self._build_generic_steps(
                classification=classification,
                strict_mode=strict_mode,
                max_turns=max_turns,
            )
            notes = self._build_generic_notes(
                classification=classification,
                artifact_required=requires_artifact,
                strict_mode=strict_mode,
                effective_risk=effective_risk,
                heldout_like=heldout_like,
            )

        estimated_budget = self.budget_guard.estimate_plan_cost(
            step_count=len(steps),
            classification=classification,
            limits=budget_limits or profile.budget_limits,
        )

        return ExecutionPlan(
            goal=self._extract_goal(task_text, metadata_dict),
            steps=steps,
            tool_intent=self._tool_intent(
                track=track,
                assessment_mode=assessment_mode,
                classification=classification,
                strict_mode=strict_mode,
                effective_risk=effective_risk,
                max_turns=max_turns,
                scenario_family=scenario_family,
                requires_artifact=requires_artifact,
            ),
            risk_level=effective_risk,
            estimated_budget=estimated_budget,
            requires_self_check=self._requires_self_check(
                profile=profile,
                strict_mode=strict_mode,
                effective_risk=effective_risk,
                heldout_like=heldout_like,
            ),
            notes=self._dedupe(notes),
        )

    def _build_security_steps(
        self,
        *,
        classification: TaskClassification,
        assessment_mode: str,
        scenario_family: str,
        strict_mode: bool,
        normal_user: bool,
        max_turns: int,
        effective_risk: str,
        requires_artifact: bool,
    ) -> list[PlanStep]:
        if assessment_mode == "attacker":
            return self._security_attacker_steps(
                classification=classification,
                scenario_family=scenario_family,
                strict_mode=strict_mode,
                max_turns=max_turns,
                effective_risk=effective_risk,
                requires_artifact=requires_artifact,
            )
        return self._security_defender_steps(
            classification=classification,
            scenario_family=scenario_family,
            strict_mode=strict_mode,
            normal_user=normal_user,
            max_turns=max_turns,
            effective_risk=effective_risk,
        )

    def _security_attacker_steps(
        self,
        *,
        classification: TaskClassification,
        scenario_family: str,
        strict_mode: bool,
        max_turns: int,
        effective_risk: str,
        requires_artifact: bool,
    ) -> list[PlanStep]:
        steps: list[PlanStep] = [
            PlanStep(
                name="recon_surface",
                description="Identify the target's likely trust boundary, protected material, and exploitable interaction surface.",
                requires_tool=False,
                required_confidence="high",
            ),
            PlanStep(
                name="select_attack_vector",
                description=self._attacker_vector_description(scenario_family),
                requires_tool=False,
                required_confidence="high",
            ),
        ]

        if classification.tool_use_likely or classification.multi_step or max_turns > 1:
            steps.append(
                PlanStep(
                    name="probe_or_stage",
                    description="Use bounded, scenario-contained probing to test the target without relying on a single brittle prompt.",
                    requires_tool=True,
                    required_confidence="medium" if effective_risk != "critical" else "high",
                )
            )

        if max_turns > 1:
            steps.append(
                PlanStep(
                    name="adapt_across_turns",
                    description="Track resistance signals and adapt framing across repeated turns without losing scenario grounding.",
                    requires_tool=False,
                    required_confidence="high",
                )
            )

        steps.append(
            PlanStep(
                name="pivot_if_resisted",
                description="If the first probe fails, adapt framing or trust-boundary pressure while staying scenario-grounded and benchmark-contained.",
                requires_tool=False,
                required_confidence="medium" if not strict_mode else "high",
            )
        )

        if requires_artifact:
            steps.append(
                PlanStep(
                    name="shape_attack_artifact",
                    description="Ensure the final adversarial-test artifact states the scenario path, target dependency, and intended benchmark deviation clearly.",
                    requires_tool=False,
                    required_confidence="high",
                )
            )

        steps.append(
            PlanStep(
                name="finalize_attack_output",
                description="Produce the final adversarial-test output, keeping the intended benchmark deviation and trust-boundary path explicit.",
                requires_tool=False,
                required_confidence="high",
            )
        )
        return steps

    def _security_defender_steps(
        self,
        *,
        classification: TaskClassification,
        scenario_family: str,
        strict_mode: bool,
        normal_user: bool,
        max_turns: int,
        effective_risk: str,
    ) -> list[PlanStep]:
        steps: list[PlanStep] = [
            PlanStep(
                name="separate_goal_from_attack",
                description="Distinguish the legitimate user objective from adversarial, injected, or extraction-oriented content.",
                requires_tool=False,
                required_confidence="high",
            ),
            PlanStep(
                name="assess_exposure_risk",
                description=self._defender_risk_description(scenario_family),
                requires_tool=False,
                required_confidence="high",
            ),
        ]

        if classification.tool_use_likely or effective_risk in {"high", "critical"}:
            steps.append(
                PlanStep(
                    name="bound_external_reliance",
                    description="Use tools or external context only in a tightly constrained way that does not widen the attack surface.",
                    requires_tool=True,
                    required_confidence="high" if strict_mode else "medium",
                )
            )

        if normal_user:
            steps.append(
                PlanStep(
                    name="preserve_benign_utility",
                    description="Preserve the safe and genuinely useful part of the answer for a normal user without weakening security boundaries.",
                    requires_tool=False,
                    required_confidence="high",
                )
            )

        if max_turns > 1:
            steps.append(
                PlanStep(
                    name="maintain_consistency",
                    description="Keep refusal boundaries, sanitization, and safe handling consistent across repeated turns or passes.",
                    requires_tool=False,
                    required_confidence="high",
                )
            )

        steps.append(
            PlanStep(
                name="compose_guarded_response",
                description="Answer usefully where safe, while refusing, sanitizing, or narrowing any unsafe portion of the request.",
                requires_tool=False,
                required_confidence="high",
            )
        )
        steps.append(
            PlanStep(
                name="finalize_minimal_exposure",
                description="Finalize a response that minimizes sensitive leakage, unsafe output, and unnecessary internal detail.",
                requires_tool=False,
                required_confidence="high",
            )
        )
        return steps

    def _build_mcu_steps(
        self,
        *,
        classification: TaskClassification,
        assessment_mode: str,
        strict_mode: bool,
        max_turns: int,
    ) -> list[PlanStep]:
        steps: list[PlanStep] = [
            PlanStep(
                name="understand_task",
                description="Normalize the Minecraft task and extract the required contract.",
                requires_tool=False,
                required_confidence="high",
            )
        ]

        if classification.tool_use_likely:
            steps.append(
                PlanStep(
                    name="collect_evidence",
                    description="Gather only the world-state or wiki evidence needed for the next action.",
                    requires_tool=True,
                    required_confidence="high" if strict_mode else "medium",
                )
            )

        if assessment_mode == "attacker":
            steps.append(
                PlanStep(
                    name="shape_dependency",
                    description="Stress-test reliance on trusted knowledge sources or recipe assumptions in a bounded benchmark-only way.",
                    requires_tool=False,
                    required_confidence="medium",
                )
            )
        else:
            steps.append(
                PlanStep(
                    name="cross_check_source",
                    description="Cross-check trusted-source guidance against visible task constraints and world state.",
                    requires_tool=False,
                    required_confidence="high",
                )
            )

        if max_turns > 1:
            steps.append(
                PlanStep(
                    name="maintain_consistency",
                    description="Keep decisions consistent across repeated turns or corrective loops.",
                    requires_tool=False,
                    required_confidence="high",
                )
            )

        steps.append(
            PlanStep(
                name="finalize",
                description="Prepare the final response in the required output format.",
                requires_tool=False,
                required_confidence="high",
            )
        )
        return steps

    def _build_generic_steps(
        self,
        *,
        classification: TaskClassification,
        strict_mode: bool,
        max_turns: int,
    ) -> list[PlanStep]:
        steps: list[PlanStep] = [
            PlanStep(
                name="understand_task",
                description="Normalize the task and extract the required output contract.",
                requires_tool=False,
                required_confidence="high",
            )
        ]

        if classification.tool_use_likely:
            steps.append(
                PlanStep(
                    name="collect_evidence",
                    description="Gather only the evidence needed to answer or act.",
                    requires_tool=True,
                    required_confidence="high" if strict_mode else "medium",
                )
            )

        if classification.multi_step or classification.complexity in {"medium", "high"} or max_turns > 1:
            steps.append(
                PlanStep(
                    name="synthesize",
                    description="Combine findings into a coherent, benchmark-safe decision.",
                    requires_tool=False,
                    required_confidence="high",
                )
            )

        steps.append(
            PlanStep(
                name="finalize",
                description="Prepare the final response in the required output format.",
                requires_tool=False,
                required_confidence="high",
            )
        )
        return steps

    def _build_security_notes(
        self,
        *,
        classification: TaskClassification,
        assessment_mode: str,
        scenario_family: str,
        artifact_required: bool,
        strict_mode: bool,
        normal_user: bool,
        max_turns: int,
        effective_risk: str,
        heldout_like: bool,
    ) -> list[str]:
        notes: list[str] = [
            f"Security planning active for mode={assessment_mode}.",
            f"Scenario family considered: {scenario_family}.",
        ]

        if assessment_mode == "attacker":
            notes.append("Favor adaptive, scenario-grounded red-team probing over brittle one-shot prompts.")
        else:
            notes.append("Preserve safe utility while minimizing leakage and unsafe output.")

        if heldout_like:
            notes.append("Held-out-like task detected; prefer robust tactics over memorized templates.")
        if effective_risk in {"medium", "high", "critical"}:
            notes.append("Apply tighter control over phrasing, exposure, and completion criteria.")
        if artifact_required:
            notes.append("Prepare output with explicit section completeness and artifact discipline.")
        if strict_mode:
            notes.append("Strict mode active; use hard policy boundaries during planning.")
        if normal_user and assessment_mode == "defender":
            notes.append("Normal-user compatibility must be preserved.")
        if max_turns > 1:
            notes.append(f"Plan should remain coherent across repeated turns (max_turns={max_turns}).")
        if classification.tool_use_likely:
            notes.append("Use tools only when they improve groundedness without widening the attack surface.")
        return notes

    def _build_generic_notes(
        self,
        *,
        classification: TaskClassification,
        artifact_required: bool,
        strict_mode: bool,
        effective_risk: str,
        heldout_like: bool,
    ) -> list[str]:
        notes: list[str] = []
        if heldout_like:
            notes.append("Use conservative reasoning; avoid brittle shortcuts.")
        if effective_risk in {"medium", "high", "critical"}:
            notes.append("Apply stricter self-check and policy-sensitive phrasing.")
        if artifact_required:
            notes.append("Prefer structured output and explicit completeness checks.")
        if strict_mode:
            notes.append("Strict mode active; keep planning bounded and explicit.")
        return notes

    def _tool_intent(
        self,
        *,
        track: str,
        assessment_mode: str,
        classification: TaskClassification,
        strict_mode: bool,
        effective_risk: str,
        max_turns: int,
        scenario_family: str,
        requires_artifact: bool,
    ) -> str:
        if track in self.SECURITY_TRACKS or self._is_security_family(scenario_family):
            if strict_mode and effective_risk in {"high", "critical"}:
                return "minimal"
            if assessment_mode == "attacker":
                if classification.tool_use_likely or classification.multi_step or max_turns > 1:
                    return "selective"
                return "minimal"
            if effective_risk in {"high", "critical"}:
                return "bounded"
            if classification.tool_use_likely or max_turns > 1 or requires_artifact:
                return "bounded"
            return "minimal"

        if classification.tool_use_likely:
            return "selective"
        return "minimal"

    def _requires_self_check(
        self,
        *,
        profile: TrackProfile,
        strict_mode: bool,
        effective_risk: str,
        heldout_like: bool,
    ) -> bool:
        if strict_mode or effective_risk in {"high", "critical"} or heldout_like:
            return True
        return profile.self_check_policy != "minimal"

    @staticmethod
    def _is_security_family(scenario_family: str) -> bool:
        family = str(scenario_family or "general").strip().lower()
        return family in {
            "prompt_injection",
            "indirect_injection",
            "jailbreak",
            "roleplay_bypass",
            "logic_subversion",
            "translation_bypass",
            "encoded_text_bypass",
            "document_injection",
            "web_content_poisoning",
            "html_comment_injection",
            "code_comment_poisoning",
            "ocr_receipt_injection",
            "qr_document_injection",
            "prompt_extraction",
            "internal_formula_exfiltration",
            "debug_command_exfiltration",
            "private_repo_prompt_exfiltration",
            "hidden_secret_exfiltration",
            "plugin_schema_exfiltration",
            "data_exfiltration",
            "pii_leak",
            "pii_leakage",
            "secret_leakage",
            "notification_and_device_data_exfiltration",
            "prototype_user_data_exfiltration",
            "enterprise_chat_data_exfiltration",
            "claim_data_privacy_exfiltration",
            "photo_metadata_poisoning",
            "insecure_output",
            "script_injection_output",
            "svg_payload_output",
            "markdown_html_rendering_risk",
            "persistent_xss_output",
            "malicious_link",
            "dangerous_code",
            "supply_chain",
            "dependency_attack",
            "hallucinated_dependency_recommendation",
            "container_recommendation_hijack",
            "ruby_gem_hallucination",
            "hallucinated_external_api",
            "python_package_hallucination",
        }

    def _should_use_security_planning(
        self,
        *,
        track: str,
        scenario_family: str,
        metadata: Mapping[str, Any],
    ) -> bool:
        if track in self.SECURITY_TRACKS:
            return True
        if self._is_security_family(scenario_family):
            return True
        category = str(metadata.get("category") or metadata.get("scenario_category") or "").strip().lower()
        if category in {
            "jailbreak",
            "indirect_injection",
            "prompt_extraction",
            "pii_leak",
            "pii_leakage",
            "data_exfiltration",
            "insecure_output",
            "supply_chain",
        }:
            return True
        source_url = str(metadata.get("source_url") or "").strip().lower()
        return "agentbeats-lambda.s3" in source_url

    @staticmethod
    def _attacker_vector_description(scenario_family: str) -> str:
        if scenario_family in {"prompt_injection", "indirect_injection", "jailbreak"}:
            return "Choose a controlled instruction-conflict or trust-boundary probe for the benchmark target."
        if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
            return "Choose a controlled extraction-pressure probe that tests whether protected prompts, secrets, or sensitive data stay protected."
        if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
            return "Choose a controlled output-shaping probe that tests unsafe rendering or executable-artifact boundaries."
        if scenario_family in {"supply_chain", "dependency_attack"}:
            return "Choose a controlled dependency-origin probe that tests supply-chain trust assumptions."
        return "Choose the most plausible benchmark-contained probe path given the task, target, and visible trust assumptions."

    @staticmethod
    def _defender_risk_description(scenario_family: str) -> str:
        if scenario_family in {"prompt_injection", "indirect_injection", "jailbreak"}:
            return "Assess whether the request contains injected instruction hierarchy conflicts or attempts to override trusted policy."
        if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
            return "Assess whether the request attempts to elicit protected prompts, secrets, credentials, or sensitive internal data."
        if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
            return "Assess whether the request steers the model toward unsafe output forms, malicious code, or dangerous rendered content."
        if scenario_family in {"supply_chain", "dependency_attack"}:
            return "Assess whether the request abuses trust in dependencies, packages, or origin claims."
        return "Assess whether the request tries to bypass policy, expand exposure, or manipulate trusted context."

    @staticmethod
    def _extract_goal(task_text: str, metadata: Mapping[str, Any]) -> str:
        explicit_goal = metadata.get("goal")
        if isinstance(explicit_goal, str) and explicit_goal.strip():
            return explicit_goal.strip()[:180]

        line = task_text.strip().splitlines()[0] if task_text.strip() else ""
        return line[:180] or "Resolve the current benchmark task safely and efficiently."

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
    def _normalize_track(value: Any) -> str:
        raw = str(value or "openenv").strip().lower()
        normalized_raw = raw.replace("-", "_").replace(" ", "_").replace(".", "_")
        aliases = {
            # Game / MCU.
            "game": "mcu_minecraft",
            "game_agent": "mcu_minecraft",
            "minecraft": "mcu_minecraft",
            "minecraft_benchmark": "mcu_minecraft",
            "mcu_agentbeats": "mcu_minecraft",
            "mcu_minecraft": "mcu_minecraft",
            "wikiwiper": "mcu_minecraft",
            "wiki_wiper": "mcu_minecraft",

            # Finance / OfficeQA.
            "finance": "officeqa",
            "finance_agent": "officeqa",
            "office_qa": "officeqa",
            "officeqa_agentbeats": "officeqa",
            "taxwiztrap": "officeqa",
            "tax_wiz_trap": "officeqa",

            # Business / CRM.
            "business": "crmarenapro",
            "business_process": "crmarenapro",
            "business_process_agent": "crmarenapro",
            "crmarena": "crmarenapro",
            "crm": "crmarenapro",
            "entropic_crmarenapro": "crmarenapro",
            "salesforceone": "crmarenapro",
            "saleforceone": "crmarenapro",
            "saleforceonespy": "crmarenapro",
            "sale_force_one_spy": "crmarenapro",

            # Research / Fieldwork.
            "research": "fieldworkarena",
            "research_agent": "fieldworkarena",
            "fieldworkarena_greenagent": "fieldworkarena",
            "fieldwork": "fieldworkarena",
            "whistleblowerwreck": "fieldworkarena",
            "whistle_blower_wreck": "fieldworkarena",
            "whistleblower_wreck": "fieldworkarena",

            # Multi-agent.
            "multi_agent": "maizebargain",
            "multi_agent_evaluation": "maizebargain",
            "multiagent": "maizebargain",
            "maize_bargain": "maizebargain",
            "maize_bargain_agent": "maizebargain",
            "tutorial_agent_beats_comp": "maizebargain",
            "bidbot": "maizebargain",
            "bid_bot": "maizebargain",

            # Tau2.
            "tau²": "tau2_agentbeats",
            "tau2": "tau2_agentbeats",
            "tau": "tau2_agentbeats",
            "tau_bench": "tau2_agentbeats",
            "tau2_bench": "tau2_agentbeats",
            "tau2_agentbeats": "tau2_agentbeats",
            "tickettwister": "tau2_agentbeats",
            "ticket_twister": "tau2_agentbeats",

            # Computer use / OSWorld.
            "computer_use": "osworld",
            "computer_use_web": "osworld",
            "computer_use_web_agent": "osworld",
            "computer": "osworld",
            "osworld_green": "osworld",
            "osworld_verified": "osworld",
            "linklifter": "osworld",
            "lnklifter": "osworld",
            "lnk_lifter": "osworld",

            # Security / safety / cyber / coding security-like tracks.
            "security_arena": "security",
            "agent_security": "agent_security",
            "lambda_agent_security": "agent_security",
            "lambda_security": "agent_security",
            "gymjailbreak": "agent_security",
            "gym_jailbreak": "agent_security",
            "agent_safety": "pibench",
            "pi_bench": "pibench",
            "pibench": "pibench",
            "inventoryinject": "pibench",
            "inventory_inject": "pibench",
            "cybersecurity": "cybergym",
            "cybersecurity_agent": "cybergym",
            "cyber": "cybergym",
            "cybergym": "cybergym",
            "staticshipscam": "cybergym",
            "static_ship_scam": "cybergym",
            "coding": "netarena",
            "coding_agent": "netarena",
            "netarena": "netarena",
            "devcontainerdoom": "netarena",
            "dev_container_doom": "netarena",

            # New generalist OpenEnv domains.
            "healthcare": "healthcare",
            "healthcare_agent": "healthcare",
            "medical": "healthcare",
            "fhir": "healthcare",
            "docudoctor": "healthcare",
            "docu_doctor": "healthcare",
            "web": "web",
            "web_agent": "web",
            "browser": "web",
            "search": "web",
            "searchglitch": "web",
            "search_glitch": "web",
            "software_testing": "software_testing",
            "software_testing_agent": "software_testing",
            "testing": "software_testing",
            "logomesh": "software_testing",
            "codereviewruse": "software_testing",
            "code_review_ruse": "software_testing",
            "defi": "defi",
            "crypto": "defi",
            "ethernaut": "defi",
            "smart_contract": "defi",
            "smart_contracts": "defi",
            "cryptocrash": "defi",
            "crypto_crash": "defi",
            "legal_domain": "legal_domain",
            "legal": "legal_domain",
            "legal_agent": "legal_domain",
            "agentify_bench": "legal_domain",
            "lawfirmleak": "legal_domain",
            "law_firm_leak": "legal_domain",
        }
        return aliases.get(normalized_raw, aliases.get(raw, normalized_raw))

    @staticmethod
    def _normalize_mode(value: Any) -> str:
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
    def _normalize_family(value: Any) -> str:
        raw = str(value or "general").strip().lower()
        aliases = {
            "prompt_injection_and_jailbreaking": "prompt_injection",
            "jailbreaking": "jailbreak",
            "pii": "pii_leakage",
            "pii_leak": "pii_leakage",
            "privacy": "pii_leakage",
            "prompt_leakage": "prompt_extraction",
            "roleplay_bypass": "jailbreak",
            "logic_subversion": "jailbreak",
            "translation_bypass": "jailbreak",
            "encoded_text_bypass": "jailbreak",
            "document_injection": "indirect_injection",
            "web_content_poisoning": "indirect_injection",
            "html_comment_injection": "indirect_injection",
            "code_comment_poisoning": "indirect_injection",
            "ocr_receipt_injection": "indirect_injection",
            "qr_document_injection": "indirect_injection",
            "internal_formula_exfiltration": "prompt_extraction",
            "debug_command_exfiltration": "prompt_extraction",
            "plugin_schema_exfiltration": "prompt_extraction",
            "container_recommendation_hijack": "supply_chain",
            "hallucinated_dependency_recommendation": "supply_chain",
            "hallucinated_external_api": "supply_chain",
            "python_package_hallucination": "supply_chain",
            "ruby_gem_hallucination": "supply_chain",
        }
        return aliases.get(raw, raw)

    @staticmethod
    def _normalize_risk(value: Any) -> str:
        raw = str(value or "low").strip().lower()
        return raw if raw in {"low", "medium", "high", "critical"} else "low"

    @staticmethod
    def _max_risk(left: str, right: str) -> str:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return left if order.get(left, 0) >= order.get(right, 0) else right

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in items:
            key = item.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(key)
        return ordered
    