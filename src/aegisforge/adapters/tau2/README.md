# tau2 Adapter

This directory contains the AegisForge adapter layer for OpenEnv-oriented integrations.

## Purpose

The adapter isolates OpenEnv-specific behavior from the core AegisForge runtime. Its job is to translate between the stable AegisForge execution model and any OpenEnv-facing conventions, payloads, or environment-specific hooks that may be needed for local development, demos, or track-oriented extensions.

The adapter should remain optional. The public runtime contract of AegisForge is still centered on:

- `GET /health`
- `GET /.well-known/agent-card.json`

## Files

- `__init__.py` — package marker and public exports
- `adapter.py` — main adapter implementation
- `config.py` — adapter-specific configuration model and defaults
- `README.md` — local documentation for this adapter

## Design rules

### Keep the boundary narrow
The adapter should expose a small, explicit interface and avoid leaking third-party assumptions into the core runtime.

### Do not override the core contract
The adapter must not break or redefine the behavior of the main AegisForge runtime, especially health checks, Agent Card generation, or baseline execution flow.

### Keep track logic isolated
OpenEnv-specific logic belongs here, not in the generic runtime modules unless it is truly shared infrastructure.

### Stay honest in documentation
If a feature depends on OpenEnv-specific setup, say so clearly. The adapter should not make the core runtime appear to support behavior that only exists under a separate track configuration.

## Expected responsibilities

Typical responsibilities for this adapter may include:

- mapping AegisForge requests into OpenEnv-compatible calls
- validating adapter configuration
- translating environment-facing responses into runtime-safe outputs
- offering small helper utilities for demos or track-specific runs

## Configuration

Configuration should be loaded through `config.py` and kept minimal. Feature flags and endpoints should come from environment variables or explicit runtime configuration, not hidden constants.

Examples of good adapter configuration values:

- environment name
- endpoint URL
- timeout values
- feature enable/disable flags

Examples of values that should **not** be hardcoded here:

- benchmark answer keys
- hidden scenario solutions
- evaluator-specific shortcuts
- private credentials

## Testing

This adapter should be covered by focused unit tests in:

- `tests/test_adapters/test_openenv_adapter.py`

Tests should validate:

- configuration loading
- adapter initialization
- success-path translation behavior
- clean failure handling

## Integration stance

This adapter is an extension point, not the identity of the repository. AegisForge remains a clean A2A purple-agent runtime first, with OpenEnv support layered on top in a controlled way.

## Maintenance notes

When updating this adapter:

1. keep changes local to this directory whenever possible
2. update matching tests
3. update integration docs if behavior changes
4. avoid importing heavyweight third-party code into the main runtime path unless it is strictly necessary
