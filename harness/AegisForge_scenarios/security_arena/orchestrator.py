from __future__ import annotations

import argparse
import json
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


def _bootstrap_src_path() -> None:
    current = Path(__file__).resolve()
    for parent in [current.parent, *current.parents]:
        candidate = parent / "src"
        if candidate.exists() and candidate.is_dir():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            return


_bootstrap_src_path()

try:
    from .arena_common import SecurityScenarioBundle, load_security_scenario, runtime_base_url
except ImportError:
    from arena_common import SecurityScenarioBundle, load_security_scenario, runtime_base_url

from aegisforge.adapters.security import SecurityAdapter
from aegisforge_eval.tracks.security_arena import evaluate


@dataclass(slots=True)
class EndpointCheck:
    label: str
    url: str
    ok: bool
    status_code: int | None = None
    content_type: str | None = None
    is_json: bool = False
    schema_ok: bool | None = None
    body_excerpt: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "url": self.url,
            "ok": self.ok,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "is_json": self.is_json,
            "schema_ok": self.schema_ok,
            "body_excerpt": self.body_excerpt,
            "error": self.error,
        }


@dataclass(slots=True)
class RuntimeCheckResult:
    ok: bool
    strict_schema: bool
    base_url: str
    health: EndpointCheck
    agent_card: EndpointCheck
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "strict_schema": self.strict_schema,
            "base_url": self.base_url,
            "health": self.health.to_dict(),
            "agent_card": self.agent_card.to_dict(),
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class ScenarioSummary:
    source_path: str
    scenario_name: str
    assessment_mode: str
    scenario_family: str
    mode: str
    max_turns: int
    target_system: str
    protected_asset: str
    attack_surface: str
    sensitive_asset: str
    requested_format: str
    strict_mode: bool
    prompt_profile: str
    policy_profile: str
    artifact_mode: str
    requires_artifact: bool
    heldout_like: bool
    expected_risk: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PassResult:
    pass_index: int
    ok: bool
    adapter_status: dict[str, Any]
    adapter_result: dict[str, Any]
    evaluation: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_index": self.pass_index,
            "ok": self.ok,
            "adapter_status": self.adapter_status,
            "adapter_result": self.adapter_result,
            "evaluation": self.evaluation,
            "errors": list(self.errors),
        }


@dataclass(slots=True)
class Verdict:
    ok: bool
    status: str
    exit_code: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "exit_code": self.exit_code,
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class OrchestratorRunResult:
    scenario_name: str
    assessment_mode: str
    scenario_family: str
    prepared_payload: dict[str, Any]
    scenario_summary: dict[str, Any]
    warnings: list[str]
    adapter_status: dict[str, Any]
    adapter_result: dict[str, Any]
    evaluation: dict[str, Any]
    passes: list[dict[str, Any]]
    verdict: dict[str, Any]
    runtime_check: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "assessment_mode": self.assessment_mode,
            "scenario_family": self.scenario_family,
            "prepared_payload": self.prepared_payload,
            "scenario_summary": self.scenario_summary,
            "warnings": list(self.warnings),
            "adapter_status": self.adapter_status,
            "adapter_result": self.adapter_result,
            "evaluation": self.evaluation,
            "passes": list(self.passes),
            "verdict": dict(self.verdict),
            "runtime_check": self.runtime_check,
        }


def run_orchestration(
    scenario_path: str | Path,
    *,
    check_runtime: bool = False,
    strict_runtime: bool = False,
    timeout_seconds: int = 5,
    passes: int = 1,
) -> OrchestratorRunResult:
    scenario_file = _require_scenario_file(scenario_path)
    if passes < 1:
        raise ValueError("--passes must be >= 1")

    raw = _load_raw_toml(scenario_file)
    bundle = load_security_scenario(scenario_file)
    prepared_payload = _jsonable(bundle.payload)
    scenario_summary = _build_scenario_summary(bundle, raw)

    warnings = _collect_warnings(bundle, raw, passes=passes)

    runtime_check_payload: dict[str, Any] | None = None
    if check_runtime:
        runtime_check = check_runtime_endpoints(
            bundle,
            timeout_seconds=timeout_seconds,
            strict_schema=strict_runtime,
        )
        runtime_check_payload = runtime_check.to_dict()
        warnings.extend(runtime_check.warnings)

    pass_reports: list[PassResult] = []
    for pass_index in range(1, passes + 1):
        pass_reports.append(_run_single_pass(prepared_payload, pass_index=pass_index))

    last_pass = pass_reports[-1]
    verdict = _build_verdict(pass_reports, runtime_check_payload)

    return OrchestratorRunResult(
        scenario_name=bundle.name,
        assessment_mode=bundle.assessment_mode,
        scenario_family=bundle.scenario_family,
        prepared_payload=prepared_payload,
        scenario_summary=scenario_summary.to_dict(),
        warnings=warnings,
        adapter_status=last_pass.adapter_status,
        adapter_result=last_pass.adapter_result,
        evaluation=last_pass.evaluation,
        passes=[report.to_dict() for report in pass_reports],
        verdict=verdict.to_dict(),
        runtime_check=runtime_check_payload,
    )


