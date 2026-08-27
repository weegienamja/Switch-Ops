# Build and stage the separate EWPS v0.2.4 Alpha Windows artifacts.

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $repoRoot "release"
$stage = Join-Path $releaseRoot "ewps-v0.2.4-alpha"
$resolvedReleaseRoot = [System.IO.Path]::GetFullPath($releaseRoot)
$resolvedStage = [System.IO.Path]::GetFullPath($stage)
if (-not $resolvedStage.StartsWith($resolvedReleaseRoot + [System.IO.Path]::DirectorySeparatorChar)) {
    throw "Refusing to stage outside the repository release directory."
}

& powershell -ExecutionPolicy Bypass -File (Join-Path $repoRoot "desktop\scripts\build-backend-sidecar.ps1")
if ($LASTEXITCODE -ne 0) { throw "EWPS backend sidecar build failed with exit code $LASTEXITCODE" }

& powershell -ExecutionPolicy Bypass -File (Join-Path $repoRoot "desktop\scripts\package-windows.ps1") `
    -ConfigPath "src-tauri/tauri.ewps.conf.json"
if ($LASTEXITCODE -ne 0) { throw "EWPS Windows package build failed with exit code $LASTEXITCODE" }

if (Test-Path -LiteralPath $resolvedStage) {
    Remove-Item -Recurse -Force -LiteralPath $resolvedStage
}
New-Item -ItemType Directory -Path $resolvedStage | Out-Null

$bundleRoot = Join-Path $repoRoot "desktop\src-tauri\target\release\bundle"
$installerFiles = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File | Where-Object {
    $_.Extension -in @(".exe", ".msi")
}
if (-not $installerFiles) { throw "No NSIS or MSI artifacts were produced." }

foreach ($artifact in $installerFiles) {
    $kind = if ($artifact.Extension -eq ".msi") { "x64_en-US" } else { "x64-setup" }
    $name = "SwitchOps_EWPS_0.2.4-alpha_{0}{1}" -f $kind, $artifact.Extension
    Copy-Item -Force -LiteralPath $artifact.FullName -Destination (Join-Path $resolvedStage $name)
}

$artifacts = Get-ChildItem -LiteralPath $resolvedStage -File | Sort-Object Name
$checksumLines = foreach ($artifact in $artifacts) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifact.FullName).Hash.ToLowerInvariant()
    "$hash  $($artifact.Name)"
}
$checksumPath = Join-Path $resolvedStage "SHA256SUMS.txt"
[System.IO.File]::WriteAllLines($checksumPath, $checksumLines, [System.Text.UTF8Encoding]::new($false))

Write-Host "[ewps] Experimental artifacts staged in $resolvedStage"
Get-Content -LiteralPath $checksumPath
