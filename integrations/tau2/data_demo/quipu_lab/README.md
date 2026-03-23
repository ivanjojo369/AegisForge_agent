# quipu_lab demo data for τ² integration

This folder contains **minimal reproducible demo artifacts** for the `quipu_lab` τ²-style integration.

## Purpose

These files are not meant to replace a full benchmark dataset.  
They exist to provide:

- a compact task example
- a compact trace example
- a compact run-summary example

This helps the AegisForge repo demonstrate that the τ²-style capability is implemented as a coherent Purple-facing feature.

## Files

- `sample_task.json`  
  Minimal example of a τ²-style task input.

- `sample_trace.json`  
  Minimal example of an execution trace.

- `sample_run.json`  
  Minimal example of a summarized run/result artifact.

## Why demo artifacts matter

For a competition repository, empty integration folders feel incomplete.  
These examples make the repository more credible by showing:

- the expected structure of an input task
- the shape of a trace produced by a run
- the shape of the final result summary

## Expected usage

These files can be used by:

- smoke tests
- adapter examples
- docs screenshots
- report generation demos
- future CI sanity checks

## Notes

The examples should stay:

- small
- explicit
- easy to inspect
- safe to publish

They should illustrate the interface and artifact shape without pretending to be a full hidden evaluation set.