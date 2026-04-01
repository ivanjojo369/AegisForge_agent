[CmdletBinding()]
param(
    [string[]]$ScenarioIds = @(
        "banbotbypass",
        "chainofchaos",
        "censorship",
        "translationtrauma",
        "taxwiztrap",
        "wikiwiper",
        "hiddenhtml",
        "codecommentcon",
        "imageimplant",
        "qrquirk",
        "saleforceonespy",
        "debugdoor",
        "gitgud",
        "secretkeeper",
        "pluginpirate2",
        "frontedgefail",
        "svgscript",
        "renderbender",
        "cardflowcrash",
        "mirispoof",
        "phonepalprobe",
        "insuranceinsider",
        "insurancephotoinject",
        "prototypeproprobe",
        "slacksnoop",
        "swiftsupply",
        "pypi_piper",
        "devcontainerdoom",
        "gemheist",
        "apiapocalypse"
    ),
    [ValidateSet("defender", "attacker")]
    [string[]]$Modes = @("defender", "attacker"),
    [switch]$SkipRuntimeCheck,
    [switch]$SkipSuitePreflight,
    [switch]$StopOnFirstFail,
    [string]$RuntimeBaseUrl = "http://127.0.0.1:8001"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
}
else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        throw "Python executable not found. Expected: $VenvPython or python on PATH."
    }
    $Python = $cmd.Source
}

$Resolver     = Join-Path $RepoRoot "harness\AegisForge_scenarios\security_arena\scenario_resolver.py"
$Orchestrator = Join-Path $RepoRoot "harness\AegisForge_scenarios\security_arena\orchestrator.py"
$Catalog      = Join-Path $RepoRoot "harness\AegisForge_scenarios\security_arena\scenario_catalog.toml"
$TemplatesDir = Join-Path $RepoRoot "templates\security_adapter"
$GeneratedDir = Join-Path $RepoRoot "harness\AegisForge_scenarios\security_arena\generated"

foreach ($requiredPath in @($Resolver, $Orchestrator, $Catalog, $TemplatesDir, $GeneratedDir)) {
    if (-not (Test-Path $requiredPath)) {
        throw "Required path not found: $requiredPath"
    }
}

function Get-ItemCount {
    param($Value)

    if ($null -eq $Value) { return 0 }
    if ($Value -is [string]) { return 1 }
    return @($Value).Length
}

function Invoke-SuitePreflight {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseUrl
    )

    $base = $BaseUrl.TrimEnd("/")
    $healthOk = $false
    $agentCardOk = $false
    $reasons = @()

    try {
        $resp = Invoke-WebRequest -Uri "$base/health" -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -eq 200) {
            $healthOk = $true
        }
        else {
            $reasons += "health_status_$($resp.StatusCode)"
        }
    }
    catch {
        $reasons += "health_error: $($_.Exception.Message)"
    }

    try {
        $resp = Invoke-WebRequest -Uri "$base/.well-known/agent-card.json" -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -eq 200) {
            $agentCardOk = $true
        }
        else {
            $reasons += "agent_card_status_$($resp.StatusCode)"
        }
    }
    catch {
        $reasons += "agent_card_error: $($_.Exception.Message)"
    }

    [pscustomobject]@{
        ok            = ($healthOk -and $agentCardOk)
        base_url      = $base
        health_ok     = $healthOk
        agent_card_ok = $agentCardOk
        reasons       = @($reasons)
    }
}

function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,

        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $quotedArgs = foreach ($arg in $ArgumentList) {
        if ($arg -match '[\s"]') {
            '"' + ($arg -replace '"', '\"') + '"'
        }
        else {
            $arg
        }
    }
    $psi.Arguments = [string]::Join(' ', $quotedArgs)

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    [void]$proc.Start()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    [pscustomobject]@{
        ExitCode = $proc.ExitCode
        StdOut   = $stdout
        StdErr   = $stderr
    }
}

function Get-GeneratedTomlPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScenarioId,
        [Parameter(Mandatory = $true)]
        [string]$Mode
    )

    Join-Path $GeneratedDir "${ScenarioId}_${Mode}.toml"
}

function Read-RawFile {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return $null
    }

    try {
        [System.IO.File]::ReadAllText($Path)
    }
    catch {
        Get-Content -Path $Path -Raw
    }
}

