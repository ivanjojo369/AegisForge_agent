# Phase 2 Gap Analysis

## Objective

This document summarizes the main gaps between the current AegisForge repository state and a stronger Phase 2 general-purpose competitive posture.

The focus is not on infrastructure completeness alone. The focus is on competitive readiness, visibility of core reasoning, and generalization under evaluation.

## Current Strengths

AegisForge already shows several important strengths:

- multi-track structure
- visible adapters
- evaluation runner layer
- submission preparation and validation scripts
- smoke coverage for environments and adapters
- repository structure aligned with a reusable purple-agent system

This means the project is already beyond the level of a one-off prototype.

## Main Gaps

### 1. Strategy Layer Is Not Yet Visible Enough
The repository needs a first-class strategy package that makes planning, routing, self-check, and budget logic explicit.

Why it matters:
- improves interpretability
- reduces monolithic logic in core files
- makes competitive reasoning look intentional

### 2. Orchestration Is Not Yet Exposed as a Clear Layer
Execution flow is present, but it should become more visible and modular.

Why it matters:
- shows clean-state thinking
- makes recovery and episode lifecycle easier to inspect
- aligns with reproducibility expectations

### 3. Telemetry and Scorecarding Need Stronger Presence
The project should emit clearer traces, summaries, and scorecards.

Why it matters:
- improves debugging
- supports failure analysis
- makes the repository feel much more evaluation-mature

### 4. Held-Out and Generalization Machinery Should Be More Explicit
Generalization is central to competitive credibility, but it is not yet visible enough as a package.

Why it matters:
- prevents a purely smoke-test mindset
- signals robustness-oriented design
- supports stronger research documentation

### 5. Research Narrative Can Be Expanded
The repository should communicate not only what it does, but why its design choices matter.

Why it matters:
- increases perceived technical depth
- helps reviewers and collaborators understand the architecture
- differentiates the project from thinner submissions

## Recommended Priorities

### High Priority
- add `src/aegisforge/strategy/`
- add `src/aegisforge/orchestration/`
- add `src/aegisforge/prompts/`

### Medium Priority
- add `src/aegisforge/telemetry/`
- add `src/aegisforge_eval/heldouts/`
- expand trace and scorecard artifacts

### Lower Priority but Valuable
- add playbooks and example plugin packages
- expand architecture and research docs
- add ablation-oriented scripts and reports

## Success Condition

A stronger Phase 2-ready AegisForge should visibly communicate three things:

1. how it decides
2. how it executes
3. how it proves transfer and robustness

When those three become visible, the repository stops looking like a competent shell and starts looking like a serious competitive system.
