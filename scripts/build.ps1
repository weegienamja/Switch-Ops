$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $repoRoot
try {
    & (Join-Path $repoRoot "desktop\scripts\build-backend-sidecar.ps1")
    & (Join-Path $repoRoot "desktop\scripts\package-windows.ps1")
} finally {
    Pop-Location
}
