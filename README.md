# AegisForge Agent — Unified Purple Agent for AgentX-AgentBeats Phase 2

**AegisForge Agent** is an A2A-compatible **Purple Agent** for AgentX-AgentBeats Phase 2. It is designed as one unified runtime, not as separate benchmark-specific bots. The agent combines a clean A2A server, selected-opponent routing, benchmark-aware track profiles, safety/self-check logic, and reproducible local validation.

The current runtime is aligned around two complementary capabilities:

1. **AegisForge Unified Purple Agent v1.0** — the submission-facing A2A Purple Agent runtime under `src/aegisforge/`.
2. **AegisForge Evaluation Lab v0.1.2** — a defensive read-only benchmark-repo analyzer under `integrations/openenv/envs/omnibench_aegis_env/evaluation_lab/`.

The submitted product is the **Purple Agent runtime**. The Evaluation Lab is supporting evidence and tooling, not a replacement for the A2A agent.

---

## What this repo submits

The submission-facing product is:

```text
Dockerfile -> run.sh -> src/aegisforge/a2a_server.py -> Executor -> AegisForgeAgent
```

Core files:

```text
src/aegisforge/a2a_server.py
src/aegisforge/agent.py
src/aegisforge/executor.py
src/aegisforge/agent_card.py
src/aegisforge/runner.py
src/aegisforge/role_policy.py
src/aegisforge/strategy/
src/aegisforge/adapters/
```

The runtime exposes:

```text
GET /health
GET /.well-known/agent-card.json
GET /.well-known/agent.json
A2A JSON-RPC endpoints mounted by the A2A SDK
```

---

## Selected opponent matrix

AegisForge is configured as a unified Purple Agent for one selected opponent per category.

| Category | Selected opponent | Canonical track | Reference repo |
|---|---:|---|---|
| Game Agent | Minecraft Benchmark / MCU-AgentBeats | `mcu` | `KWSMooBang/MCU-AgentBeats` |
| Finance Agent | OfficeQA | `officeqa` | `arnavsinghvi11/officeqa_agentbeats` |
| Business Process Agent | Entropic CRMArenaPro | `crmarena` | `rkstu/entropic-crmarenapro` |
| Research Agent | FieldWorkArena | `fieldworkarena` | `ast-fri/FieldWorkArena-GreenAgent` |
| Multi-agent Evaluation | MAizeBargAIn | `maizebargain` | `gsmithline/tutorial-agent-beats-comp` |
| τ²-Bench | τ²-Bench | `tau2` | `RDI-Foundation/tau2-agentbeats` |
| Computer Use & Web Agent | OSWorld-Verified | `osworld` | `RDI-Foundation/osworld-green` |
| Agent Safety | Pi-Bench | `pibench` | Pi-Bench green agent |
| Cybersecurity Agent | CyberGym | `cybergym` | CyberGym green agent |
| Coding Agent | NetArena | `netarena` | NetArena green agent |

Important alias rule:

```text
mcu, mcu-minecraft, and mcu_minecraft are the same selected Game Agent opponent.
The canonical runtime track is always: mcu
```

---

## Repository structure

```text
AegisForge_agent/
  src/aegisforge/                         # Submission-facing Purple Agent runtime
    a2a_server.py                         # A2A server entrypoint
    agent.py                              # Unified Purple Agent core
    agent_card.py                         # Internal card payload helpers
    executor.py                           # A2A Executor -> AegisForgeAgent bridge
    runner.py                             # serve/card/doctor runtime helper
    role_policy.py                        # Track/mode posture selection
    strategy/
      router.py
      task_classifier.py
      planner.py
      self_check.py
      track_profiles/
        crmarenapro.toml
        officeqa.toml
        mcu_minecraft.toml
        fieldworkarena.toml
        maizebargain.toml
        tau2_agentbeats.toml
        osworld.toml
        pibench.toml
        cybergym.toml
        netarena.toml
    adapters/
      openenv/
      security/
      tau2/

  integrations/
    openenv/envs/omnibench_aegis_env/
      evaluation_lab/                     # Defensive read-only repo analyzer
      server/                             # Space / local server app
      tests/
    security_arena/
    tau2/

  tests/                                  # Main repo tests
  scripts/                                # Local validation and packaging scripts
  tooling/                                # Optional submission/evaluation tooling
  harness/                                # Optional local harness and scenarios
  assets/                                 # Example card/health/schema assets
  docs/                                   # Submission, architecture, and abstract docs

  Dockerfile
  run.sh
  pyproject.toml
  requirements.txt
  requirements-dev.txt
  README.md
  SUBMISSION.md
```

