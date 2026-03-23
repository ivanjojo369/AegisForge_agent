# Harness

This directory contains minimal scenario-facing evaluation scaffolding used to exercise AegisForge in a controlled and reproducible way.

## Purpose

The harness is not the product. The product is the AegisForge runtime in `src/aegisforge/`.

The harness exists to:

- hold lightweight local scenarios
- support reproducible demos
- provide evidence that the runtime behaves correctly
- avoid mixing benchmark-specific logic directly into the core runtime

## Guidelines

- Keep harness files small and explicit
- Avoid copying large upstream benchmark trees into this directory
- Prefer scenario examples and wrappers over full third-party framework dumps
- Keep track-specific logic outside the core server path
