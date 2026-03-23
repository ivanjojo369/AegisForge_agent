# Readiness Checklist

## Purpose

This checklist is intended to help assess whether AegisForge is ready for stronger Phase 2 general-purpose positioning.

The goal is not perfection. The goal is to ensure that the most visible and competition-relevant components are present, coherent, and defensible.

## Repository Structure

- [ ] Core agent package is coherent and importable
- [ ] Adapters are separated by track
- [ ] Evaluation runner is functional
- [ ] Submission preparation scripts run successfully
- [ ] Submission validation scripts run successfully
- [ ] Templates and fixtures are present for smoke coverage

## Strategy Layer

- [ ] Task classification exists
- [ ] Planner exists and returns a structured plan
- [ ] Router exists and selects track-aware paths
- [ ] Budget guard exists and enforces practical limits
- [ ] Self-check exists and validates response contracts
- [ ] Track profiles exist for major supported tracks

## Orchestration Layer

- [ ] Episode lifecycle is explicit
- [ ] Execution loop is separated from task intake
- [ ] Task context construction is reusable
- [ ] Recovery logic is defined or planned
- [ ] State reset or clean-start assumptions are documented

## Prompts

- [ ] Core prompts are versioned
- [ ] Track-aware prompts exist
- [ ] Output contracts are documented
- [ ] Prompt manifest is present

## Telemetry

- [ ] Structured trace schema exists
- [ ] Episode summaries are generated
- [ ] Budget statistics are captured
- [ ] Reports or scorecards are emitted to artifacts
- [ ] Failure categories are normalized

## Generalization

- [ ] Held-out registry exists
- [ ] Degradation analysis exists
- [ ] Generality checks are documented or implemented
- [ ] Smoke and robustness suites are distinguished
- [ ] Overfitting risks are acknowledged in docs

## Testing

- [ ] Core smoke tests pass
- [ ] Adapter tests pass
- [ ] Environment smoke tests pass
- [ ] Strategy tests exist
- [ ] Held-out or robustness tests exist
- [ ] Budget or resilience tests exist

## Documentation

- [ ] Architecture docs explain the major layers
- [ ] Research docs explain transfer from Lambda work
- [ ] Failure modes are documented
- [ ] Ablation philosophy is documented
- [ ] Phase 2 gap analysis is current

## Final Readiness Question

AegisForge is much closer to standout status when the answer to all three questions is yes:

- [ ] Can a reviewer see how the agent decides?
- [ ] Can a reviewer see how the agent executes?
- [ ] Can a reviewer see how the agent demonstrates generalization?
