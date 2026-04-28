AegisForge is a unified A2A-compatible Purple Agent for AgentX-AgentBeats Phase 2. It integrates a single runtime for the selected opponent matrix across Game, Finance, Business Process, Research, Multi-agent, τ²-Bench, Computer Use/Web, Agent Safety, Cybersecurity, and Coding tracks. The agent normalizes MCU/Minecraft into the canonical mcu profile and supports OfficeQA, CRMArenaPro, FieldWorkArena, MAizeBargAIn, tau2, OSWorld, Pi-Bench, CyberGym, and NetArena through shared classification, routing, policy, planning, self-check, and artifact controls. The design emphasizes generalization over hardcoded benchmark answers, preserves attacker/defender mode, exposes standard A2A health and Agent Card endpoints, and includes a defensive read-only Evaluation Lab for public benchmark repository analysis.

# SUBMISSION.md — AegisForge Agent / QuipuLoop

AgentX-AgentBeats Phase 2 · Purple Agent

---

## 1. Submission summary

**Project name:** AegisForge Agent  
**Team / handle:** QuipuLoop / ivanjojo369  
**Submission type:** A2A-compatible Purple Agent  
**Repository:** `https://github.com/ivanjojo369/AegisForge_agent`  
**Primary runtime path:** `Dockerfile -> run.sh -> src/aegisforge/a2a_server.py -> Executor -> AegisForgeAgent`  
**Runtime identity:** AegisForge Unified Purple Agent v1.0  

AegisForge is a unified Phase 2 Purple Agent for AgentX-AgentBeats. It is designed to compete through one A2A-compatible runtime while supporting a selected-opponent matrix across Game, Finance, Business Process, Research, Multi-agent, τ²-Bench, Computer Use/Web, Agent Safety, Cybersecurity, and Coding categories.

The repository does not present separate benchmark-specific bots. Instead, it exposes one Purple Agent runtime with selected-opponent profiles, track-aware routing, bounded memory, self-check logic, role/artifact policies, and reproducible validation.

---

## 2. Submitted product

The submitted product is the A2A Purple Agent runtime implemented under:

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

Supporting evidence and tooling live under:

```text
integrations/
harness/
tooling/
templates/
tests/
scripts/
docs/
assets/
```

These supporting layers help with validation, local scenarios, evaluation, and documentation, but the actual submission target is the single A2A-compatible Purple runtime.

---

## 3. Selected opponent matrix

AegisForge targets one selected opponent per category.

| Category | Selected opponent | Canonical track | Notes |
|---|---|---|---|
| Game Agent | Minecraft Benchmark / MCU-AgentBeats | `mcu` | `mcu`, `mcu-minecraft`, and `mcu_minecraft` normalize to `mcu` |
| Finance Agent | OfficeQA | `officeqa` | document QA / finance reasoning |
| Business Process Agent | Entropic CRMArenaPro | `crmarena` | CRM/business-process reasoning |
| Research Agent | FieldWorkArena | `fieldworkarena` | field/research task grounding |
| Multi-agent Evaluation | MAizeBargAIn | `maizebargain` | bargaining / multi-agent strategy |
| τ²-Bench | τ²-Bench | `tau2` | trajectory/tool-action discipline |
| Computer Use & Web Agent | OSWorld-Verified | `osworld` | computer-use / web / desktop state |
| Agent Safety | Pi-Bench | `pibench` | policy compliance and agent safety |
| Cybersecurity Agent | CyberGym | `cybergym` | sandbox cybersecurity benchmark tasks |
| Coding Agent | NetArena | `netarena` | network/coding repair tasks |

Important alias rule:

```text
mcu and mcu-minecraft are the same selected Game Agent opponent.
Canonical track: mcu
```

---

## 4. Public endpoint to register

**Base URL:** `<PUBLIC_BASE_URL>`

Expected endpoints:

```text
GET <PUBLIC_BASE_URL>/health
GET <PUBLIC_BASE_URL>/.well-known/agent-card.json
GET <PUBLIC_BASE_URL>/.well-known/agent.json
```

The runtime should advertise the deployed public URL in the Agent Card through `--card-url` or `AEGISFORGE_CARD_URL` / `AEGISFORGE_PUBLIC_URL`.

---

## 5. Deployment contract

Recommended public runtime path:

```text
Dockerfile -> run.sh -> src/aegisforge/a2a_server.py -> Executor -> AegisForgeAgent
```

Recommended local runner path:

```text
python -m aegisforge.runner --mode serve --host 127.0.0.1 --port 8001
```

Supported runtime arguments:

```text
--host <host>
--port <port>
--card-url <url>
```

Environment variables:

```text
AEGISFORGE_HOST
AEGISFORGE_PORT
AEGISFORGE_CARD_URL
AEGISFORGE_PUBLIC_URL
PUBLIC_URL
AEGISFORGE_MAX_CONTEXT_AGENTS
AEGISFORGE_DEFAULT_ASSESSMENT_MODE
AEGISFORGE_TRACK
AEGISFORGE_DEBUG_ARTIFACTS
AEGISFORGE_TRACE_ARTIFACTS
```

