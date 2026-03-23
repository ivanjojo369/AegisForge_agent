# τ² integration for AegisForge

This directory documents how **τ²-style evaluation concepts** are integrated into AegisForge.

## Why this exists

The goal is **not** to vendor or duplicate the official τ² repository.  
The goal is to adapt the useful parts of that challenge into a form that strengthens **AegisForge as a Phase 2 · Purple agent**.

That means translating τ² ideas into:

- an internal domain representation
- adapter-based task normalization
- reproducible demo inputs
- evaluation traces and run summaries
- lightweight smoke-testable flows

## Scope of this integration

Inside AegisForge, τ² is treated as:

- a **multi-turn evaluation mode**
- a **domain-oriented adapter path**
- a **reportable benchmark-style capability**

The first target domain is:

- `quipu_lab`

## Key paths

- `src/aegisforge/adapters/tau2/`
- `src/aegisforge/adapters/tau2/quipu_lab/`
- `src/aegisforge_eval/tracks/tau2.py`
- `integrations/tau2/data_demo/quipu_lab/`
- `tests/tests_envs/test_tau2_quipu_lab_smoke.py`
- `harness/AegisForge_scenarios/data/tau2/quipu_lab/`

## What “done” looks like

A strong τ² integration inside AegisForge should provide:

- a task schema
- a normalized run path
- a minimal trace artifact
- a run summary artifact
- a smoke test
- a clear README showing how the flow works

## Relationship to the official τ² challenge

The official τ² challenge focuses on contributions such as:

- new domains
- framework improvements
- new agent architectures

Inside AegisForge, we reinterpret that spirit as a **Purple-native capability** that improves:

- generality
- technical quality
- reproducibility
- evaluation readiness

## Current subdirectory

See:

- `data_demo/quipu_lab/`

for small reproducible examples that show how the τ²-style mode should look inside the AegisForge repository.