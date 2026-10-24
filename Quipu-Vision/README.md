# Quipu Vision 0.1 — primer módulo funcional

Motor visual **independiente**, consumible mediante el contrato de plugins existente de AegisForge.

## Qué hace

Compara imágenes de una cámara fija (referencia, observación y, opcionalmente, segunda observación) usando diferencias de píxeles, operaciones morfológicas y contornos de OpenCV. Emite un reporte estructurado con SHA256 de las entradas, cambios detectados, regiones y decisión sobre si repetir la observación. Puede guardar una imagen anotada.

**No** clasifica objetos, no identifica personas y **no** certifica que una zona sea segura. Las escenas deben compartir ángulo de cámara, tamaño y condiciones de luz. Los resultados requieren revisión humana. No conecta todavía el plugin al servidor A2A ni ejecuta nada en AWS; esos son los próximos pasos.

## Instalación en Windows

Desde `C:\Users\PC\Documents\AGI-Prototipo`:

```powershell
.\AegisForge_agent\.venv\Scripts\python.exe -m pip install -e "C:\Users\PC\Documents\Quipu-Vision"
.\AegisForge_agent\.venv\Scripts\python.exe -m pip check
.\AegisForge_agent\.venv\Scripts\python.exe -c "import cv2, quipu_vision; print('OpenCV',cv2.__version__)"
```

La versión de OpenCV requerida está fijada en `pyproject.toml`. Si previamente está instalado otro paquete que expone `cv2` (como la variante headless), revisar antes de instalar; mantener una sola distribución de OpenCV.

## Prueba con imágenes de un mismo ángulo

```powershell
.\AegisForge_agent\.venv\Scripts\python.exe -m quipu_vision --reference "C:\ruta\referencia.png" --observed "C:\ruta\observacion.png" --annotated "C:\ruta\resultado.png"
```

Y con una segunda imagen:

```powershell
.\AegisForge_agent\.venv\Scripts\python.exe -m quipu_vision --reference "C:\ruta\referencia.png" --observed "C:\ruta\observacion.png" --followup "C:\ruta\segunda.png" --annotated "C:\ruta\resultado.png"
```

## Invocación en AegisForge (explícita)

Abre PowerShell dentro de `AegisForge_agent/` y ejecuta este bloque, sustituyendo las dos rutas por imágenes tuyas tomadas desde el mismo punto:

```powershell
@'
from aegisforge.plugins.examples.quipu_vision import QuipuVisionPlugin
from aegisforge.plugins.contracts.plugin_contract import PluginContext
ctx = PluginContext(
    task_id="demo-1", track="vision", prompt="analizar cambio",
    metadata={
        "reference_path": r"C:\ruta\referencia.png",
        "observed_path": r"C:\ruta\observacion.png"
    }
)
print(QuipuVisionPlugin().run(ctx))
'@ | .\.venv\Scripts\python.exe -
```

## Registro del proyecto

- Motor: `Quipu-Vision/` — preparar como repositorio Git independiente.
- Puente: `AegisForge_agent/src/aegisforge/plugins/examples/quipu_vision.py` — se agrega al repositorio existente.
- Evitar duplicar el motor en ambos repositorios.
- Siguiente incremento: conectar la capacidad al runtime A2A real y demostrar un componente de OpenCV 5 ejecutándose en AWS.
