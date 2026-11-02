# Run one part of the product as its own docker compose project (ADR 0025). Same four parts as scripts/stack.sh.
# Usage (from the repository folder):
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 db                        run the database migrations (do this first, and after every release)
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 web                       the site, its API and its timer
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 gateway                   the only part that talks to Meta
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 agent                     the ChatDeal AI
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 agent -Service agent -Scale 3     run 3 copies of one service
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 all                       db, then web, gateway and agent: everything on one machine
#   ... -Action logs | ps | down           -Build to rebuild the image first
# Each part reads only its own deploy\<part>\.env (copy .env.example), so it is handed only its own secrets.
# The parts share the database and nothing else, so you can run any subset, on any machines.   Frontend alone: scripts\dev-frontend.ps1
param(
  [Parameter(Mandatory)][ValidateSet('db', 'web', 'gateway', 'agent', 'all')][string]$Stack,
  [ValidateSet('up', 'down', 'logs', 'ps')][string]$Action = 'up',
  [string]$Service,
  [int]$Scale = 1,
  [switch]$Build
)
$ErrorActionPreference = 'Stop'
$deploy = Join-Path $PSScriptRoot '..\deploy'

function Invoke-Part([string]$part) {
  $dir = Join-Path $deploy $part
  $envFile = Join-Path $dir '.env'
  if (-not (Test-Path $envFile)) { throw "Missing $envFile. Copy $dir\.env.example to $dir\.env and fill it in (the demo's deploy\.env is not used)." }
  $base = @('compose', '--env-file', $envFile, '-f', (Join-Path $dir 'docker-compose.yml'))
  switch ($Action) {
    'up' {
      if ($part -eq 'db') { if ($Build) { docker @base build }; docker @base run --rm migrate }
      else { $a = @('up', '-d'); if ($Build) { $a += '--build' }; if ($Service) { $a += @('--scale', "$Service=$Scale") }; docker @base @a }
    }
    'down' { if ($part -ne 'db') { docker @base down } }
    'logs' { if ($part -ne 'db') { docker @base logs -f --tail 50 } }
    'ps'   { if ($part -ne 'db') { docker @base ps } }
  }
}

if ($Stack -eq 'all') {
  $order = if ($Action -eq 'down') { @('agent', 'gateway', 'web') } else { @('db', 'web', 'gateway', 'agent') }
  foreach ($p in $order) { Write-Host "== $p =="; Invoke-Part $p }
} else { Invoke-Part $Stack }
