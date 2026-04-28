from __future__ import annotations

"""FastAPI server for omnibench_aegis_env using the BaseDomain contract.

This registry-backed version supports:
- Research Agent -> InventoryInject
- Computer Use & Web Agent -> LinkLifter
- Finance Agent -> taxwiztrap
- Multi-agent Evaluation -> BidBot
- τ²-Bench -> TicketTwister
- Game Agent -> wikiwiper
- Business Process Agent -> saleforceone

Key goals:
- avoid the deprecated BaseOpenEnv / StepResult contract entirely;
- satisfy the practical OpenEnv HTTP contract used by the checker;
- keep a stable stateful session between /reset and /step calls;
- expose a safer default /state view while still allowing full debug state.
"""

import os
import platform
import shutil
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..evaluation_lab.models import EvaluationLabRequest
from ..evaluation_lab.report import build_report
from ..evaluation_lab.scanner import scan_repo
from ..base import BaseDomain
from ..domains.business_process import (
    SALEFORCEONE_ENV_ID,
    SALEFORCEONE_SCENARIO_ID,
    BusinessProcessDomain,
)
from ..domains.computer_use import (
    LINKLIFTER_ENV_ID,
    LINKLIFTER_SCENARIO_ID,
    ComputerUseLinkLifterEnv,
)
from ..domains.finance import (
    TAXWIZTRAP_ENV_ID,
    TAXWIZTRAP_SCENARIO_ID,
    FinanceTaxwiztrapEnv,
)
from ..domains.game import (
    WIKIWIPER_ENV_ID,
    WIKIWIPER_SCENARIO_ID,
    GameDomain,
)
from ..domains.multi_agent import (
    BIDBOT_ENV_ID,
    BIDBOT_SCENARIO_ID,
    MultiAgentDomain,
)
from ..domains.research import (
    RESEARCH_ENV_ID,
    SCENARIO_ID as RESEARCH_SCENARIO_ID,
    ResearchInventoryInjectEnv,
)
from ..domains.tau2 import (
    TICKETTWISTER_ENV_ID,
    TICKETTWISTER_SCENARIO_ID,
    Tau2Domain,
)

DEFAULT_VERSION = "0.2.0"
DEFAULT_ENV_ID = RESEARCH_ENV_ID
DEFAULT_SCENARIO_ID = RESEARCH_SCENARIO_ID
DEFAULT_DOMAIN = "research"
DEBUG_STATE_ENV_VAR = "OPENENV_DEBUG_STATE"
DEBUG_ERROR_ENV_VAR = "OPENENV_DEBUG_ERRORS"
EVAL_LAB_GIT_ENV_VAR = "OPENENV_EVAL_LAB_GIT"


@dataclass(frozen=True, slots=True)
class EnvRegistration:
    env_id: str
    scenario_id: str
    domain: str
    env_cls: type[BaseDomain]


def _registration_entries() -> list[EnvRegistration]:
    return [
        EnvRegistration(
            env_id=RESEARCH_ENV_ID,
            scenario_id=RESEARCH_SCENARIO_ID,
            domain="research",
            env_cls=ResearchInventoryInjectEnv,
        ),
        EnvRegistration(
            env_id=LINKLIFTER_ENV_ID,
            scenario_id=LINKLIFTER_SCENARIO_ID,
            domain="computer_use",
            env_cls=ComputerUseLinkLifterEnv,
        ),
        EnvRegistration(
            env_id=TAXWIZTRAP_ENV_ID,
            scenario_id=TAXWIZTRAP_SCENARIO_ID,
            domain="finance",
            env_cls=FinanceTaxwiztrapEnv,
        ),
        EnvRegistration(
            env_id=BIDBOT_ENV_ID,
            scenario_id=BIDBOT_SCENARIO_ID,
            domain="multi_agent",
            env_cls=MultiAgentDomain,
        ),
        EnvRegistration(
            env_id=TICKETTWISTER_ENV_ID,
            scenario_id=TICKETTWISTER_SCENARIO_ID,
            domain="tau2",
            env_cls=Tau2Domain,
        ),
        EnvRegistration(
            env_id=WIKIWIPER_ENV_ID,
            scenario_id=WIKIWIPER_SCENARIO_ID,
            domain="game",
            env_cls=GameDomain,
        ),
        EnvRegistration(
            env_id=SALEFORCEONE_ENV_ID,
            scenario_id=SALEFORCEONE_SCENARIO_ID,
            domain="business_process",
            env_cls=BusinessProcessDomain,
        ),
    ]


_ENV_ENTRIES = _registration_entries()
ENV_REGISTRY: dict[str, EnvRegistration] = {item.env_id.lower(): item for item in _ENV_ENTRIES}
SCENARIO_REGISTRY: dict[str, str] = {
    RESEARCH_SCENARIO_ID.lower(): RESEARCH_ENV_ID.lower(),
    "inventoryinject": RESEARCH_ENV_ID.lower(),
    LINKLIFTER_SCENARIO_ID.lower(): LINKLIFTER_ENV_ID.lower(),
    "linklifter": LINKLIFTER_ENV_ID.lower(),
    TAXWIZTRAP_SCENARIO_ID.lower(): TAXWIZTRAP_ENV_ID.lower(),
    "taxwiztrap": TAXWIZTRAP_ENV_ID.lower(),
    BIDBOT_SCENARIO_ID.lower(): BIDBOT_ENV_ID.lower(),
    "bidbot": BIDBOT_ENV_ID.lower(),
    TICKETTWISTER_SCENARIO_ID.lower(): TICKETTWISTER_ENV_ID.lower(),
    "tickettwister": TICKETTWISTER_ENV_ID.lower(),
    WIKIWIPER_SCENARIO_ID.lower(): WIKIWIPER_ENV_ID.lower(),
    "wikiwiper": WIKIWIPER_ENV_ID.lower(),
    SALEFORCEONE_SCENARIO_ID.lower(): SALEFORCEONE_ENV_ID.lower(),
    "saleforceone": SALEFORCEONE_ENV_ID.lower(),
}


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _prepend_to_path(directory: str | os.PathLike[str] | None) -> None:
    """Prepend an existing directory to PATH once.

    The Evaluation Lab scanner calls `git` through subprocess. On Windows
    terminals launched from VS Code or Conda, Git can be installed but absent
    from PATH, so the static scan fails with a hidden 500. This helper makes
    the server resilient without executing any analyzed repository code.
    """

    if not directory:
        return
    path = os.fspath(directory)
    if not os.path.isdir(path):
        return

    current = os.environ.get("PATH", "")
    parts = [item for item in current.split(os.pathsep) if item]
    normalized = {os.path.normcase(os.path.abspath(item)) for item in parts}
    key = os.path.normcase(os.path.abspath(path))
    if key not in normalized:
        os.environ["PATH"] = path + (os.pathsep + current if current else "")


def _candidate_git_directories() -> list[str]:
    candidates: list[str] = []

    configured = os.getenv(EVAL_LAB_GIT_ENV_VAR)
    if configured:
        configured = os.path.expandvars(os.path.expanduser(configured.strip().strip('"')))
        if configured.lower().endswith(("git.exe", "/git")):
            candidates.append(os.path.dirname(configured))
        else:
            candidates.append(configured)

    if platform.system().lower() == "windows":
        for root in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ):
            if not root:
                continue
            candidates.extend(
                [
                    os.path.join(root, "Git", "cmd"),
                    os.path.join(root, "Git", "bin"),
                    os.path.join(root, "Git", "mingw64", "bin"),
                ]
            )

    # Keep normal Unix/Homebrew locations as harmless fallbacks for hosted Spaces.
    candidates.extend(["/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"])
    return candidates