function ConvertFrom-JsonCompat {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }

    try {
        $Text | ConvertFrom-Json
    }
    catch {
        $null
    }
}

function Get-JsonObjectFromText {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }

    $clean = $Text -replace "^﻿", ""
    $clean = $clean -replace "`0", ""
    $clean = $clean.Trim()

    $parsed = ConvertFrom-JsonCompat -Text $clean
    if ($null -ne $parsed) {
        return $parsed
    }

    $firstBrace = $clean.IndexOf("{")
    $lastBrace = $clean.LastIndexOf("}")
    if ($firstBrace -ge 0 -and $lastBrace -gt $firstBrace) {
        $candidate = $clean.Substring($firstBrace, $lastBrace - $firstBrace + 1)
        $parsed = ConvertFrom-JsonCompat -Text $candidate
        if ($null -ne $parsed) {
            return $parsed
        }
    }

    $null
}

function Get-NestedValue {
    param(
        $Object,
        [string]$Path
    )

    if ($null -eq $Object -or [string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }

    $current = $Object
    foreach ($part in $Path.Split(".")) {
        if ($null -eq $current) {
            return $null
        }

        $prop = $current.PSObject.Properties[$part]
        if ($null -eq $prop) {
            return $null
        }

        $current = $prop.Value
    }

    $current
}

function Get-FirstNonEmptyValue {
    param(
        $Object,
        [string[]]$Paths
    )

    foreach ($path in $Paths) {
        $value = Get-NestedValue -Object $Object -Path $path
        if ($null -eq $value) { continue }

        if ($value -is [string]) {
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value.Trim()
            }
            continue
        }

        return $value
    }

    $null
}

function Get-NormalizedKey {
    param([AllowNull()][string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return "" }
    (($Text.ToLowerInvariant()) -replace "[^a-z0-9]", "")
}

function Get-NonEmptySignalCount {
    param($Value)

    if ($null -eq $Value) { return 0 }

    if ($Value -is [string]) {
        if ([string]::IsNullOrWhiteSpace($Value)) { return 0 }
        return 1
    }

    if ($Value -is [bool] -or $Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) {
        return 1
    }

    if ($Value -is [System.Collections.IDictionary]) {
        $count = 0
        foreach ($key in $Value.Keys) {
            $count += Get-NonEmptySignalCount -Value $Value[$key]
        }
        return $count
    }

    if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
        $count = 0
        foreach ($item in @($Value)) {
            $count += Get-NonEmptySignalCount -Value $item
        }
        return $count
    }

    if ($null -ne $Value.PSObject) {
        $propCount = Get-ItemCount $Value.PSObject.Properties
        if ($propCount -gt 0) {
            $count = 0
            foreach ($prop in $Value.PSObject.Properties) {
                $count += Get-NonEmptySignalCount -Value $prop.Value
            }
            return $count
        }
    }

    1
}

function Get-CompactString {
    param($Value)

    if ($null -eq $Value) { return $null }
    if ($Value -is [string]) { return $Value.Trim() }
    if ($Value -is [bool] -or $Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) {
        return [string]$Value
    }

    try {
        (($Value | ConvertTo-Json -Depth 50 -Compress) -replace "\r?\n", " ")
    }
    catch {
        [string]$Value
    }
}

function Get-VerdictSummary {
    param($Object)

    if ($null -eq $Object) { return $null }
    if ($Object -is [string]) { return $Object.Trim() }

    $status = Get-NestedValue -Object $Object -Path "status"
    $ok = Get-NestedValue -Object $Object -Path "ok"
    $exitCode = Get-NestedValue -Object $Object -Path "exit_code"

    $parts = @()
    if (-not [string]::IsNullOrWhiteSpace([string]$status)) { $parts += "status=$status" }
    if ($null -ne $ok) { $parts += "ok=$ok" }
    if ($null -ne $exitCode) { $parts += "exit_code=$exitCode" }

    if ((Get-ItemCount $parts) -gt 0) {
        return ($parts -join ", ")
    }

    Get-CompactString -Value $Object
}

function Test-GenericText {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return $true }

    $patterns = @(
        "default","generic","placeholder","unknown","unspecified",
        "not specified","not provided","tbd","todo","n/a"
    )

    $lower = $Text.ToLowerInvariant()
    foreach ($p in $patterns) {
        if ($lower -like "*$p*") { return $true }
    }
    $false
}


