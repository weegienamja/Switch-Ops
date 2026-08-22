# Dev convenience: start backend (mock) + frontend in two terminals.
$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$venv = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
$backend = Join-Path $repoRoot "backend"
$frontend = Join-Path $repoRoot "frontend"

Start-Process powershell -ArgumentList "-NoExit", "-Command", "& '$venv'; cd '$backend'; `$env:SWITCH_MOCK_MODE='true'; python -m app.main"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$frontend'; pnpm dev"

Write-Host "[switchops] Backend on http://127.0.0.1:8765, frontend on http://localhost:3000"
