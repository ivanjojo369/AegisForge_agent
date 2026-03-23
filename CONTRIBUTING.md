# Contributing

Thanks for contributing to AegisForge.

## Goals

Contributions should make the repo cleaner, more reproducible, and easier to evaluate publicly.

## General rules

- keep the core runtime stable
- keep optional integrations modular
- prefer small, explicit changes
- document behavior that affects public endpoints or submission flow

## Repository hygiene

Do not commit:

- `.venv/`
- `__pycache__/`
- `*.egg-info`
- temporary logs
- local result dumps

## Where changes belong

- runtime code: `src/aegisforge/`
- scripts: `scripts/`
- tests: `tests/`
- docs: `docs/`
- examples: `examples/`

## Recommended workflow

1. create a focused branch
2. make a small change
3. add or update tests when needed
4. update docs if behavior changes
5. run lint and smoke checks

## Pull request expectations

A good pull request should:

- explain what changed
- explain why it changed
- avoid unrelated repo churn
- keep the public runtime contract intact
