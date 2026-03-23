# Adapter Flow

## Purpose

AegisForge uses adapters to translate heterogeneous benchmark/task formats into a consistent internal execution model. The adapter layer is the boundary between external tracks and the reusable competitive core.

This design prevents the agent from being overfit to a single benchmark contract while keeping the strategy, orchestration, and telemetry layers reusable across tracks.

## Design Goals

- Normalize task inputs from multiple tracks into a common `TaskContext`
- Preserve track-specific constraints without leaking them into core logic
- Isolate benchmark glue from strategic reasoning
- Make evaluation behavior reproducible and debuggable
- Support future tracks with minimal changes to the agent core

## High-Level Flow

1. An incoming task reaches the evaluation runner or the A2A server.
2. The runner selects the appropriate track module.
3. The track module delegates task translation to the corresponding adapter.
4. The adapter converts raw benchmark payloads into internal models.
5. The strategy layer classifies, plans, and routes the task.
6. The execution layer runs the selected path.
7. The adapter converts the internal result back into the track-specific response format.
8. Telemetry, traces, and summaries are emitted for debugging and analysis.

## Core Responsibilities of an Adapter

Each adapter should handle the following responsibilities:

### 1. Input normalization
Map raw task payloads into internal objects such as:

- `TaskContext`
- `TrackHint`
- `ExecutionConstraints`
- `ArtifactRequirements`

### 2. Constraint mapping
Translate track-specific rules into reusable internal policies:

- output format expectations
- tool availability
- allowed artifacts
- time or cost constraints
- track-specific safety checks

### 3. Context shaping
Prepare only the context that the core agent needs:

- task description
- environment signals
- tool affordances
- benchmark metadata
- hidden or inferred task properties

### 4. Result projection
Convert internal outputs back into the benchmark's expected envelope:

- final answer
- structured JSON
- artifacts
- trace metadata
- evaluation-side fields

## Why This Layer Matters

Without a strong adapter layer, benchmark-specific assumptions leak into the core agent. That makes the system harder to extend and easier to overfit.

AegisForge treats adapters as strict translation boundaries. The goal is not only correctness but transferability: the same planning and self-check logic should work whether the task comes from OpenEnv, a security-style track, or a future track added later.

## Example Internal Contract

A useful adapter contract should expose methods similar to:

- `build_task_context(raw_input)`
- `build_constraints(raw_input)`
- `build_artifact_contract(raw_input)`
- `finalize_response(internal_result)`
- `emit_adapter_metadata(internal_result)`

This contract keeps the benchmark integration explicit and testable.

## Recommended Structure

A security adapter package can include:

- `adapter.py` — entry point and translation logic
- `config.py` — adapter-specific settings
- `policy_bridge.py` — mapping from track rules to internal policies
- `context_mapper.py` — input normalization and shaping
- `README.md` — usage and assumptions

A similar shape can be used for OpenEnv and Tau2 adapters.

## Testing Strategy

Adapter tests should verify:

- correct normalization of raw task payloads
- stable mapping into `TaskContext`
- correct formatting of final responses
- safe handling of missing or malformed benchmark fields
- deterministic behavior on smoke fixtures

## Competitive Value

This layer helps AegisForge stand out because it communicates a clear engineering philosophy:

- benchmark-aware
- benchmark-decoupled
- reusable across tracks
- auditable under evaluation

That makes the repository look less like a one-off submission and more like a reusable competition system.
