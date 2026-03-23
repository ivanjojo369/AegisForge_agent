# Security Adapter Template

This template is intended as a starting point for a security-oriented AegisForge
adapter configuration.

## What this template provides

- track-focused configuration defaults,
- conservative budget limits,
- security prompt profile selection,
- telemetry enabled by default.

## Expected integration points

- `src/aegisforge/adapters/security/adapter.py`
- `src/aegisforge/adapters/security/policy_bridge.py`
- `src/aegisforge/adapters/security/context_mapper.py`
- `src/aegisforge/strategy/role_policy.py`
- `src/aegisforge/strategy/artifact_policy.py`

## Recommended next steps

1. Align the adapter with the current strategy layer.
2. Emit trace events during execution.
3. Export scorecards after local evaluation.
4. Validate held-out behavior before packaging a submission.
