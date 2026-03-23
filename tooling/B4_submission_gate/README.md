# B4_submission_gate

`B4_submission_gate` is the phase that comes **after evaluation** and **before
public registration**. It exists to answer one practical question:

> Is this AegisForge build actually ready to be deployed, checked, and registered?

## Why this phase exists

`A3_evaluator` helps you inspect behavior.
`B4_submission_gate` helps you harden and verify the submission package.

This layer is where you validate:

- public `/health` availability,
- public `/.well-known/agent-card.json` availability,
- basic agent-card structure,
- basic repository readiness signals,
- normalized JSON / Markdown reports for handoff.

## Suggested endpoints

Start the gate service:

```bash
uvicorn tooling.B4_submission_gate.app.server:app --reload --port 8014
```

Health check:

```bash
curl http://127.0.0.1:8014/health
```

Run a gate check:

```bash
curl -X POST http://127.0.0.1:8014/check-endpoint       -H "content-type: application/json"       -d '{"base_url":"http://127.0.0.1:8000"}'
```

## Folder contract

- `app/checks.py` performs raw endpoint checks.
- `app/validators.py` validates payload structure.
- `app/reporters.py` converts results into JSON/Markdown outputs.
- `schemas/` documents the expected result shapes.

This is intentionally narrow and submission-focused so it complements the
main Purple Agent repo instead of competing with it.
