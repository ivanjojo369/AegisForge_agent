# Transfer from Lambda Security Arena to AegisForge

## Purpose
This note explains how lessons from the Lambda Agent Security custom track should transfer into AegisForge for the general Phase 2 Purple setting.

## What Should Transfer
The goal is not to copy Lambda's benchmark-specific structure into AegisForge. The goal is to transfer the competitive qualities that made the security-track work valuable.

Key transferable properties include:

- disciplined execution under constraints
- explicit orchestration of task episodes
- strong plugin and role boundaries
- reproducibility and clean-state thinking
- resistance to fragile heuristics
- attention to traces and artifacts

## What Should Not Transfer Directly
These parts should not be copied as-is:

- security-arena-specific benchmark assets
- custom-track submission glue
- benchmark-specific scenario data
- attacker/defender-only assumptions as permanent repo structure

## AegisForge Interpretation
AegisForge should absorb Lambda's competitive maturity while remaining benchmark-general. That means:

- extracting policy and planning logic into reusable modules
- exposing orchestration clearly
- showing evidence of held-out testing and degradation analysis
- keeping benchmark-specific behavior inside adapters rather than the core runtime

## Strategic Takeaway
Lambda proved that a serious competitive agent benefits from visible execution logic, strong contracts, and disciplined constraints. AegisForge should inherit those strengths while presenting them in a more general and more reusable architecture.
