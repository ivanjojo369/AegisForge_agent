# Cost Control Playbook

## Purpose

This playbook defines the practical budget discipline that AegisForge should maintain during evaluation.

Competitive systems should not only solve tasks; they should solve them efficiently.

## Core Principles

- prefer short effective plans over excessive reasoning loops
- avoid redundant retries
- compress context when appropriate
- finalize gracefully near hard limits
- reserve extra effort for truly high-risk tasks

## Recommended Behavioral Pattern

1. Estimate task complexity early.
2. Set an expected budget range before deep execution.
3. Spend additional budget only when it clearly improves task quality.
4. Compress or simplify when warning thresholds are crossed.
5. Finalize with the best supported answer before hard failure.

## Common Waste Patterns

- repeated self-reflection with no new information
- duplicate parsing or formatting passes
- over-engineered planning for trivial tasks
- retrying the same failing action without adaptation

## Metrics Worth Tracking

- number of reasoning passes
- budget consumed per episode
- retries per episode
- final status under budget pressure
- held-out degradation under tight budgets

## Implementation Hint

This playbook should influence:

- budget guard thresholds
- planner aggressiveness
- route selection
- telemetry summaries
