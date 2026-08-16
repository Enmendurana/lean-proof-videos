param(
    [string]$Python = "py -3.12"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Invoke-Expression "$Python -m venv .venv"
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# Fetch the exact dependency revisions and mathlib build cache first.
lake exe cache get

# LeanTeX currently targets older Lean parser and String APIs. This tracked
# compatibility patch updates the removed macro and iterator-based splitting.
$LeanTeXRoot = Join-Path $ProjectRoot ".lake\packages\LeanTeX"
git -C $LeanTeXRoot apply --check "$ProjectRoot\patches\leantex-lean-4.28.patch" 2>$null
if ($LASTEXITCODE -eq 0) {
    git -C $LeanTeXRoot apply "$ProjectRoot\patches\leantex-lean-4.28.patch"
}
lake build Animate

Write-Host "Setup complete. Render with .\render-proof.cmd and inspect cache with .\render-proof-cache.cmd."
