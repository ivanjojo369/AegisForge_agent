# SUBMISSION.md — AegisForge Agent (QuipuLoop)

AgentX–AgentBeats Phase 2 · Purple

## 1. Submission summary

**Project name:** AegisForge  
**Team / handle:** QuipuLoop Labs / ivanjojo369  
**Submission type:** Purple Agent (A2A-compatible)  
**Primary repository:** this repository  
**Primary runtime path:** `Dockerfile -> run.sh -> src/aegisforge/a2a_server.py -> /health -> /.well-known/agent-card.json`

AegisForge is a unified Phase 2 Purple agent built around a clean A2A-compatible runtime, reproducible local verification, and benchmark-capability absorption inside a single agent architecture.

This repository is intentionally organized to present **one competitive Purple agent**, not three separate benchmark projects. In particular:

- **τ²** is integrated as a Purple-native capability layer that contributes a domain implementation, reusable adapter/evaluation structure, and evidence of multi-turn benchmark absorption inside AegisForge.
- **OpenEnv** contributes environment-facing integration patterns and modular capability expansion for broader agent-environment interaction.
- **Security Arena / Agent Security** contributes defense-oriented design pressure, safer-response posture, and security-minded evaluation discipline.

The submitted product is therefore the **A2A-compatible Purple runtime implemented in this repository**, together with the integrated capability layers that strengthen its generality, evaluation structure, and architectural coherence.

---

## 2. What is the actual submitted product?

The submitted product is the **A2A-compatible Purple agent** implemented under:

- `src/aegisforge/a2a_server.py`
- `src/aegisforge/agent.py`
- `src/aegisforge/executor.py`
- `src/aegisforge/adapters/`

The following areas support the submitted product by providing evaluation, integration evidence, harness data, templates, and documentation:

- `docs/`
- `integrations/`
- `harness/`
- `tooling/`
- `templates/`
- `tests/`

These supporting areas are part of the repository’s technical evidence, but the core submitted product remains the A2A-compatible Purple runtime and its integrated capability layers.

---

## 3. Public endpoint to register

**Base URL:** `<PUBLIC_BASE_URL>`

Expected public endpoints:

- `GET <PUBLIC_BASE_URL>/health`
- `GET <PUBLIC_BASE_URL>/.well-known/agent-card.json`

If a custom card URL is used, it should still resolve correctly from the deployed runtime.

---

## 4. Deployment contract

The official submission path is:

`Dockerfile -> run.sh -> src/aegisforge/a2a_server.py -> /health -> /.well-known/agent-card.json`

Runtime arguments supported by `run.sh`:

- `--host <host>`
- `--port <port>`
- `--card-url <url>`

Example:

```bash
./run.sh --host 0.0.0.0 --port 8001 --card-url https://example.com/.well-known/agent-card.json
```

Environment variables used by the launcher:

- `AEGISFORGE_HOST`
- `AEGISFORGE_PORT`
- `AEGISFORGE_CARD_URL`

For this repository line of work, public and local examples should use ports **8001, 8002, 8003, ...** and avoid `8000`.

---

## 5. Local verification checklist

Before public registration, the following should pass locally:

### Lint

```bash
ruff check src tests scripts
```

### Smoke tests

```bash
pytest tests/test_smoke -q
```

### Core tests

```bash
pytest tests/test_core -q
```

### Adapter tests

```bash
pytest tests/test_adapters -q
```

### τ² integration checks

```bash
pytest tests/test_adapters/test_tau2_adapter.py -q
pytest tests/tests_envs/test_tau2_quipu_lab_smoke.py -q
```

### CLI / script tests

```bash
pytest tests/test_cli tests/test_scripts -q
```

### A2A end-to-end check

Linux/macOS:

```bash
bash scripts/check_a2a_e2e.sh
```

Windows PowerShell:

```powershell
.\scripts\check_a2a_e2e.ps1
```

### Repo/package verification

```bash
python scripts/verify_repo.py
python scripts/prepare_submission.py
python scripts/verify_public_endpoint.py
```

### Container verification

```bash
docker build --platform linux/amd64 -t aegisforge-agent:latest .
docker run --rm -p 8001:8001 aegisforge-agent:latest
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/.well-known/agent-card.json
```

---

## 6. Minimal public run instructions

### Docker build

```bash
docker build --platform linux/amd64 -t aegisforge-agent:latest .
```

### Docker run

```bash
docker run --rm -p 8001:8001 aegisforge-agent:latest
```

### Expected health check

```bash
curl http://127.0.0.1:8001/health
```

### Expected agent card

```bash
curl http://127.0.0.1:8001/.well-known/agent-card.json
```

---

## 7. Repository contents relevant for judges

### Core product

- `src/aegisforge/a2a_server.py`
- `src/aegisforge/agent.py`
- `src/aegisforge/executor.py`
- `src/aegisforge/adapters/`

