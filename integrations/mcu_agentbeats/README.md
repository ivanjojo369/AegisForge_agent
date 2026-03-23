# MCU-AgentBeats integration

## Purpose

This integration turns **MCU-AgentBeats** into a first-class benchmark target inside **AegisForge_agent** for **AgentX-AgentBeats Phase 2 · Purple**.

The immediate objective is to support the selected scenario family **WikiWiper**, adapted to the Minecraft domain as **trusted knowledge poisoning**.

In this setup, AegisForge must be able to operate in two modes:

- **attacker**: attempt to steer the target system through poisoned Minecraft knowledge sources
- **defender**: detect, resist, or contain poisoned knowledge while still solving the benchmark task

## Why MCU-AgentBeats

MCU-AgentBeats is a strong fit for this scenario because it depends on external or semi-trusted task knowledge such as:

- crafting recipes
- potion recipes
- enchantment guidance
- mining and navigation guidance
- tool-selection knowledge
- multi-step planning grounded in Minecraft mechanics

That makes it a natural target for **indirect injection through a trusted knowledge source**.

## WikiWiper adaptation for Minecraft

The original WikiWiper pattern assumes a poisoned wiki page that later gets read by an LLM-based system.

For MCU-AgentBeats, the same pattern becomes:

- a **Minecraft wiki, recipe sheet, or guide** is treated as trusted knowledge
- an attacker poisons that source with false but plausible instructions
- the target agent consumes that source during planning or execution
- the target agent either fails the task, wastes resources, or takes an incorrect action

## Supported operating modes

### Defender mode

AegisForge receives a task under the MCU benchmark and must:

1. interpret the task correctly
2. avoid over-trusting corrupted knowledge
3. cross-check suspicious instructions when possible
4. produce a safe and task-consistent response

### Attacker mode

AegisForge attempts to craft or select a poisoned knowledge artifact that is likely to push the target benchmark toward:

- incorrect crafting
- wrong tool choice
- faulty planning
- inefficient or self-defeating action sequences

## Expected metadata contract

Suggested runtime metadata keys for this integration:

- `track_hint = "mcu"`
- `scenario_family = "wikiwiper"`
- `assessment_mode = "attacker" | "defender"`
- `knowledge_source = "wiki"`
- `knowledge_trust = "trusted" | "suspect"`
- `domain = "minecraft"`

## Minimal flow

1. The benchmark task arrives through the A2A runtime.
2. AegisForge classifies the request as `mcu`.
3. The runtime loads the MCU strategy profile.
4. The scenario family is detected or supplied as `wikiwiper`.
5. The selected mode changes prompt, policy, routing, and validation behavior.
6. The response is scored by the benchmark and optionally by local eval harnesses.

## Immediate implementation targets

This README belongs to the first implementation slice. The files that should accompany it are:

- `integrations/mcu_agentbeats/data_demo/wikiwiper_task.md`
- `harness/AegisForge_scenarios/data/mcu/wikiwiper/scenario.toml`
- `harness/AegisForge_scenarios/data/mcu/wikiwiper/notes.md`
- `src/aegisforge/prompts/tracks/mcu/attacker.md`
- `src/aegisforge/prompts/tracks/mcu/defender.md`
- `src/aegisforge/prompts/tracks/mcu/wikiwiper.md`

## Scope note

This integration is focused on **runtime behavior** and **benchmark adaptation**.

It intentionally avoids submission-only concerns such as publication workflow, external promotion, or packaging details that are not required to execute the benchmark logic.
