$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

uv sync --extra test

$hostAddr = $env:HOST
if (-not $hostAddr) { $hostAddr = "127.0.0.1" }

$port = $env:PORT
if (-not $port) { $port = "9009" }

$card = $env:CARD_URL
if (-not $card) { $card = "http://$hostAddr`:$port/" }

uv run python -m aegisforge.a2a_server --host $hostAddr --port $port --card-url $card
