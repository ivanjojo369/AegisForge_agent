# Failure Modes

## Purpose

This document defines the main failure modes that AegisForge should track during development, evaluation, and held-out testing.

The goal is not only to list mistakes, but to create a shared language for debugging competitive behavior. A repository becomes much stronger when it can describe how it fails, not only when it succeeds.

## Philosophy

AegisForge is intended to be a reusable purple-agent system for general evaluation tracks. That means failure analysis must focus on transfer, robustness, and evaluation realism, not just single-benchmark wins.

The most important question is:

**When AegisForge fails, why does it fail, and is that failure likely to generalize?**

## Failure Categories

### 1. Routing Failures
The system chooses the wrong track path, adapter, or prompt profile.

Typical signs:
- wrong adapter selected
- task is treated as low-risk when it is not
- task is routed into a simpler policy than needed
- tool-use assumptions do not match the task

Impact:
- poor first-step quality
- avoidable downstream degradation
- benchmark mismatch

### 2. Planning Failures
The planner builds an incomplete or weak plan.

Typical signs:
- too few steps for a multi-step task
- missing artifact requirements
- poor prioritization
- underestimating task complexity

Impact:
- shallow outputs
- format misses
- weak final answers

### 3. Budget Failures
The system spends too much budget or manages the budget poorly.

Typical signs:
- unnecessary extra reasoning passes
- repeated retries without improvement
- failure to compress context near limits
- no graceful finalization when close to hard limits

Impact:
- cost inefficiency
- timeout risk
- fragile behavior on longer tasks

### 4. Validation Failures
The output is plausible but violates a response contract or misses required structure.

Typical signs:
- malformed JSON
- missing fields
- weak artifact packaging
- unsupported claim structure

Impact:
- evaluation penalties despite reasonable reasoning
- avoidable benchmark failures

### 5. Generalization Failures
The system performs well on seen patterns but degrades on held-outs.

Typical signs:
- performance collapses on small task variations
- policy relies on narrow heuristics
- overfitting to known task phrasing
- weak transfer across tracks

Impact:
- unstable leaderboard performance
- misleading internal metrics

### 6. Recovery Failures
The system encounters an error but cannot recover productively.

Typical signs:
- repeated invalid steps
- no fallback strategy
- context corruption after partial failure
- bad finalization after tool or parsing errors

Impact:
- brittle behavior
- poor resilience under pressure

### 7. Explainability Failures
The system may complete the task, but the trace is too poor to debug or trust.

Typical signs:
- no clear route reason
- no episode summary
- missing budget signal
- impossible-to-compare runs

Impact:
- hard to improve
- weak research narrative
- poor reproducibility

## Priority Failure Set

For Phase 2 readiness, the most important failure modes to prioritize are:

1. routing failures
2. planning failures
3. budget failures
4. generalization failures
5. validation failures

These five categories affect both competitive performance and repository credibility.

## Instrumentation Recommendation

Each episode should ideally report at least one normalized failure label when something goes wrong. This enables:

- trend analysis
- ablation comparisons
- held-out degradation analysis
- readiness tracking

## Why This Document Exists

AegisForge should not rely on vague statements like "the agent struggled." It should instead use a repeatable taxonomy that supports debugging and technical storytelling.

That is especially important for a repository meant to look strong, research-oriented, and competition-ready.
