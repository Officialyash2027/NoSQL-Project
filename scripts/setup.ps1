# One-time setup on Windows: virtual environment, dependencies, .env
# Usage (from the project root):  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-Dev]
param([switch]$Dev)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment (.venv) with Python 3.11..."
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { python -m venv .venv }
}
.\.venv\Scripts\python -m pip install --upgrade pip
if ($Dev) { .\.venv\Scripts\pip install -r requirements-dev.txt } else { .\.venv\Scripts\pip install -r requirements.txt }
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env from .env.example" }
New-Item -ItemType Directory -Force data\models, data\runtime, results, graphs | Out-Null
Write-Host "Setup complete. Next: scripts\start.ps1"