def _ensure_git_available_for_evaluation_lab() -> str:
    """Ensure `git` is discoverable before the read-only repo scanner runs."""

    git_path = shutil.which("git")
    if git_path:
        return git_path

    for directory in _candidate_git_directories():
        _prepend_to_path(directory)
        git_path = shutil.which("git")
        if git_path:
            return git_path

    raise RuntimeError(
        "Git executable was not found on PATH. Install Git, add it to PATH, or set "
        f"{EVAL_LAB_GIT_ENV_VAR} to the Git executable or Git directory."
    )


def _public_error_detail(stage: str) -> str:
    if stage == "scan":
        return "The Evaluation Lab failed while running the static read-only scan. Check Space logs for the server traceback."
    if stage == "report":
        return "The Evaluation Lab scanned the repository but failed while building the report artifact. Check Space logs for the server traceback."
    return "The Evaluation Lab failed while analyzing this repository. Check Space logs for the server traceback."


def _evaluation_lab_error_response(
    exc: Exception,
    *,
    stage: str,
    status_code: int = 500,
) -> JSONResponse:
    """Return stable JSON errors while always logging the real traceback."""

    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)

    debug = _env_flag(DEBUG_ERROR_ENV_VAR, default=False)
    detail = f"{type(exc).__name__}: {exc}" if debug else _public_error_detail(stage)
    content: dict[str, Any] = {
        "error": "evaluation_lab_internal_error",
        "detail": detail,
        "message": "The Evaluation Lab failed while analyzing this repository.",
        "stage": stage,
        "debug_hint": f"Set {DEBUG_ERROR_ENV_VAR}=1 locally to include exception details in this JSON response.",
    }
    if debug:
        content["exception_type"] = type(exc).__name__
        content["exception_message"] = str(exc)
    return JSONResponse(status_code=status_code, content=content)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _jsonable(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _jsonable(vars(value))
        except Exception:
            pass
    return str(value)


def _normalize_action(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        raise HTTPException(status_code=400, detail="step payload is empty")

    if "name" in payload:
        return {
            "name": str(payload.get("name") or ""),
            "args": dict(payload.get("args") or {}),
        }

    raw_action = payload.get("action")
    if isinstance(raw_action, str):
        normalized = {"name": raw_action, "args": {}}
        for key, value in payload.items():
            if key == "action":
                continue
            if key == "args" and isinstance(value, Mapping):
                normalized["args"] = dict(value)
            else:
                normalized["args"][key] = value
        return normalized

    if isinstance(raw_action, Mapping):
        return {
            "name": str(raw_action.get("name") or raw_action.get("action") or ""),
            "args": dict(raw_action.get("args") or {}),
        }

    return {"name": "", "args": {}}


def _serialize_actions(
    env: BaseDomain,
    state: Optional[dict[str, Any]],
    observation: Optional[dict[str, Any]],
) -> list[Any]:
    if isinstance(observation, dict):
        for key in ("available_actions", "actions"):
            if key in observation:
                return _jsonable(observation[key])

    if isinstance(state, dict):
        for key in ("available_actions", "actions"):
            if key in state:
                return _jsonable(state[key])

    for attr_name in ("action_space", "available_actions", "actions"):
        attr = getattr(env, attr_name, None)
        if callable(attr):
            try:
                value = attr()
                if value is not None:
                    return _jsonable(value)
            except Exception:
                pass
        elif attr is not None:
            return _jsonable(attr)

    return []


def _extract_reset_parts(result: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    observation = dict(result.get("observation") or {})
    state = dict(result.get("state") or {})
    info = dict(result.get("info") or {})
    return observation, state, info


def _extract_step_parts(
    result: Mapping[str, Any],
) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any], dict[str, Any]]:
    observation = dict(result.get("observation") or {})
    reward = float(result.get("reward") or 0.0)
    done = bool(result.get("done") or False)
    truncated = bool(result.get("truncated") or False)
    info = dict(result.get("info") or {})
    state = dict(result.get("state") or {})
    return observation, reward, done, truncated, info, state


def _align_public_state(state: dict[str, Any]) -> dict[str, Any]:
    aligned = dict(state)
    if "target_progress" in aligned and "target_score" not in aligned:
        aligned["target_score"] = aligned["target_progress"]
    return aligned


def _filter_public_state(state: dict[str, Any]) -> dict[str, Any]:
    if _env_flag(DEBUG_STATE_ENV_VAR, default=False):
        return dict(state)

    filtered = dict(state)
    for key in list(filtered.keys()):
        if key.startswith("hidden_") or key.startswith("internal_"):
            filtered.pop(key, None)

    for key in ("debug_trace", "private_notes"):
        filtered.pop(key, None)

    return filtered


def _canonicalize_env_key(text: str | None) -> str:
    return str(text or "").strip().lower()


def _resolve_registration(env_id: str | None, scenario_id: str | None) -> EnvRegistration:
    env_key = _canonicalize_env_key(env_id)
    if env_key and env_key in ENV_REGISTRY:
        return ENV_REGISTRY[env_key]

    scenario_key = _canonicalize_env_key(scenario_id)
    if scenario_key and scenario_key in SCENARIO_REGISTRY:
        resolved_env_key = SCENARIO_REGISTRY[scenario_key]
        return ENV_REGISTRY[resolved_env_key]

    if not env_key and not scenario_key:
        return ENV_REGISTRY[DEFAULT_ENV_ID.lower()]

    raise HTTPException(
        status_code=400,
        detail={
            "message": "unsupported env/scenario",
            "supported_env_ids": [item.env_id for item in ENV_REGISTRY.values()],
            "supported_scenarios": sorted({item.scenario_id for item in ENV_REGISTRY.values()}),
            "received_env_id": env_id,
            "received_scenario_id": scenario_id,
        },
    )


def _create_env(payload: dict[str, Any]) -> tuple[EnvRegistration, str | None, dict[str, Any], BaseDomain]:
    mission_id = payload.get("mission_id")
    scenario_id = str(payload.get("scenario_id") or "").strip() or None
    options = dict(payload.get("options") or {})
    env_id = str(options.get("env_id") or options.get("environment_id") or "").strip() or None

    registration = _resolve_registration(env_id=env_id, scenario_id=scenario_id)
    env = registration.env_cls()
    return registration, mission_id, options, env


def _reset_env(env: BaseDomain, payload: dict[str, Any], scenario_id: str, options: dict[str, Any]) -> Mapping[str, Any]:
    default_max_steps = getattr(env, "default_max_steps", 6)
    reset_kwargs: dict[str, Any] = {
        "seed": payload.get("seed"),
        "max_steps": _coerce_positive_int(options.get("max_steps"), default=default_max_steps),
        "scenario_id": scenario_id,
    }

    for key in ("mission", "task_category", "metadata"):
        if key in options and options.get(key) is not None:
            reset_kwargs[key] = options.get(key)

    try:
        result = env.reset(**reset_kwargs)
        if _is_mapping(result):
            return result
    except TypeError:
        pass

    try:
        result = env.reset(reset_kwargs)
        if _is_mapping(result):
            return result
    except TypeError:
        pass

    for key, value in reset_kwargs.items():
        try:
            setattr(env, key, value)
        except Exception:
            pass

    result = env.reset()
    if not _is_mapping(result):
        raise HTTPException(status_code=500, detail="env.reset() did not return a mapping")
    return result


@dataclass(slots=True)
class ActiveSession:
    env_id: str
    scenario_id: str
    domain: str
    mission_id: Optional[str]
    env: BaseDomain
    state: dict[str, Any] = field(default_factory=dict)
    last_observation: dict[str, Any] = field(default_factory=dict)
    last_info: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ServerRuntime:
    env_name: str = "omnibench_aegis_env"
    version: str = DEFAULT_VERSION
    initialized: bool = True
    active: Optional[ActiveSession] = None

    def health_payload(self) -> dict[str, Any]:
        if self.active is None:
            active_env_id = DEFAULT_ENV_ID
            active_scenario = DEFAULT_SCENARIO_ID
            active_domain = DEFAULT_DOMAIN
        else:
            active_env_id = self.active.env_id
            active_scenario = self.active.scenario_id
            active_domain = self.active.domain

        return {
            "status": "ok",
            "env": self.env_name,
            "env_name": self.env_name,
            "version": self.version,
            "initialized": self.initialized,
            "active_env_id": active_env_id,
            "active_domain": active_domain,
            "active_scenario": active_scenario,
        }


RUNTIME = ServerRuntime()

app = FastAPI(
    title="omnibench_aegis_env",
    version=DEFAULT_VERSION,
    description="OpenEnv server for omnibench_aegis_env using BaseDomain.",
)


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(TypeError)
async def type_error_handler(_request: Request, exc: TypeError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    """Human-friendly landing page for the Hugging Face Space.

    The machine-facing OpenEnv endpoints remain unchanged:
    /health, /contract, /reset, /step, /state, /actions, /docs, /openapi.json.
    """
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>omnibench_aegis_env</title>
  <style>
    :root {
      --bg: #020617;
      --panel: rgba(15, 23, 42, 0.78);
      --line: rgba(148, 163, 184, 0.22);
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #60a5fa;
      --green: #86efac;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(37, 99, 235, 0.34), transparent 34rem),
        radial-gradient(circle at top right, rgba(16, 185, 129, 0.22), transparent 30rem),
        linear-gradient(135deg, #020617 0%, #0f172a 56%, #111827 100%);
    }
    a { color: inherit; }
    .wrap {
      width: min(1160px, calc(100% - 40px));
      margin: 0 auto;
      padding: 54px 0 46px;
    }
    .hero {
      padding: 34px;
      border: 1px solid var(--line);
      border-radius: 32px;
      background: linear-gradient(180deg, rgba(15, 23, 42, 0.84), rgba(15, 23, 42, 0.62));
      box-shadow: 0 30px 90px rgba(0, 0, 0, 0.34);
    }
    .badge-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 22px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.06);
      color: #cbd5e1;
      font-size: 13px;
      font-weight: 750;
    }
    .badge.green {
      color: var(--green);
      border-color: rgba(34, 197, 94, 0.34);
      background: rgba(34, 197, 94, 0.10);
    }
    h1 {
      margin: 0;
      max-width: 920px;
      font-size: clamp(42px, 7vw, 82px);
      line-height: 0.92;
      letter-spacing: -0.07em;
    }
    .lead {
      max-width: 840px;
      margin: 24px 0 0;
      color: #cbd5e1;
      font-size: clamp(17px, 2vw, 20px);
      line-height: 1.65;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 30px;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 44px;
      padding: 12px 16px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.07);
      color: #f8fafc;
      font-weight: 800;
      text-decoration: none;
    }
    .btn.primary {
      background: #2563eb;
      border-color: #60a5fa;
    }
    .section-title {
      margin: 34px 0 14px;
      color: #93c5fd;
      font-size: 13px;
      font-weight: 900;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 15px;
    }
    .card {
      min-height: 134px;
      padding: 20px;
      border-radius: 24px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 18px 54px rgba(0, 0, 0, 0.20);
    }
    .card strong {
      display: block;
      margin-bottom: 8px;
      font-size: 18px;
      letter-spacing: -0.02em;
    }
    .card p {
      margin: 0;
      color: var(--muted);
      line-height: 1.52;
      font-size: 14px;
    }
    .pill {
      display: inline-block;
      margin-bottom: 12px;
      padding: 5px 9px;
      border-radius: 999px;
      background: rgba(96, 165, 250, 0.12);
      color: #bfdbfe;
      font-size: 12px;
      font-weight: 850;
    }
    .quick {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 15px;
      margin-top: 15px;
    }
    .code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      white-space: pre-wrap;
      color: #dbeafe;
      background: rgba(2, 6, 23, 0.62);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      font-size: 13px;
      line-height: 1.55;
      overflow-x: auto;
    }
    footer {
      margin-top: 30px;
      color: #64748b;
      font-size: 13px;
      text-align: center;
    }
    @media (max-width: 880px) {
      .grid, .quick { grid-template-columns: 1fr; }
      .hero { padding: 26px; }
      .wrap { width: min(100% - 28px, 1160px); padding-top: 28px; }
    }
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="badge-row">
        <span class="badge green">● Running</span>
        <span class="badge">OpenEnv-compatible</span>
        <span class="badge">OAS 3.1</span>
        <span class="badge">AegisForge</span>
        <span class="badge">Sprint 3 coverage</span>
      </div>

      <h1>omnibench_aegis_env</h1>
      <p class="lead">
        A compact OpenEnv evaluation server for multi-domain agent workflows:
        research, computer use, finance, multi-agent evaluation, tau2, game,
        business process, agent safety, cybersecurity, and coding security scenarios.
      </p>

      <div class="actions">
        <a class="btn primary" href="/lab">Open AegisForge Lab</a>
        <a class="btn" href="/docs">Open API Docs</a>
        <a class="btn" href="/health">Health</a>
        <a class="btn" href="/contract">Contract</a>
        <a class="btn" href="/openapi.json">OpenAPI JSON</a>
        <a class="btn" href="/state">State</a>
      </div>
    </section>

    <div class="section-title">Sprint 3 / AgentX-AgentBeats coverage</div>
    <section class="grid">
      <article class="card">
        <span class="pill">Agent Safety · Pi-Bench</span>
        <strong>WhistleBlowerWreck</strong>
        <p>PII leak scenario focused on protected disclosure, safe handling, and policy-grounded response behavior.</p>
      </article>
      <article class="card">
        <span class="pill">Cybersecurity · CyberGym</span>
        <strong>StaticShipScam</strong>
        <p>Supply-chain security scenario for identifying unsafe package or static artifact behavior.</p>
      </article>
      <article class="card">
        <span class="pill">Coding Agent · NetArena</span>
        <strong>DevContainerDoom</strong>
        <p>Devcontainer supply-chain scenario for safe coding-agent environment and dependency behavior.</p>
      </article>
    </section>

    <div class="section-title">Existing scenario suite</div>
    <section class="grid">
      <article class="card"><span class="pill">Research</span><strong>InventoryInject</strong><p>Grounded inventory inspection, analysis, quarantine, and safe fact extraction.</p></article>
      <article class="card"><span class="pill">Computer Use</span><strong>LinkLifter</strong><p>Safe link scanning, grounded navigation, and destination verification.</p></article>
      <article class="card"><span class="pill">Finance</span><strong>taxwiztrap</strong><p>Tax calculation with careful unit normalization and canonical answer submission.</p></article>
      <article class="card"><span class="pill">Multi-agent</span><strong>BidBot</strong><p>Roster building, matchup simulation, scoring, and equilibrium assessment.</p></article>
      <article class="card"><span class="pill">τ²-Bench</span><strong>TicketTwister</strong><p>Task bundle loading, user simulation, conversation execution, and bundle scoring.</p></article>
      <article class="card"><span class="pill">Game Agent</span><strong>wikiwiper</strong><p>Objective inspection, zone scanning, route navigation, threat engagement, and cleanup verification.</p></article>
      <article class="card"><span class="pill">Business Process</span><strong>saleforceone</strong><p>Privacy-safe CRM routing, schema checks, context filtering, and policy application.</p></article>
    </section>

    <div class="section-title">API quick start</div>
    <section class="quick">
      <article class="card">
        <strong>Stable OpenEnv endpoints</strong>
        <p>
          The presentation layer only changes this landing page. The machine-facing
          endpoints remain available for evaluators and scripts: /health, /contract,
          /reset, /step, /state, /actions, /docs, and /openapi.json.
        </p>
      </article>
      <article class="code">POST /reset
{
  "scenario_id": "InventoryInject",
  "options": {
    "domain": "research"
  }
}

