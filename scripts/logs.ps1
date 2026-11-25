# See the logs of the running services, one part or all of them together, readable.
#
#   powershell -ExecutionPolicy Bypass -File scripts\logs.ps1                     last 20 lines of every service, then exit
#   powershell -ExecutionPolicy Bypass -File scripts\logs.ps1 -Follow             keep going: new lines from every service, live (Ctrl+C to stop)
#   powershell -ExecutionPolicy Bypass -File scripts\logs.ps1 -Part agent -Follow -Tail 100
#   powershell -ExecutionPolicy Bypass -File scripts\logs.ps1 -Errors             only warnings and errors (and the stack trace under them)
#   powershell -ExecutionPolicy Bypass -File scripts\logs.ps1 -Chat 1a2b3c4d     one customer's conversation across every service (the code after c: in each line)
#   powershell -ExecutionPolicy Bypass -File scripts\logs.ps1 -List               which services there are
#   powershell -ExecutionPolicy Bypass -File scripts\logs.ps1 -Raw                the original JSON lines, untouched
#
# -Part: all | web | gateway | agent | admin | sim | db   (or part of a container name, e.g. gateway-send)
# Every line starts with the service it came from, then the time, the level and the message. The product logs one JSON object per line; this makes it readable.
param(
  [string]$Part = 'all',
  [int]$Tail = 20,
  [switch]$Follow,
  [switch]$Errors,
  [switch]$Raw,
  [string]$Chat = '',
  [switch]$List
)
$ErrorActionPreference = 'Continue'
$m = [Environment]::GetEnvironmentVariable('Path', 'Machine'); $u = [Environment]::GetEnvironmentVariable('Path', 'User'); $env:Path = "$m;$u"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Host 'Docker is not on the PATH. Start Docker Desktop first.' -ForegroundColor Yellow; exit 1 }

$names = @(docker ps --format '{{.Names}}' 2>$null | Where-Object { $_ -match '^(chatdeal-|meta-sim)' -and $_ -notmatch 'bench' } | Sort-Object)
if ($names.Count -eq 0) { Write-Host 'No services are running (is Docker Desktop started?).' -ForegroundColor Yellow; exit 1 }

$groups = @{ web = 'chatdeal-web'; gateway = 'chatdeal-gateway'; agent = 'chatdeal-agent'; admin = 'chatdeal-admin'; sim = 'meta-sim'; db = 'chatdeal-pg' }
$pick = if ($Part -eq 'all') { $names } elseif ($groups.ContainsKey($Part)) { $names | Where-Object { $_ -like "$($groups[$Part])*" } } else { $names | Where-Object { $_ -like "*$Part*" } }
if ($List -or -not $pick) { Write-Host 'Running services:'; $names | ForEach-Object { "  $_" }; if (-not $pick -and -not $List) { Write-Host "No service matches '$Part'." -ForegroundColor Yellow }; exit 0 }

$colors = 'Cyan', 'Green', 'Yellow', 'Magenta', 'Blue', 'DarkCyan', 'DarkGreen', 'DarkYellow', 'DarkMagenta', 'Gray'
function Show-Line([string]$name, [string]$color, [string]$line) {
  if ($Raw) { if (-not $Errors -or $line -match '"level": "(WARNING|ERROR|CRITICAL)"|Traceback|Error') { Write-Host "[$name] $line" -ForegroundColor $color }; return }
  $short = $name -replace '^chatdeal-', '' -replace '-1$', ''
  try {
    $j = $line | ConvertFrom-Json -ErrorAction Stop
    if ($Errors -and $j.level -notin 'WARNING', 'ERROR', 'CRITICAL') { return }
    $cid = "$($j.conversation_id)"
    if ($Chat -and -not $cid.StartsWith($Chat)) { return }                  # one customer's conversation only
    $t = if ($j.ts) { ([datetime]$j.ts).ToLocalTime().ToString('HH:mm:ss') } else { '        ' }
    $lvl = ("$($j.level)").PadRight(7)
    $tag = if ($cid.Length -ge 8) { 'c:' + $cid.Substring(0, 8) } else { '          ' }     # the short code to follow one conversation with -Chat
    $txt = "$($j.msg)"; if ($j.exc) { $txt += "`n    " + (("$($j.exc)" -split "`n" | Select-Object -Last 6) -join "`n    ") }
    $c = switch ("$($j.level)") { 'ERROR' { 'Red' } 'CRITICAL' { 'Red' } 'WARNING' { 'Yellow' } default { $color } }
    Write-Host ("{0,-16} {1} {2} {3} {4}" -f $short, $t, $lvl, $tag, $txt) -ForegroundColor $c
  } catch {
    if ($Chat) { return }
    if ($Errors -and $line -notmatch 'Traceback|Error|error|fail') { return }
    Write-Host ("{0,-16} {1}" -f $short, $line) -ForegroundColor $color
  }
}

if (-not $Follow) {
  $i = 0
  foreach ($n in $pick) { $color = $colors[$i++ % $colors.Count]; docker logs --tail $Tail $n 2>&1 | ForEach-Object { Show-Line $n $color "$_" } }
  exit 0
}

Write-Host "Following $($pick.Count) service(s): $($pick -join ', ')   (Ctrl+C to stop)" -ForegroundColor DarkGray
$jobs = @(); $i = 0
foreach ($n in $pick) { $jobs += Start-Job -Name $n -ArgumentList $n, $Tail -ScriptBlock { param($n, $t) $env:Path = "$([Environment]::GetEnvironmentVariable('Path','Machine'));$([Environment]::GetEnvironmentVariable('Path','User'))"; docker logs -f --tail $t $n 2>&1 | ForEach-Object { "$_" } } }
try {
  while ($true) {
    $i = 0
    foreach ($j in $jobs) { $color = $colors[$i++ % $colors.Count]; Receive-Job -Job $j -ErrorAction SilentlyContinue | ForEach-Object { Show-Line $j.Name $color "$_" } }
    Start-Sleep -Milliseconds 400
  }
} finally { $jobs | Stop-Job -ErrorAction SilentlyContinue; $jobs | Remove-Job -Force -ErrorAction SilentlyContinue }
