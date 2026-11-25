# What is running and where: every address and port, who can reach it, the database and the model in use. Read-only; never prints a secret.
#
#   powershell -ExecutionPolicy Bypass -File scripts\info.ps1
#
# fresh-start.ps1 and stack.ps1 call this when they finish, so a deployment always ends with the same clear picture.
param([switch]$Quiet)
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$m = [Environment]::GetEnvironmentVariable('Path', 'Machine'); $u = [Environment]::GetEnvironmentVariable('Path', 'User'); $env:Path = "$m;$u"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Host 'Docker is not on the PATH.' -ForegroundColor Yellow; exit 1 }

function Get-EnvValue([string]$file, [string]$key) {
  foreach ($rel in @($file, 'deploy\all.env')) {        # the part's own file first; then the optional shared one
    $f = Join-Path $root $rel
    if (-not (Test-Path $f)) { continue }
    foreach ($l in [System.IO.File]::ReadAllLines($f)) { if ($l -match "^$key=(.*)$") { $v = $Matches[1].Trim(); if ($v.StartsWith('#')) { $v = '' }; $v = ($v -replace '\s+#.*$', ''); if ($v) { return $v } } }
  }
  return ''
}
# what each component is for, in plain words
$what = @{
  'web/site' = 'Shop app and owner login (open this in a browser)'; 'web/api' = 'The API behind the shop app'; 'web/scheduler' = 'Web timers: billing, reminders, alerts'
  'gateway/receive' = 'Receives WhatsApp messages from Meta (the webhook)'; 'gateway/inbound' = 'Turns received messages into conversations'; 'gateway/send' = 'Sends replies to WhatsApp'
  'gateway/scheduler' = 'Gateway timers'; 'agent/agent' = 'The AI: reads, decides and writes the replies'; 'agent/scheduler' = 'Follow-ups and the conversation clock'
  'admin/site' = "Founder console (sign in with the founder's authenticator)"; 'admin/api' = 'The API behind the founder console'
}
$rows = @()
$raw = @(docker ps -a --filter 'name=chatdeal-' --filter 'name=meta-sim' --format '{{.Names}}|{{.Status}}|{{.Ports}}' 2>$null)
foreach ($line in $raw) {
  $p = $line -split '\|', 3
  if ($p.Count -lt 3) { continue }
  $name = $p[0]
  if ($name -match '^chatdeal-pg$') { $svc = 'database'; $comp = 'postgres' }
  elseif ($name -match '^meta-sim') { $svc = 'simulator'; $comp = 'phones' }
  elseif ($name -match '^chatdeal-([a-z]+)-([a-z]+)-\d+$') { $svc = $Matches[1]; $comp = $Matches[2] }
  else { continue }
  $state = if ($p[1] -match 'unhealthy') { 'UNHEALTHY' } elseif ($p[1] -match 'healthy') { 'healthy' } elseif ($p[1] -match '^Up') { 'running' } else { 'STOPPED' }
  $ports = @()
  foreach ($m2 in [regex]::Matches($p[2], '(?<ip>\d+\.\d+\.\d+\.\d+):(?<host>\d+)->(?<inner>\d+)/tcp')) { $ports += [pscustomobject]@{ Ip = $m2.Groups['ip'].Value; Port = $m2.Groups['host'].Value } }
  $purpose = $what["$svc/$comp"]; if (-not $purpose) { $purpose = if ($svc -eq 'database') { 'The database (local container)' } elseif ($svc -eq 'simulator') { 'Pretend WhatsApp phones for testing (no real Meta)' } else { '' } }
  $rows += [pscustomobject]@{ Svc = $svc; Comp = $comp; State = $state; Ports = $ports; Purpose = $purpose }
}
if ($rows.Count -eq 0) { Write-Host 'Nothing of ChatDeal is running.' -ForegroundColor Yellow; exit 0 }

Write-Host ''
Write-Host 'WHAT IS RUNNING' -ForegroundColor Cyan
Write-Host ('{0,-10} {1,-10} {2,-10} {3,-28} {4}' -f 'SERVICE', 'COMPONENT', 'STATE', 'ADDRESS (who can open it)', 'WHAT IT DOES')
foreach ($r in ($rows | Sort-Object Svc, Comp)) {
  $addr = '(inside Docker only)'
  $seen = @{}
  $list = @()
  foreach ($pt in $r.Ports) { if ($seen[$pt.Port]) { continue }; $seen[$pt.Port] = $true; $who = if ($pt.Ip -eq '127.0.0.1') { 'this computer' } else { 'whole network' }; $list += "localhost:$($pt.Port) ($who)" }
  if ($list.Count -gt 0) { $addr = $list -join ', ' }
  $color = if ($r.State -in 'healthy', 'running') { 'Green' } else { 'Red' }
  Write-Host ('{0,-10} {1,-10} {2,-10} {3,-28} {4}' -f $r.Svc, $r.Comp, $r.State, $addr, $r.Purpose) -ForegroundColor $color
}

