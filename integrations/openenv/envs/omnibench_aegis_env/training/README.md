# Training / Evaluation

Esta carpeta agrupa los artefactos de evaluación y rollout para `omnibench_aegis_env`.

## Objetivo

Ofrecer una base simple para:

- evaluar agentes sobre el entorno
- correr episodios o rollouts
- conectar un stub de LLM
- documentar un tutorial reproducible

## Archivos esperados

```text
README.md
run_eval.py
run_rollout.py
llm_agent_stub.py
OpenEnv_OmniBench_Aegis_Tutorial.ipynb
```

## Descripción

### `run_eval.py`
Lanza una evaluación básica del agente sobre uno o varios dominios. Ideal para reportes resumidos.

### `run_rollout.py`
Corre episodios paso a paso y registra observaciones, acciones, rewards y estado final.

### `llm_agent_stub.py`
Stub mínimo de agente compatible con el entorno. Su propósito es facilitar integración rápida con un LLM favorito antes de usar una política más compleja.

### `OpenEnv_OmniBench_Aegis_Tutorial.ipynb`
Notebook tutorial con ejemplo de:
- conexión al server
- reset de sesión
- rollout corto
- lectura de estado
- interpretación del reward

## Flujo recomendado

1. Levantar el server del entorno
2. Probar conexión con el stub
3. Ejecutar `run_rollout.py`
4. Ejecutar `run_eval.py`
5. Abrir el notebook para demostración y documentación

## Qué debe registrar una evaluación útil

- dominio
- misión o task category
- número de pasos
- reward acumulado
- éxito / fallo
- observaciones clave
- violaciones o penalizaciones, si existen

## Buenas prácticas

- Mantener trazas legibles
- Separar rollout de evaluación agregada
- No esconder errores de conexión o contrato
- Usar seeds reproducibles cuando sea posible
