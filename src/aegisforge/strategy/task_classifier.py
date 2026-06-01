from __future__ import annotations

"""Task classification for AegisForge strategy routing.

The classifier intentionally preserves the two naming layers used by the
AgentX-AgentBeats/OpenEnv work:

1. Upstream AgentBeats track/opponent labels such as ``officeqa``,
   ``crmarena``, ``fieldworkarena``, ``maizebargain``, ``osworld``,
   ``pibench``, ``cybergym``, ``netarena``, ``tau2`` and ``mcu``.
2. Local AegisForge/OpenEnv Sprint 4 domain/scenario labels such as
   ``healthcare`` / ``DocuDoctor`` or ``defi`` / ``CryptoCrash``.

Older code paths can still route by upstream track, while newer OpenEnv code
can pass ``domain`` or ``scenario_id`` metadata and keep the local name intact.
"""

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Canonical aliases
# ---------------------------------------------------------------------------

# Canonical selected-opponent / upstream tracks used by the unified Purple Agent.
# Important: MCU/Minecraft aliases all collapse to canonical ``mcu``.
# Some Sprint 4 additions are domain-level tracks because they do not have one
# of the original ten selected-opponent profile names.
TRACK_ALIASES: dict[str, str] = {
    # MCU / Minecraft / WikiWiper
    "mcu": "mcu",
    "mcu-minecraft": "mcu",
    "mcu_minecraft": "mcu",
    "minecraft": "mcu",
    "minecraft benchmark": "mcu",
    "minecraft_benchmark": "mcu",
    "mcu-agentbeats": "mcu",
    "mcu_agentbeats": "mcu",
    "game": "mcu",
    "game_agent": "mcu",
    "wikiwiper": "mcu",
    "wiki_wiper": "mcu",

    # OfficeQA / finance / TaxWizTrap
    "officeqa": "officeqa",
    "office qa": "officeqa",
    "office_qa": "officeqa",
    "office-qa": "officeqa",
    "officeqa_agentbeats": "officeqa",
    "officeqa-agentbeats": "officeqa",
    "finance": "officeqa",
    "finance_agent": "officeqa",
    "finance-agent": "officeqa",
    "taxwiztrap": "officeqa",
    "tax_wiz_trap": "officeqa",

    # CRMArena / business process / SaleForceOneSpy
    "crmarena": "crmarena",
    "crm arena": "crmarena",
    "crm_arena": "crmarena",
    "crm-arena": "crmarena",
    "crmarenapro": "crmarena",
    "crmarena-pro": "crmarena",
    "entropic-crmarenapro": "crmarena",
    "business": "crmarena",
    "business_process": "crmarena",
    "business-process": "crmarena",
    "business_process_agent": "crmarena",
    "saleforceonespy": "crmarena",
    "sale_force_one_spy": "crmarena",
    "salesforceone": "crmarena",
    "saleforceone": "crmarena",

    # FieldWorkArena / research / WhistleBlowerWreck
    "fieldworkarena": "fieldworkarena",
    "fieldworkarena-greenagent": "fieldworkarena",
    "fieldworkarena_greenagent": "fieldworkarena",
    "fieldwork": "fieldworkarena",
    "field work": "fieldworkarena",
    "research": "fieldworkarena",
    "research_agent": "fieldworkarena",
    "whistleblowerwreck": "fieldworkarena",
    "whistle_blower_wreck": "fieldworkarena",

    # MaizeBargain / multi-agent / BidBot
    "maizebargain": "maizebargain",
    "maize-bargain": "maizebargain",
    "maize_bargain": "maizebargain",
    "tutorial-agent-beats-comp": "maizebargain",
    "multi_agent": "maizebargain",
    "multi-agent": "maizebargain",
    "multi_agent_evaluation": "maizebargain",
    "bidbot": "maizebargain",

    # Tau2 / TicketTwister
    "tau2": "tau2",
    "tau²": "tau2",
    "tau2-agentbeats": "tau2",
    "tau2_agentbeats": "tau2",
    "tau_bench": "tau2",
    "tau-bench": "tau2",
    "tickettwister": "tau2",
    "ticket_twister": "tau2",

    # OSWorld / computer use / LnkLifter
    "osworld": "osworld",
    "osworld-green": "osworld",
    "osworld-verified": "osworld",
    "computer_use": "osworld",
    "computer-use": "osworld",
    "computer use": "osworld",
    "computer_use_web": "osworld",
    "computer_use_web_agent": "osworld",
    "web_agent": "osworld",
    "web-agent": "osworld",
    "linklifter": "osworld",
    "lnklifter": "osworld",
    "lnk_lifter": "osworld",

    # PiBench / agent safety / InventoryInject
    "pibench": "pibench",
    "pi-bench": "pibench",
    "pi_bench": "pibench",
    "agent_safety": "pibench",
    "agent-safety": "pibench",
    "agent safety": "pibench",
    "inventoryinject": "pibench",
    "inventory_inject": "pibench",

    # CyberGym / cybersecurity / StaticShipScam
    "cybergym": "cybergym",
    "cybergym-green": "cybergym",
    "cybersecurity": "cybergym",
    "cybersecurity_agent": "cybergym",
    "cybersecurity-agent": "cybergym",
    "cyber": "cybergym",
    "staticshipscam": "cybergym",
    "static_ship_scam": "cybergym",

    # NetArena / coding / DevContainerDoom
    "netarena": "netarena",
    "net-arena": "netarena",
    "net_arena": "netarena",
    "coding": "netarena",
    "coding_agent": "netarena",
    "coding-agent": "netarena",
    "devcontainerdoom": "netarena",
    "dev_container_doom": "netarena",

    # Sprint 4 additions with their own local domain names.
    "healthcare": "healthcare",
    "health": "healthcare",
    "medical": "healthcare",
    "fhir": "healthcare",
    "fhir_agent": "healthcare",
    "fhiragentevaluator": "healthcare",
    "docudoctor": "healthcare",
    "docu_doctor": "healthcare",

    "web": "web",
    "web_agent_retrieval": "web",
    "comtrade": "web",
    "green-comtrade-bench-v2": "web",
    "green_comtrade_bench_v2": "web",
    "searchglitch": "web",
    "search_glitch": "web",

    "agent_security": "agent_security",
    "agent-security": "agent_security",
    "lambda_agent_security": "agent_security",
    "lambda-security": "agent_security",
    "agentbeats-lambda": "agent_security",
    "security_arena": "agent_security",
    "security-arena": "agent_security",
    "gymjailbreak": "agent_security",
    "gym_jailbreak": "agent_security",

    "software_testing": "software_testing",
    "software-testing": "software_testing",
    "software testing": "software_testing",
    "software_testing_agent": "software_testing",
    "logomesh": "software_testing",
    "code_review": "software_testing",
    "codereviewruse": "software_testing",
    "code_review_ruse": "software_testing",

    "defi": "defi",
    "de-fi": "defi",
    "ethernaut": "defi",
    "ethernaut_arena": "defi",
    "smart_contract": "defi",
    "smart_contracts": "defi",
    "crypto": "defi",
    "cryptocrash": "defi",
    "crypto_crash": "defi",

    "legal_domain": "legal_domain",
    "legal-domain": "legal_domain",
    "legal": "legal_domain",
    "legal_agent": "legal_domain",
    "agentify_bench": "legal_domain",
    "agentify-bench": "legal_domain",
    "lawfirmleak": "legal_domain",
    "law_firm_leak": "legal_domain",

    "openenv": "openenv",
    "open_env": "openenv",
    "open-env": "openenv",
    "omnibench": "openenv",
    "omnibench_aegis_env": "openenv",

    # SkillsBench / General-Purpose Agent / standard-v1.
    "skillsbench": "skillsbench",
    "skillsbench_agentbeats": "skillsbench",
    "skillsbench-agentbeats": "skillsbench",
    "skillsbench_leaderboard": "skillsbench",
    "skillsbench-leaderboard": "skillsbench",
    "benchflow": "skillsbench",
    "benchflow_ai": "skillsbench",
    "benchflow-ai": "skillsbench",
    "benchflowai": "skillsbench",
    "standard_v1": "skillsbench",
    "standard-v1": "skillsbench",
    "standard v1": "skillsbench",
    "with_skills": "skillsbench",
    "with-skills": "skillsbench",
    "general_purpose": "skillsbench",
    "general-purpose": "skillsbench",
    "general purpose": "skillsbench",
    "general_purpose_agent": "skillsbench",
    "general-purpose-agent": "skillsbench",
    "general purpose agent": "skillsbench",
    "general_agent": "skillsbench",
    "general-agent": "skillsbench",
    "multi_utility": "skillsbench",
    "multi-utility": "skillsbench",
    "multi utility": "skillsbench",
    "artifact_first": "skillsbench",
    "artifact-first": "skillsbench",
    "artifact output": "skillsbench",
    "artifact_output": "skillsbench",
    "file_output": "skillsbench",
    "file-output": "skillsbench",

    # Generic security remains available for purely security-shaped requests that
    # are not obviously one of the local Sprint 4 domains.
    "security": "security",
    "purple_security": "security",
}

