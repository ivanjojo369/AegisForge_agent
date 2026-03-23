# τ² Integration

This document explains how AegisForge relates to τ²-Bench-style evaluation.

## Purpose

The τ² integration layer allows AegisForge to expose a clean boundary for conversational evaluation workflows without embedding the τ² framework directly into the main runtime.

## Integration goals

- keep the runtime modular
- isolate τ²-specific orchestration concerns
- preserve judge-friendly behavior
- avoid polluting the public runtime with benchmark internals

## Expected adapter surface

A minimal τ² adapter should include:

- `adapter.py`
- `config.py`
- `README.md`

## Suggested responsibilities

- translating config into adapter-ready behavior
- normalizing runtime calls
- keeping τ²-facing logic explicit and testable

## Non-goals

- mirroring the whole τ² repo
- copying its internal directory tree into AegisForge
- treating τ² code as the identity of the repo

## Recommended approach

Treat τ² as an external ecosystem. AegisForge should integrate with it professionally, but remain structurally independent.
