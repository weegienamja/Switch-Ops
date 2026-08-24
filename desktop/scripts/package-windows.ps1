# Package the Tauri Windows installer (NSIS + MSI).

param(
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot   = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$desktopDir = Join-Path $repoRoot "desktop"
$previousEncodedRustFlags = [Environment]::GetEnvironmentVariable(
    "CARGO_ENCODED_RUSTFLAGS",
    "Process"
)
$rustFlagSeparator = [char]0x1f
$releaseRemapFlags = @(
    "--remap-path-prefix=$($repoRoot.Path)=.",
    "--remap-path-prefix=$env:USERPROFILE=~"
) -join $rustFlagSeparator
$env:CARGO_ENCODED_RUSTFLAGS = if ($previousEncodedRustFlags) {
    $previousEncodedRustFlags + $rustFlagSeparator + $releaseRemapFlags
} else {
    $releaseRemapFlags
}

Push-Location $desktopDir
try {
    & pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "pnpm install failed with exit code $LASTEXITCODE" }
    $tauriArgs = @("tauri", "build")
    if ($ConfigPath) {
        $tauriArgs += @("--config", $ConfigPath)
    }
    & pnpm @tauriArgs
    if ($LASTEXITCODE -ne 0) { throw "Tauri build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
    if ($null -eq $previousEncodedRustFlags) {
        Remove-Item Env:CARGO_ENCODED_RUSTFLAGS -ErrorAction SilentlyContinue
    } else {
        $env:CARGO_ENCODED_RUSTFLAGS = $previousEncodedRustFlags
    }
}

Write-Host "[switchops] Bundles in: $desktopDir\src-tauri\target\release\bundle\"