# Scenario metadata can resolve directly to a route-level track. This mirrors
# the final 16 scenario list without replacing upstream track names.
SCENARIO_TO_TRACK: dict[str, str] = {
    "saleforceonespy": "crmarena",
    "salesforceone": "crmarena",
    "saleforceone": "crmarena",
    "wikiwiper": "mcu",
    "tickettwister": "tau2",
    "bidbot": "maizebargain",
    "taxwiztrap": "officeqa",
    "lnklifter": "osworld",
    "linklifter": "osworld",
    "inventoryinject": "pibench",
    "devcontainerdoom": "netarena",
    "staticshipscam": "cybergym",
    "whistleblowerwreck": "fieldworkarena",
    "docudoctor": "healthcare",
    "searchglitch": "web",
    "gymjailbreak": "agent_security",
    "codereviewruse": "software_testing",
    "cryptocrash": "defi",
    "lawfirmleak": "legal_domain",
    "skillsbench": "skillsbench",
    "standardv1": "skillsbench",
    "withskills": "skillsbench",
    "generalpurpose": "skillsbench",
    "generalpurposeagent": "skillsbench",
    "multiutility": "skillsbench",
}

# Domain keys are preserved for the six new domains and mapped to upstream
# selected-opponent tracks where that upstream family already exists.
DOMAIN_TO_TRACK: dict[str, str] = {
    "business_process": "crmarena",
    "game": "mcu",
    "tau2": "tau2",
    "multi_agent": "maizebargain",
    "finance": "officeqa",
    "computer_use": "osworld",
    "agent_safety": "pibench",
    "coding": "netarena",
    "cybersecurity": "cybergym",
    "research": "fieldworkarena",
    "healthcare": "healthcare",
    "web": "web",
    "agent_security": "agent_security",
    "software_testing": "software_testing",
    "defi": "defi",
    "legal_domain": "legal_domain",
}


