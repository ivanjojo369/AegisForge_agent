# AegisArena Env Fixtures

Este directorio contiene fixtures mínimos para pruebas de `aegisarena_env`.

## Propósito

Permitir pruebas reproducibles de forma, payloads y contratos mínimos sin depender siempre de una corrida completa del entorno.

## Archivos esperados

```text
aegisarena_env/
  README.md
  reset_response_min.json
  step_response_min.json
  state_response_min.json
  sample_config.toml
  sample_track_payload.json
```

## Qué representa cada fixture

### `reset_response_min.json`

Forma mínima válida para una respuesta de `reset()`.

### `step_response_min.json`

Forma mínima válida para una respuesta de `step()`.

### `state_response_min.json`

Forma mínima válida para una respuesta de `state()`.

### `sample_config.toml`

Configuración de ejemplo para pruebas locales del adapter o del cliente.

### `sample_track_payload.json`

Payload de ejemplo para el track OpenEnv y futuras pruebas de solver/evaluación.

## Reglas

- Mantener fixtures pequeños.
- Priorizar estructura y contrato, no una simulación completa.
- Actualizar fixtures cuando cambie el esquema público del entorno.
- No usar fixtures como sustituto de pruebas end-to-end.

## Relación con tests

Estos fixtures deben ser útiles para:
- `test_openenv_aegisarena_env_smoke.py`
- tests de adapter OpenEnv
- futuras pruebas del solver LLM
- validación de payloads del track
