# AegisArena Env

`aegisarena_env` es el entorno OpenEnv principal de `AegisForge_agent` para entrenamiento y evaluación interna orientada a **AgentX–AgentBeats Phase 2**.

## Objetivo

Construir un entorno serio, reproducible y extensible que entrene capacidades útiles para competir como Purple agent, priorizando primero los dominios del **1st Sprint**.

## Enfoque actual: Sprint 1

La primera versión fuerte del entorno se especializa en tres familias de misión:

- `game_ops`
- `finance_ops`
- `business_ops`

Esto sigue la lógica estratégica de la competencia: comenzar por los dominios que aparecen primero en la secuencia de sprints y después expandir el entorno hacia los siguientes grupos.

## Filosofía del entorno

`aegisarena_env` no debe funcionar como una colección de respuestas fijas ni como un lookup table. Debe favorecer:
- razonamiento genuino,
- adaptación a seeds distintas,
- robustez ante distractores,
- eficiencia de herramientas,
- y generalización a tareas no triviales.

## Tipos de misión

### `game_ops`

Misiones de razonamiento secuencial inspiradas en game agents.

Ejemplos de capacidades entrenadas:
- planificación paso a paso,
- manejo de recursos limitados,
- evaluación de trade-offs,
- toma de decisiones bajo información parcial.

### `finance_ops`

Misiones inspiradas en finance agents.

Ejemplos de capacidades entrenadas:
- lectura de tablas y snapshots,
- cálculo de métricas,
- validación de consistencia,
- respuesta precisa con costo bajo.

### `business_ops`

Misiones inspiradas en business process agents.

Ejemplos de capacidades entrenadas:
- triage de casos,
- selección de siguiente paso,
- cumplimiento de reglas de proceso,
- priorización de SLA, costo e impacto.

## API del entorno

El entorno expone los endpoints base de OpenEnv:

- `GET /health`
- `POST /reset`
- `POST /step`
- `GET /state`

## Observación y estado

La observación visible entrega contexto parcial del episodio, herramientas disponibles, historial reciente, budget restante y estado operativo.

El estado interno conserva información más rica, incluyendo:
- `mission_id`
- `mission_type`
- `hidden_truth`
- `score`
- `budget_remaining`
- `step_count`
- `success`
- `done`
- `failure_mode`
- `history`

## Acciones base

Las acciones comunes del entorno son:
- `inspect_context`
- `query_tool`
- `propose_plan`
- `take_action`
- `submit_final`

Cada dominio decide qué herramientas concretas están disponibles y cómo se calcula el reward.

## Recompensa

La recompensa debe alinearse con los objetivos del proyecto:
- exactitud,
- calidad del razonamiento,
- uso eficiente de herramientas,
- cumplimiento del objetivo,
- penalización por costo innecesario,
- penalización por rutas incorrectas o cierre prematuro.

## Estructura esperada

```text
aegisarena_env/
  README.md
  requirements.txt
  openenv.yaml
  models.py
  client.py
  config.py
  mission_registry.py
  reward.py
  state_engine.py
  seeds.py
  missions/
    __init__.py
    common.py
    game_ops.py
    finance_ops.py
    business_ops.py
  data/
    README.md
    game_ops/
    finance_ops/
    business_ops/
  server/
    Dockerfile
    app.py
```

## Build local

Desde la raíz del repo:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent

docker build -f integrations/openenv/envs/aegisarena_env/server/Dockerfile -t aegisforge-openenv-aegisarena:local integrations/openenv/envs/aegisarena_env
```

## Run local

Por script local dedicado:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
.\scripts\run_aegisarena_local_env.ps1
```

## Rol dentro de AegisForge

`aegisarena_env` debe convertirse en el entorno OpenEnv serio del repo, mientras `demo_env` permanece como entorno de humo y validación rápida.

## Evolución prevista

Después del Sprint 1, el entorno podrá extenderse con nuevos grupos de misión para sprints posteriores, sin cambiar su identidad general ni romper la API base.
