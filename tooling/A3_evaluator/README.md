# A3_evaluator

`A3_evaluator` is the lightweight evaluation phase that sits beside the main
`aegisforge` runtime. Its job is to offer a simple local service for running
adapter-facing checks and scoring demos without polluting the Purple Agent
entrypoint itself.

## Purpose

This layer exists to make the repository feel more complete and auditable:

- it isolates evaluator logic from the core A2A runtime,
- it provides a stable place for local benchmark-style checks,
- it gives you a bridge between `src/aegisforge_eval/` and ad hoc tooling.

## Suggested local usage

```bash
uvicorn tooling.A3_evaluator.app.server:app --reload --port 8013
```

Health check:

```bash
curl http://127.0.0.1:8013/health
```

Minimal evaluation request:

```bash
curl -X POST http://127.0.0.1:8013/evaluate       -H "content-type: application/json"       -d '{"adapter":"tau2","domain":"quipu_lab","turns":[{"role":"user","content":"hello"}]}'
```

## Contract

- `server.py` exposes HTTP endpoints.
- `src/aegisforge_eval/` holds the reusable evaluation logic.
- This directory can evolve independently without changing the Purple runtime.
