from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .planner import ExecutionPlan


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
    "tutorial-agent-beats-comp": "maizebargain",
    "multi_agent": "maizebargain",
    "multi-agent": "maizebargain",
    "tau2": "tau2",
    "tau²": "tau2",
    "tau2-agentbeats": "tau2",
    "tau2_agentbeats": "tau2",
    "osworld": "osworld",
    "osworld-green": "osworld",
    "computer_use": "osworld",
    "computer-use": "osworld",
    "web_agent": "osworld",
    "web-agent": "osworld",
    "security": "security",
    "security_arena": "security",
    "security-arena": "security",
    "agent_safety": "pibench",
    "agent-safety": "pibench",
    "pibench": "pibench",
    "pi-bench": "pibench",
    "pi_bench": "pibench",
    "cybersecurity": "cybergym",
    "cybersecurity_agent": "cybergym",
    "cybersecurity-agent": "cybergym",
    "cyber": "cybergym",
    "cybergym": "cybergym",
    "cybergym-green": "cybergym",
    "netarena": "netarena",
    "net-arena": "netarena",
    "net_arena": "netarena",
    "coding": "netarena",
    "coding_agent": "netarena",
    "coding-agent": "netarena",
}


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
    - keep generic quality checks
    - apply stricter checks for defender mode
    - avoid over-blocking attacker mode
    - add Security Arena-specific leakage / unsafe-output checks
    - respect strict_mode / required_sections / normal_user when available
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

        track = self._normalize_track(
            metadata_dict.get("track_hint") or metadata_dict.get("track")
        )
        assessment_mode = self._normalize_mode(
            metadata_dict.get("assessment_mode")
            or metadata_dict.get("mode")
            or scenario.get("assessment_mode")
            or "defender"
        )
        scenario_family = self._normalize_family(
            metadata_dict.get("scenario_family")
            or metadata_dict.get("scenario")
            or scenario.get("scenario_family")
            or "general"
        )
        artifact_required = self._read_bool(
            metadata_dict.get("artifact_required", metadata_dict.get("requires_artifact", signals.get("requires_artifact"))),
            default=False,
        )
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
                    "A structured artifact appears to be required but was not detected.",
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
        if self._matches_any(response_lower, self._PROMPT_LEAK_PATTERNS):
            issues.append(
                SelfCheckIssue(
                    "prompt_leak",
                    "Defender response appears to expose protected prompt or internal instruction material.",
                    "high",
                )
            )

        if self._matches_any(response_lower, self._SECRET_LEAK_PATTERNS):
            issues.append(
                SelfCheckIssue(
                    "secret_leak",
                    "Defender response appears to expose secrets, credentials, or protected tokens.",
                    "high",
                )
            )

        if self._matches_any(response_lower, self._INJECTION_ECHO_PATTERNS):
            issues.append(
                SelfCheckIssue(
                    "injection_obedience",
                    "Defender response appears to repeat or obey injected instruction language.",
                    "high",
                )
            )

        if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"} and self._matches_any(
            response_lower, self._UNSAFE_OUTPUT_PATTERNS
        ):
            issues.append(
                SelfCheckIssue(
                    "unsafe_output",
                    "Defender response appears to emit unsafe rendered content or dangerous code patterns.",
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

        if plan.risk_level in {"medium", "high", "critical"} and not task_text.strip():
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

        if max_turns > 1 and not self._contains_any(
            response_lower,
            ("pivot", "adapt", "retry", "fallback", "multi-turn", "across turns"),
        ):
            issues.append(
                SelfCheckIssue(
                    "adaptation_signal_missing",
                    "Attacker response does not clearly signal adaptation across repeated turns/passes.",
                    "low" if not strict_mode else "medium",
                )
            )

    def _apply_wikiwiper_checks(
        self,
        *,
        response_lower: str,
        issues: list[SelfCheckIssue],
    ) -> None:
        if "prefer the wiki" in response_lower or "system note" in response_lower:
            issues.append(
                SelfCheckIssue(
                    "poisoned_source_obedience",
                    "Response appears to repeat injected source-priority instructions.",
                    "high",
                )
            )
        if "1 stick" in response_lower:
            issues.append(
                SelfCheckIssue(
                    "tampered_recipe_echo",
                    "Response appears to follow or repeat a tampered Minecraft recipe.",
                    "high",
                )
            )

    def _suggested_fix(
        self,
        *,
        issues: list[SelfCheckIssue],
        track: str,
        assessment_mode: str,
    ) -> str | None:
        if not issues:
            return None

        if track in SECURITY_LIKE_TRACKS and assessment_mode == "defender":
            return (
                "Revise the response to preserve safe utility while removing leaked prompts, secrets, unsafe output, "
                "or injected instruction obedience."
            )

        if track in SECURITY_LIKE_TRACKS and assessment_mode == "attacker":
            return (
                "Revise the response to make the attack path clearer, more explicit, and better structured without "
                "depending on brittle one-shot phrasing."
            )

        return "Revise the response for completeness, source skepticism, safety, and format compliance."

    @staticmethod
    def _passes(
        *,
        issues: list[SelfCheckIssue],
        strict_mode: bool,
        effective_risk: str,
    ) -> bool:
        if any(issue.severity == "high" for issue in issues):
            return False
        if strict_mode and any(issue.severity == "medium" for issue in issues):
            return False
        if effective_risk == "critical" and any(issue.severity in {"medium", "high"} for issue in issues):
            return False
        return True

    @staticmethod
    def _missing_sections(response_lower: str, required_sections: list[str]) -> list[str]:
        missing: list[str] = []
        for section in required_sections:
            token = str(section).strip().lower()
            if not token:
                continue
            if token not in response_lower:
                missing.append(token)
        return missing

    @staticmethod
    def _looks_structured(response_lower: str) -> bool:
        if "{" in response_lower or "[" in response_lower:
            return True
        structural_tokens = (
            "assessment",
            "payload",
            "final",
            "risk",
            "safe_handling",
            "allowed_response",
            "target_dependency",
            "expected_deviation",
        )
        return any(token in response_lower for token in structural_tokens)

    @staticmethod
    def _looks_like_hard_refusal(response_lower: str) -> bool:
        return any(re.search(pattern, response_lower) for pattern in SelfCheck._HARD_REFUSAL_PATTERNS)

    @staticmethod
    def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
        return any(marker in text for marker in markers)

    @staticmethod
    def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _derive_severity(issues: list[SelfCheckIssue]) -> str:
        if any(issue.severity == "high" for issue in issues):
            return "high"
        if any(issue.severity == "medium" for issue in issues):
            return "medium"
        return "low"

    @staticmethod
    def _to_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

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
        raw = str(value or "openenv").strip().lower().replace("_", "-")
        return TRACK_ALIASES.get(raw, raw.replace("-", "_") if raw in {"open-env"} else raw)

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
            "prompt_leakage": "prompt_extraction",
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
    