function Get-ModeSpecificSoftReasons {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("defender", "attacker")]
        [string]$Mode,

        [Parameter(Mandatory = $true)]
        [int]$FindingsCount,

        $EffectiveSignal,
        $HasFindingsSignal,
        $AnyHighRiskSignal
    )

    $softReasons = @()
    $hasSignalField = ($null -ne $HasFindingsSignal) -or ($null -ne $AnyHighRiskSignal)

    switch ($Mode) {
        "attacker" {
            if ($FindingsCount -le 0) {
                $softReasons += "no_findings_attacker"

                if (-not $hasSignalField) {
                    $softReasons += "missing_signal_fields_attacker"
                }
                elseif ($EffectiveSignal -eq $true) {
                    $softReasons += "signal_true_but_no_findings_attacker"
                }
                elseif ($EffectiveSignal -eq $false) {
                    $softReasons += "signal_false_and_no_findings_attacker"
                }
            }
        }

        "defender" {
            if (-not $hasSignalField) {
                $softReasons += "missing_signal_fields_defender"
            }
            elseif ($EffectiveSignal -eq $false) {
                if ($FindingsCount -gt 0) {
                    $softReasons += "findings_present_but_signal_false_defender"
                }
                else {
                    $softReasons += "signal_false_defender"
                }
            }
        }
    }

    @($softReasons | Select-Object -Unique)
}

