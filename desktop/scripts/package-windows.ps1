# Package the Tauri Windows installer (NSIS + MSI).

$ErrorActionPreference = "Stop"

$repoRoot   = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$desktopDir = Join-Path $repoRoot "desktop"

Push-Location $desktopDir
try {
    & pnpm install
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed with exit code $LASTEXITCODE" }
    & pnpm tauri build
    if ($LASTEXITCODE -ne 0) { throw "Tauri build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host "[switchops] Bundles in: $desktopDir\src-tauri\target\release\bundle\"