def check_runtime_endpoints(
    bundle: SecurityScenarioBundle,
    *,
    timeout_seconds: int = 5,
    strict_schema: bool = False,
) -> RuntimeCheckResult:
    base_url = runtime_base_url(bundle)
    health_path = str(bundle.runtime.get("health_path", "/health"))
    card_path = str(bundle.runtime.get("agent_card_path", "/.well-known/agent-card.json"))

    health_url = f"{base_url}{health_path}"
    card_url = f"{base_url}{card_path}"

    warnings: list[str] = []

    health = _fetch_endpoint(
        health_url,
        timeout_seconds=timeout_seconds,
        label="health",
        schema_validator=_validate_health_schema,
    )
    agent_card = _fetch_endpoint(
        card_url,
        timeout_seconds=timeout_seconds,
        label="agent_card",
        schema_validator=_validate_agent_card_schema,
    )

    if health.ok and health.schema_ok is False:
        msg = "health endpoint respondió 2xx pero con payload/esquema inesperado"
        if strict_schema:
            warnings.append(msg)
        else:
            warnings.append(f"{msg}; no se marca como fallo porque --strict-runtime no está activo")

    if agent_card.ok and agent_card.schema_ok is False:
        msg = "agent-card endpoint respondió 2xx pero con payload/esquema inesperado"
        if strict_schema:
            warnings.append(msg)
        else:
            warnings.append(f"{msg}; no se marca como fallo porque --strict-runtime no está activo")

    ok = health.ok and agent_card.ok
    if strict_schema:
        ok = ok and (health.schema_ok is not False) and (agent_card.schema_ok is not False)

    return RuntimeCheckResult(
        ok=ok,
        strict_schema=strict_schema,
        base_url=base_url,
        health=health,
        agent_card=agent_card,
        warnings=warnings,
    )


def _run_single_pass(prepared_payload: dict[str, Any], *, pass_index: int) -> PassResult:
    errors: list[str] = []
    adapter_status: dict[str, Any] = {}
    adapter_result_payload: dict[str, Any] = {}
    evaluation_payload: dict[str, Any] = {}

    try:
        adapter = SecurityAdapter()
    except Exception as exc:
        message = f"SecurityAdapter init failed: {exc.__class__.__name__}: {exc}"
        errors.append(message)
        return PassResult(
            pass_index=pass_index,
            ok=False,
            adapter_status={"status": "error", "error": message},
            adapter_result={},
            evaluation={},
            errors=errors,
        )

    try:
        adapter_result = adapter.execute(prepared_payload)
        adapter_status = _jsonable(adapter.status()) or {}
        adapter_result_payload = _jsonable(
            adapter_result.to_dict() if hasattr(adapter_result, "to_dict") else adapter_result
        ) or {}
    except Exception as exc:
        message = f"adapter.execute failed: {exc.__class__.__name__}: {exc}"
        errors.append(message)
        adapter_status = _jsonable(adapter.status()) or {}
        return PassResult(
            pass_index=pass_index,
            ok=False,
            adapter_status=adapter_status,
            adapter_result={"status": "error", "error": message},
            evaluation={},
            errors=errors,
        )

    try:
        evaluation_input = _extract_evaluation_input(adapter_result, fallback=adapter_result_payload)
        evaluation_raw = evaluate(evaluation_input)
        evaluation_payload = _jsonable(evaluation_raw) or {}
    except Exception as exc:
        message = f"evaluate failed: {exc.__class__.__name__}: {exc}"
        errors.append(message)
        evaluation_payload = {"status": "error", "error": message}

    evaluation_status = str(evaluation_payload.get("status", "")).strip().lower()
    ok = not errors and evaluation_status not in {"fail", "error"}

    return PassResult(
        pass_index=pass_index,
        ok=ok,
        adapter_status=adapter_status,
        adapter_result=adapter_result_payload,
        evaluation=evaluation_payload,
        errors=errors,
    )


