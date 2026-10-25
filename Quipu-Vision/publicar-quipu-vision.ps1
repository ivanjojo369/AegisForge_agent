# Publicacion coordinada de Quipu Vision en DOS repositorios.
# Sin -Publicar: prepara clones y deja commits SIN crear ni subir.
# Con -Publicar: tras validar, crea y envia commits a main, sin force push.
# Nunca modifica el arbol original de AegisForge_agent.
[CmdletBinding()]
param(
    [string]$Origen = 'C:\Users\PC\Documents\AGI-Prototipo\AegisForge_agent',
    [string]$Destino = '',
    [switch]$Publicar
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Ejecutar-Git {
    param([string]$Directorio, [string[]]$Argumentos)
    & git -C $Directorio @Argumentos
    if ($LASTEXITCODE -ne 0) { throw "Fallo git en $Directorio : $($Argumentos -join ' ')" }
}
function Copiar-Archivo {
    param([string]$Fuente, [string]$DestinoArchivo)
    if (-not (Test-Path -LiteralPath $Fuente -PathType Leaf)) {
        throw "Falta archivo necesario: $Fuente"
    }
    $carpeta = Split-Path -Parent $DestinoArchivo
    New-Item -ItemType Directory -Path $carpeta -Force | Out-Null
    Copy-Item -LiteralPath $Fuente -Destination $DestinoArchivo -Force
}
function Copiar-Ambos {
    param([string]$RutaRelativa)
    $archivo = Join-Path $visionLocal $RutaRelativa
    Copiar-Archivo $archivo (Join-Path $standalone $RutaRelativa)
    Copiar-Archivo $archivo (Join-Path (Join-Path $integrated 'Quipu-Vision') $RutaRelativa)
}
function Copiar-ArchivosPatron {
    param([string]$Subcarpeta, [string]$Patron)
    $carpeta = Join-Path $visionLocal $Subcarpeta
    if (-not (Test-Path -LiteralPath $carpeta -PathType Container)) {
        throw "Falta la carpeta: $carpeta"
    }
    $archivos = @(Get-ChildItem -LiteralPath $carpeta -File -Filter $Patron)
    if ($archivos.Count -eq 0) { throw "No se encontraron archivos: $carpeta / $Patron" }
    foreach ($f in $archivos) { Copiar-Ambos (Join-Path $Subcarpeta $f.Name) }
}

Get-Command git -ErrorAction Stop | Out-Null
$Origen = (Resolve-Path -LiteralPath $Origen).Path
$visionLocal = Join-Path $Origen 'Quipu-Vision'
if (-not (Test-Path -LiteralPath (Join-Path $Origen 'Dockerfile'))) {
    throw 'El origen no es la raiz de AegisForge_agent: falta Dockerfile.'
}
if (-not (Test-Path -LiteralPath (Join-Path $visionLocal 'pyproject.toml'))) {
    throw 'Falta Quipu-Vision/pyproject.toml en el origen.'
}
$top = (& git -C $Origen rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0) { throw 'El proyecto principal no parece ser un repositorio Git.' }
if ([IO.Path]::GetFullPath($top).TrimEnd('\','/') -ne [IO.Path]::GetFullPath($Origen).TrimEnd('\','/')) {
    throw "Origen inesperado de Git: $top"
}

# La distribucion guardada debe corresponder al mismo plugin que se esta ejecutando.
$pluginActivo = Join-Path $Origen 'src\aegisforge\plugins\examples\quipu_vision.py'
$pluginIntegracion = Join-Path $visionLocal 'integrations\aegisforge\quipu_vision.py'
if (-not (Test-Path -LiteralPath $pluginActivo) -or -not (Test-Path -LiteralPath $pluginIntegracion)) {
    throw 'Falta alguna copia del plugin quipu_vision.py.'
}
$hashActivo = (Get-FileHash -LiteralPath $pluginActivo -Algorithm SHA256).Hash
$hashIntegracion = (Get-FileHash -LiteralPath $pluginIntegracion -Algorithm SHA256).Hash
if ($hashActivo -ne $hashIntegracion) {
    throw 'Las dos copias de quipu_vision.py difieren. No se publican versiones incompatibles. Revisa cual fue la validada.'
}

if ([string]::IsNullOrWhiteSpace($Destino)) {
    $Destino = Join-Path $env:TEMP ('quipu-release-' + [guid]::NewGuid().ToString('N'))
}
$Destino = [IO.Path]::GetFullPath($Destino)
if (Test-Path -LiteralPath $Destino) { throw "La carpeta destino ya existe: $Destino" }
New-Item -ItemType Directory -Path $Destino -Force | Out-Null
$standalone = Join-Path $Destino 'Quipu-Vision'
$integrated = Join-Path $Destino 'AegisForge_agent'
Write-Host 'Clonando copias LIMPIAS; el proyecto original no se modifica...' -ForegroundColor Cyan
& git clone --branch main --depth 1 https://github.com/ivanjojo369/Quipu-Vision.git $standalone
if ($LASTEXITCODE -ne 0) { throw 'No se pudo clonar Quipu-Vision.' }
& git clone --branch main --depth 1 https://github.com/ivanjojo369/AegisForge_agent.git $integrated
if ($LASTEXITCODE -ne 0) { throw 'No se pudo clonar AegisForge_agent.' }

# Motor y pruebas originales, nuevos experimentos y modulos distribuibles.
Copiar-ArchivosPatron 'src\quipu_vision' '*.py'
Copiar-ArchivosPatron 'tests' 'test_*.py'
Copiar-ArchivosPatron 'tests\web' '*.test.mjs'
$directos = @(
    'pyproject.toml', 'README.md',
    'src\vision_experiments.py',
    'integrations\aegisforge\quipu_vision.py',
    'integrations\aws\vision-lambda.yaml',
    'tools\integrate_web.py',
    'integrations\aegisforge\web\evaluation-lab.html',
    'integrations\aegisforge\web\vision\image-quality.js',
    'integrations\aegisforge\web\vision\evidence-overlay.js',
    'integrations\aegisforge\web\vision\decision-trace.js'
)
foreach ($rel in $directos) { Copiar-Ambos $rel }

# La interfaz integrada es la que realmente fue probada: fuente canonica.
$assets = @('index.html','app.js','styles.css','image-quality.js','evidence-overlay.js','decision-trace.js')
foreach ($nombre in $assets) {
    $fuente = Join-Path $Origen (Join-Path 'src\aegisforge\web\vision' $nombre)
    Copiar-Archivo $fuente (Join-Path $integrated (Join-Path 'src\aegisforge\web\vision' $nombre))
    Copiar-Archivo $fuente (Join-Path $standalone (Join-Path 'integrations\aegisforge\web\vision' $nombre))
    Copiar-Archivo $fuente (Join-Path (Join-Path $integrated 'Quipu-Vision') (Join-Path 'integrations\aegisforge\web\vision' $nombre))
}
$lab = Join-Path $Origen 'src\aegisforge\web\evaluation-lab.html'
Copiar-Archivo $lab (Join-Path $integrated 'src\aegisforge\web\evaluation-lab.html')
Copiar-Archivo $lab (Join-Path $standalone 'integrations\aegisforge\web\evaluation-lab.html')
Copiar-Archivo $lab (Join-Path $integrated 'Quipu-Vision\integrations\aegisforge\web\evaluation-lab.html')

Copiar-Archivo (Join-Path $Origen 'src\aegisforge\a2a_server.py') (Join-Path $integrated 'src\aegisforge\a2a_server.py')
Copiar-Archivo $pluginActivo (Join-Path $integrated 'src\aegisforge\plugins\examples\quipu_vision.py')
Copiar-Archivo $pluginActivo (Join-Path $standalone 'integrations\aegisforge\quipu_vision.py')
Copiar-Archivo $pluginActivo (Join-Path $integrated 'Quipu-Vision\integrations\aegisforge\quipu_vision.py')

# Se usa la plantilla AWS mas reciente del paquete distribuible.
$aws = Join-Path $visionLocal 'integrations\aws\vision-lambda.yaml'
Copiar-Archivo $aws (Join-Path $integrated 'infra\aws\vision-lambda.yaml')

# Copias del conjunto sintetico propio: NO incluir outputs locales ni secretos.
Copiar-Ambos 'examples\evaluation\manifest.json'
Copiar-Ambos 'examples\evaluation\evaluate.py'
$imgs = @(Get-ChildItem -LiteralPath (Join-Path $visionLocal 'examples\evaluation\images') -File -Filter '*.png')
if ($imgs.Count -eq 0) { throw 'No hay imagenes PNG del benchmark.' }
foreach ($f in $imgs) { Copiar-Ambos (Join-Path 'examples\evaluation\images' $f.Name) }

# Los documentos enviados por el usuario son DRAFT hasta completar AWS/video/Devpost.
$documentos = Join-Path $PSScriptRoot 'docs'
$evidencias = Join-Path $PSScriptRoot 'evidence'
foreach ($archivo in @(Get-ChildItem -LiteralPath $documentos -File)) {
    foreach ($repoDestino in @($standalone, (Join-Path $integrated 'Quipu-Vision'))) {
        Copiar-Archivo $archivo.FullName (Join-Path $repoDestino (Join-Path 'docs\competitions\opencv\submission-kit' $archivo.Name))
    }
}
foreach ($archivo in @(Get-ChildItem -LiteralPath $evidencias -File)) {
    foreach ($repoDestino in @($standalone, (Join-Path $integrated 'Quipu-Vision'))) {
        Copiar-Archivo $archivo.FullName (Join-Path $repoDestino (Join-Path 'docs\competitions\opencv\evidence' $archivo.Name))
    }
}

# Herramientas de comprobacion y solicitud AWS firmada (no contienen credenciales).
$herramientas = Join-Path $PSScriptRoot 'scripts'
foreach ($archivo in @(Get-ChildItem -LiteralPath $herramientas -File)) {
    foreach ($repoDestino in @($standalone, (Join-Path $integrated 'Quipu-Vision'))) {
        Copiar-Archivo $archivo.FullName (Join-Path $repoDestino (Join-Path 'tools\competition2026' $archivo.Name))
    }
}

# Solo se hace 'git add -A' sobre clones NUEVOS que contienen exclusivamente
# archivos de GitHub y los archivos elegidos por las reglas anteriores.
foreach ($repoDestino in @($standalone, $integrated)) {
    Ejecutar-Git $repoDestino @('add','-A')
    Ejecutar-Git $repoDestino @('diff','--cached','--check')
    Write-Host "`nCAMBIOS PREPARADOS: $repoDestino" -ForegroundColor Cyan
    Ejecutar-Git $repoDestino @('diff','--cached','--stat')
    Ejecutar-Git $repoDestino @('status','--short')
}
Write-Host "`nClones preparados en: $Destino" -ForegroundColor Yellow

if (-not $Publicar) {
    Write-Host 'MODO REVISION: no se creo ningun commit ni se hizo push.' -ForegroundColor Yellow
    Write-Host 'Revisa los dos clones. Si todo esta correcto, ejecuta de nuevo este script con -Publicar (crea clones nuevos).'
    return
}

# Nunca se fuerza la rama principal. Si GitHub avanzo, push fallara con seguridad.
foreach ($repoDestino in @($standalone, $integrated)) {
    $staged = (& git -C $repoDestino diff --cached --name-only)
    if ($LASTEXITCODE -ne 0) { throw "No se pudo consultar staging de $repoDestino" }
    if (-not $staged) { throw "Sin cambios para confirmar: $repoDestino. No se publican versiones parciales." }
}
Ejecutar-Git $standalone @('commit','-m','Release Quipu Vision 0.4 engine and 0.5 web evaluation candidate')
Ejecutar-Git $integrated @('commit','-m','Integrate Quipu Vision web evaluation and competition documentation')
Write-Host 'Publicando Quipu-Vision independiente...' -ForegroundColor Cyan
Ejecutar-Git $standalone @('push','origin','main')
Write-Host 'Publicando integracion AegisForge...' -ForegroundColor Cyan
Ejecutar-Git $integrated @('push','origin','main')
Write-Host 'Publicados ambos repositorios. Revisar SHAs:' -ForegroundColor Green
Ejecutar-Git $standalone @('rev-parse','HEAD')
Ejecutar-Git $integrated @('rev-parse','HEAD')
Write-Host 'AWS/video/Devpost siguen PENDIENTES y no se afirman como completados.'
