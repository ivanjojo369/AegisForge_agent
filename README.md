# AegisForge Agent (QuipuLoop) — AgentX–AgentBeats Phase 2 (Purple)

**AegisForge** is a Phase 2 (**Purple**) AgentX–AgentBeats entry that consolidates real, runnable capabilities inspired by:
- **τ²-Bench**: multi-turn organizational-agent patterns, domain/tool interfaces, and a custom domain (`quipu_lab`)
- **OpenEnv**: environment-server interaction patterns (agent ↔ env via HTTP)
- **Agent Security / Security Arena**: defense-in-depth (injection resistance, sanitization, safe output formatting)

This repository is intentionally organized so judges can clearly see the **Purple Agent core** vs. **integration evidence** vs. **local harness/tooling**.

---

## Repository structure

```
AegisForge_agent/
  .github/workflows/
    test-and-publish.yml              # CI + container publish

  src/aegisforge/                     # ✅ Purple Agent core (A2A)
    __init__.py
    a2a_server.py                     # A2A server entrypoint
    agent.py                          # agent router/orchestrator
    adapters/
      openenv/                        # OpenEnv adapter(s)
      security/                       # Security adapter(s) / policies
      tau2/                           # τ² adapter(s)

  integrations/                       # ✅ Minimal evidence + integration assets (thin copies)
    openenv/
    security_arena/
    tau2/

  harness/                            # 🟨 Optional local harness/scenarios (NOT the A2A product)
    AegisForge_scenarios/
      debate/
      security_arena/

  tooling/                            # 🟨 Optional developer tooling
    A3_evaluator/app/server.py

  tests/
  scripts/
    regen_requirements.ps1            # regenerate requirements*.txt via uv -> TEMP (Windows-safe)
  docs/
  assets/

  pyproject.toml                      # source-of-truth dependencies
  uv.lock                             # (if used)
  requirements.txt                    # auto-generated compatibility (from pyproject)
  requirements-dev.txt                # auto-generated compatibility (from pyproject --extra test)
  Dockerfile
  README.md
```

**Key principle:** `src/aegisforge/` is the product submitted/registered as the **Purple Agent**.
`integrations/` proves capability with minimal, runnable assets.
`harness/` and `tooling/` are optional and exist for local iteration only.

---

## Requirements

- Python **3.11+** recommended
- Windows / PowerShell supported
- **uv** recommended (but `requirements*.txt` are provided as compatibility)

---

## Setup

### Option A (recommended): uv

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
uv sync --extra test
```

### Option B: pip (compatibility)

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

---

## Run the Purple Agent (A2A server)

```powershell
uv run python -m aegisforge.a2a_server
```

If your server supports host/port configuration, set env vars (example):

```powershell
$env:HOST="127.0.0.1"
$env:PORT="9009"
uv run python -m aegisforge.a2a_server
```

> Notes:
> - The **Agent Card** and A2A endpoints should be served by `a2a_server.py`.
> - Keep the runtime deterministic where possible (timeouts, bounded memory/logging).

---

## Regenerate `requirements*.txt` (Windows-safe)

`pyproject.toml` is the source of truth. `requirements*.txt` are generated for compatibility.

```powershell
.\scripts\regen_requirements.ps1
```

---

## Capability demos (runnable evidence)

These demos are meant to be **small, reproducible, and honest**. They do not include private/held-out evaluation tasks.

### Demo A — τ²-style domain/tooling
- Demonstrates multi-turn handling and a minimal public sample using τ²-like interfaces.
- Includes custom domain evidence under `integrations/tau2/` (e.g., `quipu_lab`).

Run (placeholder—wire to your demo entrypoints):
```powershell
uv run python -c "from aegisforge.adapters.tau2 import demo; demo.run()"
```

### Demo B — OpenEnv environment interaction
- Demonstrates connecting to an environment server and executing one short episode.
- Assets/config under `integrations/openenv/`.

Run:
```powershell
uv run python -c "from aegisforge.adapters.openenv import demo; demo.run()"
```

### Demo C — Security layer (defense-in-depth)
- Demonstrates prompt-injection detection, sanitization, and safe output formatting.
- Policy assets under `integrations/security_arena/`.

Run:
```powershell
uv run python -c "from aegisforge.adapters.security import demo; demo.run()"
```

---

## Tests

```powershell
uv run pytest -q
```

---

## Docker

Build (recommended for CI/runner parity):

```powershell
docker build --platform linux/amd64 -t ghcr.io/<owner>/<repo>:latest .
```

Run locally:

```powershell
docker run --rm -p 9009:9009 ghcr.io/<owner>/<repo>:latest
```

---

## Competition / fairness notes

- No hard-coded benchmark answers.
- No exploitation of evaluation harness or platform vulnerabilities.
- No secrets committed (no `.env`, no API keys).
- Generated outputs are not versioned (`results/`, `output/`, caches).

---

## Team / Contact

- Team / Project: **QuipuLoop Labs – AegisForge**
- Handle: **ivanjojo369**
