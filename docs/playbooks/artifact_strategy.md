# Artifact Strategy Playbook

## Purpose

This playbook explains how AegisForge should think about artifact-producing tasks such as structured reports, JSON outputs, intermediate files, and track-specific deliverables.

The goal is not merely to create artifacts, but to create the right artifacts in a way that remains evaluation-friendly and reproducible.

## Core Principles

- respect the requested artifact contract
- prefer simple, valid, well-structured artifacts
- avoid unnecessary artifact complexity
- preserve final usability and validation readiness
- ensure consistency between the answer and the artifact payload

## Recommended Behavioral Pattern

1. Determine whether the task expects a final answer, an artifact, or both.
2. Infer the contract early: format, fields, and required deliverables.
3. Plan with artifact constraints in mind instead of bolting them on at the end.
4. Validate the artifact before finalization.
5. Keep trace metadata sufficient for debugging generation failures.

## Common Failure Modes

- generating the right content in the wrong format
- producing malformed JSON
- forgetting required fields
- describing an artifact without actually creating the relevant payload
- mismatch between the text answer and the structured artifact

## Implementation Hint

This playbook should connect to:

- planner logic
- response contracts
- self-check validation
- artifact-related telemetry fields
