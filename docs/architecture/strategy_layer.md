# Strategy Layer

## Why a Strategy Layer Exists
AegisForge already has a strong shell for adapters, evaluation, and submission packaging. The next step is to make the competitive brain of the system explicit.

Without a visible strategy layer, core reasoning logic tends to become scattered across `agent.py`, `executor.py`, and adapter code. That makes the system harder to explain, tune, and compare across tracks.

## Responsibilities
The strategy layer should own:

- task classification
- route selection
- planning
- budget control
- self-checking before final output
- track-specific decision biases

## Proposed Modules

### `task_classifier.py`
Infers task type, track guess, risk level, expected artifacts, and complexity.

### `planner.py`
Builds a short execution plan for the episode: goal, steps, expected tool usage, and estimated budget.

### `router.py`
Chooses the adapter, prompt profile, and policy profile that best fit the task.

### `budget_guard.py`
Tracks step budget, estimated token use, context compression triggers, and early-stop rules.

### `self_check.py`
Validates the output against format contracts, risk expectations, and required evidence before returning a final answer.

### `track_profiles/`
Stores per-track defaults for planning style, caution level, prompt bias, and validation strictness.

## Design Principle
The strategy layer should not know benchmark internals in detail. Instead, it should reason over a normalized `TaskContext` and produce reusable decisions that any adapter can apply.

## Outcome
Making this layer explicit helps AegisForge look more like a serious competitive agent and less like a thin benchmark wrapper.
