# Whole product on one machine for a demo: database, backend, AI engine and frontend in a single compose project, with demo shops loaded.
# Usage (from the repository folder):   powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
#   -Down    stop it            -Reset    stop and DELETE the demo database
# Not for development or production: use scripts\stack.ps1 and scripts\dev-frontend.ps1 for the split stacks.
param([switch]$Down, [switch]$Reset)
if ($Reset) { & (Join-Path $PSScriptRoot 'up.ps1') -Reset; exit $LASTEXITCODE }
if ($Down)  { & (Join-Path $PSScriptRoot 'up.ps1') -Down;  exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot 'up.ps1') -Demo