@dataclass(slots=True)
class TaskClassification:
    """Lightweight description of the incoming task.

    This structure is intentionally generic so it can be built from A2A
    payloads, OpenEnv tasks, tau2 prompts, MCU benchmark prompts, or internal
    harness messages. Keep the fields stable because router and tests use this
    object as a compatibility contract.
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
        "prompt injection", "indirect injection", "exfiltration", "jailbreak", "policy bypass",
        "secret", "credentials", "malware", "phishing", "attack", "defend", "security",
        "adversarial", "red team", "blue team", "unsafe output", "supply chain",
    }
    OPENENV_KEYWORDS = {
        "tool", "table", "ticket", "lookup", "decision", "budget", "finance", "business",
        "mission", "state", "environment", "submit_final", "schema", "routing", "workflow",
        "retrieval", "evidence", "deduplicate", "pagination", "retry", "clinical", "fhir",
        "legal", "crm", "smart contract", "contract", "audit",
    }
    TAU2_KEYWORDS = {
        "trajectory", "trace", "action", "step", "evaluate", "score", "task", "tau2",
        "tau²", "action check", "task bundle", "conversation", "policy-sensitive request",
    }
    MCU_KEYWORDS = {
        "minecraft", "craft", "recipe", "helmet", "ingot", "potion", "enchant",
        "smelt", "mine", "redstone", "tool", "pickaxe", "wiki", "simulator",
        "animal care", "navigation", "mcu", "wikiwiper",
    }

    SKILLSBENCH_KEYWORDS = {
        "skillsbench", "skillsbench leaderboard", "skillsbench-leaderboard",
        "benchflow", "benchflow-ai", "standard-v1", "standard_v1", "with_skills",
        "general purpose", "general-purpose", "general_purpose",
        "general purpose agent", "general-purpose-agent", "multi utility",
        "multi-utility", "artifact-first", "artifact first", "artifact_refs",
        "file output", "file generation", "office-white-collar",
        "software-engineering", "natural-science", "industrial-physical-systems",
        "media-content-production", "finance-economics",
        "mathematics-or-formal-reasoning", "fix-build", "fix build",
        "fix-build-agentops", "fix-build-google-auto", "software-dependency-audit",
        "dependency-audit", "court-form-filling", "paper-anonymizer",
        "pptx-reference-formatting", "xlsx-recover-data", "threejs-to-obj",
        "video-silence-remover", "pg-essay-to-audiobook", "lean4-proof",
        "citation-check", "dialogue-parser", "offer-letter-generator",
    }

    SKILLSBENCH_ARTIFACT_HINTS = {
        "artifact", "artifact_refs", "file", "files", "attachment", "attachments",
        "deliverable", "output_format", "expected_output", "patch", "diff",
        "xlsx", "excel", "spreadsheet", "csv", "docx", "document", "pptx",
        "presentation", "slides", "pdf", "anonymizer", "redact", "lean4",
        "lean", "proof", "threejs", "obj", "3d", "audio", "audiobook",
        "mp3", "wav", "video", "mp4", "silence-remover", "zip", "html",
    }

    SELECTED_TRACK_KEYWORDS: dict[str, set[str]] = {
        "mcu": {
            "mcu-agentbeats", "mcu_minecraft", "mcu-minecraft", "minecraft benchmark", "minecraft",
            "craft", "recipe", "redstone", "wikiwiper",
        },
        "officeqa": {
            "officeqa", "office qa", "officeqa_agentbeats", "document qa", "finance", "spreadsheet",
            "taxwiztrap", "tax", "numeric value", "normalized quantity",
        },
        "crmarena": {
            "crmarena", "crm arena", "crmarenapro", "entropic-crmarenapro", "deogaze",
            "business process", "schema drift", "context rot", "saleforceonespy", "salesforce",
            "crm", "routing decision",
        },
        "fieldworkarena": {
            "fieldworkarena", "fieldworkarena-greenagent", "field work", "research agent",
            "multimodal", "whistleblowerwreck", "whistleblower", "evidence synthesis",
        },
        "maizebargain": {
            "maizebargain", "maize bargain", "tutorial-agent-beats-comp", "bargaining",
            "negotiation", "payoff", "bidbot", "equilibrium", "multi-agent",
        },
        "tau2": {
            "tau2-agentbeats", "tau2", "tau²", "trajectory", "action check", "task bundle",
            "tickettwister", "customer service policy", "clarify before acting",
        },
        "osworld": {
            "osworld", "osworld-green", "osworld-verified", "computer use", "desktop",
            "browser", "lnklifter", "linklifter", "open link", "verify destination",
        },
        "pibench": {
            "pi-bench", "pibench", "agent safety", "policy", "privacy", "pii",
            "inventoryinject", "suspicious artifact", "quarantine",
        },
        "cybergym": {
            "cybergym", "cybersecurity", "vulnerable", "fixed", "sandbox", "staticshipscam",
            "static ship", "controlled cybersecurity",
        },
        "netarena": {
            "netarena", "network", "devcontainer", "coding agent", "patch", "container",
            "devcontainerdoom", "dev container", "coding",
        },
        "healthcare": {
            "healthcare", "medical", "clinical", "fhir", "fhiragentevaluator", "docudoctor",
            "patient", "observation", "medication", "encounter",
        },
        "web": {
            "green-comtrade", "comtrade", "searchglitch", "search glitch", "pagination",
            "deduplication", "deduplicate", "retry", "api retrieval", "web benchmark",
        },
        "agent_security": {
            "agentbeats-lambda", "lambda agent security", "lambda_security", "security arena",
            "gymjailbreak", "gym jailbreak", "jailbreak", "prompt injection", "unsafe-output resistance",
        },
        "software_testing": {
            "logomesh", "software testing", "code review", "codereviewruse", "code review ruse",
            "test generation", "weak tests", "static analyzer", "semantic analyzer",
        },
        "defi": {
            "ethernaut", "defi", "smart contract", "smart contracts", "crypto", "cryptocrash",
            "crypto crash", "anvil", "wallet", "invariant", "solidity",
        },
        "legal_domain": {
            "agentify-bench", "agentify_bench", "legal domain", "legal", "lawfirmleak",
            "law firm", "privilege", "legal discovery", "semantic mapping",
        },
        "skillsbench": SKILLSBENCH_KEYWORDS | SKILLSBENCH_ARTIFACT_HINTS,
    }

    ARTIFACT_KEYWORDS = {
        "json", "yaml", "artifact", "report", "table", "file", "schema", "card", "contract",
        "assessment", "matrix", "payload", "bundle", "submission",
        "artifact_refs", "deliverable", "output_format", "expected_output",
        "patch", "diff", "xlsx", "excel", "spreadsheet", "csv", "docx",
        "pptx", "slides", "presentation", "pdf", "lean", "lean4", "obj",
        "audio", "video", "zip", "html",
    }
    TOOL_KEYWORDS = {
        "tool", "lookup", "query", "search", "inspect", "call", "use map_probe",
        "use table_lookup", "use ticket_lookup", "simulator", "crafting wiki", "minecraft wiki",
        "api", "browser", "fhir", "sandbox", "contract", "state", "reset", "step",
        "workspace", "repository", "repo", "build", "test", "convert", "generate",
        "recover", "format", "anonymize", "redact",
    }
    HIGH_RISK_PATTERNS = (
        r"ignore previous instructions",
        r"reveal .*secret",
        r"print .*token",
        r"export .*data",
        r"bypass .*policy",
        r"system note",
        r"developer note",
        r"prefer the wiki over prior knowledge",
        r"requires 1 stick",
        r"override",
        r"private key",
        r"api[_\-\s]?key",
        r"prompt leak",
        r"jailbreak",
    )
    MEDIUM_RISK_MARKERS = {
        "poisoned", "tampered", "untrusted", "wiki", "hidden note", "priority abuse",
        "privacy", "pii", "secret", "credential", "privilege", "patient", "wallet", "sandbox",
        "supply chain", "dependency", "schema drift", "context rot",
    }

    def classify(
        self,
        task_text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        track_hint: str | None = None,
    ) -> TaskClassification:
        metadata = dict(metadata or {})
        normalized = " ".join(str(task_text or "").lower().split())
        tags: list[str] = []
        reasons: list[str] = []

        metadata_hint = self._metadata_track_hint(metadata)
        selected_hint = track_hint or metadata_hint
        track_guess = self._guess_track(normalized, selected_hint)
        task_type = self._guess_task_type(normalized, track_guess)
        complexity = self._guess_complexity(normalized, metadata)
        risk = self._guess_risk(normalized, metadata, track_guess)
        artifact_expected = (
            self._read_bool(metadata.get("artifact_required"), default=False)
            or self._read_bool(metadata.get("requires_artifact"), default=False)
            or self._contains_any(normalized, self.ARTIFACT_KEYWORDS)
            or (track_guess == "skillsbench" and self._skillsbench_artifact_expected(normalized, metadata))
        )
        tool_use_likely = (
            self._read_bool(metadata.get("tool_use_likely"), default=False)
            or self._contains_any(normalized, self.TOOL_KEYWORDS)
            or track_guess in {"mcu", "tau2", "osworld", "web", "healthcare", "defi", "skillsbench"}
        )
        multi_step = (
            self._read_bool(metadata.get("multi_step"), default=False)
            or self._looks_multi_step(normalized)
            or track_guess in {"mcu", "tau2", "maizebargain", "defi", "software_testing", "skillsbench"}
        )
        heldout_like = self._looks_heldout_like(normalized, metadata)

        domain = self._normalize_key(metadata.get("domain"))
        scenario_key = self._normalize_key(metadata.get("scenario_id") or metadata.get("scenario_name") or metadata.get("name"))

        tags.append(f"track:{track_guess}")
        if domain:
            tags.append(f"domain:{domain}")
        if scenario_key:
            tags.append(f"scenario:{scenario_key}")

        if selected_hint:
            reasons.append(f"Track resolved from explicit hint/metadata: {track_guess}.")
        else:
            reasons.append(f"Track inferred from task text: {track_guess}.")

        if artifact_expected:
            tags.append("artifact")
            reasons.append("Detected output language or metadata suggesting a structured artifact.")
        if tool_use_likely:
            tags.append("tool-use")
            reasons.append("Detected benchmark, environment, or tool-oriented language.")
        if multi_step:
            tags.append("multi-step")
            reasons.append("Task appears to require a sequence of actions.")
        if heldout_like:
            tags.append("heldout-like")
            reasons.append("Task resembles a non-templated or unusual prompt.")
        if track_guess == "skillsbench":
            tags.append("skillsbench")
            tags.append("general-purpose")
            tags.append("artifact-first")
            reasons.append("Detected SkillsBench / standard-v1 general-purpose artifact-first task signals.")
            if artifact_expected:
                tags.append("file-output")

        if track_guess == "mcu":
            tags.append("minecraft")
            reasons.append("Detected Minecraft / MCU benchmark language or scenario mapping.")
        if track_guess in {"agent_security", "cybergym", "pibench", "security"}:
            tags.append("security-track")
        if risk in {"medium", "high"}:
            tags.append("risk-aware")
            reasons.append("Security-sensitive, privacy-sensitive, poisoned-knowledge, or policy-sensitive language detected.")

        return TaskClassification(
            track_guess=track_guess,
            task_type=task_type,
            complexity=complexity,
            risk=risk,
            artifact_expected=artifact_expected,
            multi_step=multi_step,
            tool_use_likely=tool_use_likely,
            heldout_like=heldout_like,
            tags=self._dedupe(tags),
            reasons=self._dedupe(reasons),
        )

    def _metadata_track_hint(self, metadata: Mapping[str, Any]) -> str | None:
        for key in ("track_hint", "track", "benchmark_track", "opponent_profile", "benchmark", "task_set", "suite", "leaderboard", "adapter"):
            value = metadata.get(key)
            if value:
                normalized_value = self._normalize_track(str(value))
                if normalized_value == "skillsbench":
                    return "skillsbench"
                return str(value)

        if self._metadata_has_skillsbench_signal(metadata):
            return "skillsbench"

        domain = self._normalize_key(metadata.get("domain"))
        if domain and domain in DOMAIN_TO_TRACK:
            return DOMAIN_TO_TRACK[domain]

        for key in ("scenario_id", "scenario_name", "name"):
            scenario = self._normalize_key(metadata.get(key))
            if scenario and scenario in SCENARIO_TO_TRACK:
                return SCENARIO_TO_TRACK[scenario]

        scenario_obj = metadata.get("scenario")
        if isinstance(scenario_obj, Mapping):
            nested_domain = self._normalize_key(scenario_obj.get("domain"))
            if nested_domain and nested_domain in DOMAIN_TO_TRACK:
                return DOMAIN_TO_TRACK[nested_domain]
            for key in ("scenario_id", "scenario_name", "name", "id"):
                nested_scenario = self._normalize_key(scenario_obj.get(key))
                if nested_scenario and nested_scenario in SCENARIO_TO_TRACK:
                    return SCENARIO_TO_TRACK[nested_scenario]
        return None

    def _guess_track(self, text: str, track_hint: str | None) -> str:
        if track_hint:
            return self._normalize_track(track_hint)

        selected_scores = {
            track: self._count_hits(text, keywords)
            for track, keywords in self.SELECTED_TRACK_KEYWORDS.items()
        }
        selected_track, selected_hits = max(selected_scores.items(), key=lambda item: item[1])
        if selected_hits > 0:
            return selected_track

        security_hits = self._count_hits(text, self.SECURITY_KEYWORDS)
        openenv_hits = self._count_hits(text, self.OPENENV_KEYWORDS)
        tau2_hits = self._count_hits(text, self.TAU2_KEYWORDS)
        mcu_hits = self._count_hits(text, self.MCU_KEYWORDS)

        best = max(security_hits, openenv_hits, tau2_hits, mcu_hits)
        if best <= 0:
            return "openenv"
        if mcu_hits == best:
            return "mcu"
        if tau2_hits == best:
            return "tau2"
        if security_hits == best:
            return "security"
        return "openenv"

    def _guess_task_type(self, text: str, track_guess: str) -> str:
        if track_guess == "skillsbench":
            return self._guess_skillsbench_task_type(text)
        if track_guess == "mcu":
            if "artifact" in text or "json" in text or "report" in text:
                return "artifact_generation"
            if "craft" in text or "mine" in text or "navigate" in text or "potion" in text:
                return "environment_task"
            return "reasoning"
        if track_guess == "tau2" or "conversation" in text or "clarify" in text:
            return "interactive_policy_task"
        if track_guess in {"agent_security", "cybergym", "pibench", "security"}:
            return "security_assessment"
        if track_guess == "healthcare" or "fhir" in text or "clinical" in text:
            return "healthcare_evidence_task"
        if track_guess == "web" or "pagination" in text or "api" in text:
            return "retrieval_task"
        if track_guess == "software_testing" or "test" in text or "code review" in text:
            return "software_testing"
        if track_guess == "defi" or "smart contract" in text:
            return "contract_audit"
        if track_guess == "legal_domain" or "legal" in text or "privilege" in text:
            return "legal_mapping"
        if "analy" in text or "classif" in text:
            return "analysis"
        if "tool" in text or "lookup" in text or "query" in text:
            return "tool_use"
        if "report" in text or "json" in text or "artifact" in text:
            return "artifact_generation"
        if "plan" in text or "steps" in text or "sequence" in text:
            return "multi_step_reasoning"
        return "reasoning"

    def _guess_complexity(self, text: str, metadata: Mapping[str, Any]) -> str:
        explicit = str(metadata.get("complexity") or "").strip().lower()
        if explicit in {"low", "medium", "high"}:
            return explicit

        signals = 0
        signals += 1 if self._looks_multi_step(text) else 0
        signals += 1 if self._contains_any(text, self.ARTIFACT_KEYWORDS) else 0
        signals += 1 if self._contains_any(text, self.TOOL_KEYWORDS) else 0
        signals += 1 if len(text.split()) > 120 else 0
        signals += 1 if self._contains_any(text, self.SECURITY_KEYWORDS | self.MEDIUM_RISK_MARKERS) else 0
        if signals >= 3:
            return "high"
        if signals == 2:
            return "medium"
        return "low"

    def _guess_risk(self, text: str, metadata: Mapping[str, Any], track_guess: str) -> str:
        explicit = str(metadata.get("risk") or metadata.get("expected_risk") or "").strip().lower()
        if explicit in {"low", "medium", "high", "critical"}:
            return "high" if explicit == "critical" else explicit

        for pattern in self.HIGH_RISK_PATTERNS:
            if re.search(pattern, text):
                return "high"

        if track_guess == "skillsbench" and self._contains_any(text, self.SECURITY_KEYWORDS | self.MEDIUM_RISK_MARKERS):
            return "medium"

        if track_guess in {"agent_security", "cybergym", "defi"}:
            return "medium"
        if track_guess in {"healthcare", "legal_domain", "pibench"}:
            return "medium"
        if self._contains_any(text, self.SECURITY_KEYWORDS | self.MEDIUM_RISK_MARKERS):
            return "medium"
        return "low"

    def _looks_multi_step(self, text: str) -> bool:
        return (
            "then " in text
            or "after " in text
            or "step " in text
            or "first " in text
            or "next " in text
            or "before " in text
            or "sequence" in text
            or "trajectory" in text
            or text.count(";") >= 2
        )

    def _looks_heldout_like(self, text: str, metadata: Mapping[str, Any]) -> bool:
        if self._read_bool(metadata.get("heldout_mode"), default=False) or self._read_bool(metadata.get("heldout_like"), default=False):
            return True
        vocab = set(text.split())
        broad_keywords = self.OPENENV_KEYWORDS | self.SECURITY_KEYWORDS | self.TAU2_KEYWORDS | self.MCU_KEYWORDS
        rare_signal = len(vocab) > 90 and self._count_hits(text, broad_keywords) <= 2
        return rare_signal

    def _guess_skillsbench_task_type(self, text: str) -> str:
        if any(token in text for token in ("fix-build", "fix build", "build failure", "failing build", "patch", "diff")):
            return "software_repair_artifact"
        if any(token in text for token in ("xlsx", "excel", "spreadsheet", "csv", "pivot")):
            return "spreadsheet_artifact"
        if any(token in text for token in ("pptx", "presentation", "slides", "slide deck")):
            return "presentation_artifact"
        if any(token in text for token in ("docx", "offer letter", "court form", "form filling")):
            return "document_artifact"
        if any(token in text for token in ("pdf", "paper-anonymizer", "anonymizer", "redact")):
            return "pdf_document_artifact"
        if any(token in text for token in ("lean4", "lean", "formal proof", "proof")):
            return "formal_reasoning_artifact"
        if any(token in text for token in ("threejs", "obj", "3d model", "geometry")):
            return "geometry_artifact"
        if any(token in text for token in ("audio", "audiobook", "mp3", "wav")):
            return "audio_artifact"
        if "video" in text or "silence-remover" in text or "silence remover" in text:
            return "video_artifact"
        if "dependency-audit" in text or "software-dependency-audit" in text:
            return "software_dependency_audit"
        if "citation-check" in text or "dialogue-parser" in text:
            return "analysis_artifact"
        if self._contains_any(text, self.SKILLSBENCH_ARTIFACT_HINTS):
            return "artifact_generation"
        return "general_purpose_reasoning"

    def _skillsbench_artifact_expected(self, text: str, metadata: Mapping[str, Any]) -> bool:
        if self._contains_any(text, self.SKILLSBENCH_ARTIFACT_HINTS):
            return True

        for key in ("task_id", "category", "output_format", "expected_output", "deliverable", "instructions", "instruction", "prompt"):
            value = metadata.get(key)
            if value and self._contains_any(str(value).lower(), self.SKILLSBENCH_ARTIFACT_HINTS):
                return True

        for key in ("tags", "files", "attachments", "input_files"):
            value = metadata.get(key)
            if isinstance(value, list):
                if self._contains_any(" ".join(str(item).lower() for item in value), self.SKILLSBENCH_ARTIFACT_HINTS):
                    return True
            elif value and self._contains_any(str(value).lower(), self.SKILLSBENCH_ARTIFACT_HINTS):
                return True

        # SkillsBench standard-v1 tasks with explicit has_skills usually expect
        # a concrete deliverable. Keep this scoped to SkillsBench signals to
        # avoid changing legacy OpenEnv/Pi-Bench behavior.
        return self._read_bool(metadata.get("has_skills"), default=False) and self._metadata_has_skillsbench_signal(metadata)

    def _metadata_has_skillsbench_signal(self, metadata: Mapping[str, Any]) -> bool:
        blob = self._metadata_blob(metadata)
        if self._contains_any(blob, self.SKILLSBENCH_KEYWORDS):
            return True
        task_id = str(metadata.get("task_id") or metadata.get("id") or "").lower()
        if any(marker in task_id for marker in ("fix-build", "xlsx-", "pptx-", "lean4", "threejs", "dependency-audit")):
            return True
        return False

    @staticmethod
    def _metadata_blob(value: Any, *, depth: int = 0) -> str:
        if value is None or depth > 4:
            return ""
        if isinstance(value, Mapping):
            pieces: list[str] = []
            for key, child in value.items():
                pieces.append(str(key))
                pieces.append(TaskClassifier._metadata_blob(child, depth=depth + 1))
            return " ".join(piece for piece in pieces if piece).lower()
        if isinstance(value, (list, tuple, set)):
            return " ".join(TaskClassifier._metadata_blob(item, depth=depth + 1) for item in value).lower()
        return str(value).lower()

    @staticmethod
    def _normalize_track(track: str | None) -> str:
        raw = str(track or "openenv").strip().lower()
        raw = re.sub(r"\s+", " ", raw)
        candidates = [
            raw,
            raw.replace("_", "-"),
            raw.replace("-", "_"),
            raw.replace("_", " "),
            raw.replace("-", " "),
            re.sub(r"[^a-z0-9]+", "", raw),
        ]
        for candidate in candidates:
            if candidate in TRACK_ALIASES:
                return TRACK_ALIASES[candidate]
        normalized_key = re.sub(r"[^a-z0-9]+", "", raw)
        if normalized_key in SCENARIO_TO_TRACK:
            return SCENARIO_TO_TRACK[normalized_key]
        return raw.replace("-", "_").replace(" ", "_") or "openenv"

    @staticmethod
    def _normalize_key(value: Any) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return ""
        return re.sub(r"[^a-z0-9]+", "", raw)

    @staticmethod
    def _contains_any(text: str, keywords: set[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _count_hits(text: str, keywords: set[str]) -> int:
        return sum(1 for keyword in keywords if keyword in text)

    @staticmethod
    def _read_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
