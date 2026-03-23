# Repository Layout

This document explains where things belong inside AegisForge.

## Root

- `README.md` — public entrypoint
- `pyproject.toml` — project metadata and packaging config
- `uv.lock` — locked dependency state
- `Dockerfile` — main runtime image
- `run.sh` — container/runtime entry helper
- `.env.example` — environment template

## Source

- `src/aegisforge/` — runtime core
- `src/aegisforge/adapters/` — optional track integrations
- `src/aegisforge/plugins/` — optional plugin registry and base interfaces
- `src/aegisforge/utils/` — shared low-level utilities

## Documentation

- `docs/` — architecture, release, submission, integrations
- `docs/demos/` — demo-oriented markdown files

## Assets and examples

- `assets/` — example Agent Card and submission schema
- `examples/` — payloads and scenario examples

## Scripts

- `scripts/` — helper scripts for local run, validation, provenance, and packaging

## Tests

- `tests/test_smoke/` — endpoint-level smoke checks
- `tests/test_core/` — runtime unit tests
- `tests/test_adapters/` — adapter tests
- `tests/test_cli/` — script and CLI tests
- `tests/fixtures/` — JSON and TOML fixtures

## Integrations and harness

- `integrations/` — external ecosystem notes and helper wrappers
- `harness/` — minimal scenario-facing evaluation scaffolding

## Layout philosophy

The repo should feel centered around `src/aegisforge/`, not around external benchmark codebases. External integrations support the runtime, but do not define the repo’s identity.
