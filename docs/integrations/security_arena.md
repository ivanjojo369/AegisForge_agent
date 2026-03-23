# Security Arena Integration

This document explains how AegisForge relates to Security Arena-style evaluation.

## Purpose

The Security Arena integration layer helps AegisForge support attacker or defender oriented evaluation setups while keeping security-specific orchestration separate from the base A2A runtime.

## Core principles

- keep the base runtime small
- isolate security-specific logic behind adapters
- make supported behavior explicit
- avoid overclaiming track support in the public README

## Expected adapter surface

A minimal Security Arena adapter should include:

- `adapter.py`
- `config.py`
- `README.md`

## Good candidates for this layer

- role-specific configuration
- scenario-facing wrappers
- security track notes
- local validation helpers

## Things to avoid

- copying an entire upstream security benchmark tree
- mixing scenario internals into the core runtime
- turning AegisForge into a general-purpose security framework clone

## Practical rule

If a feature is only available through a Security Arena setup, document it as an integration capability, not as universal core behavior.
