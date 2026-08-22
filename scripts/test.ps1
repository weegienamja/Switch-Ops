$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$venv = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"
$backend = Join-Path $repoRoot "backend"
$frontend = Join-Path $repoRoot "frontend"

Push-Location $backend
try {
    & $venv
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

Push-Location $frontend
try {
    pnpm install
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed with exit code $LASTEXITCODE" }
    pnpm typecheck
    if ($LASTEXITCODE -ne 0) { throw "Frontend typecheck failed with exit code $LASTEXITCODE" }
    pnpm lint
    if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed with exit code $LASTEXITCODE" }
    pnpm build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