Local examples in this repo should use ports `8001`, `8002`, `8003`, etc.

---

## 6. Local verification checklist

### Compile the core agent

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m py_compile .\src\aegisforge\agent.py
```

### Main test suite

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m pytest tests -q
```

### Focused checks

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m pytest tests\test_core tests\test_strategy tests\test_plugins tests\test_smoke -q
python -m pytest tests\test_adapters -q
python -m pytest tests\scripts tests\test_ci -q
python -m pytest tests\test_evaluation_lab_precision.py -q
```

### Runtime doctor

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m aegisforge.runner --mode doctor --host 127.0.0.1 --port 8001 --pretty
```

Expected result:

```json
{
  "status": "ok"
}
```

### Agent Card check

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m aegisforge.runner --mode card --host 127.0.0.1 --port 8001 --pretty
```

### Local server check

Terminal 1:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m aegisforge.runner --mode serve --host 127.0.0.1 --port 8001
```

Terminal 2:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
Invoke-RestMethod http://127.0.0.1:8001/.well-known/agent-card.json | ConvertTo-Json -Depth 20
Invoke-RestMethod http://127.0.0.1:8001/.well-known/agent.json | ConvertTo-Json -Depth 20
```

---

## 7. Docker verification

Build:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
docker build --platform linux/amd64 -t aegisforge-agent:latest .
```

Run:

```powershell
docker run --rm -p 8001:8001 aegisforge-agent:latest
```

Check:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
Invoke-RestMethod http://127.0.0.1:8001/.well-known/agent-card.json | ConvertTo-Json -Depth 20
```

---

## 8. Evaluation Lab support tool

AegisForge also includes a defensive support UI:

```text
integrations/openenv/envs/omnibench_aegis_env/evaluation_lab/
```

Purpose:

```text
read-only public GitHub benchmark repo scan
static/sandbox-safe risk review
secret-like value masking
controlled scenario generation
benign payload artifacts
no target code execution
no Docker execution from analyzed repos
```

Run locally:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m uvicorn integrations.openenv.envs.omnibench_aegis_env.server.app:app --host 127.0.0.1 --port 8001
```

Open:

```text
http://127.0.0.1:8001/lab
```

This Evaluation Lab is not the registered Purple Agent endpoint. It is supporting tooling and evidence for the repository.

---

## 9. Technical design summary

AegisForge uses the following internal flow:

```text
A2A message
-> Executor
-> AegisForgeAgent
-> metadata extraction
-> track normalization
-> classification
-> budget guard
-> router
-> role policy
-> artifact policy
-> planner
-> prompt/context expansion
-> self-check
-> A2A artifact response
```

Key design choices:

```text
single A2A Purple runtime
selected-opponent track matrix
attacker/defender assessment mode support
mcu-minecraft alias normalization to mcu
security-like handling for pibench/cybergym/netarena
bounded context-agent cache in Executor
structured artifacts when requested
safe fallback behavior
trace/debug artifacts behind environment flags
```

---

## 10. Integrity and fair play statement

This submission is intended to comply with AgentX-AgentBeats Phase 2 integrity expectations.

AegisForge does not intentionally use:

```text
hardcoded benchmark answer tables
task-specific lookup tables presented as reasoning
benchmark/platform exploits
secret extraction from real systems
unauthorized access
hidden held-out task leakage
committed API keys or private credentials
```

The agent can operate in attacker/defender mode inside the benchmark contract. That does not mean it is designed or authorized to attack real systems.

---

## 11. Abstract-ready description

AegisForge is a unified A2A-compatible Purple Agent for AgentX-AgentBeats Phase 2. It supports a selected-opponent matrix across Game, Finance, Business Process, Research, Multi-agent, τ²-Bench, Computer Use/Web, Agent Safety, Cybersecurity, and Coding categories through one shared runtime. The design emphasizes generalization over hardcoding: track normalization, benchmark-aware profiles, bounded execution memory, routing/planning policies, self-checks, and structured A2A artifact responses are integrated under a single agent architecture. Supporting tooling includes a defensive read-only Evaluation Lab for public benchmark repository review, but the submitted product remains the A2A Purple Agent runtime under `src/aegisforge/`.

---

## 12. Registration checklist

Before registering or updating the platform entry:

```text
[ ] GitHub repo is public and pushed
[ ] README.md reflects the unified v1.0 runtime
[ ] SUBMISSION.md reflects the selected opponent matrix
[ ] Docker image builds successfully
[ ] Runtime doctor returns status=ok
[ ] /health works on deployed URL
[ ] /.well-known/agent-card.json works on deployed URL
[ ] /.well-known/agent.json works on deployed URL
[ ] Agent Card URL matches deployment URL
[ ] tests pass locally
[ ] public endpoint verification passes
[ ] final abstract is ready for the registration form
[ ] Agent is registered on AgentBeats
[ ] selected green-agent runs / leaderboard attempts are launched or documented
```

---

## 13. Final status statement


AegisForge is prepared as a unified Purple Agent runtime. The remaining submission tasks are operational: push the final repo state, verify CI, deploy a stable public endpoint, confirm the Agent Card, register the agent, and run/document the selected opponent evaluations.
