# Training / Evaluation

Esta carpeta agrupa los artefactos públicos de rollout, evaluación y demostración para `omnibench_aegis_env`, el entorno OpenEnv principal de `AegisForge_agent`.

Su propósito no es reemplazar toda la infraestructura interna del proyecto, sino ofrecer una base **clara, reproducible y revisable** para que cualquier persona pueda:

- conectar un agente simple o un stub de LLM,
- correr episodios sobre el entorno,
- ejecutar evaluación básica por dominio,
- y entender el flujo mínimo de uso mediante un notebook tutorial.

---

## Objetivo

Este directorio existe para cubrir la parte pública de **training / evaluation artifacts** de la submission.

Aquí se concentra una ruta mínima para:

1. levantar el entorno;
2. ejecutar rollouts reproducibles;
3. correr evaluación agregada;
4. inspeccionar resultados;
5. mostrar un tutorial ejecutable y fácil de revisar.

---

## Contenido de la carpeta

```text
training/
  README.md
  run_eval.py
  run_rollout.py
  llm_agent_stub.py
  notebooks/
    OpenEnv_OmniBench_Aegis_Tutorial.ipynb
```

### Descripción de archivos

#### `run_rollout.py`
Ejecuta episodios paso a paso contra el entorno y registra información útil por corrida, como observaciones, acciones, rewards y estado final.

#### `run_eval.py`
Lanza una evaluación básica sobre uno o varios dominios y resume resultados agregados para comparación y revisión.

#### `llm_agent_stub.py`
Stub mínimo de agente compatible con el contrato del entorno. Sirve como punto de partida para integrar un LLM favorito o una política más compleja.

#### `notebooks/OpenEnv_OmniBench_Aegis_Tutorial.ipynb`
Notebook tutorial con una demostración reproducible del flujo básico:
- conexión al servidor,
- reset de sesión,
- rollout corto,
- lectura de estado,
- interpretación de reward y resultado final.

---

## Requisitos previos

Desde la raíz del repo:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
```

Asegura dependencias del proyecto en tu entorno habitual.

---

## Flujo recomendado

### 1) Levantar el servidor del entorno

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python -m uvicorn integrations.openenv.envs.omnibench_aegis_env.server.app:app --host 127.0.0.1 --port 8001
```

### 2) Ejecutar un rollout básico

En otra terminal:

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python integrations/openenv/envs/omnibench_aegis_env/training/run_rollout.py --base-url http://127.0.0.1:8001 --json
```

### 3) Ejecutar una evaluación básica

```powershell
cd C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent
python integrations/openenv/envs/omnibench_aegis_env/training/run_eval.py --base-url http://127.0.0.1:8001 --json
```

### 4) Abrir el notebook tutorial

Abre:

```text
integrations/openenv/envs/omnibench_aegis_env/training/notebooks/OpenEnv_OmniBench_Aegis_Tutorial.ipynb
```

Ese notebook está pensado como demostración pública del contrato del entorno y del flujo mínimo de interacción.

---

## Qué debe registrar una evaluación útil

Una corrida o evaluación pública debería dejar, como mínimo:

- dominio o familia de tarea,
- misión o task category,
- número de pasos ejecutados,
- reward acumulado,
- éxito o fallo,
- terminal reason,
- observaciones clave,
- penalizaciones o violaciones, si existen.

Esto ayuda a mantener trazabilidad y a diferenciar entre:
- fallos del agente,
- fallos de integración,
- fallos de contrato,
- y rutas exitosas limpias.

---

## Filosofía de esta carpeta

La idea de estos artifacts no es “esconder complejidad”, sino exponer una versión mínima y útil del flujo de evaluación:

- `run_rollout.py` muestra la interacción episodio por episodio;
- `run_eval.py` resume desempeño agregado;
- `llm_agent_stub.py` simplifica pruebas iniciales;
- el notebook documenta el uso de forma accesible para revisores.

En conjunto, esta carpeta permite revisar el entorno como una pieza reproducible y no solo como una demo aislada.

---

## Buenas prácticas

- Mantener trazas legibles y comparables.
- Separar rollout detallado de evaluación agregada.
- No ocultar errores de conexión o contrato.
- Usar seeds reproducibles cuando aplique.
- Mantener la salida suficientemente clara para revisión pública.

---

## Relación con el resto del proyecto

Esta carpeta forma parte de:

```text
integrations/openenv/envs/omnibench_aegis_env/
```

Para contexto adicional del entorno, ver también:

- `../README.md`
- `../openenv.yaml`
- `../server/app.py`
- `../client.py`

Y para el runtime del agente:

- `src/aegisforge/agent.py`
- `src/aegisforge/runner.py`
- `src/aegisforge/a2a_server.py`

---

## Alcance

Estos artifacts representan la ruta pública mínima de entrenamiento/evaluación para la submission.  
No intentan cubrir toda la experimentación interna del proyecto, sino dejar una base:

- corrible,
- entendible,
- reproducible,
- y fácil de revisar por terceros.

---

## Estado actual

La carpeta `training/` ya ofrece una base funcional para:

- conectar el entorno con un agente simple,
- correr rollouts,
- ejecutar evaluación,
- y demostrar el flujo técnico del entorno mediante notebook y scripts públicos.
