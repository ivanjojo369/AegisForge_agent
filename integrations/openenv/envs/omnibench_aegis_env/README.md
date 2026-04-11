---
title: OmniBench Aegis Env
emoji: ⚔️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 8000
base_path: /web
pinned: false
short_description: Reproducible multi-domain OpenEnv benchmark env.
---
# OmniBench Aegis Env

`omnibench_aegis_env` es el entorno OpenEnv principal para `AegisForge_agent`. Su objetivo es exponer una interfaz reproducible, evaluable y multi-dominio para tareas agentic inspiradas en benchmarks reales, sin mezclar la lógica del agente con la del entorno.

Este proyecto se construyó para demostrar tres cosas al mismo tiempo:

1. un **entorno OpenEnv** sólido y verificable;
2. una **infraestructura de evaluación** que permita comparar baseline vs candidate de forma limpia; y
3. una **capa de integración con AegisForge** que mantenga separadas la planificación, la seguridad, el routing y la ejecución.

---

## Submission Snapshot

`omnibench_aegis_env` integra dominios multi-agent, computer use, finance, research, game, business process y τ²-style service tasks bajo un contrato HTTP uniforme. El entorno se ejecuta mediante un servidor FastAPI con sesiones controladas por `state_engine`, clientes HTTP ligeros, scripts de rollout/eval, generación de payloads canónicos, curriculum, transfer y taxonomía de fallas. En la validación final, el candidate principal alcanzó **7/7 pass**, **score_mean = 1.0**, con mejora específica en los dominios que antes estaban flojos: `business_process`, `multi_agent` y `tau2`.

---

## Problema y motivación

Muchos repos agentic mezclan en una sola capa:

- lógica del agente,
- estado del entorno,
- reglas de recompensa,
- evaluación,
- y smoke tests.

Eso vuelve difícil saber si una mejora real proviene del agente, del entorno o del harness.

La motivación de `omnibench_aegis_env` es separar esas piezas con claridad:

- **el entorno** administra estado, observación, acciones y reward;
- **el agente** decide estrategia, políticas y routing;
- **los scripts de entrenamiento/evaluación** validan el contrato y registran resultados;
- **los artifacts de curriculum/transfer** permiten endurecer escenarios sin romper reproducibilidad.

El resultado buscado no es un “demo bonito” únicamente, sino una base que sirva para iterar, comparar y depurar sin perder trazabilidad.

---

## Qué construye este entorno

El entorno ofrece:

- dominios modulares;
- sesiones controladas por `state_engine.py`;
- recompensas auditables vía `reward.py`;
- API OpenEnv por `server/app.py`;
- payloads reproducibles generados desde fixtures;
- evaluación y rollouts con summaries y archivos por escenario;
- taxonomía agregada de fallas;
- infraestructura de curriculum, transfer y matrices de variantes.

---

## Dominios y escenarios ancla

La validación principal de esta línea de trabajo se concentró en estos siete escenarios:

- `research` → `InventoryInject`
- `computer_use` → `LinkLifter`
- `finance` → `taxwiztrap`
- `multi_agent` → `BidBot`
- `tau2` → `TicketTwister`
- `game` → `wikiwiper`
- `business_process` → `saleforceone`

Además, la arquitectura deja espacio para dominios y familias relacionadas como `coding`, `healthcare`, `web`, `agent_safety`, `officeqa`, `crmarena`, `fieldwork` y otras extensiones futuras.

---

## Arquitectura general

```text
AegisForge_agent/
  integrations/openenv/envs/omnibench_aegis_env/
    client.py
    openenv.yaml
    reward.py
    state_engine.py
    server/
      app.py
    domains/
      research.py
      computer_use.py
      finance.py
      multi_agent.py
      tau2.py
      game.py
      business_process.py
    scripts/
      build_sample_payloads.py
      build_curriculum_payloads.py
      generate_variant_matrix.py
    training/
      run_rollout.py
      run_eval.py
      compare_eval_runs.py
      aggregate_failure_taxonomy.py
      llm_agent_stub.py
  src/aegisforge/
    agent.py
    a2a_server.py
    runner.py
```

