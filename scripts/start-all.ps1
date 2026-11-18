# Turn everything on, then show the logs of every service live.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1              start everything, print the addresses, then follow all logs (Ctrl+C stops the logs only, the servers keep running)
#   powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1 -NoLogs      start everything and print the addresses, then return
#   powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1 -Errors      follow only warnings and errors
#   powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1 -Build       rebuild the images first (after changing code)
#
# It starts Docker Desktop if it is not running, the database, the four parts (gateway, agent, web, admin) and the Meta simulator. Nothing is
# rebuilt unless you ask (-Build). Stopping: scripts\stack.ps1 <part> -Action down, or Docker Desktop.
param([switch]$NoLogs, [switch]$Errors, [switch]$Build)
$ErrorActionPreference = 'Continue'
$root = Join-Path $PSScriptRoot '..'
$m = [Environment]::GetEnvironmentVariable('Path', 'Machine'); $u = [Environment]::GetEnvironmentVariable('Path', 'User'); $env:Path = "$m;$u"

function Test-Engine { try { $null = docker version --format '{{.Server.Version}}' 2>$null; return ($LASTEXITCODE -eq 0) } catch { return $false } }

Write-Host '1/4  Docker ...'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Host 'Docker is not installed or not on the PATH.' -ForegroundColor Red; exit 1 }
if (-not (Test-Engine)) {
  $exe = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'
  if (Test-Path $exe) { Write-Host '     starting Docker Desktop (about a minute) ...'; Start-Process $exe } else { Write-Host '     Docker is not running. Start Docker Desktop, then run this again.' -ForegroundColor Yellow }
  for ($i = 0; $i -lt 60 -and -not (Test-Engine); $i++) { Start-Sleep 5 }
  if (-not (Test-Engine)) { Write-Host 'Docker did not start. Open Docker Desktop, wait until it says "running", then run this again.' -ForegroundColor Red; exit 1 }
}
Write-Host '     Docker is running.' -ForegroundColor Green

Write-Host '2/4  The database ...'
docker start chatdeal-pg 2>$null | Out-Null
for ($i = 0; $i -lt 30; $i++) { docker exec chatdeal-pg pg_isready -U postgres 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 2 }
if ($LASTEXITCODE -ne 0) { Write-Host '     the database (chatdeal-pg) did not come up: docker logs chatdeal-pg' -ForegroundColor Red; exit 1 }
Write-Host '     database ready.' -ForegroundColor Green

Write-Host '3/4  The parts and the Meta simulator ...'
foreach ($part in 'gateway', 'agent', 'web', 'admin') {
  $dir = Join-Path $root "deploy\$part"
  if (-not (Test-Path (Join-Path $dir '.env'))) { Write-Host "     ${part}: no deploy\${part}\.env (run scripts\setup-db.ps1 first), skipped" -ForegroundColor Yellow; continue }
  $a = @('compose', '--env-file', (Join-Path $dir '.env'), '-f', (Join-Path $dir 'docker-compose.yml'), 'up', '-d'); if ($Build) { $a += '--build' }
  docker @a 2>&1 | Out-Null
  Write-Host ("     {0}: {1}" -f $part, $(if ($LASTEXITCODE -eq 0) { 'started' } else { 'FAILED (run scripts\stack.ps1 ' + $part + ' to see why)' }))
}
$sim = Join-Path $root 'tools\meta-sim'
if (Test-Path (Join-Path $sim '.env')) {
  $a = @('compose', '--env-file', (Join-Path $sim '.env'), '-f', (Join-Path $sim 'docker-compose.yml'), 'up', '-d'); if ($Build) { $a += '--build' }
  docker @a 2>&1 | Out-Null
  Write-Host ("     simulator: {0}" -f $(if ($LASTEXITCODE -eq 0) { 'started' } else { 'FAILED' }))
}

Write-Host '4/4  Waiting for each address to answer ...'
$targets = [ordered]@{ 'Shop app' = 'http://localhost:8080/healthz'; "Founder's console" = 'http://localhost:8082/healthz'; 'Webhook receive' = 'http://localhost:8001/healthz'; 'Meta simulator' = 'http://localhost:9100/' }
$deadline = (Get-Date).AddSeconds(120)
foreach ($k in $targets.Keys) {
  $ok = $false
  while (-not $ok -and (Get-Date) -lt $deadline) { try { $ok = (Invoke-WebRequest $targets[$k] -UseBasicParsing -TimeoutSec 4).StatusCode -eq 200 } catch { Start-Sleep 3 } }
  Write-Host ("     {0,-18} {1,-34} {2}" -f $k, ($targets[$k] -replace '/healthz$', ''), $(if ($ok) { 'up' } else { 'NOT ANSWERING' })) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
}

if ($NoLogs) { Write-Host "`nAll on. See the logs any time:  powershell -ExecutionPolicy Bypass -File scripts\logs.ps1 -Follow"; exit 0 }
Write-Host "`nAll on. Showing the logs of every service live. Press Ctrl+C to stop watching (the servers keep running).`n"
$l = @('-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'logs.ps1'), '-Follow', '-Tail', '5'); if ($Errors) { $l += '-Errors' }
& powershell @l
