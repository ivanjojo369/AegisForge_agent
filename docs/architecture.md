# Architecture

AegisForge is organized as a clean runtime core plus optional external integrations.

## Design goals

- Keep the public runtime small and stable
- Make `/health` and the Agent Card deterministic
- Isolate track-specific logic behind adapters
- Preserve reproducibility and submission hygiene

## Top-level architecture

```text
Client / Evaluator
        |
        v
  AegisForge A2A Runtime
        |
        +-- agent.py
        +-- executor.py
        +-- a2a_server.py
        +-- health.py
        +-- agent_card.py
        |
        +-- adapters/
              +-- openenv/
              +-- tau2/
              +-- security/
```

## Core modules

### `agent.py`
Defines the public behavior of the AegisForge agent at the runtime layer.

### `executor.py`
Coordinates request execution and delegates optional track-specific handling to adapters when enabled.

### `a2a_server.py`
Owns the HTTP-facing runtime behavior and endpoint wiring.

### `health.py`
Produces a deterministic health payload used by smoke tests and deployment probes.

### `agent_card.py`
Builds the public Agent Card document that the evaluator sees at `/.well-known/agent-card.json`.

### `config.py`
Loads environment variables and turns them into a validated application configuration object.

### `models.py`
Holds small typed models used across runtime responses and config serialization.

### `logging.py`
Centralizes logger setup so scripts, server startup, and runtime checks use a consistent log style.

## Adapter model

Adapters are optional, track-oriented modules that should not destabilize the base runtime. Each adapter should expose a small, narrow interface and avoid leaking third-party structural assumptions into the main server.

Expected adapter files:

- `adapter.py`
- `config.py`
- `README.md`

## Plugin model

A lightweight plugin layer can register optional capabilities without bloating the runtime path. The registry should remain simple and explicit.

## Runtime contract

A healthy AegisForge deployment should always provide:

- `GET /health`
- `GET /.well-known/agent-card.json`

Those endpoints are the minimum public contract.

## Fresh-state expectations

Each run should begin from a clean state. Runtime behavior should not depend on hidden previous-session artifacts, cached scenario answers, or static task-specific lookup tables.

## Submission boundary

The core runtime is the product being evaluated publicly. Integrations, demos, and harness code support that runtime, but should remain secondary in the structure and in the README narrative.