### Separación de responsabilidades

- **`server/app.py`**: expone el contrato HTTP del entorno.
- **`state_engine.py`**: administra sesiones, estado y routing al dominio correcto.
- **`reward.py`**: descompone shaping, bonus y penalizaciones.
- **`client.py`**: cliente HTTP liviano alineado al contrato actual.
- **`run_rollout.py` / `run_eval.py`**: ejecutan episodios y validaciones reproducibles.
- **`compare_eval_runs.py`**: compara baseline vs candidate.
- **`aggregate_failure_taxonomy.py`**: resume terminal reasons, failure modes y clases de error.
- **`src/aegisforge/a2a_server.py` + `runner.py`**: exponen el runtime A2A/agent card del sistema.

---

## Contrato del entorno

El servidor del entorno expone el contrato mínimo práctico de OpenEnv utilizado por esta integración:

- `GET /health`
- `GET /contract`
- `POST /reset`
- `POST /step`
- `GET /state`
- `GET /actions`

Esto permite que el checker, los scripts de rollout/eval y los clientes de integración trabajen con el mismo contrato observable.

---

## Filosofía de diseño

1. **El entorno no reemplaza al agente.**  
   `AegisForge_agent` conserva clasificación, routing, presupuestos, políticas y self-check.

2. **El entorno debe ser reproducible.**  
   Seeds, fixtures, payloads y summaries deben poder regenerarse de forma consistente.

3. **La recompensa debe ser auditable.**  
   El reward se descompone para facilitar depuración y trazabilidad.

4. **El estado debe ser observable.**  
   El sistema permite inspeccionar progreso, terminal reasons, success path y metadata por episodio.

5. **La evaluación debe ser comparable.**  
   No basta con “pasó”; se necesita comparar baseline vs candidate y ubicar exactamente qué mejoró.

---

## Resultados técnicos finales

### Baseline vs candidate final

En la comparación final de esta fase:

- **baseline**: `score_mean = 0.9143`
- **candidate final**: `score_mean = 1.0`
- **passes**: `7/7`
- **warnings**: `0`
- **fails**: `0`

Las mejoras medibles ocurrieron exactamente en:

- `business_process / saleforceone` → `0.8 -> 1.0`
- `multi_agent / BidBot` → `0.8 -> 1.0`
- `tau2 / TicketTwister` → `0.8 -> 1.0`

Los otros cuatro dominios se mantuvieron estables y correctos.

### Taxonomía final

La taxonomía agregada del candidate final quedó limpia:

- `failure_modes: none`
- `error_classes: none`
- terminal reasons limpias en todos los casos

Incluyendo:

- `clean_success`
- `clean_navigation_success`
- `clean_tax_submission`
- `clean_bidbot_submission`
- `clean_tickettwister_submission`
- `clean_wikiwiper_submission`
- `clean_saleforceone_submission`

### Qué significó eso en la práctica

Esta fase no solo dejó “un entorno que corre”, sino un entorno con:

- **comparación real entre baseline y candidate**;
- **mejoras aisladas por dominio**;
- **corrección de rutas canónicas por escenario**;
- **cierre limpio de success path** en los siete escenarios ancla.

---

## Reproducible Demo

### 1) Levantar el servidor OpenEnv

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m uvicorn integrations.openenv.envs.omnibench_aegis_env.server.app:app --host 127.0.0.1 --port 8001
```

### 2) Generar payloads

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
$env:PYTHONPATH = "C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent;C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent\integrations\openenv\envs"
python integrations/openenv/envs/omnibench_aegis_env/scripts/build_sample_payloads.py --base-url http://127.0.0.1:8001 --json
```

### 3) Ejecutar rollout

