param(
    [string]$Python = "py -3.12"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Invoke-Expression "$Python -m venv .venv"
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"

Push-Location (Join-Path $ProjectRoot "studio")
try {
    npm install
    npm run build
}
finally {
    Pop-Location
}

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

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Lean Proof Studio.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
$Shortcut.Arguments = '"' + (Join-Path $ProjectRoot "launch-proof-studio.vbs") + '"'
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "Open the local Lean Proof Studio"
$Shortcut.Save()

Write-Host "Setup complete. Open 'Lean Proof Studio' from the desktop or render with .\render-proof.cmd."
