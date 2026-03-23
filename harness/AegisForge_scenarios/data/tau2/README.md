# quipu_lab harness data

This folder stores **seed artifacts** for the `quipu_lab` τ²-style domain inside AegisForge.

## Purpose

The harness data provides small, inspectable pieces of domain context so the repo can demonstrate how `quipu_lab` is grounded.

These files are intended to support:

- task seeding
- tool availability examples
- policy excerpts
- documentation and smoke scenarios

## Files

- `quipu_lab_seed.json`  
  Minimal starting state or domain seed.

- `quipu_lab_tools.json`  
  Tool inventory or tool metadata for the domain.

- `quipu_lab_policy_excerpt.json`  
  A compact policy excerpt showing how the domain constrains behavior.

## Why this matters

The official τ² spirit revolves around domains that are structured, tool-aware, and policy-aware.  
Inside AegisForge, these seed files help make the τ²-style integration feel like a **real Purple capability** rather than an empty shell.

## Intended usage

These artifacts can be referenced by:

- `src/aegisforge/adapters/tau2/quipu_lab/tasks.py`
- `src/aegisforge/adapters/tau2/quipu_lab/tools.py`
- `src/aegisforge/adapters/tau2/quipu_lab/policy.py`
- `tests/tests_envs/test_tau2_quipu_lab_smoke.py`

## Guidance

Keep these files:

- minimal
- explicit
- versionable
- safe for public release

They should help document the domain and enable reproducible examples without exposing anything sensitive or benchmark-leaking.