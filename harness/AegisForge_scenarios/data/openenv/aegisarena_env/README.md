# AegisArena Env Harness Data

Este directorio contiene artefactos ligeros de harness para `aegisarena_env`.

## Propósito

Dar soporte a corridas reproducibles, smoke checks y validaciones mínimas del entorno sin depender de un benchmark externo.

## Archivos esperados

```text
aegisarena_env/
  README.md
  env_seed.json
  mission_mix.json
  sample_actions_game.json
  sample_actions_finance.json
  sample_actions_business.json
  expected_reset_min.json
  expected_step_min.json
  expected_state_min.json
```

## Descripción rápida

### `env_seed.json`

Seed base recomendada para corridas reproducibles del entorno.

### `mission_mix.json`

Distribución inicial de dominios activos del Sprint 1.

Ejemplo conceptual:
- `game_ops`
- `finance_ops`
- `business_ops`

### `sample_actions_game.json`

Secuencia mínima de acciones de ejemplo para una misión `game_ops`.

### `sample_actions_finance.json`

Secuencia mínima de acciones de ejemplo para una misión `finance_ops`.

### `sample_actions_business.json`

Secuencia mínima de acciones de ejemplo para una misión `business_ops`.

### `expected_reset_min.json`

Contrato mínimo esperado para la respuesta de `reset()`.

### `expected_step_min.json`

Contrato mínimo esperado para la respuesta de `step()`.

### `expected_state_min.json`

Contrato mínimo esperado para la respuesta de `state()`.

## Criterio de uso

Estos archivos no sustituyen la lógica del entorno. Solo documentan:
- forma mínima esperada,
- seeds base,
- mezclas iniciales,
- rutas ligeras de comprobación.

## Relación con scripts

Este directorio debe ser compatible con:
- `run_aegisarena_purple_eval.ps1`
- `prepare_openenv_purple_submission.ps1`
- `validate_openenv_purple_submission.ps1`
