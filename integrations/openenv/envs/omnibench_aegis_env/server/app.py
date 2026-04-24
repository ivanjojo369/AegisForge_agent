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
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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

DEFAULT_VERSION = "0.1.0"
DEFAULT_ENV_ID = RESEARCH_ENV_ID
DEFAULT_SCENARIO_ID = RESEARCH_SCENARIO_ID
DEFAULT_DOMAIN = "research"
DEBUG_STATE_ENV_VAR = "OPENENV_DEBUG_STATE"


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
        <a class="btn primary" href="/docs">Open API Docs</a>
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