POST /step
{
  "name": "inspect_inventory",
  "args": {}
}</article>
    </section>

    <footer>
      Built for AegisForge · OpenEnv · OmniBench-style local and hosted evaluation workflows.
    </footer>
  </main>
</body>
</html>
    """


@app.get("/health")
def health() -> dict[str, Any]:
    return RUNTIME.health_payload()


@app.get("/contract")
def contract() -> dict[str, Any]:
    supported_domains = sorted({item.domain for item in ENV_REGISTRY.values()})
    primary_scenarios = sorted({item.scenario_id for item in ENV_REGISTRY.values()})
    supported_env_ids = [item.env_id for item in ENV_REGISTRY.values()]
    return {
        "env_id": DEFAULT_ENV_ID,
        "name": "omnibench_aegis_env",
        "version": DEFAULT_VERSION,
        "description": "AegisForge OpenEnv server backed by BaseDomain.",
        "primary_scenarios": primary_scenarios,
        "supported_domains": supported_domains,
        "supported_env_ids": supported_env_ids,
    }


def _get_active_session() -> ActiveSession:
    if RUNTIME.active is None:
        raise HTTPException(status_code=409, detail="environment has not been reset yet")
    return RUNTIME.active


@app.post("/reset")
async def reset(request: Request) -> dict[str, Any]:
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="reset payload must be a JSON object")

    registration, mission_id, options, env = _create_env(payload)
    raw_result = _reset_env(env=env, payload=payload, scenario_id=registration.scenario_id, options=options)

    observation, state, info = _extract_reset_parts(raw_result)
    state = _align_public_state(state)

    session = ActiveSession(
        env_id=registration.env_id,
        scenario_id=registration.scenario_id,
        domain=registration.domain,
        mission_id=mission_id,
        env=env,
        state=state,
        last_observation=observation,
        last_info=info,
    )
    RUNTIME.active = session

    return {
        "env_id": registration.env_id,
        "scenario_id": registration.scenario_id,
        "mission_id": mission_id,
        "observation": _jsonable(observation),
        "state": _jsonable(_filter_public_state(state)),
        "info": _jsonable(info),
        "actions": _serialize_actions(env, state, observation),
    }


@app.post("/step")
async def step(request: Request) -> dict[str, Any]:
    session = _get_active_session()
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="step payload must be a JSON object")

    action = _normalize_action(payload)
    raw_result = session.env.step(session.state, action)
    if not _is_mapping(raw_result):
        raise HTTPException(status_code=500, detail="env.step() did not return a mapping")

    observation, reward, done, truncated, info, state = _extract_step_parts(raw_result)
    state = _align_public_state(state)

    session.state = state
    session.last_observation = observation
    session.last_info = info

    return {
        "env_id": session.env_id,
        "scenario_id": session.scenario_id,
        "mission_id": session.mission_id,
        "observation": _jsonable(observation),
        "reward": reward,
        "done": done,
        "truncated": truncated,
        "info": _jsonable(info),
        "state": _jsonable(_filter_public_state(state)),
        "actions": _serialize_actions(session.env, state, observation),
    }


@app.get("/state")
def state() -> dict[str, Any]:
    session = _get_active_session()
    return {
        "env_id": session.env_id,
        "scenario_id": session.scenario_id,
        "mission_id": session.mission_id,
        "state": _jsonable(_filter_public_state(session.state)),
        "last_observation": _jsonable(session.last_observation),
        "last_info": _jsonable(session.last_info),
    }


@app.get("/actions")
def actions() -> dict[str, Any]:
    session = _get_active_session()
    return {
        "env_id": session.env_id,
        "scenario_id": session.scenario_id,
        "actions": _serialize_actions(session.env, session.state, session.last_observation),
    }



PURPLE_SUPPORTED_TRACKS: dict[str, dict[str, Any]] = {
    "mcu": {
        "label": "MCU / MCU-Minecraft",
        "category": "general_agent",
        "priority": "medium",
        "focus": "Minecraft-style general agent benchmark routing and robust task execution.",
    },
    "officeqa": {
        "label": "OfficeQA",
        "category": "office_agent",
        "priority": "medium",
        "focus": "Document-grounded question answering and office workflow consistency.",
    },
    "crmarena": {
        "label": "CRMArena",
        "category": "business_process",
        "priority": "medium",
        "focus": "CRM workflow reasoning, policy compliance, and safe tool-use behavior.",
    },
    "fieldworkarena": {
        "label": "FieldWorkArena",
        "category": "field_agent",
        "priority": "medium",
        "focus": "Field-task planning, environmental grounding, and robust step selection.",
    },
    "maizebargain": {
        "label": "MaizeBargain",
        "category": "negotiation_agent",
        "priority": "medium",
        "focus": "Negotiation strategy, bounded persuasion, and rule-following behavior.",
    },
    "tau2": {
        "label": "τ²-Bench",
        "category": "tool_user_agent",
        "priority": "high",
        "focus": "Multi-turn task completion, tool use, policy adherence, and stop-condition discipline.",
    },
    "osworld": {
        "label": "OSWorld",
        "category": "computer_use",
        "priority": "high",
        "focus": "Computer-use planning, observation grounding, and safe action selection.",
    },
    "pibench": {
        "label": "Pi-Bench",
        "category": "agent_safety",
        "priority": "high",
        "focus": "Agent safety, privacy protection, refusal boundaries, and policy-grounded reasoning.",
    },
    "cybergym": {
        "label": "CyberGym",
        "category": "cybersecurity_agent",
        "priority": "high",
        "focus": "Controlled cybersecurity benchmark behavior, A2A validation, and defensive reporting.",
    },
    "netarena": {
        "label": "NetArena",
        "category": "coding_agent",
        "priority": "high",
        "focus": "Network/coding-agent benchmark behavior, reproducible diagnosis, and safe tool use.",
    },
}

PURPLE_TRACK_ALIASES: dict[str, str] = {
    "minecraft": "mcu",
    "mcu-minecraft": "mcu",
    "mcuminecraft": "mcu",
    "pi-bench": "pibench",
    "pi_bench": "pibench",
    "cyber-gym": "cybergym",
    "cyber_gym": "cybergym",
    "net-arena": "netarena",
    "net_arena": "netarena",
    "tau": "tau2",
    "tau²": "tau2",
    "tau2-bench": "tau2",
    "τ²-bench": "tau2",
    "crm-arena": "crmarena",
    "crm_arena": "crmarena",
    "fieldwork-arena": "fieldworkarena",
    "fieldwork_arena": "fieldworkarena",
    "maize-bargain": "maizebargain",
    "maize_bargain": "maizebargain",
    "office-qa": "officeqa",
    "office_qa": "officeqa",
}

PURPLE_BENCHMARK_ROLES = {"auto", "attacker", "defender"}


def _safe_ui_text(value: Any, *, default: str = "", max_len: int = 500) -> str:
    text = str(value if value is not None else default).strip()
    if not text:
        text = default
    return text[:max_len]


def _canonicalize_purple_track(value: Any) -> str:
    raw = str(value or "cybergym").strip().lower()
    normalized = raw.replace(" ", "").replace("/", "-")
    return PURPLE_TRACK_ALIASES.get(normalized, normalized)


def _build_purple_benchmark_preview(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build a safe Phase 2 Purple preview artifact.

    This endpoint intentionally does not execute target code, contact third-party
    systems, generate real exploit payloads, or run benchmark submissions. It
    prepares the metadata contract that a safe A2A purple competitor should
    expose before an official AgentBeats run.
    """

    track = _canonicalize_purple_track(payload.get("track"))
    if track not in PURPLE_SUPPORTED_TRACKS:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "unsupported purple track",
                "received_track": payload.get("track"),
                "supported_tracks": sorted(PURPLE_SUPPORTED_TRACKS),
            },
        )

    role = str(payload.get("role") or "auto").strip().lower()
    if role not in PURPLE_BENCHMARK_ROLES:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "unsupported benchmark role",
                "received_role": payload.get("role"),
                "supported_roles": sorted(PURPLE_BENCHMARK_ROLES),
            },
        )

    profile = PURPLE_SUPPORTED_TRACKS[track]
    opponent = _safe_ui_text(
        payload.get("opponent_profile"),
        default=f"{track}-green",
        max_len=160,
    )
    opponent_url = _safe_ui_text(
        payload.get("opponent_url"),
        default="https://github.com/RDI-Foundation/cybergym-green",
        max_len=500,
    )
    purple_agent_url = _safe_ui_text(
        payload.get("purple_agent_url"),
        default="",
        max_len=500,
    )

    a2a_status = "ready" if purple_agent_url else "manual_required"
    registration_status = "manual_required"
    leaderboard_status = "preview_only"

    return {
        "lab": "AegisForge Evaluation Lab",
        "version": DEFAULT_VERSION,
        "mode": "purple_benchmark_preview",
        "competition": "AgentX-AgentBeats Phase 2",
        "benchmark_scope": "controlled_only",
        "track": track,
        "track_label": profile["label"],
        "track_category": profile["category"],
        "priority": profile["priority"],
        "role": role,
        "opponent_profile": opponent,
        "green_agent_reference": opponent_url,
        "purple_agent_reference": purple_agent_url or None,
        "a2a_compatible_focus": True,
        "leaderboard_ready": False,
        "leaderboard_status": leaderboard_status,
        "registration_status": registration_status,
        "safety_policy": {
            "no_real_exploitation": True,
            "no_third_party_targeting": True,
            "no_harmful_payloads": True,
            "no_secret_extraction": True,
            "no_persistence": True,
            "no_evasion": True,
            "benchmark_only": True,
        },
        "pre_run_self_check": [
            {
                "id": "scope",
                "status": "ready",
                "detail": "Run is constrained to authorized benchmark or simulated environments only.",
            },
            {
                "id": "a2a_agent_card",
                "status": a2a_status,
                "detail": "Provide and validate the purple agent A2A endpoint / Agent Card before official runs.",
            },
            {
                "id": "agentbeats_registration",
                "status": registration_status,
                "detail": "Register the purple agent on agentbeats.dev before leaderboard assessment.",
            },
            {
                "id": "opponent_profile",
                "status": "ready",
                "detail": f"Using opponent profile '{opponent}' for track '{track}'.",
            },
            {
                "id": "safety_boundaries",
                "status": "ready",
                "detail": "Real exploitation, harmful payloads, unauthorized access, evasion, persistence, and secret extraction are denied.",
            },
            {
                "id": "metadata",
                "status": "ready",
                "detail": "Track, role, opponent, scope, and reproducibility metadata are present.",
            },
        ],
        "strategy_outline": [
            "Read the green agent task contract and allowed A2A message format.",
            "Select track-specific reasoning and tool-use policy from the opponent profile.",
            "Use benchmark-safe attacker/defender role behavior only inside the official task scope.",
            "Prefer evidence-grounded actions, bounded retries, clear stop conditions, and policy-aware outputs.",
            "Emit reproducible run metadata, self-check status, and a concise final artifact for leaderboard review.",
        ],
        "track_focus": profile["focus"],
        "run_metadata_contract": {
            "track": track,
            "role": role,
            "opponent_profile": opponent,
            "cost_tracking": True,
            "latency_tracking": True,
            "retry_tracking": True,
            "seed_tracking": True,
            "artifact_export": True,
            "hardcoding_policy": "deny_lookup_tables_and_benchmark_answer_hardcoding",
        },
        "post_run_self_check_template": [
            "Confirm the run stayed inside benchmark scope.",
            "Record score, cost, latency, retries, and failure mode.",
            "Attach A2A transcript or official run artifact when available.",
            "Summarize strengths, weaknesses, and next routing/profile updates.",
        ],
        "recommended_next_steps": [
            "Expose the actual A2A purple agent endpoint and validate its Agent Card.",
            "Add CI tests for /api/evaluation-lab/purple-preview.",
            "Connect this preview metadata to src/aegisforge/agent.py routing once the UI contract is stable.",
            "Register the purple agent on agentbeats.dev for official leaderboard runs.",
        ],
    }


