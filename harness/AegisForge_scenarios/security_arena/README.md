# AegisForge

AegisForge is a submission-ready A2A purple-agent runtime built to be judge-friendly, reproducible, and easy to deploy for AgentBeats-style evaluation.

The project is structured around a clean core runtime in `src/aegisforge/`, with optional integrations for external ecosystems such as OpenEnv, τ²-Bench, and Security Arena. Those integrations are intentionally kept modular so the main repo remains focused on a stable, public-facing purple-agent runtime.

## What this repo provides

- A Dockerized A2A runtime that binds to `0.0.0.0:8000`
- A health endpoint at `/health`
- A public Agent Card at `/.well-known/agent-card.json`
- Local run and smoke-check scripts
- Submission-oriented docs, metadata, and provenance utilities
- A clean separation between core runtime and optional integrations

## Core principles

- **Judge-friendly:** minimal surprises for evaluators
- **Fresh-state:** every run should start clean
- **Reproducible:** repo, image, metadata, and checks should line up
- **Fair-play aligned:** no hardcoded answers or track-specific lookup hacks

## Quickstart

### Docker

```bash
docker build -t aegisforge:local .
docker run --rm -p 8000:8000 \
  -e HOST=0.0.0.0 \
  -e PORT=8000 \
  -e AGENT_PORT=8000 \
  -e AEGISFORGE_PUBLIC_URL=http://127.0.0.1:8000 \
  aegisforge:local
  