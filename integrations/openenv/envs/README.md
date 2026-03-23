# AegisForge OpenEnv Demo Env

Entorno OpenEnv mínimo para validar una integración local y contenedorizable dentro de `AegisForge_agent`.

## Objetivo

Este entorno existe para probar el flujo base de OpenEnv dentro del repo:

- levantar un entorno aislado por HTTP,
- exponer endpoints de salud y de interacción,
- soportar `reset()`, `step()` y `state()`,
- servir como base para adapter, harness, fixtures y tests.

## Estructura

```text
demo_env/
  requirements.txt
  openenv.yaml
  models.py
  client.py
  README.md
  server/
    Dockerfile
    app.py
```

## Endpoints

- `GET /`
- `GET /health`
- `POST /reset`
- `POST /step`
- `GET /state`

## Lógica del entorno

Este `demo_env` usa una dinámica mínima de episodio:

- `max_steps = 5`
- `target_score = 3`
- acción `advance` suma score según `value`
- acción `hold` no cambia score
- acción `finish` termina el episodio

El episodio también termina automáticamente si:

- se alcanza `target_score`, o
- se llega a `max_steps`

## Build Docker

Desde la raíz del repo:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent

docker build -f integrations/openenv/envs/demo_env/server/Dockerfile -t aegisforge-openenv-demo:local integrations/openenv/envs/demo_env
```

## Run Docker

```powershell
docker run --rm -p 8011:8011 aegisforge-openenv-demo:local
```

## Pruebas manuales

### Health

```powershell
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8011/health
```

### Reset

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8011/reset -ContentType "application/json" -Body "{}"
```

### Step

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8011/step -ContentType "application/json" -Body '{"action":"advance","value":1}'
```

### State

```powershell
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8011/state
```

## Ejemplo de flujo esperado

1. `POST /reset`
2. `POST /step` con `{"action":"advance","value":1}`
3. `POST /step` con `{"action":"advance","value":2}`
4. `GET /state`

Resultado esperado:

- `score >= 3`
- `done = true`
- `success = true`

## Cliente local

El archivo `client.py` expone:

- `DemoEnvClient`
- `AsyncDemoEnvClient`

con métodos:

- `health()`
- `reset()`
- `step()`
- `state()`

## Rol dentro de AegisForge

Este entorno no es todavía el submission final del reto OpenEnv. Su función actual es servir como:

- entorno demo reproducible,
- base de integración para `src/aegisforge/adapters/openenv/`,
- objetivo de smoke tests,
- soporte para scripts `run / prepare / validate`.

## Próximos pasos

Los siguientes componentes recomendados son:

- `tests/test_adapters/test_openenv_adapter.py`
- `tests/tests_envs/test_openenv_demo_env_smoke.py`
- `tests/fixtures/openenv/demo_env/...`
- `harness/AegisForge_scenarios/data/openenv/demo_env/...`
- scripts `run_openenv_purple_eval.ps1`, `prepare_openenv_purple_submission.ps1`, `validate_openenv_purple_submission.ps1`
