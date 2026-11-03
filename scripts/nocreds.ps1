# Runs one command with Docker's saved-login helper hidden, for PCs where Docker says "error getting credentials ... logon session does not exist".
# Public images are then pulled anonymously. Only this one run is affected: PATH is restored afterwards.
#   .\scripts\nocreds.ps1 .\scripts\stack.ps1 db
#   .\scripts\nocreds.ps1 .\scripts\stack.ps1 web
#   .\scripts\nocreds.ps1 docker pull postgres:16-alpine
# The lasting fix is to sign out of Windows and sign back in.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
$ErrorActionPreference = 'Stop'
if (-not $Command -or $Command.Count -eq 0) { throw 'Usage: .\scripts\nocreds.ps1 <command> [arguments]   for example   .\scripts\nocreds.ps1 .\scripts\stack.ps1 db' }
$real = (Get-Command docker -CommandType Application | Select-Object -First 1).Source
$oldPath = $env:PATH
try {
  $env:PATH = ($env:PATH -split ';' | Where-Object { $_ -and $_ -notmatch 'Docker' }) -join ';'
  function docker { & $real @args }
  $rest = @(); if ($Command.Count -gt 1) { $rest = $Command[1..($Command.Count - 1)] }
  if ($Command[0] -eq 'docker') { & $real @rest } else { & $Command[0] @rest }
}
finally { $env:PATH = $oldPath }
