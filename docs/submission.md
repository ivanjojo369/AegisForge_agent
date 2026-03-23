# Submission Workflow

This document is the **internal submission workflow guide** for AegisForge.

The canonical public-facing handoff document is the repository root file:

- `SUBMISSION.md`

Use this file to prepare, validate, and freeze a release before public registration.

---

## Purpose

This guide is intentionally shorter and more operational than `SUBMISSION.md`.

It exists to help the maintainer answer these questions before release:

- Is the repository clean?
- Does the local gate pass?
- Does the container boot correctly?
- Does the public endpoint respond correctly?
- Is the metadata ready for registration?

---

## Scope

This document covers the internal release workflow for the main Purple submission path:

`Dockerfile -> run.sh -> src/aegisforge/a2a_server.py -> /health -> /.well-known/agent-card.json`

Support layers such as `aegisforge_eval`, adapters, harnesses, demos, and tooling should strengthen the release, but they must not break the base runtime.

---

## Pre-release cleanup

Before running any submission checks, confirm the repository is clean.

### Remove or ignore local noise

Make sure these are not committed:

- `.venv/`
- `.ruff_cache/`
- `__pycache__/`
- `*.egg-info/`
- local logs
- temporary screenshots
- ad hoc result dumps
- machine-specific secrets or tokens

### Review untracked files

Every untracked file should be classified as one of:

- keep and commit
- move outside the repo
- delete

Do not publish a release while key files are still floating as ambiguous untracked content.

---

## Documentation sync

Before release, confirm these files are aligned with the actual runtime:

- `README.md`
- `SUBMISSION.md`
- `docs/ABSTRACT.md`
- `docs/architecture.md`
- `docs/quickstart.md`
- `docs/fair_play.md`
- `docs/release.md`

Check especially for:

- stale commands
- wrong paths
- overclaims about capabilities
- outdated endpoint names
- examples that no longer match current behavior

---

## Local verification order

Run the local checks in this order.

### 1. Lint

```bash
ruff check src tests scripts
```

### 2. Smoke tests

```bash
pytest tests/test_smoke -q
```

### 3. Core tests

```bash
pytest tests/test_core -q
```

### 4. Adapter tests

```bash
pytest tests/test_adapters -q
```

### 5. CLI and script tests

```bash
pytest tests/test_cli tests/test_scripts -q
```

### 6. A2A end-to-end check

Linux/macOS:

```bash
bash scripts/check_a2a_e2e.sh
```

Windows PowerShell:

```powershell
.\scripts\check_a2a_e2e.ps1
```

If any of these fail, stop and fix the runtime before moving to deployment.

---

## Container verification

Build and boot the image using the same path intended for submission.

### Build

```bash
docker build --platform linux/amd64 -t aegisforge-agent:latest .
```

### Run

```bash
docker run --rm -p 8000:8000 aegisforge-agent:latest
```

### Verify locally

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/.well-known/agent-card.json
```

Expected result:

- `/health` returns success JSON
- `/.well-known/agent-card.json` returns valid JSON metadata

---

## Public endpoint verification

Once deployed, validate the real public endpoint.

### Required checks

- deployed base URL is reachable
- `/health` returns success
- `/.well-known/agent-card.json` returns valid metadata
- Agent Card points to the correct public service
- no localhost URLs remain in the public metadata

### Verification script

```bash
python scripts/verify_public_endpoint.py
```

If the public endpoint fails verification, do not proceed to registration.

---

## Submission metadata workflow

Prepare metadata only after the runtime and public endpoint are stable.

### Required sources of truth

- deployed public URL
- agent card URL
- image reference
- git commit SHA
- UTC timestamp
- README path
- abstract path

### Supporting files

- `assets/submission_schema.json`
- `scripts/prepare_submission.py`
- `scripts/record_provenance.py`

Metadata must reflect the exact submitted state, not a tentative or older build.

---

## Fair play and release discipline

Before release, confirm:

- no hardcoded benchmark answer tables
- no misleading demo claims
- no hidden benchmark-specific shortcuts presented as general reasoning
- no committed secrets
- no undocumented external dependencies required for the runtime to boot

This repo should remain understandable, reproducible, and honest about what is and is not part of the actual submission.

---

## Final internal go / no-go checklist

Approve release only if all items below are true.

- [ ] repository is clean
- [ ] untracked files have been reviewed
- [ ] docs match current behavior
- [ ] lint passes
- [ ] smoke tests pass
- [ ] core tests pass
- [ ] adapter tests pass
- [ ] CLI and script tests pass
- [ ] A2A e2e checks pass
- [ ] Docker build succeeds
- [ ] local `/health` works
- [ ] local `/.well-known/agent-card.json` works
- [ ] public endpoint verification passes
- [ ] provenance is captured
- [ ] submission metadata is generated from the final state
- [ ] `SUBMISSION.md` is up to date

---

## Maintainer note

Use `SUBMISSION.md` when presenting the project to judges, reviewers, or the registration workflow.

Use this file when preparing the repo internally for a clean, reproducible release.