@app.get("/lab", response_class=HTMLResponse)
def evaluation_lab() -> str:
    """Interactive Green Defensive + Purple Benchmark evaluation lab."""
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AegisForge Evaluation Lab</title>
  <style>
    :root {
      --bg:#020617;
      --panel:rgba(15,23,42,.84);
      --panel2:rgba(30,41,59,.58);
      --line:rgba(148,163,184,.24);
      --text:#e5e7eb;
      --muted:#94a3b8;
      --accent:#60a5fa;
      --green:#86efac;
      --purple:#d8b4fe;
      --red:#fca5a5;
      --yellow:#fde68a;
    }
    *{box-sizing:border-box}
    body{
      margin:0;min-height:100vh;
      font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      color:var(--text);
      background:
        radial-gradient(circle at top left,rgba(37,99,235,.32),transparent 34rem),
        radial-gradient(circle at top right,rgba(168,85,247,.26),transparent 30rem),
        radial-gradient(circle at 70% 30%,rgba(16,185,129,.16),transparent 26rem),
        linear-gradient(135deg,#020617 0%,#0f172a 56%,#111827 100%)
    }
    a{color:#bfdbfe;text-decoration:none}
    .wrap{width:min(1220px,calc(100% - 40px));margin:0 auto;padding:36px 0 42px}
    .topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:18px}
    .brand{font-weight:950;letter-spacing:-.03em}
    .nav{display:flex;gap:10px;flex-wrap:wrap}
    .nav a,button{
      border:1px solid var(--line);border-radius:14px;padding:10px 13px;color:#f8fafc;
      background:rgba(255,255,255,.07);font-weight:850;cursor:pointer
    }
    button.primary{background:#2563eb;border-color:#60a5fa}
    button.purple{background:#7c3aed;border-color:#c084fc}
    button:disabled{opacity:.58;cursor:not-allowed}
    .hero,.panel{
      border:1px solid var(--line);border-radius:28px;
      background:linear-gradient(180deg,rgba(15,23,42,.86),rgba(15,23,42,.64));
      box-shadow:0 24px 80px rgba(0,0,0,.32)
    }
    .hero{padding:30px}
    h1{margin:0;font-size:clamp(38px,6vw,70px);line-height:.96;letter-spacing:-.065em}
    h2{margin:0 0 10px;letter-spacing:-.035em}
    .lead{max-width:960px;color:#cbd5e1;font-size:18px;line-height:1.65}
    .badge-row{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:18px}
    .badge{padding:7px 11px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.06);color:#cbd5e1;font-size:12px;font-weight:900}
    .badge.green{color:var(--green);border-color:rgba(34,197,94,.34);background:rgba(34,197,94,.10)}
    .badge.purple{color:var(--purple);border-color:rgba(168,85,247,.42);background:rgba(168,85,247,.13)}
    .mode-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
    .grid{display:grid;grid-template-columns:.95fr 1.05fr;gap:16px;margin-top:16px}
    .panel{padding:22px}
    .panel.green-mode{border-color:rgba(34,197,94,.28)}
    .panel.purple-mode{border-color:rgba(168,85,247,.34)}
    label{display:block;color:#bfdbfe;font-size:13px;font-weight:950;margin:14px 0 8px}
    input,select{
      width:100%;border:1px solid var(--line);border-radius:16px;padding:14px;
      background:rgba(2,6,23,.58);color:var(--text);outline:none;font-size:15px
    }
    option{background:#0f172a;color:#e5e7eb}
    .muted{color:var(--muted);line-height:1.55}
    .checks{display:grid;gap:9px;margin:14px 0;color:#dbeafe}
    .checks div{line-height:1.4}
    .metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin-bottom:14px}
    .metric{border:1px solid var(--line);border-radius:18px;padding:14px;background:var(--panel2)}
    .metric span{color:var(--muted);display:block;font-size:12px;font-weight:900;text-transform:uppercase;letter-spacing:.08em}
    .metric strong{display:block;margin-top:6px;font-size:24px}
    .section-title{margin:20px 0 10px;color:#93c5fd;font-size:12px;font-weight:950;letter-spacing:.14em;text-transform:uppercase}
    .item{border:1px solid var(--line);border-radius:16px;padding:13px;margin:8px 0;background:rgba(2,6,23,.36)}
    .item strong{display:block;margin-bottom:6px}
    .item small{color:var(--muted)}
    pre{white-space:pre-wrap;word-break:break-word;max-height:460px;overflow:auto;background:rgba(2,6,23,.72);border:1px solid var(--line);border-radius:18px;padding:14px;color:#dbeafe;font-size:12px;line-height:1.52}
    .status{margin-top:12px;color:#cbd5e1}
    .error{color:var(--red)}.warning{color:var(--yellow)}.ok{color:var(--green)}.purple-text{color:var(--purple)}
    .hint{font-size:12px;color:#94a3b8;margin-top:8px;line-height:1.45}
    @media(max-width:960px){.grid,.mode-grid,.metric-grid{grid-template-columns:1fr}.wrap{width:min(100% - 28px,1220px)}}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="topbar">
      <div class="brand">AegisForge Evaluation Lab <span class="muted">v0.2.0</span></div>
      <nav class="nav"><a href="/">Landing</a><a href="/docs">API Docs</a><a href="/health">Health</a><a href="/contract">Contract</a></nav>
    </div>

    <section class="hero">
      <div class="badge-row">
        <span class="badge green">● Green Defensive Mode</span>
        <span class="badge purple">● Purple Benchmark Mode</span>
        <span class="badge">AgentX-AgentBeats Phase 2</span>
        <span class="badge">A2A-compatible focus</span>
        <span class="badge">Benchmark-only</span>
        <span class="badge">Precision scanner v0.1.2</span>
      </div>
      <h1>Evaluate and compete safely.</h1>
      <p class="lead">
        Use Green Defensive Mode for read-only public benchmark repository analysis, or use Purple Benchmark Mode
        to prepare safe, A2A-compatible AgentX-AgentBeats Phase 2 run metadata. Purple mode is benchmark-scoped:
        it does not target third-party systems, execute target code, extract secrets, or generate harmful real-world payloads.
      </p>
    </section>

    <section class="mode-grid">
      <article class="panel green-mode">
        <h2>Green Defensive Mode</h2>
        <p class="muted">Read-only public repo scan, defensive risk findings, controlled scenarios, and benign evaluation payload artifacts.</p>
        <label for="repoUrl">Repository URL</label>
        <input id="repoUrl" value="https://github.com/RDI-Foundation/cybergym-green" />
        <div class="checks">
          <div>✓ Static/read-only analysis</div>
          <div>✓ GitHub public repos only</div>
          <div>✓ Secret-like values masked</div>
          <div>✓ Benign payload artifacts only</div>
          <div>✓ Target code execution disabled</div>
          <div>✓ Docker execution disabled for analyzed repos</div>
        </div>
        <button class="primary" onclick="runLab()">Run Defensive Evaluation</button>
        <button onclick="copyArtifact()">Copy JSON</button>
        <div id="status" class="status">Green mode ready.</div>
      </article>

      <article class="panel purple-mode">
        <h2>Purple Benchmark Mode</h2>
        <p class="muted">
          Prepare a safe Purple Agent run preview for AgentX-AgentBeats: track routing, role metadata,
          opponent profile, A2A self-checks, leaderboard artifact fields, and reproducibility contract.
        </p>
        <label for="purpleTrack">Track</label>
        <select id="purpleTrack">
          <option value="cybergym" selected>CyberGym · Cybersecurity Agent</option>
          <option value="pibench">Pi-Bench · Agent Safety</option>
          <option value="netarena">NetArena · Coding Agent</option>
          <option value="tau2">τ²-Bench · Tool-use Agent</option>
          <option value="osworld">OSWorld · Computer Use</option>
          <option value="mcu">MCU / MCU-Minecraft · General Agent</option>
          <option value="officeqa">OfficeQA · Office Agent</option>
          <option value="crmarena">CRMArena · Business Process</option>
          <option value="fieldworkarena">FieldWorkArena · Field Agent</option>
          <option value="maizebargain">MaizeBargain · Negotiation Agent</option>
        </select>
        <label for="purpleRole">Benchmark role</label>
        <select id="purpleRole">
          <option value="auto" selected>auto · choose safely from benchmark context</option>
          <option value="attacker">attacker · benchmark-only role</option>
          <option value="defender">defender · benchmark-only role</option>
        </select>
        <label for="opponentProfile">Opponent profile</label>
        <input id="opponentProfile" value="cybergym-green" />
        <label for="opponentUrl">Green agent / benchmark reference</label>
        <input id="opponentUrl" value="https://github.com/RDI-Foundation/cybergym-green" />
        <label for="purpleAgentUrl">Purple A2A endpoint / Agent Card URL</label>
        <input id="purpleAgentUrl" placeholder="Optional until registered on agentbeats.dev" />
        <div class="checks">
          <div>✓ Benchmark-only attacker/defender roles</div>
          <div>✓ Opponent-aware routing metadata</div>
          <div>✓ Pre/post self-check artifact</div>
          <div>✓ No real exploitation or third-party targeting</div>
        </div>
        <button class="purple" onclick="runPurplePreview()">Generate Purple Run Preview</button>
        <button onclick="copyArtifact()">Copy JSON</button>
        <div id="purpleStatus" class="status">Purple mode ready.</div>
      </article>
    </section>

    <section class="grid">
      <article class="panel">
        <h2>Green report</h2>
        <div class="metric-grid">
          <div class="metric"><span>Risk score</span><strong id="riskScore">—</strong></div>
          <div class="metric"><span>Risk tier</span><strong id="riskTier">—</strong></div>
          <div class="metric"><span>Files analyzed</span><strong id="filesAnalyzed">—</strong></div>
          <div class="metric"><span>Findings</span><strong id="findingCount">—</strong></div>
          <div class="metric"><span>Review load</span><strong id="reviewLoad">—</strong></div>
        </div>
        <div class="section-title">Scan limits</div><div id="limits" class="muted">No report yet.</div>
        <div class="section-title">Repo shape</div><div id="repoShape" class="muted">No report yet.</div>
        <div class="section-title">Precision notes</div><div id="precisionNotes" class="muted">No report yet.</div>
        <div class="section-title">Findings</div><div id="findings" class="muted">No report yet.</div>
        <div class="section-title">Controlled scenarios</div><div id="scenarios" class="muted">No report yet.</div>
        <div class="section-title">Benign payloads</div><div id="payloads" class="muted">No report yet.</div>
      </article>

      <article class="panel purple-mode">
        <h2>Purple run preview</h2>
        <div class="metric-grid">
          <div class="metric"><span>Mode</span><strong id="purpleMode">—</strong></div>
          <div class="metric"><span>Track</span><strong id="purpleTrackOut">—</strong></div>
          <div class="metric"><span>Role</span><strong id="purpleRoleOut">—</strong></div>
          <div class="metric"><span>A2A</span><strong id="purpleA2A">—</strong></div>
          <div class="metric"><span>Leaderboard</span><strong id="purpleLeaderboard">—</strong></div>
        </div>
        <div class="section-title">Safety policy</div><div id="purplePolicy" class="muted">No preview yet.</div>
        <div class="section-title">Pre-run self-check</div><div id="purpleChecks" class="muted">No preview yet.</div>
        <div class="section-title">Strategy outline</div><div id="purpleStrategy" class="muted">No preview yet.</div>
        <div class="section-title">Run metadata contract</div><div id="purpleMetadata" class="muted">No preview yet.</div>
        <div class="section-title">Recommended next steps</div><div id="purpleNextSteps" class="muted">No preview yet.</div>
      </article>
    </section>

    <section class="panel" style="margin-top:16px">
      <h2>JSON artifact</h2>
      <p class="hint">This panel shows the latest Green report or Purple preview artifact. Copy it into README/SUBMISSION notes or attach it to local validation logs.</p>
      <pre id="jsonOut">{}</pre>
    </section>
  </main>
<script>
let lastArtifact=null;
function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;')}
async function parseJsonResponse(res){const raw=await res.text();let data={};try{data=raw?JSON.parse(raw):{}}catch(parseErr){throw new Error(raw?raw.slice(0,500):`HTTP ${res.status}`)}if(!res.ok){let detail=data.detail||data.message||data.error||`HTTP ${res.status}`;if(typeof detail==='object')detail=detail.message||JSON.stringify(detail);throw new Error(detail||'Request failed')}return data}
async function runLab(){const status=document.getElementById('status');const repoUrl=document.getElementById('repoUrl').value.trim();status.className='status';status.textContent='Running read-only defensive scan...';try{const res=await fetch('/api/evaluation-lab/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({repo_url:repoUrl})});const data=await parseJsonResponse(res);lastArtifact=data;renderReport(data);document.getElementById('jsonOut').textContent=JSON.stringify(data,null,2);if(data.scan_limits&&data.scan_limits.file_limit_reached){status.className='status warning';status.textContent='Evaluation complete. Analysis was truncated at the safety file limit.'}else{status.className='status ok';status.textContent='Evaluation complete.'}}catch(err){status.className='status error';status.textContent=String(err.message||err)}}
async function runPurplePreview(){const status=document.getElementById('purpleStatus');status.className='status';status.textContent='Building benchmark-safe Purple preview...';const payload={track:document.getElementById('purpleTrack').value,role:document.getElementById('purpleRole').value,opponent_profile:document.getElementById('opponentProfile').value.trim(),opponent_url:document.getElementById('opponentUrl').value.trim(),purple_agent_url:document.getElementById('purpleAgentUrl').value.trim()};try{const res=await fetch('/api/evaluation-lab/purple-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await parseJsonResponse(res);lastArtifact=data;renderPurplePreview(data);document.getElementById('jsonOut').textContent=JSON.stringify(data,null,2);status.className='status ok';status.textContent='Purple preview generated. No target code was executed.'}catch(err){status.className='status error';status.textContent=String(err.message||err)}}
function riskTierFromScore(score){score=Number(score||0);if(score>=80)return'high';if(score>=50)return'elevated';if(score>=25)return'moderate';return'low'}
function renderSummary(summary){if(!summary)return'';const sev=summary.by_severity||{};const cat=summary.by_category||{};const sevText=Object.entries(sev).map(([k,v])=>`${esc(k)}:${esc(v)}`).join(' · ');const catText=Object.entries(cat).map(([k,v])=>`${esc(k)}:${esc(v)}`).join(' · ');return `<div class="item"><strong>Summary</strong><small>${sevText||'no severity summary'}</small><br><small>${catText||'no category summary'}</small></div>`}
function renderLimits(data){const limits=data.scan_limits||{};if(!Object.keys(limits).length)return'No scan-limit metadata returned.';const cls=limits.file_limit_reached?'warning':'ok';const text=limits.file_limit_reached?`Analysis truncated at safety limit (${limits.max_files} files).`:'Analysis completed within safety limits.';return `<div class="item"><strong class="${cls}">${esc(text)}</strong><small>Max files: ${esc(limits.max_files??'—')} · Max file bytes: ${esc(limits.max_file_bytes??'—')} · Large skipped: ${esc(limits.files_skipped_large??0)} · Unreadable: ${esc(limits.files_unreadable??0)}</small></div>`}
function renderRepoShape(data){const shape=data.repo_shape||{};if(!Object.keys(shape).length)return'No repo-shape metadata returned.';return `<div class="item"><small>${Object.entries(shape).map(([k,v])=>`${esc(k)}=${esc(v)}`).join(' · ')}</small></div>`}
function renderPrecisionNotes(data){const notes=data.precision_notes||[];const classifier=data.classifier_version?`<div class="item"><strong>Classifier ${esc(data.classifier_version)}</strong></div>`:'';return classifier+(notes.length?notes.map(n=>`<div class="item"><small>${esc(n)}</small></div>`).join(''):'No precision notes returned.')}
function renderReport(data){document.getElementById('riskScore').textContent=data.risk_score??'0';document.getElementById('riskTier').textContent=data.risk_tier??riskTierFromScore(data.risk_score);document.getElementById('filesAnalyzed').textContent=data.files_analyzed??'0';document.getElementById('findingCount').textContent=(data.findings||[]).length;document.getElementById('reviewLoad').textContent=(data.review_load&&data.review_load.level)||'—';document.getElementById('limits').innerHTML=renderLimits(data);document.getElementById('repoShape').innerHTML=renderRepoShape(data);document.getElementById('precisionNotes').innerHTML=renderPrecisionNotes(data);const findings=data.findings||[];document.getElementById('findings').innerHTML=findings.length?renderSummary(data.finding_summary)+findings.map(i=>`<div class="item"><strong>${esc(i.severity)} · ${esc(i.category)}</strong><small>${esc(i.file)}:${esc(i.line)} — ${esc(i.evidence)}</small><br><small>${esc(i.recommendation)}</small></div>`).join(''):'No findings detected by the static defensive scanner.';document.getElementById('scenarios').innerHTML=(data.controlled_scenarios||[]).map(i=>`<div class="item"><strong>${esc(i.title)}</strong><small>${esc(i.id)} · ${esc(i.mode)}</small><br><small>${esc(i.goal)}</small></div>`).join('')||'No controlled scenarios returned.';document.getElementById('payloads').innerHTML=(data.benign_payloads||[]).map(i=>`<div class="item"><strong>${esc(i.id)}</strong><small>${esc(i.type)} · ${esc(i.purpose)}</small></div>`).join('')||'No benign payloads returned.'}
function renderList(items){return (items||[]).map(i=>`<div class="item"><small>${esc(i)}</small></div>`).join('')||'No items returned.'}
function renderObj(obj){obj=obj||{};return Object.keys(obj).length?`<div class="item"><small>${Object.entries(obj).map(([k,v])=>`${esc(k)}=${esc(typeof v==='object'?JSON.stringify(v):v)}`).join(' · ')}</small></div>`:'No metadata returned.'}
function renderPurplePreview(data){document.getElementById('purpleMode').textContent='preview';document.getElementById('purpleTrackOut').textContent=data.track||'—';document.getElementById('purpleRoleOut').textContent=data.role||'—';document.getElementById('purpleA2A').textContent=data.a2a_compatible_focus?'focus':'—';document.getElementById('purpleLeaderboard').textContent=data.leaderboard_status||'—';document.getElementById('purplePolicy').innerHTML=renderObj(data.safety_policy);document.getElementById('purpleChecks').innerHTML=(data.pre_run_self_check||[]).map(i=>`<div class="item"><strong>${esc(i.id)} · ${esc(i.status)}</strong><small>${esc(i.detail)}</small></div>`).join('')||'No checks returned.';document.getElementById('purpleStrategy').innerHTML=renderList(data.strategy_outline);document.getElementById('purpleMetadata').innerHTML=renderObj(data.run_metadata_contract);document.getElementById('purpleNextSteps').innerHTML=renderList(data.recommended_next_steps)}
async function copyArtifact(){if(!lastArtifact)return;await navigator.clipboard.writeText(JSON.stringify(lastArtifact,null,2));document.getElementById('status').textContent='JSON artifact copied.';document.getElementById('purpleStatus').textContent='JSON artifact copied.'}
</script>
</body>
</html>
    """

@app.post("/api/evaluation-lab/analyze")
async def analyze_evaluation_lab(request: Request) -> JSONResponse:
    payload: dict[str, Any] = {}
    try:
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="evaluation lab payload must be a JSON object")

        lab_request = EvaluationLabRequest.from_mapping(payload)
        if lab_request.mode != "read_only_defensive":
            raise HTTPException(status_code=400, detail="only read_only_defensive mode is supported")

        # scanner.py shells out to `git clone` in read-only mode. Make the runtime
        # robust on Windows/Conda/VS Code terminals where Git is installed but not
        # visible on PATH.
        _ensure_git_available_for_evaluation_lab()

        try:
            scan = scan_repo(lab_request.repo_url)
        except ValueError:
            raise
        except Exception as exc:
            return _evaluation_lab_error_response(exc, stage="scan")

        try:
            report = build_report(scan)
        except Exception as exc:
            return _evaluation_lab_error_response(exc, stage="report")

        # Preserve scanner v0.1.2 metadata even if report.py is still older.
        # This keeps UI/API output consistent across local and hosted builds.
        for key in (
            "finding_summary",
            "risk_tier",
            "review_load",
            "scan_limits",
            "repo_shape",
            "classifier_version",
            "precision_notes",
        ):
            if key in scan:
                report[key] = scan[key]

        if not lab_request.include_findings:
            report["findings"] = []
        if not lab_request.include_controlled_scenarios:
            report["controlled_scenarios"] = []
        if not lab_request.include_benign_payloads:
            report["benign_payloads"] = []

        report.setdefault("runtime", {})
        if isinstance(report["runtime"], dict):
            report["runtime"].update(
                {
                    "git_available": True,
                    "git_path": shutil.which("git"),
                    "server_version": DEFAULT_VERSION,
                }
            )

        return JSONResponse(status_code=200, content=_jsonable(report))
    except HTTPException:
        raise
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc), "stage": "request"})
    except Exception as exc:
        return _evaluation_lab_error_response(exc, stage="request")


@app.post("/api/evaluation-lab/purple-preview")
async def purple_benchmark_preview(request: Request) -> JSONResponse:
    payload: dict[str, Any] = {}
    try:
        payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="purple preview payload must be a JSON object")
        preview = _build_purple_benchmark_preview(payload)
        return JSONResponse(status_code=200, content=_jsonable(preview))
    except HTTPException:
        raise
    except Exception as exc:
        return _evaluation_lab_error_response(exc, stage="purple_preview")



@app.get("/api/evaluation-lab/policy")
def evaluation_lab_policy() -> dict[str, Any]:
    git_path = shutil.which("git")
    if not git_path:
        for directory in _candidate_git_directories():
            _prepend_to_path(directory)
            git_path = shutil.which("git")
            if git_path:
                break
    return {
        "mode": "green_defensive_and_purple_benchmark_preview",
        "supported_modes": ["read_only_defensive", "purple_benchmark_preview"],
        "runtime": {
            "git_available": bool(git_path),
            "git_path": git_path,
            "git_env_var": EVAL_LAB_GIT_ENV_VAR,
        },
        "allows": [
            "public_github_repo_static_scan",
            "defensive_risk_reporting",
            "controlled_scenario_generation",
            "benign_payload_generation",
            "purple_benchmark_metadata_preview",
            "a2a_self_check_preview",
            "opponent_profile_routing_preview",
            "leaderboard_artifact_contract_preview",
        ],
        "denies": [
            "target_code_execution",
            "exploit_generation",
            "secret_extraction",
            "evasion",
            "persistence",
            "unauthorized_access",
            "running_target_docker_containers",
            "real_world_payload_generation",
            "third_party_targeting",
        ],
    }


def build_app() -> FastAPI:
    return app


def main() -> None:
    import uvicorn

    host = os.getenv("OPENENV_HOST", "127.0.0.1")
    port = _to_int(os.getenv("OPENENV_PORT", "8000"), 8000)
    uvicorn.run(
        "integrations.openenv.envs.omnibench_aegis_env.server.app:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