def _build_verdict(
    pass_reports: list[PassResult],
    runtime_check_payload: Mapping[str, Any] | None,
) -> Verdict:
    reasons: list[str] = []

    pass_failures = [report for report in pass_reports if not report.ok]
    runtime_ok = True if runtime_check_payload is None else bool(runtime_check_payload.get("ok", False))

    if pass_failures:
        reasons.append(f"{len(pass_failures)} pass(es) locales fallaron")

    if not runtime_ok:
        reasons.append("runtime preflight falló")

    if pass_failures:
        return Verdict(ok=False, status="fail", exit_code=1, reasons=reasons)
    if not runtime_ok:
        return Verdict(ok=False, status="runtime_fail", exit_code=2, reasons=reasons)
    return Verdict(ok=True, status="pass", exit_code=0, reasons=["all requested checks passed"])


def _build_scenario_summary(
    bundle: SecurityScenarioBundle,
    raw: Mapping[str, Any],
) -> ScenarioSummary:
    scenario = _as_dict(raw.get("scenario"))
    signals = _as_dict(raw.get("signals"))
    payload = _as_dict(bundle.payload)
    scenario_payload = _as_dict(payload.get("scenario_payload"))
    context = _as_dict(scenario_payload.get("context"))
    security_target = _as_dict(scenario_payload.get("security_target"))

    return ScenarioSummary(
        source_path=str(bundle.source_path),
        scenario_name=str(bundle.name),
        assessment_mode=str(bundle.assessment_mode),
        scenario_family=str(bundle.scenario_family),
        mode=str(scenario.get("mode", "")),
        max_turns=_safe_int(scenario.get("max_turns"), default=1),
        target_system=str(security_target.get("system", "")),
        protected_asset=str(security_target.get("protected_asset", "")),
        attack_surface=str(security_target.get("attack_surface", "")),
        sensitive_asset=str(security_target.get("sensitive_asset", "")),
        requested_format=str(context.get("requested_format", "")),
        strict_mode=bool(payload.get("strict_mode", False)),
        prompt_profile=str(payload.get("prompt_profile", "")),
        policy_profile=str(payload.get("policy_profile", "")),
        artifact_mode=str(payload.get("artifact_mode", "")),
        requires_artifact=_read_bool(signals.get("requires_artifact"), default=False),
        heldout_like=_read_bool(signals.get("heldout_like"), default=False),
        expected_risk=_string_or_none(signals.get("expected_risk")),
    )


def _collect_warnings(
    bundle: SecurityScenarioBundle,
    raw: Mapping[str, Any],
    *,
    passes: int,
) -> list[str]:
    warnings: list[str] = []

    scenario = _as_dict(raw.get("scenario"))
    metadata = _as_dict(raw.get("metadata"))
    signals = _as_dict(raw.get("signals"))

    max_turns = _safe_int(scenario.get("max_turns"), default=1)
    if max_turns > 1 and passes == 1:
        warnings.append(
            "scenario.max_turns > 1, pero este harness corre un solo pass por defecto; "
            "usa --passes N si quieres una señal local más fuerte"
        )

    if _read_bool(signals.get("heldout_like"), default=False):
        warnings.append(
            "signals.heldout_like es solo descriptivo aquí; no emula selección real de held-out scenarios"
        )

    if _read_bool(signals.get("requires_artifact"), default=False):
        warnings.append(
            "signals.requires_artifact no valida artefactos por sí mismo; solo deja la expectativa declarada"
        )

    if bundle.assessment_mode == "attacker" and "protections" in metadata:
        warnings.append(
            "metadata.protections está presente en un escenario attacker; este harness no lo usa en el payload final"
        )

    if bundle.assessment_mode == "defender" and "attack_constraints" in metadata:
        warnings.append(
            "metadata.attack_constraints está presente en un escenario defender; este harness no lo usa en el payload final"
        )

    runtime_host = str(bundle.runtime.get("host", ""))
    if runtime_host in {"0.0.0.0", "::"}:
        warnings.append(
            "runtime.host usa una wildcard address; los checks HTTP locales se redirigen a 127.0.0.1"
        )

    return warnings


