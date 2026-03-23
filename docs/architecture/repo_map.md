# AegisForge Repo Map

## Purpose
This document explains the high-level structure of the AegisForge repository for AgentBeats Phase 2 Purple.

## Core Layers

### `src/aegisforge/`
The main runtime for the purple agent. This layer contains the agent entrypoints, execution flow, configuration, health endpoints, A2A server integration, and shared models.

### `src/aegisforge/adapters/`
Adapters bridge benchmark-specific inputs and outputs into AegisForge's internal execution model. Each adapter should translate task context, constraints, and output contracts into a common agent-facing representation.

### `src/aegisforge_eval/`
This layer contains evaluation runners, schemas, track definitions, and reporting helpers. It is the main place to run internal evaluation before packaging submissions.

### `integrations/`
Integration packages for local environments, demos, or benchmark-facing wrappers.

### `harness/`
Internal scenario data and reusable evaluation assets. This is the workspace for smoke tasks, held-outs, and scenario playbooks.

### `tests/`
Repository-wide validation. This should include smoke tests, adapter tests, strategy tests, held-out tests, resilience tests, and evaluation checks.

## Recommended Expansion Areas

- `src/aegisforge/strategy/`: explicit competitive decision-making layer
- `src/aegisforge/orchestration/`: episode execution and recovery flow
- `src/aegisforge/prompts/`: versioned prompt assets
- `src/aegisforge/telemetry/`: traces, scorecards, summaries
- `src/aegisforge_eval/heldouts/`: generalization and degradation testing

## Architectural Goal
AegisForge should look like a reusable purple-agent platform rather than a single-track benchmark wrapper. The repository should make its strategy, orchestration, and generalization story visible at the tree level.
