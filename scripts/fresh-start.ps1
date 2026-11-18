# Everything from a clean checkout to a running product, in one command (run from the repository folder):
#
#   powershell -ExecutionPolicy Bypass -File scripts\fresh-start.ps1 -LlmKey sk-or-v1-...
#
# What it does, in order (safe to run again; it keeps secrets and data unless you pass -Reset):
#   1 starts Docker Desktop if needed        2 creates the database container (chatdeal-pg, password below) and waits for it
#   3 scripts\setup-db.ps1: database, five roles, deploy\<part>\.env files, tables
#   4 scripts\connect-meta-sim.ps1: wires the Meta stand-in    5 puts in the model settings (OpenRouter, openai/gpt-oss-20b) and the site address
#   6 builds and starts gateway, agent, web and the simulator   7 waits for every address, then prints them
# The founder console (admin part) is not started here: its sign-in screen is not finished.
#   -Reset   first deletes the chatdeal containers and data volumes and the deploy\*\.env files: a truly blank start
param(
  [string]$LlmKey = '',
  [string]$PgPassword = 'admin',
  [int]$PgPort = 5432,
  [int]$WebPort = 8085,
  [switch]$Reset
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
[Environment]::CurrentDirectory = $root      # .NET file calls follow this, not PowerShell's folder: without it they look in the wrong place
$m =[Environment]::GetEnvironmentVariable('Path', 'Machine'); $u = [Environment]::GetEnvironmentVariable('Path', 'User'); $env:Path = "$m;$u"
function Step([string]$t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Dk { $ErrorActionPreference = 'Continue'; $o = & docker @args 2>&1; $o | ForEach-Object { "$_" } }   # docker writes progress and "no such volume" to stderr: never treat that as failure
function Test-Engine { try { $null = docker version --format '{{.Server.Version}}' 2>$null; return ($LASTEXITCODE -eq 0) } catch { return $false } }
function Full([string]$p) { if ([System.IO.Path]::IsPathRooted($p)) { $p } else { Join-Path $root $p } }
function Set-EnvValue([string]$file, [string]$key, [string]$value) {
  $file = Full $file
  if (-not (Test-Path $file)) { throw "Missing $file : the earlier step did not write it. Run with -Reset, or check the lines above for an error." }
  $utf8 =New-Object System.Text.UTF8Encoding($false)
  $lines = [System.Collections.Generic.List[string]]([System.IO.File]::ReadAllLines($file))
  $at = -1
  for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^$key=") { $at = $i; break } }
  if ($at -ge 0) { $lines[$at] = "$key=$value" } else { $lines.Add("$key=$value") }
  [System.IO.File]::WriteAllLines($file, $lines, $utf8)
}
function Get-EnvValue([string]$file, [string]$key) {
  $file = Full $file
  if (-not (Test-Path $file)) { return '' }
  foreach ($l in [System.IO.File]::ReadAllLines($file)) { if ($l -match "^$key=(.*)$") { $v = $Matches[1].Trim(); if ($v.StartsWith('#')) { return '' }; return ($v -replace '\s+#.*$', '') } }
  return ''
}

Step '1/7  Docker'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is not installed. Install Docker Desktop, then run this again.' }
if (-not (Test-Engine)) {
  $exe = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'
  if (Test-Path $exe) { Write-Host 'starting Docker Desktop (about a minute) ...'; Start-Process $exe }
  for ($i = 0; $i -lt 60 -and -not (Test-Engine); $i++) { Start-Sleep 5 }
  if (-not (Test-Engine)) { throw 'Docker did not start. Open Docker Desktop, wait until it says running, then run this again.' }
}
Write-Host 'Docker is running.' -ForegroundColor Green

if ($Reset) {
  Step 'Reset: removing the old chatdeal containers, data and .env files'
  foreach ($p in 'gateway', 'agent', 'web', 'admin') { $e = "deploy\$p\.env"; if (Test-Path $e) { Dk compose --env-file $e -f "deploy\$p\docker-compose.yml" down -v | Out-Null } }
  if (Test-Path 'tools\meta-sim\.env') { Dk compose --env-file tools\meta-sim\.env -f tools\meta-sim\docker-compose.yml down -v | Out-Null }
  Dk rm -f chatdeal-pg | Out-Null; Dk volume rm chatdeal-pgdata | Out-Null
  foreach ($p in 'db', 'gateway', 'agent', 'web', 'admin') { Remove-Item "deploy\$p\.env" -ErrorAction SilentlyContinue }
  Remove-Item 'tools\meta-sim\.env' -ErrorAction SilentlyContinue
}

Step "2/7  The database container (chatdeal-pg, port $PgPort)"
$have = (Dk ps -a --filter name=^chatdeal-pg$ --format '{{.Names}}') -contains 'chatdeal-pg'
if (-not $have) {
  Dk run -d --name chatdeal-pg --restart unless-stopped -e "POSTGRES_PASSWORD=$PgPassword" -p "${PgPort}:5432" -v chatdeal-pgdata:/var/lib/postgresql/data postgres:16 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not create the database container. Is port $PgPort already used (a Postgres installed on this PC)? Stop it, or pass -PgPort 5434." }
} else { Dk start chatdeal-pg | Out-Null }
$ready = $false
for ($i = 0; $i -lt 40 -and -not $ready; $i++) { Dk exec chatdeal-pg pg_isready -U postgres | Out-Null; if ($LASTEXITCODE -eq 0) { $ready = $true } else { Start-Sleep 2 } }
if (-not $ready) { throw 'The database did not come up: docker logs chatdeal-pg' }
Write-Host 'database ready.' -ForegroundColor Green

Step '3/7  Database, roles, .env files, tables (a few minutes the first time: builds the backend image)'
& powershell -ExecutionPolicy Bypass -File scripts\setup-db.ps1 -PgContainer chatdeal-pg -SuperPassword $PgPassword -AppDbPort $PgPort
if ($LASTEXITCODE -ne 0) { throw 'setup-db failed (see above).' }

Step '4/7  Meta stand-in wiring'
& powershell -ExecutionPolicy Bypass -File scripts\connect-meta-sim.ps1
if ($LASTEXITCODE -ne 0) { throw 'connect-meta-sim failed (see above).' }

Step '5/7  Model and site settings'
$agent = 'deploy\agent\.env'; $web = 'deploy\web\.env'
if (-not $LlmKey) { $LlmKey = Get-EnvValue $agent 'OPENAI_COMPAT_API_KEY' }
if (-not $LlmKey) {
  $s = Read-Host 'OpenRouter API key (starts sk-or-; Enter to skip: replies then fail to the safe holding message)' -AsSecureString
  $LlmKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
}
Set-EnvValue $agent 'LLM_PROVIDER' 'openai_compat'
Set-EnvValue $agent 'OPENAI_COMPAT_BASE_URL' 'https://openrouter.ai/api/v1'
if ($LlmKey) { Set-EnvValue $agent 'OPENAI_COMPAT_API_KEY' $LlmKey }
Set-EnvValue $agent 'OPENAI_COMPAT_THINKING_TOKENS' '800'   # gpt-oss thinks before it answers: without room for that the reply is cut off at the token limit
Set-EnvValue $agent 'LLM_EFFORT_PLANNER' 'low'
foreach ($k in 'LLM_MODEL_PLANNER', 'LLM_MODEL_WRITER', 'LLM_MODEL_CHECK') { Set-EnvValue $agent $k 'openai/gpt-oss-20b' }
Set-EnvValue $web 'WEB_PORT' "$WebPort"
Set-EnvValue $web 'ALLOWED_ORIGINS' "http://localhost:$WebPort"
Set-EnvValue $web 'META_SDK_ORIGIN' 'http://localhost:9100'
Write-Host 'done.' -ForegroundColor Green

Step '6/7  Building and starting gateway, agent, web and the simulator (first build takes several minutes)'
foreach ($p in 'gateway', 'agent', 'web') {
  $out = $null
  for ($try = 1; $try -le 2; $try++) {            # a first build can fail on a passing network error (a package download): one retry
    $out = Dk compose --env-file "deploy\$p\.env" -f "deploy\$p\docker-compose.yml" up -d --build
    if ($LASTEXITCODE -eq 0) { break }
    Write-Host "$p did not start (attempt $try of 2)." -ForegroundColor Yellow
  }
  if ($LASTEXITCODE -ne 0) { Write-Host ($out | Select-Object -Last 25 | Out-String); throw "$p failed to start. The last lines of Docker's output are above; send them and the problem can be fixed." }
  Write-Host "$p started."
}
Dk compose --env-file tools\meta-sim\.env -f tools\meta-sim\docker-compose.yml up -d --build | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'The simulator failed to start.' }
Write-Host 'simulator started.'

Step '7/7  Waiting for every address'
$targets = [ordered]@{ 'Shop app (sign up here)' = "http://localhost:$WebPort/healthz"; 'Webhook receive' = 'http://localhost:8001/healthz'; 'Meta simulator (the phones)' = 'http://localhost:9100/' }
$deadline = (Get-Date).AddSeconds(180); $all = $true
foreach ($k in $targets.Keys) {
  $ok = $false
  while (-not $ok -and (Get-Date) -lt $deadline) { try { $ok = (Invoke-WebRequest $targets[$k] -UseBasicParsing -TimeoutSec 4).StatusCode -eq 200 } catch { Start-Sleep 3 } }
  if (-not $ok) { $all = $false }
  Write-Host ('{0,-30} {1,-32} {2}' -f $k, ($targets[$k] -replace '/healthz$', ''), $(if ($ok) { 'up' } else { 'NOT ANSWERING' })) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
}
if (-not $LlmKey) { Write-Host "`nNo model key set: customers get the safe holding message. Re-run with -LlmKey sk-or-..." -ForegroundColor Yellow }
if ($all) { Write-Host "`nReady. Open http://localhost:$WebPort, sign up, click Connect with Meta, then chat from http://localhost:9100. Logs: scripts\logs.ps1 -Follow" -ForegroundColor Green } else { Write-Host "`nSomething did not answer: scripts\logs.ps1 -Errors" -ForegroundColor Red; exit 1 }