$web = ($rows | Where-Object { $_.Svc -eq 'web' -and $_.Comp -eq 'site' } | Select-Object -First 1)
$recv = ($rows | Where-Object { $_.Svc -eq 'gateway' -and $_.Comp -eq 'receive' } | Select-Object -First 1)
$sim = ($rows | Where-Object { $_.Svc -eq 'simulator' } | Select-Object -First 1)
$adm = ($rows | Where-Object { $_.Svc -eq 'admin' -and $_.Comp -eq 'site' } | Select-Object -First 1)
function First-Port($r) { if ($r -and $r.Ports.Count -gt 0) { return $r.Ports[0].Port }; return $null }
Write-Host ''
Write-Host 'OPEN THESE' -ForegroundColor Cyan
if (First-Port $web) { Write-Host ("  Shop app            http://localhost:{0}" -f (First-Port $web)) }
if (First-Port $adm) { Write-Host ("  Founder console     http://localhost:{0}" -f (First-Port $adm)) }
if (First-Port $sim) { Write-Host ("  Test phones         http://localhost:{0}   (chat as a customer; no real WhatsApp)" -f (First-Port $sim)) }
if (First-Port $recv) { Write-Host ("  Webhook (for Meta)  http://localhost:{0}/webhooks/whatsapp   (a real Meta app needs a public HTTPS address in front of this)" -f (First-Port $recv)) }

# the database, without its password
$dburl = Get-EnvValue 'deploy\web\.env' 'DATABASE_URL'
if (-not $dburl) { $dburl = Get-EnvValue 'deploy\agent\.env' 'DATABASE_URL' }
Write-Host ''
Write-Host 'DATABASE' -ForegroundColor Cyan
if ($dburl -match '^postgres(?:ql)?://([^:@/]+)(?::[^@]*)?@([^/]+)/([^?]+)(\?.*)?$') {
  $tls = if ($dburl -match 'sslmode=(require|verify-ca|verify-full)') { 'encrypted (TLS)' } else { 'NOT encrypted (fine on one computer, not in production)' }
  Write-Host ("  {0}  database {1}  login {2}  connection {3}" -f $Matches[2], $Matches[3], $Matches[1], $tls)
} else { Write-Host '  not set up yet (deploy\web\.env has no DATABASE_URL)' -ForegroundColor Yellow }
$pgp = ($rows | Where-Object { $_.Svc -eq 'database' } | Select-Object -First 1)
if (First-Port $pgp) { Write-Host ("  The local database container is on port {0}, reachable from this computer only." -f (First-Port $pgp)) }

# the model
$prov = Get-EnvValue 'deploy\agent\.env' 'LLM_PROVIDER'
Write-Host ''
Write-Host 'AI MODEL' -ForegroundColor Cyan
if ($prov) {
  $url = Get-EnvValue 'deploy\agent\.env' 'OPENAI_COMPAT_BASE_URL'
  $hostName = if ($url -match '^https?://([^/]+)') { $Matches[1] } else { '' }
  $key = if ($prov -eq 'anthropic') { Get-EnvValue 'deploy\agent\.env' 'ANTHROPIC_API_KEY' } else { Get-EnvValue 'deploy\agent\.env' 'OPENAI_COMPAT_API_KEY' }
  $keyState = if ($prov -eq 'local') { 'no key needed (built-in stand-in, NOT for real customers)' } elseif ($key) { 'key is set' } else { 'KEY IS MISSING: customers get the safe holding message' }
  Write-Host ("  provider {0} {1}  planner {2}  writer {3}  check {4}" -f $prov, $(if ($hostName) { "($hostName)" } else { '' }), (Get-EnvValue 'deploy\agent\.env' 'LLM_MODEL_PLANNER'), (Get-EnvValue 'deploy\agent\.env' 'LLM_MODEL_WRITER'), (Get-EnvValue 'deploy\agent\.env' 'LLM_MODEL_CHECK'))
  Write-Host "  $keyState" -ForegroundColor $(if ($key -or $prov -eq 'local') { 'Gray' } else { 'Red' })
} else { Write-Host '  not set up yet (deploy\agent\.env has no LLM_PROVIDER)' -ForegroundColor Yellow }

if (-not $Quiet) {
  Write-Host ''
  Write-Host 'COMMANDS' -ForegroundColor Cyan
  Write-Host '  Live logs, readable         scripts\logs.ps1 -Follow        (only problems: add -Errors   one service: -Part agent)'
  Write-Host '  One customer, all services  scripts\logs.ps1 -Follow -Chat 1a2b3c4d      (the short code shown in each log line)'
  Write-Host '  This summary again          scripts\info.ps1'
  Write-Host '  Stop one service            scripts\stack.ps1 <web|gateway|agent|admin> -Action down'
}
Write-Host ''

