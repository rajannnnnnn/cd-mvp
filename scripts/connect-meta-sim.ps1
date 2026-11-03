# Wire the Meta stand-in (tools/meta-sim) to this machine's product, using the product's own settings and nothing else (ADR 0045).
# Run it once, from the repository folder:
#   powershell -ExecutionPolicy Bypass -File scripts\connect-meta-sim.ps1
# It writes tools\meta-sim\.env and the Meta lines of deploy\gateway\.env and deploy\web\.env. Secrets are generated here and are not printed.
# Safe to run again: it keeps the secrets it made before. To go back to real Meta later, delete these lines and put your real values in (docs\DEPLOYMENT.md).
#   -BrowserHost  the address your BROWSER uses to reach the stand-in (default localhost; use this PC's LAN address to open the phones from another device)
param([string]$BrowserHost = 'localhost', [int]$Port = 9100)
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..'
$gw = Join-Path $root 'deploy\gateway\.env'
$web = Join-Path $root 'deploy\web\.env'
$sim = Join-Path $root 'tools\meta-sim\.env'
foreach ($f in $gw, $web) { if (-not (Test-Path $f)) { throw "Missing $f. Run scripts\setup-db.ps1 first: it writes the deploy\<part>\.env files." } }

$utf8 = New-Object System.Text.UTF8Encoding($false)       # no byte-order mark: docker compose reads the first key of the file as-is
function Read-Lines([string]$file) { if (Test-Path $file) { return @([System.IO.File]::ReadAllLines($file)) } else { return @() } }
function Get-EnvValue([string]$file, [string]$key) {
  foreach ($l in Read-Lines $file) {
    if ($l -match "^$key=(.*)$") { $v = $Matches[1].Trim(); if ($v.StartsWith('#')) { return '' }; return ($v -replace '\s+#.*$', '') }
  }
  return ''
}
function Set-EnvValue([string]$file, [string]$key, [string]$value) {
  $lines = New-Object System.Collections.Generic.List[string]
  foreach ($l in Read-Lines $file) { $lines.Add($l) }
  $at = -1
  for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^$key=") { $at = $i; break } }
  if ($at -lt 0) { for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^#\s*$key=") { $at = $i; break } } }   # an optional, commented-out line
  if ($at -ge 0) { $lines[$at] = "$key=$value" } else { $lines.Add("$key=$value") }
  [System.IO.File]::WriteAllLines($file, $lines.ToArray(), $utf8)
}
function New-Hex([int]$bytes) { $b = New-Object byte[] $bytes; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b | ForEach-Object { $_.ToString('x2') }) -join '' }

# The two secrets both sides must share: the stand-in signs webhooks with the app secret and checks the verify token, exactly as Meta does.
$appSecret = Get-EnvValue $sim 'SIM_APP_SECRET'; if (-not $appSecret) { $appSecret = Get-EnvValue $gw 'META_APP_SECRET' }; if (-not $appSecret) { $appSecret = New-Hex 16 }
$verify = Get-EnvValue $gw 'META_VERIFY_TOKEN'; if (-not $verify) { $verify = Get-EnvValue $sim 'SIM_VERIFY_TOKEN' }; if (-not $verify) { $verify = New-Hex 16 }

if (-not (Test-Path $sim)) { Copy-Item (Join-Path $root 'tools\meta-sim\.env.example') $sim }
Set-EnvValue $sim 'SIM_PORT' "$Port"
Set-EnvValue $sim 'SIM_WEBHOOK_URL' 'http://host.docker.internal:8001/webhooks/whatsapp'
Set-EnvValue $sim 'SIM_APP_SECRET' $appSecret
Set-EnvValue $sim 'SIM_VERIFY_TOKEN' $verify

# The gateway talks to the stand-in from inside Docker, so it reaches it through the host.
Set-EnvValue $gw 'META_GRAPH_BASE' "http://host.docker.internal:$Port"
Set-EnvValue $gw 'META_APP_ID' 'sim-app'
Set-EnvValue $gw 'META_CONFIG_ID' 'sim-config'
Set-EnvValue $gw 'META_APP_SECRET' $appSecret
Set-EnvValue $gw 'META_VERIFY_TOKEN' $verify
Set-EnvValue $gw 'OTP_CHANNEL' 'whatsapp_cloud'
Set-EnvValue $gw 'PLATFORM_SENDER_PHONE_NUMBER_ID' 'sim-platform'
Set-EnvValue $gw 'PLATFORM_WA_ACCESS_TOKEN' 'SIMTOKEN-platform'

# The web part gives the BROWSER the stand-in's sign-up script, so these use the address the browser uses.
Set-EnvValue $web 'META_APP_ID' 'sim-app'
Set-EnvValue $web 'META_CONFIG_ID' 'sim-config'
Set-EnvValue $web 'META_SDK_URL' "http://${BrowserHost}:$Port/sdk.js"
Set-EnvValue $web 'META_SIGNUP_ORIGINS' "http://${BrowserHost}:$Port"
Set-EnvValue $web 'META_GRAPH_BASE' "http://host.docker.internal:$Port"
Set-EnvValue $web 'OTP_CHANNEL' 'whatsapp_cloud'
Set-EnvValue $web 'PLATFORM_SENDER_PHONE_NUMBER_ID' 'sim-platform'
Set-EnvValue $web 'PLATFORM_WA_ACCESS_TOKEN' 'SIMTOKEN-platform'

Write-Host 'Done. The stand-in and the product now share one app secret and one verify token (generated here, not shown).' -ForegroundColor Green
Write-Host '  wrote tools\meta-sim\.env, and the Meta lines of deploy\gateway\.env and deploy\web\.env'
Write-Host ''
Write-Host 'Next:'
Write-Host '  1. start the stand-in:   cd tools\meta-sim ; ..\..\scripts\nocreds.ps1 docker compose --env-file .env up -d --build ; cd ..\..'
Write-Host '  2. restart the product:  .\scripts\nocreds.ps1 .\scripts\stack.ps1 gateway   then   web'
Write-Host "  3. open the phones:      http://${BrowserHost}:$Port/"
