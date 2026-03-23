# Ablations

## Purpose

This document outlines the ablation philosophy for AegisForge.

Ablations are used to answer a simple question:

**Which parts of the system actually matter for competitive performance, generalization, and cost control?**

Without ablations, a repository can look sophisticated while still hiding fragile assumptions.

## Ablation Principles

A good ablation should isolate one meaningful system change at a time.

Examples include removing or weakening:

- planner logic
- routing logic
- self-check behavior
- budget guarding
- track-specific prompt profiles
- telemetry-assisted debugging workflows

The goal is not exhaustive experimentation for its own sake. The goal is to reveal what genuinely contributes to performance and robustness.

## Recommended Core Ablations

### 1. No Planner
Replace the planner with a minimal direct-response baseline.

Question answered:
- Does explicit planning improve task completion and structure quality?

### 2. No Router
Use a single static policy path for all tasks.

Question answered:
- Does task-aware routing materially improve transfer across tracks?

### 3. No Budget Guard
Disable budget-aware decision logic.

Question answered:
- Does budget control improve cost efficiency without hurting success?

### 4. No Self-Check
Skip final validation and contract review.

Question answered:
- How much does self-check reduce preventable formatting and policy failures?

### 5. Flat Prompts
Replace track-aware prompts with a single generic prompt.

Question answered:
- How much of the system's quality comes from prompt specialization versus structural reasoning?

### 6. Reduced Context Mode
Aggressively compress context before execution.

Question answered:
- How sensitive is the agent to limited context windows or aggressive summarization?

### 7. Held-Out Stress Mode
Test the system on task variants not present in standard smoke cases.

Question answered:
- Which layers actually help under transfer pressure?

## Metrics to Compare

Each ablation should be evaluated along at least these dimensions:

- task success or completion quality
- format correctness
- budget usage
- latency or step count
- held-out degradation
- failure mode distribution

## Why This Matters

Ablations do more than strengthen internal engineering. They also improve the repository's research posture.

They make it easier to argue that AegisForge is:

- modular by design
- measurable in practice
- not overfit to one benchmark contract
- capable of meaningful transfer across evaluation settings

## Suggested Artifact Outputs

Ablation runs should produce:

- per-run scorecards
- compact summaries
- failure mode counts
- held-out degradation tables

These can live under `artifacts/ablations/` and `artifacts/scorecards/`.

## Initial Recommendation

The first ablation suite should focus on:

1. no planner
2. no router
3. no budget guard
4. no self-check

This gives a strong first picture of where the competitive value of the architecture actually lives.
