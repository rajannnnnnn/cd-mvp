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
# Each part reads its own deploy\<part>\.env (copy .env.example) and, first, the optional shared deploy\all.env (copy all.env.example): one file for an easy deploy, the part's own
# file wins. Either way a part is handed only the settings its own compose file names, so it gets only its own secrets.
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
$shared = Join-Path $deploy 'all.env'      # optional: one file for an easy deploy; each part's own .env is read after it and wins

# Before anything starts: which settings of this part are still empty. Mandatory ones are named so you know what to fill in; nothing is blocked here
# (the part itself prints the exact report if it cannot start, and `python -m salesai doctor <role>` checks everything).
# Layered settings: the shared file first, then the part's own; a later file wins, but an EMPTY value never blanks one set earlier (a part's .env copied from its
# example is full of empty lines, and docker compose would otherwise treat each as "set to nothing" and wipe what you filled in once in deploy\all.env).
function Merge-Env([string[]]$files, [string]$part) {
  $o = [ordered]@{}
  foreach ($f in $files) { foreach ($l in [System.IO.File]::ReadAllLines($f)) {
    if ($l -match '^\s*([A-Z][A-Z0-9_]*)=(.*)$') { $k = $Matches[1]; $v = $Matches[2]; $t = $v.Trim(); if ($t.StartsWith('#')) { $t = '' }; if ($t -ne '' -or -not $o.Contains($k)) { $o[$k] = $v } } } }
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("chatdeal-$part-" + [guid]::NewGuid().ToString('N') + '.env')
  [System.IO.File]::WriteAllLines($tmp, [string[]]@($o.Keys | ForEach-Object { "$_=$($o[$_])" }), (New-Object System.Text.UTF8Encoding($false)))
  return $tmp
}

function Show-EnvReport([string]$part, [string[]]$envFiles) {
  try {
    $db = 'DATABASE_URL', 'SYSTEM_DATABASE_URL'
    $need = @{
      db = @('MIGRATION_DATABASE_URL')
      web = $db + @('JWT_SECRET', 'OTP_SECRET')
      gateway = $db + @('META_APP_SECRET', 'META_VERIFY_TOKEN', 'MASTER_KEY')
      agent = $db + @('PRICING_DATABASE_URL', 'LLM_PROVIDER')
      admin = $db + @('CONSOLE_DATABASE_URL', 'ADMIN_JWT_SECRET', 'MASTER_KEY', 'JWT_SECRET', 'OTP_SECRET', 'ADMIN_ALLOWED_ORIGINS', 'FOUNDER_PHONE', 'FOUNDER_EMAIL', 'FOUNDER_TOTP_SECRET', 'ADMIN_EMAIL_FROM')
    }
    $nice = @{
      web = 'ALLOWED_ORIGINS', 'INVOICE_SELLER_NAME', 'ALERT_EMAIL_TO', 'SMTP_HOST'
      gateway = 'META_APP_ID', 'META_CONFIG_ID', 'PLATFORM_SENDER_PHONE_NUMBER_ID', 'PLATFORM_WA_ACCESS_TOKEN'
      agent = 'OPENAI_COMPAT_API_KEY', 'ANTHROPIC_API_KEY'
      admin = @(); db = @()
    }
    $values = @{}
    foreach ($line in ($envFiles | ForEach-Object { Get-Content $_ })) { if ($line -match '^\s*([A-Z][A-Z0-9_]*)=(.*)$') { $v = $Matches[2].Trim(); if ($v.StartsWith('#')) { $v = '' }; if ($v -or -not $values.ContainsKey($Matches[1])) { $values[$Matches[1]] = $v } } }
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
  $files = @(); if (Test-Path $shared) { $files += $shared }; if (Test-Path $envFile) { $files += $envFile }
  if ($files.Count -eq 0) { throw "Missing $envFile. Copy $dir\.env.example to $dir\.env and fill it in, or fill in the one shared file: copy deploy\all.env.example to deploy\all.env (the demo's deploy\.env is not used)." }
  Show-EnvReport $part $files
  $merged = $null
  $envArg = if ($files.Count -gt 1) { $merged = Merge-Env $files $part; $merged } else { $files[0] }
  $base = @('compose', '--env-file', $envArg, '-f', (Join-Path $dir 'docker-compose.yml'))
  try {
  switch ($Action) {
    'up' {
      if ($part -eq 'db') { if ($Build) { docker @base build }; docker @base run --rm migrate }
      else { $a = @('up', '-d'); if ($Build) { $a += '--build' }; if ($Service) { $a += @('--scale', "$Service=$Scale") }; if ($Only) { $a += $Only }; docker @base @a }
    }
    'down' { if ($part -ne 'db') { if ($Only) { docker @base stop @Only } else { docker @base down } } }
    'logs' { if ($part -ne 'db') { $l = @('logs', '-f', '--tail', '50'); if ($Only) { $l += $Only }; docker @base @l } }
    'ps'   { if ($part -ne 'db') { docker @base ps } }
  }
  } finally { if ($merged) { Remove-Item -LiteralPath $merged -ErrorAction SilentlyContinue } }      # the merged copy holds every secret: it never outlives the command
}

if ($Stack -eq 'all') {
  $order = if ($Action -eq 'down') { @('agent', 'gateway', 'admin', 'web') } else { @('db', 'web', 'admin', 'gateway', 'agent') }
  foreach ($p in $order) { Write-Host "== $p =="; Invoke-Part $p }
} else { Invoke-Part $Stack }
if ($Action -eq 'up') { & (Join-Path $PSScriptRoot 'info.ps1') -Quiet }      # every start ends with the same clear picture: addresses, ports, database, model
