# Telemetry Flow

## Purpose

Telemetry in AegisForge exists to make competitive behavior visible, debuggable, and explainable. A strong telemetry layer improves reproducibility, makes regressions easier to detect, and supports scorecard-style analysis across tracks and held-outs.

The telemetry layer should not be treated as optional logging. It is part of the competitive system.

## Design Goals

- Capture meaningful execution traces without overwhelming the runtime
- Track budget, decisions, failures, and final outputs
- Support both live debugging and post-run analysis
- Produce artifacts that are useful for ablations and readiness checks
- Keep the signal structured and comparable across tracks

## High-Level Flow

1. A task is received and assigned an episode identifier.
2. The orchestration layer initializes telemetry state.
3. Strategy decisions emit structured events.
4. The execution loop records step-level outcomes.
5. Budget statistics are updated after each meaningful operation.
6. Self-check emits validation status before finalization.
7. The runner writes a compact episode summary.
8. Optional scorecards are generated from the collected trace data.

## What Should Be Captured

### Episode metadata
- episode id
- track name
- adapter used
- timestamp
- configuration profile

### Strategy signals
- classification result
- route decision
- selected prompt profile
- selected policy profile
- planner summary

### Budget signals
- estimated budget
- budget used so far
- warning thresholds crossed
- final budget consumption

### Execution signals
- steps attempted
- step outcome
- retries
- recoveries triggered
- final completion status

### Validation signals
- self-check pass/fail
- contract violations
- risk flags
- suggested revisions

### Output signals
- response type
- artifact presence
- response length
- final structured status

## Suggested Core Components

The telemetry package should include:

- `events.py` — event types and helper constructors
- `trace_schema.py` — canonical structure for episode traces
- `emitter.py` — write and collect telemetry events
- `budget_stats.py` — cost and budget accounting
- `failure_taxonomy.py` — normalized failure categories
- `episode_summary.py` — compact human-readable summaries
- `scorecard.py` — aggregated metrics over multiple episodes

## Trace Granularity

AegisForge should prefer structured telemetry over raw verbose logs.

The target is not to record every token-level detail. The target is to make the system analyzable at the policy and execution level.

Recommended granularity:

- one event per strategic decision
- one event per execution step
- one event per recovery action
- one event for final validation
- one event for episode finalization

## File Outputs

Telemetry outputs should map into visible artifact directories such as:

- `artifacts/traces/`
- `artifacts/reports/`
- `artifacts/scorecards/`
- `artifacts/heldouts/`

This makes the repository easier to inspect and demonstrates evaluation maturity.

## Why This Helps in Competition

A repository that exposes telemetry clearly signals that the team cares about:

- reproducibility
- cost awareness
- failure analysis
- iterative improvement
- held-out robustness

In a competitive context, this helps the project look deliberate rather than improvised.

## Minimal Viable Telemetry

If implementation time is limited, the minimum useful telemetry should still capture:

- episode id
- track
- route decision
- planner summary
- budget used
- final status
- self-check result

Even this reduced signal already enables much better debugging than plain console logs.
