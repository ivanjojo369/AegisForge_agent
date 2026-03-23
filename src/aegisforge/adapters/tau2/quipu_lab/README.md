# quipu_lab — τ²-style domain for AegisForge

This directory contains the **Purple-adapted τ²-style domain layer** for `quipu_lab`.

## Purpose

`quipu_lab` is the AegisForge-native interpretation of a τ²-style conversational evaluation domain.  
Its goal is to let the main Purple agent operate on:

- structured multi-turn tasks
- explicit tools
- domain policies
- reproducible task schemas

This is **not** a copy of the upstream τ² framework.  
Instead, it is a clean adaptation for **AgentX–AgentBeats Phase 2 · Purple**.

## Files

- `policy.py`  
  Domain rules, constraints, and safety expectations.

- `tools.py`  
  Tool definitions and helper functions exposed to the domain.

- `tasks.py`  
  Sample task builders and task-loading helpers.

- `schemas.py`  
  Data structures used to normalize task input, traces, and outputs.

## Design goals

This domain implementation is intended to make AegisForge feel like a **single competitive Purple agent** with a τ²-style capability, rather than a bundle of unrelated side projects.

The design emphasizes:

- maintainability
- reproducibility
- explicit task structure
- clean adapter boundaries
- compatibility with AegisForge evaluation/reporting flows

## How it connects to the repo

Main related paths:

- `src/aegisforge/adapters/tau2/adapter.py`
- `src/aegisforge_eval/tracks/tau2.py`
- `integrations/tau2/data_demo/quipu_lab/`
- `tests/tests_envs/test_tau2_quipu_lab_smoke.py`
- `harness/AegisForge_scenarios/data/tau2/quipu_lab/`

## Intended outcome

When fully wired, this folder should support a flow where:

1. a τ²-style task is loaded,
2. AegisForge normalizes it through the τ² adapter,
3. the Purple agent executes the task,
4. a trace/report is produced in a consistent format.

That makes `quipu_lab` an internal **Purple-ready benchmark mode** rather than just a placeholder domain.