function Invoke-Case {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScenarioId,

        [Parameter(Mandatory = $true)]
        [string]$Mode,

        [Parameter(Mandatory = $true)]
        [string]$SuiteDir
    )

    $CaseName = "${ScenarioId}_${Mode}"
    $CaseDir = Join-Path $SuiteDir $CaseName
    New-Item -ItemType Directory -Force -Path $CaseDir | Out-Null

    $ResolverStdout = Join-Path $CaseDir "resolver.stdout.log"
    $ResolverStderr = Join-Path $CaseDir "resolver.stderr.log"
    $OrchStdout     = Join-Path $CaseDir "orchestrator.stdout.log"
    $OrchStderr     = Join-Path $CaseDir "orchestrator.stderr.log"

    $GeneratedToml = Get-GeneratedTomlPath -ScenarioId $ScenarioId -Mode $Mode

    Write-Host ""
    Write-Host "=== $CaseName ===" -ForegroundColor Cyan

    $resolverArgs = @(
        $Resolver,
        "--catalog", $Catalog,
        "--templates-dir", $TemplatesDir,
        "--scenario-id", $ScenarioId,
        "--mode", $Mode
    )
    $resolverNative = Invoke-NativeCapture -FilePath $Python -ArgumentList $resolverArgs -WorkingDirectory $RepoRoot
    $resolverExit = $resolverNative.ExitCode
    Set-Content -Path $ResolverStdout -Value $resolverNative.StdOut -Encoding UTF8
    Set-Content -Path $ResolverStderr -Value $resolverNative.StdErr -Encoding UTF8

    $resolverOk = ($resolverExit -eq 0) -and (Test-Path $GeneratedToml)

    $orchRaw = ""
    $orchErr = ""
    if ($resolverOk) {
        $orchArgs = @(
            $Orchestrator,
            "--scenario", $GeneratedToml,
            "--pretty"
        )
        if (-not $SkipRuntimeCheck) {
            $orchArgs += "--check-runtime"
        }

        $orchNative = Invoke-NativeCapture -FilePath $Python -ArgumentList $orchArgs -WorkingDirectory $RepoRoot
        $orchExit = $orchNative.ExitCode
        $orchRaw = $orchNative.StdOut
        $orchErr = $orchNative.StdErr
        Set-Content -Path $OrchStdout -Value $orchRaw -Encoding UTF8
        Set-Content -Path $OrchStderr -Value $orchErr -Encoding UTF8
    }
    else {
        $orchExit = -999
    }

    if ([string]::IsNullOrWhiteSpace($orchRaw)) {
        $orchRaw = Read-RawFile -Path $OrchStdout
    }

    $jsonObj = Get-JsonObjectFromText -Text $orchRaw
    $jsonParseOk = ($null -ne $jsonObj)
    $rawStdoutChars = if ($orchRaw) { $orchRaw.Length } else { 0 }

    $scenarioName = Get-FirstNonEmptyValue -Object $jsonObj -Paths @(
        "scenario_name",
        "scenario_summary.scenario_name",
        "prepared_payload.scenario_name",
        "adapter_result.scenario_name",
        "adapter_result.payload.scenario_name",
        "evaluation.details.scenario_name"
    )

    $targetSystem = Get-FirstNonEmptyValue -Object $jsonObj -Paths @(
        "target_system",
        "scenario_summary.target_system",
        "prepared_payload.target_system",
        "prepared_payload.security.target_system",
        "adapter_result.payload.target_system",
        "adapter_result.payload.security.target_system",
        "evaluation.details.target_system"
    )

    $goal = Get-FirstNonEmptyValue -Object $jsonObj -Paths @(
        "goal",
        "prepared_payload.goal",
        "prepared_payload.artifact.goal",
        "prepared_payload.security.goal",
        "adapter_result.payload.goal",
        "adapter_result.payload.artifact.goal",
        "adapter_result.payload.security.goal"
    )

    $artifactType = Get-FirstNonEmptyValue -Object $jsonObj -Paths @(
        "artifact.type",
        "prepared_payload.artifact.type",
        "prepared_payload.security.artifact_type",
        "adapter_result.payload.artifact.type",
        "adapter_result.payload.security.artifact_type"
    )

    $findings = Get-FirstNonEmptyValue -Object $jsonObj -Paths @(
        "prepared_payload.findings",
        "evaluation.details.findings",
        "adapter_result.payload.input.findings",
        "adapter_result.payload.security.findings"
    )

    $findingsCount = Get-NonEmptySignalCount -Value $findings

    $hasFindingsSignal = Get-FirstNonEmptyValue -Object $jsonObj -Paths @(
        "evaluation.details.has_findings_signal"
    )
    $anyHighRiskSignal = Get-FirstNonEmptyValue -Object $jsonObj -Paths @(
        "evaluation.details.any_high_risk_signal"
    )
    $effectiveSignal = $hasFindingsSignal
    if ($null -eq $effectiveSignal) { $effectiveSignal = $anyHighRiskSignal }

    $runtimeOk = Get-FirstNonEmptyValue -Object $jsonObj -Paths @(
        "runtime_check.ok"
    )

    $verdictObj = Get-FirstNonEmptyValue -Object $jsonObj -Paths @("verdict")
    $verdictSummary = Get-VerdictSummary -Object $verdictObj

    $failReasons = @()
    $softReasons = @()

    if ($resolverExit -ne 0) {
        $failReasons += "resolver_exit_$resolverExit"
    }
    if (-not $resolverOk) {
        $failReasons += "resolver_failed_or_missing_toml"
    }

    if ($orchExit -eq -999) {
        $failReasons += "orchestrator_skipped_due_to_resolver"
    }
    elseif ($orchExit -eq 1) {
        $failReasons += "orchestrator_exit_1"
    }
    elseif ($orchExit -eq 2) {
        $softReasons += "orchestrator_exit_2_runtime_fail"
    }
    elseif ($orchExit -ne 0) {
        $failReasons += "orchestrator_exit_$orchExit"
    }

    if (-not $jsonParseOk) {
        if ($rawStdoutChars -eq 0) {
            $failReasons += "stdout_empty"
        }
        else {
            $failReasons += "stdout_not_parseable_json"
        }
    }

    if ($jsonParseOk) {
        if ([string]::IsNullOrWhiteSpace([string]$scenarioName)) {
            $failReasons += "missing_scenario_name"
        }
        elseif ((Get-NormalizedKey $scenarioName) -ne (Get-NormalizedKey $ScenarioId)) {
            $failReasons += "scenario_name_mismatch:$scenarioName"
        }

        if ([string]::IsNullOrWhiteSpace([string]$targetSystem)) {
            $failReasons += "missing_target_system"
        }
        elseif (Test-GenericText ([string]$targetSystem)) {
            $softReasons += "generic_target_system"
        }

        if ([string]::IsNullOrWhiteSpace([string]$goal)) {
            if ($Mode -eq "defender") { $failReasons += "missing_goal" }
            else { $softReasons += "missing_goal_attacker" }
        }
        elseif (Test-GenericText ([string]$goal)) {
            $softReasons += "generic_goal"
        }

        if ([string]::IsNullOrWhiteSpace([string]$artifactType)) {
            if ($Mode -eq "defender") { $failReasons += "missing_artifact_type" }
            else { $softReasons += "missing_artifact_type_attacker" }
        }
        elseif (Test-GenericText ([string]$artifactType)) {
            $softReasons += "generic_artifact_type"
        }

        if ($null -eq $verdictSummary -or [string]::IsNullOrWhiteSpace([string]$verdictSummary)) {
            $failReasons += "missing_verdict"
        }

        if (-not $SkipRuntimeCheck -and $runtimeOk -eq $false) {
            $failReasons += "runtime_preflight_failed"
        }

        $softReasons += Get-ModeSpecificSoftReasons -Mode $Mode -FindingsCount $findingsCount -EffectiveSignal $effectiveSignal -HasFindingsSignal $hasFindingsSignal -AnyHighRiskSignal $anyHighRiskSignal
    }

    if ((Get-ItemCount $failReasons) -gt 0) {
        $status = "FAIL"
    }
    elseif ((Get-ItemCount $softReasons) -gt 0) {
        $status = "SOFT PASS"
    }
    else {
        $status = "PASS"
    }

    [pscustomobject]@{
        case                 = $CaseName
        scenario_id          = $ScenarioId
        mode                 = $Mode
        resolver_exit        = $resolverExit
        orchestrator_exit    = $orchExit
        json_parse_ok        = $jsonParseOk
        raw_stdout_chars     = $rawStdoutChars
        runtime_ok           = $runtimeOk
        generated_toml       = $GeneratedToml
        scenario_name        = $scenarioName
        target_system        = $targetSystem
        goal                 = $goal
        artifact_type        = $artifactType
        findings_count       = $findingsCount
        has_findings_signal  = $hasFindingsSignal
        any_high_risk_signal = $anyHighRiskSignal
        effective_signal     = $effectiveSignal
        verdict_summary      = $verdictSummary
        status               = $status
        fail_reasons         = ($failReasons -join "; ")
        soft_reasons         = ($softReasons -join "; ")
        case_dir             = $CaseDir
        resolver_stdout      = $ResolverStdout
        resolver_stderr      = $ResolverStderr
        orchestrator_stdout  = $OrchStdout
        orchestrator_stderr  = $OrchStderr
    }
}

