# Run one part of the product as its own docker compose project.
# Usage (from the repository folder):
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 gateway                       start the channel gateway (receive, inbound, send)
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 gateway -Service send -Scale 2    run 2 copies of one part
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 backend                       start the rest of the backend (api, worker, scheduler)
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 ai -Scale 3                   start / resize the AI engine to 3 copies
#   ... -Action logs | ps | down           -Build to rebuild the image first
# Start the backend before the others: it runs the database migrations.   Frontend: scripts\dev-frontend.ps1
param(
  [Parameter(Mandatory)][ValidateSet('gateway', 'backend', 'ai')][string]$Stack,
  [ValidateSet('up', 'down', 'logs', 'ps')][string]$Action = 'up',
  [string]$Service,
  [int]$Scale = 1,
  [switch]$Build
)
$ErrorActionPreference = 'Stop'
$deploy = Join-Path $PSScriptRoot '..\deploy'
$where = @{ gateway = @('gateway', 'docker-compose.yml'); backend = @('standalone', 'backend.yml'); ai = @('standalone', 'ai-engine.yml') }[$Stack]
$dir = Join-Path $deploy $where[0]
$envFile = Join-Path $dir '.env'
if (-not (Test-Path $envFile)) { throw "Missing $envFile. Copy $dir\.env.example to $dir\.env and fill it in (the demo's deploy\.env is not used)." }
$base = @('compose', '--env-file', $envFile, '-f', (Join-Path $dir $where[1]))
if ($Stack -eq 'ai' -and -not $Service) { $Service = 'ai-engine' }

switch ($Action) {
  'up'   { $a = @('up', '-d'); if ($Build) { $a += '--build' }; if ($Service) { $a += @('--scale', "$Service=$Scale") }; docker @base @a }
  'down' { docker @base down }
  'logs' { docker @base logs -f --tail 50 }
  'ps'   { docker @base ps }
}
