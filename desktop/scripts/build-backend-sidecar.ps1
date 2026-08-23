# Build the FastAPI backend into a single-file sidecar exe for Tauri.
#
# Output: desktop/binaries/switchops-backend-<target-triple>.exe
#
# Tauri's sidecar resolver expects the file name to end with the Rust target
# triple (e.g. x86_64-pc-windows-msvc.exe) on Windows.

$ErrorActionPreference = "Stop"

$repoRoot   = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$backendDir = Join-Path $repoRoot "backend"
$binDir     = Join-Path $repoRoot "desktop\binaries"
$venvDir    = Join-Path $repoRoot ".venv"

if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir | Out-Null }

# Determine Rust target triple (default to x86_64-pc-windows-msvc).
$target = "x86_64-pc-windows-msvc"
try {
    $rustcVersion = & rustc -vV 2>$null
    if ($LASTEXITCODE -eq 0) {
        $hostLine = $rustcVersion | Select-String "^host:"
        if ($hostLine) { $target = ($hostLine -replace "host:\s*", "").Trim() }
    }
} catch {}

Write-Host "[switchops] Target triple: $target"

if (-not (Test-Path (Join-Path $venvDir "Scripts\python.exe"))) {
    Write-Host "[switchops] Creating venv at $venvDir"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -m venv $venvDir
    } else {
        & python -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0) { throw "Python 3.11 virtual environment creation failed" }
}

$py  = Join-Path $venvDir "Scripts\python.exe"
$pip = Join-Path $venvDir "Scripts\pip.exe"

& $pip install --upgrade pip | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE" }
& $pip install -r (Join-Path $backendDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "backend dependency install failed with exit code $LASTEXITCODE" }
& $pip install pyinstaller==6.22.2
if ($LASTEXITCODE -ne 0) { throw "PyInstaller install failed with exit code $LASTEXITCODE" }

Push-Location $backendDir
try {
    & $py -m PyInstaller `
        --onefile `
        --name "switchops-backend" `
        --noconfirm `
        --clean `
        --collect-submodules netmiko `
        --collect-submodules paramiko `
        --collect-submodules keyring `
        --collect-data netaddr `
        --workpath "build\pyinstaller" `
        --specpath "build\spec" `
        sidecar.py

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $built = Join-Path $backendDir "dist\switchops-backend.exe"
    if (-not (Test-Path $built)) { throw "PyInstaller did not produce $built" }

    $dest = Join-Path $binDir ("switchops-backend-{0}.exe" -f $target)
    Copy-Item -Force $built $dest
    Write-Host "[switchops] Sidecar -> $dest"
} finally {
    Pop-Location
}
