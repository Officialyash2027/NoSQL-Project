# Run experiments from the command line (recommended for report numbers).
#   scripts\run_experiments.ps1                      -> all four, quick profile
#   scripts\run_experiments.ps1 -Experiment e1 -Profile default
#   scripts\run_experiments.ps1 -Experiment e3 -Set "seeds=[1,2]"
param([ValidateSet("e1", "e2", "e3", "e4", "all")][string]$Experiment = "all",
      [ValidateSet("quick", "default", "large")][string]$Profile = "quick",
      [string[]]$Set = @())
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "backend")
$args2 = @("-m", "app.experiments.runner", $Experiment, "--profile", $Profile)
foreach ($s in $Set) { $args2 += @("--set", $s) }
$env:PYTHONUNBUFFERED = "1"
& (Join-Path $root ".venv\Scripts\python.exe") @args2
exit $LASTEXITCODE
