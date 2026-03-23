# Adapter Template

This template README is intended for AegisForge adapters.

## Purpose

Describe what this adapter does, what it depends on, and how it interacts with the base runtime.

## Files

- `adapter.py` — runtime-facing adapter implementation
- `config.py` — adapter-specific configuration helpers
- `adapter_config.template.toml` — example configuration
- `Dockerfile.template` — optional adapter service container template

## Notes

- Keep the adapter narrow in scope
- Avoid leaking third-party repo structure into the main runtime
- Document required environment variables and assumptions clearly
