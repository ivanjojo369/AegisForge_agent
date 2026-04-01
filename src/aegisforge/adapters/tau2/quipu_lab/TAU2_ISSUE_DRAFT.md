# Issue Draft — Add/upgrade `quipu_lab` domain support for τ²-Bench / AgentBeats Phase 2 Purple

## Summary
I would like to contribute an upgraded `quipu_lab` domain integration for τ²-Bench-focused evaluation flows used in AegisForge Agent / AgentBeats Phase 2 Purple.

This contribution expands the task catalog, strengthens catalog validation, adds dedicated catalog tests, hardens smoke coverage, and includes reproducible local evaluation / submission-prep / validation scripts for the `quipu_lab` pipeline.

## What this adds
- Expanded `quipu_lab` task catalog to **33 base tasks**
- Stronger catalog validation in `tasks.py`
- Dedicated catalog test coverage
- Hardened smoke subset checks
- End-to-end local pipeline for:
  - eval
  - submission package preparation
  - submission validation

## Why this may be useful
This contribution is aimed at improving realistic multi-turn conversational evaluation coverage for organization-facing agent behavior, especially:
- clarification-before-action behavior
- policy vs preference tradeoffs
- tool-use discipline
- multi-step handoffs
- retrieval under noise / distractors
- minimum-query behavior
- memory preservation across turns

## Validation status
Locally verified:
- adapter tests passing
- task catalog tests passing
- quipu_lab smoke tests passing
- Docker build passing
- health check passing
- agent card check passing
- local e2e passing
- submission prepare / validate passing

## Question for maintainers
Would you prefer this contribution as:
1. a domain-focused PR centered on `quipu_lab`, or
2. a broader PR that also highlights the framework hardening around catalog validation and submission scripts?

## Additional notes
I can provide:
- trajectory link in the PR description
- a concise technical note / paper link if useful
- documentation for any framework modifications or task omissions
