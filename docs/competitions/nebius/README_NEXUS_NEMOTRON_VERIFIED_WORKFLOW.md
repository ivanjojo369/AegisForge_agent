# AegisForge Nexus — Plan Nemotron → ejecución controlada → verificación

## Archivos nuevos (no reemplazan módulos existentes)

**Programa:** `scripts/nexus/demo_nemotron_verified_workflow.py`  
**Pruebas:** `tests/test_nexus/test_demo_nemotron_verified_workflow.py`  
**Esta guía:** `docs/competitions/nebius/README_NEXUS_NEMOTRON_VERIFIED_WORKFLOW.md`

Ruta absoluta del programa en Windows:

```
C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent\scripts\nexus\demo_nemotron_verified_workflow.py
```

## Requisitos

- Los módulos Nexus existentes en `src/aegisforge/nexus/`.
- El programa anterior **`scripts/nexus/demo_verified_workflow.py`**. La nueva demostración reutiliza sus herramientas y pruebas estáticas. **No lo elimines**.
- Entorno Python de AegisForge con dependencias instaladas.
- Para `--live` solamente: una API Key válida de Nebius Token Factory, proporcionada por el titular autorizado.

## Instalación del paquete

En PowerShell, dentro del repositorio:

```powershell
cd "C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent"
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\AegisForge-Nexus-Nemotron-Verified-Bridge.zip" -DestinationPath .
$env:PYTHONPATH = Join-Path (Get-Location) "src"
Test-Path .\scripts\nexus\demo_nemotron_verified_workflow.py
Test-Path .\scripts\nexus\demo_verified_workflow.py
Test-Path .\tests\test_nexus\test_demo_nemotron_verified_workflow.py
```

Los tres `Test-Path` deben devolver `True`.

## 1. Vista previa local, SIN conexión ni costos

```powershell
python .\scripts\nexus\demo_nemotron_verified_workflow.py --offline
```

Obtendrás un plan sintético, metadatos y `status: review_only`. No se ejecutan herramientas.

## 2. Demostración local completa, SIN conexión ni costos

```powershell
python .\scripts\nexus\demo_nemotron_verified_workflow.py --offline --execute --save-evidence
```

Revisa el plan mostrado. Si estás de acuerdo, introduce **`EJECUTAR`**. Cualquier otro texto cancela la ejecución de herramientas. En las pruebas locales, el resultado fue `verification_status: verified`, dos herramientas y tres comprobaciones independientes.

## 3. Una prueba REAL de extremo a extremo con Nemotron

Cuando estés preparado, y con autorización del titular de la cuenta para cualquier cargo:

```powershell
python .\scripts\nexus\demo_nemotron_verified_workflow.py --live --execute --save-evidence
```

1. Introduce `SI` para autorizar **una sola** inferencia real. No hay reintentos automáticos.
2. Introduce la `NEBIUS_API_KEY` de forma oculta si no está ya configurada en el entorno. **Nunca la envíes al chat ni al repositorio.**
3. El programa solicita a NVIDIA Nemotron un plan de cuatro pasos.
4. El plan debe ser exactamente `reason → nexus.demo.write → nexus.demo.tests → verify`, con dependencias lineales. Si no coincide, **no se ejecuta ninguna herramienta**.
5. Revisa el plan en pantalla. Únicamente después introduce **`EJECUTAR`** para permitir las herramientas estáticas del anfitrión.
6. Nexus ejecuta dos herramientas locales, verifica los bytes por SHA-256, repite los seis tests fijos y exporta trazas cuando se indicó `--save-evidence`.

**Importante:** el modelo propone el *orden y los identificadores* de las operaciones autorizadas; no genera ni ejecuta código nuevo, no selecciona rutas, no proporciona argumentos a las herramientas y no dispone de consola. El directorio temporal no constituye un aislamiento de seguridad para código no confiable. Por tanto, esta demostración NO demuestra autonomía general de programación. Demuestra la integración real entre inferencia, revisión del plan, herramientas fijas y evidencia independiente.

Es posible que Nemotron produzca un plan estructuralmente válido pero incompatible con este flujo rígido. En ese caso la operación se bloquea tras la inferencia y **no hay un segundo intento automático ni herramientas ejecutadas**.

## Pruebas

```powershell
python -m unittest discover -s tests/test_nexus -p "test_demo_nemotron_verified_workflow.py" -v
python -m unittest discover -s tests/test_nexus -p "test_*.py" -v
```

La batería nueva contiene 16 pruebas. En el entorno local de preparación se ejecutaron **189/189** pruebas Nexus satisfactoriamente, sin hacer llamadas reales a Nebius en esa batería.

## Evidencias

Con `--save-evidence`, se crean nuevos archivos JSON y JSONL en:

```
C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent\evidence\nexus\
```

Nombres como `nemotron_verified_live_<ID>.json` y `nemotron_verified_live_<ID>.jsonl`. La traza JSONL está encadenada con SHA-256. El resumen no contiene la clave, las conversaciones completas ni los textos de los pasos del modelo; solo información limitada de auditoría. Antes de publicar cualquier archivo de evidencias, revísalo igualmente.

## Si falla

- `UnsafePlanError`: el plan no coincidió exactamente con el flujo aprobado; ninguna herramienta se ejecutó.
- `NebiusAPIError`: revisar clave, acceso al modelo y configuración oficial; no se hace reintento automático.
- `operator_declined`: no se autorizaron herramientas.
- `verification_status: failed` o `inconclusive`: conservar el resultado; no declarar el flujo como verificado.

Este script es adicional: **no reemplaza** `demo_verified_workflow.py`, `smoke_nebius_nemotron.py` ni los ocho módulos Nexus.
