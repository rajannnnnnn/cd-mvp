# One-time setup, run with Postgres SUPERUSER credentials (never stored): creates the database, the five app roles (with fresh passwords),
# writes the five deploy\<part>\.env files with the database URLs and the secrets you generate yourself, and runs the migrations (the tables).
# The app parts only ever get their own limited logins; the superuser is used here and nowhere else.
#
#   Postgres in Docker (no psql needed on this PC):
#     powershell -ExecutionPolicy Bypass -File scripts\setup-db.ps1 -PgContainer chatdeal-pg -SuperPassword devpass
#   Postgres installed on this PC or elsewhere (needs psql on PATH):
#     powershell -ExecutionPolicy Bypass -File scripts\setup-db.ps1 -PgHost 127.0.0.1 -SuperUser postgres -SuperPassword yourpassword
#
# Safe to run again: existing secrets (JWT, OTP, admin, master key, webhook token) are kept; only the database passwords are renewed.
# What it cannot fill in: Meta values, the model key, SMTP, your domain names. Those are yours (docs\DEPLOYMENT.md).
param(
  [string]$PgContainer = '',                       # a running Postgres container; commands run inside it
  [string]$PgHost = '127.0.0.1',
  [int]$PgPort = 5432,
  [string]$SuperUser = 'postgres',
  [string]$SuperPassword = '',
  [string]$Database = 'salesai',
  [string]$AppDbHost = 'host.docker.internal',     # how the app containers reach Postgres (use the machine's private address on other machines)
  [int]$AppDbPort = 5432,
  [switch]$SkipMigrate
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..'
$deploy = Join-Path $root 'deploy'
$parts = 'db', 'web', 'gateway', 'agent', 'admin'

function New-Hex([int]$bytes) { $b = New-Object byte[] $bytes; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b | ForEach-Object { $_.ToString('x2') }) -join '' }
function New-Base64([int]$bytes) { $b = New-Object byte[] $bytes; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b) }
function Get-EnvValue([string]$part, [string]$key) {
  $f = Join-Path $deploy "$part\.env"
  if (-not (Test-Path $f)) { return '' }
  $m = Select-String -Path $f -Pattern "^$key=(.+)$" | Select-Object -First 1
  if ($m) { return $m.Matches[0].Groups[1].Value.Trim() } else { return '' }
}
function Keep-Or-New([string]$key, [scriptblock]$make) {
  foreach ($p in $parts) { $v = Get-EnvValue $p $key; if ($v) { return $v } }
  return (& $make)
}

# Run psql as the superuser: inside the container, or from this PC.
function Invoke-Psql([string]$db, [string[]]$psqlArgs, [string]$stdin = '') {
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'      # Windows PowerShell 5 treats any text a program writes to stderr as an error; the exit code is what counts
  if ($PgContainer) {
    $a = @('exec', '-i', $PgContainer, 'psql', '-U', $SuperUser, '-d', $db, '-v', 'ON_ERROR_STOP=1', '-q') + $psqlArgs
    if ($stdin) { $stdin | docker @a } else { docker @a }
  } else {
    $env:PGPASSWORD = $SuperPassword
    $a = @('-h', $PgHost, '-p', "$PgPort", '-U', $SuperUser, '-d', $db, '-v', 'ON_ERROR_STOP=1', '-q') + $psqlArgs
    if ($stdin) { $stdin | psql @a } else { psql @a }
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
  }
  $ErrorActionPreference = $prev
  if ($LASTEXITCODE -ne 0) { throw "psql failed (exit $LASTEXITCODE). Check the Postgres address and the superuser password." }
}

Write-Host '1/4  Checking Postgres ...'
Invoke-Psql 'postgres' @('-c', 'select 1') | Out-Null

