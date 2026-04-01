# AegisForge Security Arena Harness

This folder contains local harness configuration for **Lambda × Berkeley AgentBeats Security Arena** style runs using the AegisForge runtime.

The purpose of this directory is to provide:

- a local scenario configuration shape for **Security Arena** runs
- a clean place to test **attacker** and **defender** modes
- a reproducible structure for smoke checks and pre-submission validation
- a bridge between the AegisForge runtime and Security Arena-specific metadata such as:
  - `assessment_mode`
  - `scenario_family`
  - `target_system`
  - `protected_asset`
  - `attack_surface`
  - `sensitive_asset`

## Scope

This harness directory is for **local validation and iteration**.

It is **not** the official Phase 2 submission format by itself. Instead, it helps validate that AegisForge behaves coherently in Security Arena-style attacker/defender runs before packaging, submission, or benchmark-facing integration.

## Supported modes

The harness is designed around two primary evaluation modes:

- `attacker`
- `defender`

These map directly to the Security Arena Phase 2 structure, where teams compete with offensive or defensive agents over selected scenarios.

## Scenario families

The example configuration is designed to support Security Arena-style families such as:

- `prompt_injection`
- `indirect_injection`
- `jailbreak`
- `prompt_extraction`
- `data_exfiltration`
- `pii_leakage`
- `secret_leakage`
- `insecure_output`
- `xss`
- `malicious_link`
- `dangerous_code`
- `supply_chain`
- `dependency_attack`

## Runtime assumptions

This harness assumes the AegisForge runtime is served through the local A2A app and that the runtime defaults are aligned with:

- host: `0.0.0.0`
- port: `8001`
- health endpoint: `/health`
- Agent Card: `/.well-known/agent-card.json`

## How to use

1. Copy `scenario.example.toml` to a real scenario file, for example:

   - `scenario.attacker.toml`
   - `scenario.defender.toml`
   - `scenario.prompt_injection.toml`

2. Adjust:
   - `assessment_mode`
   - `scenario_family`
   - target metadata
   - runtime URL/port if needed

3. Run your local eval or harness script against that scenario config.

## Suggested local workflow

Typical iteration loop:

1. start the AegisForge runtime
2. verify `/health`
3. verify `/.well-known/agent-card.json`
4. run a local Security Arena harness config in attacker mode
5. run the same or a paired config in defender mode
6. inspect traces / artifacts / self-check behavior
7. refine policies, routing, planning, and prompts

## Notes

- Keep scenarios **scenario-grounded** and benchmark-relevant.
- Avoid turning this folder into a generic placeholder area.
- Prefer one file per concrete local scenario once you begin active testing.
- If the runtime or track metadata changes, update this folder so it stays aligned with the real execution path.