`src/aegisforge/` is the product. `integrations/`, `harness/`, and `tooling/` are support/evidence layers.

---

## Requirements

Recommended:

```text
Python 3.11+
PowerShell on Windows or Bash on Linux/macOS
uv for dependency management
Docker for deployment parity
```

---

## Setup

### Option A — uv

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
uv sync --extra test
```

### Option B — pip

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

---

## Run the A2A Purple Agent

Recommended runtime entrypoint:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m aegisforge.runner --mode serve --host 127.0.0.1 --port 8001
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

Agent card:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/.well-known/agent-card.json | ConvertTo-Json -Depth 20
```

Compatibility alias endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/.well-known/agent.json | ConvertTo-Json -Depth 20
```

---

## Runtime doctor and card checks

Doctor mode validates the runtime wiring without starting a long-running server:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m aegisforge.runner --mode doctor --host 127.0.0.1 --port 8001 --pretty
```

Expected top-level result:

```json
{
  "status": "ok"
}
```

Print the card directly:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m aegisforge.runner --mode card --host 127.0.0.1 --port 8001 --pretty
```

The card should advertise `AegisForge Unified Purple Agent` and the selected opponent tracks.

---

## AegisForge Evaluation Lab

The Evaluation Lab is a support tool for defensive read-only review of public benchmark repositories. It accepts public GitHub repo URLs, performs static/sandbox-safe analysis, masks secret-like values, reports risk/review-load, and generates controlled scenarios plus benign evaluation payloads.

Run locally:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m uvicorn integrations.openenv.envs.omnibench_aegis_env.server.app:app --host 127.0.0.1 --port 8001
```

Open:

```text
http://127.0.0.1:8001/lab
```

Safety constraints:

```text
read-only static analysis
public GitHub repositories only
secret-like values masked
benign payload artifacts only
no target code execution
no Docker execution from analyzed repositories
```

---

## Tests

Run the main test suite:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m pytest tests -q
```

Useful focused checks:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m pytest tests\test_core tests\test_strategy tests\test_plugins tests\test_smoke -q
python -m pytest tests\test_adapters -q
python -m pytest tests\scripts tests\test_ci -q
python -m pytest tests\test_evaluation_lab_precision.py -q
```

Optional integration env checks:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m pytest integrations\openenv\envs\omnibench_aegis_env\tests -q
```

Compile the core agent:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m py_compile .\src\aegisforge\agent.py
```

---

## Docker

Build:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
docker build --platform linux/amd64 -t aegisforge-agent:latest .
```

Run locally on port 8001:

```powershell
docker run --rm -p 8001:8001 aegisforge-agent:latest
```

Verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
Invoke-RestMethod http://127.0.0.1:8001/.well-known/agent-card.json | ConvertTo-Json -Depth 20
```

---

## Fair play and integrity

AegisForge is intended to operate within the AgentX-AgentBeats rules and A2A benchmark contract:

```text
no hardcoded benchmark answer tables
no task-specific lookup tables pretending to be reasoning
no platform or benchmark exploit attempts
no secret extraction from real systems
no unauthorized access
no hidden evaluation leakage
no committed API keys or private secrets
```

The agent can operate in attacker/defender assessment modes **inside the benchmark contract**, but it is not designed to attack real systems.

---

## Submission status checklist

Before final platform registration:

```text
[ ] GitHub repository is public and up to date
[ ] README.md is updated
[ ] SUBMISSION.md is updated
[ ] Agent card endpoint is reachable
[ ] /health endpoint is reachable
[ ] Docker image builds
[ ] Runtime doctor returns status=ok
[ ] Main tests pass
[ ] Public endpoint verification passes
[ ] Abstract/description is ready for registration form
[ ] Agent is registered on AgentBeats
[ ] Leaderboard runs are launched or documented
```

---

## Team / contact

```text
Project: AegisForge Agent / QuipuLoop
Handle: ivanjojo369
Repository: github.com/ivanjojo369/AegisForge_agent
```
