# Release Guide

This document describes a clean release flow for AegisForge.

## Release objectives

A release should produce:

- a tagged git state
- a buildable Docker image
- a validated public endpoint
- a matching Agent Card
- submission metadata with provenance

## Recommended flow

### 1. Clean the repo

Run the cleanup script and verify no local build noise remains:

- `.venv/`
- `__pycache__/`
- `*.egg-info`
- temporary logs
- leftover result artifacts

### 2. Run checks

Minimum checks:

- lint
- smoke
- `/health`
- Agent Card validation

### 3. Build image

Example:

```bash
docker build -t ghcr.io/your-namespace/aegisforge:latest .
```

### 4. Record provenance

Capture:

- git commit
- branch or tag
- image reference
- UTC timestamp
- public URL
- track label

### 5. Validate public endpoint

Confirm:

- `GET /health` returns status 200
- `GET /.well-known/agent-card.json` returns valid JSON
- public URL in the card matches deployment reality

### 6. Prepare submission metadata

Generate submission metadata and validate it against the local schema.

## Recommended tags

- `v0.1.0`
- `v0.1.1`
- `v0.2.0`

## Release checklist

- README updated
- ABSTRACT updated
- docs aligned with current behavior
- smoke checks pass
- provenance saved
- submission metadata generated
