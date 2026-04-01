from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from ...utils.validation import require_int_in_range, require_non_empty_string


_ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_optional_string(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip()


def _read_optional_risk(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if value not in _ALLOWED_RISK_LEVELS:
        raise ValueError(
            f"{name} must be one of {sorted(_ALLOWED_RISK_LEVELS)}, got {raw!r}"
        )
    return value


def _normalize_mode(value: str | None) -> str:
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
    raise ValueError(f"Unsupported security assessment mode: {value!r}")


def _normalize_family(value: str | None) -> str:
    raw = str(value or "general").strip().lower()
    aliases = {
        "prompt_injection_and_jailbreaking": "prompt_injection",
        "jailbreaking": "jailbreak",
        "prompt_leakage": "prompt_extraction",
        "pii": "pii_leakage",
    }
    return aliases.get(raw, raw)


@dataclass(slots=True)
class SecurityAdapterConfig:
    enabled: bool
    role: str
    scenario_name: str
    scenario_family: str
    target_system: str
    protected_asset: str
    attack_surface: str
    sensitive_asset: str
    timeout_seconds: int
    strict_mode: bool
    prompt_profile: str
    policy_profile: str
    artifact_mode: str
    max_turns: int
    normal_user: bool
    expected_risk: str | None

    @property
    def assessment_mode(self) -> str:
        return self.role

    @classmethod
    def from_env(cls) -> "SecurityAdapterConfig":
        enabled = _read_bool_env("AEGISFORGE_ENABLE_SECURITY", False)

        role = _normalize_mode(
            os.environ.get("SECURITY_ASSESSMENT_MODE")
            or os.environ.get("SECURITY_ROLE")
            or "defender"
        )

        scenario_name = require_non_empty_string(
            os.environ.get("SECURITY_SCENARIO_NAME", "security-default"),
            "SECURITY_SCENARIO_NAME",
        )

        scenario_family = _normalize_family(
            require_non_empty_string(
                os.environ.get("SECURITY_SCENARIO_FAMILY", "prompt_injection"),
                "SECURITY_SCENARIO_FAMILY",
            )
        )

        target_system = require_non_empty_string(
            os.environ.get("SECURITY_TARGET_SYSTEM", "security_target"),
            "SECURITY_TARGET_SYSTEM",
        )
        protected_asset = require_non_empty_string(
            os.environ.get("SECURITY_PROTECTED_ASSET", _default_protected_asset(scenario_family)),
            "SECURITY_PROTECTED_ASSET",
        )
        attack_surface = require_non_empty_string(
            os.environ.get("SECURITY_ATTACK_SURFACE", _default_attack_surface(scenario_family)),
            "SECURITY_ATTACK_SURFACE",
        )
        sensitive_asset = require_non_empty_string(
            os.environ.get("SECURITY_SENSITIVE_ASSET", protected_asset),
            "SECURITY_SENSITIVE_ASSET",
        )

        timeout_seconds = require_int_in_range(
            int(os.environ.get("SECURITY_TIMEOUT_SECONDS", "30")),
            "SECURITY_TIMEOUT_SECONDS",
            minimum=1,
            maximum=300,
        )

        strict_mode = _read_bool_env("SECURITY_STRICT_MODE", False)

        prompt_profile = require_non_empty_string(
            _read_optional_string("SECURITY_PROMPT_PROFILE", _default_prompt_profile(role)),
            "SECURITY_PROMPT_PROFILE",
        )
        policy_profile = require_non_empty_string(
            _read_optional_string(
                "SECURITY_POLICY_PROFILE",
                _default_policy_profile(role, scenario_family),
            ),
            "SECURITY_POLICY_PROFILE",
        )
        artifact_mode = require_non_empty_string(
            _read_optional_string("SECURITY_ARTIFACT_MODE", _default_artifact_mode(role)),
            "SECURITY_ARTIFACT_MODE",
        )

        max_turns = require_int_in_range(
            int(os.environ.get("SECURITY_MAX_TURNS", "1")),
            "SECURITY_MAX_TURNS",
            minimum=1,
            maximum=50,
        )

        normal_user = _read_bool_env("SECURITY_NORMAL_USER", False)
        expected_risk = _read_optional_risk("SECURITY_EXPECTED_RISK")

        return cls(
            enabled=enabled,
            role=role,
            scenario_name=scenario_name,
            scenario_family=scenario_family,
            target_system=target_system,
            protected_asset=protected_asset,
            attack_surface=attack_surface,
            sensitive_asset=sensitive_asset,
            timeout_seconds=timeout_seconds,
            strict_mode=strict_mode,
            prompt_profile=prompt_profile,
            policy_profile=policy_profile,
            artifact_mode=artifact_mode,
            max_turns=max_turns,
            normal_user=normal_user,
            expected_risk=expected_risk,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _default_protected_asset(scenario_family: str) -> str:
    if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
        return "protected_prompts_or_sensitive_internal_data"
    if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
        return "safe_output_boundary"
    if scenario_family in {"supply_chain", "dependency_attack"}:
        return "dependency_trust_boundary"
    return "instruction_hierarchy_and_protected_behavior"


def _default_attack_surface(scenario_family: str) -> str:
    if scenario_family in {"prompt_injection", "indirect_injection", "jailbreak"}:
        return "instruction_hierarchy_or_embedded_context"
    if scenario_family in {"prompt_extraction", "data_exfiltration", "pii_leakage", "secret_leakage"}:
        return "prompt_or_secret_exposure_path"
    if scenario_family in {"insecure_output", "xss", "malicious_link", "dangerous_code"}:
        return "rendered_output_or_generated_code_path"
    if scenario_family in {"supply_chain", "dependency_attack"}:
        return "dependency_origin_or_verification_path"
    return "trusted_context_and_response_surface"


def _default_prompt_profile(role: str) -> str:
    return "security_attacker" if role == "attacker" else "security_defender"


def _default_policy_profile(role: str, scenario_family: str) -> str:
    if role == "attacker":
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


def _default_artifact_mode(role: str) -> str:
    return "attack_plan" if role == "attacker" else "guarded_response"
