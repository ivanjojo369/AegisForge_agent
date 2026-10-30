# AegisForge Nexus — interfaz web local (v0.1)

Esta entrega **agrega** una interfaz al repositorio; no reemplaza ni modifica `api.py`, `executor.py`, `verifier.py` ni la demostración Nemotron ya validada.

## Rutas exactas en Windows

Raíz del repositorio:

`C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent\`

| Archivo | Ruta dentro del repositorio |
| --- | --- |
| `index.html` | `src\aegisforge\web\nexus\index.html` |
| `styles.css` | `src\aegisforge\web\nexus\styles.css` |
| `app.js` | `src\aegisforge\web\nexus\app.js` |
| `serve_nexus_web.py` | `scripts\nexus\serve_nexus_web.py` |
| `test_web_dashboard.py` | `tests\test_nexus\test_web_dashboard.py` |
| Este documento | `docs\competitions\nebius\README_NEXUS_WEB.md` |

## 1. Instalación sin reemplazar archivos existentes

Descarga `AegisForge-Nexus-Web-Dashboard.zip` a Descargas y ejecuta:

```powershell
cd "C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent"
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\AegisForge-Nexus-Web-Dashboard.zip" -DestinationPath .
$env:PYTHONPATH = Join-Path (Get-Location) "src"
Test-Path .\src\aegisforge\web\nexus\index.html
Test-Path .\src\aegisforge\web\nexus\styles.css
Test-Path .\src\aegisforge\web\nexus\app.js
Test-Path .\scripts\nexus\serve_nexus_web.py
```

Los cuatro `Test-Path` deben imprimir `True`. No uses `-Force` si ya tienes archivos con el mismo nombre y deseas conservarlos.

## 2. Abrir la interfaz sin Nebius (recomendado primero)

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) "src"
python .\scripts\nexus\serve_nexus_web.py
```

Abre **http://127.0.0.1:8012/** en el navegador. Solo se escucha en `127.0.0.1`, no en la red pública. Para cerrar el servidor, presiona `Ctrl+C` en PowerShell.

En el panel, pulsa **Cargar evidencia** y selecciona estos archivos que ya generaste:

```text
evidence\nexus\nemotron_verified_live_5e5b3659841545bca0145b3798bf39e9.json
evidence\nexus\nemotron_verified_live_5e5b3659841545bca0145b3798bf39e9.jsonl
```

La interfaz muestra el plan de cuatro pasos, dos invocaciones y tres comprobaciones si esos datos están presentes. Los datos los procesa tu navegador; no se envían a Nebius ni al servidor como parte de la importación. **El visor no certifica la autenticidad de archivos JSON/JSONL importados.** Para verificar la cadena de integridad con el comprobador Python ya existente, usa el método de `aegisforge.nexus.evidence.verify_jsonl` indicado abajo.

### Comprobar la cadena JSONL localmente (opcional)

```powershell
python -c "from aegisforge.nexus.evidence import verify_jsonl; from pathlib import Path; p=Path('evidence/nexus/nemotron_verified_live_5e5b3659841545bca0145b3798bf39e9.jsonl'); events=verify_jsonl(p); print('Cadena íntegra:', len(events), 'eventos')"
```

Esto comprueba integridad y consistencia interna, **no** prueba por sí solo la autenticidad de acciones externas.

## 3. API en vivo (opcional: puede consumir créditos)

Usa el mismo servidor y puerto 8012, pero inicia en modo `--live`. **Detén el servidor offline** con `Ctrl+C` antes de cambiar de modo.

Solo continúa con la autorización del titular de la cuenta Nebius y el responsable de cualquier cargo.

Abre una PowerShell en la raíz del repositorio:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$secure = Read-Host 'NEBIUS_API_KEY (oculta)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $env:NEBIUS_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }

$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$env:NEXUS_API_TOKEN = [BitConverter]::ToString($bytes).Replace('-', '')
$env:NEXUS_PLANNER_MAX_TOKENS = '3200'

# Opcional: transfiere el token temporal al campo de la interfaz mediante el portapapeles.
Set-Clipboard -Value $env:NEXUS_API_TOKEN
python .\scripts\nexus\serve_nexus_web.py --live
```

Después abre **http://127.0.0.1:8012/**, pega el token en **NEXUS_API_TOKEN** y pulsa **Conectar**. El navegador mantiene el token solamente en memoria, no en `localStorage` ni en parámetros de URL. Una vez pegado, borra el portapapeles desde otra ventana de PowerShell si no lo necesitas:

```powershell
Set-Clipboard -Value ''
```

**Importante:** el navegador no debe recibir la `NEBIUS_API_KEY`. La clave se usa solamente en el proceso local de Python. Para planificar pulsa **Generar plan con Nemotron** y confirma el costo antes de la petición. **Ninguna herramienta se ejecuta desde la API HTTP**, incluso después de pulsar **Finalizar sin herramientas**. En ese caso la verificación puede ser `inconclusive` por diseño.

El resultado `verified` que obtuviste antes provino del script de terminal `demo_nemotron_verified_workflow.py`, con autorización explícita y herramientas predefinidas. Esta interfaz permite **visualizar su evidencia guardada**, pero no reproduce la ejecución de esas herramientas mediante HTTP.

## 4. Pruebas locales (sin Nebius)

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -m unittest discover -s tests/test_nexus -p 'test_web_dashboard.py' -v
```

Las cinco pruebas del nuevo servidor comprueban rutas estáticas, medidas de seguridad y la prohibición de ejecutar herramientas por HTTP. Para la batería completa usa `-p 'test_*.py'`.

## Restricciones de seguridad

- Solo sirve los archivos de UI expresamente definidos; no sirve `.env`, claves, archivos del repositorio ni la carpeta `evidence` automáticamente.
- Servidor ligado a `127.0.0.1`. No añadas un túnel público ni lo expongas a Internet como está.
- Política CSP restrictiva, sin CDN, sin analítica, sin almacenamiento persistente de credenciales.
- Importa archivos de usuario de hasta 1 MB cada uno; la pantalla usa `textContent` para mostrar datos no confiables.
- Solo `/nexus/prepare` realiza una petición a Nebius. No hay reintentos automáticos en el navegador.
- El código fuente se mantiene organizado para pruebas locales; no es un producto público endurecido para producción.
