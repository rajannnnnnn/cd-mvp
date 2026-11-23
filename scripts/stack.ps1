# Run one part of the product as its own docker compose project (ADR 0025). Same four parts as scripts/stack.sh.
# Usage (from the repository folder):
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 db                        run the database migrations (do this first, and after every release)
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 web                       the site, its API and its timer
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 gateway                   the only part that talks to Meta
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 agent                     the ChatDeal AI
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 agent -Service agent -Scale 3     run 3 copies of one service
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 admin                     the founder's console (its own address and sign-in)
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 all                       db, then web, admin, gateway and agent: everything on one machine
#   powershell -ExecutionPolicy Bypass -File scripts\stack.ps1 gateway -Only receive,inbound   start only some of a part's services (also with -Action logs | down)
#   ... -Action logs | ps | down           -Build to rebuild the image first
# Each part reads only its own deploy\<part>\.env (copy .env.example), so it is handed only its own secrets.
# The parts share the database and nothing else, so you can run any subset, on any machines.   Frontend alone: scripts\dev-frontend.ps1
param(
  [Parameter(Mandatory)][ValidateSet('db', 'web', 'gateway', 'agent', 'admin', 'all')][string]$Stack,
  [ValidateSet('up', 'down', 'logs', 'ps')][string]$Action = 'up',
  [string]$Service,
  [string[]]$Only,                          # start / stop / follow only these services of the part, e.g. -Only receive,inbound
  [int]$Scale = 1,
  [switch]$Build
)
$ErrorActionPreference = 'Stop'
$deploy = Join-Path $PSScriptRoot '..\deploy'

# Before anything starts: which settings of this part are still empty. Mandatory ones are named so you know what to fill in; nothing is blocked here
# (the part itself prints the exact report if it cannot start, and `python -m salesai doctor <role>` checks everything).
function Show-EnvReport([string]$part, [string]$envFile) {
  try {
    $db = 'DATABASE_URL', 'SYSTEM_DATABASE_URL'
    $need = @{
      db = @('MIGRATION_DATABASE_URL')
      web = $db + @('JWT_SECRET', 'OTP_SECRET')
      gateway = $db + @('META_APP_SECRET', 'META_VERIFY_TOKEN', 'MASTER_KEY')
      agent = $db + @('PRICING_DATABASE_URL', 'LLM_PROVIDER')
      admin = $db + @('CONSOLE_DATABASE_URL', 'ADMIN_JWT_SECRET', 'MASTER_KEY', 'JWT_SECRET', 'OTP_SECRET', 'ADMIN_ALLOWED_ORIGINS')
    }
    $nice = @{
      web = 'ALLOWED_ORIGINS', 'INVOICE_SELLER_NAME', 'ALERT_EMAIL_TO', 'SMTP_HOST'
      gateway = 'META_APP_ID', 'META_CONFIG_ID', 'PLATFORM_SENDER_PHONE_NUMBER_ID', 'PLATFORM_WA_ACCESS_TOKEN'
      agent = 'OPENAI_COMPAT_API_KEY', 'ANTHROPIC_API_KEY'
      admin = @(); db = @()
    }
    $values = @{}
    foreach ($line in Get-Content $envFile) { if ($line -match '^\s*([A-Z][A-Z0-9_]*)=(.*)$') { $v = $Matches[2].Trim(); if ($v.StartsWith('#')) { $v = '' }; $values[$Matches[1]] = $v } }
    $missing = @($need[$part] | Where-Object { -not $values[$_] })
    $optional = @($nice[$part] | Where-Object { -not $values[$_] })
    if ($part -eq 'agent' -and $values['LLM_PROVIDER'] -eq 'openai_compat' -and -not $values['OPENAI_COMPAT_API_KEY']) { $missing += 'OPENAI_COMPAT_API_KEY (the model key)' }
    if ($part -eq 'agent' -and $values['LLM_PROVIDER'] -eq 'anthropic' -and -not $values['ANTHROPIC_API_KEY']) { $missing += 'ANTHROPIC_API_KEY (the model key)' }
    if ($missing.Count -gt 0) { Write-Host "[$part] MANDATORY and still empty: $($missing -join ', ')" -ForegroundColor Yellow }
    else { Write-Host "[$part] all mandatory settings are filled in." -ForegroundColor Green }
    if ($optional.Count -gt 0) { Write-Host "[$part] optional / needed later (not blocking): $($optional -join ', ')" }
  } catch { Write-Host "[$part] could not read the settings file for the report: $($_.Exception.Message)" }
}

function Invoke-Part([string]$part) {
  $dir = Join-Path $deploy $part
  $envFile = Join-Path $dir '.env'
  if (-not (Test-Path $envFile)) { throw "Missing $envFile. Copy $dir\.env.example to $dir\.env and fill it in (the demo's deploy\.env is not used)." }
  Show-EnvReport $part $envFile
  $base = @('compose', '--env-file', $envFile, '-f', (Join-Path $dir 'docker-compose.yml'))
  switch ($Action) {
    'up' {
      if ($part -eq 'db') { if ($Build) { docker @base build }; docker @base run --rm migrate }
      else { $a = @('up', '-d'); if ($Build) { $a += '--build' }; if ($Service) { $a += @('--scale', "$Service=$Scale") }; if ($Only) { $a += $Only }; docker @base @a }
    }
    'down' { if ($part -ne 'db') { if ($Only) { docker @base stop @Only } else { docker @base down } } }
    'logs' { if ($part -ne 'db') { $l = @('logs', '-f', '--tail', '50'); if ($Only) { $l += $Only }; docker @base @l } }
    'ps'   { if ($part -ne 'db') { docker @base ps } }
  }
}

if ($Stack -eq 'all') {
  $order = if ($Action -eq 'down') { @('agent', 'gateway', 'admin', 'web') } else { @('db', 'web', 'admin', 'gateway', 'agent') }
  foreach ($p in $order) { Write-Host "== $p =="; Invoke-Part $p }
} else { Invoke-Part $Stack }
