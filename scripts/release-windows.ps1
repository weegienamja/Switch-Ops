# Build + sign-off + (manual) git push.
# This script does NOT push automatically — it prepares the build.
$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

& (Join-Path $repoRoot "scripts\test.ps1")
& (Join-Path $repoRoot "scripts\verify-no-secrets.ps1")
& (Join-Path $repoRoot "scripts\build.ps1")

Write-Host "[switchops] Release artifacts ready under desktop\src-tauri\target\release\bundle\"
Write-Host "[switchops] Review, then: git add . ; git commit -m 'Build SwitchOps desktop dashboard' ; git push -u origin main"
