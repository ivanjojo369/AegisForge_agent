# Harness Data for OmniBench Aegis Env

Esta carpeta contiene los artefactos mínimos para validar y demostrar el entorno `omnibench_aegis_env` desde el lado de harness.

## Propósito

Separar **datos de prueba** de la lógica del entorno. Aquí no va el servidor ni la mecánica interna de los dominios. Aquí van seeds, contratos esperados y acciones de ejemplo.

## Archivos esperados

```text
env_seed.json
expected_reset_min.json
expected_step_min.json
expected_state_min.json
mission_mix.json
sample_actions_coding.json
sample_actions_finance.json
sample_actions_research.json
sample_actions_web.json
sample_actions_agent_safety.json
README.md
```

## Descripción rápida

### `env_seed.json`
Contiene una semilla base del entorno y valores por defecto para arrancar sesiones reproducibles.

### `expected_reset_min.json`
Define la forma mínima esperada de la respuesta de `/reset`.

### `expected_step_min.json`
Define la forma mínima esperada de la respuesta de `/step`.

### `expected_state_min.json`
Define la forma mínima esperada de la respuesta de `/state`.

### `mission_mix.json`
Permite mezclar dominios o familias de misión con pesos relativos para demos, smoke tests o generación de payloads.

### `sample_actions_*.json`
Cada archivo contiene una o más secuencias de acciones válidas para un dominio concreto. Sirven para:

- smoke tests
- demos reproducibles
- pruebas rápidas del server
- depuración básica del shaping

## Regla práctica

- **Contratos mínimos** = forma estructural esperada
- **Seeds** = reproducibilidad
- **Sample actions** = trayectorias mínimas de prueba
- **Mission mix** = distribución configurable de episodios

## Flujo sugerido de uso

1. Cargar `env_seed.json`
2. Hacer `POST /reset`
3. Comparar contra `expected_reset_min.json`
4. Aplicar acciones desde `sample_actions_<domain>.json`
5. Comparar respuestas con `expected_step_min.json`
6. Consultar `GET /state`
7. Comparar con `expected_state_min.json`

## Buenas prácticas

- No mezclar datos temporales o logs aquí
- Mantener nombres estables y explícitos
- Evitar acciones demasiado complejas para smoke tests
- Documentar cualquier cambio de contrato
