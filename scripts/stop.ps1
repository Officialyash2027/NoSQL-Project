# Stop the HANF containers. (Stop the API server itself with Ctrl+C in its terminal.)
#   scripts\stop.ps1          -> stop containers (data kept until removed)
#   scripts\stop.ps1 -Remove  -> stop and remove containers (Redis data is not persisted anyway)
param([switch]$Remove)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($Remove) { docker compose --profile full down } else { docker compose --profile full stop }