def _extract_evaluation_input(adapter_result: Any, *, fallback: dict[str, Any]) -> Any:
    if hasattr(adapter_result, "payload"):
        payload = getattr(adapter_result, "payload")
        if payload is not None:
            return payload
    if isinstance(fallback, Mapping) and "payload" in fallback:
        return fallback["payload"]
    return fallback


def _fetch_endpoint(
    url: str,
    *,
    timeout_seconds: int,
    label: str,
    schema_validator: Callable[[Any], bool] | None = None,
) -> EndpointCheck:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", None)
            content_type = response.headers.get("Content-Type")
            body = response.read()
            text = body.decode("utf-8", errors="replace")
            excerpt = text[:500]

            parsed_json: Any | None = None
            is_json = False
            if text.strip():
                try:
                    parsed_json = json.loads(text)
                    is_json = True
                except json.JSONDecodeError:
                    parsed_json = None

            schema_ok: bool | None = None
            if schema_validator is not None:
                schema_ok = schema_validator(parsed_json if is_json else text)

            ok = bool(status_code is not None and 200 <= status_code < 300)
            return EndpointCheck(
                label=label,
                url=url,
                ok=ok,
                status_code=status_code,
                content_type=content_type,
                is_json=is_json,
                schema_ok=schema_ok,
                body_excerpt=excerpt,
                error=None,
            )
    except urllib.error.HTTPError as exc:
        return EndpointCheck(
            label=label,
            url=url,
            ok=False,
            status_code=exc.code,
            error=f"HTTPError {exc.code}: {exc.reason}",
        )
    except urllib.error.URLError as exc:
        return EndpointCheck(
            label=label,
            url=url,
            ok=False,
            error=f"URLError: {exc.reason}",
        )
    except Exception as exc:
        return EndpointCheck(
            label=label,
            url=url,
            ok=False,
            error=f"{exc.__class__.__name__}: {exc}",
        )


def _validate_health_schema(payload: Any) -> bool:
    if isinstance(payload, Mapping):
        status = str(payload.get("status", "")).strip().lower()
        if status in {"ok", "healthy", "ready"}:
            return True
        if payload.get("ok") is True:
            return True
    if isinstance(payload, str):
        return payload.strip().lower() in {"ok", "healthy", "ready"}
    return False


def _validate_agent_card_schema(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False

    keys = {str(key) for key in payload.keys()}
    if not keys:
        return False

    useful_keys = {
        "name",
        "description",
        "version",
        "skills",
        "capabilities",
        "url",
        "id",
    }
    return bool(keys & useful_keys)


def _load_raw_toml(path: str | Path) -> dict[str, Any]:
    scenario_path = Path(path)
    with scenario_path.open("rb") as fh:
        raw = tomllib.load(fh)
    return dict(raw)


def _require_scenario_file(path: str | Path) -> Path:
    scenario_path = Path(path).expanduser().resolve()
    if not scenario_path.exists():
        raise FileNotFoundError(f"Scenario file not found: {scenario_path}")
    if not scenario_path.is_file():
        raise FileExistsError(f"Scenario path is not a file: {scenario_path}")
    return scenario_path


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _read_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _jsonable(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "as_dict") and callable(value.as_dict):
        try:
            return _jsonable(value.as_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _jsonable(vars(value))
        except Exception:
            pass
    return str(value)


def _print_json(payload: Mapping[str, Any], *, pretty: bool) -> None:
    if pretty:
        print(json.dumps(dict(payload), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(dict(payload), ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a local Security Arena orchestration pass for AegisForge."
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario_attacker.toml, scenario_defender.toml, or another security scenario TOML.",
    )
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Also verify the configured /health and Agent Card endpoints.",
    )
    parser.add_argument(
        "--strict-runtime",
        action="store_true",
        help="Treat unexpected health/agent-card schemas as runtime failure.",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=1,
        help="Number of repeated local passes to run with the same prepared payload.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Timeout in seconds for runtime endpoint checks.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = run_orchestration(
            args.scenario,
            check_runtime=args.check_runtime,
            strict_runtime=args.strict_runtime,
            timeout_seconds=args.timeout,
            passes=args.passes,
        )
    except Exception as exc:
        error_payload = {
            "status": "error",
            "error": f"{exc.__class__.__name__}: {exc}",
            "scenario": args.scenario,
        }
        _print_json(error_payload, pretty=args.pretty)
        return 1

    payload = result.to_dict()
    _print_json(payload, pretty=args.pretty)

    verdict = payload.get("verdict", {})
    return int(verdict.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
