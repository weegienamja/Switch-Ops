# Fail fast when the packaged sidecar is older than the backend sources.
#
# The desktop shell can now prove it is talking to the sidecar it spawned, but
# that says nothing about how old that sidecar is. `tauri dev` never rebuilds
# it, so editing backend/app and running the desktop application will happily
# exercise a binary built days earlier. This check turns that into an error
# with instructions instead of a silently stale dashboard.
#
# Set SWITCHOPS_ALLOW_STALE_SIDECAR=1 to proceed anyway.

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$backendApp = Join-Path $repoRoot "backend\app"
$binDir = Join-Path $repoRoot "desktop\binaries"

$target = "x86_64-pc-windows-msvc"
try {
    $rustcVersion = & rustc -vV 2>$null
    if ($LASTEXITCODE -eq 0) {
        $hostLine = $rustcVersion | Select-String "^host:"
        if ($hostLine) { $target = ($hostLine -replace "host:\s*", "").Trim() }
    }
} catch {}

$sidecar = Join-Path $binDir ("switchops-backend-{0}.exe" -f $target)

if (-not (Test-Path $sidecar)) {
    Write-Host "[switchops] No backend sidecar at $sidecar" -ForegroundColor Red
    Write-Host "[switchops] Build it first:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\desktop\scripts\build-backend-sidecar.ps1"
    exit 1
}

$sidecarTime = (Get-Item $sidecar).LastWriteTimeUtc

$newest = Get-ChildItem -Path $backendApp -Recurse -Filter *.py -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike '*__pycache__*' -and $_.FullName -notlike '*\tests\*' } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if ($null -eq $newest) {
    Write-Host "[switchops] No backend sources found to compare against." -ForegroundColor Yellow
    exit 0
}

if ($newest.LastWriteTimeUtc -gt $sidecarTime) {
    $rel = $newest.FullName.Substring($repoRoot.Path.Length).TrimStart('\')
    Write-Host ""
    Write-Host "[switchops] The backend sidecar is STALE." -ForegroundColor Red
    Write-Host ("  sidecar built : {0}Z" -f $sidecarTime.ToString("yyyy-MM-ddTHH:mm:ss"))
    Write-Host ("  newest source : {0}Z  ({1})" -f $newest.LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ss"), $rel)
    Write-Host ""
    Write-Host "  The desktop application would run backend code older than your edits." -ForegroundColor Yellow
    Write-Host "  Rebuild it:" -ForegroundColor Yellow
    Write-Host "    powershell -ExecutionPolicy Bypass -File .\desktop\scripts\build-backend-sidecar.ps1"
    Write-Host ""
    Write-Host "  To run anyway: `$env:SWITCHOPS_ALLOW_STALE_SIDECAR=1" -ForegroundColor DarkGray
    Write-Host ""
    if ($env:SWITCHOPS_ALLOW_STALE_SIDECAR -eq "1") {
        Write-Host "[switchops] SWITCHOPS_ALLOW_STALE_SIDECAR=1 set; continuing." -ForegroundColor Yellow
        exit 0
    }
    exit 1
}

Write-Host ("[switchops] Sidecar is current (built {0}Z)." -f $sidecarTime.ToString("yyyy-MM-ddTHH:mm:ss")) -ForegroundColor Green
exit 0
