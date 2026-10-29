# Start HANF on Windows.
#   scripts\start.ps1            -> 5 Redis shards in Docker + API/UI on http://127.0.0.1:8000
#   scripts\start.ps1 -Full      -> also MongoDB and Cassandra (Cassandra needs ~1-2 min to boot)
#   scripts\start.ps1 -Emulated  -> no Docker: in-process emulated Redis (clearly labelled in the UI)
param([switch]$Full, [switch]$Emulated, [int]$Port = 8000)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run scripts\setup.ps1 first." }

if ($Emulated) {
    $env:HANF_REDIS_MODE = "emulated"
    $env:HANF_MONGO_ENABLED = "false"
    $env:HANF_CASSANDRA_ENABLED = "false"
    Write-Host "EMULATED mode: Redis is simulated in-process (fakeredis). Results are NOT live measurements." -ForegroundColor Yellow
} else {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw "Docker is not running. Start Docker Desktop, or use: scripts\start.ps1 -Emulated" }
    if ($Full) { docker compose --profile full up -d } else { docker compose up -d }
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed" }
    Write-Host "Waiting for Redis shards to report healthy..."
    for ($i = 0; $i -lt 30; $i++) {
        $healthy = (docker ps --filter "name=hanf-redis" --filter "health=healthy" -q | Measure-Object).Count
        if ($healthy -ge 5) { break }
        Start-Sleep -Seconds 1
    }
    Write-Host "Redis shards healthy: $healthy/5"
    if ($Full) { Write-Host "MongoDB/Cassandra are starting in the background; the dashboard shows when they connect (Engines page -> Reconnect)." }
}

$env:PYTHONUNBUFFERED = "1"
Write-Host ""
Write-Host "Dashboard : http://127.0.0.1:$Port/"
Write-Host "API docs  : http://127.0.0.1:$Port/docs"
Write-Host "Health    : http://127.0.0.1:$Port/health"
Write-Host "Press Ctrl+C to stop the server (containers keep running; scripts\stop.ps1 stops them)."
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $Port
