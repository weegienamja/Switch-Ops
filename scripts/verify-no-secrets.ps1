# Secret scan. Refuses commit if any plaintext secret is found in tracked files.
$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

# Real-secret patterns. We deliberately do NOT match the placeholder.
$patterns = @(
    @{ Name = 'switch-password-env'; Regex = 'SWITCH_PASSWORD\s*=\s*(?!__REPLACE)\S{4,}' },
    @{ Name = 'enable-secret-env'; Regex = 'SWITCH_ENABLE_SECRET\s*=\s*(?!__REPLACE)\S{4,}' },
    @{ Name = 'ios-enable-secret'; Regex = 'enable secret\s+\d+\s+(?!__REPLACE|<redacted>)[\$A-Za-z0-9./]{6,}' },
    @{ Name = 'ios-user-secret'; Regex = 'username\s+\w+\s+privilege\s+\d+\s+secret\s+\d+\s+(?!__REPLACE|<redacted>)[\$A-Za-z0-9./]{6,}' }
)

$exclude = @(
    '\.venv\\',
    'node_modules\\',
    '\.next\\',
    '\\out\\',
    '\\target\\',
    '\\dist\\',
    '\\build\\',
    '__pycache__\\',
    '\\binaries\\',
    '\\backups\\',
    '\\logs\\',
    '\\data\\',
    'tsconfig\.tsbuildinfo$',
    '\.lock$',
    'package-lock\.json$',
    'pnpm-lock\.yaml$',
    'Cargo\.lock$',
    '\\\.git\\'
)

# Files allowed to mention the patterns *by name* (this script + governance docs + tests).
$allowList = @(
    'scripts\\verify-no-secrets\.ps1$',
    'desktop\\scripts\\verify-no-secrets\.ps1$',
    'AGENTS\.md$',
    'CLAUDE\.md$',
    'README\.md$',
    '\.github\\instructions\\security\.instructions\.md$',
    'backend\\app\\tests\\'
)

$files = Get-ChildItem -Recurse -File | Where-Object {
    $full = $_.FullName
    -not ($exclude | Where-Object { $full -match $_ })
}

$violations = @()
foreach ($file in $files) {
    $rel = $file.FullName.Substring($repoRoot.Path.Length + 1)
    if ($allowList | Where-Object { $rel -match $_ }) { continue }
    try {
        $content = Get-Content -Raw -ErrorAction Stop -LiteralPath $file.FullName
    } catch { continue }
    if (-not $content) { continue }
    foreach ($pattern in $patterns) {
        $hits = [regex]::Matches($content, $pattern.Regex)
        foreach ($hit in $hits) {
            $violations += [pscustomobject]@{
                File = $rel
                Rule = $pattern.Name
            }
        }
    }
}

if ($violations.Count -gt 0) {
    Write-Host "[switchops] Secret scan FAILED:" -ForegroundColor Red
    $violations | Format-Table -AutoSize | Out-String | Write-Host
    exit 1
}

Write-Host "[switchops] Secret scan passed." -ForegroundColor Green
exit 0
