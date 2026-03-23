# OpenEnv Integration

This document explains how AegisForge relates to OpenEnv.

## Purpose

The OpenEnv integration layer exists to make AegisForge interoperate cleanly with environment-driven evaluation setups without turning the main repo into an OpenEnv fork.

## Principles

- Keep OpenEnv-specific behavior behind an adapter boundary
- Avoid importing OpenEnv structural assumptions into the base runtime
- Preserve the same public runtime contract: `/health` and Agent Card
- Document clearly what is native behavior and what is integration-specific

## Expected structure

A minimal OpenEnv adapter should expose:

- `adapter.py`
- `config.py`
- `README.md`

## What belongs here

- small wrappers
- config translation
- environment-facing helper functions
- notes for reproducible local testing

## What does not belong here

- full upstream OpenEnv source trees
- large environment dumps
- unrelated examples copied wholesale from external repos

## Recommended workflow

1. keep AegisForge core stable
2. add a thin OpenEnv adapter
3. document required config
4. test adapter behavior in isolation
5. keep the public runtime path clean
