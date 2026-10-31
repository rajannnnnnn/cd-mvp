# Frontend on its own, with hot reload. It proxies /api and /webhooks to the backend stack (scripts\stack.ps1 backend up).
# Usage (from the repository folder):   powershell -ExecutionPolicy Bypass -File scripts\dev-frontend.ps1
#   Point it elsewhere with $env:API_PROXY / $env:INGRESS_PROXY (defaults: http://localhost:8000 and :8001).
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\frontend')
if (-not (Test-Path 'node_modules')) { npm install }
npm run dev
