# AegisArena Env Data

Este directorio contiene la data base de `aegisarena_env`.

## Objetivo

Separar la lógica del entorno de los datos de misión para evitar hardcoding excesivo y permitir variación reproducible por seed.

## Subdirectorios esperados

```text
data/
  game_ops/
    mission_templates.json
    tool_specs.json
    heldout_templates.json
  finance_ops/
    mission_templates.json
    tool_specs.json
    heldout_templates.json
  business_ops/
    mission_templates.json
    tool_specs.json
    heldout_templates.json
```

## Qué debe haber en cada dominio

### `mission_templates.json`

Plantillas principales de episodios entrenables.

Deben incluir, según el dominio:
- resumen de misión,
- contexto observable inicial,
- verdad interna del episodio,
- herramientas disponibles,
- criterio de éxito,
- criterio de fallo,
- budget base,
- máximo de pasos.

### `tool_specs.json`

Definición de herramientas y acciones disponibles en ese dominio.

Puede incluir:
- nombre,
- descripción,
- payload esperado,
- costo,
- efectos laterales,
- restricciones.

### `heldout_templates.json`

Casos reservados para validación más dura.

Su propósito es:
- evitar sobreajuste,
- simular tareas no vistas,
- probar generalidad real,
- endurecer evaluación interna.

## Reglas de diseño

- Evitar IDs o nombres demasiado memorables si eso facilita hardcoding.
- Introducir variación por seed en orden, contexto y distractores.
- Mantener formato consistente entre dominios.
- No mezclar lógica del servidor con data estática.

## Relación con la evaluación

Estos archivos alimentan:
- el generador de episodios,
- el sampler por seed,
- la mezcla de misiones del Sprint 1,
- los held-outs internos,
- y la futura evaluación con solver LLM.
