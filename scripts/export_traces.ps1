param(
    [Parameter(Mandatory=$true)][string]$SourceDir,
    [Parameter(Mandatory=$true)][string]$OutDir
)

if (-not (Test-Path $SourceDir)) {
    throw "SourceDir does not exist: $SourceDir"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Get-ChildItem -Path $SourceDir -Recurse -File -Include *.json |
    ForEach-Object {
        $target = Join-Path $OutDir $_.Name
        Copy-Item $_.FullName $target -Force
    }

Write-Host "Trace export completed."
