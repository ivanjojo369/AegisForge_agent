# Fair Play

AegisForge is intended to follow a clean and competition-safe operating model.

## Commitments

### No hardcoded answers
The runtime should not embed benchmark-specific answers, scenario-specific lookup tables, or hidden response maps.

### No benchmark exploitation
AegisForge should not depend on platform bugs, evaluator quirks, or undocumented shortcuts.

### Genuine reasoning path
Behavior should flow through the agent and executor pipeline, not through precomputed task outputs.

### Fresh-state runs
Each run should begin in a clean state. Leftover outputs, result caches, or hidden mutable state should not influence new evaluations.

### Transparent integration boundary
Optional adapters for OpenEnv, τ²-Bench, or Security Arena should remain explicit. The core runtime should not pretend to support behavior that is only available through an external track-specific setup.

### License-aware reuse
Third-party code, snippets, and templates should be used in ways compatible with their licenses and with clear separation from original AegisForge code.

## What this means in practice

- Keep scenario examples in `examples/`, not embedded into runtime logic
- Keep track-specific artifacts in adapter layers
- Validate public endpoints with reproducible scripts
- Record provenance for release and submission steps
- Make documentation match the actual runtime behavior