```powershell
python integrations/openenv/envs/omnibench_aegis_env/training/run_rollout.py --base-url http://127.0.0.1:8001 --json
```

### 4) Ejecutar eval

```powershell
python integrations/openenv/envs/omnibench_aegis_env/training/run_eval.py --base-url http://127.0.0.1:8001 --json
```

### 5) Comparar baseline vs candidate

```powershell
python integrations/openenv/envs/omnibench_aegis_env/training/compare_eval_runs.py \
  --baseline-summary <baseline_eval_summary.json> \
  --candidate-summary <candidate_eval_summary.json> \
  --baseline-results-dir <baseline_eval_dir> \
  --candidate-results-dir <candidate_eval_dir> \
  --output <compare_output.json> \
  --json
```

---

## Curriculum, Transfer y Benchmarking

Para endurecer el entorno y no quedarnos en una sola trayectoria canónica, la fase B incorporó infraestructura específica para:

- **curriculum payloads** por nivel;
- **transfer payloads** `heldout_like`;
- **variant matrices** con distintas repeticiones;
- **compare baseline vs candidate**;
- **failure taxonomy** agregada.

### Curriculum

Se generaron payloads por niveles:

- `easy`
- `medium`
- `hard`
- `heldout_like`

Los artifacts incluyen `curriculum_level`, `curriculum_rank`, notas por nivel y metadata para cada payload.

### Transfer

Se generó un bloque separado de `heldout_like` para:

- `computer_use`
- `finance`
- `multi_agent`
- `tau2`
- `business_process`

Esto permitió evaluar generalización fuera del caso base inmediato sin romper el contrato del entorno.

### Benchmark infra

La infraestructura de benchmarking de esta fase incluye:

- summaries de rollout/eval;
- compare baseline vs candidate por escenario;
- matrices de variantes con repeticiones múltiples;
- taxonomía agregada de fallas y terminal reasons.

---

## Procedural Generation y Variant Infrastructure

La fase actual ya incorpora una forma útil de variación controlada mediante:

- `seed_variant`
- `difficulty`
- `curriculum_level`
- `max_steps`
- repeticiones múltiples por combinación

Esto no pretende venderse como una procedural generation máxima o totalmente abierta, sino como una **infraestructura de variantes útil y reproducible** que permite:

- mover el mismo escenario entre niveles;
- separar seeds base y heldout-like;
- aumentar horizonte;
- generar matrices con diferentes repeticiones.

En esta etapa, el valor principal no está en producir escenarios arbitrarios, sino en endurecer rutas ya validadas sin perder control del contrato ni comparabilidad de resultados.

---

## Green/Runtime Wrapper y Agent Card

Además del entorno OpenEnv, el proyecto expone una capa A2A/runnable mediante:

- `src/aegisforge/a2a_server.py`
- `src/aegisforge/runner.py`
- `src/aegisforge/agent.py`

Esta capa:

- sirve `/health`;
- publica `/.well-known/agent-card.json`;
- expone una agent card con streaming;
- declara capacidades y skills del sistema;
- integra el runtime del agente con adaptadores y rutas para `openenv`, `tau2`, `security`, `mcu`, `officeqa` y `crmarena`.