### Integrated τ² capability

- `src/aegisforge/adapters/tau2/`
- `src/aegisforge/adapters/tau2/quipu_lab/`
- `src/aegisforge_eval/tracks/tau2.py`
- `integrations/tau2/`
- `harness/AegisForge_scenarios/data/tau2/`
- `tests/test_adapters/test_tau2_adapter.py`
- `tests/tests_envs/test_tau2_quipu_lab_smoke.py`

### Submission-facing docs

- `README.md`
- `docs/ABSTRACT.md`
- `docs/architecture.md`
- `docs/quickstart.md`
- `docs/release.md`
- `docs/submission.md`

### Verification / packaging

- `scripts/check_a2a_e2e.sh`
- `scripts/check_a2a_e2e.ps1`
- `scripts/prepare_submission.py`
- `scripts/verify_repo.py`
- `scripts/verify_public_endpoint.py`

### Example assets

- `assets/agent-card.example.json`
- `assets/health_response.example.json`
- `assets/sample_output.png`
- `assets/submission_schema.json`

---

## 8. Abstract-ready description

AegisForge is a modular yet unified Purple agent built for AgentX–AgentBeats Phase 2 with an emphasis on clean A2A interoperability, reproducible runtime validation, and capability absorption under a single agent architecture. Rather than presenting benchmark-specific side projects, the repository integrates Purple-native capability layers derived from τ²-style domain modeling, OpenEnv-style environment interaction, and Security Arena-style defense-oriented evaluation pressure. This design supports explicit adapters, structured evaluation tracks, verifiable public endpoints, and a maintainable runtime path that aims for generality rather than task-specific hardcoding.

---

## 9. Integrated Purple capabilities: τ², OpenEnv, and Security Arena

These components are used here as **integrated capability layers inside AegisForge**, not as detached benchmark replicas and not as blockers for the main Purple submission.

### τ²

Within AegisForge, τ² contributes across three dimensions:

#### a. New domain

AegisForge integrates a τ²-style domain under `src/aegisforge/adapters/tau2/quipu_lab/`, including domain policy, tools, tasks, schemas, demo data, harness data, fixtures, and smoke validation.

#### b. Framework improvement

AegisForge includes a reusable τ² adapter/config/evaluation pattern through:

- `src/aegisforge/adapters/tau2/config.py`
- `src/aegisforge/adapters/tau2/adapter.py`
- `src/aegisforge_eval/tracks/tau2.py`
- `templates/tau2_adapter/`
- τ²-specific adapter and smoke tests

This allows τ²-style capability absorption to strengthen the internal evaluation and integration structure of the Purple agent, rather than remaining an isolated experiment.

#### c. New agent architecture

The τ² work is intentionally absorbed into the same A2A-compatible Purple runtime used by the submitted product. It is not presented as a separate service or benchmark-only mini-repository. Instead, it strengthens the architecture of **one unified Purple agent** that can host multiple capability layers behind the same runtime path.

### OpenEnv

Within AegisForge, OpenEnv contributes environment-facing interaction patterns, modular integration design, and a path for expanding benchmark-style capabilities without fragmenting the submitted agent into separate products.

### Security Arena / Agent Security

Within AegisForge, Security Arena contributes defense-oriented evaluation pressure, safer-response posture, and a security-minded testing philosophy intended to improve the robustness of the submitted Purple runtime.

Taken together, these three lines strengthen AegisForge as a single Phase 2 Purple agent with broader domain coverage, clearer evaluation structure, and more coherent architecture.

---

## 10. Integrity / fair play statement

This submission is intended to comply with the competition’s integrity expectations:

- no hardcoded benchmark answer tables
- no benchmark/platform exploit attempts
- no secret keys committed
- no hidden task-specific lookup behavior presented as general reasoning
- no misleading claims about held-out or unreproduced results

Any demos included in this repo are small, explicit, and intended as transparent capability evidence rather than hidden evaluation leakage.

---

## 11. Registration checklist

Before registering on the platform, confirm all of the following:

- [ ] public GitHub repository is ready
- [ ] `README.md` is up to date
- [ ] `docs/ABSTRACT.md` is final
- [ ] Docker image builds successfully
- [ ] deployed URL is stable
- [ ] `/health` responds correctly
- [ ] `/.well-known/agent-card.json` responds correctly
- [ ] local smoke/core checks pass
- [ ] τ² adapter and smoke checks pass
- [ ] public endpoint verification passes
- [ ] agent card metadata matches deployed URL
- [ ] final abstract text is ready to paste into registration form

---

## 12. Final note

This file is the submission-oriented handoff document and the canonical submission narrative for the repository.

For general repository usage, setup, and demos, see:

- `README.md`
- `docs/quickstart.md`
- `docs/architecture.md`
- `docs/ABSTRACT.md`
- `docs/submission.md`
