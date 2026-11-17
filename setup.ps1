# PixelSpryte setup — run on any Windows machine.
#   powershell -ExecutionPolicy Bypass -File setup.ps1
# Installs the two Python deps, checks the environment, and tells you what is missing.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "PixelSpryte setup" -ForegroundColor Cyan

# 1. find a 3.12+ interpreter (3.11 segfaults on fusionscript.dll)
$py = $null
foreach ($candidate in @("py -3.12", "py -3", "python")) {
    try {
        $ver = & cmd /c "$candidate -c ""import sys;print('%d.%d' % sys.version_info[:2])""" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$ver -ge [version]"3.12") { $py = $candidate; break }
    } catch { }
}
if (-not $py) {
    Write-Host "No Python 3.12+ found. Install it, then re-run." -ForegroundColor Red
    exit 1
}
Write-Host "interpreter: $py ($ver)"

# 2. deps
& cmd /c "$py -m pip install --user -r requirements.txt"
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed" -ForegroundColor Red; exit 1 }

# 3. environment report
& cmd /c "$py doctor.py"
exit $LASTEXITCODE
