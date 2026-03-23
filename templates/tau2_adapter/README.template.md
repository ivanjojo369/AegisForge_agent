# τ² adapter template for AegisForge

This template documents the expected structure and intent of a **τ²-style adapter** inside the AegisForge repository.

## Purpose

A τ² adapter in AegisForge is **not** meant to copy the upstream τ² framework.  
Its purpose is to translate the useful parts of τ²-style conversational evaluation into a **Purple-native capability** that can be exercised through the main AegisForge agent.

That means the adapter should support:

- structured multi-turn tasks
- explicit domain framing
- tool-aware execution
- reproducible task normalization
- consistent trace and result artifacts

## Primary goal

The adapter should make AegisForge feel like **one coherent Purple agent** with a τ²-style mode, rather than a separate benchmark project embedded inside the repo.

## Minimum expected files

A strong τ² adapter template should align with these paths:

- `src/aegisforge/adapters/tau2/config.py`
- `src/aegisforge/adapters/tau2/adapter.py`
- `src/aegisforge/adapters/tau2/quipu_lab/`
- `src/aegisforge_eval/tracks/tau2.py`
- `integrations/tau2/data_demo/quipu_lab/`
- `tests/test_adapters/test_tau2_adapter.py`
- `tests/tests_envs/test_tau2_quipu_lab_smoke.py`

## Domain expectation

The first Purple-facing τ²-style domain is:

- `quipu_lab`

That domain should expose the same core ideas that make τ² useful:

- policy
- tools
- tasks
- structured task schema
- traceable outputs

## Configuration surface

The adapter should be compatible with environment-driven configuration such as:

- `AEGISFORGE_ENABLE_TAU2`
- `TAU2_BASE_URL`
- `TAU2_DOMAIN_NAME`
- `TAU2_TIMEOUT_SECONDS`
- `TAU2_STRICT_MODE`

The default domain for this template is expected to be:

- `quipu_lab`

## Execution model

A typical τ²-style flow inside AegisForge should look like this:

1. load or receive a normalized task payload
2. validate the task structure
3. normalize the task into the adapter contract
4. resolve required tools
5. execute a deterministic or real task flow
6. emit a structured result and trace
7. hand the output to the local evaluation/reporting layer

## Template philosophy

This template is intentionally biased toward:

- reproducibility
- transparency
- small public-safe demo artifacts
- explicit structure over hidden benchmark logic
- compatibility with AegisForge Phase 2 · Purple goals

## What should not happen

This adapter template should **not** be used to:

- vendor the full upstream τ² repository
- duplicate τ² CLI, web, or leaderboard systems
- mix unrelated benchmark infrastructure into the AegisForge runtime
- hardcode benchmark answers or hidden evaluation shortcuts

## Recommended companion artifacts

A complete τ²-style adapter implementation should ideally ship with:

- a config template
- a Docker template if the adapter is packaged separately
- a domain README
- demo task JSON
- demo trace JSON
- demo run JSON
- adapter-level tests
- environment-level smoke tests

## Success condition

This template is successful when a reader can quickly understand:

- what the τ² adapter is for
- how it differs from the upstream τ² framework
- which domain it supports first
- how it plugs into AegisForge
- how it can be tested and extended cleanly
