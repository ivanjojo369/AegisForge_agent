# OpenEnv Environments in AegisForge_agent

Este directorio agrupa los entornos OpenEnv mantenidos dentro de `AegisForge_agent`.

## Propósito

Mantener entornos reproducibles, contenedorizables y evaluables localmente para fortalecer la capacidad OpenEnv-native del agente Purple unificado.

## Entornos actuales

### `demo_env`

Entorno mínimo de humo para validar integración base.

Se usa para:
- pruebas rápidas de `health / reset / step / state`,
- smoke tests,
- validación local de scripts `run / prepare / validate`,
- comprobación rápida de que la capa OpenEnv sigue viva.

No debe considerarse el entorno final fuerte del proyecto.

### `aegisarena_env`

Entorno principal de nueva generación para entrenamiento y evaluación interna de `AegisForge_agent` orientado a **AgentX–AgentBeats Phase 2**.

La versión inicial de `aegisarena_env` está especializada en el **1st Sprint**, con foco en:
- `game_ops`
- `finance_ops`
- `business_ops`

La arquitectura está diseñada para expandirse después a los sprints siguientes sin rehacer el entorno desde cero.

## Convención general

Cada entorno debería mantener, como mínimo, esta estructura:

```text
<env_name>/
  README.md
  requirements.txt
  openenv.yaml
  models.py
  client.py
  server/
    Dockerfile
    app.py
```

## Criterios de calidad esperados

Todos los entornos OpenEnv del repo deben procurar:
- reproducibilidad por seed,
- API estándar (`reset`, `step`, `state`),
- despliegue local por HTTP,
- build reproducible por Docker,
- smoke path local,
- tests y fixtures mínimos,
- compatibilidad con adapter, track y scripts de evaluación.

## Relación con AgentBeats Phase 2

El objetivo de estos entornos no es copiar benchmarks oficiales, sino construir una base interna que ayude a:
- mejorar generalidad,
- entrenar razonamiento multi-step,
- controlar costo/eficiencia,
- endurecer robustez ante tareas no vistas,
- y preparar a `AegisForge_agent` para desempeñarse mejor como Purple agent.
