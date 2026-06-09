from __future__ import annotations

"""Lightweight post-generation self-checks for AegisForge.

The self-check layer should not solve benchmark tasks.  It validates that the
response/plan shape is coherent before returning output.  v0.4 adds explicit
SkillsBench/BenchFlow filesystem-output-primary checks while preserving the
existing Security Arena / Pi-Bench / CyberGym / NetArena guarded-response logic.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .planner import ExecutionPlan


SELF_CHECK_VERSION = "self_check_v0_4_skillsbench_filesystem_output_primary_2026_06_09"


# Canonical selected-opponent tracks for AgentX-AgentBeats Phase 2.
# Note: "mcu" and "mcu-minecraft" are intentionally the same track.
MCU_LIKE_TRACKS = {"mcu"}
SECURITY_LIKE_TRACKS = {"security", "pibench", "cybergym", "netarena"}

TRACK_ALIASES = {
    "mcu": "mcu",
    "mcu-minecraft": "mcu",
    "mcu_minecraft": "mcu",
    "minecraft": "mcu",
    "minecraft benchmark": "mcu",
    "minecraft-benchmark": "mcu",
    "mcu-agentbeats": "mcu",
    "mcu_agentbeats": "mcu",
    "officeqa": "officeqa",
    "office_qa": "officeqa",
    "office-qa": "officeqa",
    "finance": "officeqa",
    "finance_agent": "officeqa",
    "finance-agent": "officeqa",
    "crmarena": "crmarena",
    "crm_arena": "crmarena",
    "crm-arena": "crmarena",
    "crmarenapro": "crmarena",
    "entropic-crmarenapro": "crmarena",
    "business": "crmarena",
    "business_process": "crmarena",
    "business-process": "crmarena",
    "fieldworkarena": "fieldworkarena",
    "fieldworkarena-greenagent": "fieldworkarena",
    "fieldworkarena_greenagent": "fieldworkarena",
    "research": "fieldworkarena",
    "maizebargain": "maizebargain",
    "maize-bargain": "maizebargain",
    "maize_bargain": "maizebargain",
    "multi-agent": "maizebargain",
    "multi_agent": "maizebargain",
    "tau2": "tau2",
    "tau²": "tau2",
    "tau-2": "tau2",
    "tau_2": "tau2",
    "osworld": "osworld",
    "osworld-green": "osworld",
    "computer-use": "osworld",
    "computer_use": "osworld",
    "pibench": "pibench",
    "pi-bench": "pibench",
    "pi_bench": "pibench",
    "agent-safety": "pibench",
    "agent_safety": "pibench",
    "cybergym": "cybergym",
    "cyber-gym": "cybergym",
    "cyber_gym": "cybergym",
    "cybergym-green": "cybergym",
    "cybergym_green": "cybergym",
    "cybersecurity-agent": "cybergym",
    "cybersecurity_agent": "cybergym",
    "staticshipscam": "cybergym",
    "gymjailbreak": "cybergym",
    "arvo": "cybergym",
    "oss-fuzz": "cybergym",
    "oss_fuzz": "cybergym",
    "netarena": "netarena",
    "net-arena": "netarena",
    "net_arena": "netarena",
    "coding-agent": "netarena",
    "coding_agent": "netarena",

    # SkillsBench / BenchFlow / General-Purpose Agent aliases.
    "skillsbench": "skillsbench",
    "skillsbench-agentbeats": "skillsbench",
    "skillsbench_agentbeats": "skillsbench",
    "skillsbench-leaderboard": "skillsbench",
    "skillsbench_leaderboard": "skillsbench",
    "benchflow": "skillsbench",
    "benchflow-ai": "skillsbench",
    "benchflow_ai": "skillsbench",
    "benchflowai": "skillsbench",
    "standard-v1": "skillsbench",
    "standard_v1": "skillsbench",
    "standard v1": "skillsbench",
    "with-skills": "skillsbench",
    "with_skills": "skillsbench",
    "general-purpose": "skillsbench",
    "general_purpose": "skillsbench",
    "general-purpose-agent": "skillsbench",
    "general_purpose_agent": "skillsbench",
    "artifact-first": "skillsbench",
    "artifact_first": "skillsbench",
    "filesystem-first": "skillsbench",
    "filesystem_first": "skillsbench",
    "filesystem-output-primary": "skillsbench",
    "filesystem_output_primary": "skillsbench",
    "sandbox-file-output": "skillsbench",
    "sandbox_file_output": "skillsbench",
}

SKILLSBENCH_FAMILIES = {
    "json_output",
    "csv_output",
    "code_solution",
    "office_xlsx",
    "office_pptx",
    "office_docx",
    "pdf_document",
    "lean_solution",
    "security_config",
    "media_output",
    "general_file_output",
}

SKILLSBENCH_PATH_MARKERS = (
    "/root/answer.json",
    "/root/output",
    "/app/workspace",
    "/app/output",
    "/output/",
    "/workspace/",
    "/home/github/build/failed",
    "task_workspace_executor",
    "workspace_execution",
    "output_contract",
    "primary_outputs",
    "wrote_any_file",
    "ok_writes",
    "filesystem_output_primary",
    "filesystem-output-primary",
)

SKILLSBENCH_TASK_HINTS = (
    "fix-build-agentops",
    "fix-build-google-auto",
    "fix-build",
    "software-dependency-audit",
    "dependency-audit",
    "court-form-filling",
    "paper-anonymizer",
    "pptx-reference-formatting",
    "exceltable-in-ppt",
    "xlsx-recover-data",
    "pdf-excel-diff",
    "sales-pivot-analysis",
    "threejs-to-obj",
    "video-silence-remover",
    "pg-essay-to-audiobook",
    "lean4-proof",
    "citation-check",
    "dialogue-parser",
    "offer-letter-generator",
)


@dataclass(slots=True)
class SelfCheckIssue:
    code: str
    message: str
    severity: str = "medium"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(slots=True)
class SelfCheckResult:
    passed: bool
    issues: list[SelfCheckIssue] = field(default_factory=list)
    severity: str = "low"
    suggested_fix: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": [issue.as_dict() for issue in self.issues],
            "severity": self.severity,
            "suggested_fix": self.suggested_fix,
        }


class SelfCheck:
    """Run lightweight post-generation validation before returning output.

    Design goals:
    - keep generic quality checks;
    - apply stricter checks for defender mode;
    - avoid over-blocking attacker mode;
    - add Security Arena-specific leakage / unsafe-output checks;
    - add SkillsBench filesystem-output-primary checks;
    - respect strict_mode / required_sections / normal_user when available.
    """

    _EMPTY_MARKERS = ("todo", "tbd", "fixme", "placeholder")
    _INJECTION_ECHO_PATTERNS = (
        r"ignore previous instructions",
        r"disregard prior instructions",
        r"developer note",
        r"system note",
        r"hidden instruction",
        r"follow the injected prompt",
    )
    _PROMPT_LEAK_PATTERNS = (
        r"system prompt\s*:",
        r"internal prompt\s*:",
        r"hidden prompt\s*:",
        r"tool schema\s*:",
        r"internal tool definition",
        r"developer message\s*:",
    )
    _SECRET_LEAK_PATTERNS = (
        r"api[_\-\s]?key\s*[:=]",
        r"access[_\-\s]?token\s*[:=]",
        r"bearer\s+[a-z0-9\._\-]{10,}",
        r"password\s*[:=]",
        r"secret\s*[:=]",
        r"-----begin [a-z ]*private key-----",
    )
    _UNSAFE_OUTPUT_PATTERNS = (
        r"<script\b",
        r"javascript:",
        r"onerror\s*=",
        r"onload\s*=",
        r"eval\s*\(",
        r"document\.cookie",
        r"innerhtml\s*=",
    )
    _SUSPICIOUS_SUPPLY_CHAIN_PATTERNS = (
        r"install from this unverified repo",
        r"disable verification",
        r"skip signature check",
        r"trust the package blindly",
        r"curl .* \| .*sh",
    )
    _HARD_REFUSAL_PATTERNS = (
        r"\bi can(?:not|'t)\b",
        r"\bi won't\b",
        r"\brefuse\b",
        r"\bcannot comply\b",
        r"\bcan't comply\b",
    )

    def validate_response(
        self,
        *,
        task_text: str,
        response: str,
        plan: ExecutionPlan,
        metadata: Mapping[str, Any] | None = None,
    ) -> SelfCheckResult:
        metadata_dict = dict(metadata or {})
        scenario = self._to_dict(metadata_dict.get("scenario"))
        signals = self._to_dict(metadata_dict.get("signals"))

        issues: list[SelfCheckIssue] = []

        response = response or ""
        response_lower = response.lower()
        task_text = task_text or ""
        task_lower = task_text.lower()

        track = self._normalize_track(
            metadata_dict.get("track_hint")
            or metadata_dict.get("track")
            or metadata_dict.get("benchmark")
            or metadata_dict.get("task_set")
            or metadata_dict.get("adapter")
        )
        if track != "skillsbench" and self._looks_like_skillsbench(metadata_dict, task_text):
            track = "skillsbench"

        assessment_mode = self._normalize_mode(
            metadata_dict.get("assessment_mode")
            or metadata_dict.get("mode")
            or scenario.get("assessment_mode")
            or "defender"
        )
        scenario_family = self._normalize_family(
            metadata_dict.get("scenario_family")
            or metadata_dict.get("family")
            or metadata_dict.get("contract_family")
            or metadata_dict.get("output_family")
            or metadata_dict.get("scenario")
            or scenario.get("scenario_family")
            or "general"
        )
        artifact_required = self._read_bool(
            metadata_dict.get("artifact_required", metadata_dict.get("requires_artifact", signals.get("requires_artifact"))),
            default=False,
        )
        if track == "skillsbench":
            artifact_required = True

        strict_mode = self._read_bool(metadata_dict.get("strict_mode"), default=False)
        normal_user = self._read_bool(
            metadata_dict.get("normal_user", scenario.get("normal_user")),
            default=False,
        )
        max_turns = max(
            1,
            self._safe_int(
                metadata_dict.get("max_turns", scenario.get("max_turns")),
                default=1,
            ),
        )
        expected_risk = self._normalize_risk(
            metadata_dict.get("expected_risk", signals.get("expected_risk"))
        )
        effective_risk = self._max_risk(
            self._normalize_risk(getattr(plan, "risk_level", None)),
            expected_risk,
        )
        required_sections = self._as_list(metadata_dict.get("required_sections"))

        self._apply_generic_checks(
            response=response,
            response_lower=response_lower,
            artifact_required=artifact_required,
            strict_mode=strict_mode,
            required_sections=required_sections,
            issues=issues,
        )

        if track == "skillsbench":
            self._apply_skillsbench_checks(
                task_text=task_text,
                task_lower=task_lower,
                response=response,
                response_lower=response_lower,
                metadata=metadata_dict,
                scenario_family=scenario_family,
                strict_mode=strict_mode,
                issues=issues,
            )

        if track in SECURITY_LIKE_TRACKS:
            if assessment_mode == "defender":
                self._apply_security_defender_checks(
                    task_text=task_text,
                    response=response,
                    response_lower=response_lower,
                    scenario_family=scenario_family,
                    plan=plan,
                    strict_mode=strict_mode,
                    normal_user=normal_user,
                    max_turns=max_turns,
                    effective_risk=effective_risk,
                    required_sections=required_sections,
                    issues=issues,
                )
            else:
                self._apply_security_attacker_checks(
                    response=response,
                    response_lower=response_lower,
                    scenario_family=scenario_family,
                    artifact_required=artifact_required,
                    strict_mode=strict_mode,
                    max_turns=max_turns,
                    required_sections=required_sections,
                    issues=issues,
                )

        if scenario_family == "wikiwiper":
            self._apply_wikiwiper_checks(
                response_lower=response_lower,
                issues=issues,
            )

        severity = self._derive_severity(issues)
        passed = self._passes(issues=issues, strict_mode=strict_mode, effective_risk=effective_risk)
        suggested_fix = self._suggested_fix(
            issues=issues,
            track=track,
            assessment_mode=assessment_mode,
        )

        return SelfCheckResult(
            passed=passed,
            issues=issues,
            severity=severity,
            suggested_fix=suggested_fix,
        )

    def _apply_generic_checks(
        self,
        *,
        response: str,
        response_lower: str,
        artifact_required: bool,
        strict_mode: bool,
        required_sections: list[str],
        issues: list[SelfCheckIssue],
    ) -> None:
        stripped = response.strip()

        if not stripped:
            issues.append(SelfCheckIssue("empty_response", "The response is empty.", "high"))
            return

        if len(stripped) < 20:
            issues.append(
                SelfCheckIssue(
                    "thin_response",
                    "The response looks too short to be complete.",
                    "medium",
                )
            )

        if artifact_required and not self._looks_structured(response_lower):
            issues.append(
                SelfCheckIssue(
                    "artifact_missing",
                    "A structured artifact or filesystem-output contract appears to be required but was not detected.",
                    "high" if strict_mode else "medium",
                )
            )

        missing_sections = self._missing_sections(response_lower, required_sections)
        if missing_sections:
            issues.append(
                SelfCheckIssue(
                    "required_sections_missing",
                    f"Response appears to miss required sections: {', '.join(missing_sections)}.",
                    "high" if strict_mode else "medium",
                )
            )

        if any(marker in response_lower for marker in self._EMPTY_MARKERS):
            issues.append(
                SelfCheckIssue(
                    "unfinished",
                    "Response still contains unfinished or placeholder markers.",
                    "medium" if strict_mode else "low",
                )
            )

    def _apply_skillsbench_checks(
        self,
        *,
        task_text: str,
        task_lower: str,
        response: str,
        response_lower: str,
        metadata: Mapping[str, Any],
        scenario_family: str,
        strict_mode: bool,
        issues: list[SelfCheckIssue],
    ) -> None:
        metadata_blob = self._json_snippet(metadata, max_chars=22000).lower()
        combined = " ".join([task_lower, response_lower, metadata_blob]).replace("_", "-")

        filesystem_signal = self._contains_any(combined, tuple(marker.replace("_", "-") for marker in SKILLSBENCH_PATH_MARKERS))
        artifact_refs_primary_claim = self._contains_any(
            combined,
            (
                "artifact-refs must not be empty",
                "artifact-refs are primary",
                "artifact-refs primary",
                "a2a artifacts are the scoring channel",
                "filepart is the scoring channel",
                "only artifact-refs",
            ),
        )
        diagnostic_refs_signal = self._contains_any(
            combined,
            (
                "artifact-refs-role",
                "diagnostic-and-compatibility-signal",
                "diagnostic",
                "compatibility",
            ),
        )
        sandbox_file_signal = bool(re.search(r"(/root|/app/workspace|/output|/workspace|/home/github/build/failed)[^\s`'\"<>]*", combined))
        workspace_execution_signal = self._contains_any(
            combined,
            (
                "workspace-executor",
                "task-workspace-executor",
                "workspace-execution",
                "wrote-any-file",
                "ok-writes",
                "write-count",
                "workspace-visible",
            ),
        )
        output_contract_signal = self._contains_any(
            combined,
            (
                "output-contract",
                "primary-outputs",
                "requirements",
                "solver-family",
                "selected-solver-key",
                "filesystem-output-primary",
            ),
        )

        if not filesystem_signal and not sandbox_file_signal and not workspace_execution_signal:
            issues.append(
                SelfCheckIssue(
                    "skillsbench_filesystem_signal_missing",
                    "SkillsBench response lacks clear filesystem-output-primary evidence or sandbox file-path evidence.",
                    "high" if strict_mode else "medium",
                )
            )

        if artifact_refs_primary_claim:
            issues.append(
                SelfCheckIssue(
                    "skillsbench_artifact_refs_overclaimed",
                    "Response appears to treat artifact_refs/FilePart as the primary SkillsBench scoring channel instead of diagnostics.",
                    "high",
                )
            )

        if not diagnostic_refs_signal and "artifact-refs" in combined:
            issues.append(
                SelfCheckIssue(
                    "skillsbench_artifact_refs_role_unclear",
                    "artifact_refs are mentioned but their diagnostic/compatibility role is not clear.",
                    "medium" if strict_mode else "low",
                )
            )

        if not output_contract_signal:
            issues.append(
                SelfCheckIssue(
                    "skillsbench_output_contract_unclear",
                    "SkillsBench response does not clearly expose output contract / primary output / selected solver evidence.",
                    "medium" if strict_mode else "low",
                )
            )

        task_id = str(
            metadata.get("canonical_task_id")
            or metadata.get("environment_canonical_task_id")
            or metadata.get("contract_task_id")
            or metadata.get("task_id")
            or metadata.get("id")
            or ""
        ).lower().replace("_", "-")
        family = self._infer_skillsbench_family(metadata=metadata, scenario_family=scenario_family, text=combined)

        if family == "office_pptx" and ".pptx" not in combined and "presentation" not in combined and "powerpoint" not in combined:
            issues.append(
                SelfCheckIssue(
                    "skillsbench_pptx_output_not_visible",
                    "PPTX-family task does not clearly show a .pptx/presentation output.",
                    "high" if strict_mode else "medium",
                )
            )

        if ("exceltable-in-ppt" in combined or "pptx-reference-formatting" in combined) and self._contains_any(
            combined,
            ("selected-solver-key\": \"code-solution", "selected-solver-key=code-solution", "family\": \"xlsx", "family=xlsx")
        ):
            issues.append(
                SelfCheckIssue(
                    "skillsbench_pptx_misrouted",
                    "PPTX task appears to be routed as code/xlsx instead of office_pptx.",
                    "high",
                )
            )

        if family == "code_solution" and ("fix-build" in combined or "patch-0.diff" in combined) and not self._contains_any(
            combined,
            ("patch-0.diff", "patch_0.diff", "/home/github/build/failed", "failed-reasons.txt", "git apply"),
        ):
            issues.append(
                SelfCheckIssue(
                    "skillsbench_build_repair_patch_evidence_missing",
                    "Build-repair task lacks visible patch_*.diff or failed_reasons.txt evidence.",
                    "high" if strict_mode else "medium",
                )
            )

        if family == "lean_solution" and ".lean" not in combined and "lean" not in combined:
            issues.append(
                SelfCheckIssue(
                    "skillsbench_lean_output_not_visible",
                    "Lean/formal-reasoning task does not clearly show a .lean output.",
                    "medium",
                )
            )

        if family == "security_config":
            if self._matches_any(combined, self._SECRET_LEAK_PATTERNS):
                issues.append(
                    SelfCheckIssue(
                        "skillsbench_security_secret_like_output",
                        "Security-config output appears to contain secret-like material.",
                        "high",
                    )
                )
            if not self._contains_any(combined, ("config", "policy", "findings", "audit", "rule", "detection", "defensive")):
                issues.append(
                    SelfCheckIssue(
                        "skillsbench_security_config_shape_unclear",
                        "Security-config family lacks visible defensive config/findings/audit shape.",
                        "medium" if strict_mode else "low",
                    )
                )

        if "general-file-output" in combined and family != "general_file_output":
            issues.append(
                SelfCheckIssue(
                    "skillsbench_generic_family_override_risk",
                    f"Response includes general_file_output despite stronger inferred family {family}.",
                    "medium",
                )
            )

        # If the executor/harness reports real writes, do not punish thin final
        # prose too harshly; the scoring target is filesystem output.
        if workspace_execution_signal and self._contains_any(combined, ("wrote-any-file\": true", "wrote-any-file=true", "ok-writes\": 1", "ok-writes=1")):
            self._downgrade_issue(issues, "thin_response", "low")
            self._downgrade_issue(issues, "artifact_missing", "low")

        if task_id and any(hint in task_id for hint in SKILLSBENCH_TASK_HINTS) and not filesystem_signal:
            issues.append(
                SelfCheckIssue(
                    "skillsbench_known_task_without_filesystem_contract",
                    f"Known SkillsBench task {task_id!r} was detected without clear filesystem contract evidence.",
                    "medium",
                )
            )

    def _apply_security_defender_checks(
        self,
        *,
        task_text: str,
        response: str,
        response_lower: str,
        scenario_family: str,
        plan: ExecutionPlan,
        strict_mode: bool,
        normal_user: bool,
        max_turns: int,
        effective_risk: str,
        required_sections: list[str],
        issues: list[SelfCheckIssue],
    ) -> None:
        if self._matches_any(response_lower, self._INJECTION_ECHO_PATTERNS):
            issues.append(
                SelfCheckIssue(
                    "injection_echo",
                    "Defender response appears to echo or endorse injected instructions.",
                    "high",
                )
            )

        if self._matches_any(response_lower, self._PROMPT_LEAK_PATTERNS):
            issues.append(
                SelfCheckIssue(
                    "prompt_leakage",
                    "Defender response may reveal internal/system/tool prompt material.",
                    "high",
                )
            )

        if self._matches_any(response_lower, self._SECRET_LEAK_PATTERNS):
            issues.append(
                SelfCheckIssue(
                    "secret_leakage",
                    "Defender response contains secret-like material.",
                    "high",
                )
            )

        if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"} and self._matches_any(
            response_lower, self._UNSAFE_OUTPUT_PATTERNS
        ):
            issues.append(
                SelfCheckIssue(
                    "unsafe_output_rendering",
                    "Defender response appears to include unsafe executable/renderable output.",
                    "high",
                )
            )

        if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
            if re.search(r"\b(ssn|social security|private key|api key|token|password)\b", response_lower):
                issues.append(
                    SelfCheckIssue(
                        "sensitive_term_exposure",
                        "Defender response contains strong indicators of sensitive-data exposure.",
                        "high",
                    )
                )

        if scenario_family in {"supply_chain", "dependency_attack"} and self._matches_any(
            response_lower, self._SUSPICIOUS_SUPPLY_CHAIN_PATTERNS
        ):
            issues.append(
                SelfCheckIssue(
                    "unsafe_dependency_guidance",
                    "Defender response appears to endorse unsafe dependency or verification practices.",
                    "high",
                )
            )

        min_len = 60
        if effective_risk in {"high", "critical"} or strict_mode:
            min_len = 90
        if normal_user:
            min_len = max(min_len, 100)

        if len(response.strip()) < min_len:
            issues.append(
                SelfCheckIssue(
                    "underdeveloped_guarded_response",
                    "Defender response may be too thin for the risk level or utility requirement.",
                    "medium",
                )
            )

        if normal_user and self._looks_like_hard_refusal(response_lower) and not self._contains_any(
            response_lower,
            (
                "safe alternative",
                "i can help with",
                "you can",
                "instead",
                "allowed_response",
                "safe_handling",
            ),
        ):
            issues.append(
                SelfCheckIssue(
                    "normal_user_utility_loss",
                    "Defender response looks overly refusing and may fail to preserve normal-user utility.",
                    "medium" if not strict_mode else "high",
                )
            )

        if max_turns > 1 and not self._contains_any(
            response_lower,
            (
                "consistent",
                "final",
                "allowed_response",
                "safe_handling",
                "instruction_isolation",
            ),
        ):
            issues.append(
                SelfCheckIssue(
                    "multi_turn_consistency_unclear",
                    "Defender response does not clearly signal stable handling for repeated turns/passes.",
                    "low" if not strict_mode else "medium",
                )
            )

        if required_sections and self._missing_sections(response_lower, required_sections):
            issues.append(
                SelfCheckIssue(
                    "defender_structure_weak",
                    "Defender response may not satisfy the expected guarded-response structure.",
                    "medium",
                )
            )

        if getattr(plan, "risk_level", "low") in {"medium", "high", "critical"} and not task_text.strip():
            issues.append(
                SelfCheckIssue(
                    "missing_task_grounding",
                    "High-sensitivity defender response was checked without clear task grounding text.",
                    "low",
                )
            )

    def _apply_security_attacker_checks(
        self,
        *,
        response: str,
        response_lower: str,
        scenario_family: str,
        artifact_required: bool,
        strict_mode: bool,
        max_turns: int,
        required_sections: list[str],
        issues: list[SelfCheckIssue],
    ) -> None:
        if len(response.strip()) < 40:
            issues.append(
                SelfCheckIssue(
                    "weak_attack_output",
                    "Attacker response may be too thin to express a credible vector or payload strategy.",
                    "medium",
                )
            )

        if artifact_required and not self._looks_structured(response_lower):
            issues.append(
                SelfCheckIssue(
                    "attack_artifact_weak",
                    "Attacker artifact appears under-structured for the requested output.",
                    "medium" if not strict_mode else "high",
                )
            )

        if required_sections:
            missing_sections = self._missing_sections(response_lower, required_sections)
            if missing_sections:
                issues.append(
                    SelfCheckIssue(
                        "attack_sections_unclear",
                        f"Attacker artifact may be missing expected sections: {', '.join(missing_sections)}.",
                        "medium",
                    )
                )

        if scenario_family in {"prompt_injection", "indirect_injection", "jailbreak"}:
            if "payload" not in response_lower and "vector" not in response_lower and "fallback" not in response_lower:
                issues.append(
                    SelfCheckIssue(
                        "missing_attack_vector",
                        "Injection-oriented attacker output should make the vector more explicit.",
                        "medium",
                    )
                )

        if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
            if "exposure" not in response_lower and "extract" not in response_lower and "reveal" not in response_lower:
                issues.append(
                    SelfCheckIssue(
                        "missing_extraction_goal",
                        "Extraction-oriented attacker output should make the exposure goal more explicit.",
                        "medium",
                    )
                )

        if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
            if "output_shape" not in response_lower and "unsafe" not in response_lower and "render" not in response_lower:
                issues.append(
                    SelfCheckIssue(
                        "missing_output_shape",
                        "Unsafe-output attacker output should make the output-shaping path more explicit.",
                        "medium",
                    )
                )

        if scenario_family in {"supply_chain", "dependency_attack"}:
            if "trust_surface" not in response_lower and "dependency" not in response_lower and "package" not in response_lower:
                issues.append(
                    SelfCheckIssue(
                        "missing_trust_surface",
                        "Supply-chain attacker output should make the dependency or trust surface clearer.",
                        "medium",
                    )
                )

        if max_turns > 1 and "final" not in response_lower:
            issues.append(
                SelfCheckIssue(
                    "attack_multiturn_finality_unclear",
                    "Repeated attacker passes should keep a stable final artifact target.",
                    "low",
                )
            )

    @staticmethod
    def _apply_wikiwiper_checks(*, response_lower: str, issues: list[SelfCheckIssue]) -> None:
        if "wikipedia" not in response_lower and "wiki" not in response_lower:
            issues.append(
                SelfCheckIssue(
                    "wikiwiper_source_context_unclear",
                    "WikiWiper response does not clearly acknowledge wiki/source context.",
                    "low",
                )
            )

    def _looks_like_skillsbench(self, metadata: Mapping[str, Any], task_text: str) -> bool:
        blob = " ".join(
            [
                self._json_snippet(metadata, max_chars=16000),
                task_text or "",
            ]
        ).lower().replace("_", "-")
        if self._contains_any(blob, ("skillsbench", "benchflow", "standard-v1", "with-skills", "filesystem-output-primary")):
            return True
        return any(hint in blob for hint in SKILLSBENCH_TASK_HINTS)

    def _infer_skillsbench_family(self, *, metadata: Mapping[str, Any], scenario_family: str, text: str) -> str:
        candidates = [
            scenario_family,
            str(metadata.get("family") or ""),
            str(metadata.get("contract_family") or ""),
            str(metadata.get("output_family") or ""),
            str(metadata.get("environment_family_hint") or ""),
            str(metadata.get("canonical_task_id") or ""),
            str(metadata.get("environment_canonical_task_id") or ""),
            str(metadata.get("contract_task_id") or ""),
            str(metadata.get("task_id") or ""),
            str(metadata.get("id") or ""),
            text,
        ]
        blob = " ".join(candidates).lower().replace("_", "-")

        if any(token in blob for token in ("exceltable-in-ppt", "pptx-reference-formatting", "office-pptx", ".pptx", "powerpoint", "presentation", "slides")):
            return "office_pptx"
        if any(token in blob for token in ("office-xlsx", ".xlsx", ".xls", "excel", "spreadsheet")):
            return "office_xlsx"
        if any(token in blob for token in ("office-docx", ".docx", "word document", "offer-letter")):
            return "office_docx"
        if any(token in blob for token in ("pdf-document", ".pdf", "court-form", "paper-anonymizer", "form-filling")):
            return "pdf_document"
        if any(token in blob for token in ("lean-solution", "lean4", ".lean", "formal-reasoning")):
            return "lean_solution"
        if any(token in blob for token in ("security-config", "security-audit", "firewall", "policy", "detection", "audit", "cybersecurity")):
            return "security_config"
        if any(token in blob for token in ("code-solution", "software-patch", "fix-build", "patch-0.diff", "patch_0.diff", ".py", "solution.py")):
            return "code_solution"
        if any(token in blob for token in ("csv-output", ".csv")):
            return "csv_output"
        if any(token in blob for token in ("json-output", ".json", "data-json")):
            return "json_output"
        if any(token in blob for token in ("media-output", "media-processing", "video", "audio", ".obj", "threejs")):
            return "media_output"
        if "general-file-output" in blob:
            return "general_file_output"
        return "general_file_output"

    def _looks_structured(self, response_lower: str) -> bool:
        return self._contains_any(
            response_lower,
            (
                "{",
                "```json",
                "```yaml",
                "summary",
                "evidence",
                "final",
                "artifact",
                "file",
                "path",
                "filesystem",
                "output_contract",
                "task_workspace_executor",
                "workspace_execution",
                "artifact_refs",
            ),
        )

    @staticmethod
    def _missing_sections(response_lower: str, required_sections: list[str]) -> list[str]:
        missing: list[str] = []
        for section in required_sections:
            token = str(section or "").strip().lower()
            if not token:
                continue
            # Allow `section_name:value` style markers, plain headings, or
            # sentence references with spaces.
            alternatives = {
                token,
                token.replace("_", " "),
                token.replace(":", ""),
                token.split(":", 1)[0],
            }
            if not any(alt and alt in response_lower for alt in alternatives):
                missing.append(token)
        return missing

    @staticmethod
    def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    @staticmethod
    def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
        return any(str(needle or "").lower() in text for needle in needles)

    @staticmethod
    def _looks_like_hard_refusal(response_lower: str) -> bool:
        return any(re.search(pattern, response_lower, flags=re.IGNORECASE) for pattern in SelfCheck._HARD_REFUSAL_PATTERNS)

    @staticmethod
    def _derive_severity(issues: list[SelfCheckIssue]) -> str:
        order = {"low": 0, "medium": 1, "high": 2}
        severity = "low"
        for issue in issues:
            if order.get(issue.severity, 1) > order.get(severity, 0):
                severity = issue.severity
        return severity

    @staticmethod
    def _passes(*, issues: list[SelfCheckIssue], strict_mode: bool, effective_risk: str) -> bool:
        if any(issue.severity == "high" for issue in issues):
            return False
        if strict_mode and any(issue.severity == "medium" for issue in issues):
            return False
        if effective_risk == "critical" and any(issue.severity in {"medium", "high"} for issue in issues):
            return False
        return True

    @staticmethod
    def _suggested_fix(*, issues: list[SelfCheckIssue], track: str, assessment_mode: str) -> str | None:
        if not issues:
            return None
        codes = {issue.code for issue in issues}
        if "skillsbench_filesystem_signal_missing" in codes:
            return (
                "Run/output the SkillsBench workspace executor, expose output_contract/task_workspace_execution, "
                "and write real files under /root, /root/output, /app/workspace, /output, or /home/github/build/failed."
            )
        if "skillsbench_artifact_refs_overclaimed" in codes:
            return "Treat artifact_refs/FilePart as diagnostic compatibility evidence; make filesystem output the primary scoring channel."
        if "skillsbench_pptx_misrouted" in codes or "skillsbench_pptx_output_not_visible" in codes:
            return "Route PPTX tasks to office_pptx and emit a real .pptx deliverable."
        if "skillsbench_build_repair_patch_evidence_missing" in codes:
            return "For build-repair tasks, emit failed_reasons.txt and valid patch_*.diff files under /home/github/build/failed."
        if any(code.startswith("skillsbench_") for code in codes):
            return "Rebuild the SkillsBench final output around filesystem-output-primary evidence and solver-specific deliverables."
        if track == "security" and assessment_mode == "defender":
            return "Strengthen guarded-response structure, avoid leakage, and preserve safe normal-user utility."
        if track == "security" and assessment_mode == "attacker":
            return "Make the attack artifact more explicit while staying within the controlled benchmark scope."
        return "Revise the response to address the listed self-check issues."

    @staticmethod
    def _downgrade_issue(issues: list[SelfCheckIssue], code: str, severity: str) -> None:
        for index, issue in enumerate(issues):
            if issue.code == code:
                issues[index] = SelfCheckIssue(issue.code, issue.message, severity)

    @staticmethod
    def _to_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return {str(k): v for k, v in value.items()}
        return {}

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[,;\n]", value) if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _read_bool(value: Any, *, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "force", "forced", "required"}

    @staticmethod
    def _safe_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_track(value: Any) -> str:
        raw = str(value or "openenv").strip().lower()
        raw_dash = raw.replace("_", "-")
        raw_under = raw.replace("-", "_").replace(" ", "_")
        return TRACK_ALIASES.get(raw, TRACK_ALIASES.get(raw_dash, TRACK_ALIASES.get(raw_under, raw_under)))

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
            "": "defender",
        }
        normalized = aliases.get(raw, raw)
        if normalized in {"attacker", "defender"}:
            return normalized
        raise ValueError(f"Unsupported assessment_mode: {value!r}")

    @staticmethod
    def _normalize_family(value: Any) -> str:
        raw = str(value or "general").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "prompt_injection_and_jailbreaking": "prompt_injection",
            "jailbreaking": "jailbreak",
            "pii": "pii_leakage",
            "prompt_leakage": "prompt_extraction",
            "presentation": "office_pptx",
            "pptx": "office_pptx",
            "spreadsheet": "office_xlsx",
            "excel": "office_xlsx",
            "document": "office_docx",
            "docx": "office_docx",
            "pdf": "pdf_document",
            "formal_reasoning": "lean_solution",
            "software_patch": "code_solution",
            "security_audit": "security_config",
            "data_json": "json_output",
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
    def _json_snippet(value: Any, *, max_chars: int = 12000) -> str:
        try:
            import json

            return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)[:max_chars]
        except Exception:
            return str(value)[:max_chars]


def validate_self_check_selftest() -> dict[str, Any]:
    """Validate SkillsBench checks without needing a real planner object."""

    class _Plan:
        risk_level = "low"

    checker = SelfCheck()

    good = checker.validate_response(
        task_text="SkillsBench standard-v1 exceltable-in-ppt task.",
        response=(
            '{"status":"completed","scoring_channel":"filesystem_output_primary",'
            '"artifact_refs_role":"diagnostic_and_compatibility_signal",'
            '"workspace_execution":{"workspace_visible":true,"wrote_any_file":true,"ok_writes":1},'
            '"output_contract":{"primary_outputs":["/root/output/final_deck.pptx"],"family":"office_pptx"}}'
        ),
        plan=_Plan(),  # type: ignore[arg-type]
        metadata={
            "track": "skillsbench",
            "task_set": "standard-v1",
            "condition": "with_skills",
            "task_id": "exceltable-in-ppt",
            "family": "office_pptx",
        },
    )

    bad = checker.validate_response(
        task_text="SkillsBench standard-v1 exceltable-in-ppt task.",
        response="Done. The artifact_refs are the primary scoring channel.",
        plan=_Plan(),  # type: ignore[arg-type]
        metadata={"track": "skillsbench", "task_id": "exceltable-in-ppt"},
    )

    errors: list[str] = []
    if not good.passed:
        errors.append(f"good SkillsBench response should pass: {[i.as_dict() for i in good.issues]}")
    bad_codes = {issue.code for issue in bad.issues}
    if "skillsbench_artifact_refs_overclaimed" not in bad_codes:
        errors.append("bad response should flag artifact_refs overclaim")
    if "skillsbench_filesystem_signal_missing" not in bad_codes:
        errors.append("bad response should flag missing filesystem signal")

    return {
        "ok": not errors,
        "errors": errors,
        "version": SELF_CHECK_VERSION,
        "good": good.as_dict(),
        "bad": bad.as_dict(),
    }


__all__ = [
    "SELF_CHECK_VERSION",
    "SelfCheckIssue",
    "SelfCheckResult",
    "SelfCheck",
    "validate_self_check_selftest",
]