### Ejemplo de arranque del wrapper A2A

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m aegisforge.runner --mode serve --host 127.0.0.1 --port 8001
```

### Endpoints de verificación

```powershell
Invoke-WebRequest http://127.0.0.1:8001/health | Select-Object -ExpandProperty Content
Invoke-WebRequest http://127.0.0.1:8001/.well-known/agent-card.json | Select-Object -ExpandProperty Content
```

---

## Qué aprendimos en esta iteración

1. **Los payloads correctos importan tanto como el runner correcto.**  
   Parte del debugging crítico vino de descubrir cuándo el sistema estaba generando payloads buenos pero ejecutando directorios equivocados.

2. **`require_success` fue clave para separar “pass superficial” de éxito real.**  
   Eso ayudó a exponer dominios que parecían sanos pero seguían incompletos.

3. **Los sample actions por dominio deben estar alineados con la trayectoria semántica real.**  
   Esto fue especialmente importante para `finance`, `business_process`, `multi_agent` y `tau2`.

4. **La taxonomía de fallas aceleró la depuración.**  
   Ver terminal reasons y failure modes agregados fue más útil que mirar solo un pass/fail global.

5. **Separar baseline y candidate dentro de `phaseB` volvió la comparación mucho más limpia.**

---

## Por qué este entorno es interesante

Este proyecto no es solo otro servidor con endpoints de benchmark. Lo interesante es la combinación de:

- un contrato OpenEnv claro;
- dominios y escenarios ancla reproducibles;
- comparación real baseline vs candidate;
- curriculum/transfer/variant infrastructure;
- taxonomía de fallas;
- integración con un runtime A2A del agente.

Eso lo vuelve útil no solo para “pasar un checker”, sino para iterar en serio sobre:

- robustez de trayectorias,
- alineación entre payloads y entorno,
- cobertura de failure modes,
- y regresión controlada entre versiones.

---

## Repositorio: mapa práctico para jueces o revisores

### Entorno OpenEnv

- `integrations/openenv/envs/omnibench_aegis_env/openenv.yaml`
- `integrations/openenv/envs/omnibench_aegis_env/server/app.py`
- `integrations/openenv/envs/omnibench_aegis_env/client.py`
- `integrations/openenv/envs/omnibench_aegis_env/state_engine.py`
- `integrations/openenv/envs/omnibench_aegis_env/reward.py`

### Evaluación y entrenamiento

- `integrations/openenv/envs/omnibench_aegis_env/training/run_rollout.py`
- `integrations/openenv/envs/omnibench_aegis_env/training/run_eval.py`
- `integrations/openenv/envs/omnibench_aegis_env/training/compare_eval_runs.py`
- `integrations/openenv/envs/omnibench_aegis_env/training/aggregate_failure_taxonomy.py`
- `integrations/openenv/envs/omnibench_aegis_env/training/llm_agent_stub.py`

### Payload infrastructure

- `integrations/openenv/envs/omnibench_aegis_env/scripts/build_sample_payloads.py`
- `integrations/openenv/envs/omnibench_aegis_env/scripts/build_curriculum_payloads.py`
- `integrations/openenv/envs/omnibench_aegis_env/scripts/generate_variant_matrix.py`

### Wrapper/runtime

- `src/aegisforge/a2a_server.py`
- `src/aegisforge/runner.py`
- `src/aegisforge/agent.py`

### Resultados de Phase B

- `phaseB/baseline_*`
- `phaseB/candidate_*`
- `phaseB/compare_*.json`
- `phaseB/compare_latest_baseline_vs_candidate.json`

---

## Limitaciones actuales

- La parte de storytelling/submission todavía depende de cómo se presente externamente el proyecto.
- La procedural generation actual es útil y reproducible, pero aún puede ampliarse hacia variación semántica más fuerte.
- La profundidad multi-agent puede seguir creciendo con más escenarios y más métricas internas de interacción.

---

## Próximos pasos

1. expandir la narrativa de submission alrededor del porqué del entorno;
2. reforzar procedural generation con cambios semánticos más fuertes;
3. ampliar la cobertura multi-agent más allá de una sola familia canónica;
4. mantener la taxonomía y el compare como puertas de regresión obligatorias;
5. seguir usando `phaseB` como historial limpio de baseline/candidate.

---

## Estado actual

`omnibench_aegis_env` ya no está solo en fase de base operativa. En esta línea de trabajo quedó convertido en un entorno:

- **corrible**,
- **reproducible**,
- **comparable**,
- **instrumentado**,
- y **alineado con el runtime de AegisForge**.

La fase actual dejó una base técnica fuerte para presentación, iteración y endurecimiento futuro.
