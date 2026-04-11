[CmdletBinding()]
param(
    [string]$BaseUrl = "",
    [int]$Port = 8003,
    [string]$PythonExe = "",
    [bool]$RunPytest = $true
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    $BaseUrl = "http://127.0.0.1:$Port"
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $RepoPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
    if (Test-Path $RepoPython) {
        $PythonExe = $RepoPython
    }
    elseif ($env:VIRTUAL_ENV) {
        $VenvPython = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
        if (Test-Path $VenvPython) {
            $PythonExe = $VenvPython
        }
        else {
            $PythonExe = "python"
        }
    }
    else {
        $PythonExe = "python"
    }
}

function Test-Condition {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Wait-ServerHealth {
    param(
        [string]$Url,
        [int]$Retries = 30,
        [int]$DelaySeconds = 1
    )

    for ($i = 1; $i -le $Retries; $i++) {
        try {
            $health = Invoke-RestMethod -Method Get -Uri "$Url/health"
            if ($health.status -eq "ok") {
                return $health
            }
        }
        catch {
            # El servidor puede tardar unos segundos en levantar.
        }

        Start-Sleep -Seconds $DelaySeconds
    }

    throw "El servidor no respondió healthy en $Url/health después de $Retries intentos."
}

function Invoke-ScenarioStubJson {
    param(
        [string]$Domain,
        [string]$ScenarioId,
        [string]$Url,
        [string]$PythonExe
    )

    $jsonText = & $PythonExe -m integrations.openenv.envs.omnibench_aegis_env.training.llm_agent_stub `
        --base-url $Url `
        --domain $Domain `
        --scenario-id $ScenarioId `
        --json

    if ($LASTEXITCODE -ne 0) {
        throw "El stub falló para $Domain / $ScenarioId"
    }

    try {
        return ($jsonText | ConvertFrom-Json)
    }
    catch {
        throw "No se pudo parsear la salida JSON del stub para $Domain / $ScenarioId. Salida: $jsonText"
    }
}

function Invoke-PytestRegression {
    param(
        [string]$PythonExe
    )

    Write-Host "[info] Ejecutando pytest de regresión..." -ForegroundColor Cyan

    & $PythonExe -m pytest `
        integrations/openenv/envs/omnibench_aegis_env/tests/test_server_research_inventoryinject.py `
        integrations/openenv/envs/omnibench_aegis_env/tests/test_client_stub_inventoryinject.py `
        integrations/openenv/envs/omnibench_aegis_env/tests/test_server_computer_use_linklifter.py `
        integrations/openenv/envs/omnibench_aegis_env/tests/test_client_stub_linklifter.py `
        integrations/openenv/envs/omnibench_aegis_env/tests/test_server_finance_taxwiztrap.py `
        integrations/openenv/envs/omnibench_aegis_env/tests/test_client_stub_taxwiztrap.py `
        -q

    if ($LASTEXITCODE -ne 0) {
        throw "Pytest de regresión falló."
    }

    Write-Host "[ok] Pytest de regresión en verde." -ForegroundColor Green
}

Write-Host "[info] Iniciando suite end-to-end de OpenEnv..." -ForegroundColor Cyan
Write-Host "[info] BaseUrl: $BaseUrl" -ForegroundColor DarkCyan
Write-Host "[info] Port: $Port" -ForegroundColor DarkCyan
Write-Host "[info] PythonExe: $PythonExe" -ForegroundColor DarkCyan
Write-Host "[info] RunPytest: $RunPytest" -ForegroundColor DarkCyan

& $PythonExe -c "import sys; print(sys.executable)"

$serverArgs = @(
    "-m", "uvicorn",
    "integrations.openenv.envs.omnibench_aegis_env.server.app:app",
    "--host", "127.0.0.1",
    "--port", "$Port"
)

$serverProcess = $null

try {
    $serverProcess = Start-Process -FilePath $PythonExe -ArgumentList $serverArgs -PassThru -WindowStyle Hidden

    $health = Wait-ServerHealth -Url $BaseUrl
    Write-Host "[ok] Health respondió." -ForegroundColor Green

    $contract = Invoke-RestMethod -Method Get -Uri "$BaseUrl/contract"

    Test-Condition -Condition ($health.env -eq "omnibench_aegis_env") -Message "Health.env no coincide."
    Test-Condition -Condition ($contract.supported_env_ids -contains "omnibench_aegis_env:research.inventoryinject") -Message "Falta env research.inventoryinject en contract."
    Test-Condition -Condition ($contract.supported_env_ids -contains "omnibench_aegis_env:computer_use.linklifter") -Message "Falta env computer_use.linklifter en contract."
    Test-Condition -Condition ($contract.supported_env_ids -contains "omnibench_aegis_env:finance.taxwiztrap") -Message "Falta env finance.taxwiztrap en contract."
    Test-Condition -Condition ($contract.primary_scenarios -contains "InventoryInject") -Message "Falta InventoryInject en contract."
    Test-Condition -Condition ($contract.primary_scenarios -contains "LinkLifter") -Message "Falta LinkLifter en contract."
    Test-Condition -Condition ($contract.primary_scenarios -contains "taxwiztrap") -Message "Falta taxwiztrap en contract."

    Write-Host "[ok] Contract validado." -ForegroundColor Green

    $research = Invoke-ScenarioStubJson -Domain "research" -ScenarioId "InventoryInject" -Url $BaseUrl -PythonExe $PythonExe
    Test-Condition -Condition ([bool]$research.success) -Message "InventoryInject no terminó con success=true."
    Test-Condition -Condition ($research.final_state.final_outcome -eq "clean_report") -Message "InventoryInject no terminó en clean_report."

    Write-Host "[ok] InventoryInject validado end-to-end." -ForegroundColor Green

    $computerUse = Invoke-ScenarioStubJson -Domain "computer_use" -ScenarioId "LinkLifter" -Url $BaseUrl -PythonExe $PythonExe
    Test-Condition -Condition ([bool]$computerUse.success) -Message "LinkLifter no terminó con success=true."
    Test-Condition -Condition ($computerUse.final_state.final_outcome -eq "clean_result") -Message "LinkLifter no terminó en clean_result."

    Write-Host "[ok] LinkLifter validado end-to-end." -ForegroundColor Green

    $finance = Invoke-ScenarioStubJson -Domain "finance" -ScenarioId "taxwiztrap" -Url $BaseUrl -PythonExe $PythonExe
    Test-Condition -Condition ([bool]$finance.success) -Message "taxwiztrap no terminó con success=true."
    Test-Condition -Condition ($finance.final_state.final_outcome -eq "clean_answer") -Message "taxwiztrap no terminó en clean_answer."
    Test-Condition -Condition ($finance.final_state.final_answer -eq "MXN 15660.00") -Message "taxwiztrap no devolvió la respuesta final esperada."

    Write-Host "[ok] taxwiztrap validado end-to-end." -ForegroundColor Green

    if ($RunPytest) {
        Invoke-PytestRegression -PythonExe $PythonExe
    }

    Write-Host ""
    Write-Host "=== RESUMEN E2E ===" -ForegroundColor Cyan
    Write-Host "Health: OK"
    Write-Host "Contract: OK"
    Write-Host "InventoryInject: OK"
    Write-Host "LinkLifter: OK"
    Write-Host "taxwiztrap: OK"
    if ($RunPytest) {
        Write-Host "Pytest: OK"
    }
    Write-Host "[ok] Suite end-to-end completada." -ForegroundColor Green
}
finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force
    }
}
