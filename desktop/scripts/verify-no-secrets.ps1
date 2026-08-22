# Quick local secret scan stub for the desktop folder (delegates to root).
& (Join-Path $PSScriptRoot "..\..\scripts\verify-no-secrets.ps1")