if (-not $SkipSuitePreflight -and -not $SkipRuntimeCheck) {
    $SuitePreflight = Invoke-SuitePreflight -BaseUrl $RuntimeBaseUrl
    Write-Host ("Runtime preflight: base_url={0} health_ok={1} agent_card_ok={2} ok={3}" -f `
        $SuitePreflight.base_url, `
        $SuitePreflight.health_ok, `
        $SuitePreflight.agent_card_ok, `
        $SuitePreflight.ok) -ForegroundColor Yellow

    if ((Get-ItemCount $SuitePreflight.reasons) -gt 0) {
        Write-Host ("Preflight reasons: {0}" -f ($SuitePreflight.reasons -join " | ")) -ForegroundColor DarkYellow
    }

    if (-not $SuitePreflight.ok) {
        throw "Suite preflight failed. Start the local runtime first or use -SkipSuitePreflight / -SkipRuntimeCheck."
    }
}

$Cases = foreach ($scenarioId in $ScenarioIds) {
    foreach ($mode in $Modes) {
        [pscustomobject]@{
            scenario_id = $scenarioId
            mode        = $mode
        }
    }
}

$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$SuiteDir = Join-Path $RepoRoot "runs\security_suite\$RunStamp"
New-Item -ItemType Directory -Force -Path $SuiteDir | Out-Null

Write-Host "Scenarios: $(Get-ItemCount $ScenarioIds)" -ForegroundColor Yellow
Write-Host "Modes: $($Modes -join ', ')" -ForegroundColor Yellow
Write-Host "Total cases: $(Get-ItemCount $Cases)" -ForegroundColor Yellow
Write-Host "Suite dir: $SuiteDir" -ForegroundColor Yellow

$Results = @()