Write-Host "2/4  Creating database '$Database' and the five app roles ..."
$exists = if ($PgContainer) { docker exec -i $PgContainer psql -U $SuperUser -d postgres -tAc "select 1 from pg_database where datname='$Database'" }
          else { $env:PGPASSWORD = $SuperPassword; psql -h $PgHost -p $PgPort -U $SuperUser -d postgres -tAc "select 1 from pg_database where datname='$Database'"; Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
if (-not ("$exists".Trim() -eq '1')) { Invoke-Psql 'postgres' @('-c', "create database $Database") }
$pw = @{}
foreach ($r in 'owner', 'user', 'pricing', 'system', 'console') { $pw[$r] = New-Hex 16 }
$sql = Get-Content -Raw (Join-Path $root 'db\bootstrap_roles.sql')
Invoke-Psql $Database @('-v', "owner_pw=$($pw.owner)", '-v', "user_pw=$($pw.user)", '-v', "pricing_pw=$($pw.pricing)", '-v', "system_pw=$($pw.system)", '-v', "console_pw=$($pw.console)") $sql

Write-Host '3/4  Writing deploy\<part>\.env (existing secrets are kept) ...'
$jwt = Keep-Or-New 'JWT_SECRET' { New-Hex 32 }
$otp = Keep-Or-New 'OTP_SECRET' { New-Hex 32 }
$adminJwt = Keep-Or-New 'ADMIN_JWT_SECRET' { New-Hex 32 }
$master = Keep-Or-New 'MASTER_KEY' { New-Base64 32 }
$verify = Keep-Or-New 'META_VERIFY_TOKEN' { New-Hex 16 }
function Url([string]$role, [string]$pass) { "postgresql://app_${role}:$pass@${AppDbHost}:$AppDbPort/$Database" }
$common = @{
  DATABASE_URL = Url 'user' $pw.user; SYSTEM_DATABASE_URL = Url 'system' $pw.system; MIGRATION_DATABASE_URL = Url 'owner' $pw.owner
}
$extra = @{
  web = @{ JWT_SECRET = $jwt; OTP_SECRET = $otp }
  gateway = @{ MASTER_KEY = $master; META_VERIFY_TOKEN = $verify }
  agent = @{ PRICING_DATABASE_URL = Url 'pricing' $pw.pricing }
  admin = @{ CONSOLE_DATABASE_URL = Url 'console' $pw.console; JWT_SECRET = $jwt; OTP_SECRET = $otp; ADMIN_JWT_SECRET = $adminJwt; MASTER_KEY = $master }
  db = @{}
}
foreach ($p in $parts) {
  $dir = Join-Path $deploy $p
  $envFile = Join-Path $dir '.env'
  $text = if (Test-Path $envFile) { Get-Content -Raw $envFile } else { Get-Content -Raw (Join-Path $dir '.env.example') }
  $values = @{}; foreach ($k in $common.Keys) { $values[$k] = $common[$k] }; foreach ($k in $extra[$p].Keys) { $values[$k] = $extra[$p][$k] }
  foreach ($k in $values.Keys) {
    $line = "$k=$($values[$k])"
    if ($text -match "(?m)^$k=") { $text = [regex]::Replace($text, "(?m)^$k=.*$", { param($m) $line }) } else { $text = $text.TrimEnd() + "`r`n$line`r`n" }
  }
  [System.IO.File]::WriteAllText($envFile, $text, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "     wrote deploy\$p\.env"
}

if ($SkipMigrate) { Write-Host '4/4  Skipped the migrations (-SkipMigrate).' }
else {
  Write-Host '4/4  Running the migrations (builds the backend image the first time: a few minutes) ...'
  & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stack.ps1') db
  if ($LASTEXITCODE -ne 0) { throw 'The migrations failed. Read the message above.' }
}

Write-Host ''
Write-Host 'Done. The database, roles, tables and the secrets you generate yourself are in place. Still yours to fill in (see docs\DEPLOYMENT.md):'
Write-Host '  web:     ALLOWED_ORIGINS, invoice details, SMTP / alert email'
Write-Host '  gateway: META_APP_ID, META_APP_SECRET, META_CONFIG_ID, platform WhatsApp number id and token'
Write-Host '  agent:   LLM_PROVIDER, the model API key (and base URL)'
Write-Host '  admin:   ADMIN_ALLOWED_ORIGINS'
Write-Host 'Back up deploy\gateway\.env and deploy\admin\.env (MASTER_KEY): if it is lost, every shop must reconnect WhatsApp.'
Write-Host 'Next: scripts\stack.ps1 web ... gateway ... agent ... admin, then python -m salesai doctor.'