foreach ($case in $Cases) {
    try {
        $result = Invoke-Case -ScenarioId $case.scenario_id -Mode $case.mode -SuiteDir $SuiteDir
        $Results += $result

        if ($StopOnFirstFail -and $result.status -eq "FAIL") {
            break
        }
    }
    catch {
        $failure = [pscustomobject]@{
            case                 = "$($case.scenario_id)_$($case.mode)"
            scenario_id          = $case.scenario_id
            mode                 = $case.mode
            resolver_exit        = $null
            orchestrator_exit    = $null
            json_parse_ok        = $false
            raw_stdout_chars     = 0
            runtime_ok           = $null
            generated_toml       = $null
            scenario_name        = $null
            target_system        = $null
            goal                 = $null
            artifact_type        = $null
            findings_count       = 0
            has_findings_signal  = $null
            any_high_risk_signal = $null
            effective_signal     = $null
            verdict_summary      = $null
            status               = "FAIL"
            fail_reasons         = "powershell_exception:$($_.Exception.Message)"
            soft_reasons         = ""
            case_dir             = Join-Path $SuiteDir "$($case.scenario_id)_$($case.mode)"
            resolver_stdout      = $null
            resolver_stderr      = $null
            orchestrator_stdout  = $null
            orchestrator_stderr  = $null
        }
        $Results += $failure

        if ($StopOnFirstFail) {
            break
        }
    }
}

$SummaryCsv  = Join-Path $SuiteDir "suite_summary.csv"
$SummaryJson = Join-Path $SuiteDir "suite_summary.json"
$SummaryMd   = Join-Path $SuiteDir "suite_summary.md"

$Results | Export-Csv -Path $SummaryCsv -NoTypeInformation -Encoding UTF8
$Results | ConvertTo-Json -Depth 100 | Set-Content -Path $SummaryJson -Encoding UTF8

$md = @()
$md += "# Security Suite Summary"
$md += ""
$md += "Run: $RunStamp"
$md += ""
$md += "| case | status | resolver_exit | orchestrator_exit | json_parse_ok | raw_stdout_chars | runtime_ok | scenario_name | target_system | artifact_type | findings_count | effective_signal | verdict_summary |"
$md += "|---|---|---:|---:|---|---:|---|---|---|---|---:|---|---|"

foreach ($r in $Results) {
    $md += "| $($r.case) | $($r.status) | $($r.resolver_exit) | $($r.orchestrator_exit) | $($r.json_parse_ok) | $($r.raw_stdout_chars) | $($r.runtime_ok) | $($r.scenario_name) | $($r.target_system) | $($r.artifact_type) | $($r.findings_count) | $($r.effective_signal) | $($r.verdict_summary) |"
}

$md += ""
$md += "## Notes"
foreach ($r in $Results) {
    $md += ""
    $md += "### $($r.case)"
    $md += "- fail_reasons: $($r.fail_reasons)"
    $md += "- soft_reasons: $($r.soft_reasons)"
    $md += "- case_dir: $($r.case_dir)"
}

Set-Content -Path $SummaryMd -Value $md -Encoding UTF8

Write-Host ""
Write-Host "=== SUITE SUMMARY ===" -ForegroundColor Green
$Results | Format-Table case, status, resolver_exit, orchestrator_exit, json_parse_ok, raw_stdout_chars, runtime_ok, scenario_name, target_system, artifact_type, findings_count, effective_signal, verdict_summary -AutoSize

Write-Host ""
Write-Host "Artifacts:"
Write-Host "  CSV : $SummaryCsv"
Write-Host "  JSON: $SummaryJson"
Write-Host "  MD  : $SummaryMd"

$passCount = Get-ItemCount ($Results | Where-Object { $_.status -eq "PASS" })
$softCount = Get-ItemCount ($Results | Where-Object { $_.status -eq "SOFT PASS" })
$failCount = Get-ItemCount ($Results | Where-Object { $_.status -eq "FAIL" })

Write-Host ""
Write-Host "PASS=$passCount  SOFT_PASS=$softCount  FAIL=$failCount"

if ($failCount -eq 0 -and $softCount -le 1) {
    Write-Host "Freeze recommendation: YES" -ForegroundColor Yellow
}
else {
    Write-Host "Freeze recommendation: NOT YET" -ForegroundColor Yellow
}
